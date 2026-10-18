# evefit · 配装理解工具

给 agent 用的 EVE Online 配装「读取 + 理解」工具套件。
产出结构化事实与诊断；判断与叙事交给调用方 agent。

---

## 目录

```
evefit/
├── README.md                     导航 + 复跑说明
├── PROGRESS.md                   工作台账（状态/待办/硬结论/踩坑）
├── docs/
│   ├── 想法-技术路线讨论.md     讨论记录（13 轮，含全部裁决与实测证据）
│   ├── 配装理解-字段规格-v1.md   六视角字段规格（实现依据）
│   ├── 配装理解-软件设计-v1.md   分层架构与组件职责
│   └── 开发规范-v1.md           编码/测试/回归/子代理协作规范
├── spike/                        可行性验证脚本（历史证据，可复跑）
└── .gitignore                    忽略脚本运行时缓存
```

## 当前状态

- **阶段**：设计定稿，尚未进入实现
- **范围**：读取 + 理解。生成、战报、战争层均后置
- **地基已验证**（全部实测，非推断）：
  - 计算引擎 `eveshipfit-dogma-engine` 13.6.0（MIT）可用，**0.33 ms/次**
  - EFT 往返无损；堆叠惩罚正确；抗性与 ESI 官方值 12/12 项一致
  - 引擎**无任何查询能力** → 必须自建 SDE 索引库

## 分层

| 层 | 职责 |
|---|---|
| L5 接口 | MCP / CLI / Skill：只做「参数 → 结果」，不做 NL、不做叙事 |
| L3 投影 | 单位换算、六视角、三态 provenance、可疑点、归因、分级渲染（**核心自研资产**） |
| L2 计算 | 引擎封装、输入归一化、假设推定、情景枚举（**无 LLM**） |
| L1 数据 | SDE 索引库、属性字典（含 ×1000 缩放手工表） |
| L4 知识 | RAG：敌人伤害剖面等（**不参与数值计算**） |

## 六视角

火力 · 生存 · 机动 · 资源 · 约束 · 不稳定

每个视角只给事实、边界、可疑点；不汇总、不评分、不给判据。

## 复跑验证脚本

脚本已改好路径，从**工作区根目录**运行：

```powershell
$env:UV_CACHE_DIR = "$PWD\evefit\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]" python evefit\spike\smoke2.py
```

| 脚本 | 验证内容 |
|---|---|
| `fetch.py` | 从 zkillboard 拉样本 killmail |
| `smoke.py` | killmail → EFT，pass 4 派生属性、耗时 |
| `smoke2.py` | 伤害剖面加权、技能档影响、逐装备 volley |
| `smoke3.py` | 单位、alpha 聚合、往返无损、状态敏感性 |
| `verify.py` | 抗性 vs ESI 官方值对拍 |
| `survival.py` | 分层 HP / 四系 EHP / 修复速率语义 |
| `state_test.py` | 模块 state 对速度/信号/对齐的影响 |
| `mobility.py` | 机动属性口径 |
| `weapon_attrs.py` | 武器与弹药属性（含 ×1000 缩放） |
| `find_attrs.py` | 属性 ID 反查（heat / drone / launcher） |

样本：killmail **139246907**（Hecate，2026-10-18），仅 4 KB，随脚本保留在 `spike/kill.json`。

## 下一步

1. P0：SDE 索引库 + 属性字典 + 单位表
2. P1：引擎封装 + 归一化 + 假设推定
3. P2：六视角 overview

详见 `docs/配装理解-软件设计-v1.md` 第 6 节分期。
