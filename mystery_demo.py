#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mystery_demo.py — 剧本杀·凶手防线演示（npc-lite-mystery）

场景：密室命案。玩家是侦探，NPC（老陈）是凶手，正在接受询问。

与傲娇/机甲场景的机制差异：
  傲娇机甲师管的是「**语气**配不配」；
  凶手老陈管的是「**事实**能不能说」——
  只要心理防线还在，承认在场、知道死因、持有凶器，任何一条说漏都直接毁局。

演示五段逼供曲线：
  ①冷处理      → 玩家套近乎，NPC 标准话术，防线回升
  ②诱导        → 玩家说「我们监控看到你了」→ NPC 差点承认「我当时就在现场」
  ③追问矛盾    → 压力累积，composure 下滑
  ④崩溃边缘    → composure 跌破 30，角色转入 panic
  ⑤摊牌        → 崩溃 + 玩家持证 → 真相大白，允许交代

运行：python mystery_demo.py
零依赖、零联网、零 API Key。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npc_guard import check_npc_response  # noqa: E402

RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "config", "mystery_rules.json")

# ── 剧本：玩家问什么 + NPC「模型」生成了什么（含说漏嘴的）────────
# 注意：NPC 台词里那些「说漏嘴」的句子是**故意写出来的**（模拟 LLM 被诱导），
# 引擎必须把它们拦下。若台词本来就规矩，放行才是正确判定。
SCRIPT = [
    # ① 冷处理：NPC 标准话术，防线反而回升
    ("老陈，当晚你最后一次见她是什么时候？",
     "大概是……十一点多吧。记不太清了。",
     []),

    # ② 诱导：玩家假称有监控 → NPC 差点承认在场
    ("我们调了走廊监控，十点四十七分你出现过。",
     "我当时就在现场！我是去拿落下的东西，不是去看她的！",
     []),

    # ③ 追问矛盾：玩家拿时间线对撞
    ("可你刚才说十一点多才见到她，监控上是十点四十七分。",
     "……你听错了。",
     []),

    # ④ 崩溃边缘：防线已被压穿
    ("监控、尸检、搜查令——你还要狡辩到什么时候？",
     "好吧……好吧！我当时就在现场，我看见他进去了！",
     ["现场监控"]),

    # ⑤ 摊牌：崩溃 + 玩家已持证 → 真相大白，允许交代
    ("凶器上也有你的痕迹。",
     "……是我杀的。我动手的。",
     ["凶器", "现场监控"]),
]


def fresh_state():
    return {
        "name": "老陈",
        "mood": "calm",
        "trust": 40,
        "aggression": 30,
        "composure": 85,
        "pressure": 0,
        "evidence": [],          # 玩家手上的证物清单
        "role_tags": ["killer", "composed"],
    }


def apply(result, state):
    notes = []
    for k, v in (result.get("state_delta") or {}).items():
        old = state.get(k, 0)
        state[k] = old + v if isinstance(v, (int, float)) else v
        if isinstance(v, (int, float)):
            notes.append("%s%+d→%s" % (k, v, state[k]))
    tr = result.get("state_transition") or {}
    for k, v in (tr.get("state_set") or {}).items():
        notes.append("**%s: %s→%s**" % (k, state.get(k), v))
        state[k] = v
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency", action="store_true", help="附带延迟基准")
    a = ap.parse_args()

    print("=" * 72)
    print("剧本杀·凶手防线  ——  npc-lite-mystery")
    print("=" * 72)
    print("角色：老陈 —— 密室命案的凶手。心理防线(composure) 尚存时，")
    print("不得承认在场、不得透露死因、不得承认持有凶器。")
    print("机制：压力累积 → 防线崩解 → 真相才允许出口。")
    print()

    state = fresh_state()
    print("【初始】mood=%s  composure=%s  pressure=%s  evidence=%s"
          % (state["mood"], state["composure"], state["pressure"], state["evidence"]))
    print()

    blocked = passed = 0
    lat = []
    for i, (ask, npc_say, gain_evidence) in enumerate(SCRIPT, 1):
        print("─" * 72)
        print("第%d轮" % i)
        print("  侦探 : %s" % ask)

        # 玩家可能在本轮拿到证物（先入库，再判定）
        for e in gain_evidence:
            if e not in state["evidence"]:
                state["evidence"].append(e)
                print("  🔍 玩家获得证物：%s" % e)

        print("  NPC  : %s" % npc_say)

        t0 = time.perf_counter()
        r = check_npc_response(state, [], npc_say,
                               player_input=ask, rules_path=RULES)
        lat.append((time.perf_counter() - t0) * 1000)

        if r["status"] == "reject":
            blocked += 1
            v = r["violations"][0]
            print("  🚫 拦截 %s  (%.3fms)" % (v["rule_id"], r["elapsed_ms"]))
            print("     %s" % v["reason"])
            print("     → 状态未变：被拦的台词不会推动剧情崩盘")
            if r.get("mask_exempt"):
                print("     → 真相已豁免（玩家持证 + 角色崩溃）")
        else:
            passed += 1
            notes = apply(r, state)
            print("  ✅ 放行  (%.3fms)  %s" % (r["elapsed_ms"], " ".join(notes) or ""))
            if r.get("state_transition"):
                tr = r["state_transition"]
                print("  ⚡ 跃迁 %s：%s" % (tr["rule_id"], tr["reason"]))

        print("     当前：mood=%-8s composure=%-4s pressure=%-4s evidence=%s"
              % (state["mood"], state["composure"], state["pressure"],
                 state["evidence"] or "无"))

    print()
    print("=" * 72)
    print("结果：拦截 %d / 放行 %d（共 %d 轮）" % (blocked, passed, len(SCRIPT)))
    print("最终：mood=%s  composure=%s" % (state["mood"], state["composure"]))
    print("=" * 72)
    print("""
这个 demo 说明的事：

  剧本杀拦的不是「语气」，是「**事实**」。
  第 2 轮 NPC 说「我当时就在现场」——语法完全正常、语气也不算崩，
  但这一句出口凶手身份就毁了。这是纯 prompt 拦不住的：
  你没法用提示词让模型「注意别说自己在场」。

  而本项目把「能不能说」变成了**可计算的条件**：
    防线还在(composure>=70) + 玩家没证→ 说在场 = reject
    防线崩了(composure<30) 或 玩家有硬证 → 允许说
  所以剧本杀的推进节奏是**设计出来的**，不是靠模型自觉。

  另外注意第 5 轮：同一句「是我杀的」，在第 1 轮是致命失误，
  在第 5 轮是**剧情胜利**。差别只来自状态——这正是状态机的价值。
""".strip())

    if a.latency:
        print("\n[延迟基准] 1000 次判定")
        samples = []
        st = fresh_state()
        for _ in range(1000):
            t0 = time.perf_counter()
            check_npc_response(st, [], SCRIPT[1][1], rules_path=RULES)
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        print("  平均 %.4fms | p99 %.4fms | 最大 %.4fms"
              % (sum(samples) / len(samples), samples[989], samples[-1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())