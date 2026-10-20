#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fold_memory.py — 长期记忆折叠 CLI

把长对话历史压缩成极简 path_history 事实列表，防止 context 超长导致大模型失忆。

用法：
    python fold_memory.py dialog.txt                    # 从文件读，一行一条
    python fold_memory.py --json '["玩家偷了剑","…"]'   # 从 JSON 数组读
    python fold_memory.py --stdin                       # 从标准输入读
    python fold_memory.py dialog.txt --out facts.json   # 落盘，供对话循环直接 load

设计取舍（重要）：
  折叠的失败模式**不对称**——
    · 漏掉关键事实（玩家偷过东西）→ NPC 失忆，说出违背前史的话 → 人设崩塌
    · 多带一条废话             → 仅浪费几个 token，无正确性影响
  故刻意做成**高��度、低召回**：只认高确定性事实，宁可少抽，不可抽错。
  抽不出事实时返回空 list（合法，不报错）——「没抽到」≠「输入非法」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npc_guard import fold_memory  # noqa: E402


def read_dialogue(args):
    if args.json:
        return json.loads(args.json)
    if args.stdin:
        return [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if args.path:
        if not os.path.exists(args.path):
            raise SystemExit("文件不存在: %s" % args.path)
        with open(args.path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    raise SystemExit("请提供 --path / --json / --stdin 之一")


def main():
    ap = argparse.ArgumentParser(description="长期记忆折叠：长对话 → 极简事实列表")
    ap.add_argument("path", nargs="?", help="对话文件（一行一条）")
    ap.add_argument("--json", help="直接传 JSON 数组字符串")
    ap.add_argument("--stdin", action="store_true", help="从标准输入读")
    ap.add_argument("--max-facts", type=int, default=12, help="事实条数上限（默认 12）")
    ap.add_argument("--out", help="输出 JSON 文件路径")
    a = ap.parse_args()

    dialogue = read_dialogue(a)
    facts = fold_memory(dialogue, max_facts=a.max_facts)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(facts, f, ensure_ascii=False, indent=2)
        print("[fold] %d 条对话 → %d 条事实 → %s"
              % (len(dialogue), len(facts), a.out))
    else:
        print(json.dumps(facts, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())