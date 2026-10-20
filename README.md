# npc-lite: 毫秒级的大模型 NPC 人设拦截器

> **纯规则计算，拒绝延迟；状态机分离，拒绝失忆。**

零依赖、零联网、零API Key。单次判定 **平均 0.024ms**（预算 50ms，余量 426×）。

```bash
python demo.py --latency       # 完整闭环 + 延迟基准，无需任何 API
python tests/test_npc_lite.py  # 39 项验收，机器判定
```

---

## 为什么需要它

大模型扮演 NPC 时，人设崩塌的典型方式不是「说得不好」，而是**说得太顺**：
角色正在愤怒，模型却回一句「我微笑着热情地抱住你，好开心呀」——
语气通顺、语法正确、**与角色状态直接矛盾**。传统做法是在 prompt 里反复叮嘱，
但模型在长对话里必然漂移。

本中间件的定位：**不替模型写台词，只在台词出口前做一次判定**。

```
候选回复 ──▶ 规则判定 ──▶ pass   → 放行 + 状态增量
                    │
                    └──────▶ reject → 带规则 id 与改写指引，要求重写
```

与宏观域 `hard_prune` 的分工差异：

| | 宏观 hard_prune | npc-lite |
|---|---|---|
| 判据 | 指标 target + 方向 sign | 角色状态字段 + 文本特征 |
| 缺数据时 | 保守不剪（宁可漏剪） | **抛错**（状态不明时绝不放行）|
| 用途 | 推演分支过滤 | 实时对话拦截 |

**为什么缺数据时反转保守语义**：宏观推演里漏剪只是少一条分支；
对话里放行等于让人设崩坏的台词流到用户眼前，**不可逆**。宁报错。

---

## 二、主 API

```python
from npc_guard import check_npc_response

result = check_npc_response(
    current_state = {"mood": "angry", "trust": 25, "aggression": 80,
                     "role_tags": ["tsundere"], "scene": "court"},
    path_history  = ["玩家偷了东西", "玩家撒了谎"],
    llm_response  = "呀！你终于回来啦！我微笑着热情地抱住你，好开心呀！",
)
```

返回：

```json
{
  "status": "reject",
  "reason": "NPC-MOOD-001(hard): 角色处于愤怒状态，却输出了热情/亲昵语气，人设崩塌",
  "state_delta": {},
  "violations": [{"rule_id": "NPC-MOOD-001", "reason": "...", "severity": "hard"}],
  "rewrite_hint": "角色正在愤怒——请改写为敌意/冷淡/威胁的语气，不要出现亲昵表情词。",
  "elapsed_ms": 0.470
}
```

### 三个必须知道的语义

1. **`status` 只有 pass / reject 两种**，没有「不确定」。
   规则没命中就是 pass——那本来就是放行，不需要第三种状态。
2. **`reject` 时 `state_delta` 恒为空**。不能让崩坏的回复改变角色数值。
3. **契约违例抛异常，不返回 reject**。缺字段是程序错误（`StateContractError`），
   与「判定不通过」是两件不同的事，混在一起会让调用方无法区分该修代码还是该重写台词。

---

## 三、状态契约（不满足即抛错）

```json
{
  "mood":       "angry",        // 必填：情绪状态字符串
  "trust":      25,             // 必填：玩家对角色的信任 0-100
  "aggression": 80,             // 必填：攻击性 0-100
  "affinity":   -3,             // 选填：好感度 -100..100
  "fear":       0,              // 选填
  "role_tags":  ["tsundere"],   // 选填：角色标签
  "scene":      "court",        // 选填：场景标签
  "name":       "艾莉丝"         // 选填
}
```

`path_history` 缺省（`None`）是**合法**的，等价于空列表——不要跟「字段缺失」混为一谈。

---

## 四、状态演化（轻量加减法）

判定通过时，中间件返回该应用的状态增量，**已按边界裁剪**：

```python
state["trust"] = 25
check_npc_response(state, [], "说实话，我不会骗你。")
# → state_delta = {"trust": 5}     # 25 + 5 = 30

state["trust"] = 98
check_npc_response(state, [], "说实话，我不会骗你。")
# → state_delta = {"trust": 2}     # 裁剪到 100，故实际 +2 而非 +5
```

### `actor` 维度：一个真实踩过的坑

规则增量必须标明作用方向，否则会出现**语义反转**：

```python
state = {"mood": "angry", "trust": 50, "aggression": 40, "affinity": 0}
check_npc_response(state, [], "滚开！你这个笨蛋！")
# → {"aggression": 5, "affinity": -3}
```

NPC 在骂玩家，是**角色自身攻击性升高**，不是「玩家不再信任 NPC」。
早期版本把辱骂词挂在 `trust -8` 上，等于角色每骂一次玩家的信任就掉8 点——
方向完全反了。修正见 `config/npc_rules.json` 的 `actor` 字段
（`npc` = 角色自身数值 / `player_rel` = 玩家与角色的关系数值）。

---

## 五、长期记忆折叠（Memory Fold）

```bash
python fold_memory.py dialog.txt              # 长对话 → 事实列表
python fold_memory.py --json '["玩家偷了剑","玩家撒了谎"]'
python fold_memory.py --stdin < dialog.txt
```

```python
from npc_guard import fold_memory
facts = fold_memory(dialogue, max_facts=12)
# 40 条对话 → ['玩家偷了东西', '玩家撒了谎', '玩家道歉了', '玩家威胁了NPC']
```

折叠出的事实**直接参与拦截**，不是摆设：

```python
check_npc_response(state, facts, "我一直都相信你，你从没骗过我。")
# → reject: path_history 显示玩家曾撒谎，角色不应声称从未怀疑
```

### 为什么做成「高��度、低召回」

折叠的失败模式**不对称**：

- **漏掉关键事实**（玩家偷过东西）→ NPC 失忆，说出违背前史的话 → 人设崩塌
- **多带一条废话** → 仅浪费几个 token，无正确性影响

所以宁可少抽，不可抽错。抽不出事实时返回空 list（合法，不报错）——
「没抽到」不等于「输入非法」。

---

## 六、接入

### Python 后端

```python
import sys; sys.path.insert(0, "./npc-lite")
from npc_guard import check_npc_response

def npc_turn(state, history, player_input):
    for attempt in range(3):                      # 最多重试 3 次
        reply = your_llm_call(player_input,
                              hint=prev_hint)      # 把拒绝理由喂回模型
        r = check_npc_response(state, history, reply)
        if r["status"] == "pass":
            for k, v in r["state_delta"].items():
                state[k] = state.get(k, 0) + v
            return reply, r
        prev_hint = "%s %s" % (r["reason"], r["rewrite_hint"])
    return None, last_result                        # fail-closed：不放行
```

### Unity（C#）

模块是纯计算、无 IO 状态，最可靠的接法是**子进程桥接**（避免绑定 CPython）：

```csharp
var psi = new ProcessStartInfo("python", "npc_bridge.py");
psi.ArgumentList.Add(stateJson);          // argv 传 JSON
psi.RedirectStandardOutput = true;
var proc = Process.Start(psi);
string result = proc.StandardOutput.ReadToEnd();
```

`npc_bridge.py`（十几行，stdin/stdout 传 JSON）：

```python
import sys, json
from npc_guard import check_npc_response
req = json.loads(sys.stdin.read())
print(json.dumps(check_npc_response(
    req["state"], req.get("history"), req["reply"]), ensure_ascii=False))
```

单次 0.024ms 的纯计算，**即使走子进程也在 5ms 量级**，远低于一帧 16ms。

---

## 七、自定义规则

编辑 `config/npc_rules.json`，无需改代码：

```json
{
  "id": "NPC-CUSTOM-001",
  "when": {
    "all_of": [
      {"metric": "mood", "op": "in", "value": ["cold", "aloof"]},
      {"metric": "reply_text", "op": "contains_any", "value": ["求你了", "拜托你"]}
    ]
  },
  "reason": "冷淡角色不应哀求",
  "severity": "hard"
}
```

- `metric`：`mood`/`trust` 等状态字段、`reply_text`（候选回复）、`history_text`（path_history）
- 文本算子：`contains_any` / `contains_all` / `contains_none`
- 标量算子：`==` `!=` `<` `<=` `>` `>=` `in` `not_in` `exists` `unknown`

**新增规则后请在 `npc_guard.py` 的 `_HINTS` 登记改写指引**——
`tests/test_npc_lite.py::test_all_rules_have_hint` 会拦住遗漏（这坑真实踩过）。

`severity`：`hard` 命中即 reject；`soft` 目前同样计入 reject，
但排在 hard 之后、优先展示 soft 之外的规则理由。留作扩展点。

---

## 八、验收结果（实测，非声称）

```
通过 39 项，失败 0 项
```

| 验收项 | 标准 | 实测 |
|---|---|---|
| 延迟 | < 50ms | 平均 **0.024ms** / p99 **0.050ms** / 最大 **0.117ms**（1000 次）|
| 抗崩人设 | 100% 拦截 | 愤怒傲娇 NPC × 8 条热情讨好回复 = **8/8 全拦** |
| 不静默失败 | 缺字段报错 | 10 类契约违例**全部抛错**，无静默放行 |
| 零依赖 | fresh clone 可跑 | 屏蔽 9 个第三方库后全链路通过 |

额外钉死的不变量（都是真实踩出来的）：

- **敌意回复不被误拦**（反向对照，否则等于不可用）
- 行为型 OOC（欢迎/请你喝酒）不漏检 —— 词表回退会被测试拦住
- `NPC 辱骂 → trust 不变`（actor 方向不可反）
- 愤怒角色说原谅 → 拦截
- 边界裁剪：`trust 98 +5 → 实际 +2`（不越界）
- 每条规则都有专属 `rewrite_hint`，无孤儿 hint

---

## 九、已知边界（诚实披露）

1. **文本判定是关键词匹配，不是语义理解。**「热情」「关心」这类词的变体
   （热情洋溢 / 温情脉脉）可能漏检。当前是**高召回优先**的取舍：
   宁可漏剪，也**不误拦正常对话**——误拦会让 NPC 变得结巴，同样是人设崩塌。
2. **规则质量直接决定效果。**规则不全时系统不会乱拦，但也不会拦下所有错误。
   这是刻意取舍：规则是显式的、可审计的、可由你掌控的。
3. **状态演化是标量加减法**，没有跨轮因果链。复杂的心理变化请用多个字段组合表达。
4. **中文分词依赖关键词表。**接入其他语言需替换 `_FACT_PATTERNS` 与规则库词表。
5. **`fold_memory` 是保守抽取**（10 类高确定性事实）。超出这 10 类的关键事实抽不到，
   需要扩展 `npc_guard.py` 里的 `_FACT_PATTERNS`。

---

## License

MIT