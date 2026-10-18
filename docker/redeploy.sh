#!/bin/sh
# =============================================================================
# evefit MCP · NAS 一键重部署
#
# 用法（必须 root）：
#   sudo sh /volume1/docker/evefit-mcp/docker/redeploy.sh
#
# 做四件事：① git pull ② 重建镜像 ③ 重启容器 ④ 自检 /health
# 失败即退出（set -e），并把关键输出留在 last-deploy.log 里。
# =============================================================================
set -eu

REPO_DIR="${1:-/volume1/docker/evefit-mcp}"
LOG="$REPO_DIR/last-deploy.log"
COMPOSE="docker compose -f docker/docker-compose.yml"
PORT="${EVEFIT_PORT:-8080}"

cd "$REPO_DIR"
: > "$LOG"

say() { echo "[redeploy] $*" | tee -a "$LOG"; }
run() { echo "+ $*" >> "$LOG"; "$@" >> "$LOG" 2>&1; }

say "repo: $REPO_DIR"
say "1/4 拉取最新代码"
run git -c core.hooksPath=/dev/null pull --ff-only
say "    HEAD = $(git rev-parse --short HEAD)"

say "2/4 重建镜像（可能几分钟）"
run $COMPOSE build

say "3/4 重启容器"
run $COMPOSE up -d

say "4/4 等待健康检查"
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
