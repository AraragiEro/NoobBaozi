# noobbaozi MCP · Docker 容器化（HTTP 传输 · 局域网访问）

把 `noobbaozi/mcp_server.py` 的**工具集**（`TOOL_NAMES`，含 `fit_*` / `zkb_*`）包成一个 **HTTP MCP 服务**，跑在容器里，局域网内其他机器
（或别人的 agent）可以用 `http://<宿主机IP>:8080/mcp` 接上来。

```
POST http://<宿主机IP>:8080/mcp      # MCP 端点（JSON-RPC 2.0，streamable HTTP）
GET  http://<宿主机IP>:8080/health   # 健康检查 → {"ok":true,"server":"noobbaozi","version":"1.0.0","transport":"http","tools":5,...}
```

容器里跑的入口命令（与 T-47 的 HTTP 传输约定一致，**不要自己另改**）：

```sh
python -m noobbaozi.mcp_server --transport http --host 0.0.0.0 --port 8080
```

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 镜像定义（python:3.13-slim、非 root、不含 `data/`、HEALTHCHECK 打 `/health`） |
| `entrypoint.sh` | 容器入口：预检数据卷 → 需要时建库/下载 → `exec` 服务 |
| `docker-compose.yml` | 一条命令构建 + 起容器 + 卷 + 端口 + 健康检查 |
| `.dockerignore` | 构建上下文排除清单（**注意位置陷阱，见 §10.1**） |
| `run-native.ps1` | 没有 Docker 时，本机原生起同一个 HTTP 服务的等价脚本 |

> ⚠️ **本机（开发机）没有安装 Docker**，所以 **镜像从未被构建过、容器从未被运行过**。
> 哪些验证做了、哪些没做，见 §8「诚实声明」。读这份文档时请把这一点放在心上。

---

## 1. 前置条件

1. **Docker**（含 Compose V2，即 `docker compose` 子命令；`docker-compose` 老二进制不保证可用）
   ```powershell
   docker --version
   docker compose version
   ```
2. **SDE 数据**（二选一，镜像里不带数据）：
   - **推荐**：宿主机 `noobbaozi/data/` 里已经有索引库或 SDE zip
     （`eve-online-static-data-<build>-jsonl.zip` ≈ 95 MB 或 `noobbaozi-<build>.sqlite3` ≈ 14 MB）；
     compose 会把 `../data` 挂进容器。
   - 或者：首次启动时设 `NOOBBAOZI_SDE_FETCH=1`，让容器自己下载官方 SDE 并现场建库（约 95 MB 出网）。
3. **网络**：HTTP 无鉴权，**默认只该在可信局域网里用**（见 §7）。

---

## 2. 构建与启动

在**工作区根目录**执行（`-f` 用的是相对路径，跟你在哪个目录无关）：

```powershell
# 构建 + 后台启动
docker compose -f noobbaozi/docker/docker-compose.yml up -d --build

# 看状态（STATUS 里会显示 healthy / unhealthy）
docker compose -f noobbaozi/docker/docker-compose.yml ps

# 跟日志（entrypoint 的日志全部走 stderr，所以这里看得到）
docker compose -f noobbaozi/docker/docker-compose.yml logs -f

# 停止（不删数据卷）
docker compose -f noobbaozi/docker/docker-compose.yml down
```

只想构建镜像、不用 compose：

```powershell
# 注意：构建上下文必须是 noobbaozi/（不是 noobbaozi/docker/），Dockerfile 里的 COPY 路径相对它
docker build -t noobbaozi-mcp:1.0.0 -f noobbaozi/docker/Dockerfile noobbaozi/
```

本机自检（在宿主机上）：

```powershell
curl.exe http://127.0.0.1:8080/health
# 期望：{"ok":true,"server":"noobbaozi","version":"1.0.0","transport":"http","tools":5,...}
```

> `/health` 与 `POST /mcp` 由 T-47 的 HTTP 传输实现。**截至本文写作时，该实现已在工作区（但未提交）**，
> 本机原生实测通过（见 §8）。如果你在更早的提交上构建，`mcp_server.py` 还是「只有 stdio」的版本：
> 那时容器会正常启动，但 `/health` 不存在、`docker compose ps` 里 STATUS 显示 `unhealthy` —— 那不代表服务没起来，见 §9.2。

---

## 3. 从局域网另一台机器验证

先查宿主机 IP（Windows）：

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | Select-Object IPAddress,InterfaceAlias
```

然后**在另一台机器**上：

```bash
# 健康检查
curl http://192.168.5.6:8080/health

# MCP 端点探活（不带 initialize，返回 JSON-RPC 错误也算通了 —— 说明 HTTP 层活着）
curl -i -X POST http://192.168.5.6:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

连不上时按顺序查：① `docker compose ps` 的端口映射是不是 `0.0.0.0:8080->8080/tcp`；
② 宿主机防火墙入站规则（§9.4）；③ 两台机器是不是同一个网段/VLAN；④ 宿主机上 `curl 127.0.0.1:8080/health` 通不通。

---

## 4. MCP 客户端配置（HTTP 传输，**不是** stdio）

`noobbaozi/mcp/README.md` 里的 `"command": "uv"` 那份配置是 **stdio** 的，容器化之后要换成 URL 形式。
通用 `mcpServers` 写法：

```json
{
  "mcpServers": {
    "noobbaozi": {
      "type": "http",
      "url": "http://192.168.5.6:8080/mcp",
      "headers": {
        "Accept": "application/json, text/event-stream"
      }
    }
  }
}
```

要点：

| 项 | 说明 |
|---|---|
| `type` | 写 `"http"`；个别客户端用 `"streamable-http"` 或 `"sse"`，以客户端文档为准 |
| `url` | 一定是 `.../mcp`，**不是**根路径、也不是 `/health` |
| `headers.Accept` | 部分客户端默认不带 `text/event-stream`，服务端会 406。带上最保险 |
| 认证 | 无。不要往 `headers` 里塞 token —— 服务端根本不看（见 §7） |
| 替代写法 | Claude Desktop 系可用 `"url"` 字段直接给 URL，效果相同；本仓库没在客户端上实测过（§8） |

连上之后的调用约定与 stdio 完全一致：先 `tools/list`（条数 = `/health` 的 `tools`，
两处同源、不抄数字），再按
`fit_parse → fit_index → fit_view / fit_get → fit_detail → fit_explain / fit_sensitivity`
的顺序调。
工具语义、错误码、返回信封见 `noobbaozi/mcp/README.md` §3–§4。

---

## 5. 数据卷（为什么数据不进镜像）

镜像里**只有代码**；`noobbaozi/data/` 通过卷挂载进来。

**为什么**：

- 体积：`eve-online-static-data-*.zip`（94.7 MB）+ `noobbaozi-*.sqlite3`（14.3 MB）。烤进镜像会让每次代码改动都重推 ~110 MB。
- 可变性：SDE 每出一次新 build 就要换一份数据，数据不该跟代码版本绑死。
- 复用：宿主机上已经有建好的索引库，容器直接用，不必重复建、也不必（在内网里）重复下载。
- 权限/审计：数据留在宿主机，删容器不删数据。

**怎么挂**：compose 里是 `../data:/app/noobbaozi/data`（相对 compose 文件所在目录，即 `noobbaozi/data`）。
**读写**，不是 `:ro` —— 索引库要能写：首次建库、以及 SDE 升 build 时新建 `noobbaozi-<新build>.sqlite3`
都会写进这个目录。

**怎么更新 SDE**：

```powershell
# 1) 停容器（避免边写边读）
docker compose -f noobbaozi/docker/docker-compose.yml down

# 2) 在宿主机上把新 zip 放进 data/，然后建库（容器外跑也一样）
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.sde.ingest
# 或者完全交给容器：启动时不带库、带 NOOBBAOZI_SDE_FETCH=1，entrypoint 会下载最新 SDE 并建库

# 3) 再起容器
docker compose -f noobbaozi/docker/docker-compose.yml up -d
```

索引库文件名带 build 号（`noobbaozi-<build>.sqlite3`），**多 build 并存、可回滚**；服务永远用 build 号最大的那个，
所以升级是「多一个文件」而不是「覆盖旧文件」。回滚 = 删掉新库文件。

**Linux 宿主机的 uid 注意**：绑定的卷继承宿主机文件属主。容器内进程是 `noobbaozi`（uid 1000）。
如果宿主机 `noobbaozi/data/` 的属主不是 1000，**读**没问题（世界可读），但**建库会写失败**。修法二选一：

```bash
sudo chown -R 1000:1000 noobbaozi/data          # 让 data/ 归容器用户
# 或在 compose 里改用命名卷 + 手动拷数据
```

（Windows / Docker Desktop 的用户不用担心这个，它的 bind mount 会做 uid 映射。）

---

## 6. 环境变量

| 变量 | 默认 | 谁在读 | 说明 |
|---|---|---|---|
| `NOOBBAOZI_HOST` | `0.0.0.0` | `entrypoint.sh` | 传给 `--host`。写 `127.0.0.1` 就只给容器自己用，容器外连不上 |
| `NOOBBAOZI_PORT` | `8080` | `entrypoint.sh` + healthcheck | 传给 `--port`；改了要同时改 compose 的端口映射和 healthcheck |
| `NOOBBAOZI_DATA_DIR` | `/app/noobbaozi/data` | `entrypoint.sh` only | **只影响 entrypoint 去哪里找库/zip**，见 §10.2 |
| `NOOBBAOZI_DRY_RUN` | `0` | `entrypoint.sh` | `1` = 只打印将要执行的命令、不启动服务、退出 0 |
| `NOOBBAOZI_SDE_FETCH` | `0` | `entrypoint.sh` | `1` = 没有本地数据时允许联网下载官方 SDE |
| `PYTHONIOENCODING` | `utf-8` | Python | 中文日志不乱码 |
| `PYTHONUNBUFFERED` | `1` | Python | `docker logs` 立刻可见 |

> ⚠️ **`--host` 的服务端默认值是 `127.0.0.1`（只监听回环）**，不是 `0.0.0.0`。
> 容器里必须显式传 `--host 0.0.0.0` —— 本套配置的 `NOOBBAOZI_HOST=0.0.0.0` 就是干这个的。
> 把 `NOOBBAOZI_HOST` 改成 `127.0.0.1`，容器内服务只接回环，宿主机端口映射照常「监听」但**局域网必然连不上**。
> 另外 `NOOBBAOZI_PORT` 同时被 healthcheck 读取，改端口记得三处一起改（compose `ports`、`NOOBBAOZI_PORT`、反代配置）。

---

## 7. 安全提示（请务必读完）

- **HTTP 传输没有鉴权、没有 TLS。** 任何能连到 `8080` 的人都能调**全部**工具（就是 `tools/list` 那份）。
- **只在可信局域网内使用。** 不要把 `8080` 直接发布到公网（不要 DMZ、不要路由器端口映射、不要 `0.0.0.0` + 公网 IP）。
- 想缩小暴露面：compose 里把端口改成 `"127.0.0.1:8080:8080"` 只给宿主机；需要跨机时前面挂一层反代。
- **对外暴露必须加反向代理 + 认证**，例如 nginx/Caddy 前置 TLS + Basic Auth / OAuth / mTLS，并且：
  - 反代要转发 `Accept`、`Content-Type`，并**关闭对 SSE 的缓冲**（`proxy_buffering off`），否则 streamable HTTP 会被憋住；
  - 反代要设合理的读超时（≥ 60s，工具调用可能慢）；
  - 只允许 `/mcp`（以及内网探针用的 `/health`），别整站透传。
- 工具本身不吃凭据、不落盘用户输入，但会把**调用参数原文**记进错误信息（`error.raw` / `line`），
  所以日志里可能出现配装文本 —— 按内部数据对待。

---

## 8. 诚实声明：验证到什么程度

**已证实（本机可复现，命令与输出见 §11）**

| 项 | 怎么验的 |
|---|---|
| Dockerfile / compose 里引用的每个路径都真实存在 | 逐条 `Test-Path`（`noobbaozi/api.py`、`config/`、`engine/`、`project/`、`sde/`、`docker/entrypoint.sh`、`data/`、`spike/uvcache/` …） |
| entrypoint 的**分支决策逻辑**（4 条分支 + 退出码 0/2/3/4） | 本机**没有可用 POSIX sh**（Git 自带的 bash/dash 在沙箱里 `couldn't create signal pipe, Win32 error 5`，WSL `E_ACCESSDENIED`），因此用**逻辑等价复算**：把脚本的判定条件逐条搬进 Python，在真实目录 + 三个合成目录上跑，核对分支与退出码。**这不是执行 sh 本身。** |
| entrypoint 里内嵌的那段 Python 下载脚本语法正确 | 从 `sh` 文本里抽出来用 `compile()` 校验 |
| 所有交付文件是 **LF**（无 CRLF、无 BOM） | 逐文件扫描 `\r\n` 计数 = 0、首字节不是 `EF BB BF` |
| `entrypoint.sh` 没有 bashism（`[[`、`local`、`function`、`==` 等）、块结构平衡 | 文本扫描：`if/fi`、`case/esac`、`for/done` 计数；禁用词表命中数 0 |
| `docker-compose.yml` 是合法 YAML，且含要求的键 | PyYAML 解析 + 键路径断言（`services.noobbaozi-mcp`、`build.context`、`ports`、`volumes`、`environment`、`healthcheck`、`restart`） |
| `run-native.ps1` 能跑 | ① `-DryRun` 跑通三种目录布局（有数据 / 只有 zip / 空），退出码 0/0/2 符合预期；② `-Host` / `-Port` 绑定生效；③ **真启动**（非 dry-run）成功 |
| **入口命令在本机原生可跑** | `run-native.ps1 -Host 127.0.0.1 -Port 18080` 真起服务，服务端日志：`noobbaozi 1.0.0 启动（http）：POST http://127.0.0.1:18080/mcp · GET http://127.0.0.1:18080/health` / `工具就绪：['fit_parse','fit_index','fit_view','fit_get','fit_detail']（43ms）` |
| `GET /health` 实测 | `curl.exe http://127.0.0.1:18080/health` → `HTTP 200` + `{"ok":true,"server":"noobbaozi","version":"1.0.0","transport":"http","tools":5,"sessions":0}` |
| **healthcheck 命令双向量过** | 服务在 → `EXIT=0`；把 `NOOBBAOZI_PORT` 指到没人监听的端口 → `EXIT=1`。证明探针**不是永真**，且判据（`ok is True`）有效 |
| `POST /mcp` 实测 | `initialize` 返回 `protocolVersion/ capabilities.tools / serverInfo{"noobbaozi","1.0.0"}`；`tools/list` 返回工具清单（`fit_*`；那次实测是 5 个，现在条数见 `/health`）；响应是 SSE（`event: message` + `data:`） |
| 工具链事实 | 本机 `docker --version` → **命令不存在**；`uv 0.9.18` 可用；`sh`/`bash`/WSL 均不可用（见上） |

> 上述 `/health` 与 `/mcp` 的实测，用的是**工作区当前（未提交）的 T-47 HTTP 实现**，跑在**宿主机原生 Python** 上，
> **不是容器里**。所以它证明「命令行契约与探针正确」，**不证明镜像可用**。

**未证实（本机条件不具备，均未做）**

- ❌ **镜像构建未验证**（没有 Docker；`pip install eveshipfit-dogma-engine[sde]==13.6.0` 在 `python:3.13-slim` 里是否能装、是否需要编译器，没验过）
- ❌ **容器运行未验证**（entrypoint 在容器里的实际行为、`USER noobbaozi` 的权限、卷挂载后的可写性、`/health` 从容器内打）
- ❌ **entrypoint.sh 本身没有真正执行过**（本机无 POSIX sh，见上表第 2 行；只做了等价复算）
- ❌ **局域网另一台机器实测未做**（只在宿主机回环 `127.0.0.1:18080` 上验过）
- ❌ **MCP 客户端（Claude Desktop / Cursor / 自研 agent）真连未做**（用 curl 直接打 JSON-RPC 验的）
- ❌ **UID/属主、防火墙** 只在文档层面给方案，未实测

---

## 9. 常见故障

### 9.1 容器起不来，日志说「数据卷缺失」，退出码 2

```
[entrypoint] 错误: /app/noobbaozi/data 下既没有索引库（noobbaozi-*.sqlite3）也没有 SDE zip（*.zip）。
```

原因：镜像里没数据，卷又是空的（`noobbaozi/data/` 目录不存在或为空时，Docker 会建一个空目录挂上去）。
修：把 SDE zip 放进 `noobbaozi/data/`（或在宿主机先建好索引库），或临时开 `NOOBBAOZI_SDE_FETCH=1`。

### 9.2 healthcheck 一直 `unhealthy`

按可能性排序：

1. **构建用的代码里没有 HTTP 传输** → `/health` 根本没实现，服务在跑但探针 404。看日志确认 `启动 noobbaozi MCP 服务` 已打印；
   或直接 `docker compose logs | Select-String "启动（http）"`。工作区当前（未提交）的 T-47 实现已验证可用，但更早的提交只有 stdio。
2. 容器内服务绑定到了 `127.0.0.1` 而不是 `0.0.0.0` → 探针在容器内连 `127.0.0.1` 是通的，所以这个通常不影响探针，但**容器外**会连不上。
3. 启动慢于 `start_period: 20s`（首次打开 SDE 索引 100–300 ms，正常不该超）→ 调大 `start_period`。
4. 端口不是 8080 而 healthcheck 还在打 8080 —— healthcheck 读的是容器内的 `NOOBBAOZI_PORT`，改端口时两个一起改。

手动复算探针：

```powershell
docker compose -f noobbaozi/docker/docker-compose.yml exec noobbaozi-mcp python -c "import json,os,urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('NOOBBAOZI_PORT','8080')+'/health')))"
```

### 9.3 entrypoint 报 `\r: not found` / `: not found` / `syntax error`

**CRLF 问题**：Windows 上编辑过的 `.sh` / `Dockerfile` 会带 CRLF，容器里的 `/bin/sh` 会把 `\r` 当成命令的一部分：

```
/app/docker/entrypoint.sh: line 3: \r: not found
```

本仓库的交付文件都是 LF（§8 已验证）。若你自己改过，先修回来：

```powershell
# 去掉 CRLF 并剥掉 BOM，统一 LF
$p = 'noobbaozi/docker/entrypoint.sh'
$t = [System.IO.File]::ReadAllText($p)
$t = $t.TrimStart([char]0xFEFF) -replace "`r`n", "`n" -replace "`r", "`n"
[System.IO.File]::WriteAllText($p, $t, [System.Text.UTF8Encoding]::new($false))
```

仓库已有 `.gitattributes`（`* text=auto eol=lf`）兜底，但**已被 CRLF 化的工作区副本不会被它自动纠正**。
另外容器里只有 `sh`（dash），**没有 bash** —— 写 `#!/bin/bash` 会失败。

### 9.4 端口被占用 / 局域网连不上

```powershell
# 谁占了 8080
Get-NetTCPConnection -LocalPort 8080 -State Listen | Select-Object OwningProcess,LocalAddress
Get-Process -Id (Get-NetTCPConnection -LocalPort 8080 -State Listen).OwningProcess

# 换端口：compose 里 ports 与 NOOBBAOZI_PORT 一起改，或临时
docker run --rm -p 8081:8081 -e NOOBBAOZI_PORT=8081 -v "${PWD}/noobbaozi/data:/app/noobbaozi/data" noobbaozi-mcp:1.0.0

# 防火墙放行（管理员 PowerShell）
New-NetFirewallRule -DisplayName 'noobbaozi MCP 8080' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080
```

连不上但 `curl 127.0.0.1:8080` 通的，基本都是防火墙或网段问题。

### 9.5 工具调用返回 `index_unavailable`

服务起来了，但索引库没建好（卷空了 / 建库失败）。看 entrypoint 日志有没有 `索引库就绪:`。
手动建库见 §5。

---

## 10. 已知偏离与坑（诚实记录）

### 10.1 `.dockerignore` 放在 `docker/` 里 —— Docker 不会读它

Docker 只读取**构建上下文根**的 `.dockerignore`。当前 `context: ..`（= `noobbaozi/`），
所以生效文件必须是 `noobbaozi/.dockerignore`，本文件（`noobbaozi/docker/.dockerignore`）**不生效**。

- 为什么还这么放：本任务的写入范围只授权 `noobbaozi/docker/` 下的 6 个文件，不能新建 `noobbaozi/.dockerignore`。
- 为什么仍然安全：Dockerfile **没有用 `COPY . /app`**，而是逐条白名单 COPY（`api.py` `contracts.py`
  `cli.py` `mcp_server.py` `__init__.py` + `config/` `engine/` `project/` `sde/` + `docker/`）。
  所以即使忽略清单完全失效，`data/` 里那 94 MB 也不可能被烤进镜像 —— 这是结构上的保证。
- 想真正启用：`Copy-Item noobbaozi\docker\.dockerignore noobbaozi\.dockerignore`（需在 `noobbaozi/` 根落一个文件，超出本任务范围）。

### 10.2 `NOOBBAOZI_DATA_DIR` 只被 entrypoint 读，服务本体不读它

产品代码里**没有任何地方**读取 `NOOBBAOZI_DATA_DIR`：`noobbaozi/config/paths.py` 用
`Path(__file__).parent.parent / "data"` 算数据目录（= `<包根>/data`，绝对路径，无环境变量开关）。
所以：

- 在容器里两者**天然一致**：包在 `/app/noobbaozi`，数据目录就是 `/app/noobbaozi/data`，正好是卷的挂载点；
- `NOOBBAOZI_DATA_DIR` 只决定 **entrypoint 去哪里找 `noobbaozi-*.sqlite3` / `*.zip`**；
- 你若把它指到别处，entrypoint 找得到、服务本体却仍读 `/app/noobbaozi/data` → 会「明明有库却 `index_unavailable`」。
  **别改它**（容器内保持默认）。
- 想真正重定位数据目录，只能改 `config/paths.py`（**产品代码，本任务禁区**，故只报告不改）。

### 10.3 `[sde]` extra 里的大文件 vs 被排除的 `data/`

`eveshipfit-dogma-engine[sde]` 会拉 `eveshipfit-sde`（引擎自己的静态数据包，纯 Python，体量不小）。
**这个是依赖，必须进镜像**；被排除的是 `noobbaozi/data/` 里那份 **JSONL zip + noobbaozi 自建索引库**
（noobbaozi 自己的派生数据）。两者是不同的东西，别混：

| 东西 | 在哪 | 进镜像？ |
|---|---|---|
| `eveshipfit-sde`（引擎数据包） | `site-packages/`，pip 装 | ✅ 是，依赖 |
| `eve-online-static-data-*.zip` 94.7 MB | `noobbaozi/data/` | ❌ 否，卷 |
| `noobbaozi-<build>.sqlite3` 14.3 MB | `noobbaozi/data/` | ❌ 否，卷（建库产物） |

### 10.4 `deploy.resources.limits` 只在 Compose V2 生效

`docker-compose` V1（Python 版）会**忽略** `deploy` 段并给一条警告。用 `docker compose`（V2）才有实际限制。
V1 上要限资源就用 `docker run --memory=2g` 或 `mem_limit`。

### 10.5 这套配置依赖 T-47 的 HTTP 传输

T-47（给 `mcp_server.py` 加 `--transport http --host --port`、`POST /mcp`、`GET /health`）**已经写进本工作区**
（`git status` 里是未提交的 `M mcp_server.py` + 新增 `mcp_http.py` 等）。本机原生实测：服务起得来、`/health` 200、
`initialize` / `tools/list` 正常（§8）。

但要注意两点：

- 若你在**更早的提交**（只有 stdio 的版本）上构建镜像，容器能起，但 `/health` 不存在、HTTP 请求不可用；
- 本任务**没有**修改任何产品代码，也不对 T-47 的实现质量负责 —— 这里只对齐它的命令行契约。

---

## 11. 本机无 Docker 时的等价启动：`run-native.ps1`

```powershell
# 等价于容器里那一步（默认 8080 / 0.0.0.0）
.\noobbaozi\docker\run-native.ps1

# 只看计划，不启动
.\noobbaozi\docker\run-native.ps1 -DryRun

# 换端口 / 只听本机
.\noobbaozi\docker\run-native.ps1 -Port 8081 -Host 127.0.0.1
```

它会：

1. 从脚本位置推导路径（`noobbaozi/`、工作区根、`data/`、`spike/uvcache/`），不写死任何绝对路径；
2. 把 `UV_CACHE_DIR` 指到 `noobbaozi/spike/uvcache`（与 `noobbaozi/README.md` 的复跑命令共用缓存）；
3. 预检 `data/`：有索引库 → 打印库文件与体积；有 zip 没库 → 打印建库命令；什么都没有 → 退出码 2；
4. 打印将执行的 `uv run ... python -m noobbaozi.mcp_server --transport http --host ... --port ...`；
5. 打印**局域网可访问 URL**（自动枚举非回环 IPv4），并提示防火墙放行命令；
6. 前台启动服务（`Ctrl+C` 停止）。

`-Host` 是 `[Alias('Host')]`（变量名用 `ListenHost`），因为 `$Host` 是 PowerShell 自动变量；
脚本管理作用域策略可能需要：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

（本机实测：默认策略拒绝执行未签名脚本，上面这条能过。）

---

## 12. Dockerfile 设计要点

| 决策 | 原因 |
|---|---|
| 基础镜像 `python:3.13-slim` | 与 `noobbaozi/README.md` 里 `uv run --python 3.13` 对齐；slim 少 ~700 MB |
| **先装依赖，再 COPY 代码** | 改代码不会打掉依赖层缓存；依赖版本集中在 `ARG NOOBBAOZI_PIP_SPEC` 一行 |
| 依赖声明用 `printf` 内联而不是 `requirements.txt` | 写入范围只允许 `docker/` 下 6 个文件；缓存语义等价（依赖层不含代码） |
| 用 `pip` 不用 `uv` | `[sde]` extra 只是个纯 Python 数据包，没有 uv 特有的解析诉求；镜像里少一个工具链 |
| `COPY` 白名单，不用 `COPY .` | 结构上保证 `data/`（94.7 MB）永不进镜像，且不依赖 `.dockerignore` 位置（§10.1） |
| `useradd -m noobbaozi` + `USER noobbaozi` | 非 root 运行；`--uid 1000` 可用构建参数调，便于 Linux 宿主对齐卷属主 |
| `EXPOSE 8080` | 声明端口（纯文档性质，实际映射在 compose） |
| `HEALTHCHECK` 用 `python -c` | slim 里没有 curl；exec form 避开 `/bin/sh` 引号转义；判据是 `/health` 里 `ok == true` |
| `ENTRYPOINT ["/app/docker/entrypoint.sh"]` | 预检/建库/兜底错误都在入口里做，`docker run` 与 compose 行为一致 |
| `WORKDIR /app` + 代码在 `/app/noobbaozi/` | `python -m noobbaozi.mcp_server` 靠 cwd 找包，cwd 必须是包的父目录 |

---

## 13. 复现本文件的验收（§8 的「已证实」怎么跑的）

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
Set-Location F:\WorkSpace\GIT\opencode_general_workspace

# 1) 路径存在性
foreach ($p in @('noobbaozi/api.py','noobbaozi/contracts.py','noobbaozi/cli.py','noobbaozi/mcp_server.py','noobbaozi/__init__.py',
                 'noobbaozi/config','noobbaozi/engine','noobbaozi/project','noobbaozi/sde','noobbaozi/docker/entrypoint.sh',
                 'noobbaozi/data','noobbaozi/spike/uvcache')) { "{0,-40} {1}" -f $p, (Test-Path $p) }

# 2) LF / BOM
Get-ChildItem noobbaozi/docker -File | ForEach-Object {
  $b = [System.IO.File]::ReadAllBytes($_.FullName)
  $crlf = 0; for ($i=0; $i -lt $b.Length-1; $i++) { if ($b[$i] -eq 13 -and $b[$i+1] -eq 10) { $crlf++ } }
  $bom = ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)
  "{0,-20} CRLF={1} BOM={2}" -f $_.Name, $crlf, $bom
}

# 3) compose 是合法 YAML（注意：PowerShell 传给原生 exe 的内联 -c 会被剥引号，复杂脚本请写文件再跑）
uv run --python 3.13 --no-project --with pyyaml python -c "import yaml; d=yaml.safe_load(open('noobbaozi/docker/docker-compose.yml',encoding='utf-8')); print(sorted(d['services']['noobbaozi-mcp']))"

# 4) 原生等价启动（只看计划）
.\noobbaozi\docker\run-native.ps1 -DryRun

# 5) 真启动 + 验 /health + 验 healthcheck 命令（另开一个窗口）
.\noobbaozi\docker\run-native.ps1 -Host 127.0.0.1 -Port 18080

#   在第二个窗口：
curl.exe -s -w "`nHTTP=%{http_code}`n" http://127.0.0.1:18080/health
$env:NOOBBAOZI_PORT='18080'
python -c "import json,os,sys,urllib.request; port=os.environ.get('NOOBBAOZI_PORT','8080'); data=json.load(urllib.request.urlopen('http://127.0.0.1:'+port+'/health', timeout=4)); sys.exit(0 if data.get('ok') is True else 1)"
"HEALTHCHECK_EXIT=$LASTEXITCODE"   # 期望 0

#   验完 Ctrl+C 停掉即可（端口 18080 会释放）
```

> 注：`python -c "..."` 里的双引号由 PowerShell 直接传给 python 时**不会被剥**（`python` 在 PATH 上是 exe，
> 这里能过）；但含 `{}`/`[]`/多行的复杂脚本建议先落成 `.py` 文件再跑 —— 本任务验收时就踩过这个坑。
