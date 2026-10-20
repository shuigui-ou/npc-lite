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
    }
    for r in raw.get("rules", []):
        out["rules"].append({
            "id": r["id"],
            "when": r.get("when", {}),
            "reason": r.get("reason", ""),
            "severity": r.get("severity", "hard"),
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
    return current_state.get(metric)


def _text_contains_any(haystack, needles):
    return any(n in haystack for n in needles)


def _eval_leaf(current_state, leaf, ctx):
    metric = leaf.get("metric")
    op = leaf.get("op")
    target = leaf.get("value")

    if op in ("contains_any", "contains_all", "contains_none"):
        hay = _get_metric(current_state, metric, ctx)
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
                       rules_path=None):
    """NPC 一致性单点判定。

    参数
      current_state : dict  角色当前状态（必填；缺 required 字段抛 StateContractError）
      path_history  : list  过去的关键事实（可为空 list）
      llm_response  : str   大模型生成的候选回复（非空 str）
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
    }

    # 2) 剪枝判定
    violations = []
    for r in compiled["rules"]:
        if _eval_pred(state, r["when"], ctx):
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
        }

    # 3) 通过 → 计算状态增量（裁剪到边界后回算实际值）
    raw_delta, fired = _apply_deltas(state, compiled, ctx)
    bounded = _bounded_delta(state, raw_delta, compiled["bounds"])

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "status": "pass",
        "reason": "通过全部一致性规则" + ("（触发状态增量: %s）" % ",".join(fired) if fired else ""),
        "state_delta": bounded,
        "violations": [],
        "elapsed_ms": round(elapsed_ms, 3),
    }


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
    "NPC-SELF-001": "角色不得打破第四人称——请删除任何提及 AI/语言模型的措辞。",
}


def _hint(rule_id):
    return _HINTS.get(rule_id, "请依据被拒规则调整措辞后重写。")


if __name__ == "__main__":
    demo_state = {"mood": "angry", "trust": 20, "aggression": 80,
                  "role_tags": ["tsundere"], "scene": "court"}
    r = check_npc_response(demo_state, ["玩家偷了剑"], "哎呀你终于回来了，我微笑着张开双臂热情地拥抱你！")
    print(json.dumps(r, ensure_ascii=False, indent=2))