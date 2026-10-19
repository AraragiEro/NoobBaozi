# 团队名册与在役期限

> 目的：避免「一个任务一个新代理」造成反复重建上下文，同时防止长期运行后上下文漂移。
> 规则：**每个开发员工最多执行 5 个任务，第 5 个交付时写交接摘要后退场。**

## 一、在役成员

| 代号 | 角色 | 文件拥有权 | 已执行任务 | 状态 |
|---|---|---|---|---|
| `dev-engine` | 投影层与数值口径（六视角 / 诊断 / 渲染） | `project/**`、`config/diagnostics.json`、`config/activation_groups.json`、`tests/test_views_*.py` / `test_render.py` / `test_attr_coverage.py` / `test_units.py` | **0 / 5** | 在役 |
| `dev-surface` | 对外契约与工具面（API / CLI / MCP / 文档 / 容器） | `api.py`、`cli.py`、`mcp_server.py`、`mcp_http.py`、`mcp/**`、`docker/**`、`skill/**`、`tests/test_api.py` / `test_cli.py` / `test_mcp_*.py` | **0 / 5** | 在役 |
| `evefit-tester` | 独立验收（不占开发名额） | `acceptance/**` | 常驻 | 在役 |

Lead 拥有：`engine/**`、`sde/**`、`contracts.py`、`docs/**`、`PROGRESS.md`、其余测试与配置文件、`git` 操作。

## 二、任务计数

| 日期 | 员工 | 任务 | 累计 |
|---|---|---|---|
| 10-19 | `dev-surface` | 1. `zkb_stats` 战报实体画像（A 段采集+契约，B 段实现 34 条测试） | **1 / 5** |
| 10-19 | `dev-engine` | 1. 审查炮位三态 + EFT 边界（7 条硬结论、2 个真缺陷、非永真证明） | **1 / 5** |
| 10-19 | `dev-engine` | 2. `unclassified` 拆分 / `HARDPOINT_LABELS` 去重 / `slot_usage` 的 unknown→0 | **2 / 5**（进行中） |

> 每交付一个任务，Lead 在此表追加一行；员工到 **5/5** 时写交接摘要并退场。

### 一次性代理（不计入在役期限）

| 代号 | 任务 | 状态 |
|---|---|---|
| T-54 | 炮位三态 + EFT 渲染的初版实现 | 已并入主线（`25d2055`），不再派活 |

## 三、交接摘要模板

员工在执行第 5 个任务时，除常规回报外必须额外给出：

```markdown
## 交接摘要（N/5，退场）
### 当前状态
<本领域现在是什么样：关键模块、已实现能力、已知限制>
### 未决事项
<做到一半的事、已知但未修的问题、待确认的口径>
### 下一步建议
<下一个接任者应当先做什么，为什么>
### 踩过的坑
<会让新人重复犯错的具体陷阱，含文件与行为>
```

## 四、并发写纪律

- 两名员工**不写同一文件**；跨范围需求 → 停下报告给 Lead 裁决
- 所有 `git` 写操作只由 Lead 执行
- 临时文件只放 `noobbaozi/.tmp/`；工作区根目录只允许 `AGENTS.md` + 项目目录
- 交付前必须跑全量 pytest，基线为当前 `passed` 数（不得下降）
