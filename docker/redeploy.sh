#!/bin/sh
# =============================================================================
# noobbaozi MCP · NAS 一键重部署
#
# 用法（必须 root）：
#   sudo sh <仓库目录>/docker/redeploy.sh
#
# 仓库目录**自动从脚本自身位置推导**（= 本文件所在目录的上一级），
# 所以目录叫 noobbaozi / evefit-mcp / 任何名字都能用，不要硬编码路径。
#
# 做五件事：① 停旧容器释放端口 ② git pull ③ 重建镜像 ④ 重启容器 ⑤ 自检 /health
# 失败即退出（set -e），关键输出留在 last-deploy.log。
# =============================================================================
set -eu

# 仓库目录 = 脚本所在目录的上一级（不依赖目录名）
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
LOG="$REPO_DIR/last-deploy.log"
COMPOSE="docker compose -f docker/docker-compose.yml"
PORT="${NOOBBAOZI_PORT:-8080}"

cd "$REPO_DIR"
: > "$LOG"

say() { echo "[redeploy] $*" | tee -a "$LOG"; }
# run() 失败必须**喊出来**再退出：只用 set -e 的话，命令输出进了日志、终端上只剩「脚本没跑完」，
# 操作者看不到原因（第一次部署就栽在这上面）。
run() {
  echo "+ $*" >> "$LOG"
  if ! "$@" >> "$LOG" 2>&1; then
    say "!! 失败：$*"
    say "   最后 20 行日志："
    tail -20 "$LOG" | sed 's/^/   /' >&2
    exit 1
  fi
}

# 用 sudo 跑时仓库属主是普通用户，git 会以 "dubious ownership" 拒绝一切操作。
# 这是**部署目录**（不是开发目录），显式声明安全即可；幂等，重复执行无副作用。
git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true

say "repo: $REPO_DIR"
say "1/5 释放端口（停掉旧版容器）"
# 旧版（改名前的 evefit-mcp）会一直占着 8080，导致新容器起不来。
# 只 stop 不 rm：主人说过「旧的留着」，随时能 start 回来。
for legacy in evefit-mcp noobbaozi-mcp; do
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$legacy"; then
    say "    停掉旧容器 $legacy"
    run docker stop "$legacy"
  fi
done

say "2/5 同步到远端最新（部署快照仓库是单向镜像）"
# 为什么不是 `git pull`：部署快照仓库每次都是重新 `git init` 后强制推送的，
# 历史每次都不相关 → `pull` 必然 "Not possible to fast-forward"。
# 这个仓库在 NAS 上**没有任何本地改动**（纯部署用），所以正确的语义是「对齐远端」而不是「合并」。
run git -c core.hooksPath=/dev/null fetch --all --prune
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)
run git reset --hard "origin/$BRANCH"
say "    HEAD = $(git rev-parse --short HEAD)"

say "3/5 重建镜像（可能几分钟）"
run $COMPOSE build

say "4/5 重启容器"
run $COMPOSE up -d

say "5/5 等待健康检查"
i=0
while [ "$i" -lt 30 ]; do
  if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"ok": *true'; then
    say "    /health OK"
    break
  fi
  i=$((i + 1))
  sleep 2
done

if [ "$i" -ge 30 ]; then
  say "!! 30 次探测都没拿到 /health，最后 40 行日志："
  $COMPOSE logs --tail 40 >> "$LOG" 2>&1 || true
  echo "--- last-deploy.log 末尾 ---"
  tail -40 "$LOG"
  exit 1
fi

echo "--- tools/list 自检 ---"
TOOLS=$(curl -s -m 8 -X POST "http://127.0.0.1:$PORT/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"redeploy","version":"1"}}}' \
  | sed -n 's/^data: //p' | head -1 | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("result",{}).get("serverInfo"))' 2>/dev/null || echo "(initialize 解析失败，见日志)")
echo "serverInfo: $TOOLS"

echo "--- 容器状态 ---"
$COMPOSE ps
echo "--- 完成：http://<本机IP>:$PORT/mcp ---"
