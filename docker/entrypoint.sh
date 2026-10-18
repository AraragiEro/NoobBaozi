#!/bin/sh
# =============================================================================
# evefit MCP 容器入口
#   1) 预检数据卷：有索引库 → 直接用；有 SDE zip → 现场建库；都没有 → 说清楚怎么修
#   2) EVEFIT_DRY_RUN=1 → 只打印「将要执行什么」，退出 0（本机验证分支逻辑用）
#   3) exec 起 HTTP 传输的 MCP 服务（T-47 约定的命令行）
#
# 只用 POSIX sh：python:3.13-slim 是 Debian，有 sh 和 dash，**没有 bash**。
# 本文件必须是 LF：CRLF 会让容器里的 /bin/sh 报 `\r: not found`。
# =============================================================================
set -eu

log() {
    printf '[entrypoint] %s\n' "$*" >&2
}

# ─── 0. 环境变量（带默认值）─────────────────────────────────────────────────
EVEFIT_HOST=${EVEFIT_HOST:-0.0.0.0}
EVEFIT_PORT=${EVEFIT_PORT:-8080}
EVEFIT_DATA_DIR=${EVEFIT_DATA_DIR:-/app/evefit/data}
EVEFIT_DRY_RUN=${EVEFIT_DRY_RUN:-0}
EVEFIT_SDE_FETCH=${EVEFIT_SDE_FETCH:-0}

# export：下面 python -c 那段是从 os.environ 读目录的（单引号里的 $ 不会被 shell 展开，
# 所以必须真的在环境里；光有 shell 变量不够）。
export EVEFIT_HOST EVEFIT_PORT EVEFIT_DATA_DIR EVEFIT_DRY_RUN EVEFIT_SDE_FETCH

# `python -m evefit.mcp_server` 靠 cwd 找到 evefit 包 → cwd 必须是 evefit/ 的父目录。
if [ -d /app/evefit ]; then
    cd /app
fi

log "启动配置：host=$EVEFIT_HOST port=$EVEFIT_PORT data=$EVEFIT_DATA_DIR dry_run=$EVEFIT_DRY_RUN sde_fetch=$EVEFIT_SDE_FETCH cwd=$(pwd)"

# ─── 辅助 ────────────────────────────────────────────────────────────────────
# 统一出口：dry-run 时只打印，不执行。
run() {
    if [ "$EVEFIT_DRY_RUN" = "1" ]; then
        log "DRY-RUN 将执行: $*"
        return 0
    fi
    log "执行: $*"
    "$@"
}

# 在 $EVEFIT_DATA_DIR 里挑第一个 evefit-*.sqlite3 与第一个 *.zip（结果写进全局变量）。
detect_files() {
    db_file=
    zip_file=
    for candidate in "$EVEFIT_DATA_DIR"/evefit-*.sqlite3; do
        if [ -f "$candidate" ]; then
            db_file=$candidate
            break
        fi
    done
    for candidate in "$EVEFIT_DATA_DIR"/*.zip; do
        if [ -f "$candidate" ]; then
            zip_file=$candidate
            break
        fi
    done
}

# 从 eve-online-static-data-<build>-jsonl.zip 里取 build 号；取不到就什么都不打印。
build_number_from_zip() {
    _base=${1##*/}
    case "$_base" in
        eve-online-static-data-*-jsonl.zip)
            _build=${_base#eve-online-static-data-}
            _build=${_build%-jsonl.zip}
            case "$_build" in
                '' | *[!0-9]*) return 0 ;;
            esac
            printf '%s' "$_build"
            ;;
    esac
    return 0
}

# ─── 1. 预检：索引库 / SDE zip ───────────────────────────────────────────────
detect_files
build_needed=0
fetch_needed=0
preflight_failed=0

if [ -n "$db_file" ]; then
    log "发现索引库：$db_file"
elif [ -n "$zip_file" ]; then
    log "没有索引库（evefit-*.sqlite3），但找到 SDE zip：$zip_file → 需要现场建库"
    build_needed=1
elif [ "$EVEFIT_SDE_FETCH" = "1" ]; then
    log "没有索引库，也没有 SDE zip；EVEFIT_SDE_FETCH=1 → 允许联网下载官方 SDE"
    fetch_needed=1
    build_needed=0
else
    preflight_failed=1
    log "错误: $EVEFIT_DATA_DIR 下既没有索引库（evefit-*.sqlite3）也没有 SDE zip（*.zip）。"
    log "  镜像里**只有代码**，SDE 数据是运行时卷（zip 95 MiB + sqlite 14 MiB，不该烤进镜像）。修复方式二选一："
    log "  (a) 先准备数据卷：宿主机 evefit/data/ 里要有 eve-online-static-data-<build>-jsonl.zip"
    log "      或 evefit-<build>.sqlite3；compose 默认把 ../data 挂到 $EVEFIT_DATA_DIR。"
    log "  (b) 允许首次联网：设 EVEFIT_SDE_FETCH=1，容器内下载官方 SDE 并现场建库（约 95 MiB）。"
    log "  官方 SDE 地址："
    log "    https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
    log "    https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-<build>-jsonl.zip"
fi

if [ "$preflight_failed" = "1" ] && [ "$EVEFIT_DRY_RUN" != "1" ]; then
    log "退出码 2（数据卷缺失；本分支在 dry-run 下不退出，见文末 DRY-RUN 结论）"
    exit 2
fi

# ─── 2. 需要时才下载官方 SDE ────────────────────────────────────────────────
if [ "$fetch_needed" = "1" ]; then
    if [ "$EVEFIT_DRY_RUN" = "1" ]; then
        log "DRY-RUN 将执行: python -c <读 latest.jsonl 取 buildNumber，下载 eve-online-static-data-<build>-jsonl.zip 到 $EVEFIT_DATA_DIR>"
        log "DRY-RUN 将执行: python -m evefit.sde.ingest --build <下载到的 build>"
    else
        log "下载官方 SDE（latest.jsonl → buildNumber → zip），目标目录 $EVEFIT_DATA_DIR"
        if ! zip_file=$(python -c '
import json, os, sys, urllib.request
DATA_DIR = os.environ.get("EVEFIT_DATA_DIR", "/app/evefit/data")
BASE = "https://developers.eveonline.com/static-data/tranquility/"
HEADERS = {"User-Agent": "evefit-docker/1.0 (+https://developers.eveonline.com/static-data)"}
def open_url(url, timeout):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)
try:
    with open_url(BASE + "latest.jsonl", 60) as resp:
        build = int(json.load(resp)["buildNumber"])
except Exception as exc:
    print("[entrypoint] SDE 下载失败：读 " + BASE + "latest.jsonl 出错：" + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
    sys.exit(10)
name = "eve-online-static-data-" + str(build) + "-jsonl.zip"
target = os.path.join(DATA_DIR, name)
print("[entrypoint] 下载 " + BASE + name + " → " + target, file=sys.stderr)
try:
    with open_url(BASE + name, 300) as resp:
        with open(target, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
except Exception as exc:
    print("[entrypoint] SDE 下载失败：拉 " + BASE + name + " 出错：" + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
    try:
        os.remove(target)
    except OSError:
        pass
    sys.exit(11)
print(target)
'); then
            log "错误: SDE 下载失败（详见上面的 stderr）。请检查容器的出网、代理、DNS，或改为挂载已有数据卷（见 (a)）。"
            log "退出码 3"
            exit 3
        fi
        if [ -z "$zip_file" ] || [ ! -f "$zip_file" ]; then
            log "错误: 下载脚本没有给出可用的 zip 路径（收到 '$zip_file'）。"
            log "退出码 3"
            exit 3
        fi
        log "SDE 就绪：$zip_file"
        build_needed=1
    fi
fi

# ─── 3. 需要时才建库（有 zip 没索引库）──────────────────────────────────────
if [ "$build_needed" = "1" ] && [ "$EVEFIT_DRY_RUN" != "1" ]; then
    build_number=$(build_number_from_zip "$zip_file")
    if [ -n "$build_number" ]; then
        log "从 zip 文件名取到 build=$build_number（建库不需要联网）"
        run python -m evefit.sde.ingest --build "$build_number"
    else
        log "zip 文件名不是 eve-online-static-data-<build>-jsonl.zip 形态 → 交给 ingest 自取最新 build（需要联网）"
        run python -m evefit.sde.ingest
    fi
    # 建库后再确认一次，别把失败留给运行时
    if [ ! -f "$zip_file" ]; then
        log "警告: 建库后 zip 不见了（$zip_file）"
    fi
    detect_files
    if [ -n "$db_file" ]; then
        log "索引库就绪：$db_file"
    else
        log "错误: 建库命令返回成功，但 $EVEFIT_DATA_DIR 下仍然没有 evefit-*.sqlite3。"
        log "退出码 4"
        exit 4
    fi
elif [ "$build_needed" = "1" ] && [ "$EVEFIT_DRY_RUN" = "1" ]; then
    build_number=$(build_number_from_zip "$zip_file")
    if [ -n "$build_number" ]; then
        log "DRY-RUN 将执行: python -m evefit.sde.ingest --build $build_number"
    else
        log "DRY-RUN 将执行: python -m evefit.sde.ingest"
    fi
fi

# ─── 4. dry-run：打印启动命令后退出 0 ───────────────────────────────────────
if [ "$EVEFIT_DRY_RUN" = "1" ]; then
    log "DRY-RUN 将执行: python -m evefit.mcp_server --transport http --host $EVEFIT_HOST --port $EVEFIT_PORT"
    if [ "$preflight_failed" = "1" ]; then
        log "DRY-RUN 结论：数据卷缺失 —— 真实运行会在预检阶段以退出码 2 结束，不会走到启动"
    else
        log "DRY-RUN 结论：预检通过 —— 真实运行会 exec 上面的命令（MCP 端点 http://$EVEFIT_HOST:$EVEFIT_PORT/mcp，健康检查 /health）"
    fi
    log "DRY-RUN 结束（未启动服务，退出码 0）"
    exit 0
fi

# ─── 5. 起服务 ──────────────────────────────────────────────────────────────
log "启动 evefit MCP 服务：transport=http host=$EVEFIT_HOST port=$EVEFIT_PORT（MCP /mcp，健康检查 /health）"
exec python -m evefit.mcp_server --transport http --host "$EVEFIT_HOST" --port "$EVEFIT_PORT"
