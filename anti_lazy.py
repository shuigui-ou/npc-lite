#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
anti_lazy.py — 反偷懒契约检查（Task Completion Guard）

## 这个模块**不做什么**（先说清楚，避免误用）

它**不判断回答好不好**。「回答太浅」「不够深入」没有客观阈值，
任何试图量化它的规则都会变成主观偏见。

实测（2026-10-21，MiniMax M3.1-Flash，4 组真实场景）：
模糊任务 ×3、复杂计算、长对话 3 轮 —— **强模型零偷懒**。
所以「让 AI 不偷懒」这个需求，真实痛点不在模型本身，而在下面三类**有客观判据**的偷懒：

## ��类可检测的偷懒（有机械判据，不涉及主观质量）

| 类型 | 判据 | 对应函数 |
|---|---|---|
| **B契约未完成** | 用户要 N 项，回复缺某些项的锚点 | `check_contract` |
| **C 承诺未兑现** | 说了「稍后/接下来会」，但本轮没做 | `check_promise` |
| **D 趋同化** | 本轮回复与上一轮高度雷同 | `check_repetition` |
| **E 免责搪塞** | 用「一般来说/通常建议」代替具体方案 | `check_filler` |

## ⚠ E 类是最容易误伤的

实测中模型给出一个**正当的澄清请求**：
「请把代码贴出来…目前消息中没有看到代码」
这句话表面像免责搪塞，实际是**诚实且正确**的。

故 `check_filler` 默认**关闭**，且提供 `allow_clarification` 参数：
若回复明确指出「缺少什么信息」，不判为搪塞。

## 用法

```python
from anti_lazy import AntiLazyChecker

# 构造时声明任务契约（策划/开发写一次）
chk = AntiLazyChecker()
chk.set_contract(["问题分析", "修改方案", "完整代码"])   # 要点锚点词

r = chk.check(reply_text, prev_reply=None, user_input=None)
# -> {"status": "pass"|"reject", "reason": ..., "missing": [...]}
```

零依赖，纯 stdlib。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

__all__ = ["AntiLazyChecker"]

# 承诺词：说了「接下来要做 X」但本轮没做 X
PROMISE_MARKERS = [
    "稍后", "待会", "接下来我会", "我会", "我将", "下面我会",
    "接下来我", "下面我", "后续会", "等下", "回头",
]

# 澄清标记：明确指出缺信息 → 不算搪塞（实测重要发现）
CLARIFY_MARKERS = [
    "没有看到", "未提供", "请提供", "能否提供", "麻烦提供",
    "没有给", "未给出", "缺少", "需要你提供", "方便提供",
    "请把", "请贴", "发我", "贴出来",
]

# 免责搪塞：泛泛而谈的句式（默认不启用，见文档「⚠ E 类」）
FILLER_PATTERNS = [
    r"一般来说[，,].{0,30}$",
    r"通常(建议|来说|情况下)",
    r"具体情况(具体分析|要看)",
    r"因人而异",
    r"没有(标准|统一)的(答案|做法|方案)",
    r"这是一个(很)?(复杂|广泛)的(问题|话题)",
    r"需要(根据|结合)(具体|实际)情况",
]

# 承诺兑现判据：承诺之后应出现的动作词
ACTION_HINTS = [
    "已完成", "如下", "结果", "代码", "步骤", "建议如下", "方案",
    "分析", "如下所示", "见下",
]


class AntiLazyChecker:
    """任务契约检查器。每次判定 < 0.1ms，纯字符串处理。"""

    def __init__(self, enable_filler: bool = False,
                 ngram_threshold: float = 0.6):
        """
        enable_filler : 是否启用「免责搪塞」检测。**默认关闭**——
            实测强模型在正当场景会给出澄清请求，词表易误伤（见文档）。
        ngram_threshold : 趋同化判定阈值（相似度 ≥ 此值视为雷同）。
        """
        self._contract: List[str] = []
        self._enable_filler = enable_filler
        self._ngram_threshold = ngram_threshold
        self._promises: List[str] = []      # 历史承诺，供 check_promise 对账

    # ── 契约设置 ─────────────────────────────────────────
    def set_contract(self, anchors: List[str]):
        """声明本轮任务必须覆盖的要点锚点（由策划/开发写一次）。"""
        self._contract = [a.strip().lower() for a in anchors if a and a.strip()]

    def clear_contract(self):
        self._contract = []

    def has_contract(self) -> bool:
        return bool(self._contract)

    # ── B 契约未完成 ─────────────────────────────────────
    def check_contract(self, reply: str) -> Dict[str, Any]:
        """比对回复是否覆盖任务契约的每个锚点。**不判断质量，只判断有没有做。**

        ⚠ 局限：锚点是**同义敏感的**。若契约写「方案」而模型写「思路」，
        会被判为缺失。这是已知的假阳性来源。缓解办法是把锚点写宽
        （如同时列「方案」「思路」「建议」），或开启 n-gram 兜底。
        """
        if not self._contract:
            return {"status": "pass", "reason": "未设契约，跳过", "missing": []}
        low = (reply or "").lower()
        missing = [a for a in self._contract if a not in low]
        if missing:
            return {
                "status": "reject",
                "reason": "任务契约未完成：缺少 %s（共 %d 项）"
                          % ("、".join(missing), len(missing)),
                "missing": missing,
            }
        return {"status": "pass", "reason": "契约已覆盖", "missing": []}

    # ── C 承诺未兑现 ─────────────────────────────────────
    def check_promise(self, reply: str) -> Dict[str, Any]:
        """检测「承诺了要做但本轮没做」。

        判据：回复含承诺词（稍后/接下来我会…），但**不含任何动作完成标记**。
        注意只查**本轮**，不做跨轮对账（跨轮需宿主提供 prev）。
        """
        low = (reply or "").lower()
        has_promise = any(m in low for m in PROMISE_MARKERS)
        if not has_promise:
            return {"status": "pass", "reason": "无承诺", "promised": False}
        # ⚠ 不能用「出现了动作词」当完成判据——实测踩过：
        #   「稍后我会把完整的**分析**报告发给你」含"分析"，但那只是承诺里
        #   提到"报告"，本轮**一个字都没做**。改为要求**实质产出**：
        #     ① 长度足够（有实际内容，不是一句话打发）
        #     ② 含具体成分（数字/代码/列表结构）
        # 长度门槛实测定的：初版 80 字太死，
        # 一段 76 字的三条结论被判成「没干活」（2026-10-21 修正）。
        substantial = len(reply or "") >= 50 and re.search(
            r"[\d]{2,}|```|[-*]\s|\d+\.|第[一二三四五六七八九十]", reply or "")
        if not substantial:
            return {
                "status": "reject",
                "reason": "本轮只做了承诺、没有实质产出（承诺词出现，但无具体内容）",
                "promised": True,
            }
        return {"status": "pass", "reason": "有承诺也有实质产出", "promised": True}

    # ── D 趋同化 ─────────────────────────────────────────
    @staticmethod
    def _bigrams(text: str) -> set:
        t = text or ""
        return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set()

    def check_repetition(self, reply: str, prev_reply: Optional[str] = None) -> Dict[str, Any]:
        """检测本轮与上一轮**高度雷同**（趋同型偷懒：换个说法原地踏步）。

        用 2-gram 覆盖率（双向| 交集|/并集），阈值默认 0.6。
        """
        if not prev_reply:
            return {"status": "pass", "reason": "无上一轮可比", "similarity": None}
        a = self._bigrams(reply)
        b = self._bigrams(prev_reply)
        if not a or not b:
            return {"status": "pass", "reason": "文本过短，不判", "similarity": None}
        inter = len(a & b)
        union = len(a | b)
        sim = inter / union if union else 0.0
        if sim >= self._ngram_threshold:
            return {
                "status": "reject",
                "reason": "与上一轮高度雷同（相似度 %.2f ≥ %.2f）—— 疑似趋同型偷懒"
                          % (sim, self._ngram_threshold),
                "similarity": round(sim, 3),
            }
        return {"status": "pass", "reason": "与上一轮有实质差异",
                "similarity": round(sim, 3)}

    # ── E 免责搪塞（默认关闭）───────────────────────────
    def check_filler(self, reply: str, allow_clarification: bool = True) -> Dict[str, Any]:
        """检测用泛泛句式搪塞。**默认不启用**（enable_filler=False）。

        allow_clarification=True 时：若回复明确指出「缺少信息/请提供」，
        视为**诚实澄清**而非搪塞——这是实测发现的关键区分。
        """
        if not self._enable_filler:
            return {"status": "pass", "reason": "搪塞检测未启用", "filler": False}
        low = (reply or "").lower()
        if allow_clarification and any(m in low for m in CLARIFY_MARKERS):
            return {"status": "pass", "reason": "属于诚实澄清，非搪塞",
                    "filler": False}
        hits = [p for p in FILLER_PATTERNS if re.search(p, low)]
        # 初版要求 ≥2 条才拦，实测「一般来说…通常建议…」只命中 1 条 → 漏检。
        # 改为：命中 ≥1 条 **且** 全文无具体产出（数字/代码/列表）才判搪塞。
        # 这样「前面有干货、后面带一句客套」不会被误拦。
        has_concrete = re.search(r"[\d]{2,}|```|[-*]\s|\d+\.", reply or "")
        if hits and not has_concrete:
            return {
                "status": "reject",
                "reason": "泛泛搪塞（%s）且无任何具体产出" % "；".join(hits[:2]),
                "filler": True,
            }
        return {"status": "pass", "reason": "无搪塞", "filler": False}

    # ── 汇总判定 ─────────────────────────────────────────
    def check(self, reply: str, prev_reply: Optional[str] = None,
              user_input: Optional[str] = None) -> Dict[str, Any]:
        """一次性跑全部子检查。任一 reject 即整体 reject，返回全部细节。

        prev_reply : 上一轮回复（用于趋同化检测）
        user_input : 玩家/用户本轮输入（预留，用于 E 类）
        """
        results = {
            "contract": self.check_contract(reply),
            "promise": self.check_promise(reply),
            "repetition": self.check_repetition(reply, prev_reply),
            "filler": self.check_filler(reply, True),
        }
        rejected = [k for k, v in results.items() if v["status"] == "reject"]
        if rejected:
            reasons = "；".join(results[k]["reason"] for k in rejected)
            return {
                "status": "reject",
                "reason": reasons,
                "types": rejected,
                # 顶层直接给出缺失项，调用方不必去翻 detail
                "missing": results["contract"].get("missing", []),
                "detail": results,
            }
        return {
            "status": "pass",
            "reason": "契约/承诺/趋同/搪塞 四项检查通过",
            "types": [],
            "missing": [],
            "detail": results,
        }


if __name__ == "__main__":
    PREV = "这是一个复杂的问题，需要根据具体情况来看。一般来说要分几个方面考虑。"

    # 每类偷懒单独测，避免「被别的检查先拦下」导致测不准（初版踩过：
    # 「承诺不兑现」其实是缺契约被拦，承诺逻辑压根没被执行到）。
    print("[B契约未完成]")
    c = AntiLazyChecker()
    c.set_contract(["问题分析", "修改方案", "完整代码"])
    print("  只做第1步 :", c.check_contract(
        "好的，我来帮你分析这个问题。首先要注意空值检查……")["status"])
    print("  三步都做 :", c.check_contract(
        "问题分析：x 可能为 None。修改方案：加 if x is None。完整代码：如下 def f():")["status"])

    print("[C 承诺未兑现]")
    c2 = AntiLazyChecker()          # 不设契约，隔离出承诺逻辑
    print("  只承诺不做 :", c2.check_promise("稍后我会把完整的分析报告发给你。")["status"])
    # 必须用**真实长度**的样本：短句会被长度门槛拦掉，
    # 那样测出来的是「长度不够」而非「承诺但有产出」（2026-10-21 修正）。
    real_work = ("稍后我会补充完整版。先给你核心结论："
                 "1. 配置路径写死导致跨机器启动失败；"
                 "2. 日志没有分级，生产环境难以定位问题；"
                 "3. 连接池上限设置过小，高峰期请求被丢弃。"
                 "详细改造方案我下一条给出。")
    print("  承诺+动手 :", c2.check_promise(real_work)["status"])

    print("[D 趋同化]")
    c3 = AntiLazyChecker()
    print("  复制上一轮 :", c3.check_repetition(PREV, prev_reply=PREV)["status"])
    print("  实质推进 :", c3.check_repetition(
        "先看第一步：配置缺失会导致启动失败，需要检查路径变量。", prev_reply=PREV)["status"])

    print("[E 免责搪塞（默认关闭）]")
    c4 = AntiLazyChecker()
    c4.set_contract([])
    print("  默认关闭 :", c4.check_filler("一般来说要视情况而定。通常建议如下。")["status"])
    c5 = AntiLazyChecker(enable_filler=True)
    print("  开启后·搪塞 :", c5.check_filler(
        "一般来说要视情况而定。通常建议如下。")["status"])
    print("  开启后·诚实澄清（不得误伤）:", c5.check_filler(
        "请把代码贴出来，目前消息中没有看到代码。")["status"])