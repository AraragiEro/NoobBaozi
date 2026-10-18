"""SDE 下载与解析：把官方 JSONL 静态数据变成可查询的 SQLite 索引库（L1）。

入口只有 `build_index()`：

    from noobbaozi.sde.ingest import build_index
    build_index()                 # 取最新 build；已有同 build 的库则直接复用
    build_index(build=3607705)    # 指定 build
    build_index(force=True)       # 强制重建（zip 走本地缓存，不重新下载）

数据源（实测，build 3607705）：

- `https://developers.eveonline.com/static-data/tranquility/latest.jsonl`
  → 一个 JSON 对象，build 号在字段 **`buildNumber`**（不是 `sde`），另有 `releaseDate`；
- `https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-<build>-jsonl.zip`
  → 实测 **99,297,951 B（约 94.7 MiB）**，102 个成员；解压后 `types.jsonl` 单文件就有
  154 MB，所以**按成员名流式读 zip，绝不解压整包**；
- 只取 5 个成员：`types.jsonl` / `groups.jsonl` / `categories.jsonl` /
  `dogmaAttributes.jsonl` / `dogmaUnits.jsonl`；
- 包内 `_sde.jsonl` 自带 `{"_key":"sde","buildNumber":...,"releaseDate":...}`，
  用来交叉校验本地缓存没串版本。

JSONL 实际结构（**先 dump 前两行确认过，勿凭猜**）：

```json
{"_key": 35683, "groupID": 1305, "name": {"de": "...", "en": "Hecate", "zh": "赫卡特级", ...},
 "portionSize": 1, "published": true, "volume": 50000.0, "metaLevel": 0, ...}
```

- 记录主键是根上的整数 **`_key`**；本 build 的这 5 个成员里**没有**
  `{"_key":..,"_value":..}` 包装（原始文本搜 `"_value"` 命中 0 行），
  整数引用（`groupID` / `categoryID` / `unitID`）是裸整数；
- 名称是多语言字典，索引取 `en` 作 `types.name`（EFT/ESI 口径）、`zh` 作 `types.name_zh`；
- 字段**部分记录缺省**，缺省处理：`metaLevel`→0、`volume`→0.0、`published`→false、
  `display_name`→回退 `name`。

产物：`noobbaozi/data/noobbaozi-<build>.sqlite3`（gitignore；索引库是构建产物，见设计文档 §7.1）。
下载的 zip 保留在 `noobbaozi/data/` 作缓存，`force=True` 重建时不必重新下载 95 MiB。

依赖约束（开发规范 §2）：只依赖标准库（`sqlite3` / `urllib` / `zipfile` / `json`）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

from noobbaozi.config.paths import SDE_SCHEMA_PATH, db_path, zip_path
from noobbaozi.sde.modes import write_modes

#: 取最新 build 号的元数据文件（JSON object，字段 buildNumber）。
LATEST_BUILD_URL: Final[str] = (
    "https://developers.eveonline.com/static-data/tranquility/latest.jsonl"
)

#: SDE zip 的按 build 拼装地址（实测 latest 简写会 302 到这里）。
SDE_ZIP_URL_TEMPLATE: Final[str] = (
    "https://developers.eveonline.com/static-data/tranquility/"
    "eve-online-static-data-{build}-jsonl.zip"
)

#: 只读这 5 个成员（其余 97 个成员与本项目无关，尤其是巨大的地图数据）。
REQUIRED_MEMBERS: Final[tuple[str, ...]] = (
    "types.jsonl",
    "groups.jsonl",
    "categories.jsonl",
    "dogmaAttributes.jsonl",
    "dogmaUnits.jsonl",
)

#: 包内自带 build 信息的成员，用于交叉校验缓存版本。
BUILD_INFO_MEMBER: Final[str] = "_sde.jsonl"

_USER_AGENT: Final[str] = (
    "noobbaozi/0.1 (SDE index builder; python-urllib; +local research tool)"
)
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": _USER_AGENT,
    "Accept": "*/*",
    "Accept-Encoding": "identity",
}
_TIMEOUT_S: Final[float] = 180.0
_CHUNK_SIZE: Final[int] = 1 << 20
_PROGRESS_STEP: Final[int] = 10

_INSERT_CATEGORIES: Final[str] = (
    "INSERT INTO categories(category_id, name, published) VALUES (?, ?, ?)"
)
_INSERT_GROUPS: Final[str] = (
    "INSERT INTO groups(group_id, name, category_id) VALUES (?, ?, ?)"
)
_INSERT_TYPES: Final[str] = (
    "INSERT INTO types(type_id, name, name_lower, name_zh, group_id, meta_level,"
    " published, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_UNITS: Final[str] = (
    "INSERT INTO units(unit_id, name, display_name) VALUES (?, ?, ?)"
)
_INSERT_ATTRIBUTES: Final[str] = (
    "INSERT INTO attributes(attribute_id, name, display_name, description, unit_id,"
    " high_is_good, default_value, published) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def _log(message: str) -> None:
    """构建过程日志（规范：下载进度与耗时打到 stdout）。"""
    print(f"[sde] {message}", flush=True)


# ---- 下载 -------------------------------------------------------------------


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_latest_build() -> tuple[int, str | None]:
    """取当前 TQ 的 SDE build 号。

    Returns:
        `(buildNumber, releaseDate)`。字段名是 **`buildNumber`**，不是 `sde`
        （实测 `latest.jsonl` 内容：
        `{"_key":"sde","buildNumber":3607705,"releaseDate":"2026-10-12T11:08:38Z"}`）。

    Raises:
        RuntimeError: 响应里没有 `buildNumber`（格式变了 → 停下来重看结构，不要猜）。
    """
    payload = _fetch_json(LATEST_BUILD_URL)
    if not isinstance(payload, dict) or "buildNumber" not in payload:
        raise RuntimeError(
            f"{LATEST_BUILD_URL} 里没有 buildNumber 字段；实际收到: {str(payload)[:300]}"
        )
    return int(payload["buildNumber"]), payload.get("releaseDate")


def _download(url: str, destination: Path) -> int:
    """流式下载到 `destination`（先写 `.part` 再原子改名）。返回字节数。"""
    partial = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers=_HEADERS)
    started = time.perf_counter()
    written = 0
    next_tick = _PROGRESS_STEP
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        total = int(response.headers.get("Content-Length") or 0)
        with partial.open("wb") as handle:
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if total:
                    percent = written * 100 // total
                    if percent >= next_tick:
                        _log(
                            f"  下载 {percent:3d}%  {written / 1024**2:6.1f}/"
                            f"{total / 1024**2:.1f} MiB  {time.perf_counter() - started:.1f}s"
                        )
                        next_tick += _PROGRESS_STEP
    os.replace(partial, destination)
    elapsed = time.perf_counter() - started
    speed = written / 1024**2 / elapsed if elapsed > 0 else 0.0
    _log(f"  下载完成 {written / 1024**2:.1f} MiB，耗时 {elapsed:.1f}s（{speed:.1f} MiB/s）")
    return written


def ensure_zip(build: int) -> tuple[Path, int]:
    """确保 `build` 对应的 SDE zip 在本地缓存里；返回 `(path, size_bytes)`。"""
    target = zip_path(build)
    if target.is_file() and target.stat().st_size > 0:
        size = target.stat().st_size
        _log(f"复用缓存的 SDE zip {target.name}（{size / 1024**2:.1f} MiB）")
        return target, size
    url = SDE_ZIP_URL_TEMPLATE.format(build=build)
    _log(f"下载 SDE zip: {url}")
    size = _download(url, target)
    return target, size


def read_archive_build(archive: Path) -> tuple[int | None, str | None]:
    """读 zip 内 `_sde.jsonl` 的 build 信息，用于交叉校验缓存版本。"""
    try:
        with zipfile.ZipFile(archive) as handle:
            member = _find_member(handle, BUILD_INFO_MEMBER)
            if member is None:
                return None, None
            with handle.open(member) as stream:
                record = json.loads(stream.readline().decode("utf-8"))
        return int(record["buildNumber"]), record.get("releaseDate")
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None, None


# ---- 解析 -------------------------------------------------------------------


def _find_member(handle: zipfile.ZipFile, basename: str) -> str | None:
    """按 basename 找 zip 成员（容忍成员被放进子目录）。"""
    for name in handle.namelist():
        if name.replace("\\", "/").rsplit("/", 1)[-1] == basename:
            return name
    return None


def _resolve_members(handle: zipfile.ZipFile) -> dict[str, str]:
    """定位 5 个必需成员；缺一个就报错（不静默降级）。"""
    resolved: dict[str, str] = {}
    for basename in REQUIRED_MEMBERS:
        member = _find_member(handle, basename)
        if member is None:
            raise RuntimeError(
                f"SDE zip 缺少必需成员 {basename}；实际成员示例 "
                f"{sorted(handle.namelist())[:20]} ...（共 {len(handle.namelist())} 个）"
            )
        resolved[basename] = member
    return resolved


def _iter_jsonl(handle: zipfile.ZipFile, member: str) -> Iterator[dict[str, Any]]:
    """按行流式读 zip 内的 JSONL 成员（不解压整包，内存恒定）。"""
    with handle.open(member) as stream:
        for raw in stream:
            if not raw.strip():
                continue
            yield json.loads(raw)


def _localized(value: Any, language: str = "en") -> str | None:
    """SDE 的名称/描述是多语言字典；缺 `en` 时退回第一个非空值，也兼容纯字符串。"""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        preferred = value.get(language)
        if isinstance(preferred, str) and preferred:
            return preferred
        for candidate in value.values():
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _type_rows(
    handle: zipfile.ZipFile, member: str
) -> Iterator[tuple[int, str, str, str | None, int | None, int, int, float]]:
    """`types.jsonl` → 索引行。

    `types.name` 取英文名（契约里的类型名即 EFT/ESI 口径），中文名单独放 `name_zh`
    供中文检索；`name_lower` 供大小写不敏感的精确/前缀匹配。
    """
    for record in _iter_jsonl(handle, member):
        type_id = int(record["_key"])
        name = _localized(record.get("name")) or f"<type {type_id}>"
        yield (
            type_id,
            name,
            name.lower(),
            _localized(record.get("name"), "zh"),
            None if record.get("groupID") is None else _as_int(record["groupID"]),
            _as_int(record.get("metaLevel"), 0),
            int(bool(record.get("published", False))),
            _as_float(record.get("volume"), 0.0),
        )


def _group_rows(
    handle: zipfile.ZipFile, member: str
) -> Iterator[tuple[int, str, int | None]]:
    for record in _iter_jsonl(handle, member):
        group_id = int(record["_key"])
        yield (
            group_id,
            _localized(record.get("name")) or f"<group {group_id}>",
            None if record.get("categoryID") is None else _as_int(record["categoryID"]),
        )


def _category_rows(handle: zipfile.ZipFile, member: str) -> Iterator[tuple[int, str, int]]:
    for record in _iter_jsonl(handle, member):
        category_id = int(record["_key"])
        yield (
            category_id,
            _localized(record.get("name")) or f"<category {category_id}>",
            int(bool(record.get("published", False))),
        )


def _unit_rows(
    handle: zipfile.ZipFile, member: str
) -> Iterator[tuple[int, str | None, str | None]]:
    for record in _iter_jsonl(handle, member):
        yield (
            int(record["_key"]),
            record.get("name"),
            _localized(record.get("displayName")),
        )


def _attribute_rows(
    handle: zipfile.ZipFile, member: str
) -> Iterator[tuple[int, str | None, str | None, str | None, int | None, int, float, int]]:
    for record in _iter_jsonl(handle, member):
        name = record.get("name")
        description = record.get("description")
        yield (
            int(record["_key"]),
            name,
            _localized(record.get("displayName")) or name,
            description if isinstance(description, str) else None,
            None if record.get("unitID") is None else _as_int(record["unitID"]),
            int(bool(record.get("highIsGood", False))),
            _as_float(record.get("defaultValue"), 0.0),
            int(bool(record.get("published", False))),
        )


#: 成员 basename → 行生成器。`_populate` 用这张表保持循环单薄。
_ROW_BUILDERS: Final[dict[str, Any]] = {
    "categories.jsonl": _category_rows,
    "groups.jsonl": _group_rows,
    "types.jsonl": _type_rows,
    "dogmaUnits.jsonl": _unit_rows,
    "dogmaAttributes.jsonl": _attribute_rows,
}

#: (中文标签, 表名, zip 成员 basename, 插入语句)；顺序即写入顺序。
_INSERTS: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("分类", "categories", "categories.jsonl", _INSERT_CATEGORIES),
    ("组", "groups", "groups.jsonl", _INSERT_GROUPS),
    ("类型", "types", "types.jsonl", _INSERT_TYPES),
    ("单位", "units", "dogmaUnits.jsonl", _INSERT_UNITS),
    ("属性", "attributes", "dogmaAttributes.jsonl", _INSERT_ATTRIBUTES),
)


# ---- 建库 -------------------------------------------------------------------


def _cached_build(target: Path) -> int | None:
    """读已有索引库的 `sde_build`；文件不存在/不是本项目库时返回 `None`。"""
    try:
        with sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'sde_build'").fetchone()
        return None if row is None else int(row[0])
    except (sqlite3.Error, ValueError, OSError):
        return None


def _populate(
    conn: sqlite3.Connection,
    archive: Path,
    *,
    build: int,
    release: str | None,
    source_url: str,
) -> dict[str, int]:
    """把 zip 里的 5 个成员 + 模式映射写进已建好 schema 的连接，返回各表行数。"""
    counts: dict[str, int] = {}
    with zipfile.ZipFile(archive) as handle:
        members = _resolve_members(handle)
        for label, table, member_basename, statement in _INSERTS:
            started = time.perf_counter()
            builder = _ROW_BUILDERS[member_basename]
            conn.executemany(statement, builder(handle, members[member_basename]))
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            _log(
                f"  {label} {table:10s} {counts[table]:6d} 行  "
                f"{time.perf_counter() - started:.2f}s"
            )

    started = time.perf_counter()
    counts["modes"], warnings = write_modes(conn)
    _log(f"  模式 modes      {counts['modes']:6d} 行  {time.perf_counter() - started:.2f}s")
    for warning in warnings:
        _log(f"  WARN {warning}")

    meta = {
        "sde_build": str(build),
        "sde_release_date": release or "",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "schema_version": "1",
        "source_url": source_url,
        "source_zip": archive.name,
        "builder": "noobbaozi.sde.ingest",
    }
    conn.executemany(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", sorted(meta.items())
    )
    return counts


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("types", "groups", "categories", "attributes", "units", "modes")
    }


def build_index(build: int | None = None, force: bool = False) -> Path:
    """构建（或复用）SDE 索引库，返回库文件路径。

    Args:
        build: 目标 build 号；`None` 表示从 `latest.jsonl` 取当前最新。
        force: `True` 时即使同 build 的库已存在也重建（zip 走本地缓存，不重新下载）。

    幂等性：同 build 且库已存在 → 直接返回，不下载任何东西；build 变化 → 新建对应文件
    （文件名带 build 号，多 build 并存、可回滚，不会覆盖旧库）。

    Raises:
        RuntimeError: zip 缺成员、或缓存 zip 内 `_sde.jsonl` 的 buildNumber 与目标不符。
        urllib.error.HTTPError: 下载被拒（403 等）。
    """
    started_total = time.perf_counter()
    if build is None:
        build, release = fetch_latest_build()
        _log(f"最新 build = {build}（release {release}）")
    else:
        release = None
        _log(f"指定 build = {build}")

    target = db_path(build)
    if not force and target.is_file():
        existing = _cached_build(target)
        if existing == build:
            _log(
                f"复用已有索引库 {target}（build {build}，"
                f"{target.stat().st_size / 1024**2:.1f} MiB），"
                f"耗时 {time.perf_counter() - started_total:.2f}s"
            )
            return target
        _log(f"已有库 build={existing} 与目标 build={build} 不一致 → 重建")

    archive, archive_size = ensure_zip(build)
    archive_build, archive_release = read_archive_build(archive)
    if archive_build is not None and archive_build != build:
        raise RuntimeError(
            f"缓存 zip {archive} 的 buildNumber={archive_build} 与目标 build={build} 不符；"
            "删掉该缓存文件后重试"
        )
    release = release or archive_release
    source_url = SDE_ZIP_URL_TEMPLATE.format(build=build)

    temporary = target.with_name(target.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    _log(f"建库 → {target}（源 zip {archive_size / 1024**2:.1f} MiB）")
    conn = sqlite3.connect(temporary)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.executescript(SDE_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        _populate(conn, archive, build=build, release=release, source_url=source_url)
        conn.commit()
        conn.execute("PRAGMA optimize")
        final_counts = _table_counts(conn)
    except BaseException:
        conn.close()
        if temporary.exists():
            temporary.unlink()
        raise
    else:
        conn.close()

    os.replace(temporary, target)
    _log(
        f"完成 build {build} → {target}（{target.stat().st_size / 1024**2:.1f} MiB）"
        f" 总耗时 {time.perf_counter() - started_total:.1f}s；"
        + " / ".join(f"{key}={value}" for key, value in final_counts.items())
    )
    return target


# ---- CLI --------------------------------------------------------------------


def inspect(archive: Path) -> None:
    """打印 zip 成员与 5 个成员的**前两行**——结构没确认过就不许写解析代码。"""
    with zipfile.ZipFile(archive) as handle:
        members = _resolve_members(handle)
        _log(f"zip 成员共 {len(handle.namelist())} 个；必需成员 {list(members)}")
        for basename, member in members.items():
            info = handle.getinfo(member)
            _log(f"{basename}: 压缩 {info.compress_size:,} B / 原始 {info.file_size:,} B")
            with handle.open(member) as stream:
                for line_no in range(2):
                    raw = stream.readline()
                    if not raw:
                        break
                    _log(f"  第 {line_no} 行（{len(raw)} B）: {raw.decode('utf-8', 'replace')[:400]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m noobbaozi.sde.ingest",
        description="构建 noobbaozi SDE 索引库（下载 → 解析 → SQLite）",
    )
    parser.add_argument("--build", type=int, default=None, help="目标 SDE build 号，默认取最新")
    parser.add_argument("--force", action="store_true", help="同 build 也重建（zip 用缓存）")
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="只下载/打开 zip 并打印成员结构与前两行，不建库",
    )
    args = parser.parse_args(argv)

    if args.inspect:
        build = args.build
        if build is None:
            build, _release = fetch_latest_build()
            _log(f"最新 build = {build}")
        archive, _size = ensure_zip(build)
        inspect(archive)
        return 0

    print(build_index(build=args.build, force=args.force))
    return 0


if __name__ == "__main__":
    sys.exit(main())
