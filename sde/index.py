"""SDE 索引库查询 API（L1 数据层）。

严格实现 `noobbaozi/contracts.py` 末尾的 `Index` 契约（方法名/参数/返回类型一字不改）：
`open` / `search_types` / `get_type` / `types_in_group` / `modes_for_ship` /
`attribute` / `stats` / `close`。

**契约外扩展**：`skills()`（全部已发布技能的 `type_id`），单独放在
「契约外扩展」小节，不改动上面任何一个契约方法；存在的理由是 `engine.assumptions`
需要「全部技能」才能实现「全 4 级 + 补最低要求」的技能档约定（T-12）。

设计要点：

1. **只读**：连接用 `file:...?mode=ro` 打开并加 `PRAGMA query_only`，
   误写会在 SQLite 层直接报错，不会悄悄改掉构建产物。
2. **线程安全**：`sqlite3.Connection` 本身不允许跨线程共用，所以 `Index` 为
   **每个线程各开一条连接**（`threading.local` + `check_same_thread=False`），
   实例可以放心在线程池里共享；`close()` 用锁收集并关闭所有线程的连接。
3. **`close()` 幂等**：重复调用不报错；关闭后再查询会抛 `RuntimeError`。
4. **搜索三级匹配**：精确 → 前缀 → 包含，大小写不敏感（英文名按预计算
   `name_lower` 列比较；中文名 `name_zh` 一并参与，供中文检索）。
5. **不猜**：歧义（同名多版本）由调用方决定怎么用，索引层只按
   `published` 优先、`meta_level` 次之的确定性顺序返回，不做语义判定。

依赖约束（开发规范 §2）：只用标准库 `sqlite3` + `noobbaozi.contracts`，外加**同层**的
`noobbaozi.sde.attribute_dict`（缩放系数由它提供，禁止 `sde` 反向依赖 `engine`）。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Final

from noobbaozi.contracts import AttributeRow, IndexStats, TypeRow
from noobbaozi.sde.attribute_dict import scale_of

#: 未登记属性的兜底缩放系数，与 `sde/attribute_dict.scale_of()` 对未知属性的返回值
#: 同义（原值）。真实缩放表归 `config/attributes.py` + `sde/attribute_dict.py`，
#: `attribute()` 交给 `scale_of()` 决定，**不再恒返回本常量**。
DEFAULT_SCALE: Final[float] = 1.0

_TYPES_SELECT: Final[str] = """
    SELECT t.type_id   AS type_id,
           t.name      AS name,
           t.group_id  AS group_id,
           g.name      AS group_name,
           t.meta_level AS meta_level,
           t.published AS published,
           t.volume    AS volume
      FROM types t
      LEFT JOIN groups g ON g.group_id = t.group_id
"""

#: 精确优先 + 前缀 + 包含 的三级匹配；LIKE 的 `% _ \\` 由调用方转义并指定 ESCAPE。
_SEARCH_SQL: Final[str] = (
    _TYPES_SELECT
    + """
     WHERE t.name_lower = :q
        OR t.name_lower LIKE :prefix ESCAPE '\\'
        OR t.name_lower LIKE :contains ESCAPE '\\'
        OR t.name_zh = :raw
        OR t.name_zh LIKE :zh_prefix ESCAPE '\\'
        OR t.name_zh LIKE :zh_contains ESCAPE '\\'
     ORDER BY CASE
                WHEN t.name_lower = :q OR t.name_zh = :raw THEN 0
                WHEN t.name_lower LIKE :prefix ESCAPE '\\'
                  OR t.name_zh LIKE :zh_prefix ESCAPE '\\' THEN 1
                ELSE 2
              END,
              t.published DESC,
              t.meta_level DESC,
              length(t.name) ASC,
              t.type_id ASC
     LIMIT :limit
"""
)


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符，使 `%`/`_` 按字面匹配（EVE 名字里常有 `_`）。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_type_row(row: sqlite3.Row) -> TypeRow:
    """sqlite3.Row → 契约 `TypeRow`（published 由 0/1 转 bool）。"""
    return TypeRow(
        type_id=row["type_id"],
        name=row["name"],
        group_id=row["group_id"],
        group_name=row["group_name"],
        meta_level=row["meta_level"],
        published=bool(row["published"]),
        volume=row["volume"],
    )


class Index:
    """SDE 索引库的只读查询入口。用 `Index.open(path)` 构造。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._closed = False

    # ---- 生命周期 ---------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> "Index":
        """打开索引库。

        Args:
            path: `noobbaozi/data/noobbaozi-<build>.sqlite3`（用 `config.paths.db_path()` 取）。

        Raises:
            FileNotFoundError: 文件不存在（提示先跑 `sde.ingest.build_index()`）。
            sqlite3.DatabaseError: 文件不是可用的 SQLite 库。
        """
        resolved = Path(path)
        if not resolved.is_file():
            raise FileNotFoundError(
                f"索引库不存在: {resolved}；请先运行 "
                '`python -c "from noobbaozi.sde.ingest import build_index; build_index()"`'
            )
        index = cls(resolved)
        # 立刻取一次连接 + 读一次 meta，尽早把"不是 noobbaozi 索引库"暴露成异常。
        index._connection().execute("SELECT COUNT(*) FROM meta").fetchone()
        return index

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _connection(self) -> sqlite3.Connection:
        """取当前线程的连接（懒建）。线程安全见模块 docstring 第 2 条。"""
        if self._closed:
            raise RuntimeError("Index 已 close()，不能继续查询")
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            # mode=ro：只读打开，写操作直接失败；as_uri() 兼容 Windows 盘符路径。
            conn = sqlite3.connect(
                f"{self._path.as_uri()}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
        return conn

    def close(self) -> None:
        """关闭所有线程的连接。**幂等**：重复调用不报错。"""
        with self._lock:
            if self._closed and not self._connections:
                return
            connections, self._connections = self._connections, []
            self._closed = True
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:  # 已被回收/重复关闭：忽略，幂等语义优先
                pass
        self._local = threading.local()

    # ---- 查询 -------------------------------------------------------------

    def search_types(self, query: str, limit: int = 10) -> list[TypeRow]:
        """按名搜类型：精确优先 → 前缀 → 包含，大小写不敏感。

        - 英文名（`types.name_lower`）与中文名（`types.name_zh`）同时参与；
        - 同级内排序：`published` 优先 → `meta_level` 高优先 → 名字短优先 → `type_id` 小优先，
          完全确定（同样的库同样的查询永远给同样的顺序）；
        - 空查询或 `limit <= 0` 返回空列表。

        Examples:
            `search_types("Hecate", 3)` 首位是 35683（精确命中），
            其后才是 `Hecate Defense Mode` 之类的包含命中。
        """
        needle = (query or "").strip()
        if not needle or limit <= 0:
            return []
        escaped = _escape_like(needle)
        params = {
            "q": needle.lower(),
            "raw": needle,
            "prefix": f"{escaped}%",
            "contains": f"%{escaped}%",
            "zh_prefix": f"{escaped}%",
            "zh_contains": f"%{escaped}%",
            "limit": limit,
        }
        rows = self._connection().execute(_SEARCH_SQL, params).fetchall()
        return [_to_type_row(row) for row in rows]

    def get_type(self, type_id: int) -> TypeRow | None:
        """按 `type_id` 取一个类型；不存在返回 `None`。"""
        row = self._connection().execute(
            _TYPES_SELECT + " WHERE t.type_id = ?", (type_id,)
        ).fetchone()
        return None if row is None else _to_type_row(row)

    def types_in_group(self, group_id: int) -> list[TypeRow]:
        """列出某 group 的全部类型（含 `published=false` 的，如模式 type）。

        顺序确定：`published` 优先 → `name` → `type_id`。
        """
        rows = self._connection().execute(
            _TYPES_SELECT
            + " WHERE t.group_id = ? ORDER BY t.published DESC, t.name ASC, t.type_id ASC",
            (group_id,),
        ).fetchall()
        return [_to_type_row(row) for row in rows]

    def modes_for_ship(self, ship_type_id: int) -> list[TypeRow]:
        """某艘船可切换的模式（T3D/T3C）。无模式返回空列表。

        映射来自 `modes` 表（`sde/modes.py` 用 group 1306 + 命名约定反解写入）。
        顺序按 `type_id` 升序，稳定可复现。
        """
        rows = self._connection().execute(
            _TYPES_SELECT
            + """
              JOIN modes m ON m.mode_type_id = t.type_id
             WHERE m.ship_type_id = ?
             ORDER BY t.type_id ASC
            """,
            (ship_type_id,),
        ).fetchall()
        return [_to_type_row(row) for row in rows]

    def attribute(self, attribute_id: int) -> AttributeRow | None:
        """按 `attribute_id` 取一条 dogma 属性；不存在返回 `None`。

        - `unit`：SDE 的 dogma 单位**量纲名**（`Length` / `Mass` / `Milliseconds` / `Volume` ...），
          不是显示缩写（`m` / `kg` / `s`）——缩写存在 `units.display_name`，本契约不透出。
          物理换算（毫秒 → 秒、×1000 → 物理值）在属性字典层 `to_physical()` 做。
        - `display_name`：SDE 没有 displayName 时回退到 `name`（约 1620/2871 条没有）。
        - `scale`：来自属性字典层 `sde/attribute_dict.scale_of()`（表在 `config/attributes.py`）：
          `1.0` 原值 / `0.001` 存储值是毫秒 / `1000.0` 存储值被放大 1000 倍。
          未登记的属性 `scale_of()` 返回 `DEFAULT_SCALE`（1.0）且不抛异常。
        """
        row = self._connection().execute(
            """
            SELECT a.attribute_id AS attribute_id,
                   a.name         AS name,
                   COALESCE(a.display_name, a.name) AS display_name,
                   u.name         AS unit,
                   a.high_is_good AS high_is_good
              FROM attributes a
              LEFT JOIN units u ON u.unit_id = a.unit_id
             WHERE a.attribute_id = ?
            """,
            (attribute_id,),
        ).fetchone()
        if row is None:
            return None
        return AttributeRow(
            attribute_id=row["attribute_id"],
            name=row["name"],
            display_name=row["display_name"],
            unit=row["unit"],
            scale=scale_of(attribute_id),
            high_is_good=bool(row["high_is_good"]),
        )

    def stats(self) -> IndexStats:
        """索引库规模统计 + `sde_build`（来自 `meta` 表）。"""
        conn = self._connection()
        row = conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM types)      AS types,
                   (SELECT COUNT(*) FROM groups)     AS groups,
                   (SELECT COUNT(*) FROM attributes) AS attributes,
                   (SELECT COUNT(*) FROM modes)      AS modes
            """
        ).fetchone()
        meta_row = conn.execute(
            "SELECT value FROM meta WHERE key = 'sde_build'"
        ).fetchone()
        sde_build = int(meta_row["value"]) if meta_row is not None else 0
        return IndexStats(
            sde_build=sde_build,
            types=row["types"],
            groups=row["groups"],
            attributes=row["attributes"],
            modes=row["modes"],
        )

    # ---- 契约外扩展（engine.assumptions 专用；不在 contracts.Index 里）------

    def skills(self) -> list[int]:
        """列出 SDE 里**全部已发布技能**的 `type_id`（升序）。

        **这是契约外扩展方法**：`contracts.Index` 只描述查询原语，没有它。
        `engine.assumptions.derive_skill_tier` 需要「全部技能」这个集合来表达
        「全 4 级 + 补最低要求」的约定（T-12 修正，PROGRESS §5 欠账 18）。

        - 技能 = `groups.category_id = 16`（Skill）下的类型；
        - 只返回 `published = 1` 的（未发布类型不是玩家可训练技能，T-12 口径）；
        - 顺序按 `type_id` 升序，稳定可复现。
        """
        rows = self._connection().execute(
            """
            SELECT t.type_id AS type_id
              FROM types t
              JOIN groups g ON g.group_id = t.group_id
             WHERE g.category_id = 16
               AND t.published = 1
             ORDER BY t.type_id ASC
            """
        ).fetchall()
        return [int(row["type_id"]) for row in rows]

    # ---- 杂项 -------------------------------------------------------------

    @property
    def path(self) -> Path:
        """索引库文件路径。"""
        return self._path

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return f"Index({self._path!s}, {state})"
