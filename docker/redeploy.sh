#!/bin/sh
# =============================================================================
# noobbaozi MCP · NAS 一键重部署
#
# 用法（必须 root）：
#   sudo sh <仓库目录>/docker/redeploy.sh
#   sudo NOOBBAOZI_SDE_FETCH=1 sh <仓库目录>/docker/redeploy.sh   # 数据卷空、允许首次联网下载
#   sh <仓库目录>/docker/redeploy.sh --sync-only                  # 只做「保护 + 同步 + 恢复 + 预检」，不碰 docker
#   sudo NOOBBAOZI_DATA_DIR=/volume1/docker/noobbaozi/data sh <仓库目录>/docker/redeploy.sh
#
# 仓库目录**自动从脚本自身位置推导**（= 本文件所在目录的上一级），所以目录叫
# noobbaozi / evefit-mcp / 任何名字都能用。
# ⚠️ **不要硬编码路径**：早期脚本写死过 `/volume1/docker/noobbaozi`，项目从 evefit 改名
# 成 noobbaozi 之后整段失效 —— 同一个坑不要踩第三次。
#
# 部署源就是**开发仓本身**（`AraragiEro/NoobBaozi`，线性历史）。NAS 上这份 checkout 是
# **纯部署目录**：没有本地改动、也不会产生提交，所以「对齐远端」的正确语义是
# `reset --hard` 而不是 `pull` / `merge`（部署目录不存在任何值得保留的本地状态）。
#
# ⚠️ 为什么第 2 步要保护 data/（真实事故 · 2026-10-20）
# -----------------------------------------------------------------------------
# 部署源曾经是「快照仓」（`data/*.sqlite3` 被 git **跟踪**）。切回开发仓之后 `data/`
# 变成了 `.gitignore` 里的内容（不再跟踪）。`git reset --hard` 会**删掉「旧历史跟踪、
# 新历史不存在」的文件** —— 14.3 MB 的 `data/noobbaozi-3607705.sqlite3` 就是这么没的，
# 容器随即以退出码 2 起不来（entrypoint 报「既没有索引库也没有 SDE zip」）。
# 所以 fetch/reset **前后**必须「先拷出去、再拷回来」，并在恢复后确认数据卷不是空的：
# 建一个必然起不来的容器没有意义，宁可在部署前就退出。
#
# 做七件事：① 停旧容器 ② 保护 data/ ③ 同步到远端 ④ 恢复 data/ 并预检
#            ⑤ 重建镜像 ⑥ 重启容器 ⑦ 健康检查 + tools/list 自检
# 失败即退出（set -e），关键输出留在 last-deploy.log（同时打到终端）。
# =============================================================================
set -eu

# ---- 参数：--sync-only / 仓库目录（两者顺序随意，都省略时用脚本位置推导）----
SYNC_ONLY=0
REPO_ARG=""
for arg in "$@"; do
  case "$arg" in
    --sync-only) SYNC_ONLY=1 ;;
    -h | --help)
      echo "用法: sh redeploy.sh [--sync-only] [仓库目录]"
      echo "  --sync-only  只做「保护数据卷 + 同步到远端 + 恢复数据卷 + 预检」，不重建镜像、不重启容器"
      exit 0
      ;;
    *) REPO_ARG="$arg" ;;
  esac
done

# 仓库目录 = 脚本所在目录的上一级（不依赖目录名）
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ -n "$REPO_ARG" ]; then
  REPO_DIR=$(CDPATH='' cd -- "$REPO_ARG" && pwd)
else
  REPO_DIR=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
fi
LOG="$REPO_DIR/last-deploy.log"
COMPOSE="docker compose -f docker/docker-compose.yml"
PORT="${NOOBBAOZI_PORT:-8080}"

# data 卷必须与 docker-compose.yml 里的 `../data` 指向同一处：
# compose 的相对路径以**compose 文件所在目录**（docker/）为基准 → docker/../data = <repo>/data。
# 允许 `NOOBBAOZI_DATA_DIR` 覆盖（entrypoint 读的是同一个变量名），但**不写死**任何绝对路径。
DATA_DIR="${NOOBBAOZI_DATA_DIR:-$REPO_DIR/data}"

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
# 测试里设 NOOBBAOZI_SKIP_GIT_CONFIG=1：被测脚本不该改宿主机的全局 gitconfig。
if [ "${NOOBBAOZI_SKIP_GIT_CONFIG:-0}" != "1" ]; then
  git config --global --add safe.directory "$REPO_DIR" 2>/dev/null || true
fi

# ---- 数据卷保护（本脚本最要紧的一段）---------------------------------------
# PROTECT_DIR 为空 = 没有在保护中；restore_data() 恢复成功后会清空它，
# 于是 EXIT trap 在正常退出时是空操作，只在「中途异常退出」时才兜底拷回。
PROTECT_DIR=""
RESTORED=0
SKIPPED=0

restore_data() {
  if [ -z "$PROTECT_DIR" ] || [ ! -d "$PROTECT_DIR" ]; then
    return 0
  fi
  mkdir -p "$DATA_DIR"
  RESTORED=0
  SKIPPED=0
  for path in "$PROTECT_DIR"/*; do
    if [ ! -e "$path" ]; then
      continue
    fi
    name=$(basename "$path")
    target="$DATA_DIR/$name"
    # reset 之后已经存在且非空 → 那是远端给的新文件，**不覆盖**（保护不是回滚）
    if [ -s "$target" ]; then
      SKIPPED=$((SKIPPED + 1))
      continue
    fi
    if ! cp -p "$path" "$target"; then
      say "!! 恢复失败: $name（备份仍在 $PROTECT_DIR，别删这个目录）"
      return 1
    fi
    RESTORED=$((RESTORED + 1))
  done
  say "    恢复了 $RESTORED 个文件（已存在跳过 $SKIPPED 个）"
  rm -rf "$PROTECT_DIR"
  PROTECT_DIR=""
  return 0
}

on_exit() {
  # 正常路径已经恢复过（PROTECT_DIR 已清空）→ 这里什么都不做；
  # 走到这里说明中途失败了（reset 被打断 / 构建失败…），数据卷必须兜底拷回去。
  if [ -n "$PROTECT_DIR" ] && [ -d "$PROTECT_DIR" ]; then
    echo "[redeploy] 异常退出：尝试把数据卷备份拷回 $DATA_DIR" >&2
    if restore_data; then
      echo "[redeploy] 兜底恢复完成" >&2
    else
      echo "[redeploy] 兜底恢复失败；备份在 $PROTECT_DIR（别删，手动拷回）" >&2
    fi
  fi
}
trap 'on_exit' EXIT

# =============================================================================
# ① 停旧容器（释放端口）
# =============================================================================
if [ "$SYNC_ONLY" = "1" ]; then
  say "1/7 跳过（--sync-only 不碰 docker）"
else
  say "1/7 释放端口（停掉旧版容器）"
  # 旧版（改名前的 evefit-mcp）会一直占着 8080，导致新容器起不来。
  # 只 stop 不 rm：主人说过「旧的留着」，随时能 start 回来。
  for legacy in evefit-mcp noobbaozi-mcp; do
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$legacy"; then
      say "    停掉旧容器 $legacy"
      run docker stop "$legacy"
    fi
  done
fi

# =============================================================================
# ② 保护数据卷（必须在 fetch/reset **之前**）
# =============================================================================
# >>> DATA-PROTECT（测试用标记：tests/test_docker_deploy.py 会**整段删除**这两行之间的内容，
#                  来复现「没有保护就会丢库」。删掉标记 = 删掉那条非永真证明的锚点。）
say "2/7 保护数据卷（reset --hard 会删掉旧历史跟踪、新历史没有的文件）"
say "    data: $DATA_DIR"
PROTECT_COUNT=0
if [ ! -d "$DATA_DIR" ]; then
  say "    $DATA_DIR 不存在（首次部署？）—— 跳过保护"
else
  PROTECT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/noobbaozi-data-protect.XXXXXX")
  for path in "$DATA_DIR"/*.sqlite3 "$DATA_DIR"/*.zip; do
    if [ -e "$path" ]; then
      name=$(basename "$path")
      source_bytes=$(wc -c < "$path" | tr -d ' ')
      # 复制失败**绝不**继续 reset：带着半份备份去删库，就是上一次事故的翻版。
      if ! cp -p "$path" "$PROTECT_DIR/$name"; then
        say "!! 保护失败（复制 $name 出错）→ 不继续同步；库里还是原样，容器也没动"
        exit 1
      fi
      # 「cp 成功」也可能是半份（磁盘满 / 写入被截断）→ 字节数必须对得上才继续。
      if [ -s "$PROTECT_DIR/$name" ]; then
        protected_bytes=$(wc -c < "$PROTECT_DIR/$name" | tr -d ' ')
      else
        protected_bytes="missing"
      fi
      if [ "$protected_bytes" != "$source_bytes" ]; then
        say "!! 保护失败（$name 复制后 $protected_bytes 字节 ≠ 原 $source_bytes 字节）→ 不继续同步"
        exit 1
      fi
      PROTECT_COUNT=$((PROTECT_COUNT + 1))
      say "    已保护: $name ($source_bytes 字节)"
    fi
  done
  say "    保护了 $PROTECT_COUNT 个文件 → $PROTECT_DIR"
fi
# <<< DATA-PROTECT

# =============================================================================
# ③ 同步到远端最新
# =============================================================================
say "3/7 同步到远端（部署目录无本地改动 → 对齐远端，不是合并）"
run git -c core.hooksPath=/dev/null fetch --all --prune
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)
if [ "$BRANCH" = "HEAD" ]; then
  # 游离 HEAD（上次部署被打断 / 手工 checkout 过）：按约定回 main，可用 env 覆盖
  BRANCH="${NOOBBAOZI_BRANCH:-main}"
  say "    当前是游离 HEAD → 按 $BRANCH 对齐"
fi
run git reset --hard "origin/$BRANCH"
say "    HEAD = $(git rev-parse --short HEAD) ($BRANCH)"

# =============================================================================
# ④ 恢复数据卷 + 部署前预检（空数据卷 → 现在就退出，别建一个必然起不来的容器）
# =============================================================================
say "4/7 恢复数据卷 + 预检"
if ! restore_data; then
  exit 1
fi

HAS_INDEX=0
HAS_ZIP=0
for candidate in "$DATA_DIR"/noobbaozi-*.sqlite3; do
  if [ -s "$candidate" ]; then
    HAS_INDEX=1
  fi
done
for candidate in "$DATA_DIR"/*.zip; do
  if [ -s "$candidate" ]; then
    HAS_ZIP=1
  fi
done

if [ "$HAS_INDEX" -eq 0 ] && [ "$HAS_ZIP" -eq 0 ]; then
  if [ "${NOOBBAOZI_SDE_FETCH:-0}" = "1" ]; then
    say "!! $DATA_DIR 里没有索引库也没有 zip，但 NOOBBAOZI_SDE_FETCH=1 → 交给 entrypoint 下载并建库"
  else
    say "!! 部署前检查失败：$DATA_DIR 下既没有索引库（noobbaozi-*.sqlite3）也没有 SDE zip（*.zip）"
    say "   这个状态重建容器必然起不来（entrypoint 退出码 2）→ 现在退出（退出码 3）；镜像与容器都没动"
    say "   修法：(a) 把索引库/zip 放回 $DATA_DIR；或 (b) 允许首次联网："
    say "         sudo NOOBBAOZI_SDE_FETCH=1 sh docker/redeploy.sh"
    exit 3
  fi
else
  say "    数据卷 OK：index=$HAS_INDEX zip=$HAS_ZIP"
fi

if [ "$SYNC_ONLY" = "1" ]; then
  say "--sync-only 完成（未重建镜像、未重启容器）"
  exit 0
fi

# =============================================================================
# ⑤ 重建镜像 / ⑥ 重启容器
# =============================================================================
say "5/7 重建镜像（可能几分钟）"
run $COMPOSE build

say "6/7 重启容器"
run $COMPOSE up -d

# =============================================================================
# ⑦ 健康检查 + tools/list 自检
# =============================================================================
say "7/7 等待健康检查"
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
