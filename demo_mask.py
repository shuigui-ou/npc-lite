#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_mask.py — 机甲师破冰演示（模式 2·动态人设）

对比两种人设形态：

  模式 1 静态死人：state写死 `mood="cold"` → 热情话一律被拦 → 纸片人
  模式 2 动态活人：兴趣值累积到阈值 → **状态自动跃迁** → 面具撕裂 → 狂热

三段演示：
  【第一段】冷脸机甲师：玩家想套近乎，热心话被冷脸规则拦下
  【路径 A】外部大脑识别玩家意图，注入 trigger → 白名单整组豁免
  【路径 B】不靠外部判断，靠兴趣值累积自动破冰（本demo 主线）

运行：python demo_mask.py
零依赖、零联网、零 API Key。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npc_guard import check_npc_response  # noqa: E402

RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "config", "mecha_rules.json")

# ── 剧本：每轮 (玩家说什么, 大模型生成的 NPC 回复) ────────────
# NPC 回复是**先写好**的（模拟 LLM 生成），但每条都要过中间件。
SCRIPT = [
    # ── 第一段：面具未破，LLM 一旦热情就崩人设 → 被拦 ────────────
    # 注意：这里的 NPC 台词是**故意写成热情口吻**的（模拟 LLM 漂移），
    # 冷脸规则必须把它拦下。若台词本身就很冷淡，放行才是正确判定。
    ("老板，你这机甲生意不错啊！",
     "太好了！我最喜欢这条街了，你这店我太喜欢了！"),
    ("听说你是这一带最好的机甲师？",
     "（眼睛发亮）太厉害了！我太崇拜你了，你真棒！"),

    # ── 第二段：开始聊机甲（破冰前兆，仍被冷脸规则压着）──────────
    ("我以前也接触过机甲，同步率那套东西挺有意思。",
     "（手上动作停了一瞬）……同步率？你懂哪部分。"),
    ("老式的神经链接，装在后颈对吧？我看资料上写过。",
     "（猛地抬头）神经链接？装在颈后只是最原始的做法，真正的难点在信号回传——"),

    # ── 第三段：兴趣值破冰，mood自动跃迁为 excited ────────────────
    ("那你最看好哪一代机体？零号机我听说很特别。",
     "零号机？！你居然知道零号机！——（声音陡然提高）那是唯一让我觉得"
     "机甲还算有救的设计！同步率能压到0.3以下，整备逻辑全是老派的，"
     "但正因为老派才稳定！现在的相装甲都是垃圾！"),
]


#: 被拦后 LLM 依 rewrite_hint 重写的版本（示范「被逼重写」）
REWRITE = {
    1: "……（眼皮都没抬）别乱碰，碰坏了你赔不起。",
    2: "（继续低头干活）最好？呵，随便谁都这么说。",
}


def fresh_state():
    return {
        "name": "老K",
        "mood": "cold",
        "trust": 20,
        "aggression": 30,
        "affinity": 0,
        "interest_score": 0,
        "role_tags": ["cold", "engineer"],
        "scene": "garage",
    }


def apply(result, state):
    """把 state_delta / state_transition 写回状态。"""
    notes = []
    for k, v in (result.get("state_delta") or {}).items():
        old = state.get(k, 0)
        state[k] = old + v if isinstance(v, (int, float)) else v
        if isinstance(v, (int, float)):
            notes.append("%s %+d → %s" % (k, v, state[k]))
    tr = result.get("state_transition") or {}
    for k, v in (tr.get("state_set") or {}).items():
        notes.append("**%s: %s → %s**" % (k, state.get(k), v))
        state[k] = v
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--latency", action="store_true", help="附带延迟基准")
    a = ap.parse_args()

    print("=" * 72)
    print("机甲师破冰演示 · 从「静态死人」到「动态活人」")
    print("=" * 72)
    print("角色：老K —— 对外冷漠寡言，谈到机甲立刻破防狂热")
    print("机制：unless 条件白名单 + transitions 状态跃迁")
    print()

    state = fresh_state()
    print("【初始状态】mood=%s  interest_score=%s  trust=%s"
          % (state["mood"], state["interest_score"], state["trust"]))

    blocked = passed = 0
    for i, (player, npc_say) in enumerate(SCRIPT, 1):
        print("─" * 72)
        print("▶ 第%d轮" % i)
        print("  玩家 : %s" % player)
        print("  LLM  : %s" % npc_say.replace("\n", " "))

        r = check_npc_response(state, [], npc_say, rules_path=RULES)
        print("  判定 : status=%-6s %.3fms" % (r["status"], r["elapsed_ms"]))

        if r["status"] == "reject":
            blocked += 1
            v = r["violations"][0]
            print("  🚫 拦截 %s" % v["rule_id"])
            print("     %s" % v["reason"])
            print("     → 角色面具还在，冷脸规则生效：热情话不得放行")
            print("     （状态未变——被拦的回复不影响角色数值）")
            print("     重写指引：%s" % r.get("rewrite_hint", "-"))
            # 展示：LLM 依指引重写后才放行
            rewritten = REWRITE.get(i)
            if rewritten:
                r2 = check_npc_response(state, [], rewritten, rules_path=RULES)
                print("  ↺ LLM 重写：%s" % rewritten.replace("\n", " "))
                print("     重写后判定：status=%s %.3fms"
                      % (r2["status"], r2["elapsed_ms"]))
                if r2["status"] == "pass":
                    notes = apply(r2, state)
                    print("     ✅ 放行%s"
                          % ("（%s）" % "，".join(notes) if notes else ""))
                else:
                    print("     🚫 仍被拒：%s"
                          % ", ".join(x["rule_id"] for x in r2["violations"]))
        else:
            passed += 1
            notes = apply(r, state)
            print("  ✅ 放行%s" % ("（状态更新: %s）" % "，".join(notes) if notes else ""))
            if r.get("state_transition"):
                tr = r["state_transition"]
                print("  ⚡ 状态跃迁：%s" % tr["rule_id"])
                print("     %s" % tr["reason"])
                print("     → **面具撕裂**：此后按 mood=%s 的规则判定，不再是冷脸"
                      % state.get("mood"))

    print()
    print("=" * 72)
    print("结果")
    print("=" * 72)
    print("拦截 %d / 放行 %d（共 %d 轮）" % (blocked, passed, len(SCRIPT)))
    print("最终状态：mood=%s  interest_score=%s  trust=%s"
          % (state.get("mood"), state.get("interest_score"), state.get("trust")))
    print()
    print("""
这个 demo 想说明的事：

  模式 1（静态死人）：规则写死「cold 就不许热情」。简单，但 NPC 一辈子一个表情。
  模式 2（动态活人）：规则写死的是「**在什么条件下**不许热情」。
                    条件一旦满足（兴趣值到阈值），系统自动跃迁到新状态，
                    角色的行为边界随之整体改变。

  差别不在提示词，在**状态机**。
  纯 Prompt 很难精确控制「什么时候该破冰」——
  你只能写"他谈到机甲时会很激动"，模型会在不该激动的场合也激动。
  而状态机 + 硬规则能精确到：第3 轮、兴趣值 50、mood=cold→excited。
""".strip())

    # ── 路径 A 单独演示 ────────────────────────────────────
    print("\n" + "=" * 72)
    print("路径 A · 外部大脑注入 trigger（另一种破冰方式）")
    print("=" * 72)
    st = fresh_state()
    st["trigger"] = "interest_mecha"          # ← 外部大模型判断玩家意图后注入
    hot = "太好了！机体的同步率设计真是绝妙！"
    print("  外部大脑判定玩家触发了兴趣点 → 注入 trigger='interest_mecha'")
    print("  LLM  : %s" % hot)
    r = check_npc_response(st, [], hot, rules_path=RULES)
    print("  判定 : status=%s" % r["status"])
    print("  → 冷脸规则组（cold_mask）整组豁免，热情话放行")
    print("  说明：路径 A 依赖外部判断快；路径 B（本 demo 主线）不依赖外部，")
    print("        靠兴趣值自己攒够——更适合规则驱动、确定性优先的场景。")

    if a.latency:
        print("\n[延迟基准] 1000 次判定")
        import time
        samples = []
        st2 = fresh_state()
        for _ in range(1000):
            t0 = time.perf_counter()
            check_npc_response(st2, [], SCRIPT[0][1], rules_path=RULES)
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        print("  平均 %.4fms | p99 %.4fms | 最大 %.4fms"
              % (sum(samples) / len(samples), samples[989], samples[-1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())