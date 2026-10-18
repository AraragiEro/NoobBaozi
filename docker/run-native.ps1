#Requires -Version 5.1
<#
.SYNOPSIS
    Docker 不可用时，在本机原生启动 evefit MCP 服务（HTTP 传输）。

.DESCRIPTION
    等价于 docker/entrypoint.sh 最后那一步：

        python -m evefit.mcp_server --transport http --host <HOST> --port <PORT>

    用 uv 现场拉依赖（eveshipfit-dogma-engine[sde]==13.6.0），并把 UV_CACHE_DIR 指到
    项目内的 evefit/spike/uvcache —— 与 evefit/README.md、evefit/mcp/README.md 的复跑
    命令共用同一份缓存，避免重复下载 ~100 MB。

    启动后会打印一批可访问的局域网 URL；按 Ctrl+C 停止（这是前台服务）。

.PARAMETER Port
    监听端口，默认 8080。

.PARAMETER Host
    监听地址，默认 0.0.0.0（所有网卡，局域网可访问）。
    （参数别名是 Host；用的是 ListenHost 变量名，因为 $Host 是 PowerShell 的自动变量。）

.PARAMETER DryRun
    只做预检 + 打印将要执行的命令 + 打印将要访问的 URL，不真的启动服务。
    等价于 entrypoint.sh 的 EVEFIT_DRY_RUN=1。

.EXAMPLE
    .\evefit\docker\run-native.ps1

.EXAMPLE
    .\evefit\docker\run-native.ps1 -Port 8081 -Host 127.0.0.1

.EXAMPLE
    .\evefit\docker\run-native.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,

    [Alias('Host')]
    [string]$ListenHost = '0.0.0.0',

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) { Write-Host "[run-native] $Message" -ForegroundColor Cyan }
function Write-Note([string]$Message) { Write-Host "[run-native] $Message" -ForegroundColor DarkGray }
function Write-Warn([string]$Message) { Write-Host "[run-native] 警告: $Message" -ForegroundColor Yellow }
function Write-Fail([string]$Message) { Write-Host "[run-native] 错误: $Message" -ForegroundColor Red }

# ── 0. 路径事实（全部由本文件位置推导，不写死）──────────────────────────────
$dockerDir = $PSScriptRoot
$evefitDir = Split-Path -Parent $dockerDir          # …/evefit
$workspaceRoot = Split-Path -Parent $evefitDir      # …/opencode_general_workspace（evefit 包的父目录）
$dataDir = Join-Path $evefitDir 'data'
$uvCacheDir = Join-Path $evefitDir 'spike\uvcache'
$engineSpec = 'eveshipfit-dogma-engine[sde]==13.6.0'

if ($env:EVEFIT_DRY_RUN -eq '1') { $DryRun = $true }
$dry = [bool]$DryRun

Write-Step "workspace=$workspaceRoot"
Write-Step "evefit=$evefitDir  data=$dataDir"
Write-Step "listen=${ListenHost}:$Port  dry_run=$dry"

# ── 1. 工具链 ───────────────────────────────────────────────────────────────
$uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Fail 'PATH 里没有 uv。装一个（winget install --id=astral-sh.uv -e）或改用容器：docker compose -f docker/docker-compose.yml up -d --build'
    exit 1
}
Write-Note "uv = $($uv.Source)"

if (Test-Path -LiteralPath $uvCacheDir) {
    $env:UV_CACHE_DIR = $uvCacheDir
    Write-Note "UV_CACHE_DIR = $uvCacheDir"
} else {
    Write-Warn "缓存目录不存在（$uvCacheDir）→ uv 会用自己的默认缓存，首次要重新下载依赖"
}

# ── 2. 数据预检（与 entrypoint.sh 分支 1 对齐）───────────────────────────────
$dbFiles = @()
$zipFiles = @()
if (Test-Path -LiteralPath $dataDir) {
    $dbFiles = @(Get-ChildItem -LiteralPath $dataDir -Filter 'evefit-*.sqlite3' -File -ErrorAction SilentlyContinue)
    $zipFiles = @(Get-ChildItem -LiteralPath $dataDir -Filter '*.zip' -File -ErrorAction SilentlyContinue)
}

$preflightFailed = $false
if ($dbFiles.Count -gt 0) {
    Write-Step "发现索引库: $($dbFiles[0].FullName)（$([math]::Round($dbFiles[0].Length / 1MB, 1)) MiB）"
} elseif ($zipFiles.Count -gt 0) {
    Write-Step "没有索引库，但找到 SDE zip: $($zipFiles[0].Name)"
    Write-Warn '本脚本不会替你建库。先跑一次建库（不需要 Docker）：'
    Write-Note ('  $env:UV_CACHE_DIR = "{0}"' -f $uvCacheDir)
    $buildFromName = $null
    if ($zipFiles[0].Name -match '^eve-online-static-data-(\d+)-jsonl\.zip$') { $buildFromName = $Matches[1] }
    if ($buildFromName) {
        Write-Note ('  uv run --python 3.13 --no-project --with "{0}" python -m evefit.sde.ingest --build {1}' -f $engineSpec, $buildFromName)
        Write-Note ('  （build {0} 从 zip 文件名取到 → 建库不需要联网）' -f $buildFromName)
    } else {
        Write-Note ('  uv run --python 3.13 --no-project --with "{0}" python -m evefit.sde.ingest' -f $engineSpec)
        Write-Note '  （zip 文件名不是 eve-online-static-data-<build>-jsonl.zip 形态 → 不传 --build，ingest 会联网取最新 build）'
    }
} else {
    $preflightFailed = $true
    Write-Fail "在 $dataDir 下既没有 evefit-*.sqlite3 也没有 *.zip。"
    Write-Note '服务本身不依赖网络，但没有索引库 → 每个工具调用都会返回 index_unavailable。'
    Write-Note '先准备数据：把 eve-online-static-data-<build>-jsonl.zip 放进 evefit/data/，再跑 python -m evefit.sde.ingest 建库。'
    Write-Note '（容器那条路可以用 EVEFIT_SDE_FETCH=1 让 entrypoint 自己下载。）'
    if (-not $dry) {
        Write-Fail '退出码 2（数据卷缺失）。先补数据；只想看计划就加 -DryRun。'
        exit 2
    }
}

# ── 3. 要执行的命令（与 T-47 约定的入口一致）─────────────────────────────────
$uvArgs = @(
    'run', '--python', '3.13', '--no-project',
    '--with', $engineSpec,
    'python', '-m', 'evefit.mcp_server',
    '--transport', 'http',
    '--host', $ListenHost,
    '--port', "$Port"
)

$joined = ($uvArgs | ForEach-Object { if ($_ -match '[\s\[\]]') { '"' + $_ + '"' } else { $_ } }) -join ' '
Write-Step "将执行: uv $joined"
Write-Step "工作目录: $workspaceRoot（python -m evefit.mcp_server 靠 cwd 找 evefit 包）"

# ── 4. 可访问的 URL（局域网）─────────────────────────────────────────────────
$lanIps = @()
try {
    $lanIps = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
        Select-Object -ExpandProperty IPAddress -Unique)
} catch {
    try {
        $lanIps = @([System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
            Where-Object { $_.AddressFamily -eq 'InterNetwork' -and $_.ToString() -notlike '127.*' } |
            ForEach-Object { $_.ToString() })
    } catch {
        $lanIps = @()
    }
}

if ($ListenHost -eq '0.0.0.0' -or $ListenHost -eq '*') {
    if ($lanIps.Count -eq 0) {
        Write-Warn '没探测到局域网 IPv4，只打印本机回环地址'
        $lanIps = @('127.0.0.1')
    }
} else {
    $lanIps = @($ListenHost)
}

Write-Step '可访问地址：'
foreach ($ip in $lanIps) {
    Write-Host ("    health : http://{0}:{1}/health" -f $ip, $Port)
    Write-Host ("    MCP    : http://{0}:{1}/mcp" -f $ip, $Port)
}
Write-Note 'windows 防火墙默认拦入站 TCP：局域网连不上时按需放行（管理员 PowerShell）：'
Write-Note ("  New-NetFirewallRule -DisplayName 'evefit MCP {0}' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {0}" -f $Port)

if ($preflightFailed) {
    Write-Warn '预检失败（没有索引库）：服务能起来，但工具调用会全部返回 index_unavailable。'
}

# ── 5. 启动 ─────────────────────────────────────────────────────────────────
if ($dry) {
    Write-Step 'DRY-RUN：不启动服务，退出码 0'
    exit 0
}

Write-Step '启动中…（前台进程，Ctrl+C 停止）'
Push-Location -LiteralPath $workspaceRoot
$code = 0
try {
    & $uv.Source @uvArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($code -ne 0) {
    Write-Fail "uv 退出码 $code"
    Write-Note '若报 unknown/unrecognized argument --transport，说明 T-47（HTTP 传输）还没合到本工作区。'
    Write-Note '若报 ModuleNotFoundError: evefit，检查 cwd 是否是 evefit 包的父目录。'
}
exit $code
