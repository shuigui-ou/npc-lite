"""零依赖独立验证入口：屏蔽全部第三方库后跑通核心链路。

由 tests/test_npc_lite.py 的验收4 以子进程方式调用（单独成文件以避开
shell 字符串转义问题——内联 \n 曾被 bash 吃掉导致假失败）。

输出末行必须是 NODDEP_OK，供父进程断言。
"""
import os
import sys

BLOCKED = ("pandas", "numpy", "akshare", "requests", "pyarrow",
           "tqdm", "yaml", "matplotlib", "urllib3")


class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("BLOCKED(simulated fresh clone): %s" % name)
        return None


sys.meta_path.insert(0, Blocker())
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import npc_guard  # noqa: E402

STATE = {"mood": "angry", "trust": 20, "aggression": 80,
         "role_tags": ["tsundere"], "scene": "court"}

# 1) 应拦
r1 = npc_guard.check_npc_response(STATE, [], "我微笑着热情地抱住你！")
assert r1["status"] == "reject", "应拦截却放行: %s" % r1
assert r1["reason"], "拦截必须带理由"
assert r1["state_delta"] == {}, "拦截时不得改状态"

# 2) 应放
r2 = npc_guard.check_npc_response(STATE, None, "滚开！我早就受够你了。")
assert r2["status"] == "pass", "应放行却拦截: %s" % r2

# 3) 缺字段必须抛
try:
    npc_guard.check_npc_response({"mood": "angry"}, [], "你好")
    raise AssertionError("缺字段却未抛错（静默放行）")
except npc_guard.StateContractError:
    pass

# 4) 记忆折叠可用
facts = npc_guard.fold_memory(["玩家偷了剑", "玩家撒了谎"])
assert len(facts) >= 2, facts

print("[nodep] blocked=%d libs | reject/pass/raise/fold 全部符合预期" % len(BLOCKED))
print("NODDEP_OK")