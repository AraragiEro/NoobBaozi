# noobbaozi · 配装理解工具

给 agent 用的 EVE Online 配装「读取 + 理解」工具套件。
**产出结构化事实与诊断；判断与叙事交给调用方 agent。**

---

## 当前状态

| 项 | 值 |
|---|---|
| 阶段 | **P0 / P1 / P2 完成**（P3 未开始） |
| 输入 | EFT / DNA / killmail（原始 KB JSON） |
| 输出 | 六视角事实 + 三态 provenance + 未知原因 + 诊断 flag |
| 验收 | **36 条真实 KB × 43 字段 = 1548/1548**；产品缺陷 0（medium+） |
| 测试 | `pytest -q noobbaozi/tests` 全绿（含变异自检） |
| 回归 | 快照 `enforced`，22 行 + 四模式 **0 DIFF** |

---

## 一条命令判断项目健康

```powershell
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.tools.regress
```

| 门禁 | 内容 |
|---|---|
| `pytest` | 全量测试 |
| `attr-coverage` | 视角用到的属性 ID 是否全在常量表（含 AST 裸 ID 扫描） |
| `snapshot` | 回归快照 `--enforce` |
| `acceptance` | 36 条真实样本全量验收（`--full` 才跑，依赖 pytest 通过） |

**退出码**：全 PASS → `0`；任一 FAIL → `1`；**有 SKIPPED 无 FAIL → 也 `1`**（跳过 ≠ 通过）。

---

## 目录

```
noobbaozi/
├── README.md                  本文件
├── PROGRESS.md                工作台账（状态 / 硬结论 / 欠账 / 踩坑）
├── contracts.py               契约：TypedDict + SPEC_VERSION
├── sde/                       L1 数据层：SDE 索引库、模式表、属性字典
├── engine/                    L2 计算层：引擎封装、归一化、假设推定
├── project/                   L3 投影层：六视角、provenance、诊断、渲染
├── tools/                     开发者工具（provenance 推导 / 常量覆盖 / 回归门禁）
├── tests/                     测试（含变异自检）
├── acceptance/                独立验收：36 样本 + 期望 + 报告 + runner
├── docs/                      讨论记录 / 字段规格 / 软件设计 / 开发规范
└── spike/                     可行性验证脚本（历史证据，可复跑）
```

## 分层

| 层 | 职责 |
|---|---|
| L5 接口 | MCP / CLI / Skill：只做「参数 → 结果」，不做 NL、不做叙事（**未开始**） |
| L4 知识 | RAG：敌人伤害剖面等（**不参与数值计算**，未开始） |
| L3 投影 | 单位换算、六视角、三态 provenance、诊断、分级渲染（**核心自研资产**） |
| L2 计算 | 引擎封装、输入归一化、假设推定、情景枚举（**无 LLM**） |
| L1 数据 | SDE 索引库、属性字典（含 ×1000 缩放手工表） |

## 六视角

火力 · 生存 · 机动 · 资源 · 约束 · 不稳定

每个视角只给**事实、边界、可疑点**；不汇总、不评分、不给判词。

## 输出的三层元信息

```json
{
  "views": {"firepower": {
    "overview":      { "dph": null, ... },
    "provenance":    { "dph": "unknown", ... },        // measured / assumed / unknown
    "unknown_reasons": { "dph": "input_incomplete" },  // 每个 unknown 为什么 unknown
    "confidence":    { "application.tracking_rad_s": "E" },  // 稀疏：只列非 A
    "flags":         ["weapon_damage_zero", ...]
  }},
  "modes": [ { "mode_id": 35686, "mode_name": "Defense", ... } ],
  "budget": { "chars": 14083, "degraded": false }
}
```

三者的分工（**正交，不可互相替代**）：

| 维度 | 回答的问题 |
|---|---|
| `provenance` | 这个数值**依赖假设**吗？ |
| `unknown_reasons` | 这条 `unknown` 是**数据缺失 / 不适用 / 并列 / 引擎不给**？ |
| `confidence` | 这个数值的**口径**可不可信？（A 已对拍 / C 不可用 / E 未建模） |

---

## 复跑验证脚本（历史证据）

从**工作区根目录**运行，先设 `$env:UV_CACHE_DIR`：

| 脚本 | 验证内容 |
|---|---|
| `spike/smoke*.py` | killmail → EFT、pass 4 派生属性、耗时、往返无损 |
| `spike/verify.py` | 抗性 vs ESI 官方值对拍 |
| `spike/survival.py` | 分层 HP / 四系 EHP / 修复速率语义 |
| `spike/state_test.py` | 模块 state 对速度 / 信号 / 对齐的影响 |
| `tools/derive_provenance.py` | 三态表的多样本推导（**会重写** `project/provenance.py`） |
| `tools/check_attr_coverage.py` | 常量覆盖硬门 |
| `tools/regress.py` | 全部门禁一键 |

## 下一步（P3，未定）

`render_detail` / `explain` / `sensitivity` · 对算接口（攻击方 + 目标 → 命中率、有效 DPS）· MCP Server + Skill

详见 `docs/配装理解-软件设计-v1.md` 第 6 节分期，与 `PROGRESS.md` §5 欠账清单。
