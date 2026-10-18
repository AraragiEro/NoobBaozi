# zkb 离线样本（`tests/fixtures/zkb/`）

这三份都是**真实响应**（2026-10-19 用 python `urllib` 直连抓取），只做了一种处理：
**删字段**，不造字段。

| 文件 | 来源请求 | 原始条数 | 存了几条 |
|---|---|---|---|
| `losses-shipTypeID-22444-pastSeconds-604800.json` | `GET https://zkillboard.com/api/losses/shipTypeID/22444/pastSeconds/604800/` | 170 | 12 |
| `kills-characterID-2121910483-pastSeconds-86400.json` | `GET https://zkillboard.com/api/kills/characterID/2121910483/pastSeconds/86400/` | 9 | 6 |
| `esi-ids-sleipnir-hecate.json` | `POST https://esi.evetech.net/latest/universe/ids/`（请求体 `["Sleipnir","Hecate"]`） | 4 行 | 全部 |

## 删掉了哪些字段（以及为什么）

- `attackers`：本工具不返回参战方（那是一次一条的活，见 `fit_killmail`）；
- `victim.items` 与 `victim.position`：**列表里确实带「被弹出的模块」**（实测存在），
  但 `zkb_search` 的契约里没有它，留着只会让样本从 1.4 MB 变成仓库负担。

保留下来的就是 `zkb_search` 真正读的字段：`killmail_id` / `killmail_time` /
`solar_system_id` / `victim.{character,corporation,alliance}_id` /
`victim.ship_type_id` / `zkb.{totalValue,labels,npc,solo,…}`。

## 用途

- `tests/test_api.py`：URL 拼装、`side=both` 合并去重、`limit` 只截断数组、
  聚合口径、限流（虚拟时钟）、名字解析失败；
- `tests/test_mcp_server.py`：`tools/call zkb_search` 的进程内往返。

样本里 **Sleipnir 这一条**（`killmail_id = 139265742`，`value = 397729064.05`）同时被
`esi-ids` 样本覆盖：`/universe/ids/` 对 `"Sleipnir"` 会同时返回一个**同名角色**
（282567669）与**船型**（22444）—— 所以解析必须按分类取，测试钉住「拿到的是 22444」。
