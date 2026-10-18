---
name: evefit
description: 读取并理解 EVE Online 配装（EFT / DNA / killmail）。通过 MCP 工具拿到六维度事实、三态可信度与诊断信号；用于回答「这套配装什么样、哪里有问题、能不能飞」，不用于生成配装或写战报叙事。
---

# evefit · 配装理解

一个 MCP 服务，暴露 5 个工具。**它只给事实，不做判断、不评分、不写叙事。**

## 什么时候用

- 用户贴了一份配装（EFT 文本 / DNA 字符串 / zkillboard 链接 / killmail JSON），想了解它
- 用户问「这套打得动吗 / 为什么死了 / 哪里有问题 / 能不能装下」
- 需要把多套配装放在一起比较某个维度

**不要用它**：生成配装、推荐配装、写战报叙事、预测战斗结果。

## 工具与调用顺序

```
fit_parse     先确认「我读懂了没有」           ~40ms
   ↓  （ship 对、模块件数对、warnings 看一眼）
fit_index     六维度摘要（约 2,000 字符）      ~120ms   ← 默认就到这一步
   ↓  （哪个维度可疑？flags 里有没有 hint）
fit_view      单视角全字段 + 三张平行表        ~20ms
   ↓
fit_get       按路径取一个数 + 它的可信度      ~20ms
fit_detail    列表型明细（升级候选、逐模块电容、完整技能表）——按需
```

**默认只调到 `fit_index` 就够。** 不要一上来拉 `fit_detail` —— 那是逐模块量级的数据。

## 三个必须看懂的字段

### 1. `provenance` —— 这个数依赖假设吗

| 值 | 含义 |
|---|---|
| `measured` | 输入直接决定，不依赖假设 |
| `assumed` | 依赖技能档 / 模块状态 / 伤害剖面等假设 |
| `unknown` | 算不出来（**不要当 0 用**） |

### 2. `unknown_reasons` —— 为什么算不出来

| 值 | 含义 | 你该怎么办 |
|---|---|---|
| `input_incomplete` | 输入数据缺失（如 killmail 不带突变信息） | 降低置信度，或说明「数据不足」 |
| `not_applicable` | **该字段对这套配装不适用** | 直接忽略，别说「不知道」 |
| `ambiguous` | 无唯一答案（如四系抗性并列） | 说「四系相同」，不是「不知道」 |
| `engine_omits` | 引擎永不提供 | 别用这个字段 |

**"不适用" 和 "不知道" 是两件事。** 炮船没有爆炸半径参数，不代表我们算不出。

### 3. `flags[].hint` —— 一句话告诉你哪儿不对

```json
{"id": "state_exclusive", "hint": "AB与MWD同时启用"}
{"id": "resource_overflow", "hint": "CPU超14.32"}
{"id": "resist_hole", "hint": "装甲最弱:爆炸"}
```

## 典型对话

**「这套配装怎么样？」**
→ `fit_index`，用六维度各一句概括 + flags 里最要紧的两三条。

**「为什么它飞不起来？」**
→ `fit_index` 看 `resources.feasible` 与 `constraints.tier_violations`；
→ 若 `feasible: false`，再 `fit_view("resources")` 看具体缺多少；必要时 `fit_detail` 看升级候选。

**「它的速度是多少？」**
→ `fit_get("mobility.speed_mps")`，**一定要连同 `provenance` 一起说**。
→ 若 `propulsion_states.single` 存在多个条目，说明**顶层速度是「多个推进同时启用」的叠加，可能是不可达状态** —— 要给出「只开某一个」的真实值。

**「这套能打死人吗？」**
→ 不要回答这个问题。给 `firepower` 的事实（DPS、齐射、射程、伤害构成），
   并说明 `application` 是**未建模**（confidence E），命中率不在能力范围内。

## 已知限制（务必如实转述，不要替它圆场）

| 限制 | 说明 |
|---|---|
| **突变模块** | killmail 不携带 Abyssal mutation，相关数值会失真；此时 `unknown_reasons` 会给 `input_incomplete` |
| **模块状态** | killmail 不带 overheat / 损伤态；工具按导入规则推定，并在 `meta.module_states` 回显 |
| **激活互斥** | 引擎只认同组互斥；跨组（如 MWD ↔ MJD）由人工规则表补，命中时出 `state_exclusive` |
| **多伤害剖面** | 目前只支持均匀剖面（25/25/25/25） |
| **命中率 / 有效 DPS** | 未建模（`application` 字段的 `confidence` 是 E） |

## 使用 MCP

服务启动：`python -m evefit.mcp_server`（stdio）。客户端配置见 `evefit/mcp/README.md`。

自检：`python -m evefit.mcp.client_smoke` —— 它会连上服务、跑完五个工具 + 两个负例，打印 `PASS`。

## 一条纪律

**工具返回 `unknown` 时，不要用你的常识填一个数。**
用户问「那到底多少」，正确的回答是「这个数据在没有植入体信息的情况下算不出来」，而不是猜一个。
