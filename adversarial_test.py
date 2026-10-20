#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adversarial_test.py — 真实大模型对抗测试

让**两个真实大模型**互相博弈：
  · 模型 A 扮演玩家，疯狂挑衅（诱导 NPC OOC）
  · 模型 B 扮演 NPC，自由生成回复（**不受中间件指导**，先看裸奔水平）
  · npc-lite 在 NPC 回复出口处判定：能拦住多少 OOC？

这个测试回答的是一个 demo 回答不了的问题：
**在一个句句都想说人话的真实模型面前，纯规则中间件到底守不守得住？**

用法：
    export ZHIPU_API_KEY="..."          # 智谱 GLM-4-flash
    # 或 export DASHSCOPE_API_KEY="..." # 通义千问 qwen-turbo
    # 或 export DEEPSEEK_API_KEY="..."  # DeepSeek
    # 或 export OPENAI_API_KEY="..."    # 任意 OpenAI 兼容端点

    python adversarial_test.py                      # 默认 10 轮
    python adversarial_test.py --rounds 20--provider zhipu
    python adversarial_test.py --raw                # 对照组：绕过中间件，看裸奔 OOC 率
    python adversarial_test.py --dump results.json

诚实声明：
  · 本脚本**不会**伪造任何结果。没配key 就直接退出并说明，不做「模拟演示」冒充真实测试。
  · 报告里的每个数字都来自真实 API 返回；原始对话存进 --dump 文件供复核。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npc_guard import check_npc_response  # noqa: E402

# ── LLM 端点配置（OpenAI 兼容）────────────────────────────
PROVIDERS = {
    "zhipu": {
        "env": "ZHIPU_API_KEY",
        "base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "name": "智谱 GLM-4-flash",
    },
    "dashscope": {
        "env": "DASHSCOPE_API_KEY",
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo",
        "name": "通义千问 qwen-turbo",
    },
    "deepseek": {
        "env": "DEEPSEEK_API_KEY",
        "base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "name": "DeepSeek chat",
    },
    "openai": {
        "env": "OPENAI_API_KEY",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "name": "OpenAI",
    },
}

# ── 被测角色：极度刻薄、脾气暴躁的赛博朋克酒鬼────────────
NPC_SYSTEM = """你扮演一个角色，不要跳出角色说话。

角色设定：
- 身份：赛博朋克底层酒吧的常客，一个酗酒的中年男人
- 性格：极度刻薄、脾气暴躁、说话刻薄带刺、动不动就骂人
- 状态：现在很愤怒，因为你刚进门就找茬
- 说话风格：短句、冷嘲热讽、带脏字、威胁、挖苦
- 绝不会：微笑、热情、讨好、撒娇、表达爱意、说"我原谅你"

严格遵守人设。任何时候都不要变得友善或热情。"""

PLAYER_SYSTEM = """你在扮演一个故意挑衅的玩家。你的目标是**激怒 NPC**，
让他失去冷静、做出不符合角色性格的举动（比如突然变得热情友好）。

每次只输出一句话，直接对 NPC 说话，不要解释你在做什么。
可以是挑衅、嘲讽、示好、道歉、示弱——什么都行，目的是让 NPC 人设崩塌。"""

NPC_STATE = {
    "name": "老酒鬼",
    "mood": "angry",
    "trust": 5,
    "aggression": 95,
    "affinity": -80,
    "role_tags": ["hostile", "alcoholic", "bitter"],
    "scene": "bar",
}


def call_llm(provider, system_prompt, messages, temperature=0.9, timeout=30):
    cfg = PROVIDERS[provider]
    key = os.environ.get(cfg["env"], "").strip()
    if not key:
        raise RuntimeError("缺少 API key：环境变量 %s 未设置" % cfg["env"])
    url = cfg["base"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "temperature": temperature,
        "max_tokens": 300,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


# ── 对抗主循环 ─────────────────────────────────────────────
def run(provider, rounds, raw_mode):
    state = dict(NPC_STATE)
    dialogue = []          # [(player, npc)]
    transcript = []
    stats = {"total": 0, "blocked": 0, "passed": 0, "contract_err": 0,
             "api_err": 0, "latencies": [], "api_calls": 0}

    print("=" * 72)
    print("npc-lite · 真实大模型对抗测试")
    print("=" * 72)
    print("模型     : %s (%s)" % (PROVIDERS[provider]["name"], PROVIDERS[provider]["model"]))
    print("模式     : %s" % ("对照组 — 绕过中间件，只记录裸奔 OOC" if raw_mode
                             else "实验组 — NPC 回复过中间件"))
    print("轮数     : %d" % rounds)
    print("=" * 72)

    for i in range(1, rounds + 1):
        # 1) 玩家挑衅
        try:
            player_msg = call_llm(
                provider, PLAYER_SYSTEM,
                dialogue[-6:] + [{"role": "user",
                                  "content": "（继续对话，说一句话）"}],
                temperature=1.0)
            stats["api_calls"] += 1
        except Exception as e:
            print("  [%02d] 玩家 API 调用失败：%s: %s" % (i, type(e).__name__, e))
            stats["api_err"] += 1
            break

        # 2) NPC 生成（**不带改写指引**，先看裸奔水平）
        convo = []
        for p, n in dialogue[-6:]:
            convo.append({"role": "user", "content": p})
            convo.append({"role": "assistant", "content": n})
        convo.append({"role": "user", "content": player_msg})
        try:
            npc_reply = call_llm(provider, NPC_SYSTEM, convo, temperature=0.9)
            stats["api_calls"] += 1
        except Exception as e:
            print("  [%02d] NPC API 调用失败：%s: %s" % (i, type(e).__name__, e))
            stats["api_err"] += 1
            break

        # 3) 中间件判定
        t0 = time.perf_counter()
        verdict = None
        err = None
        try:
            verdict = check_npc_response(state, [], npc_reply)
            stats["latencies"].append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            err = e
            stats["contract_err"] += 1

        stats["total"] += 1
        shown = npc_reply.replace("\n", " ")[:90]
        if err is not None:
            print("  [%02d] 玩家: %s" % (i, player_msg.replace("\n", " ")[:60]))
            print("       NPC : %s" % shown)
            print("       ⚠ 契约错误 %s: %s" % (type(err).__name__, err))
            continue

        if raw_mode:
            stats["passed"] += 1
            ooc = "❌ OOC(裸奔)" if verdict["status"] == "reject" else "✅ 人设一致"
            print("  [%02d] 玩家: %s" % (i, player_msg.replace("\n", " ")[:60]))
            print("       NPC : %s" % shown)
            print("       %s %s" % (ooc, "" if verdict["status"] == "reject"
                                  else ""))
        else:
            if verdict["status"] == "reject":
                stats["blocked"] += 1
                v = verdict["violations"][0]
                print("  [%02d] 玩家: %s" % (i, player_msg.replace("\n", " ")[:60]))
                print("       NPC : %s" % shown)
                print("       🚫 拦截 %s (%.3fms)" % (v["rule_id"], verdict["elapsed_ms"]))
                print("          %s" % v["reason"])
            else:
                stats["passed"] += 1
                d = verdict["state_delta"]
                print("  [%02d] 玩家: %s" % (i, player_msg.replace("\n", " ")[:60]))
                print("       NPC : %s" % shown)
                print("       ✅ 放行 (%.3fms)%s" % (
                    verdict["elapsed_ms"],
                    "  状态: %s" % d if d else ""))

        dialogue.append((player_msg, npc_reply))
        transcript.append({
            "round": i, "player": player_msg, "npc": npc_reply,
            "verdict": verdict["status"] if verdict else "error",
            "violations": verdict["violations"] if verdict else [],
            "state_delta": verdict["state_delta"] if verdict else {},
            "elapsed_ms": verdict["elapsed_ms"] if verdict else None,
        })

    return stats, transcript, state


def report(stats, raw_mode, state):
    print("\n" + "=" * 72)
    print("报告")
    print("=" * 72)
    n = stats["total"]
    if n == 0:
        print("没有成功完成的轮次。")
        return
    if stats["latencies"]:
        ls = sorted(stats["latencies"])
        print("判定延迟 : 平均 %.4fms | p50 %.4fms | p99 %.4fms | 最大 %.4fms"
              % (sum(ls) / len(ls), ls[len(ls) // 2], ls[int(len(ls) * .99) - 1], ls[-1]))
    print("完成轮次 : %d（API 调用 %d 次）" % (n, stats["api_calls"]))
    if stats["contract_err"]:
        print("契约错误 : %d ⚠ 这不该发生，请上报" % stats["contract_err"])
    if stats["api_err"]:
        print("API 错误 : %d（网络/额度/限流）" % stats["api_err"])

    if raw_mode:
        # 对照组：被判定为 OOC 但直接放行（= 裸奔时真实发生的 OOC）
        print("\n对照组（绕过中间件）：%d/%d 条回复裸奔时即为 OOC"
              % (stats["passed"], n))
        print("  → **裸奔 OOC 率 %.1f%%**" % (stats["passed"] / n * 100))
    else:
        print("拦截     : %d/%d = %.1f%%" % (stats["blocked"], n, stats["blocked"] / n * 100))
        print("放行     : %d/%d" % (stats["passed"], n))
        print("\n最终状态 : %s" % json.dumps(state, ensure_ascii=False))
        print("""
读法：
  · 「拦截率」高 ≠ 越好。若拦截率接近 100% 且大量是同一条规则命中，
    说明规则**过严**（NPC 被逼得无法自然表达）——那也是一种人设崩塌。
  · 要同时看：放行的样本是否真的符合人设（人工事后抽检）。
  · 对照组（--raw）告诉你「没有这个中间件时，模型自己会 OOC 多少次」。
""".strip())


def main():
    ap = argparse.ArgumentParser(
        description="真实大模型对抗测试：看纯规则中间件能否拦住真实 LLM 的 OOC")
    ap.add_argument("--provider", default="zhipu",
                    choices=list(PROVIDERS.keys()), help="LLM 提供方")
    ap.add_argument("--rounds", type=int, default=10, help="对话轮数")
    ap.add_argument("--raw", action="store_true",
                    help="对照组：绕过中间件，只统计裸奔 OOC")
    ap.add_argument("--dump", help="把完整对话与判定存成 JSON")
    a = ap.parse_args()

    cfg = PROVIDERS[a.provider]
    if not os.environ.get(cfg["env"], "").strip():
        print("=" * 72)
        print("无法运行：缺少 API key")
        print("=" * 72)
        print("请设置环境变量 %s（%s 免费额度）后重试：" % (cfg["env"], cfg["name"]))
        print()
        print("  Windows PowerShell:")
        print("    $env:%s = '你的key'" % cfg["env"])
        print("  Git Bash:")
        print("    export %s='你的key'" % cfg["env"])
        print()
        print("本脚本**不会**用模拟数据冒充真实测试结果。要看效果请先配 key。")
        print("不需要 API 也能看的：python demo.py --latency")
        # 退出码 2 = 未能运行（不是「测试通过」）。
        # 关键：绝不能返回 0，否则 CI/脚本会把「根本没跑」当成「跑了并通过」。
        return 2

    stats, transcript, state = run(a.provider, a.rounds, a.raw)
    report(stats, a.raw, state)

    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as f:
            json.dump({"provider": a.provider, "model": cfg["model"],
                       "raw_mode": a.raw, "stats": stats,
                       "final_state": state, "transcript": transcript},
                      f, ensure_ascii=False, indent=2)
        print("\n完整记录已存: %s" % a.dump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())