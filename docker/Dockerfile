# syntax=docker/dockerfile:1
#
# noobbaozi MCP 服务（HTTP 传输）容器镜像
# =============================================================================
# 构建上下文 = noobbaozi/ （compose 里写 context: ..），所以：
#   代码 → /app/noobbaozi/   （`python -m noobbaozi.mcp_server` 要求 cwd 是 noobbaozi/ 的父目录，即 /app）
#   docker/ → /app/docker/ （ENTRYPOINT /app/docker/entrypoint.sh 就在这）
#   noobbaozi/data/ 是**运行时卷**，不进镜像（95 MB zip + 14 MB sqlite）
#
# 本机（开发机）没有 Docker，因此**本文件未经实际构建验证**；
# 已验证的部分只有「引用的路径都真实存在」+「entrypoint 分支逻辑」+「LF 换行」，
# 见 docker/README.md §8 诚实声明。
# =============================================================================

FROM python:3.13-slim

# 依赖版本集中在这里：改依赖只动这一行，且仍在 COPY 代码之前安装 → 代码改动不会打掉依赖层缓存。
ARG NOOBBAOZI_PIP_SPEC="eveshipfit-dogma-engine[sde]==13.6.0"

# 容器内用户 uid（Linux 宿主机上挂载 data/ 时按这个 uid 对齐，见 README「数据卷」）。
ARG NOOBBAOZI_UID=1000

# PYTHONUNBUFFERED / PYTHONIOENCODING：服务日志走 stderr，容器日志要立刻可见、中文不乱码。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NOOBBAOZI_HOST=0.0.0.0 \
    NOOBBAOZI_PORT=8080 \
    NOOBBAOZI_DATA_DIR=/app/noobbaozi/data

WORKDIR /app

# ── 层 1：只装依赖（不含任何项目代码）───────────────────────────────────────
# 为什么没有单独的 requirements.txt：本任务的写入范围只允许 docker/ 下的 6 个文件。
# 用 printf 把「依赖声明」落成一层里的临时文件再 pip install，缓存语义与 COPY requirements.txt 相同：
# 只要 NOOBBAOZI_PIP_SPEC 不变，改代码不会重装依赖。
# 为什么用 pip 而不是 uv：[sde] extra 会拉 eveshipfit-sde（一个纯 Python 数据包），
# 没有 uv 特有的解析需求；镜像里少一个工具链，构建更快、攻击面更小。
RUN printf '%s\n' "${NOOBBAOZI_PIP_SPEC}" > /tmp/noobbaozi-requirements.txt \
 && pip install --no-cache-dir -r /tmp/noobbaozi-requirements.txt \
 && rm -f /tmp/noobbaozi-requirements.txt

# ── 层 2：非 root 用户 + 数据目录占位 ───────────────────────────────────────
# data/ 目录在镜像里**只建空目录**（chown 给 noobbaozi），运行期由 compose 的卷覆盖。
# 这样即使忘了挂卷，entrypoint 也能给出「数据卷缺失」的明确错误，而不是坏在别处。
RUN useradd --create-home --uid "${NOOBBAOZI_UID}" --shell /bin/sh noobbaozi \
 && mkdir -p /app/noobbaozi/data \
 && chown -R noobbaozi:noobbaozi /app

# ── 层 3：代码（显式白名单，data/ 结构上就进不来）────────────────────────────
# 不用 `COPY . /app`：Docker 只读取「构建上下文根」的 .dockerignore（即 noobbaozi/.dockerignore），
# 而本任务只被授权写 noobbaozi/docker/.dockerignore（Docker 不会读它）。
# 因此这里改成逐条白名单 COPY —— 与 .dockerignore 是否存在无关，data/ 永远不会被烤进镜像。
# 新增运行时目录时**必须**在这里补一行，否则容器里 ModuleNotFoundError。
# 注意：mcp_http.py 是 HTTP 传输的实现（mcp_server 在 main() 里惰性 import 它），
# 漏了它 → HTTP 模式启动即 ImportError；__init__.py 让 noobbaozi 成为常规包。
COPY --chown=noobbaozi:noobbaozi api.py contracts.py cli.py mcp_server.py mcp_http.py __init__.py /app/noobbaozi/
COPY --chown=noobbaozi:noobbaozi config/ /app/noobbaozi/config/
COPY --chown=noobbaozi:noobbaozi engine/ /app/noobbaozi/engine/
COPY --chown=noobbaozi:noobbaozi project/ /app/noobbaozi/project/
COPY --chown=noobbaozi:noobbaozi sde/ /app/noobbaozi/sde/
COPY --chown=noobbaozi:noobbaozi docker/ /app/docker/

# 顺手清掉宿主机带进来的 __pycache__ / .pyc（宿主与容器 Python 版本可能不同，缓存无意义）
RUN chmod 0755 /app/docker/entrypoint.sh \
 && find /app/noobbaozi /app/docker -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /app/noobbaozi /app/docker -name '*.pyc' -delete

USER noobbaozi

EXPOSE 8080

# 健康检查统一打 HTTP，不依赖 curl（slim 里没装；也不值得为一个探针装它）。
# 走 exec form（JSON 数组），绕开 /bin/sh 的引号转义。
# 判定：能连上 /health、返回里的 ok 是 true。T-47 落地前该端点不存在 → 容器会显示 unhealthy，
# 但服务本身照常运行（healthcheck 不参与 restart 决策）。
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import json,os,sys,urllib.request; port=os.environ.get('NOOBBAOZI_PORT','8080'); data=json.load(urllib.request.urlopen('http://127.0.0.1:'+port+'/health', timeout=4)); sys.exit(0 if data.get('ok') is True else 1)"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
