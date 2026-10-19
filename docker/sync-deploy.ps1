#!/usr/bin/env pwsh
# =============================================================================
# NoobBaozi · 同步部署快照到 GitHub（开发机侧，Lead 专用）
#
# 用法（在工作区根目录）：
#   pwsh -File noobbaozi/docker/sync-deploy.ps1
#
# 为什么要有这个脚本：
#   部署快照仓库**必须是线性历史**，NAS 上才能 `git pull` 快进。
#   早期做法是「每次重新 git init + 强制推送」，结果是 NAS 每次 `git pull` 都报
#   `Not possible to fast-forward` —— 因为每次推送都是互不相关的根提交。
#   现在改成：本地保留一个持久克隆（.tmp/deploy），每次只做增量提交 + 普通 push。
# =============================================================================
$ErrorActionPreference = "Stop"
$env:GIT_TERMINAL_PROMPT = "0"

$src = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$deploy = Join-Path $src ".tmp\deploy"
$remote = "https://github.com/AraragiEro/NoobBaozi.git"

# 1) 持久克隆（只克隆一次，之后就是增量）
if (-not (Test-Path (Join-Path $deploy ".git"))) {
    Write-Host "[sync] 首次克隆远端到 .tmp/deploy"
    $tok = (gh auth token).Trim()
    git -c credential.helper= -c http.sslBackend=openssl clone -q "https://x-access-token:$tok@$($remote -replace '^https://','')" $deploy
} else {
    Write-Host "[sync] 拉取远端最新（保持线性）"
    git -C $deploy -c credential.helper= -c http.sslBackend=openssl pull --ff-only -q
}

# 2) 用当前源码覆盖（保留 .git）
Get-ChildItem $deploy -Force | Where-Object Name -ne '.git' | Remove-Item -Recurse -Force
Get-ChildItem (Join-Path $src "*.py") | Copy-Item -Destination $deploy -Force
foreach ($f in @("README.md", "PROGRESS.md", "TEAM.md", ".gitattributes")) {
    $p = Join-Path $src $f
    if (Test-Path $p) { Copy-Item $p $deploy -Force }
}
foreach ($d in @("project", "engine", "sde", "config", "mcp", "skill", "docker")) {
    $sd = Join-Path $src $d
    if (-not (Test-Path $sd)) { continue }
    $dd = Join-Path $deploy $d
    New-Item -ItemType Directory -Path $dd -Force | Out-Null
    Get-ChildItem $sd -Recurse -File | Where-Object { $_.FullName -notmatch '__pycache__' } | ForEach-Object {
        $rel = $_.FullName.Substring((Resolve-Path $sd).Path.Length).TrimStart('\')
        $t = Join-Path $dd $rel
        New-Item -ItemType Directory -Path (Split-Path $t) -Force | Out-Null
        Copy-Item $_.FullName $t -Force
    }
}
New-Item -ItemType Directory -Path (Join-Path $deploy "data") -Force | Out-Null
Copy-Item (Join-Path $src "data\noobbaozi-3607705.sqlite3") (Join-Path $deploy "data") -Force
# 部署仓库必须把 data/ 入库（容器要用索引库）
[System.IO.File]::WriteAllText((Join-Path $deploy ".gitignore"), "`n__pycache__/`n*.pyc`n.pytest_cache/`n", (New-Object System.Text.UTF8Encoding $false))

# 3) 增量提交 + 普通推送（绝不 --force）
git -C $deploy config user.name "noobbaozi-bot"
git -C $deploy config user.email "noobbaozi@local"
git -C $deploy config http.postBuffer 52428800
git -C $deploy add -A
if ((git -C $deploy status --porcelain | Measure-Object).Count -eq 0) {
    Write-Host "[sync] 无改动，远端已是最新: $(git -C $deploy log --oneline -1)"
    return
}
$subject = if ($args.Count -gt 0) { $args -join " " } else { "deploy: 同步 $(Get-Date -Format 'yyyy-MM-dd HH:mm')" }
git -C $deploy commit -q -m $subject
$tok = (gh auth token).Trim()
git -C $deploy -c credential.helper= -c http.sslBackend=openssl push origin main
Write-Host "[sync] 已推送（线性）: $(git -C $deploy log --oneline -1)"
Write-Host "[sync] NAS 上执行: cd /volume1/docker/noobbaozi && git pull --ff-only && sudo sh docker/redeploy.sh"
