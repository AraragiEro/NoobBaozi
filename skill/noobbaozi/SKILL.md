---
name: noobbaozi
description: 读取并理解 EVE Online 配装（EFT / DNA / killmail）。通过 noobbaozi MCP 拿六维度事实、三态可信度与诊断信号；用于回答「这套配装什么样、哪里有问题、能不能飞」，不用于生成配装、写战报叙事或做判定。
---

# noobbaozi · 配装理解

一个 MCP 服务，提供 EVE 配装的**事实**：六维度数值、三态 provenance、诊断 flags、战报上下文。**它只给事实，不做判断、不评分、不写叙事。**

> **工具细节不在这份文件里。** 每个工具「何时用 / 返回什么 / 已知限制」都写在 MCP 的工具描述里（`tools/list` 的 `description`），推荐顺序在服务握手的 `initialize.instructions` 里。本文只留三件事：要不要用、怎么连、判读纪律。

## 什么时候用

- 用户贴了一份配装（EFT / DNA / zkillboard 链接 / killmail JSON），想了解它
- 问「这套打得动吗 / 为什么死了 / 哪里有问题 / 能不能装下 / 谁在打这个星系」
- 问「这个人 / 这个军团 / 这个联盟水平如何、常在哪儿活动」→ `zkb_stats` 给实体画像（战绩、风格指标、6 类榜单）

**不该用它做的事**（这三类别指望它，也别替它圆场）：

- **不生成配装、不推荐改法** —— 它只复述输入，不会替你设计一套
- **不写战报叙事、不给生死判词、不预测战斗结果**
- **不做评分与排名** —— `unknown` 就是 `unknown`，不许补成 0 或常识值

## 怎么连

```powershell
python -m noobbaozi.mcp_server --transport http --host 0.0.0.0 --port 8080
```

stdio 形态、客户端 JSON、`/health`、错误语义等细节见 `noobbaozi/mcp/README.md`。

## 三条判读纪律

**① `unknown` 不是 0。** `provenance == "unknown"` 时不要用常识补数；先看 `unknown_reasons` 说清是哪一种：

| reason | 含义 | 该怎么说 |
|---|---|---|
| `not_applicable` | 这套配装不适用（炮船没有导弹爆炸半径） | 「不适用」，**别说「不知道」** |
| `input_incomplete` | 输入不足（killmail 不带突变信息） | 降低置信度或说明数据不足 |
| `ambiguous` | 无唯一答案（四系抗性并列） | 「四系相同」 |

**② 顶层数值可能对应不可达状态。** 多件推进模块同时启用时，`speed_mps` / `signature_radius_m` 是**叠加值**，不一定是玩家能达到的状态：用 `mobility.propulsion_states.single` 看「只开某一个」的真实值，`state_exclusive` flag 会提示这种情况。转述时两件事一起说，别只念顶层数。

**③ 不要自己打开 zkillboard / ESI 网页。** 单条战报的上下文——含受害者与伤害 Top 的驾驶员 / 军团 / 联盟名字——`fit_killmail` 已给名字，不用再查 ESI；列表检索（每行也带受害者名字）由 `zkb_search` 给，配装数值由六维度工具给。拿不到的值工具会给 `null`（原因写在 `warnings` / `unknown_reasons` 里）—— 如实转述，不要用网页正文去补。

**④ 战报里「记录船是蛋」不等于蛋在输出。** `Capsule`（蛋）装不了任何装备，所以蛋不可能打出武器伤害：蛋出现在攻击者里，说明该玩家在这场交火中**已经失去了自己的船**，那些伤害来自他**被摧毁的船** —— 伤害数字是真的，**船归属不可信**，按船型统计时不要拿蛋当输出船（`ship_attribution` / `recorded_ship_is_capsule` 已标）。另外「攻击者人数」也不等于「有伤害人数」：`count` 是挂名人数，另有 `damaging_count` / `zero_damage_count`，两者之和等于 `count`。

**⑤ 高槽 ≠ 炮位。** turret 与 launcher 是**两种硬点**，都长在高槽模块上，但「高槽 5 个」**不是**「5 个发射器挂点」：看 `resources.hardpoints.{turret,launcher}.{total,used,free}`（`free` 为负 = 假装了，`hardpoint_violation` flag 会同时给出超装几件）；分不清是炮还是发射器的已装模块进 `hardpoints.unclassified`，别自己归类。

**⑥ 要 EFT 文本就用工具返回的 `eft` 字段，不要自己拼装。** 它是从**真实配装**渲染出来的规范文本（槽位分组、同型逐行、无人机 / 弹药物品段），可以直接复制。自己拼出来的模块名与件数会漂（实测出现过「多出 2 门发射器」这种编造），而且 `eft` 为 `null` 时 `warnings` 会说明原因 —— 那就如实说渲染不出来，**不要手写一份补上**。
