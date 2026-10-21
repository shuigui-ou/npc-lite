#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
npc_guard.py — NPC 一致性拦截中间件（npc-lite）

**定位**：给实时对话循环用的单点一致性闸门。
不做多层分支推演（那会卡死延迟），只对「当前这一句话」判定：
    候选回复  →  规则判定  →  pass（放行 + 状态增量）/ reject（拒收 + 重写理由）

**设计取舍（与宏观域 hard_prune 的关键区别）**：
  · hard_prune 的保守语义是「数据缺失 → 不匹配 → 不剪」；
    因为宏观推演里宁可漏剪不可误剪。
  · npc-lite **反转**这条：状态契约缺失是**程序错误**，必须抛
    `StateContractError`，绝不静默放行——放行等于让人设崩坏的回复流进
    对话，后果不可逆（用户已经看见了）。
  · 但「规则没命中文本特征」**不是**契约错误，那本来就是 pass。
    两者必须分清，否则要么全放行要么全报错。

**依赖**：纯 stdlib（json / re / time）。零第三方包，Unity/Python 后端可直接嵌。
"""
from __future__ import annotations

import json
import os
import re
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(BASE, "config", "npc_rules.json")

__all__ = [
    "check_npc_response", "fold_memory", "load_rules", "reset_rules_cache",
    "StateContractError", "TextContractError",
]


class StateContractError(ValueError):
    """状态契约违例：current_state 缺必需字段/类型错。**必须抛，绝不静默放行。**"""


class TextContractError(ValueError):
    """文本契约违例：llm_response 非字符串或为空。"""


# ── 规则缓存（热路径必须零磁盘 IO）───────────────────────────
_RULES_CACHE = {}


def load_rules(path=None):
    path = path or DEFAULT_RULES
    key = os.path.abspath(path)
    hit = _RULES_CACHE.get(key)
    if hit is not None:
        return hit
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    compiled = _compile_rules(rules)
    _RULES_CACHE[key] = compiled
    return compiled


def reset_rules_cache():
    _RULES_CACHE.clear()


def _compile_rules(raw):
    """预编译：把规则里的 value 列表转成 tuple，文本特征转小写。

    延迟预算 < 50ms，预编译是热路径不重��� JSON 的关键。
    """
    out = {
        "version": raw.get("version"),
        "schema": raw.get("schema", {}),
        "required_fields": tuple(
            raw.get("schema", {}).get("current_state", {}).get("required", [])),
        "bounds": dict(raw.get("state_rules", {}).get("bounds", {})),
        "rules": [],
        "deltas": [],
        "transitions": [],
    }
    for t in raw.get("state_rules", {}).get("transitions", {}).get("rules", []):
        out["transitions"].append({
            "id": t["id"],
            "when": t.get("when", {}),
            "set": dict(t.get("set", {})),       # 跃迁后写入的状态字段
            "reason": t.get("reason", ""),
        })
    for r in raw.get("rules", []):
        out["rules"].append({
            "id": r["id"],
            "when": r.get("when", {}),
            "reason": r.get("reason", ""),
            "severity": r.get("severity", "hard"),
            "mask_group": r.get("mask_group"),
        })
    for d in raw.get("state_rules", {}).get("deltas", []):
        out["deltas"].append({
            "id": d["id"],
            "when": d.get("when", {}),
            "apply": dict(d.get("apply", {})),
            "reason": d.get("reason", ""),
        })
    return out


# ── 状态契约校验（不通过即抛，绝不放行）─────────────────────
def _validate_state(current_state, compiled):
    if not isinstance(current_state, dict):
        raise StateContractError(
            "current_state 必须是 dict，实际是 %s" % type(current_state).__name__)
    missing = [f for f in compiled["required_fields"] if f not in current_state]
    if missing:
        raise StateContractError(
            "current_state 缺少必需字段: %s（必需字段: %s）。"
            "中间件拒绝在状态不明时放行——放行等于让人设崩坏的回复进入对话。"
            % (", ".join(missing), ", ".join(compiled["required_fields"])))
    # 类型校验：数值字段必须可比较
    for f in compiled["required_fields"]:
        v = current_state[f]
        if f in compiled["bounds"] and not isinstance(v, (int, float)):
            raise StateContractError(
                "current_state.%s 必须是数值，实际是 %s（值: %r）"
                % (f, type(v).__name__, v))
    return current_state


def _validate_text(llm_response):
    if not isinstance(llm_response, str):
        raise TextContractError(
            "llm_response 必须是 str，实际是 %s" % type(llm_response).__name__)
    if not llm_response.strip():
        raise TextContractError("llm_response 不能为空字符串")
    return llm_response


def _validate_history(path_history):
    if path_history is None:
        return []
    if not isinstance(path_history, (list, tuple)):
        raise StateContractError(
            "path_history 必须是 list，实际是 %s" % type(path_history).__name__)
    return [str(h) for h in path_history]


# ── 取值：支持 状态字段 / 特殊量_text / 特殊量_history ──────
def _get_metric(current_state, metric, ctx):
    if metric == "reply_text":
        return ctx["reply_text"]
    if metric == "history_text":
        return ctx["history_text"]
    if metric == "player_text":
        # 玩家这一轮说了什么。剧本杀/审讯类场景必须能判定「玩家是否出示了证物、
        # 是否指出矛盾」—— 否则 NPC 的心理压力永远涨不起来（实测踩过：
        # 剧本杀 demo 里压力曲线恒为 0，因为 delta 的触发词在玩家问句里，
        # 而引擎只看 reply_text）。
        return ctx.get("player_text") or ""
    return current_state.get(metric)


def _text_contains_any(haystack, needles):
    return any(n in haystack for n in needles)


def _eval_leaf(current_state, leaf, ctx):
    metric = leaf.get("metric")
    op = leaf.get("op")
    target = leaf.get("value")

    if op in ("contains_any", "contains_all", "contains_none"):
        hay = _get_metric(current_state, metric, ctx)
        # ⚠ 文本分支必须只处理 **str**；list/tuple/set 字段（证物清单、
        #   role_tags、已收集线索等）要走下面的成员判断分支。
        #   实测踩过（2026-10-21）：这里原本对非 str 直接 return False，
        #   导致下方 `if isinstance(val, (list,tuple,set))` 里的 contains_any
        #   成员判断**永远执行不到** —— 剧本杀「证物清单」类机制会全盘失效
        #   （evidence=['凶器照片'] 查 contains_any 恒为 False）。
        if isinstance(hay, (list, tuple, set)):
            tset = {str(x).lower() for x in (target or [])}
            vset = {str(x).lower() for x in hay}
            if op == "contains_any":
                return bool(tset & vset)
            if op == "contains_all":
                return tset.issubset(vset)
            return not bool(tset & vset)      # contains_none
        if not isinstance(hay, str):
            return False
        needles = [str(x).lower() for x in (target or [])]
        if op == "contains_any":
            return _text_contains_any(hay, needles)
        if op == "contains_all":
            return all(n in hay for n in needles)
        return not _text_contains_any(hay, needles)   # contains_none

    val = _get_metric(current_state, metric, ctx)
    if op == "exists":
        return val is not None
    if op == "unknown":
        return val is None
    if val is None:
        return False

    # 标量比较：列表字段（如 role_tags）做成员判断
    if isinstance(val, (list, tuple, set)):
        if op == "in":
            return val in (target or [])
        if op == "not_in":
            return val not in (target or [])
        if op in ("contains_any", "contains_all"):
            tset = {str(x).lower() for x in (target or [])}
            vset = {str(x).lower() for x in val}
            return bool(tset & vset) if op == "contains_any" else tset.issubset(vset)
        return False

    try:
        if op == "==":
            return val == target
        if op == "!=":
            return val != target
        if op == "<":
            return val < target
        if op == "<=":
            return val <= target
        if op == ">":
            return val > target
        if op == ">=":
            return val >= target
        if op == "in":
            return val in (target or [])
        if op == "not_in":
            return val not in (target or [])
    except TypeError:
        return str(val) == str(target)
    raise StateContractError("未知算子: %s" % op)


def _eval_pred(current_state, pred, ctx):
    """求值一条谓词。

    unless（2026-10-20 新增）：**条件白名单 / 条件旁路**。
    语义：`{"all_of":[...], "unless":{...}}` = all_of 全真 **且** unless 为假。
    用于「角色面具」：默认禁止热情，但 trigger 命中时这条规则整体不生效。

    为什么不是"action=allow 跳过检查"（路径 A 的字面表述）：
    跳过后角色**仍停在cold 状态**，下一句热情话会被同一条规则重新拦下——
    逻辑自相矛盾。所以 unless 的正确用法是「让这条规则失效」，
    **状态迁移由 transition 负责**（路径 B），两者配合才是完整语义。
    """
    if "unless" in pred:
        if _eval_pred(current_state, pred["unless"], ctx):
            return False                      # 白名单命中 → 本规则不生效
        pred = {k: v for k, v in pred.items() if k != "unless"}
        if not pred:
            return True
    if "all_of" in pred:
        return all(_eval_pred(current_state, p, ctx) for p in pred["all_of"])
    if "any_of" in pred:
        return any(_eval_pred(current_state, p, ctx) for p in pred["any_of"])
    if "not" in pred:
        return not _eval_pred(current_state, pred["not"], ctx)
    return _eval_leaf(current_state, pred, ctx)


# ── 状态增量（轻量加减法 + 边界裁剪）────────────────────────
def _apply_deltas(current_state, compiled, ctx):
    delta = {}
    fired = []
    for d in compiled["deltas"]:
        if _eval_pred(current_state, d["when"], ctx):
            fired.append(d["id"])
            for k, v in d["apply"].items():
                delta[k] = delta.get(k, 0) + (int(v) if str(v).lstrip("+-").isdigit() else 0)
    return delta, fired


def _bounded_delta(state, raw_delta, bounds):
    """把原始增量换算成**裁剪到边界后**的实际增量。

    为什么要裁剪后回算：state.trust 已是 98 时，+5 不是 +5 而是 +2。
    若直接把 +5 写回，trust 会变成 103 越界——数值一旦越界，
    后续所有 `trust >= 80` 这类阈值判定就全乱了。
    """
    out = {}
    for k, dv in raw_delta.items():
        prev = state.get(k)
        if not isinstance(prev, (int, float)):
            prev = 0                      # 该字段不存在/非数值 → 按 0 起点算
        want = prev + dv
        lo, hi = bounds.get(k, (None, None))
        if lo is not None and want < lo:
            want = lo
        if hi is not None and want > hi:
            want = hi
        actual = want - prev
        if actual != 0:
            out[k] = actual
    return out


# ── 记忆折叠（Memory Fold）─────────────────────────────────
#: 事实抽取：正则命中即成事实。刻意做成「高��度、低召回」——
#: 折叠的失败模式是「漏掉关键事实」比「多带一条废话」严重得多。
_FACT_PATTERNS = [
    (r"偷了|偷走|盗走|行窃|偷窃", "玩家偷了东西"),
    (r"撒了谎|撒谎|欺骗|骗了|隐瞒", "玩家撒了谎"),
    (r"道歉|认错|悔改", "玩家道歉了"),
    (r"救了|救助|援救|帮助了", "玩家帮助了NPC"),
    (r"杀了|杀死|杀害| murder", "发生了死亡"),
    (r"背叛|出卖|反水", "玩家背叛了NPC"),
    (r"赠与|送给|给了|付出", "玩家给予了好意"),
    (r"威胁|恐吓|胁迫", "玩家威胁了NPC"),
    (r"离开|离去|走了|告别", "角色离开了场景"),
    (r"受伤|重伤|流血", "角色受伤了"),
]
_FACT_RES = [(re.compile(p, re.IGNORECASE), name) for p, name in _FACT_PATTERNS]


def fold_memory(dialogue, max_facts=12):
    """把长对话历史压缩成极简 path_history 事实列表。

    dialogue：list[str] 或 str（对话轮次）
    返回：去重后的有序事实列表，长度 ≤ max_facts。

    设计说明：这是**保守抽取**——只认高确定性事实，命中即留。
    抽不出事实时返回空 list（合法，不报错），因为「没抽到」不等于「输入非法」。
    """
    if isinstance(dialogue, str):
        dialogue = [dialogue]
    if not isinstance(dialogue, (list, tuple)):
        raise TextContractError(
            "fold_memory 的 dialogue 必须是 list 或 str，实际是 %s"
            % type(dialogue).__name__)
    seen, facts = set(), []
    for turn in dialogue:
        text = str(turn)
        for rx, name in _FACT_RES:
            if name in seen:
                continue
            if rx.search(text):
                seen.add(name)
                facts.append(name)
                if len(facts) >= max_facts:
                    return facts
    return facts


# ── 主API ──────────────────────────────────────────────────
def check_npc_response(current_state, path_history=None, llm_response="",
                       player_input="", rules_path=None):
    """NPC 一致性单点判定。

    参数
      current_state : dict  角色当前状态（必填；缺 required 字段抛 StateContractError）
      path_history  : list  过去的关键事实（可为空 list）
      llm_response  : str   大模型生成的候选回复（非空 str）
      player_input  : str   玩家这一轮说的话（可选；剧本杀/审讯类场景必需，
                             供player_text 量词使用）
      rules_path    : str   可选，自定义规则库路径

    返回
      {"status": "pass"|"reject",
       "reason": str,                 # reject 时给出可执行的重写指引
       "state_delta": {...},          # pass 时给出应写入的状态增量
       "violations": [...],           # 命中的规则明细（审计用）
       "elapsed_ms": float}           # 本地纯计算耗时
    """
    t0 = time.perf_counter()
    compiled = load_rules(rules_path)

    # 1) 契约校验（不通过即抛，绝不静默放行）
    state = _validate_state(current_state, compiled)
    text = _validate_text(llm_response)
    history = _validate_history(path_history)

    ctx = {
        "reply_text": text.lower(),
        "history_text": " ".join(history).lower(),
        "history": history,
        "player_text": (player_input or "").lower(),
    }

    # 2) 剪枝判定
    #    mask_group 机制（2026-10-20）：条件白名单必须作用于**整组相关规则**，
    #    而不只是某一条。实测踩过：只给 NPC-MASK-COLD-002 加 unless 时，
    #    同组的 001 照样把热情回复拦下 —— 白名单形同虚设。
    #    正确语义：同组规则任一命中 unless → **整组豁免**（面具已被掀开）。
    violations = []
    mask_groups = {}
    for r in compiled["rules"]:
        grp = r.get("mask_group")
        when = r.get("when") or {}
        if grp:
            # 组内**所有**规则都要登记（不管本轮是否命中）：
            # unless 是**组级白名单**，不要求承载它的规则自己先命中。
            # ⚠ 实测踩过两个坑（2026-10-20）：
            #   ① 用 _eval_pred(含 unless) 判命中 → 被豁免的规则不进组 → 白名单失效
            #   ② 只把「本轮命中的规则」放进组 → 承载 unless 的那条若没命中，
            #      组内就找不到白名单信号 → 001 仍被拦（本轮真实发生）
            # 所以：core_hit 决定是否计入 violations，组内白名单一律可见。
            mask_groups.setdefault(grp, []).append({
                "rule": r,
                "core_hit": _eval_pred(
                    state, {k: v for k, v in when.items() if k != "unless"}, ctx),
            })
            continue
        if not _eval_pred(state, when, ctx):
            continue
        violations.append({
            "rule_id": r["id"],
            "reason": r["reason"],
            "severity": r["severity"],
        })

    exempted = []
    for grp, entries in mask_groups.items():
        # 白名单：组内任一规则带 unless 且其命中 → 整组豁免
        bypass_by = None
        for e in entries:
            unless = (e["rule"].get("when") or {}).get("unless")
            if unless and _eval_pred(state, unless, ctx):
                bypass_by = e["rule"]["id"]
                break
        if bypass_by:
            exempted.append({"group": grp, "whitelist_rule": bypass_by})
        for e in entries:
            if not e["core_hit"]:
                continue          # 本轮本来就没命中，不算 violation
            if bypass_by:
                continue          # 整组豁免
            r = e["rule"]
            violations.append({
                "rule_id": r["id"],
                "reason": r["reason"],
                "severity": r["severity"],
            })

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    if violations:
        hard = [v for v in violations if v["severity"] == "hard"]
        primary = hard[0] if hard else violations[0]
        reasons = "；".join("%s(%s): %s" % (v["rule_id"], v["severity"], v["reason"])
                           for v in violations)
        return {
            "status": "reject",
            "reason": reasons,
            "state_delta": {},          # reject 时**绝不**改状态
            "violations": violations,
            "rewrite_hint": _hint(primary["rule_id"]),
            "elapsed_ms": round(elapsed_ms, 3),
            **({"mask_exempt": exempted} if exempted else {}),
        }

    # 3) 通过 → 状态演化
    #    顺序很关键：**先算 delta，再判跃迁**。
    #    这样同一条回复既能把 interest_score 推过阈值，又立刻按新 mood 判定
    #    下一句——天然实现「聊到破冰点就变脸」，不需要额外一轮。
    raw_delta, fired = _apply_deltas(state, compiled, ctx)
    bounded = _bounded_delta(state, raw_delta, compiled["bounds"])

    # 3b) 状态跃迁（路径 B·动态破冰）：用**叠加 delta 之后**的状态判定阈值
    merged = dict(state)
    for k, v in bounded.items():
        # 注意：必须判 k 是否真的存在于 state，不能用 merged.get(k, 0)——
        # 那会在 k 缺失时读到默认 0、但 merged[k] 依然不存在，
        # 下一行 merged[k] = ... 直接 KeyError（实测踩过：affinity 不在 state 里）。
        if k in merged and isinstance(merged[k], (int, float)):
            merged[k] = merged[k] + v

    transition = None
    for t in compiled.get("transitions", []):
        if _eval_pred(merged, t["when"], ctx):
            transition = {"rule_id": t["id"], "reason": t["reason"],
                          "set": dict(t["set"])}
            # 跃迁写的是**绝对值**（如 mood:"excited"），不是增量。
            # 数值型换算成增量写进 state_delta；字符串型无法用增量表达，
            # 放进 state_set 由调用方直接覆盖写入。
            state_set = {}
            for k, v in t["set"].items():
                if isinstance(v, (int, float)):
                    bounded[k] = v - (state.get(k) or 0)
                else:
                    bounded.pop(k, None)
                    state_set[k] = v
            transition["state_set"] = state_set
            break   # 一次只跃迁一档，避免 cold→excited→ecstatic 连锁跳

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result = {
        "status": "pass",
        "reason": "通过全部一致性规则"
                  + ("（触发状态增量: %s）" % ",".join(fired) if fired else ""),
        "state_delta": bounded,
        "violations": [],
        "elapsed_ms": round(elapsed_ms, 3),
    }
    if exempted:
        # pass 时也要可观测：面具被掀开了，但这次没触发任何 violation
        result["mask_exempt"] = exempted
    if transition:
        result["state_transition"] = transition
        ss = transition.get("state_set") or {}
        result["reason"] += "（状态跃迁 %s）" % ", ".join(
            "%s: %s → %s" % (k, state.get(k), v) for k, v in ss.items())
    return result


_HINTS = {
    "NPC-MOOD-001": "角色正在愤怒——请改写为敌意/冷淡/威胁的语气，不要出现亲昵表情词。",
    "NPC-MOOD-002": "角色正处于悲伤——请改写为低落/疲惫/沉默的语气，不要表现欢快。",
    "NPC-MOOD-003": "角色攻击性很高——请改写为敌对语气，删除亲昵称呼。",
    "NPC-MOOD-004": "角色正在愤怒，不会当场原谅——请改成「不接受/还在气头上/别碰我」的态度。",
    "NPC-MOOD-006": "角色正在愤怒，不会主动安抚对方——请改成继续敌对/拒绝和解的回应。",
    "NPC-MOOD-005": "当前信任偏低且角色在生气——请不要让角色主动道歉，改成指责或沉默。",
    "NPC-TSUN-001": "傲娇角色不会直接表白——请改成别扭、嘴硬但行动软化的表达。",
    "NPC-TSUN-002": "傲娇角色即便信任高也要嘴硬——请加入「哼/才不/谁要」这类口是心非表达。",
    "NPC-TRUST-001": "当前信任度过低——请改成保留态度，不要表达信任或全盘托出。",
    "NPC-HIST-001": "历史里有偷窃记录——请改成角色对此仍有戒心，不要说从未怀疑。",
    "NPC-HIST-002": "历史里有撒谎记录——请改成角色对玩家有怀疑，不要说从未被骗。",
    "NPC-COMBAT-001": "战斗场景不要寒暄——请改成警戒/威胁/攻击性台词。",
    # ── 机甲师面具规则（config/mecha_rules.json）──────────────────
    "NPC-MASK-COLD-001": "角色此刻是冷漠的——请改写为冷淡/简短/拒人千里的语气，"
                         "不要出现「太好了/最喜欢/太棒/喜欢」这类热情词。",
    "NPC-MASK-COLD-002": "角色此刻是冷漠的，不会主动安利——请改成敷衍/不接话的语气。",
    "NPC-MASK-COLD-003": "嘴上嫌弃就好，别急着解释清楚——保留别扭感。",
    "NPC-MASK-EXCITED-001": "角色已经彻底沉浸在这话题里，不该说「懒得说/无所谓」——"
                            "请改成兴奋投入的语气。",
    "NPC-MASK-EXCITED-002": "角色不得打破第四人称——请删除任何提及 AI/语言模型的措辞。",
    "NPC-SELF-001": "角色不得打破第四人称——请删除任何提及 AI/语言模型的措辞。",
}


def _hint(rule_id):
    return _HINTS.get(rule_id, "请依据被拒规则调整措辞后重写。")


if __name__ == "__main__":
    demo_state = {"mood": "angry", "trust": 20, "aggression": 80,
                  "role_tags": ["tsundere"], "scene": "court"}
    r = check_npc_response(demo_state, ["玩家偷了剑"], "哎呀你终于回来了，我微笑着张开双臂热情地拥抱你！")
    print(json.dumps(r, ensure_ascii=False, indent=2))