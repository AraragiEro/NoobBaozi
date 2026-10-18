"""noobbaozi 路径配置 —— 全项目唯一的路径事实源。

规范依据 `docs/开发规范-v1.md` §1：
- 不得在代码里写死绝对路径，所有路径走配置（本文件）；
- 下载缓存与构建产物一律落在 `noobbaozi/data/`（已 gitignore，见 `.gitignore` 的 `data/`）。

依赖约束（§2）：`config/` 处于依赖表最底层，**只依赖标准库**，
不得 import 任何其他 `noobbaozi.*` 模块，否则会造成循环依赖。
"""

from __future__ import annotations

import re
from pathlib import Path

#: `noobbaozi/` 包根目录（本文件位于 `noobbaozi/config/paths.py`）。
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

#: 构建产物与下载缓存目录（gitignore；索引库是构建产物，不入库，见设计文档 §7.1）。
DATA_DIR: Path = PROJECT_ROOT / "data"

#: SQLite 建库脚本（由 `noobbaozi/sde/ingest.py` 执行）。
SDE_SCHEMA_PATH: Path = PROJECT_ROOT / "sde" / "schema.sql"

#: 索引库文件名模板：一个 build 一个库，便于多 build 并存与回滚。
DB_FILENAME_TEMPLATE: str = "noobbaozi-{build}.sqlite3"

#: SDE zip 缓存文件名（保留缓存，force 重建时不必重新下载 95 MiB）。
ZIP_FILENAME_TEMPLATE: str = "eve-online-static-data-{build}-jsonl.zip"

_DB_FILENAME_RE = re.compile(r"^noobbaozi-(?P<build>\d+)\.sqlite3$")


def data_dir() -> Path:
    """返回数据目录 `noobbaozi/data/`，不存在则创建。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def sorted_builds() -> list[int]:
    """`data/` 下已有索引库的 build 号，升序。"""
    builds: list[int] = []
    for path in data_dir().glob("*.sqlite3"):
        matched = _DB_FILENAME_RE.match(path.name)
        if matched:
            builds.append(int(matched.group("build")))
    return sorted(builds)


def db_path(build: int | None = None) -> Path:
    """索引库路径。

    - 指定 `build`：返回 `noobbaozi/data/noobbaozi-<build>.sqlite3`（**不检查文件是否存在**）；
    - 不指定：返回 `data/` 下 build 号最大的那个已建索引库。

    Raises:
        FileNotFoundError: 未指定 build 且一个索引库都没有（提示先跑 `build_index()`）。
    """
    if build is not None:
        return data_dir() / DB_FILENAME_TEMPLATE.format(build=build)
    builds = sorted_builds()
    if not builds:
        raise FileNotFoundError(
            f"{data_dir()} 下没有任何索引库；请先运行 "
            '`python -c "from noobbaozi.sde.ingest import build_index; build_index()"` 建库'
        )
    return db_path(builds[-1])


def zip_path(build: int) -> Path:
    """SDE zip 的本地缓存路径（**不检查文件是否存在**）。"""
    return data_dir() / ZIP_FILENAME_TEMPLATE.format(build=build)
