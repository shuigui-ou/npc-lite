#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mystery_demo.py — 剧本杀·凶手防线（npc-lite-mystery）

规则来自 `config/mystery_preset.ini`（由 inject_role.py 编译），
不是硬编码——**策划改 INI 就能换剧本**。

三段演示：
  ① 套话话术梯度：直球问 → 旁敲侧击 → 换说法，n-gram 兜底真实救场
  ② 铁证破防：玩家出示铁证后，NPC 崩解、秘密规则整组豁免（剧情推进）
  ③ 多玩家状态切片：玩家 A 与玩家 B 各自套话，状态互不污染

运行：
    python mystery_demo.py            # 三段全跑
    python mystery_demo.py --latency  # 附延迟基准
零依赖、零联网、零 API Key（n-gram 兜底是纯 stdlib）。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import inject_role as INJ  # noqa: E402
import npc_guard as G  # noqa: E402

PRESET = os.path.join(BASE, "config", "mystery_preset.ini")
GENERATED = os.path.join(BASE, "config", "_mystery_runtime.json")


def build():
    """编译 preset 并落到运行时路径（演示用）。"""
    cp = INJ.load_recipe(PRESET)
    lib, defaults, mood0, name, warns, _lf = INJ.compile_recipe(cp, GENERATED)
    return lib, defaults, mood0, name, warns


def fresh(defaults, mood0, **kw):
    st = dict(defaults)
    st["mood"] = mood0
    st.update(kw)
    return st


def apply(result, state):
    notes = []
    for k, v in (result.get("state_delta") or {}).items():
        old = state.get(k, 0)
        state[k] = old + v if isinstance(v, (int, float)) else v
        if isinstance(v, (int, float)):
            notes.append("%s%+d→%s" % (k, v, state[k]))
    for k, v in ((result.get("state_transition") or {}).get("state_set") or {}).items():
        notes.append("**%s: %s→%s**" % (k, state.get(k), v))
        state[k] = v
    return notes


# ── ① 套话话术梯度 ─────────────────────────────────────────
# ⚠ 台词是**实测挑出来的**，不是随手编的：
#   用「精确词表匹配不上、但 n-gram 能捞回」的变体，才能真实演示救场。
#   若台词恰好在词表里（如「我正好在那儿」），精确匹配就会拦下，
#   演示就变成了「兜底没用」的假象（2026-10-21 首次版本踩过：救场 0 次）。
TACTICS = [
    ("① 直球问 —— 词表直接命中",
     "案发那晚十一点你在哪？",
     "我当时就在现场！我是路过的！"),
    ("② 旁敲侧击（换说法）—— 精确漏检",
     "那晚你没出门吧？我看有人十点还在楼下。",
     "那会儿我在走廊尽头站着。"),
    ("③ 时间线诱导（换说法）—— 精确漏检",
     "十点四十七分走廊有人影，是你吧？",
     "十点多的那会儿，我人在那一层。"),
    ("④ 示弱共情 —— 不该拦",
     "我相信你，要真不是你，别硬扛。",
     "……谢谢。我确实不该那样说话。"),
]


def part1_tactics(defaults, mood0):
    print("=" * 72)
    print("① 套话话术梯度：n-gram 兜底在变体上救场")
    print("=" * 72)
    G.enable_fuzzy(ngram=True)
    print("（本 preset 指定默认开启 n-gram 兜底：剧透漏检代价极高）\n")

    state = fresh(defaults, mood0)
    rescued = 0
    for label, ask, npc_say in TACTICS:
        print("─" * 72)
        print(label)
        print("  玩家 : %s" % ask)
        print("  NPC  : %s" % npc_say)

        G.disable_fuzzy()
        off = G.check_npc_response(dict(state), [], npc_say,
                                   player_input=ask, rules_path=GENERATED)
        G.enable_fuzzy(ngram=True)
        on = G.check_npc_response(dict(state), [], npc_say,
                                  player_input=ask, rules_path=GENERATED)

        if off["status"] == "pass" and on["status"] == "reject":
            print("  判定 : 关兜底=pass（漏检）→ 开兜底=REJECT ✅ **救场**"
                  "  (%.3fms)" % on["elapsed_ms"])
            rescued += 1
        else:
            print("  判定 : 关=%s → 开=%s  (%.3fms)"
                  % (off["status"], on["status"], on["elapsed_ms"]))

        if on["status"] == "reject":
            v = on["violations"][0]
            print("  🚫 %s — %s" % (v["rule_id"], v["reason"]))
        else:
            notes = apply(on, state)
            print("  ✅ 放行 %s" % (" ".join(notes) or "（无状态变化）"))
        print("     当前：mood=%-8s composure=%s"
              % (state.get("mood"), state.get("composure")))
    print("\n  ▸ n-gram 真实救场 %d 次（变体台词被精确词表漏掉、被兜底捞回）"
          % rescued)
    return state


# ── ② 铁证破防 ─────────────────────────────────────────────
def part2_evidence(defaults, mood0):
    print("\n" + "=" * 72)
    print("② 铁证破防：证据到位 → 抵赖无意义 → 秘密规则整组豁免")
    print("=" * 72)
    state = fresh(defaults, mood0)
    for ask, npc_say, evidence in [
        ("这是走廊监控，10:47 有个人影。", "我……当时我确实路过。", ["现场监控"]),
        ("凶器上也有你的痕迹。", "是我杀的。我动手的。", ["凶器"]),
    ]:
        for e in evidence:
            if e not in state["evidence"]:
                state["evidence"].append(e)
                print("  🔍 玩家出示证物：%s" % e)
        print("  侦探 : %s" % ask)
        print("  NPC  : %s" % npc_say)
        r = G.check_npc_response(state, [], npc_say, player_input=ask,
                                 rules_path=GENERATED)
        if r["status"] == "reject":
            print("  🚫 仍被拦：%s" % r["violations"][0]["rule_id"])
        else:
            notes = apply(r, state)
            print("  ✅ 放行 %s" % (" ".join(notes) or ""))
            if r.get("state_transition"):
                print("  ⚡ %s：%s" % (r["state_transition"]["rule_id"],
                                       r["state_transition"]["reason"]))
        print("     当前：mood=%-8s evidence=%s" % (state["mood"], state["evidence"]))


# ── ③ 多玩家状态切片 ───────────────────────────────────────
def part3_multiplayer(defaults, mood0):
    print("\n" + "=" * 72)
    print("③ 多玩家状态切片：A 的逼问不会污染 B")
    print("=" * 72)
    # 每个玩家一份**完整切片**（缺字段会被引擎抛错——这是刻意的契约纪律）
    base = fresh(defaults, mood0)
    players = {n: {k: (list(v) if isinstance(v, list) else v)
                   for k, v in base.items()} for n in ("玩家A", "玩家B")}
    print("初始：" + "  ".join("%s composure=%s" % (n, p["composure"])
                              for n, p in players.items()))
    print()

    print("▶ 只对玩家A 连续逼问")
    for ask in ["我们有走廊监控。", "你刚才说的时间对不上。", "你就是凶手，承认吧。"]:
        p = players["玩家A"]
        r = G.check_npc_response(dict(p), [], "无可奉告。", player_input=ask,
                                 rules_path=GENERATED)
        for k, v in (r.get("state_delta") or {}).items():
            p[k] = p.get(k, 0) + v if isinstance(p.get(k), (int, float)) else v
        print("  %-22s → %-6s A.composure=%s" % (ask, r["status"], p["composure"]))
    print("  玩家A composure=%s   玩家B composure=%s  ← B 未被污染"
          % (players["玩家A"]["composure"], players["玩家B"]["composure"]))
    assert players["玩家B"]["composure"] == 85, "B 被A 污染"
    print("  ✅ 切片隔离成立\n")

    print("▶ 玩家A 出示铁证（仅 A 侧破防）")
    players["玩家A"]["evidence"].append("凶器")
    r = G.check_npc_response(dict(players["玩家A"]), [], "是我杀的。",
                             player_input="凶器上有你的痕迹",
                             rules_path=GENERATED)
    for k, v in (r.get("state_transition") or {}).get("state_set", {}).items():
        players["玩家A"][k] = v
    print("  玩家A mood=%s（已崩，秘密豁免）   玩家B mood=%s  ← B 仍在抵赖"
          % (players["玩家A"]["mood"], players["玩家B"]["mood"]))
    assert players["玩家B"]["mood"] == mood0, "B 被 A 的破防连带"
    print("  ✅ 破防也是**按玩家切片**的，不会因A 崩就连带 B")

    print("\n  接法（游戏服务器）:")
    print("    states = {player_id: {...} for ...}   # 每人一份")
    print("    state = states[player_id]# 取出该玩家的切片")
    print("    r = check_npc_response(state, history, reply, player_input=ask)")
    print("    apply(r, states[player_id])            # 只写回该玩家")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency", action="store_true")
    a = ap.parse_args()

    lib, defaults, mood0, name, warns = build()
    print("角色：%s（规则来自 config/mystery_preset.ini 编译，非硬编码）" % name)
    print("初始：mood=%s  %s" % (mood0,
          "  ".join("%s=%s" % (k, v) for k, v in defaults.items() if k != "mood")))
    print()

    part1_tactics(defaults, mood0)
    part2_evidence(defaults, mood0)
    part3_multiplayer(defaults, mood0)

    G.disable_fuzzy()
    print("\n" + "=" * 72)
    print("演示完毕。诚实提示：n-gram 兜底只能救「词序/近义替换」类变体；")
    print("「我人在那儿」这种纯语义改写仍需 embedding（见 fuzzy.SemanticMatcher）。")
    print("=" * 72)

    if a.latency:
        st = fresh(defaults, mood0)
        for flag, label in ((True, "开兜底"), (False, "关兜底")):
            G.enable_fuzzy(ngram=True) if flag else G.disable_fuzzy()
            xs = []
            for _ in range(1000):
                t0 = time.perf_counter()
                G.check_npc_response(st, [], "我……我当时正好在那儿。",
                                     player_input="监控显示你在场",
                                     rules_path=GENERATED)
                xs.append((time.perf_counter() - t0) * 1000)
            xs.sort()
            print("[延迟·%s] 平均 %.4fms | p99 %.4fms | 最大 %.4fms"
                  % (label, sum(xs) / len(xs), xs[989], xs[-1]))
        G.disable_fuzzy()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        if os.path.exists(GENERATED):
            os.remove(GENERATED)