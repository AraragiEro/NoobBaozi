# 团队名册与在役期限

> 目的：避免「一个任务一个新代理」造成反复重建上下文，同时防止长期运行后上下文漂移。
> 规则：**每个开发员工最多执行 5 个任务，第 5 个交付时写交接摘要后退场。**

## 一、在役成员

| 代号 | 角色 | 文件拥有权 | 已执行任务 | 状态 |
|---|---|---|---|---|
| `dev-engine-2` | 投影层与数值口径（第二任，接 self 前任交接） | `project/**`、`config/diagnostics.json`、`config/activation_groups.json`、`tests/test_views_*.py` / `test_render.py` / `test_attr_coverage.py` / `test_units.py` / `test_t54_audit.py` | **0 / 5** | 在役 |
| `dev-surface` | 对外契约与工具面（API / CLI / MCP / 文档 / 容器） | `api.py`、`cli.py`、`mcp_server.py`、`mcp_http.py`、`mcp/**`、`docker/**`、`skill/**`、`tests/test_api.py` / `test_cli.py` / `test_mcp_*.py` | **0 / 5** | 在役 |
| `evefit-tester` | 独立验收（不占开发名额） | `acceptance/**` | 常驻 | 在役 |

Lead 拥有：`engine/**`、`sde/**`、`contracts.py`、`docs/**`、`PROGRESS.md`、`git` 操作，
以及下列**引擎/底层测试**（它们测的是 Lead 的代码，员工改不了被测算的东西，所以不给员工）：
`tests/test_engine.py`、`test_sde.py`、`test_attribute_dict.py`、`test_contracts.py`、`test_fighters.py`、`test_regress.py`。

### 测试文件属主总表（**每个 test_*.py 必须有属主**）

| 属主 | 测试文件 |
|---|---|
| `dev-surface` | `test_api.py`、`test_cli.py`、`test_mcp_server.py`、`test_mcp_http.py` |
| `dev-engine-2` | `test_views_a.py`、`test_views_b.py`、`test_render.py`、`test_attr_coverage.py`、`test_units.py`、`test_t54_audit.py`、**`test_eft.py`、`test_projection_core.py`、`test_summary.py`、`test_budget_guard.py`** |
| Lead | `test_engine.py`、`test_sde.py`、`test_attribute_dict.py`、`test_contracts.py`、`test_fighters.py`、`test_regress.py` |

> **教训（2026-10-19）**：`test_eft.py` / `test_projection_core.py` 曾是「一次性代理 T-54 写的、没人认领」的文件。
> `dev-engine-2` 做改名任务时被它 9 条红卡住，才发现名册漏了 10 个测试文件。
> **规则：名册必须覆盖 `tests/` 下每一个文件；一次性代理新增的文件，交付时必须显式移交属主。**


## 二、任务计数

| 日期 | 员工 | 任务 | 累计 |
|---|---|---|---|
| 10-19 | `dev-surface` | 1. `zkb_stats` 战报实体画像（A 段采集+契约，B 段实现 34 条测试） | **1 / 5** |
| 10-19 | `dev-engine` | 1. 审查炮位三态 + EFT 边界（7 条硬结论、2 个真缺陷、非永真证明） | **1 / 5** |
| 10-19 | `dev-engine` | 2. `firepower` 单一出处 + `unclassified` 拆分 + `slot_usage` 的 unknown→0 | **2 / 5** |
| 10-19 | `dev-surface` | 2. 文档四态同步 + 预算 20,500 + 17 处取整透传 + 错误分类共用 | **2 / 5** |
| 10-19 | `dev-engine` | 3. EFT 模块行带回装填弹药（旧测试语义写反，已反转） | **3 / 5** |
| 10-19 | `dev-surface` | 3. `fit_killmail` 冷路径提速（zkb 原文短路 + systems 并行 + items 三态） | **3 / 5** |

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

## 五、开发自测 vs 节点门禁（2026-10-20 改）

**原因**：原来「每个员工交付前都跑一遍全量 1500+ 条」在员工制下代价过高 ——
一次全量约 100 秒 + 输出占上千 token，一单跑两三次就是纯浪费。全量并不需要每单都跑。

### 分工

| 角色 | 跑什么 | 什么时候 |
|---|---|---|
| **开发员工** | **只跑自己拥有的测试文件**（见「测试文件属主总表」），外加**受本次改动影响的文件** | 每次交付前 |
| **Lead** | **全量 pytest** + 属性门禁（`check_attr_coverage`） | **节点**（见下） |
| **`evefit-tester`** | 独立验收脚本（`acceptance/**`，含 65×43 字段级） | 节点，且与开发不同时 |

### 开发员工的正确自测命令

```powershell
# 只跑自己的文件（把 a b c 换成自己的）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" --with pytest `
  python -m pytest -q noobbaozi/tests/test_views_b.py noobbaozi/tests/test_combat.py `
  -p no:cacheprovider
```

**报告里写清**：跑了哪些文件、多少 passed、`git status` 是否只含自己的文件。

### 什么算「节点」

满足任一条即触发节点门禁（由 Lead 执行）：

1. **一次批量交付结束**（例如「今天这一轮」所有在役员工的单都交完）
2. **部署之前**（任何 `docker/redeploy.sh` 之前必跑一次全量）
3. **改共享形状之后**（`api.py` 信封、`project/**` 的字段键、`TOOL_NAMES`、常数量表）——
   这类改动会横跨多个测试文件，员工自测覆盖不到
4. 主人要求时

### 员工仍然必须做的（不许省）

- **非永真证明**：改坏源码 → 自己的测试必须红（这是质量底线，不是全量测试的替代品）
- **跨范围影响自查**：改了别人会断言的形状（如 `evidence` 全等、`PASS N/N`、术语表 allowance），
  **必须显式报告「这会影响 X 文件，需要其属主同步」** —— 别指望节点门禁替你发现
- `git status` 快照（只含自己的文件）

### 节点门禁命令（Lead 用）

```powershell
Set-Location F:\WorkSpace\GIT\opencode_general_workspace
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" --with pytest `
  python -m pytest -q noobbaozi/tests -p no:cacheprovider 2>&1 | Select-Object -Last 4
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
  python -m noobbaozi.tools.check_attr_coverage 2>&1 | Select-Object -Last 2
```

### 已知风险与对策

| 风险 | 对策 |
|---|---|
| 员工 A 的改动打红员工 B 的测试，直到节点才发现 | 员工**必须**在报告里写「跨范围影响」，Lead 收到后立刻转给属主 |
| 节点时一坨红，分不清谁的责任 | 节点报告里先跑一次 `pytest -q --tb=no` 拿失败清单，再按文件属主分组 |
| 员工自测「只跑自己的」漏了共享 fixture | 共享 fixture 变更必须广播（`tests/fixtures/**` 归 Lead） |

## 六、禁用 SFTP 工具（2026-10-20，主人指示）

**禁止使用任何 `sftp_*` 工具**（`sftp_upload_dir` / `sftp_download_dir` / `sftp_list` /
`sftp_read` / `sftp_write` / `sftp_mkdir` / `sftp_rename` / `sftp_delete`）。

**原因**：在 NAS（UGREEN DXP4800P + 大文件 + 链路抖动）上反复卡死，表现为**调用不返回**
（不是报错）。已发生至少三次，每次都白等数分钟并打乱并行节奏。

### 替代方案（按优先级）

| 需求 | 用这个 |
|---|---|
| 把代码送到 NAS | **GitHub 中转**：本地 push → NAS `git fetch/reset`（`docker/redeploy.sh` 已内置） |
| 把单个文件送到 NAS | 传 GitHub Release 资产 → NAS `curl -L` 下载（索引库 14 MB 就是这么过去的） |
| 看 NAS 上的文件 | `ssh_exec` 里 `cat` / `sed -n` / `head` / `grep` |
| 改 NAS 上的文件 | `ssh_exec` 里 `sed -i` / 重定向；大改用 Git 中转 |
| 大文件在 NAS 内部搬 | `ssh_exec` 里的 `cp` / `mv` / `rsync`（本地局域网内的 shell 命令，不走 SFTP 协议） |

### 判定

- 调用**超时/无返回** → 立刻换方案，**不要重试第二次**
- 不确定要传什么 → 先 `git status` + `git diff --stat` 看清楚，多数情况根本不用传文件



## 七、清理节律（2026-10-20，主人要求「及时清理」）

**每个节点结束时**（或每轮批量交付后）做一次，10 秒的事：

| 清什么 | 命令 / 判据 |
|---|---|
| 后台任务 | `job_list` 必须为空；有残留的 `job_kill` |
| 空闲员工 | `list_agents` 里除在役者外全为 `inactive`；**成员上限 8，满了就复用空闲槽位，不要再 spawn** |
| `.tmp/` | 只保留三份契约（`explain_contract.md` / `projection_contract.md` / `zkb_stats_contract.md`），其余全删 |
| 工作区根 | 只允许 `AGENTS.md` + 项目文件夹 |
| `git status` | 必须 clean（未提交 = 有人还在写） |

**为什么要有节律**：2026-10-20 那次清理从 `.tmp` 清掉 **36 MB / 750 个文件** ——
全是探针脚本与中间产物。**这些东西不占仓库，但会让人误以为"项目里有这些文件"。**

### 成员上限的处理

Team 成员上限 **8**（含退役但未销毁的）。满了之后：
- **不要 `spawn_teammate`**（会直接报 `Team member limit 8 reached`）
- 给一个 `inactive` 的老成员 `send_message` 一条完整新简报，**复用它的槽位**
- 简报里必须重申当前规矩（sftp 禁令、自测范围、写入范围）—— 老成员的上下文可能过期