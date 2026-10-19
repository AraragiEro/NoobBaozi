# zkb 离线样本（`tests/fixtures/zkb/`）

这四份都是**真实响应**（用 python `urllib` 直连抓取；本机 `curl` / PowerShell 的 TLS
不可用，只有 urllib 能出网），只做了一种处理：**删字段**，不造字段。

| 文件 | 来源请求 | 原始条数 | 存了几条 |
|---|---|---|---|
| `losses-shipTypeID-22444-pastSeconds-604800.json` | `GET https://zkillboard.com/api/losses/shipTypeID/22444/pastSeconds/604800/` | 170 | 12 |
| `kills-characterID-2121910483-pastSeconds-86400.json` | `GET https://zkillboard.com/api/kills/characterID/2121910483/pastSeconds/86400/` | 9 | 6 |
| `esi-ids-sleipnir-hecate.json` | `POST https://esi.evetech.net/latest/universe/ids/`（请求体 `["Sleipnir","Hecate"]`） | 4 行 | 全部 |
| `esi-universe-names.json` | `POST https://esi.evetech.net/latest/universe/names/`（请求体 = 152 个 id） | 152 行 | 全部（T-52） |

## 删掉了哪些字段（以及为什么）

- `attackers`：本工具不返回参战方（那是一次一条的活，见 `fit_killmail`）；
- `victim.items` 与 `victim.position`：**列表里确实带「被弹出的模块」**（实测存在），
  但 `zkb_search` 的契约里没有它，留着只会让样本从 1.4 MB 变成仓库负担。

保留下来的就是 `zkb_search` 真正读的字段：`killmail_id` / `killmail_time` /
`solar_system_id` / `victim.{character,corporation,alliance}_id` /
`victim.ship_type_id` / `zkb.{totalValue,labels,npc,solo,…}`。

`esi-universe-names.json` 里**没有删任何一条**：那 152 个 id 正好是两份战报样本要的
（`139239127` 的星系 + 受害者 + 79 名攻击者，加列表 12 行的受害者身份），删了就等于
造出「这条 id 查不到名字」的假现场。

## 用途

- `tests/test_api.py`：URL 拼装、`side=both` 合并去重、`limit` 只截断数组、
  聚合口径、限流（虚拟时钟）、名字解析失败；T-52 的**身份名字**（批量 / 缓存 / 缺项）；
- `tests/test_mcp_server.py`：`tools/call zkb_search` / `tools/call fit_killmail` 的进程内往返；
- `tests/test_cli.py`：`killmail` / `search` 的人话输出里**出现人名**。

样本里 **Sleipnir 这一条**（`killmail_id = 139265742`，`value = 397729064.05`）同时被
`esi-ids` 样本覆盖：`/universe/ids/` 对 `"Sleipnir"` 会同时返回一个**同名角色**
（282567669）与**船型**（22444）—— 所以解析必须按分类取，测试钉住「拿到的是 22444」。

## T-52 的真实名字（`esi-universe-names.json` 里的关键三条）

| id | 类别 | name |
|---|---|---|
| 2123771141 | character | `Warlock Industries Event`（139239127 的受害者） |
| 94814255 | character | `Ignacious Deviant`（最终一击：Hexa 2500mm Repeating Cannon II） |
| 2121910483 | character | `Woof1977`（伤害第一：1,086,009，占 16.6%） |

测试直接断言这三个字符串 —— 名字写错、解析漏批、返回空字典，都会当场变红。

---

# `zkb_stats` 样本（3 份，2026-10-19 抓取）

给「实体画像 / 威胁度」工具（`zkb_stats`，第 8 个工具）用的离线样本。同样是 python `urllib`
直连 zkb 抓的**真实响应**，**一个字段都没删**（三份都远小于 2 MB 的门槛，没有裁剪理由）。

| 文件 | 来源请求 | 顶层键 | 体积 |
|---|---|---|---|
| `stats-character-2117515414.json` | `GET https://zkillboard.com/api/stats/characterID/2117515414/` | 59 | 48,666 B |
| `stats-corporation-98821127.json` | `GET https://zkillboard.com/api/stats/corporationID/98821127/` | 48 | 64,835 B |
| `stats-alliance-1354830081.json` | `GET https://zkillboard.com/api/stats/allianceID/1354830081/` | 49 | 167,789 B |

三个实体：`SixtyEverSnake`（角色）、`The big boys`（公司，ticker `SE4E`）、
`Goonswarm Federation`（联盟，ticker `CONDI`，id 由 ESI `/universe/ids/` 解析得到）。

## 为什么三份的顶层键数不一样

zkb 会**省略值为空/0 的整体块**，所以键集合天然不同，**不能**按「必须有 59 个键」来断言：

- `associates` / `recentShips` / `topShips` / `allTimeSum` / `calcTrophies` 只有角色才有；
- `hasSupers` / `supers` / `updatingSupers` / `calcAlltime` 只有联盟才有；
- `allTimeSum` 在公司样本里等于 `shipsDestroyed`（734），在角色样本里等于
  `topAllTime[0].data[0].kills`（816），在联盟样本里**整个键都不存在**。

## 抓取时必须知道的两件事（都实测过）

1. **URL 结尾的 `/` 不能省。** 省了返回 `200` + **空响应体**（0 字节），不是报错；
2. **错误体也是 `200`。** id 不存在或类型错配时，HTTP 状态码仍是 `200`，正文是
   `{"error":"Invalid type or id"}`（31 字节）。所以**不能拿状态码判成败**，必须看正文。

字段实测表、`dangerRatio` 比对结论、返回形状草案见
`noobbaozi/.tmp/zkb_stats_contract.md`（临时区，不入库）。

