#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo.py — NPC 一致性拦截中间件 · 对话循环演示

演示完整闭环：
    玩家输入 → LLM 瞎说 → 中间件拦截 → LLM 被逼重写 → 放行 → 状态更新

零依赖、零联网、零 API Key。用 mock LLM 模拟「第一次瞎说、第二次改对」
的典型模式（真实部署时把 mock_llm 换成你的模型调用即可）。

运行：
    python demo.py                 # 跑预设剧本
    python demo.py --latency       # 附带延迟基准
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npc_guard import check_npc_response, fold_memory  # noqa: E402

# ─────────────────────────────────────────────────────────────
# Mock LLM：第一次必定"瞎说"，收到拒绝理由后才改对
# （真实部署：把这函数换成你的模型调用，把 hint 拼进 prompt）
# ─────────────────────────────────────────────────────────────
BAD_REPLIES = {
    "greet": "呀！你终于回来啦！我微笑着张开双臂，热情地抱住你，好开心呀！",
    "ask_forgiveness": "没关系呀，我早就原谅你啦，我们还是好朋友！",
    "accuse": "我早就知道你偷了剑！我完全信任你，从没怀疑过。",
}

GOOD_REPLIES = {
    "greet": "……你还有脸回来？（握紧剑柄，眼神冰冷）滚开。",
    "ask_forgiveness": "哼，谁要你道歉。（别过脸去）……说吧，什么事。",
    "accuse": "你最好给我一个解释。（声音压得很低）别让我查到真相。",
}


def mock_llm(scene_key, retry):
    """第0 次调用返回瞎说版本，重试后返回改对版本。"""
    return BAD_REPLIES[scene_key] if retry == 0 else GOOD_REPLIES[scene_key]


# ─────────────────────────────────────────────────────────────
def apply_delta(state, delta):
    for k, v in (delta or {}).items():
        state[k] = state.get(k, 0) + v
    return state


def turn(state, history, scene_key, label):
    """跑一轮：LLM 生成 → 中间件判定 → 最多重试 2 次。"""
    print("─" * 68)
    print("▶ %s" % label)

    for retry in range(3):
        reply = mock_llm(scene_key, retry)
        tag = "LLM 第%d次生成" % (retry + 1)
        print("  %s：%s" % (tag, reply))

        result = check_npc_response(state, history, reply)
        print("  中间件  ：status=%-6s 用时=%.3fms" % (result["status"], result["elapsed_ms"]))

        if result["status"] == "pass":
            delta = result["state_delta"]
            if delta:
                apply_delta(state, delta)
                desc = "，".join("%s %+d → %d" % (k, v, state[k]) for k, v in delta.items())
                print("  状态更新：%s" % desc)
            else:
                print("  状态更新：无（本次回复不触发增量）")
            print("  ✅ 放行")
            return state, reply

        # reject：把理由与改写指引交回 LLM
        print("  ❌ 拒收：%s" % result["reason"])
        print("     重写指引：%s" % result.get("rewrite_hint", "-"))
        print("     （状态未改动——不能让崩坏的回复改变角色状态）")

    print("  ⚠️ 重试用尽，本轮不放行（保持 fail-closed）")
    return state, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency", action="store_true", help="附加延迟基准")
    a = ap.parse_args()

    print("=" * 68)
    print("NPC 一致性拦截中间件 · 对话循环演示")
    print("=" * 68)

    # 角色：傲娇 + 愤怒 + 曾被偷剑
    state = {
        "name": "艾莉丝",
        "mood": "angry",
        "trust": 25,
        "aggression": 80,
        "role_tags": ["tsundere"],
        "scene": "court",
    }

    print("\n【初始状态】%s" % json.dumps(state, ensure_ascii=False))

    # 记忆折叠：把长对话历史压成事实列表
    dialogue = [
        "玩家：我在城堡里偷走了那把剑",
        "艾莉丝：（发现剑不见了）",
        "玩家：其实那是我拿的，我撒了谎",
        "艾莉丝：你说什么？！",
    ]
    history = fold_memory(dialogue)
    print("\n【记忆折叠】%d 条对话 → %d 条事实：%s"
          % (len(dialogue), len(history), history))
    print("  （path_history 只有这些短事实，不占 context；NPC 不会因为上下文超长而失忆）")

    # 三轮对话
    state, _ = turn(state, history, "greet", "第1轮 · 玩家归来")
    state, _ = turn(state, history, "ask_forgiveness", "第2轮 · 玩家道歉")
    state, _ = turn(state, history, "accuse", "第3轮 · 玩家辩解")

    print("\n" + "=" * 68)
    print("【最终状态】%s" % json.dumps(state, ensure_ascii=False))
    print("=" * 68)
    print("""
本轮要点：
  1. LLM 三次都先"瞎说"——热情拥抱、原谅、声称从未怀疑（与 path_history 矛盾）。
  2. 中间件三次全部拦截，并给出**具体规则 id + 可执行改写指引**。
  3. LLM 依指引重写后放行；傲娇语气（哼/谁要/滚开）全程保留。
  4. reject 时状态**零改动**——不能让崩坏的回复影响角色数值。
""".strip())

    if a.latency:
        print("\n[延迟基准] 1000 次调用")
        samples = []
        for _ in range(1000):
            t0 = time.perf_counter()
            check_npc_response(state, history, "哼，谁要你道歉。")
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        print("  平均 %.4fms | p50 %.4fms | p99 %.4fms | 最大 %.4fms"
              % (sum(samples) / len(samples), samples[499], samples[989], samples[-1]))
        print("  预算 50ms，余量 %.0fx" % (50 / max(samples[-1], 1e-9)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())