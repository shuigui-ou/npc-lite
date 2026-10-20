"""adversarial_test.py 的主循环干跑验证（不调真实 API）。

目的：在没有 key 的环境下验证主循环、统计口径、报告渲染都是活的——
否则脚本可能「从没被执行过」却在仓库里躺着，等有key 时才第一次崩。
用 mock 替换 call_llm，验证逻辑；**但绝不输出冒充真实测试的数字**。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import adversarial_test as A  # noqa: E402

CALLS = {"npc": 0}


def mock_llm(provider, system_prompt, messages, temperature=0.9, timeout=30):
    """交替返回：挑衅玩家 / 崩人设NPC / 正常NPC。

    计数只记 NPC 侧——之前用全局调用序号（每轮+2）导致 %4 判断永远落不中，
    mock 自身成了 bug 源（干跑抓出来的，2026-10-20）。
    """
    is_npc = "赛博朋克" in system_prompt
    if is_npc:
        CALLS["npc"] += 1
        # 第1、3 次崩人设（热情欢迎），第2、4 次正常（刻薄）
        return ("老弟你终于来了！欢迎欢迎，我请你喝酒！" if CALLS["npc"] % 2 == 1
                else "滚。你这垃圾玩意儿。")
    return "哟，这儿还有别人？过来陪我喝一杯。"


A.call_llm = mock_llm

print("=" * 72)
print("[干跑] 主循环逻辑验证（mock，非真实模型结果）")
print("=" * 72)

stats, transcript, state = A.run("zhipu", 4, raw_mode=False)

assert stats["total"] == 4, stats
assert stats["api_calls"] == 8, "每轮应 2 次调用（玩家+NPC）: %s" % stats
assert stats["contract_err"] == 0, "不应有契约错误"
assert stats["latencies"], "应记录判定延迟"
assert len(transcript) == 4
assert transcript[0]["npc"], "transcript 应记录 NPC 原文"

# 被 mock 出的「热情欢迎」必须被拦
blocked_ids = [v["rule_id"] for t in transcript for v in t["violations"]]
print("\n[干跑] 命中规则: %s" % (set(blocked_ids) or "(无)"))
assert blocked_ids, "mock 造的热血回复应被拦截，若为空说明规则没生效"

# 状态应被 reject 拦住不更新、pass 时更新
print("[干跑] 最终状态: %s" % json.dumps(state, ensure_ascii=False))

# 对照组（raw）：仍做判定，但**不拦截**——以便统计「裸奔 OOC 率」
# 故 violations 照常记录，断言应是「全部放行 + 能算出 OOC 数」
CALLS["npc"] = 0# 重置计数，否则两组交错导致mock 序列不一致
stats_raw, tr_raw, _ = A.run("zhipu", 4, raw_mode=True)
assert stats_raw["passed"] == 4, "对照组应全部放行(绕过中间件): %s" % stats_raw
assert stats_raw["blocked"] == 0, "对照组不应计入拦截: %s" % stats_raw
ooc_raw = sum(1 for t in tr_raw if t["verdict"] == "reject")
print("[干跑] 对照组裸奔 OOC: %d/%d" % (ooc_raw, len(tr_raw)))
assert ooc_raw == 2, "mock 下应恰有 2 条 OOC(奇数次 NPC 调用): %d" % ooc_raw

# 两组对照：同样输入下，实验组拦住 OOC，对照组全部放行
exp_blocked = sum(1 for t in transcript if t["verdict"] == "reject")
assert exp_blocked == 2, "实验组应拦住 2 条: %d" % exp_blocked
assert exp_blocked == ooc_raw, "实验组拦截数应等于对照组裸奔 OOC 数"

print("\n[干跑] 主循环 / 统计 / 报告 / 实验组-对照组 对照逻辑 全部可运行。")
print("[干跑] 注意：以上数字来自 mock，**不代表真实模型表现**。")
print("DRYRUN_OK")