# T-42 · pyfa/eos 独立对拍侦察报告

> 角色：noobbaozi 开发者（本任务只写探针与报告，**未改动任何产品代码**）
> 日期：2026-10-19 · SDE build 3607705 · 本引擎 `eveshipfit-dogma-engine 13.6.0`
> 探针：`noobbaozi/spike/pyfa_probe.py`（**唯一新增代码**）· 原始产物：`noobbaozi/spike/pyfa_scratch/`

---

## 0. 结论速览

| 问题 | 结论 |
|---|---|
| **Q1 pyfa 核心能否当库用？** | **部分可以**。PyPI 上的 `eos` 是 2016 年的死包（`PyYAML==3.12` 在 Python 3.13 上编不出来，实装失败）；真正的代码在 GitHub `pyfa-org/eos`，**纯 Python、零 PyQt 依赖**，可以只取 `eos/` 子包。但它**不带任何 SDE 数据**，必须自己喂（我用项目本地 SDE jsonl 现造了一个 data_handler，5.1 秒建完）。 |
| **Q2 pyfa 有没有「激活互斥」？** | **有属性、没跨组**。eos 建模了 `maxGroupActive/Online/Fitted` 三条规则，但计数的 key 是 **type 的 group**（`max_group.py:96-99`）。实跑验证：Panther 装 `500MN MWD(46) + Large MJD(1189)` 时 **eos 的 `validate()` 也不报 max_group_active**；只报 `skill_requirement` / `state`。而 `AB(46)+MWD(46)` 它照报。**与我们的引擎完全等价 —— 换引擎解决不了这条。** |
| **Q3 数值对拍** | **数值核心一致**：16 条真实样本，**15/16 逐件 DPS 相对差 < 1e-6**（唯一例外见 §4.2）；无人机伤害相对差 **2.6e-8 ~ 3.7e-8**；分层 HP **16/16 逐位一致**；EHP **13/16 一致**（3 条差 2.2–7.1%，根因可定位）；对齐 **16/16**；CPU/PG **16/16**。**但引擎有 2 处真实错误**：双推进速度叠乘（f12 差 140×）、MJD 信号罚常驻（f15 差 2.4×）。 |
| **建议** | **不换**。空洞在**规则层**，不在数值层；pyfa 那条路同样缺。见 §5。 |

---

## 1. 方法与可复现

```powershell
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
# 单跑某一问
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
  python noobbaozi\spike\pyfa_probe.py q2
# 三问全跑（Q3 默认取 16 条样本）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
  python noobbaozi\spike\pyfa_probe.py all --limit 16
```

产物目录 `noobbaozi/spike/pyfa_scratch/`（**说明：`spike/` 只 gitignore 了 `uvcache/`，这里的内容都是本任务新拉取的，未入库，可整目录删除**）：

| 路径 | 内容 |
|---|---|
| `eos_src/` | pyfa-org/eos master 源码（598 文件 / 240 个 `eos/**/*.py`），由 codeload tarball 展开 |
| `eos_pyenv/` | 为「真装一次 pip」准备的隔离 venv |
| `uv_install_eos.log` | `uv pip install eos` 的完整报错原文 |
| `probe_all.log` | 三问全跑的完整输出 |
| `q1_result.json` / `q2_result.json` / `q3_result.json` | 各 stage 的机器可读结果 |

三条纪律（与项目红线一致）：①装不上/跑不起来就如实打印异常，不编数字；②两边统一技能档才比；③每条结论连到 `文件:行号` 或 SDE 具体数值。

---

## 2. Q1 · pyfa 的核心（`eos`）能否作为库使用

### 2.1 PyPI：有包名，但**不可用**

```
eos        -> Eos 0.0.0.dev8  author='Pyfa Team'  requires_dist=['PyYAML (==3.12)']
              files=[('Eos-0.0.0.dev8-py2.py3-none-any.whl', '2016-10-06T22:21:05'),
                     ('Eos-0.0.0.dev8.tar.gz',            '2016-10-06T22:21:08')]
eos-pyfa   -> HTTP 404（不存在）
eve-eos    -> HTTP 404（不存在）
pyeos      -> pyEOS 0.63（Spotify 的网络设备库，与本项目无关）
```

**实装一次（不是看元数据推断）**：`uv pip install --python ...\eos_pyenv\Scripts\python.exe eos` → `rc=1`，原文（节选，全文在 `pyfa_scratch/uv_install_eos.log`）：

```
Resolved 2 packages in 3ms
   Building pyyaml==3.12
  × Failed to build `pyyaml==3.12`
  ├─▶ The build backend returned an error
  ╰─▶ Call to `setuptools.build_meta:__legacy__.build_wheel` failed (exit code: 1)
      ...
      raise PlatformError(
      distutils.compilers.errors.PlatformError: Microsoft Visual C++ 14.0
          raise SystemExit(f"error: {msg}")
      SystemExit: error: Microsoft Visual C++ 14.0 or greater ...
      PermissionError: [WinError 5] 拒绝访问。: '...\noobbaozi\spike\uvcache\builds-v0\.tmp8LxMcZ\...'
  help: `pyyaml` (v3.12) was included because `eos` (v0.0.0.dev8) depends on `pyyaml`
```

两件事要分清：

1. **真实阻塞**：`eos 0.0.0.dev8` 钉死 `PyYAML==3.12`（2015 年），Python 3.13 上没有轮子、必须本地编译 C 扩展、需要 MSVC 14.0 → **装不上**。
2. **环境副作用**：最后那条 `PermissionError: [WinError 5]` 是 uv 清理自己临时目录时撞上本机沙箱 ACL，**不是包的错**（同样的 `[WinError 5]` 也在 `spike/uvcache/builds-v0/.tmp*` 上复现，见 §7）。

### 2.2 源码：可用，且**不带 UI**

- `git clone https://github.com/pyfa-org/eos.git` → **失败**：
  `fatal: unable to access '...': schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS (0x8009030e)`
- 换 codeload tarball（Python urllib + 描述性 UA）→ 成功：`207,048 bytes`，598 个文件。
- 依赖事实（对 589 个 py 文件全树 grep）：**`PyQt` 0 命中、`wx.` 0 命中、`import yaml` / `from yaml` 0 命中、`numpy` 0 命中、`pandas` 0 命中**。
  → `PyYAML==3.12` 是**历史残留依赖**，`eos/` 包运行时根本不用它；PyQt 属 Pyfa 的 `gui/` 层。
  → **可以只取 `eos/` 子包，不需要 PyQt。**

一句话：**PyPI 装不上，源码能用，但得自己解决数据**。

### 2.3 数据依赖：eos 自己**不带 SDE**

`eos/data_handler/json_data_handler.py` 逐字要求：

```
fsd_built/{types,groups,dogmaattributes,typedogma,dogmaeffects,requiredskillsfortypes}.json
fsd_lite/{dbuffcollections,fighterabilitiesbytype}.json
phobos/metadata.json            # 只用来取 client_build 做缓存指纹
```

另一条路是 `SQLiteDataHandler(eve.db)`，表名 `evetypes/evegroups/dgmattribs/dgmtypeattribs/dgmeffects/dgmtypeeffects` + `phbmetadata`。

所以「是不是要额外下载 SDE」的答案是：**必须有 FSD 数据，但不必另行下载**——Pyfa 仓库自带 `staticdata/fsd_built/*.0.json`（分片，`types` 约 126 MB + `typedogma` 约 50 MB，合计约 180 MB）。我**没有**走这条路（本机到 `raw.githubusercontent.com` 的速率实测约 25 KB/s，且有中途断流，180 MB 不现实），而是：

> **直接拿项目本地已有的 SDE jsonl（`noobbaozi/data/eve-online-static-data-3607705-jsonl.zip`）实现一个等价 data_handler。**
> 可行的原因：eos 的 `ModInfoconverter` 吃的就是 **FSD 原生的 `modifierInfo` 格式**（`func`/`domain`/`operation`/`modifiedAttributeID`/`groupID`/`skillTypeID`），键名只差一层重命名（`_key → typeID/attributeID/effectID`、`effectCategoryID → effectCategory`）。

实测构建：**5.1 秒**，`types=9803 / attrs=1698 / effects=2988 / buffs=42`（Cleaner 只保留船/模块/弹药/无人机/舰载机/植入体/技能/子系统，故少于全量 5.3 万 type）。

> **踩到并在探针里修掉的两个坑（对将来复用很重要）**
> 1. `EveObjBuilder` 产出的对象必须**再走一次 `EffectFactory.make` / `TypeFactory.make`**（`JsonCacheHandler` 就是这么做的）——eos 的 `eve_obj/custom/*` 挂在工厂上做实例级修正，最典型的是 `online_effect_category.py`：CCP 数据里 `online` 效果(16) 的 category 是 `1=active`，eos 在工厂里改回 `4=online`。绕过工厂的表现是「所有模块 CPU/PG 恒为 0、online 类被动全不生效」。
> 2. 命令脉冲（command burst）样本会 `KeyError: 0.0`（`warfareBuffID` 在缩减数据集里没有对应 collection）→ 探针里退化为空模板并记录（命令脉冲只影响舰队成员，不影响本船数值）。

---

## 3. Q2 · pyfa 的数据模型里有没有「激活互斥」

### 3.1 有：三条 `maxGroup*` 规则，属 eos 的 restriction 体系

```
eos/const/eve.py:147-149      max_group_active = 763 / max_group_online = 978 / max_group_fitted = 1544
eos/const/eos.py:167-169      Restriction.max_group_fitted=21 / online=22 / active=23
eos/restriction/service.py:95-97   MaxGroupActiveRestrictionRegister / ...Online... / ...Fitted...
eos/restriction/restriction/max_group.py   ← 三个 register 的公共实现
```

### 3.2 但计数的 key 是 **type 的 group**，不是「激活组」

`eos/restriction/restriction/max_group.py`（逐字）：

```python
 70:        group_id = item._type.group_id                 # 计入「本 type 所在 group」
 96:            group_id = item._type.group_id
 97:            quantity = len(self.__group_item_map.get(group_id, ()))   # 同组件数
 98:            max_allowed_quantity = item.attrs[self._max_group_attr_id]  # 本件 maxGroup* 值
 99:            if quantity > max_allowed_quantity:
100:                tainted_items[item] = MaxGroupErrorData(...)
```

→ **纯同组互斥**。同一 `maxGroupActive=1` 但 group 不同的两件模块，各算各的 1 件，永远 `1 > 1 == False`。

### 3.3 数据面：SDE 里**根本没有跨组链接**

本地 SDE build 3607705 全量扫描（`maxGroup*` 三个属性的全部出现）：

| 属性 | 覆盖 type 数 | 覆盖 group 数 | group 46（推进） | group 1189（微型跳跃） |
|---|---|---|---|---|
| `maxGroupActive(763)` | 339 | 36 | **140 件，值 1.0** | **4 件，值 1.0** |
| `maxGroupOnline(978)` | 91 | 6 | 0 | 0 |
| `maxGroupFitted(1544)` | 514 | 137 | 0 | **4 件，值 1.0** |

两个原型件的全部 dogma：

```
12084 500MN Microwarpdrive II   group=  46 (Propulsion Module)
      763 maxGroupActive = 1.0 ；978 = 缺失 ；1544 = 缺失 ；canFitShipGroup01..03 = [None, None, None]
 4383 Large Micro Jump Drive    group=1189 (Micro Jump Drive)
      763 maxGroupActive = 1.0 ；978 = 缺失 ；1544 = 1.0 ；canFitShipGroup01..03 = [27.0, 898.0, 900.0]
```

**没有任何字段表达「A 组禁用 B 组」**。属性表里 `disallow*` 家族（`disallowActivation` 之类）也不存在；存在的只有 `disallowAssistance/OffensiveModifiers/EarlyDeactivation/RepeatingActivation/InEmpireSpace/...`、`activationRequiresActiveSiegeModule(5426)`、`activationRequiresActiveIndustrialCore(3265)` 等**与本问题无关**的条目（`requiresActive` 只出现在「激活 XXX 需要先开模块」这类场景）。

### 3.4 决定性证据：把两个配装真装进 eos 跑 `validate()`

探针 `q2` 步骤 [6] 实跑结果（同 `q2_result.json`）：

```
Panther：500MN MWD(46) + Large MJD(1189)   ← 跨组
   eos validate() → 违规种类：['skill_requirement(24)', 'state(27)']          ← 没有 max_group_active
Bifrost：1MN AB(46) + 5MN MWD(46)          ← 同组
   eos validate() → 违规种类：['max_group_active(23)', 'skill_requirement(24)', 'state(27)']
                    MaxGroupErrorData(group_id=46, quantity=2, max_allowed_quantity=1.0)
```

**对照本引擎（同一 SDE，13.6.0）**：

```
Panther：max_group 违规 0 条 → 无
Bifrost：max_group 违规 2 条 → {"type":"max_group","group_id":46,"limit":"active","used":2,"allowed":1}
```

### 3.5 Q2 结论

> **pyfa/eos 同样不建模跨组「激活互斥」。换 pyfa 解决不了这个问题。**
> 它能覆盖的是「同组超限」（group 46 内 AB+MWD），这一点与我们引擎**完全等价**、连违规数据结构都同构。
> 这条规则的缺口**只能由我们自己的规则层补**，因为 SDE 数据里没有可驱动的链接字段（详见 §5）。

---

## 4. Q3 · 数值对拍

### 4.1 口径（先说清，再比）

| 项 | 本引擎侧 | eos 侧 | 是否可比 |
|---|---|---|---|
| 技能档 | 显式传 `character.skills` | `fit.skills` | **两边统一：全部 512 个已发布技能 = 5 级**（= pyfa 默认，≠ 产品默认 `all4+min_required`） |
| 模块状态 | 沿用 killmail 还原出的 state | 同上（逐件同 state） | 可比 |
| 弹药 | killmail 还原的 charge | 同上 | 可比 |
| DPS | 逐件 `-12`（**单件值 × quantity**） | 逐件 `item.get_dps(reload=False)` | 可比（逐件对，不比总和） |
| 无人机 | 船级 `-14`（**含机库内未发射**） | `drone_filter`（**默认只算已发射**） | 需两边都「已发射」；探针另建一份 launched 配装专门对无人机 |
| 齐射伤 | 逐件 `-11`（单件值） | `stats.get_volley()` = **整匣弹药总伤** | **不可比**（口径不同，见 §4.4） |
| 分层 HP | `263/265/9` | `stats.hp.shield/armor/hull` | 可比 |
| 混合 EHP | `-43`（fit 环境剖面 25/25/25/25，`reactive_armor: do_not_adapt`） | `get_ehp(DmgProfile(25,25,25,25))`（**RAH 会自适应**） | 大体可比，RAH 配装会差 |
| 速度 / 信号 | `37` / `552` | `ship.attrs[37]` / `[552]` | 可比 |
| 对齐 | `-1` | eos **不直接给对齐时间**，按 `ln4 × mass × agility / 1e6` 换算 | 换算后可 |
| CPU/PG | 逐件 `49` / `15` 求和 | `stats.cpu.used` / `stats.powergrid.used`（**2 位四舍五入**） | 可比 |

样本：`noobbaozi/acceptance/fixtures/` 共 **65 条**（52 个 `*.json` killmail + 13 个 `*.eft`，各带 `*.expected.json`）。本次取按名排序的 **16 条**（`--limit 16`），覆盖：T3D / T3C / 后勤 / 指挥 / 战列 / HAC / 激光 / 弹道 / 导弹 HAC / 无人机 / 极限抗性 / 电容注射 / 辅助维修 / 吸电，**不经任何筛选**。

### 4.2 表 A · 逐件 DPS 对账（引擎 Σ(-12)×quantity vs eos 逐件）

```
fixture                   Σ(-12)     逐件一致 / 不一致
f10-t3d-confessor       175.9862     1 / 0
f11-t3d-svipul          403.4024     1 / 1   ← 唯一例外
      Δ Defender Launcher I   引擎=160.0000  eos=0.0000   -100%
f12-logistics-oneiros     0.0000     0 / 0
f13-logistics-scimitar   16.0875     1 / 0
f14-command-damnation   192.0956     1 / 0
f15-battleship-raven    758.8914     1 / 0
f16-hac-ishtar          122.4520     1 / 0
f17-laser-zealot        663.0900     2 / 0
f18-projectile-vagabond 561.4632     1 / 0
f19-missile-cerberus    423.3597     1 / 0
f20-drone-dominix        40.0000     1 / 0
f21-shield-sleipnir     776.8095     1 / 0
f22-armor-absolution      8.3333     1 / 0
f23-cap-booster         575.5012     1 / 0
f24-ancillary-rep       344.8932     5 / 0
f25-nos-neut            299.6394     2 / 0
```

**15/16 条样本的每一件伤害都逐位一致（相对差 < 1e-6）**。唯一不一致：

- **f11 的 `Defender Launcher I`：引擎算 160 DPS，eos 算 0。**
  拦截弹（Defender Missile）是反导武器，不产生伤害 → **引擎这一项是错的**（或至少是「不该进 DPS 的口径」）。这是我们产品里 `dps` 会偏高的一条真实来源，与 pyfa 无关，值得单独修。

> 顺带纠正一个容易踩的口径陷阱：`-12`（`damageDps`）**不是**「武器 DPS」的同义词。实测它在**非伤害模块**上也有值：远程护盾/装甲维修器（f13 一件 16.09）、辅助维修器、Defender 发射器。所以「引擎 DPS = Σ(-12)」这个写法本身没问题（这些模块的 `-12` 就是它们的「维修 DPS」），但**不能让「DPS」字段在读者眼里等于是武器伤害**。

### 4.3 表 B · 无人机 DPS（两边都「已发射」口径）

| fixture | 引擎 `-14` | eos `drone_filter` | 相对差 |
|---|---|---|---|
| f13-logistics-scimitar | 64.349998 | 64.35 | **3.67e-08** |
| f16-hac-ishtar | 489.808100 | 489.808117 | **3.67e-08** |
| f17-laser-zealot | 86.624997 | 86.625 | **3.12e-08** |
| f24-ancillary-rep | 435.476239 | 435.476250 | **2.63e-08** |
| f25-nos-neut | 120.656246 | 120.656250 | **3.67e-08** |
| 其余 11 条 | 0 | 0 | — |

**无人机伤害公式两边完全一致**，差异只剩浮点尾数。

> 口径提醒（这条是产品语义，不是数值错误）：引擎的 `-14` **包含机库内未发射的无人机**，eos 默认只算**已发射**的。同一配装两边会差一个数量级——这不是谁算错，是「能不能打」的定义不同，报数时必须写明。

### 4.4 表 C · 生存性 / 机动 / 资源（16 条）

| fixture | HP(盾/甲/结构) | EHP 引擎 | EHP eos | Δ% | 速度引擎 | 速度 eos | 信号 引擎 | 信号 eos | 对齐 引擎 | 对齐 eos* | CPU 引擎/eos | PG 引擎/eos |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| f10-t3d-confessor | 750/1000/937.5 | 6460.49 | 6460.49 | 0.000 | 1389.59 | 1389.59 | 65.00 | 65.00 | 19.089 | 19.089 | 207.75/207.75 | 109.80/109.80 |
| f11-t3d-svipul | 3612.1/875/687.5 | 12252.95 | 12252.95 | 0.000 | 2019.45 | 2019.45 | 437.54 | 437.54 | 6.756 | 6.756 | 256.00/256.00 | 82.10/82.10 |
| f12-logistics-oneiros | 1600/8197.5/2856.2 | 45568.92 | 45568.92 | −0.000 | **411642.27** | **2935.11** | 357.50 | 357.50 | 12.468 | 12.468 | 462.50/462.50 | 1818.00/1818.00 |
| f13-logistics-scimitar | 14832/2101.2/1792.5 | 66623.91 | 66623.92 | 0.000 | 2068.44 | 2068.44 | 442.71 | 442.71 | 8.796 | 8.796 | 410.50/410.50 | 1109.50/1109.50 |
| f14-command-damnation | 4702.5/38622.1/5500 | **271214.89** | **290526.96** | **+7.121** | 1048.24 | 1048.24 | 1590.00 | 1590.00 | 14.803 | 14.803 | 547.00/547.00 | 1595.00/1595.00 |
| f15-battleship-raven | 9625/7975/8800 | 69632.59 | 69632.59 | 0.000 | 141.25 | 141.25 | **1115.62** | **462.02** | 11.150 | 11.150 | 937.50/937.50 | 10094.00/10094.00 |
| f16-hac-ishtar | 1750/2000/2875 | 15314.38 | 15314.38 | 0.000 | 218.75 | 218.75 | 152.25 | 152.25 | 5.604 | 5.604 | 400.50/400.50 | 777.00/777.00 |
| f17-laser-zealot | 1225/5812.5/1670 | 72692.18 | 72692.18 | −0.000 | 1768.48 | 1768.48 | 421.88 | 421.88 | 8.091 | 8.091 | 381.75/381.75 | 1695.80/1695.80 |
| f18-projectile-vagabond | 5500/1750/980 | 83813.59 | 83813.59 | 0.000 | 974.22 | 974.22 | 153.39 | 153.39 | 6.592 | 6.592 | 463.50/463.50 | 1169.00/1169.00 |
| f19-missile-cerberus | 2500/1500/1750 | 21950.25 | 21950.25 | 0.000 | 667.83 | 667.83 | 135.00 | 135.00 | 7.677 | 7.677 | 612.50/612.50 | 642.00/642.00 |
| f20-drone-dominix | 9900/11000/11687.5 | 47395.50 | 47395.50 | 0.000 | 136.25 | 136.25 | 465.00 | 465.00 | 11.764 | 11.764 | 501.75/501.75 | 3787.00/3787.00 |
| f21-shield-sleipnir | 5625/4500/4625 | 39046.30 | 39046.30 | 0.000 | 499.55 | 499.55 | 252.00 | 252.00 | 11.726 | 11.726 | 474.75/474.75 | 1238.65/1238.65 |
| f22-armor-absolution | 3625/18673.5/4600 | 207286.71 | 207286.71 | 0.000 | 1275.89 | 1275.89 | 1457.50 | 1457.50 | 11.358 | 11.358 | 624.00/624.00 | 1564.00/1564.00 |
| f23-cap-booster | 11000/10312.5/9625 | 72911.67 | 72911.67 | 0.000 | 150.00 | 150.00 | 438.25 | 438.25 | 10.761 | 10.761 | 437.50/437.50 | 16524.00/16524.00 |
| f24-ancillary-rep | 3750/19581.3/5000 | **113519.96** | **117546.45** | **+3.547** | 1093.80 | 1093.80 | 1485.00 | 1485.00 | 13.723 | 13.723 | 514.00/514.00 | 1349.20/1349.20 |
| f25-nos-neut | 2062.5/3750/3750 | **25382.40** | **25949.68** | **+2.235** | 1953.35 | 1953.35 | 715.00 | 715.00 | 7.015 | 7.015 | 389.75/389.75 | 974.97/974.97 |

\* 对齐 eos 侧 = `ln4 × mass × agility / 1e6`（eos 只给 `70 agility` 和 `4 mass`），换算后 **16/16 逐位一致**。

**逐项结论**

| 指标 | 一致条数 | 偏差与根因 |
|---|---|---|
| 分层 HP（盾/甲/结构） | **16/16 逐位一致** | 无偏差 |
| 混合 EHP | **13/16** | f14 +7.121% / f24 +3.547% / f25 +2.235%：三条**都带 `Reactive Armor Hardener`**。eos 有 RAH 自适应模拟器（`eos/sim/reactive_armor_hardener.py`）会动态调抗性 → EHP 更高；我们的口径是 `environment.reactive_armor = do_not_adapt`。**不是算错，是口径不同**，但要在文档里写明（否则用户拿 pyfa 数一对就觉得我们低了 3–7%）。 |
| 速度 | **15/16** | **f12 Oneiros：引擎 411,642.27 m/s vs eos 2,935.11 m/s（差 140 倍）**。根因已定位：该舰同时装 `50MN Quad LiF Restrained MWD` + `10MN Afterburner II`，**两件在 killmail 里都是 active**，引擎把两个 `speedFactor` 叠乘（≈640×）。这与 PROGRESS §235 的 f67（AB+MWD 双开，59,928 m/s）是**同一个根因**，只是这次量级更大。**这是引擎的真实错误，不是口径。** |
| 信号半径 | **15/16** | **f15 Raven：引擎 1,115.62 vs eos 462.02**。该舰装 `Large Micro Jump Drive`（`signatureRadiusBonusPercent=150`）。引擎把 MJD **起跳 spool 期间**的 +150% 信号罚当成**常驻**；eos 只在生效瞬间算。**引擎侧应为状态瞬时值，不该进静态 `signature_radius`。** |
| 对齐时间 | **16/16**（换算后） | 无偏差；f12 的双推进 bug 不污染 mass/agility，所以对齐仍然对 |
| CPU / PG 用量 | **16/16** | eos 两位四舍五入（f25 PG `974.96725` vs `974.97`），输出端 ≤0.001% 差异 |
| 齐射伤（volley） | **不可比** | eos `get_volley()` 是「整匣弹药总伤」口径，f10 = 515.28 而我们一次齐射 175.99，**比值随配装变化**（f22 是 9×、f11 是 4.34×）。**必须先声明口径再比，本次不比。** |

### 4.5 Q3 结论：**没有系统性数值偏差，但引擎有 2 处会「放大错误」的状态语义缺陷**

- **数值核心（dogma 求解）两条引擎高度一致**：同样的 SDE、同样的技能档、同样的状态，逐件 DPS 相对差 < 1e-6、无人机 < 4e-8、HP 逐位相等、CPU/PG 逐位相等。→ **不存在「引擎算得不如 pyfa」这回事**，也不存在「需要换引擎才能算对」。
- **所有分歧都能定位到三类，且都不在数值核心**：
  1. **口径不同**（RAH 自适应、无人机是否已发射、volley 定义）——说清就行；
  2. **引擎的状态语义缺陷**（双推进叠乘 640×、MJD 信号罚常驻）——这是**我们该修的 bug**，pyfa 在这两处是对的；
  3. **引擎多算了一项**（Defender 发射器 160 DPS）——也是我们该修的。

---

## 5. 建议

### 5.1 **不换引擎**（换 = 花 20+ 个任务的代价，去买一个我们已经有的能力）

三条硬理由：

1. **pyfa 修不了我们的痛点**（§3.4 已实跑证明）：跨组激活互斥那条规则，eos 的 `validate()` 同样不报。换过去只是把 `hard_violations: 0` 换个库名报一遍。
2. **数值核心等价**（§4）：15/16 逐件一致到 1e-6，无人机到 4e-8，HP 逐位相等。换引擎不会让数更准。
3. **eos 的工程风险更高**：PyPI 已死 8 年、CI 还钉 Python 3.6、`PyYAML==3.12` 这种僵尸依赖、无版本三元组、无属性来源（provenance）概念、LGPL-3.0 vendoring 才牢靠。**我们现在能 `pip install` 到一个 13.6.0 版本化引擎，这是资产不是负债。**

### 5.2 空洞该由**哪一层**补：规则层（新增），不是引擎层

分三步，全部在 `project/`（规则层）与 `config/`（数据层）里做，不动 `engine/adapter.py`：

1. **数据层**：`config/` 新增一张**人工规则表**（不是从 SDE 生成），例如
   `config/activation_groups.json`：`{"propulsion": {"members": ["group:46", "group:1189", "group:1533"], "limit": "active=1", "source": "CCP 客户端行为 / 玩家实测", "verified_by": "...", "verified_at": "..."}}`
   —— 每个条目**必须带来源与核实日期**（红线：不编造）。它跟 `config/attributes.py` 里的常量表是同一类资产：**「知道但数据里没有」的知识，显式登记且可审计**。
2. **规则层**：`project/constraints.py` 之上新增 `state_exclusive` 规则族，把现在的
   - 引擎原生 `max_group`（同组，可直接透传）
   - 人工表的跨组互斥（`propulsion` ↔ `MJFG` 等）
   统一成**一个输出形状**：`{type: "state_exclusive", members: [...], limit: "active", used, allowed, source}`。
   **术语必须是「状态互斥」，绝不能用「非法」**（PROGRESS §240 主人已裁决）。
3. **视图层**：既然同一条「同一时刻只能开一个」也解释了双推进速度叠乘，`propulsion_states` 必须给出**单推进分支**（只开 AB / 只开 MWD），并且顶层 `speed_mps` 的 `assumptions` 里显式标注「该值是全模块 active 的**不可达状态**」。这样 f12 那个 411,642 m/s 就不会再被当成可信数。

### 5.3 双引擎对拍：**暂不引入常驻双引擎，但保留探针**

`spike/pyfa_probe.py` 已经能在 5 秒内起一个可用的 eos，成本极低。建议把它作为**离线对拍工具**保留（不进 CI、不进产品依赖），用途只有一个：**当我们怀疑引擎某个数值时，用第二实现复核**。这正是 PROGRESS §188「可疑的零值要用闭式复算或对照实验证伪」的方法论延伸。

---

## 6. 代价估算：若真的迁移 pyfa

### 6.1 现有资产清单（迁移要重建/重验的对象）

| 资产 | 规模 | 迁移后状态 |
|---|---|---|
| 引擎适配层 `engine/` | 928 行 / 5 文件 | **全废**（`adapter/normalize/assumptions/scenarios` 都是围绕 `esf_dogma_engine` 的 API 写的） |
| SDE 层 `sde/` | 985 行 / 5 文件 | **半废**：sqlite 索引可留，但 eos 要的是 Phobos json 或 `eve.db`，两套数据结构 |
| 常量与单位 `config/` | 540 行 / 147 个 `ATTR_` 常量 / 11 项缩放 | **可留**（属性 ID 是 CCP 的，eos 也用同一套） |
| 六视角投影 `project/views/` | 2,804 行 / 10 文件（base/constraints/firepower/mobility/resources/shared/stability/survivability/loader/__init__） | **全部重写取数路径**（eos 的对象模型、stats 访问点、容器 API 与引擎完全不同） |
| 渲染与诊断 `render/summary/diagnostics/provenance/reasons/confidence/units` | 3,678 行 | **大部分可留**（纯展示层），但 `provenance`/`reasons` 的**语义输入**要重建 |
| 三态 provenance 模型 | 417 行 + 65 样本重推（T-14/T-27/T-30 三个阶段） | **重建**：eos 完全没有「属性来源/可信度」概念，这条要整个从零再推一遍 |
| 65 条验收基线 | 65 个 `expected.json`（65 样本 × 44 字段 = 2,860 断言） | **全部重算 + 重新定口径** |
| 分层输出（L0/L1）契约 | `project/summary.py` 1,095 行 + `INDEX_SPEC` + 预算护栏（2,500 字符） | **重建**（字段来源全变） |
| 快照回归 | `acceptance/out/snapshot-check.json` + `check_snapshot.py`（当前 0 DIFF） | **重建**（新引擎 = 新基线，旧的 0 DIFF 失去意义） |
| 测试 | 9,215 行 / 13 文件 / **691 passed** | 至少 60% 要改（大量断言绑定引擎输出形状） |
| 工具链 | 2,158 行 / 4 文件（常量覆盖守护 / 回归四门 / 属性审计） | 常量守护可留；回归四门要重接 |
| 性能 | 引擎单次 0.33 ms + LRU 256 | eos 构建 5.1 s/进程（需常驻缓存，官方是 bz2 落盘），单次计算未实测但对象模型更重 |
| 许可与维护 | 商业可用引擎（版本化发布） | eos 是 **LGPL-3.0**，只能 vendoring/fork；上游 pip 已死 8 年、CI 是 Python 3.6 |

### 6.2 工作量粗估

| 项 | 粗估 |
|---|---|
| 重新接线引擎（数据源 + 对象构建 + 缓存 + 版本三元组） | 5–8 个任务 |
| 六视角取数路径重写 + 口径对齐 | 10–15 个任务（约等于 T-20…T-41 里视角相关的全部工作） |
| provenance 三态模型从零重推（65 样本 × 3 轴） | 6–10 个任务 |
| 65 基线重算 + 分层契约 + 快照重建 | 6–8 个任务 |
| 测试与验收体系迁移 | 6–10 个任务 |
| **合计** | **约 33–51 个任务**（对照：整个 P0→P2 期从 T-01 到 T-41 是 41 个任务） |

**收益**：eos 的 `Restriction` 枚举共 **24 条**（`cpu/powergrid/calibration/dronebay_volume/drone_bandwidth/launched_drone/drone_group/high_slot/mid_slot/low_slot/rig_slot/rig_size/subsystem_slot/subsystem_index/turret_slot/launcher_slot/implant_index/booster_index/ship_type_group/capital_item/max_group_fitted/max_group_online/max_group_active/skill_requirement`）。这些我们**引擎原生已经覆盖**了等价信息（`resource`(cpu/powergrid/calibration)、`skill`、`max_group`、`rig_size`、`capital_item`、槽位数量），其余靠 `project/constraints.py` 自己算；而**我们真正缺的那一条（跨组激活互斥）它也没有**。

> 一句话代价对比：**迁移 ≈ 重建一遍 P0→P2；收益 ≈ 0 条我们缺的规则。**

---

## 7. 未完成 / 边界 / 环境副作用

**未完成（诚实记录）**

1. **只跑了 16/65 条样本**（任务要求 10–20，已达标），但**战斗机/航母类未覆盖**：`fighterAbilitiesByType` + `FighterSquad` 需要额外映射，f26/f44 等航母样本没进对拍。
2. **volley（齐射伤）没有做等价换算**：eos 是「整匣总伤」口径，我们是一次齐射；只报了口径差异，没有拿不同口径的数直接下结论。
3. **命令脉冲（command burst）的自船数值未验证**：缩减数据集里缺 4 个 `warfareBuffID` 的 collection，探针退化为空模板。命令脉冲只影响舰队成员，不影响本船生存/机动/资源，对本次结论无影响。
4. **eos 的 RAH 自适应只在 3 条样本上表现出来**（f14/f24/f25），没有做「关掉 RAH 模拟」的对照实验来给出精确差值。
5. **`maxGroupOnline(978)` 这 91 个 type / 6 个 group 没有做行为验证**（只在数据层统计了覆盖）。

**环境副作用（必须说明，不是我改了什么东西）**

- `spike/pyfa_scratch/eos-master/`：第一次用 `tarfile.extractall()` 解 tarball 时，解到第一个文件就 `PermissionError`，随后该目录变成**不可读也不可删**（`Get-Acl` 都抛 `UnauthorizedAccessException`）。后续改成手工 `makedirs + open` 解到 `eos_src/` 就正常了。
- `spike/uvcache/builds-v0/.tmp*/`：uv 构建 PyYAML 失败后留下的 3–4 个临时目录同样被锁成不可读。
- 这些目录**都是垃圾**，不影响任何功能；因为本会话的提权审批被禁用（`approval prompts are disabled`），我没有尝试 `workspace-write`/`danger-full-access` 去修 ACL——按规则如实上报，不硬绕。
- **写入范围自查**：本任务只新建了 `noobbaozi/spike/pyfa_probe.py`、`noobbaozi/spike/pyfa_report.md`，以及 `noobbaozi/spike/pyfa_scratch/`（源码、venv、日志、结果 JSON）。**未修改**任何产品代码 / 测试 / 文档 / acceptance / PROGRESS.md。
