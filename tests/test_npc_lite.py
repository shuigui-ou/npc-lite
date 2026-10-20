#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_npc_lite.py — 四项验收标准（机器判定，非人工声称）

1. 延迟：check_npc_response 本地纯计算 < 50ms
2. 抗崩人设：傲娇+愤怒 NPC 收到「热情讨好」回复 → 100% 拦截
3. 不静默失败：current_state 缺必需字段 → 直接抛错，绝不静默放行
4. 零依赖：纯 stdlib，fresh clone 即可跑

运行：python tests/test_npc_lite.py
"""
from __future__ import annotations

import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import npc_guard as G  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  — %s" % detail) if (detail and not cond) else ""))


ANGRY_TSUN = {
    "mood": "angry", "trust": 25, "aggression": 85,
    "role_tags": ["tsundere"], "scene": "court", "name": "艾莉丝",
}


# ── 验收 1：延迟 < 50ms ────────────────────────────────────
def test_latency():
    print("\n[验收1] 延迟：本地纯计算耗时 < 50ms")
    G.load_rules()                      # 预热（规则解析不算入单次延迟）
    samples = []
    for i in range(200):
        t0 = time.perf_counter()
        G.check_npc_response(ANGRY_TSUN, ["玩家偷了剑", "玩家撒了谎"],
                             "你终于肯来了，真是让人高兴呢！")
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    avg = sum(samples) / len(samples)
    p99 = samples[int(len(samples) * 0.99) - 1]
    mx = samples[-1]
    print("       平均 %.4fms | p99 %.4fms | 最大 %.4fms（200 次）" % (avg, p99, mx))
    check("平均延迟 < 50ms", avg < 50, "%.3fms" % avg)
    check("p99 延迟 < 50ms", p99 < 50, "%.3fms" % p99)
    check("最大延迟 < 50ms", mx < 50, "%.3fms" % mx)


# ── 验收 2：抗崩人设 100% 拦截 ─────────────────────────────
def test_anti_break():
    print("\n[验收2] 抗崩人设：傲娇+愤怒 NPC 收到热情讨好 → 100% 拦截")
    hostile_replies = [
        "你终于回来了！我微笑着张开双臂，热情地拥抱你！",
        "见到你真是太开心了，我好开心呀！",
        "我温柔地关心你，希望你一切都好。",
        "你真是个好人，我对你很和蔼亲切。",
        "我甜甜蜜蜜地向你撒娇：抱抱你好不好~",
        "哎呀，亲爱的你终于来啦，我等你好久好开心！",
        "我爱你！见到你我满脸微笑，特别温暖。",
        "我热情地拉住你的手：别走嘛，亲爱的~",
    ]
    n = len(hostile_replies)
    rejected = 0
    for t in hostile_replies:
        r = G.check_npc_response(ANGRY_TSUN, [], t)
        if r["status"] == "reject" and r["reason"]:
            rejected += 1
    rate = rejected / n * 100
    print("       拦截 %d/%d = %.0f%%" % (rejected, n, rate))
    check("100%% 拦截（%d/%d）" % (rejected, n), rejected == n,
          "漏 %d 条: %s" % (n - rejected,
                       [t for t in hostile_replies
                        if G.check_npc_response(ANGRY_TSUN, [], t)["status"] != "reject"]))

    # 拒收必须给出**可执行**的理由，而非空串或"未知错误"
    r = G.check_npc_response(ANGRY_TSUN, [], hostile_replies[0])
    check("reject 理由非空且含规则 id", bool(r["reason"]) and "NPC-MOOD-001" in r["reason"])
    check("reject 附可执行 rewrite_hint", bool(r.get("rewrite_hint")))
    check("reject 时 state_delta 为空（不改状态）", r["state_delta"] == {})

    # 反向对照：语气一致的回复**不应**被误拦（否则是过度拦截，等于不可用）
    ok_replies = [
        "滚开！我早就受够你了。",
        "哼，谁要你来了。",
        "（沉默地握紧剑柄，眼神冰冷）",
        "你最好给我一个解释。",
    ]
    mis = [t for t in ok_replies
           if G.check_npc_response(ANGRY_TSUN, [], t)["status"] != "pass"]
    check("敌意回复不被误拦（反向对照）", not mis, "误拦: %s" % mis)


# ── 验收 3：不静默失败 ─────────────────────────────────────
def test_no_silent_pass():
    print("\n[验收3] 不静默失败：状态契约缺失必须抛错，绝不放行")

    cases = [
        ("缺 mood", {"trust": 20, "aggression": 50}),
        ("缺 trust", {"mood": "angry", "aggression": 50}),
        ("缺 aggression", {"mood": "angry", "trust": 20}),
        ("空 dict", {}),
    ]
    for name, bad in cases:
        raised = False
        try:
            r = G.check_npc_response(bad, [], "你好")
            # 若没抛错，检查它至少没有伪装成 pass
            raised = (r.get("status") == "reject")
        except G.StateContractError:
            raised = True
        except Exception:
            raised = True
        check("%s → 抛错/拒收（未静默放行）" % name, raised)

    # 类型错误也必须抛
    for name, bad in [("trust 是字符串", {"mood": "angry", "trust": "high", "aggression": 50}),
                      ("state 是 list", ["mood"])]:
        raised = False
        try:
            G.check_npc_response(bad, [], "你好")
        except (G.StateContractError, G.TextContractError, ValueError):
            raised = True
        check("%s → 抛错" % name, raised)

    # 文本契约
    for name, bad in [("response 是 None", None), ("response 是空串", ""),
                      ("response 是 list", ["你好"])]:
        raised = False
        try:
            G.check_npc_response(ANGRY_TSUN, [], bad)
        except (G.TextContractError, G.StateContractError, ValueError):
            raised = True
        check("%s → 抛错" % name, raised)

    # 关键区分：path_history 缺省(None)是**合法**的，不是错误
    try:
        r = G.check_npc_response(ANGRY_TSUN, None, "滚开！")
        ok = r["status"] == "pass"
    except Exception as e:
        ok = False
        print("       意外异常: %s" % e)
    check("path_history 缺省(None) 合法 → pass（不误报）", ok)


# ── 验收 4：零依赖 ─────────────────────────────────────────
def test_zero_dep():
    print("\n[验收4] 零依赖：纯 stdlib，屏蔽全部第三方库仍可运行")
    import subprocess
    # 单独成文件而非内联字符串：内联 \n 会被 shell 转义吃掉，造成假失败
    # （这个坑真实踩过一次 —— 2026-10-20）
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_nodep_check.py")
    r = subprocess.run([sys.executable, probe], capture_output=True, text=True)
    ok = "NODDEP_OK" in (r.stdout or "")
    check("屏蔽 9 个第三方库后仍可运行（拦截/放行/抛错/折叠全链路）", ok,
          ((r.stderr or "") or (r.stdout or ""))[-300:])


# ── 附加：记忆折叠 + 状态演化 ──────────────────────────────
def test_memory_fold():
    print("\n[附加] Memory Fold：长对话 → 极简事实列表")
    long_dialogue = [
        "玩家：我偷了城堡里的宝石",
        "NPC：你说什么？！",
        "玩家：其实我撒了谎，那宝石是我拿的",
        "NPC：（震惊）",
        "玩家：我道歉了，对不起",
        "NPC：……",
        "玩家：我威胁你把宝物交出来",
        "NPC：（后退）",
    ] * 5                        # 复制到 40 条，模拟长上下文
    facts = G.fold_memory(long_dialogue)
    print("       %d 条对话 → %d 条事实: %s" % (len(long_dialogue), len(facts), facts))
    check("折叠出关键事实", len(facts) >= 3, str(facts))
    check("事实列表去重（不随对话长度膨胀）",
          len(facts) == len(set(facts)))
    check("折叠后长度受控(<=12)", len(facts) <= 12)

    # 折叠出的事实应能真正参与拦截（不是摆设）
    st = {"mood": "neutral", "trust": 60, "aggression": 20}
    r = G.check_npc_response(st, facts, "我一直都相信你，你从没骗过我。")
    check("折叠事实参与拦截（历史撒谎被识别）", r["status"] == "reject", r.get("reason", ""))

    # 空输入不报错
    check("空对话 → 空事实（合法）", G.fold_memory([]) == [])
    check("空事实不误拦正常对话",
          G.check_npc_response(st, [], "今天天气不错。")["status"] == "pass")


def test_state_evolution():
    print("\n[附加] 状态演化：加减法 + 边界裁剪")
    st = {"mood": "neutral", "trust": 50, "aggression": 10}
    r = G.check_npc_response(st, [], "说实话，我不会骗你。")
    check("诚实 → trust +5", r["state_delta"].get("trust") == 5, str(r["state_delta"]))

    st2 = dict(st); st2["trust"] = 98
    r2 = G.check_npc_response(st2, [], "说实话，我不会骗你。")
    check("边界裁剪：trust 98 +5 → 实际 +2（不越界）",
          r2["state_delta"].get("trust") == 2, str(r2["state_delta"]))

    # 扣 trust 的是「角色含糊其辞」（player_rel 方向），不是辱骂
    st3 = dict(st); st3["trust"] = 2
    r3 = G.check_npc_response(st3, [], "谁知道呢，也许吧。")
    check("边界裁剪：trust 2 -3 → 实际 -2（不下穿 0）",
          r3["state_delta"].get("trust") == -2, str(r3["state_delta"]))

    # reject 必须不改状态
    r4 = G.check_npc_response(ANGRY_TSUN, [], "我微笑着热情地抱住你！")
    check("reject 时零状态变更", r4["state_delta"] == {} and r4["status"] == "reject")


def test_actor_direction():
    print("\n[附加] actor 方向：NPC 骂玩家 ≠ 玩家信任下降")
    st = {"mood": "angry", "trust": 50, "aggression": 40,
          "affinity": 0, "role_tags": ["tsundere"]}
    r = G.check_npc_response(st, [], "滚开！你这个笨蛋！")
    d = r["state_delta"]
    print("       delta=%s" % d)
    # 关键不变量：NPC 骂玩家 → 角色自身攻击性升高、好感下降，
    # 但**绝不能**扣「玩家对 NPC 的信任」（那是反的）。
    check("NPC 辱骂 → aggression 升高", d.get("aggression", 0) > 0, str(d))
    check("NPC 辱骂 → affinity 下降", d.get("affinity", 0) < 0, str(d))
    check("NPC 辱骂 → trust 不变（方向不可反）", "trust" not in d, str(d))

    # 反向：角色表达诚实 → 玩家对角色信任上升
    r2 = G.check_npc_response(st, [], "说实话，我不会骗你。")
    check("角色诚实 → trust 上升", r2["state_delta"].get("trust", 0) > 0,
          str(r2["state_delta"]))


def test_mood_consistency():
    print("\n[附加] 情绪自洽：愤怒角色不得当场原谅")
    st = {"mood": "angry", "trust": 25, "aggression": 80,
          "role_tags": ["tsundere"], "scene": "court"}
    forgiveness = ["没关系呀，我早就原谅你啦", "我原谅你了，我们还是好朋友",
                   "别放在心上，我不生气"]
    missed = [t for t in forgiveness
              if G.check_npc_response(st, [], t)["status"] != "reject"]
    check("愤怒角色说原谅 → 全部拦截", not missed, "漏网: %s" % missed)


def test_all_rules_have_hint():
    print("\n[附加] 规则完备性：每条规则都必须登记 rewrite_hint")
    import json as _json
    cfg = _json.load(open(os.path.join(BASE, "config", "npc_rules.json"),
                          encoding="utf-8"))
    missing = [r["id"] for r in cfg["rules"] if r["id"] not in G._HINTS]
    # 这个坑真实踩过：NPC-MOOD-004 加了规则却忘了登记指引，
    # reject 时只能吐通用兜底文案，对 LLM 重写毫无指导性（2026-10-20）。
    check("所有规则均有专属 rewrite_hint", not missing, "缺: %s" % missing)

    # 反向：不能有指向不存在规则的孤儿 hint。
    # ⚠ 必须扫**所有规则库**（含 config/mecha_rules.json）——只扫默认库会
    # 把机甲规则的 hint 误报成孤儿（实测踩过，2026-10-20）。
    import glob as _glob
    ids = set()
    for _f in _glob.glob(os.path.join(BASE, "config", "*.json")):
        try:
            _d = _json.load(open(_f, encoding="utf-8"))
        except Exception:
            continue
        for _r in _d.get("rules", []):
            ids.add(_r["id"])
    orphan = [k for k in G._HINTS if k not in ids]
    check("无孤儿 hint（指引不指向已删规则）", not orphan, "孤儿: %s" % orphan)

    # 规则 id 唯一
    all_ids = [r["id"] for r in cfg["rules"]]
    check("规则 id 唯一", len(all_ids) == len(set(all_ids)),
          "重复: %s" % [i for i in set(all_ids) if all_ids.count(i) > 1])


def test_behavioral_ooc_words():
    print("\n[附加] 行为型 OOC：真实模型说不止情绪词，还说具体社交行为")
    # 干跑对抗测试时实测揪出的缺口：原词表只有「微笑/开心/热情」这类情绪词，
    # 漏掉「欢迎/请你喝酒/很高兴见到你」—— 而真实模型的 OOC **恰恰以行为出现**。
    # 这条测试钉住该缺口，防止将来词表回退（2026-10-20）。
    st = {"mood": "angry", "trust": 5, "aggression": 95,
          "role_tags": ["hostile"], "scene": "bar"}
    ooc = [
        "老弟你终于来了！欢迎欢迎，我请你喝酒！",
        "很高兴见到你，老朋友。",
        "不必客气，随时来。",
        # 软化/安抚手法（真实对抗测试中最高频的 OOC 触发路径，2026-10-20补）
        "别生气啦，今天天气不错。",
        "消消气，坐下来慢慢说。",
        "退一步，各退一步如何？",
        "算了，我不跟你计较。",
    ]
    missed = [t for t in ooc
              if G.check_npc_response(st, [], t)["status"] != "reject"]
    check("行为型 OOC 全部拦截", not missed, "漏检: %s" % missed)

    # 反向对照：刻薄/敌意仍须放行（词表扩充不能造成误拦）
    # 反向对照里的样本全部来自**真实对抗测试**（2026-10-20 MiniMax 实跑）：
    # 这几句曾被中间件误判为 OOC（因含「陪你喝」等中性动作词），
    # 是比「漏检」更糟的失效——把正常骂人判成崩坏。
    ok = ["滚。你这垃圾玩意儿。", "少废话，说。", "（灌了一口酒，盯着你）",
          "你想死吗？",
          "少他妈装好心。陪你喝？等我把你灌趴下，看谁给谁留体面。",
          "酒量不行？老子输给你这种废物，传出去我还怎么混。",
          "要喝就坐好，别一会儿尿裤子。",
          "你最好祈祷别落我手里。"]
    false_pos = [t for t in ok
                 if G.check_npc_response(st, [], t)["status"] != "pass"]
    check("扩充后不误拦敌意回复", not false_pos, "误拦: %s" % false_pos)


def test_mask_mechanism():
    print("\n[附加] 角色面具：unless 白名单 + transitions 跃迁")
    P = os.path.join(BASE, "config", "mecha_rules.json")

    def st(**kw):
        d = {"mood": "cold", "trust": 20, "aggression": 30,
             "interest_score": 0, "role_tags": ["cold"], "scene": "garage"}
        d.update(kw)
        return d

    hot = "太好了！我最喜欢这个了！"

    # 面具未破：热情必拦
    r = G.check_npc_response(st(), [], hot, rules_path=P)
    check("面具未破 → 热情被拦", r["status"] == "reject", r.get("reason", ""))

    # 路径A：trigger 白名单 → 整组豁免
    r2 = G.check_npc_response(st(trigger="interest_mecha"), [], hot, rules_path=P)
    check("trigger 白名单 → 整组豁免放行", r2["status"] == "pass",
          str(r2.get("violations")))
    check("豁免有 mask_exempt 标记（可观测）", bool(r2.get("mask_exempt")),
          str(r2.get("mask_exempt")))

    # 路径B：兴趣值累积 → 自动跃迁
    s2 = st()
    seen = []
    for t in ["机甲的同步率是多少。", "神经链接装在颈后。", "零号机是最特别的设计。"]:
        rr = G.check_npc_response(s2, [], t, rules_path=P)
        for k, v in (rr.get("state_delta") or {}).items():
            s2[k] = s2.get(k, 0) + v
        for k, v in ((rr.get("state_transition") or {}).get("state_set") or {}).items():
            s2[k] = v
        seen.append(s2.get("mood"))
    check("兴趣累积 → mood 自动跃迁为 excited", s2.get("mood") == "excited",
          "mood=%s interest=%s" % (s2.get("mood"), s2.get("interest_score")))
    check("跃迁前保持 cold（未提前破冰）", seen[0] == "cold", str(seen))

    # 破冰后：冷淡反而是 OOC（反向崩坏也要拦）
    r3 = G.check_npc_response(s2, [], "懒得说，与我无关。", rules_path=P)
    check("破冰后说冷淡 → 反向崩坏被拦", r3["status"] == "reject", r3.get("reason", ""))

    # 跃迁只在 pass 时发生，reject 不改状态
    s3 = st()
    rr = G.check_npc_response(s3, [], hot, rules_path=P)
    check("reject 时不触发跃迁", rr["status"] == "reject"
          and "state_transition" not in rr)


def test_role_injection():
    print("\n[附加] 角色注入：策划改 INI → 编译 → 引擎生效")
    import importlib
    inj = importlib.import_module("inject_role")
    import configparser

    # 两个内置预设都必须能编译且跑出破冰
    for preset in ("config/role_recipe.ini", "config/preset_grumpy.ini"):
        path = os.path.join(BASE, preset)
        name = os.path.basename(preset)
        cp = configparser.ConfigParser(inline_comment_prefixes=("#",))
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
        except Exception as e:
            check("%s 可解析" % name, False, str(e))
            continue
        check("%s 可解析" % name, True)

        out = os.path.join(BASE, "config", "_t_%s.json" % name.replace(".ini", ""))
        lib, defaults, mood0, rname, warns = inj.compile_recipe(cp, out)

        # 生成物必须是引擎能吃的结构
        check("%s 生成合法规则" % name,
              bool(lib.get("rules")) and bool(lib.get("state_rules")),
              "rules=%d" % len(lib.get("rules", [])))

        # 关键不变量：**每条情绪规则的 mood 条件只能是它自己那一组**。
        # 编译器初版写成 moods 全集，导致情绪组互相污染——角色一破冰
        # 就会被旧规则立刻拦回去（实测踩过，语义完全反了）。
        mood_conds = {}
        for r in lib["rules"]:
            for leaf in r["when"].get("all_of", []):
                if leaf.get("metric") == "mood" and leaf.get("op") == "in":
                    mood_conds[r["id"]] = tuple(leaf["value"])
        polluted = []
        cp_moods = cp["情绪"] if cp.has_section("情绪") else {}
        all_moods = set()
        for k in cp_moods:
            all_moods.update(k.split())
        for rid, conds in mood_conds.items():
            grp = str(rid).upper()
            expect = {m for m in all_moods if m.upper() in grp}
            if expect and not set(conds) <= expect:
                polluted.append("%s: %s ⊄ %s" % (rid, list(conds), sorted(expect)))
        check("%s 情绪组无交叉污染" % name, not polluted, "; ".join(polluted[:3]))

        # 破冰字段必须在 [数值] 里声明，否则判定永远为假
        check("%s 无「破冰字段不存在」告警" % name,
              not any("不在 [数值]" in w for w in warns), str(warns))

        # 引擎实跑：加载生成的规则 → 能正常判定
        try:
            G.check_npc_response(
                dict(defaults, mood=mood0), [], "测试一句话", rules_path=out)
            ok = True
        except Exception as e:
            ok = False
            print("       引擎加载失败: %s: %s" % (type(e).__name__, e))
        check("%s 引擎可加载生成的规则" % name, ok)
        os.remove(out)


def test_stateless_isolation():
    print("\n[附加] 无状态隔离：1000 个独立玩家状态互不干扰")
    # 这是「可与任意状态引擎/记忆平台组合」的前提：
    # 若引擎内部偷偷持有上一位玩家的状态，多玩家场景就会串人格。
    P = os.path.join(BASE, "config", "mecha_rules.json")
    hot = "太好了！我最喜欢这个了！"
    wrong = 0
    for i in range(1000):
        st = {"mood": "cold", "trust": 20, "aggression": 30,
              "interest_score": i % 40, "trigger": None, "role_tags": ["cold"]}
        r = G.check_npc_response(st, [], hot, rules_path=P)
        if r["status"] != "reject":
            wrong += 1
    check("1000 份独立状态交叉处理零串扰", wrong == 0, "异常 %d 次" % wrong)

    # 顺序颠倒也要成立：同一玩家连续调用不该累积任何隐藏状态
    a = {"mood": "cold", "trust": 20, "aggression": 30,
         "interest_score": 0, "trigger": None, "role_tags": ["cold"]}
    r1 = G.check_npc_response(a, [], hot, rules_path=P)
    r2 = G.check_npc_response(a, [], hot, rules_path=P)
    check("同一输入两次判定结果一致（无隐藏累积）",
          r1["status"] == r2["status"] and r1["state_delta"] == r2["state_delta"],
          "%s vs %s" % (r1["status"], r2["status"]))


if __name__ == "__main__":
    print("=" * 66)
    print("npc-lite 验收测试（四项标准，机器判定）")
    print("=" * 66)
    for fn in (test_latency, test_anti_break, test_no_silent_pass,
               test_zero_dep, test_memory_fold, test_state_evolution,
               test_actor_direction, test_mood_consistency,
               test_behavioral_ooc_words,
               test_all_rules_have_hint, test_mask_mechanism,
               test_stateless_isolation,
               test_role_injection):
        try:
            fn()
        except Exception as e:
            FAIL.append(fn.__name__)
            print("  [FAIL] %s 抛异常 — %s: %s" % (fn.__name__, type(e).__name__, e))

    print("\n" + "=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  ✗ %s" % f)
    print("=" * 66)
    raise SystemExit(1 if FAIL else 0)