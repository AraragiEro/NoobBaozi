"""引擎适配层（L2）：把 `eveshipfit-dogma-engine` 包装成 `contracts.Engine` 契约。

本层只做三件事：**加载、透传、缓存**。不重写 dogma、不改写数值、不解析结果。

为什么是这样写的（每条都对应一个实测过的坑）：

1. **SDE 每进程只能加载一次**。`dogma.load_sde_from_file()` 第二次会抛
   `RuntimeError: SDE is already loaded`（实测）。所以这里用「模块级锁 + 标志位」
   做单例守卫：`Engine()` 构造多少次，真正加载只发生一次；若引擎已被别处
   （例如 `spike/*.py`）加载过，则复用并只取 build 号，不再尝试加载。
2. **`calculate` 返回原始 dict**。毫秒 / ×1000 缩放的换算属于
   `project/units.py`（规范 §4.1、红线 N5），适配层一律不改写数值。
3. **缓存必须挂在实例上**。参数是 `dict`（不可哈希），`functools.lru_cache` 用不了；
   这里用 `json.dumps(fit, sort_keys=True)` 做 key 的极简 LRU。
   只缓存 `sources=False and validate=False` 的调用（另外两种结果形状不同，不混存）。
4. **无网络、无随机性**（红线 N1）：本模块除读本地 SDE 文件外不做任何 I/O。

`load_dna` / `load_esf` / `load_esi_fitting` / `load_killmail` 契约里的 `Engine`
没有要求，但 `engine/normalize.py` 需要它们，且它们同样必须在 SDE 已加载的前提下
调用，所以在这里提供一层薄包装（顺带把「只加载一次」的保证收敛到本模块）。
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path

import esf_dogma_engine as dogma
import eveshipfit_sde

from evefit import contracts

CACHE_MAX_ENTRIES = 256
"""`Engine` 实例的 LRU 容量。单次计算 0.33 ms（实测），256 条足以覆盖六视角变体。"""

SPEC_VERSION = contracts.SPEC_VERSION
ENGINE_VERSION = contracts.ENGINE_VERSION

# ---- 模块级 SDE 单例守卫（锁 + 标志位 + 计数）----

_SDE_LOCK = threading.Lock()
_sde_loaded = False
_sde_build: int | None = None
_sde_load_count = 0


def sde_loaded() -> bool:
    """SDE 是否已在本进程内加载完成（含别处已加载的复用情形）。"""
    return _sde_loaded


def sde_load_count() -> int:
    """本模块真正调用 `load_sde_from_file` 并成功的次数；正常情况恒为 0 或 1。

    测试用它断言「重复构造 `Engine()` 不会二次加载」。
    """
    return _sde_load_count


def _ensure_sde_loaded(sde_path: str | Path | None = None) -> int:
    """保证 SDE 已加载，返回 SDE build 号。幂等，线程安全。"""
    global _sde_loaded, _sde_build, _sde_load_count

    with _SDE_LOCK:
        if _sde_loaded:
            assert _sde_build is not None
            return _sde_build

        path = str(sde_path) if sde_path is not None else str(eveshipfit_sde.sde_path())
        try:
            dogma.load_sde_from_file(path)
            _sde_load_count += 1
        except RuntimeError:
            # 引擎已在别处加载过（同进程只允许一次）→ 复用，不再尝试。
            pass

        _sde_build = int(eveshipfit_sde.build_number())
        _sde_loaded = True
        return _sde_build


# ---- 薄包装：必须「SDE 已加载」才能用的读取器 ----


def load_dna(text: str) -> dict:
    """透传 `dogma.load_dna`（DNA = `<船ID>:<模块ID>;<数量>:...::`）。"""
    _ensure_sde_loaded()
    return dogma.load_dna(text)


def load_eft(text: str) -> dict:
    """透传 `dogma.load_eft`（EFT 文本；`%esf/1` 要用 `load_esf`）。"""
    _ensure_sde_loaded()
    return dogma.load_eft(text)


def load_esf(text: str) -> dict:
    """透传 `dogma.load_esf`（只吃 `%esf/1` 格式，踩过的坑 §6.5）。"""
    _ensure_sde_loaded()
    return dogma.load_esf(text)


def load_esi_fitting(fitting: dict) -> dict:
    """透传 `dogma.load_esi_fitting`。"""
    _ensure_sde_loaded()
    return dogma.load_esi_fitting(fitting)


def load_killmail(killmail: dict) -> dict:
    """透传 `dogma.load_killmail`（zkb 等附加字段引擎会忽略，实测可直传）。"""
    _ensure_sde_loaded()
    return dogma.load_killmail(killmail)


def _fit_cache_key(fit: dict) -> str | None:
    """把 fit 变成缓存 key；含不可序列化内容时返回 None（该次调用不缓存）。"""
    try:
        return json.dumps(fit, sort_keys=True)
    except (TypeError, ValueError):
        return None


class Engine:
    """`contracts.py` 里 `engine.adapter.Engine` 契约的实现。

    实例本身很轻：构造只保证 SDE 已加载（全局只加载一次），真正的状态就是那份 LRU。
    """

    def __init__(self, sde_path: str | Path | None = None) -> None:
        # 单例守卫在 `_ensure_sde_loaded` 里；重复构造只会读一次标志位。
        self._sde_build = _ensure_sde_loaded(sde_path)
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

    # ---- 元信息 ----

    def sde_build(self) -> int:
        """当前 SDE build 号（来自 `eveshipfit_sde.build_number()`）。"""
        return self._sde_build

    def meta(self) -> contracts.Meta:
        """版本三元组（规范 §4.5 强制）。引擎版本按契约钉死，升级需同步改 contracts。"""
        return {
            "sde_build": self._sde_build,
            "engine_version": ENGINE_VERSION,
            "spec_version": SPEC_VERSION,
        }

    # ---- 计算 ----

    def calculate(
        self, fit: dict, *, sources: bool = False, validate: bool = False
    ) -> dict:
        """透传 `dogma.calculate`，返回**原始 dict**（结果请视为只读）。

        只有「不带任何选项」的调用走缓存；`sources` / `validate` 的结果形状不同，
        不参与缓存，避免两种形状互相污染。
        """
        if sources or validate:
            return dogma.calculate(
                fit, {"sources": bool(sources), "validate": bool(validate)}
            )

        key = _fit_cache_key(fit)
        if key is not None:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self._cache_hits += 1
                return cached

        self._cache_misses += 1
        result = dogma.calculate(fit)

        if key is not None:
            self._cache[key] = result
            while len(self._cache) > CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)
        return result

    def validate(self, fit: dict) -> list[dict]:
        """返回 `violations` 列表（无违规则空列表，不返回 None）。"""
        result = self.calculate(fit, validate=True)
        return list(result.get("violations") or [])

    # ---- 格式互转 ----

    def save_eft(self, fit: dict) -> str:
        """透传 `dogma.save_eft`。"""
        return dogma.save_eft(fit)

    def load_eft(self, text: str) -> dict:
        """透传 `dogma.load_eft`（EFT 文本；`%esf/1` 要用 `load_esf`）。"""
        _ensure_sde_loaded()
        return dogma.load_eft(text)

    # ---- 缓存管理 ----

    def clear_cache(self) -> None:
        """清空本实例的缓存与命中计数。"""
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> dict[str, int]:
        """缓存观测：条数 / 命中 / 未命中 / 容量。"""
        return {
            "entries": len(self._cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "capacity": CACHE_MAX_ENTRIES,
        }
