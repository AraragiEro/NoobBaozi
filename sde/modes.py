"""T3D/T3C 模式映射（group 1306）。

实测结论（`PROGRESS.md` §3 第 3 条，`spike/mode_probe*.py`）：

- 模式**不是船的属性，而是独立的 type**，全部位于 **group 1306**（本组内 `published=false`）；
- 命名约定 `<ShipName> <Mode> Mode`：
  `Hecate Defense Mode` = 35686 / `Hecate Propulsion Mode` = 35687 /
  `Hecate Sharpshooter Mode` = 35688，所属船 Hecate = 35683；
- **没有「船 → 可用模式」的显式链接属性**（模式 type 只有 volume / mass / hp / techLevel / meta
  与模式修饰项），只能靠 group 1306 全表 + 命名约定反解；
- 本 build（3607705）实测：group 1306 共 **18** 个成员，反解出 **6** 艘船
  （Confessor / Svipul / Jackdaw / Hecate / Skua / Anhinga），每艘 3 个模式；
  其中 Anhinga 的三态命名为 `Primary` / `Secondary` / `Tertiary`（不叫 Defense 等）。

本模块只负责**解析与写表**；查询 API（`modes_for_ship`）在 `noobbaozi/sde/index.py`。
依赖约束：只依赖标准库（§2 数据层）。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Final

#: 模式 type 所在的 group（实测值，勿改；改了必须重跑 spike/mode_probe4.py）。
MODE_GROUP_ID: Final[int] = 1306

#: `<ShipName> <Mode> Mode`。ShipName 用非贪婪匹配，Mode 限定为单个驼峰单词，
#: 这样 `Anhinga Primary Mode` → (Anhinga, Primary) 也能正确反解。
MODE_TYPE_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<ship>.+?) (?P<mode>[A-Z][A-Za-z]*) Mode$"
)


@dataclass(frozen=True, slots=True)
class ModeEntry:
    """一条模式映射。"""

    mode_type_id: int
    """模式 type 的 type_id（写 fit 时用作 `ship.mode`）。"""

    ship_type_id: int
    """所属船的 type_id。"""

    mode_name: str
    """剥出的模式名（`Defense` / `Propulsion` / `Sharpshooter` / `Primary` ...）。"""

    mode_type_name: str
    """模式 type 的完整名称（`Hecate Defense Mode`），便于人工核对。"""


def parse_mode_type_name(name: str) -> tuple[str, str] | None:
    """把 `<ShipName> <Mode> Mode` 拆成 `(ship_name, mode_name)`。

    不符合命名约定时返回 `None`（**不猜**：宁可漏掉，也不给出错误映射）。

    >>> parse_mode_type_name("Hecate Defense Mode")
    ('Hecate', 'Defense')
    >>> parse_mode_type_name("Hecate") is None
    True
    """
    matched = MODE_TYPE_NAME_RE.match(name)
    if matched is None:
        return None
    return matched.group("ship"), matched.group("mode")


def collect_modes(conn: sqlite3.Connection) -> tuple[list[ModeEntry], list[str]]:
    """从索引库（`types` 表已填充）里反解全部模式映射。

    Returns:
        `(entries, warnings)`：`warnings` 里是反解失败/歧义的情况，调用方负责打印；
        歧义时该模式被**跳过而不是猜一个**（规范 §4.3 / 红线 N2）。
    """
    # 全表建一次「英文名 → [(type_id, published)]」，避免逐行查库。
    by_name: dict[str, list[tuple[int, bool]]] = {}
    for type_id, name, published in conn.execute(
        "SELECT type_id, name, published FROM types WHERE group_id IS NOT ?",
        (MODE_GROUP_ID,),
    ):
        by_name.setdefault(name, []).append((type_id, bool(published)))

    mode_rows = conn.execute(
        "SELECT type_id, name FROM types WHERE group_id = ? ORDER BY type_id",
        (MODE_GROUP_ID,),
    ).fetchall()

    entries: list[ModeEntry] = []
    warnings: list[str] = []
    for type_id, type_name in mode_rows:
        parsed = parse_mode_type_name(type_name)
        if parsed is None:
            warnings.append(
                f"模式 type {type_id} '{type_name}' 不符合 <ShipName> <Mode> Mode，已跳过"
            )
            continue
        ship_name, mode_name = parsed
        candidates = by_name.get(ship_name, [])
        published = [candidate for candidate in candidates if candidate[1]]
        chosen = published or candidates
        if len(chosen) != 1:
            warnings.append(
                f"模式 '{type_name}' 反解到的船名 '{ship_name}' 有 "
                f"{len(chosen)} 个候选 {chosen}，歧义故跳过（不猜）"
            )
            continue
        entries.append(
            ModeEntry(
                mode_type_id=type_id,
                ship_type_id=chosen[0][0],
                mode_name=mode_name,
                mode_type_name=type_name,
            )
        )
    return entries, warnings


def write_modes(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    """解析并写入 `modes` 表（先清空再写，可重复执行）。

    Returns:
        `(写入行数, warnings)`。
    """
    entries, warnings = collect_modes(conn)
    conn.execute("DELETE FROM modes")
    conn.executemany(
        "INSERT OR REPLACE INTO modes(mode_type_id, ship_type_id, mode_name) VALUES (?, ?, ?)",
        [(e.mode_type_id, e.ship_type_id, e.mode_name) for e in entries],
    )
    return len(entries), warnings
