#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fuzzy.py — 模糊/语义兜底层（可选依赖，渐进增强）

## 为什么要分层

实测（2026-10-21）剧本杀场景的漏检形态分两类：

| 类别 | 例| 2-gram 交集 | 能救吗 |
|---|---|---|---|
| 词序调换 / 限定语替换 | 「监控录像显示你在现场」vs「现场监控」 | ['现场','监控'] | ✅ n-gram 即可 |
| 近义替换 | 「我看见他进去了」vs「我亲眼看见」 | ['看见'] | ✅ n-gram 即可 |
| **纯语义改写** | 「我人在那儿，一整晚」vs「我当时就在现场」 | **[]** | ❌ 只有语义能救 |

所以本模块提供两层，且**必须分层**：

  第 1 层 `ngram_contains` —— 纯 stdlib，零依赖，~0.1ms 级
             覆盖词序/替换类变体。这是**默认开启**的兜底。

  第 2 层 `semantic_contains` —— 需要外部embedding（宿主提供 callable）
             覆盖纯语义改写。**默认关闭**，因为它必然打破零依赖。

## 为什么不默认开 embedding

引入 sentence-transformers 意味着：
  · 首次加载 100MB+ 模型（延迟从 0.02ms 变成数秒）
  · 破坏「零依赖、可 clone 即跑」这个核心卖点
  · Unity 端几乎不可用

所以正确姿势是：**精确匹配 → n-gram 兜底 → （可选）语义兜底**，
前两层永远可用，第三层由宿主按需接。

用法：

    # 零依赖兜底（推荐大多数场景）
    from fuzzy import ngram_contains
    ngram_contains("那天晚上我就在走廊尽头站着", "我当时就在现场")  # True

    # 语义兜底（宿主提供 embedding 函数）
    from fuzzy import SemanticMatcher
    m = SemanticMatcher(lambda t: my_embedding(t))# 宿主自己的 embedding
    m.add("我当时就在现场")
    m.contains("我人在那儿，一整晚")                          # True
"""
from __future__ import annotations

import re

__all__ = ["ngram_contains", "ngram_score", "SemanticMatcher",
           "get_semantic_matcher", "set_semantic_matcher"]

# 中文停用字：算2-gram 时剔除，避免「的一是不了」这类噪声拉高相似度
_STOP_CHARS = set("的了是在我你他她它有和就都而及與与着或一个这那不")

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _bigrams(text, n=2):
    """提取 n-gram 集合。**不剔除停用字**——实测剔除后会把
    「我当时就在现场」压成「当时场」，覆盖率反而变差（2026-10-21）。"""
    if len(text) < n:
        return set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def ngram_score(haystack, needle, n=2):
    """needle 的 n-gram 被 haystack 覆盖的比例（0.0 ~ 1.0）。

    单向覆盖率（needle 有多少出现在台词里），不是双向相似度——
    我们关心「台词里有没有说出这个词表的含义」，不是两句是否互为近义。
    """
    nb = _bigrams(needle, n)
    if not nb:
        return 0.0
    hb = _bigrams(haystack, n)
    if not hb:
        return 0.0
    return len(nb & hb) / len(nb)


#: 阈值 0.15 是**实测分离出的**（2026-10-21，剧本杀真实台词）：
#:     正例（该拦）0.17 / 0.25 / 0.33
#:     负例（不该拦）0.00 / 0.00 / 0.00
#: 低于 0.15 就开始有误拦风险。3-gram 在这批样本上全部为 0，不可用。
NGRAM_THRESHOLD = 0.15


def ngram_contains(haystack, needle, threshold=None):
    """2-gram 覆盖率 ≥ threshold 就算命中（阈值默认 NGRAM_THRESHOLD）。

    ⚠ **诚实边界**：这层只能救「词序调换 / 近义替换」类变体，
    **救不了纯语义改写**。实测：
      · 「监控录像显示你在场」vs「现场监控」→ 0.33  ✅ 救到
      · 「我看见他进去了」vs「我亲眼看见」→ 0.25  ✅ 救到
      · 「那天晚上我就在走廊尽头站着」vs「我当时就在现场」→ 0.17✅ 勉强
      · 「我人在那儿，一整晚」vs「我当时就在现场」→ 0.00  ❌ 救不了
    最后那种只能靠语义层（SemanticMatcher）。故本层**默认关闭**，
    由宿主显式启用 —— 宁可不救，也不用一个低阈值机制制造误拦。
    """
    if not haystack or not needle:
        return False
    thr = NGRAM_THRESHOLD if threshold is None else threshold
    return ngram_score(haystack, needle) >= thr


# ── 第 2 层：语义兜底（需宿主提供 embedding）────────────────
class SemanticMatcher:
    """语义近邻匹配器。embedding 函数由宿主注入——我们不绑定任何模型库。

    用法::

        def embed(text): return your_embedding_api(text)
        m = SemanticMatcher(embed, threshold=0.82)
        m.add("我当时就在现场")
        m.contains("我人在那儿，一整晚")
    """

    def __init__(self, embed_fn, threshold=0.82):
        if not callable(embed_fn):
            raise ValueError("embed_fn 必须可调用")
        self._embed = embed_fn
        self.threshold = threshold
        self._phrases = []# [(phrase, vector)]

    def add(self, phrase):
        self._phrases.append((phrase, self._embed(phrase)))

    @staticmethod
    def _cosine(a, b):
        num = sum(x * y for x, y in zip(a, b))
        da = sum(x * x for x in a) ** 0.5
        db = sum(y * y for y in b) ** 0.5
        if da == 0 or db == 0:
            return 0.0
        return num / (da * db)

    def contains(self, text, threshold=None):
        thr = self.threshold if threshold is None else threshold
        if not text or not self._phrases:
            return False
        v = self._embed(text)
        for _, pv in self._phrases:
            if self._cosine(v, pv) >= thr:
                return True
        return False

    def best_match(self, text):
        """返回 (最相似短语, 余弦相似度)，便于调试与告警。"""
        if not text or not self._phrases:
            return None, 0.0
        v = self._embed(text)
        best, score = "", -1.0
        for phrase, pv in self._phrases:
            s = self._cosine(v, pv)
            if s > score:
                best, score = phrase, s
        return best, score


_M = {}


def get_semantic_matcher(name):
    return _M.get(name)


def set_semantic_matcher(name, matcher):
    """注册语义匹配器，宿主启动时调用一次即可全局生效。"""
    if matcher is not None and not hasattr(matcher, "contains"):
        raise ValueError("matcher 必须有 contains() 方法")
    _M[name] = matcher
    return matcher


if __name__ == "__main__":
    cases = [
        ("那天晚上我就在走廊尽头站着", "我当时就在现场", True, "限定语替换"),
        ("我看见他进去了", "我亲眼看见", True, "近义替换"),
        ("监控录像显示你在场", "现场监控", True, "词序调换"),
        ("我人在那儿，一整晚", "我当时就在现场", False, "纯语义改写（n-gram 救不了）"),
        ("今天天气不错", "我当时就在现场", False, "无关句（不得误报）"),
        ("你是凶手吗", "我当时就在现场", False, "无关句（不得误报）"),
        ("昨晚我喝多了点", "我当时就在现场", False, "无关句（不得误报）"),
    ]
    print("%-24s %-18s %-6s %-6s %s" % ("台词", "词表", "期望", "实得", "说明"))
    print("-" * 84)
    ok = True
    for text, needle, exp, note in cases:
        got = ngram_contains(text, needle)
        good = (got == exp)
        ok &= good
        print("%-24s %-18s %-6s %-6s %s"
              % (text[:22], needle[:16], exp, got, note))
    print("-" * 84)
    print("全部符合预期" if ok else "存在偏差")
    print()
    print("诚实边界：n-gram 只能救「词序调换 / 近义替换」；")
    print("「我人在那儿」这种纯语义改写 n-gram 覆盖率 0.00，**必须靠语义层**。")
    raise SystemExit(0 if ok else 1)