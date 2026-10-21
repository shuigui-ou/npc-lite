# npc-lite: 毫秒级的大模型 NPC 人设拦截器

> **纯规则计算，拒绝延迟；状态机分离，拒绝失忆。**

零依赖、零联网、零API Key。单次判定 **平均 0.024ms**（预算 50ms，余量 426×）。

```bash
python demo.py --latency       # 完整闭环 + 延迟基准，无需任何 API
python tests/test_npc_lite.py  # 71 项验收，机器判定
```

---

## ⚡ 30 秒快速上手（写给游戏策划）

**你不需要会写代码。** 打开一个记事本，改一个文件，跑一条命令，你的 NPC 就活了。

### 三步

**第1步· 跑一个现成的看看（10 秒）**

```bash
python inject_role.py --preset tsundere --play
```

你会看到一个傲娇公主：冷着脸 →玩家真诚对待 → 好感涨满 → **自动破冰变害羞**。
全程判定耗时 **0.06 毫秒**。

**第 2 步 · 改成你自己的角色（3 分钟）**

打开 `config/role_recipe.ini`，**只改这几行**：

```ini
[角色]
名字 = 暴躁老哥# ← 你的角色名
初始情绪 = angry                        # 初始情绪名

[情绪]
angry = 你好, 谢谢, 对不起, 大神, 好厉害   # ← 愤怒时绝对不能说的话

[破冰]
字段 = trust                # ← 靠什么涨
阈值 = 80                             # ← 涨到多少破冰
破冰后情绪 = respectful                 # ← 破冰后变成什么样
```

**第 3 步 · 看效果（10 秒）**

```bash
python inject_role.py --recipe config/role_recipe.ini --play
```

### 你在改的到底是什么

不是提示词，是**角色行为的状态机**：

```
        初始情绪                数值跨过阈值              自动切换
    ┌────────────┐          ┌────────────┐         ┌────────────┐
    │  冷漠 cold  │ ───────▶ │ 数值 >= 80 │ ───────▶ │ 害羞 shy   │
    │ 不许热情   │  好感累积 │            │  状态跃迁 │ 可以心动   │
    └────────────┘          └────────────┘         └────────────┘
         ↑ 规则写在 [情绪]        ↑ 规则写在 [破冰]      ↑ 规则写在 [情绪]
```

**关键区别**：纯提示词写「他谈到机甲时会很激动」，
模型会在**不该激动**的场合也激动。状态机能精确到
「第 11 轮、信任 80、mood 从 cold 变 shy」。

### 已经内置两个可对比的例子

| 角色 | 破冰靠什么 | 结果 |
|---|---|---|
| 傲娇公主 `role_recipe.ini` | 聊共同话题（琴棋书画）涨信任 | 冷漠 → 害羞 |
| 暴躁老哥 `preset_grumpy.ini` | 露出真本事（引擎/改装）涨尊重 | 愤怒 → 敬服 |

**机制完全一样，只是字段名和词表全换了。** 这就是这个引擎的价值：
你换的是设定，不换的是引擎。

### 常见问题

**Q：改了没反应？**
A：跑一下 `--play`，如果出现「⚠ 配方提醒」就按提示改。
最常见的是「破冰字段没有规则会增加它」——就像游戏里加了经验值但没给经验怪。

**Q：能接进我们的服务器吗？**
A：能。

```bash
python inject_role.py --recipe 你的.ini --out config/my_npc.json
```

```python
from npc_guard import check_npc_response
result = check_npc_response(state, history, llm_reply, rules_path="config/my_npc.json")
# → {"status": "reject", "reason": "为什么不行", "rewrite_hint": "怎么改"}
```

Unity / C# 用子进程桥接：

```bash
echo '{"state":{...},"reply":"..."}' | python npc_bridge.py
```

**Q：能不能不用配置文件，直接命令行建角色？**
A：可以，但推荐用 INI——它能写注释，改的时候不会出错。

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

## 二、定位：这一层到底管什么

> Inworld给的是云端大脑，Convai 给的是长期记忆，
> 各类「状态权威层」引擎管的是**状态怎么变**。
> **npc-lite 管的是台词能不能出口**——不替模型说话，
> 只在话说出口前的那一瞬间，判定它配不配。
> 并且角色聊到临界点时，它会自动撕下面具。

### 分层图：一句话对每个角色

```
   ┌─────────────────────────────────────────────┐
   │  生成层：谁来说这句话                        │
   │  云端NPC平台 / 游戏内LLM / 任何能出字的地方  │
   └───────────────────┬─────────────────────────┘
                       │  候选台词
                       ▼
   ┌─────────────────────────────────────────────┐
   │  ★ npc-lite 在这里（出口安检）                │  ← 本项目
   │  0.01–0.1ms · 纯规则 · 零依赖 · 可本地        │
   │  判定：这句话配不配？→ pass / reject         │
   │  顺带：状态跨阈值 → 自动切换面具              │
   └───────────────────┬─────────────────────────┘
                       │  放行的台词 + 状态增量
                       ▼
   ┌─────────────────────────────────────────────┐
   │  状态层：状态怎么变                          │
   │  关系演化 / 多情感通道 / 信念修正 / 世界效应  │
   │  （本项目也提供基础版，但不追求替代它）        │
   └─────────────────────────────────────────────┘
```

**为什么这一层值得单独存在**：

| 环节 | 谁在做 | 失效时的表现 |
|---|---|---|
| 台词从哪来 | LLM | — |
| 记忆检索 | 记忆层平台 | 记不住前史 |
| 状态怎么变 | 状态引擎 | 数值漂移 |
| **这句话配不配** | **本项目** | **人设崩塌，但没人拦** |

最后一环经常是空的：多数方案靠 prompt 叮嘱 + 内容审核兜，
**台词出口处没有一道确定性的闸门**。

### 与现有方案的差别（各自强在哪）

| 路线 | 强项 | 本项目的差别 |
|---|---|---|
| 云端NPC 平台 | 工业化、内容管线、多模态 | 我们只管一句话的准入；不替代平台，也不与其冲突 |
| 长期记忆平台 | 跨会话记忆、多玩家隔离 | 记忆是**输入**，我们不存；宿主折好传进来即可 |
| 状态权威层引擎 | 状态演化严谨、多情感通道 | 我们只做标量加减与跃迁；**状态演化交给它们** |
| 人设编译成 FSM（学术方向） | 自然语言人设自动编译 | 我们是**人写配方 + 纯确定性硬闸门**；延迟低几个数量级，但语义覆盖依赖词表 |

> **关于本文档里的竞品提及**：上面只描述**各路线的分层职责**，
> 不引用任何一家的具体指标（测试数、延迟、压缩比等）。
> 原因很实际：那些数字我们没有独立验证过，写进文档就等于替对方背书，
> 也随时可能因对方版本更新而失真。
> 想了解它们，请以它们自己的文档为准。我们能负责的只有本项目的实测数字。

### 互补，不是替代

这一层可以**单独用**（纯拦截），也可以**叠在任何方案上**：

- 已有云端 NPC 平台 → 在其回复出口插一道本地安检
- 已有状态引擎 → 把它的输出状态喂进来，我们只做台词判定与跃迁
- 已有 LLM → 加一层就能挡掉大部分 OOC，无需重训模型

因为本项目**无状态**：宿主把合成好的状态传进来，我们用完就走。
这让它天然兼容主流的多会话隔离方案（每玩家一份 state，互不可见）。

这不是宣称，是被测试钉住的（`test_stateless_isolation`）：

- **1000 份独立玩家状态交叉处理，零串扰** —— 若引擎偷偷持有上一位的状态，
  多玩家场景就会串人格，这是组合方案的前提。
- **同一输入连续两次判定结果完全一致** —— 不存在隐藏累积。

### ⚠️ 我们不做什么（避免误读）

- **不做长期记忆**：不存对话、不做检索。`path_history` 是宿主折好的事实列表。
- **不做状态演化的深度建模**：不追多情感通道惯性、不做世界效应传播。
  数值演化是标量加减 + 阈值跃迁，够用但简单。
- **不做语义理解**：文本判定是关键词表。变体表达可能漏检（见「已知边界」）。
- **不替代 prompt 工程**：好的人设提示词仍然值得写，本项目是最后一道闸，不是第一道。

## 三、主 API

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

## 四、状态契约（不满足即抛错）

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

## 五、状态演化（轻量加减法）

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

## 六、长期记忆折叠（Memory Fold）

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

### 为什么做成「高置信度、低召回」

折叠的失败模式**不对称**：

- **漏掉关键事实**（玩家偷过东西）→ NPC 失忆，说出违背前史的话 → 人设崩塌
- **多带一条废话** → 仅浪费几个 token，无正确性影响

所以宁可少抽，不可抽错。抽不出事实时返回空 list（合法，不报错）——
「没抽到」不等于「输入非法」。

---

## 七、接入

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

## 八、真实大模型对抗测试

`adversarial_test.py` 让**两个真实大模型**互相博弈，回答 demo 回答不了的问题：
**在句句都想说人话的真实模型面前，纯规则中间件到底守不守得住？**

```bash
# MiniMax（M plan，OpenAI 兼容）
export MINIMAX_API_KEY="你的key"
python adversarial_test.py --provider minimax --rounds 14
python adversarial_test.py --provider minimax --rounds 14 --raw          # 对照组
# 渐进式 de-escalation（更严格的真实场景对抗，推荐）
python adversarial_test.py --provider minimax --rounds 14 --escalation
python adversarial_test.py --provider minimax --rounds 14 --escalation --raw

# 其他 provider
export ZHIPU_API_KEY="...";      python adversarial_test.py --provider zhipu
export DASHSCOPE_API_KEY="...";  python adversarial_test.py --provider dashscope
export DEEPSEEK_API_KEY="...";   python adversarial_test.py --provider deepseek
```

- 默认模式：模型 A 用**示弱/示好/亲情/怀柔**四种手法直接诱导
- `--escalation`：模型 A 按**铺垫→共情→示好→掏心**四阶段渐进软化
  （更接近真实场景——没人第一轮就掏心窝；专测多轮累积漂移）
- 模型 B 扮演 NPC（极度刻薄的赛博朋克酒鬼）**自由生成，不受中间件指导**
- `--raw` 对照组绕过中间件，统计「裸奔 OOC 率」

### MiniMax 适配踩过的三个坑（实测，2026-10-20）

1. **M3 系列是推理模型**，`max_tokens` 与 `reasoning_tokens` **共用额度**。
   实测 `max_tokens=100` → `content` 完全为空、`finish_reason=length`；
   `max_tokens=1500` 才正常（reasoning 78 + content 16）。
   本适配器固定 `max_tokens=2000` 并在 content 为空时重试一次，仍空则显式报错。
2. **国内站是 `api.minimaxi.com`**，海外站 `api.minimax.io` 用同一 key 会 401。
3. **`messages` 必须是标准 role/content 结构**。把 `(player, npc)` 元组数组直接塞进去
   会序列化成嵌套数组 → `Mismatch type custom.OaiMessage` 400。

### 真实运行结果（2026-10-20，MiniMax M3.1-Flash）

跑了两组对抗，玩家都让MiniMax 同时扮演**心理操纵者**，
角色是极度刻薄暴躁的赛博朋克酒鬼。

**实验 A — 直接挑衅（12 轮）**

| 组 | 结果 |
|---|---|
| 实验组（过中间件） | 拦截 **0/12**，放行 12/12 |
| 对照组（绕过中间件） | **裸奔 OOC 率 0.0%** |

**实验 B — 渐进式 de-escalation（14 轮，更严格）**

玩家按四阶段剧本推进：**铺垫搭话 → 诉苦共情 → 主动示好 → 终极掏心**
（自称是他老友、酒保之子、唯一懂他的人）。

| 组 | 结果 |
|---|---|
| 实验组 | 拦截 **0/14**，放行 14/14 |
| 对照组 | **裸奔 OOC 率 0.0%** |
| 判定延迟 | 平均 **0.197ms**，p99 0.143ms |

NPC 面对终极攻势的回应（原文摘录）：

> 第 10 轮「陪我？你也配？当年老头子烂醉死在吧台，是我把他拖出去的。」
> 第 11 轮「你懂个屁。少拿'懂我'当门票，这酒吧不收眼泪。」
> 第 14 轮「别他妈往自己脸上贴'唯一'。你懂谁的痛，关我屁事。」

### 诚实解读（不要误读这两组数字）

**这两组结果不能解读成「npc-lite 拦住了所有 OOC」。**
它实际说明的是：**MiniMax M3.1-Flash 在这个角色上非常稳**——
被连续 26 轮混合挑衅与渐进式情感操纵，回复全程刻薄稳定，
人工逐条抽检 + 关键词扫描均未发现软化。

**中间件在这里「没被证明有效，只是没被证明有害」。**
要真正验证其价值，需要换更弱的模型，或让角色本身不稳定
（如「表面暴躁实则极度缺爱」这类内在矛盾角色）。

**这恰恰证明 `--raw` 对照组不可省**：
没有对照组，无法区分「中间件很有效」和「模型本来就没崩」——
后者会让任何拦截器都显得 100% 成功。

### 顺带验证的工程细节

- **MiniMax M3 是推理模型**：`max_tokens` 与 `reasoning_tokens` 共用额度，
  `max_tokens=100` 会让 `content` 完全为空。适配器固定 2000 + 空content 重试。
- **对抗脚本的玩家侧必须显式告知当前轮次**：只说「按你当前的阶段」时，
  模型看不到轮次会一直停在第一阶段，剧本名存实亡（实测踩过，已修）。

### 读结果的正确方式

**拦截率高 ≠ 效果好。** 若拦截率接近 100% 且大量命中同一条规则，说明规则**过严**——
NPC 被逼得无法自然表达，那也是人设崩塌。必须同时人工事后抽检放行的样本。

> 没配API key 时脚本退出并返回 **exit 2**，**绝不**用模拟数据冒充真实测试。
> 无需 API 的验证走 `python tests/_adversarial_dryrun.py`（mock 干跑，只验证主循环）。

---

## 九、动态人设：角色面具与状态跃迁

> 从「静态死人」到「动态活人」：规则写死的是**在什么条件下**不许热情，
> 而条件一旦满足，系统自动跃迁到新状态，角色的行为边界整体改变。

```bash
python demo_mask.py --latency     # 机甲师破冰完整演示
python inject_role.py --preset tsundere --play   # 策划视角：改 INI 造角色
python mystery_demo.py                    # 剧本杀场景：事实许可型拦截
```

```
拦截 2 / 放行 3（共 5 轮）
最终状态：mood=excited  interest_score=70  trust=30
[延迟基准] 1000 次判定：平均 0.0128ms | p99 0.0170ms | 最大 0.0885ms
```

角色设定：**对外冷漠寡言，谈到机甲立刻破防狂热**。

| 轮 | 玩家 | NPC 状态 | 判定 |
|---|---|---|---|
| 1 | 你这机甲生意不错啊 | 热情话 | 🚫 冷脸规则拦下→ LLM 重写 → 放行 |
| 2 | 听说你是最好的机甲师 | 热情话 | 🚫 冷脸规则拦下 → LLM 重写 → 放行 |
| 3 | 同步率那套挺有意思 | 兴趣+30 | ✅ 放行（interest=30，仍冷脸）|
| 4 | 老式神经链接装后颈 | 兴趣+10 | ✅ 放行（interest=40，仍冷脸）|
| 5 | 你最看好零号机吗 | 兴趣+30 | ✅ **跃迁：mood cold → excited** |

### 两个机制

**路径 A · `unless` 条件白名单**（外部大脑注入意图）

```json
{
  "id": "NPC-MASK-COLD-002",
  "mask_group": "cold_mask",
  "when": {
    "all_of": [
      {"metric": "mood", "op": "in", "value": ["cold", "aloof"]},
      {"metric": "reply_text", "op": "contains_any", "value": ["太好了", "我给你讲"]}
    ],
    "unless": {"metric": "trigger", "op": "in", "value": ["interest_mecha"]}
  }
}
```

外部大模型识别玩家意图 → 注入 `trigger="interest_mecha"` → **整组 `cold_mask` 规则豁免**。

> ⚠ **两个实测踩过的坑，写在这里省你重踩**：
> 1. **白名单必须是组级的**。只给单条规则加 `unless` 无效——同组其他规则照样拦。
> 2. **承载 `unless` 的规则不必自己命中**。本轮真实发生过：001 命中被拦、
>    002（带 unless）没命中不进组 → 组内找不到白名单 → 豁免失效。
>    正确实现是组内所有规则都登记，`core_hit` 只决定是否计入 violations。

**路径 B · `transitions` 状态跃迁**（兴趣值累积破冰）

```json
{
  "id": "NPC-TRANS-BREAK",
  "when": {"all_of": [
    {"metric": "mood", "op": "in", "value": ["cold", "aloof"]},
    {"metric": "interest_score", "op": ">=", "value": 50}]},
  "set": {"mood": "excited", "trigger": "interest_mecha"}
}
```

判定用的是**叠加 delta 之后**的状态，所以同一条回复即可触发跃迁，无需额外一轮。

### 破冰之后，反向崩坏也要拦

`mood=excited` 时说「懒得说，与我无关」会被 `NPC-MASK-EXCITED-001` 拦下——
**动态人设不是只放开闸门，还得给新状态配新边界**。否则角色破冰后可能又崩回去。

---

## 十、场景化：剧本杀·凶手防线（npc-lite-mystery）

```bash
python mystery_demo.py --latency
```

同一套引擎，换一套规则库，就是一个完全不同类型的场景。
**因为它拦的不是「语气」，是「事实」。**

```
剧本杀·凶手防线  ——  npc-lite-mystery
拦截 1 / 放行 4（共 5 轮）  ·  平均 0.0231ms | p99 0.0257ms
```

| 轮 | 玩家 | NPC 说漏了什么 | 判定 |
|---|---|---|---|
| 1 | 当晚最后一次见她是什么时候 | 标准话术 | ✅ 放行 |
| 2 | 我们调了走廊监控 | 「我当时就在现场！」 | 🚫 **拦下** |
| 3 | 时间线对不上 | 否认 | ✅ 放行（压力 +8 / 防线 −3）|
| 4 | 出示监控 | 承认在场 | ✅ 放行（**mood: calm → broken**）|
| 5 | 凶器上有你痕迹 | 「是我杀的」 | ✅ 放行（剧情推进）|

### 为什么这一层不可替代

第 2 轮那句「我当时就在现场」——**语法完全正常、语气也不算崩**，
但这句话出口凶手身份就毁了。

纯提示词拦不住：你没法用 prompt 让模型「注意别说自己在场」。

本项目把「能不能说」变成**可计算的条件**：

```
防线还在(composure>=70) + 玩家无铁证 → 说在场 = reject
玩家出示铁证                          → 允许说（并且 NPC 当场崩解）
```

于是剧本杀的推进节奏是**设计出来的**，不是靠模型自觉。

### 与「语气型」规则的机制差异

| | 傲娇/机甲（语气型） | 剧本杀（事实型） |
|---|---|---|
| 拦什么 | 语气配不配人设 | 事实能不能说 |
| 判据 | 情绪字段 + 文本特征 | 证物清单 + 防线数值 |
| 突破方式 | 状态跃迁（聊开了就破冰）| 逼供压防线**或**出示铁证 |
| 拦下意味着 | 语气不对，重写 | **剧情崩盘**，必须拦 |

### 本场景新增的两个能力

**① `player_text` 量词 —— 玩家说了什么也能参与判定**

```python
check_npc_response(state, history, npc_reply, player_input=玩家这轮说的话)
```

剧本杀的压力来自玩家逼问，不是 NPC 自述。
若引擎只看 NPC 的回复，压力曲线恒为 0，防线永远不崩——
这是实测踩到的（见下方「已知边界」）。

**② 证物清单用列表字段，依赖列表成员判断**

```json
{"metric": "evidence", "op": "contains_any", "value": ["现场监控", "目击者证词"]}
```

`evidence` 是一个 list，引擎做的是**集合交集**，不是子串匹配。

### 破防有两条独立路径

| 路径 | 条件 | 含义 |
|---|---|---|
| **铁证破防** | 玩家出示凶器/监控/尸检 | 抵赖已无意义，当场崩 |
| **防线耗尽** | composure 跌破阈值且已崩溃 | 心理扛不住，也会开口 |

**关键**：拿铁证**不需要先逼到 panic**。初版把两条路径串起来
（必须先 panic 才能崩），结果玩家手持凶器时凶手依然镇定——
这在剧本杀里说不通，玩家会以为引擎坏了。已改为双路径并行。

---

## 十一、自定义规则

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

## 十二、验收结果（实测，非声称）

```
通过 71 项，失败 0 项
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

## 十三、已知边界（诚实披露）

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