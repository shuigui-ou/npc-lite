#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
npc_bridge.py — 子进程桥接（Unity / C# / 任意语言调用）

用法：
    echo '{"state":{...},"history":[...],"reply":"..."}' | python npc_bridge.py

输入（stdin，JSON）:
    {"state": {"mood":"angry","trust":25,"aggression":80},
     "history": ["玩家偷了东西"],
     "reply": "我微笑着热情地抱住你！"}

也支持 argv 直接传（适合 Unity 的 ProcessStartInfo.ArgumentList）:
    python npc_bridge.py '{"state":{...},"reply":"..."}'

输出（stdout，JSON）:
    {"status":"reject","reason":"...","state_delta":{},"violations":[...],
     "rewrite_hint":"...","elapsed_ms":0.024}

**退出码约定（给宿主判断用）**：
    0 = 判定完成（pass 或 reject，看 status 字段）
    2 = 契约违例（缺字段 / 类型错）—— 宿主应视为**程序错误**，不是"台词被拒"
    1 = 其他异常

这条区分很重要：契约错误要修代码，reject 只要重写台词。
混成同一个退出码，宿主就只能把所有失败都当重试，掩盖真正的 bug。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from npc_guard import StateContractError, TextContractError, check_npc_response  # noqa: E402


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    raw = raw.strip()
    if not raw:
        print(json.dumps({"error": "empty_input",
                          "message": "stdin 与 argv 均为空"},
                         ensure_ascii=False))
        return 2

    try:
        req = json.loads(raw)
    except Exception as e:
        print(json.dumps({"error": "bad_json",
                          "message": "%s: %s" % (type(e).__name__, e)},
                         ensure_ascii=False))
        return 2

    try:
        result = check_npc_response(
            current_state=req.get("state"),
            path_history=req.get("history"),
            llm_response=req.get("reply", ""),
        )
    except (StateContractError, TextContractError) as e:
        print(json.dumps({"error": "contract_violation",
                          "type": type(e).__name__,
                          "message": str(e)}, ensure_ascii=False))
        return 2
    except Exception as e:
        print(json.dumps({"error": "internal",
                          "type": type(e).__name__,
                          "message": str(e)}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())