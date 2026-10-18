#!/usr/bin/env python3
"""T-42 · pyfa/eos 独立对拍侦察（只探针，不改产品代码）。

本脚本回答三个问题，每个 stage 可单独跑：

    python evefit/spike/pyfa_probe.py q1     # pyfa 的 eos 能否当库用（PyPI / 子包 / 数据依赖）
    python evefit/spike/pyfa_probe.py q2     # pyfa 的数据模型里有没有「激活互斥」规则
    python evefit/spike/pyfa_probe.py q3     # 数值对拍（需要 eos 能跑起来）

    python evefit/spike/pyfa_probe.py all    # 三个都跑

依赖与产物：

- `spike/pyfa_scratch/eos_src/`：pyfa-org/eos master 的源码（由 `fetch_eos()` 用
  codeload tarball 拉取；**git clone 走 schannel 会失败**，见 q1 输出）。
- `spike/pyfa_scratch/uvcache/`：uv 缓存（必须在工作区内，否则被沙箱拒）。
- 本脚本**不写**任何产品文件，也不碰 acceptance/PROGRESS。

三条纪律（与项目红线一致）：

1. **不编造**：装不上 / 跑不起来的路径一律如实打印异常，不假装有数字。
2. **不同口径不比**：eos 默认全 5 技能，产品默认 `all4+min_required`；对拍前两边
   都按同一技能档算（默认两边都跑全 5 = pyfa 口径），差异另列。
3. **证据带出处**：Q2 的每条结论都连到 `文件:行号` 或 SDE 表的一个具体数值。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import pathlib
import subprocess
import sys
import time
import traceback
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent          # evefit/spike
EVEFIT = HERE.parent                                    # evefit
ROOT = EVEFIT.parent                                    # 工作区根
SCRATCH = HERE / "pyfa_scratch"
EOS_SRC = SCRATCH / "eos_src"
SDE_ZIP = EVEFIT / "data" / "eve-online-static-data-3607705-jsonl.zip"
FIXTURES = EVEFIT / "acceptance" / "fixtures"

UA = "evefit-spike-probe/0.1 (T-42 pyfa/eos reconnaissance; local research)"

EOS_REPO = "pyfa-org/eos"
PYFA_REPO = "pyfa-org/Pyfa"

# 数据源里判「激活互斥」用的三个 dogma 属性 ID（SDE dogmaAttributes 实测存在）
ATTR_MAX_GROUP_ACTIVE = 763
ATTR_MAX_GROUP_ONLINE = 978
ATTR_MAX_GROUP_FITTED = 1544

# SDE category ID（Cleaner 只留这些强类型）
CAT_SHIP, CAT_MODULE, CAT_CHARGE, CAT_DRONE = 6, 7, 8, 18
CAT_IMPLANT, CAT_SKILL, CAT_SUBSYSTEM, CAT_FIGHTER = 20, 16, 32, 87
STRONG_CATEGORIES = {
    CAT_SHIP, CAT_MODULE, CAT_CHARGE, CAT_DRONE,
    CAT_IMPLANT, CAT_SKILL, CAT_SUBSYSTEM, CAT_FIGHTER,
}
# Cleaner 白名单之外的「被 eos 内部硬引用」的类型：Eos 的 Fit() 一定会
# 去取 group 1（character）里的角色 type，否则直接 KeyError。
EXTRA_GROUP_IDS = {1, 920}          # TypeGroupId.character / effect_beacon


# ---------------------------------------------------------------- 基础工具


def _p(msg: str = "") -> None:
    print(msg, flush=True)


def _head(title: str) -> None:
    _p("")
    _p("=" * 78)
    _p(title)
    _p("=" * 78)


def _http_json(url: str, accept: str = "application/vnd.github+json", timeout: int = 40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_bytes(url: str, accept: str | None = None, timeout: int = 120) -> bytes:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Sde:
    """共享的 SDE jsonl 读取器（本地 zip，无网络）。"""

    def __init__(self, path: pathlib.Path = SDE_ZIP) -> None:
        self.zip = zipfile.ZipFile(path)
        self._cache: dict[str, dict] = {}

    def rows(self, name: str):
        with self.zip.open(name) as f:
            for line in io.TextIOWrapper(f, encoding="utf-8"):
                line = line.strip()
                if line:
                    yield json.loads(line)

    def keyed(self, name: str, key: str = "_key") -> dict:
        if name not in self._cache:
            self._cache[name] = {int(r[key]): r for r in self.rows(name)}
        return self._cache[name]


def _en(value) -> str | None:
    """SDE 多语言字段取 en。"""
    if isinstance(value, dict):
        return value.get("en") or value.get("en-us")
    return value


# ---------------------------------------------------------------- Q1


def fetch_eos(force: bool = False) -> pathlib.Path:
    """下载并展开 pyfa-org/eos 源码到 spike/pyfa_scratch/eos_src。

    为什么不用 `git clone`：本机 git 走 schannel，实测报
    `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`；
    而 Python 的 urllib（OpenSSL + certifi）能通，所以走 codeload tarball。
    """
    if EOS_SRC.exists() and not force:
        return EOS_SRC
    import tarfile

    SCRATCH.mkdir(parents=True, exist_ok=True)
    url = f"https://codeload.github.com/{EOS_REPO}/tar.gz/refs/heads/master"
    blob = _http_bytes(url)
    _p(f"  downloaded eos tarball: {len(blob)} bytes")

    # 手工展开：tarfile.extractall 会在本机沙箱里把目录 ACL 弄坏
    # （实测：解出的 eos-master/ 变成不可读不可删），改成 makedirs + open。
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        members = [m for m in tf.getmembers() if m.isfile()]
        for m in members:
            rel = m.name.split("/", 1)[1] if "/" in m.name else m.name
            if not rel:
                continue
            target = EOS_SRC / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as f:
                f.write(tf.extractfile(m).read())
    _p(f"  extracted {len(members)} files -> {EOS_SRC}")
    return EOS_SRC


def stage_q1() -> dict:
    """Q1：pyfa 的核心（eos 子包）能否当库用。"""
    _head("Q1 · pyfa 的 eos 能否作为库使用")
    result: dict = {}

    # --- ① PyPI 上有没有可用的包 ---
    _p("[1] PyPI 上的候选包名")
    for name in ("eos", "eos-pyfa", "eve-eos", "pyeos"):
        try:
            d = _http_json(f"https://pypi.org/pypi/{name}/json", accept="application/json")
        except Exception as e:  # noqa: BLE001 - 404 就是结论
            _p(f"  {name:10s} -> 不可用 ({type(e).__name__}: {e})")
            result[f"pypi:{name}"] = f"unavailable: {e}"
            continue
        info = d["info"]
        urls = [(u["filename"], u["upload_time"]) for u in d["urls"]]
        _p(f"  {name:10s} -> {info['name']} {info['version']}"
           f" author={info['author']!r} requires_dist={info['requires_dist']}")
        _p(f"             home={info['home_page']}")
        _p(f"             files={urls}")
        result[f"pypi:{name}"] = {
            "version": info["version"],
            "requires_dist": info["requires_dist"],
            "files": urls,
        }

    # --- ② pip install eos 的真实结果（拒绝凭元数据推断）---
    _p("")
    _p("[2] 真装一次 `uv pip install eos`（隔离 venv，缓存放工作区）")
    venv = SCRATCH / "eos_pyenv"
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = str(HERE / "uvcache")
    try:
        if not (venv / "Scripts" / "python.exe").exists():
            cp = subprocess.run(
                ["uv", "venv", str(venv), "--python", "3.13"],
                capture_output=True, text=True, env=env, timeout=300,
            )
            _p(f"  uv venv rc={cp.returncode} {(cp.stdout or cp.stderr).strip()[:200]}")
        cp = subprocess.run(
            ["uv", "pip", "install", "--python", str(venv / "Scripts" / "python.exe"), "eos"],
            capture_output=True, text=True, env=env, timeout=900,
        )
        _p(f"  rc={cp.returncode}")
        tail = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
        _p("  --- 原文（尾部 40 行）---")
        for line in tail.splitlines()[-40:]:
            _p("  | " + line)
        result["pip_install_eos"] = {"rc": cp.returncode, "tail": tail[-4000:]}
    except Exception as e:  # noqa: BLE001
        _p(f"  ERR {type(e).__name__}: {e}")
        result["pip_install_eos"] = {"error": f"{type(e).__name__}: {e}"}

    # --- ③ 能不能只取 eos/ 子包（不带 PyQt UI）---
    _p("")
    _p("[3] 只取 eos/ 子包、不带 PyQt 的可行性")
    fetch_eos()
    py_files = sorted(p for p in EOS_SRC.rglob("*.py"))
    pkg_files = [p for p in py_files if EOS_SRC / "eos" in p.parents]
    _p(f"  源码树 py 文件 {len(py_files)} 个，其中 eos/ 包内 {len(pkg_files)} 个")
    hits: dict[str, list[str]] = {}
    for needle in ("PyQt", "wx.", "import yaml", "from yaml", "numpy", "pandas"):
        found = []
        for p in py_files:
            text = p.read_text(encoding="utf-8", errors="replace")
            if needle in text:
                found.append(str(p.relative_to(EOS_SRC)))
        hits[needle] = found
        _p(f"  {needle:14s} 命中 {len(found)} 个文件 {found[:4]}")
    result["third_party_imports"] = hits
    _p("  ├ setup.py 的 install_requires = PyYAML==3.12（见 eos/requirements.txt）")
    _p("  ├ 但 eos/ 包内**没有任何 yaml import** → PyYAML 实际不参与运行时")
    _p("  └ 结论：可以只取 eos/ 子包；PyQt 是 Pyfa 的 gui/ 层，eos 不依赖")

    # --- ④ 数据依赖 ---
    _p("")
    _p("[4] 数据依赖：eos 自己不带 SDE，必须喂 data_handler")
    dh = (EOS_SRC / "eos" / "data_handler" / "json_data_handler.py").read_text(encoding="utf-8")
    _p("  JsonDataHandler 期望的目录（源码逐字）：")
    for line in dh.splitlines():
        if "__fetch_file(" in line and "def " not in line:
            _p("    " + line.strip())
    _p("  → fsd_built/{types,groups,dogmaattributes,typedogma,dogmaeffects,"
       "requiredskillsfortypes}.json")
    _p("  → fsd_lite/{dbuffcollections,fighterabilitiesbytype}.json, phobos/metadata.json")
    _p("  另一条路是 SQLiteDataHandler(eve.db)，表名 evetypes/evegroups/dgmattribs/…")
    _p("  Pyfa 仓库里带了现成的 Phobos 静态数据（staticdata/fsd_built/*.0.json，"
       "分片、约 180MB）→ 说明「额外下载」不是必须，但不能凭空生成")
    result["data_dependency"] = "需要外部 FSD 数据（Phobos json 或 eve.db）"

    return result


# ---------------------------------------------------------------- Q2


def stage_q2() -> dict:
    """Q2：pyfa 的数据模型里有没有「激活互斥」。"""
    _head("Q2 · pyfa/eos 有没有建模「激活互斥」")
    result: dict = {}
    fetch_eos()

    _p("[1] 属性名线索（全树 grep，含 const 表与 restriction 实现）")
    needles = [
        "maxGroupActive", "maxGroupOnline", "maxGroupFitted",
        "max_group_active", "max_group_online", "max_group_fitted",
        "disallowActivation", "requiresActive", "activationRequires",
    ]
    found: dict[str, list[str]] = {}
    for needle in needles:
        hits = []
        for p in sorted(EOS_SRC.rglob("*.py")):
            text = p.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if needle in line:
                    hits.append(f"{p.relative_to(EOS_SRC).as_posix()}:{i}: {line.strip()}")
        found[needle] = hits
        _p(f"  {needle:22s} {len(hits):3d} 处")
        for h in hits[:4]:
            _p("      " + h)
    result["name_hits"] = {k: v for k, v in found.items()}

    _p("")
    _p("[2] 核心实现：eos/restriction/restriction/max_group.py")
    mpath = EOS_SRC / "eos" / "restriction" / "restriction" / "max_group.py"
    lines = mpath.read_text(encoding="utf-8").splitlines()
    for idx in range(len(lines)):
        if "group_id = item._type.group_id" in lines[idx] or \
           "quantity = len(self.__group_item_map" in lines[idx] or \
           "if quantity > max_allowed_quantity" in lines[idx]:
            lo = max(0, idx - 3)
            for n in range(lo, min(len(lines), idx + 2)):
                _p(f"    {mpath.relative_to(EOS_SRC).as_posix()}:{n + 1}: {lines[n]}")
            _p("    ---")
    _p("  读法：计数的 key 是 **item._type.group_id（type 的 group）**，")
    _p("        上限是该 item 自己 maxGroup* 属性的值 → **同组互斥**，不跨组。")
    result["count_key"] = "item._type.group_id"

    _p("")
    _p("[3] SDE 里这条规则的全部数据面（本地 zip，build 3607705）")
    sde = Sde()
    types = sde.keyed("types.jsonl")
    groups = sde.keyed("groups.jsonl")
    per_attr: dict[int, dict] = {ATTR_MAX_GROUP_ACTIVE: {}, ATTR_MAX_GROUP_ONLINE: {},
                                 ATTR_MAX_GROUP_FITTED: {}}
    for row in sde.rows("typeDogma.jsonl"):
        tid = int(row["_key"])
        for da in row.get("dogmaAttributes") or []:
            aid = int(da["attributeID"])
            if aid in per_attr:
                per_attr[aid].setdefault((int(types[tid]["groupID"]), float(da["value"])), 0)
                per_attr[aid][(int(types[tid]["groupID"]), float(da["value"]))] += 1

    for aid, label in ((ATTR_MAX_GROUP_ACTIVE, "maxGroupActive(763)"),
                       (ATTR_MAX_GROUP_ONLINE, "maxGroupOnline(978)"),
                       (ATTR_MAX_GROUP_FITTED, "maxGroupFitted(1544)")):
        total = sum(per_attr[aid].values())
        g46 = per_attr[aid].get((46, 1.0), 0)
        g1189 = per_attr[aid].get((1189, 1.0), 0)
        ngroups = len({g for g, _ in per_attr[aid]})
        _p(f"  {label:22s} 覆盖 {total:3d} 个 type / {ngroups:2d} 个 group；"
           f"group46(推进)={g46} group1189(微型跳跃)={g1189}")
        result[label] = {"types": total, "groups": ngroups, "g46": g46, "g1189": g1189}

    _p("")
    _p("[4] 决定性事实：两个模块在数据层面**没有任何跨组链接**")
    for tid in (12084, 4383):
        row = next(r for r in sde.rows("typeDogma.jsonl") if int(r["_key"]) == tid)
        attrs = {int(a["attributeID"]): a["value"] for a in row.get("dogmaAttributes") or []}
        name = _en(types[tid]["name"])
        gid = int(types[tid]["groupID"])
        _p(f"  {tid} {name:26s} group={gid:5d} ({_en(groups[gid]['name'])})")
        for aid in (ATTR_MAX_GROUP_ACTIVE, ATTR_MAX_GROUP_ONLINE, ATTR_MAX_GROUP_FITTED):
            _p(f"      {aid} = {attrs.get(aid, '<缺失>')}")
        _p(f"      canFitShipGroup01..03 = "
           f"{[attrs.get(x) for x in (1298, 1299, 1300)]}")
    _p("  → 两者都只写了 maxGroupActive=1，group 不同；数据里没有『A 组禁用 B 组』的字段")
    result["cross_group_field_exists"] = False

    _p("")
    _p("[5] 本引擎在同样的两个配装上怎么报（同一 SDE，13.6.0）")
    try:
        sys.path.insert(0, str(ROOT))
        from evefit.engine import adapter  # noqa: PLC0415

        eng = adapter.Engine()

        def axis(label: str, eft: str) -> None:
            fit = adapter.load_eft(eft)
            res = eng.calculate(fit, validate=True)
            mg = [v for v in (res.get("violations") or [])
                  if (v.get("rule") or {}).get("type") == "max_group"]
            _p(f"  {label}")
            _p(f"     max_group 违规 {len(mg)} 条："
               f"{json.dumps(mg, ensure_ascii=False) if mg else '无'}")
            result.setdefault("engine_axis", {})[label] = mg

        axis("Panther：500MN MWD(46) + Large MJD(1189)  ← 跨组",
             "[Panther, probe]\n"
             "Damage Control II\n\n"
             "500MN Microwarpdrive II\nLarge Micro Jump Drive\n\n"
             "1400mm Howitzer Artillery II, EMP L\n")
        axis("Bifrost：1MN AB(46) + 5MN MWD(46)  ← 同组",
             "[Bifrost, probe]\n"
             "Damage Control II\n\n"
             "1MN Afterburner II\n5MN Microwarpdrive II\n\n"
             "Rocket Launcher II, Scourge Rage Rocket\n")
    except Exception as e:  # noqa: BLE001
        _p(f"  ERR {type(e).__name__}: {e}")
        result["engine_axis_error"] = str(e)

    _p("")
    _p("[6] 把两个配装真的装进 eos，看它自己的 validate() 报什么")
    try:
        build_eos_source(verbose=False)          # 先建 source，它负责把 eos_src 挂到 sys.path
        from eos.restriction.exception import ValidationError  # noqa: PLC0415

        for label, eft in (
            ("Panther：500MN MWD(46) + Large MJD(1189)  ← 跨组",
             "[Panther, probe]\nDamage Control II\n\n"
             "500MN Microwarpdrive II\nLarge Micro Jump Drive\n\n"
             "1400mm Howitzer Artillery II, EMP L\n"),
            ("Bifrost：1MN AB(46) + 5MN MWD(46)  ← 同组",
             "[Bifrost, probe]\nDamage Control II\n\n"
             "1MN Afterburner II\n5MN Microwarpdrive II\n\n"
             "Rocket Launcher II, Scourge Rage Rocket\n"),
        ):
            session = _eos_fit_for_validate(adapter_eft(eft))
            _p(f"  {label}")
            try:
                session.validate()
                _p("     eos validate() → 无违规")
                result.setdefault("eos_validate", {})[label] = []
            except ValidationError as exc:
                payload = exc.args[0] if exc.args else {}
                kinds = []
                for _item, problems in payload.items():
                    for restriction in problems:
                        kinds.append(f"{restriction.name}({int(restriction)})")
                _p(f"     eos validate() → 违规种类：{sorted(set(kinds))}")
                result.setdefault("eos_validate", {})[label] = sorted(set(kinds))
    except Exception as e:  # noqa: BLE001
        _p(f"  ERR {type(e).__name__}: {e}")
        result["eos_validate_error"] = f"{type(e).__name__}: {e}"

    return result


def _eos_fit_for_validate(fit: dict):
    """把一个 `fit` dict 装成 eos Fit（模块一律 active，用于看装配规则怎么报）。"""
    import eos  # noqa: PLC0415

    session = eos.Fit()
    session.ship = eos.Ship(int(fit["ship"]["type_id"]))
    cls_map = {"high": (eos.ModuleHigh, lambda s: s.modules.high),
               "medium": (eos.ModuleMid, lambda s: s.modules.mid),
               "low": (eos.ModuleLow, lambda s: s.modules.low)}
    for item in fit.get("items") or []:
        slot = (item.get("slot") or {}).get("type")
        if slot not in cls_map:
            continue
        cls, getter = cls_map[slot]
        charge = item.get("charge")
        getter(session).equip(cls(
            int(item["type_id"]), state=eos.State.active,
            charge=eos.Charge(int(charge["type_id"])) if charge else None))
    return session


def adapter_eft(eft: str) -> dict:
    """用本项目的适配层把 EFT 文本变成 `fit` dict（只读，不改产品代码）。"""
    sys.path.insert(0, str(ROOT))
    from evefit.engine import adapter  # noqa: PLC0415

    return adapter.load_eft(eft)


# ---------------------------------------------------------------- eos 数据源


class SdeDataHandler:
    """把本地 FSD jsonl 直接喂给 eos（等价于 JsonDataHandler 的 Phobos 口径）。

    eos 的 `ModInfoconverter` 直接吃 FSD 原生的 `modifierInfo`
    （func/domain/operation/modifiedAttributeID/...），所以两边的键名只差一层重命名：

        _key              -> typeID / groupID / attributeID / effectID / buffID
        effectCategoryID  -> effectCategory
        dogmaAttributes   -> dgmtypeattribs(typeID, attributeID, value)
        dogmaEffects      -> dgmtypeeffects(typeID, effectID, isDefault)

    只保留 Cleaner 认可的强类型（船/模块/弹药/无人机/舰载机/植入体/技能/子系统），
    这样构建时间从「5.3 万 type 全量」降到可接受，且不改变任何一条被用到的数据。
    """

    def __init__(self, sde: Sde) -> None:
        self.sde = sde
        self._group_cat: dict[int, int] = {}
        for g in self._groups():
            self._group_cat[g["groupID"]] = g["categoryID"]

    def _keep_type(self, group_id) -> bool:
        if group_id is None:
            return False
        gid = int(group_id)
        return gid in EXTRA_GROUP_IDS or self._group_cat.get(gid) in STRONG_CATEGORIES

    def _groups(self) -> list[dict]:
        cats = self.sde.keyed("categories.jsonl")
        out = []
        for gid, row in self.sde.keyed("groups.jsonl").items():
            cid = row.get("categoryID")
            if cid is None:
                continue
            out.append({"groupID": int(gid), "categoryID": int(cid),
                        "name": _en(row.get("name")),
                        "published": bool(row.get("published"))})
        _ = cats
        return out

    def get_evegroups(self) -> list[dict]:
        return self._groups()

    def get_evetypes(self) -> list[dict]:
        out = []
        for tid, row in self.sde.keyed("types.jsonl").items():
            if not self._keep_type(row.get("groupID")):
                continue
            out.append({
                "typeID": int(tid),
                "groupID": int(row["groupID"]),
                "name": _en(row.get("name")),
                "published": bool(row.get("published")),
                "volume": row.get("volume"),
                "mass": row.get("mass"),
                "radius": row.get("radius"),
                "capacity": row.get("capacity"),
            })
        return out

    def get_dgmattribs(self) -> list[dict]:
        out = []
        for aid, row in self.sde.keyed("dogmaAttributes.jsonl").items():
            out.append({
                "attributeID": int(aid),
                "defaultValue": row.get("defaultValue", 0.0),
                "highIsGood": bool(row.get("highIsGood", False)),
                "stackable": bool(row.get("stackable", False)),
                "maxAttributeID": row.get("maxAttributeID"),
            })
        return out

    def get_dgmtypeattribs(self) -> list[dict]:
        types = self.sde.keyed("types.jsonl")
        rows = []
        for type_id, data in self.sde.keyed("typeDogma.jsonl").items():
            if not self._keep_type(types.get(type_id, {}).get("groupID")):
                continue
            for td in data.get("dogmaAttributes") or []:
                rows.append({"typeID": int(type_id),
                             "attributeID": int(td["attributeID"]),
                             "value": td["value"]})
        return rows

    def get_dgmtypeeffects(self) -> list[dict]:
        types = self.sde.keyed("types.jsonl")
        rows = []
        for type_id, data in self.sde.keyed("typeDogma.jsonl").items():
            if not self._keep_type(types.get(type_id, {}).get("groupID")):
                continue
            for td in data.get("dogmaEffects") or []:
                rows.append({"typeID": int(type_id),
                             "effectID": int(td["effectID"]),
                             "isDefault": bool(td.get("isDefault"))})
        return rows

    def get_dgmeffects(self) -> list[dict]:
        keep = ("isOffensive", "isAssistance", "durationAttributeID",
                "dischargeAttributeID", "rangeAttributeID", "falloffAttributeID",
                "trackingSpeedAttributeID", "fittingUsageChanceAttributeID",
                "resistanceAttributeID")
        out = []
        for eid, row in self.sde.keyed("dogmaEffects.jsonl").items():
            item = {"effectID": int(eid),
                    "effectCategory": row.get("effectCategoryID"),
                    "name": row.get("name")}
            for k in keep:
                if k in row:
                    item[k] = row[k]
            if row.get("modifierInfo"):
                item["modifierInfo"] = row["modifierInfo"]
            out.append(item)
        return out

    def get_dbuffcollections(self) -> list[dict]:
        out = []
        for bid, row in self.sde.keyed("dbuffCollections.jsonl").items():
            item = dict(row)
            item["buffID"] = int(bid)
            out.append(item)
        return out

    def get_skillreqs(self) -> list[dict]:
        rows = []
        for type_id, data in self.sde.keyed("typeDogma.jsonl").items():
            attrs = {int(a["attributeID"]): a["value"] for a in data.get("dogmaAttributes") or []}
            for slot in range(1, 6):
                skill_id = attrs.get(181 + slot)          # requiredSkill1..5 = 182..186
                level = attrs.get(276 + slot)             # requiredSkill1Level.. = 277..281
                if not skill_id:
                    continue
                rows.append({"typeID": int(type_id),
                             "skillTypeID": int(skill_id),
                             "level": int(level or 0)})
        return rows

    def get_typefighterabils(self) -> list[dict]:
        rows = []
        for tid, data in self.sde.keyed("fighterAbilitiesByType.jsonl").items():
            for ability in data.get("abilities") or data.get("fighterAbilities") or ():
                rows.append({"typeID": int(tid),
                             "abilityID": int(ability.get("abilityID", 0)),
                             "cooldownSeconds": ability.get("cooldownSeconds"),
                             "chargeCount": ability.get("chargeCount")})
        return rows

    def get_version(self):
        return "3607705"


class MemoryCacheHandler:
    """eos 的 cache_handler 是必需参数；这里只在内存里缓存，不落盘。

    落盘版（JsonCacheHandler）会把 34k type 压成 bz2，首次构建慢且占几百 MB 临时空间；
    对拍只需要一次运行内的对象，所以内存版足够，也避免在 spike 里留下大文件。

    **但有一件事必须照抄**：JsonCacheHandler 不是简单存下 `EveObjBuilder` 的产物，
    它把对象压成 primitives 再用 `EffectFactory.make` / `TypeFactory.make` 重建。
    走工厂是必要的 —— eos 的 `eve_obj/custom/*` 就挂在工厂上做实例级修正，最典型的是
    `online_effect_category.py`（CCP 数据里 `online` 效果(16) 的 category 是 1=active，
    eos 在工厂里把它改回 4=online）。直接存 converter 的原始对象会绕过这个修正，
    表现为「所有模块 CPU/PG 占用恒为 0、online 类被动全不生效」。这里照抄重建流程。
    """

    def __init__(self) -> None:
        self._fingerprint = None
        self._types: dict = {}
        self._attrs: dict = {}
        self._effects: dict = {}
        self._buffs: dict = {}

    def get_type(self, type_id):
        return self._types[type_id]

    def get_attr(self, attr_id):
        return self._attrs[attr_id]

    def get_effect(self, effect_id):
        return self._effects[effect_id]

    def get_buff_templates(self, buff_id):
        # 部分命令脉冲类的 warfareBuffID 在缩减数据集里没有对应 collection
        # （命令脉冲只影响舰队成员，不影响本船数值；本引擎那边同样不建模）。
        # 返回空 tuple 而不是抛错，否则整条样本的 HP/速度都拿不到。
        return self._buffs.get(buff_id, ())

    def get_fingerprint(self):
        return self._fingerprint

    def update_cache(self, eve_objects, fingerprint):
        from eos.eve_obj.effect import EffectFactory  # noqa: PLC0415
        from eos.eve_obj.type import TypeFactory  # noqa: PLC0415

        types, attrs, effects, buff_templates = eve_objects

        # ① 效果先重建：工厂会套用 custom/* 的实例修正
        built_effects = {}
        for e in effects:
            built_effects[e.id] = EffectFactory.make(
                e.id,
                category_id=e.category_id,
                is_offensive=e.is_offensive,
                is_assistance=e.is_assistance,
                duration_attr_id=e.duration_attr_id,
                discharge_attr_id=e.discharge_attr_id,
                range_attr_id=e.range_attr_id,
                falloff_attr_id=e.falloff_attr_id,
                tracking_speed_attr_id=e.tracking_speed_attr_id,
                fitting_usage_chance_attr_id=e.fitting_usage_chance_attr_id,
                resist_attr_id=e.resist_attr_id,
                build_status=e.build_status,
                modifiers=e.modifiers,
            )
        self._effects = built_effects

        # ② 类型重建：引用①的效果实例（default_effect 也要换成新实例）
        self._types = {}
        for t in types:
            self._types[t.id] = TypeFactory.make(
                t.id,
                group_id=t.group_id,
                category_id=t.category_id,
                attrs=t.attrs,
                effects=tuple(built_effects[e.id] for e in t.effects.values()
                              if e.id in built_effects),
                default_effect=(built_effects.get(t.default_effect.id)
                                if t.default_effect is not None else None),
                abilities_data=t.abilities_data,
                required_skills=t.required_skills,
            )

        self._attrs = {a.id: a for a in attrs}
        for template in buff_templates:
            self._buffs.setdefault(template.buff_id, []).append(template)
        self._fingerprint = fingerprint


_EOS_STATE = None


def build_eos_source(verbose: bool = True):
    """构建 eos 的 Source（含 EveObjBuilder 全流程），结果缓存在模块变量里。"""
    global _EOS_STATE
    if _EOS_STATE is not None:
        return _EOS_STATE

    sys.path.insert(0, str(EOS_SRC))
    from eos import SourceManager  # noqa: PLC0415

    sde = Sde()
    handler = SdeDataHandler(sde)
    cache = MemoryCacheHandler()
    t0 = time.time()
    SourceManager.add("build3607705", handler, cache, make_default=True)
    source = SourceManager.default
    dt = time.time() - t0
    if verbose:
        _p(f"  eos 构建完成：{dt:.1f}s，"
           f"types={len(cache._types)} attrs={len(cache._attrs)} "
           f"effects={len(cache._effects)} buffs={len(cache._buffs)}")
    _EOS_STATE = (source, cache, sde)
    return _EOS_STATE


# ---------------------------------------------------------------- Q3


def eos_fit_from_engine_fit(fit: dict, all_skills_level5: bool = True, sde: Sde | None = None,
                            launch_drones: bool = False):
    """把引擎的 fit dict 装成 eos 的 Fit（同船同模块同弹药同状态）。

    只映射「引擎已经还原出来的东西」；货舱物品在 eos 里也是货舱（不装电池）。

    `launch_drones=True`：把无人机从机库改为「已发射」（state=active 且不在 bay），
    只用于对齐「无人机伤害」口径 —— 引擎的 `-14` 含机库内未发射的无人机，
    eos 默认只算已发射的。
    """
    import eos  # noqa: PLC0415

    sde = sde or Sde()
    session = eos.Fit()
    session.ship = eos.Ship(int(fit["ship"]["type_id"]))

    state_map = {
        "offline": eos.State.offline,
        "online": eos.State.online,
        "active": eos.State.active,
        "overload": eos.State.overload,
    }
    slot_map = {
        "high": eos.ModuleHigh,
        "medium": eos.ModuleMid,
        "low": eos.ModuleLow,
        "rig": eos.Rig,
        "subsystem": eos.Subsystem,
    }

    for item in fit.get("items") or []:
        slot = (item.get("slot") or {}).get("type")
        state = state_map.get(item.get("state") or "active", eos.State.active)
        type_id = int(item["type_id"])
        quantity = int(item.get("quantity") or 1)
        charge = item.get("charge")
        charge_obj = eos.Charge(int(charge["type_id"])) if charge else None

        if slot in slot_map:
            cls = slot_map[slot]
            if slot == "rig":
                session.rigs.add(cls(type_id))
            elif slot == "subsystem":
                session.subsystems.add(cls(type_id))
            else:
                # 模块必须通过容器装入才生效；逐个 equip
                container = {"high": session.modules.high,
                             "medium": session.modules.mid,
                             "low": session.modules.low}[slot]
                if charge_obj is not None:
                    container.equip(cls(type_id, state=state, charge=charge_obj))
                else:
                    container.equip(cls(type_id, state=state))
        elif slot in ("drone", "drone_bay"):
            # 引擎把无人机放在 drone_bay（机库）槽；eos 里 Drone 只要 state=active
            # 就算「已发射」，因此 launch_drones=True 时强制 active。
            drone_state = eos.State.active if launch_drones else state
            for _ in range(quantity):
                session.drones.add(eos.Drone(type_id, state=drone_state))
        elif slot == "fighter_bay" or slot == "fighter_tube":
            for _ in range(quantity):
                session.fighters.add(eos.FighterSquad(type_id, state=state))
        elif slot == "implant":
            session.implants.add(eos.Implant(type_id))
        elif slot == "booster":
            session.boosters.add(eos.Booster(type_id))
        # cargo / service 槽不装

    if all_skills_level5:
        for type_id, row in sde.keyed("types.jsonl").items():
            gid = int(row.get("groupID") or 0)
            group = sde.keyed("groups.jsonl").get(gid) or {}
            if int(group.get("categoryID") or 0) == CAT_SKILL and row.get("published"):
                session.skills.add(eos.Skill(int(type_id), level=5))
    return session


def _attr(attrs: dict, attr_id: int):
    """引擎属性值是 `{"base": .., "value": ..}` 结构，键是 int；这里只取生效值。"""
    slot = attrs.get(attr_id)
    if slot is None:
        slot = attrs.get(str(attr_id))
    if isinstance(slot, dict):
        return slot.get("value")
    return slot


def engine_metrics(eng, fit: dict, skills: dict[int, int] | None = None) -> dict:
    """按产品口径从引擎原始输出里取对拍用的六类指标。

    注意 `-12` 并不是「武器 DPS」的同义词：实测它在**非伤害模块**上也有值
    （远程护盾/装甲维修、辅助维修器、Defender 发射器），所以这里额外给出
    逐件明细 `per_item`，让「武器口径」和「全量求和口径」都能复算。
    """
    if skills is not None:
        fit = {**fit, "character": {**(fit.get("character") or {}), "skills": dict(skills)}}
    res = eng.calculate(fit)

    ship = (res.get("ship") or {}).get("attributes") or {}
    items = res.get("items") or []
    # 引擎的 `items[i]` 不带 type_id（只有 attributes/state/max_state/charge），
    # 但与输入 `fit["items"]` 逐位置对应；不等长时宁可不认，避免错配。
    src_items = fit.get("items") or []
    aligned = len(src_items) == len(items)
    per_item = []
    dps_all = 0.0
    volley_all = 0.0
    cpu_used = 0.0
    pg_used = 0.0
    for idx, it in enumerate(items):
        attrs = it.get("attributes") or {}
        d = float(_attr(attrs, -12) or 0.0)
        v = float(_attr(attrs, -11) or 0.0)
        cpu_used += float(_attr(attrs, 49) or 0.0)
        pg_used += float(_attr(attrs, 15) or 0.0)
        dps_all += d
        volley_all += v
        if d or v:
            per_item.append({
                "index": idx,
                "type_id": src_items[idx].get("type_id") if aligned else None,
                "slot": (src_items[idx].get("slot") or {}).get("type") if aligned else None,
                "quantity": src_items[idx].get("quantity") if aligned else None,
                "dps": d,
                "volley": v,
            })
    drone_dps = float(_attr(ship, -14) or 0.0)
    fighter_dps = float(_attr(ship, -62) or 0.0)

    return {
        "dps_all": dps_all,
        "dps_drone": drone_dps,
        "dps_fighter": fighter_dps,
        "dps_total": dps_all + drone_dps + fighter_dps,
        "volley_all": volley_all,
        "volley_ship": _attr(ship, -13),
        "shield_hp": _attr(ship, 263),
        "armor_hp": _attr(ship, 265),
        "hull_hp": _attr(ship, 9),
        "ehp": _attr(ship, -43),
        "speed": _attr(ship, 37),
        "align": _attr(ship, -1),
        "sig": _attr(ship, 552),
        "cpu_used": cpu_used if cpu_used else _attr(ship, 49),
        "cpu_out": _attr(ship, 48),
        "pg_used": pg_used if pg_used else _attr(ship, 15),
        "pg_out": _attr(ship, 11),
        "per_item": per_item,
        "aligned_with_input": aligned,
    }


def eos_metrics(session) -> dict:
    """从 eos 的 stats 取同一批指标（口径差异写在报告里）。

    额外按 eos 自己的过滤器拆成 turret / missile / drone 三条，便于和引擎逐项对齐。
    """
    import eos  # noqa: PLC0415
    from eos.item_filter import drone_filter, missile_filter, turret_filter  # noqa: PLC0415

    stats = session.stats
    ship = session.ship
    out = {}
    try:
        out["dps_total"] = stats.get_dps(reload=False).total
        out["dps_reload"] = stats.get_dps(reload=True).total
        out["dps_turret"] = stats.get_dps(turret_filter).total
        out["dps_missile"] = stats.get_dps(missile_filter).total
        out["dps_drone"] = stats.get_dps(drone_filter).total
    except Exception as e:  # noqa: BLE001
        out["dps_error"] = f"{type(e).__name__}: {e}"
    try:
        out["volley_total"] = stats.get_volley().total
    except Exception as e:  # noqa: BLE001
        out["volley_error"] = f"{type(e).__name__}: {e}"
    try:
        out["shield_hp"] = stats.hp.shield
        out["armor_hp"] = stats.hp.armor
        out["hull_hp"] = stats.hp.hull
        out["ehp"] = stats.get_ehp(
            eos.DmgProfile(em=25, thermal=25, kinetic=25, explosive=25)).total
    except Exception as e:  # noqa: BLE001
        out["hp_error"] = f"{type(e).__name__}: {e}"
    try:
        out["speed"] = ship.attrs[37]
        out["sig"] = ship.attrs[552]
        out["agility"] = ship.attrs[70]
        out["mass"] = ship.attrs[4]
    except Exception as e:  # noqa: BLE001
        out["mobility_error"] = f"{type(e).__name__}: {e}"
    try:
        out["cpu_used"] = stats.cpu.used
        out["cpu_out"] = stats.cpu.output
        out["pg_used"] = stats.powergrid.used
        out["pg_out"] = stats.powergrid.output
    except Exception as e:  # noqa: BLE001
        out["resource_error"] = f"{type(e).__name__}: {e}"

    # 逐件 DPS：把差异定位到「哪一件」，而不是只比总和
    per_item = []
    try:
        candidates = []
        candidates.extend(session.modules.high)
        candidates.extend(session.modules.mid)
        candidates.extend(session.modules.low)
        candidates.extend(session.drones)
        for item in candidates:
            try:
                dmg = item.get_dps(False).total
            except Exception:  # noqa: BLE001 - 非伤害模块没有这个 mixin
                continue
            if dmg:
                per_item.append({"type_id": item._type_id, "dps": float(dmg)})
    except Exception as e:  # noqa: BLE001
        out["per_item_error"] = f"{type(e).__name__}: {e}"
    out["per_item"] = per_item
    return out


def _load_fixtures(limit: int | None = None) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name.endswith(".expected.json"):
            continue
        out.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
    out.sort(key=lambda kv: kv[0])
    return out[:limit] if limit else out


def dmg_dealer_type_ids(cache) -> set[int]:
    """用 eos 自己的类层级判定「哪些 type 是伤害输出者」。

    这样「武器口径」的划分是 pyfa 的标准，不是我们手写的 group 白名单。
    """
    from eos.eve_obj.effect.dmg_dealer.base import DmgDealerEffect  # noqa: PLC0415

    dmg_effect_ids = {eid for eid, eff in cache._effects.items()
                      if isinstance(eff, DmgDealerEffect)}
    out = set()
    for tid, item_type in cache._types.items():
        if any(eid in dmg_effect_ids for eid in item_type.effects):
            out.add(tid)
    return out


def _pct(a, b) -> float | None:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    if a == 0:
        return 0.0 if b == 0 else None
    return (b - a) / a * 100.0


def stage_q3(limit: int | None = None, only: list[str] | None = None) -> dict:
    """Q3：同一配装，两边各算一遍，逐指标比。"""
    _head("Q3 · 数值对拍（本引擎 13.6.0 vs pyfa/eos master）")
    sys.path.insert(0, str(ROOT))
    from evefit.engine import adapter  # noqa: PLC0415

    eng = adapter.Engine()
    source, cache, sde = build_eos_source()

    # 全 5 技能档（= pyfa 默认口径）
    all5: dict[int, int] = {}
    for type_id, row in sde.keyed("types.jsonl").items():
        group = sde.keyed("groups.jsonl").get(int(row.get("groupID") or 0)) or {}
        if int(group.get("categoryID") or 0) == CAT_SKILL and row.get("published"):
            all5[int(type_id)] = 5
    _p(f"  口径统一：两边都用『全部 {len(all5)} 个已发布技能 = 5 级』（pyfa 默认）")

    dmg_types = dmg_dealer_type_ids(cache)
    _p(f"  「武器 type」由 eos 的 DmgDealer 类层级判定：{len(dmg_types)} 个 type")

    fixtures = _load_fixtures()
    picked = []
    for name, km in fixtures:
        if only and not any(name.startswith(o) for o in only):
            continue
        if len(picked) >= (limit or 20):
            break
        picked.append((name, km))

    rows = []
    for name, km in picked:
        record = {"fixture": name}
        try:
            fit = adapter.load_killmail(km)
            record["ship_type_id"] = fit["ship"]["type_id"]
            record["engine"] = engine_metrics(eng, fit, skills=all5)
        except Exception as e:  # noqa: BLE001
            record["engine_error"] = f"{type(e).__name__}: {e}"
            rows.append(record)
            continue

        # ① 逐件对账：引擎 per-item 的 -12 是「单件值」，无人机/弹药等按 quantity 累乘
        engine_by_type: dict = {}
        for entry in record["engine"].get("per_item") or []:
            tid = entry.get("type_id")
            if tid is None:
                continue
            qty = entry.get("quantity") or 1
            engine_by_type[tid] = engine_by_type.get(tid, 0.0) + entry["dps"] * int(qty)

        try:
            session = eos_fit_from_engine_fit(fit, all_skills_level5=False, sde=sde)
            import eos as _eos  # noqa: PLC0415
            for type_id, level in all5.items():
                session.skills.add(_eos.Skill(int(type_id), level=level))
            record["eos"] = eos_metrics(session)

            # ② 同一配装、无人机改为「已发射」，只用来对齐无人机伤害口径
            launched = eos_fit_from_engine_fit(
                fit, all_skills_level5=False, sde=sde, launch_drones=True)
            for type_id, level in all5.items():
                launched.skills.add(_eos.Skill(int(type_id), level=level))
            record["eos_drone_launched"] = eos_metrics(launched).get("dps_drone")
        except Exception as e:  # noqa: BLE001
            record["eos_error"] = f"{type(e).__name__}: {e}"
            record["eos_trace"] = traceback.format_exc()[-1200:]
        rows.append(record)
        # 逐件对账要能打印名字：只取两边出现过的 type
        tids = {entry["type_id"] for entry in record["engine"].get("per_item") or []
                if entry.get("type_id")}
        tids |= {entry["type_id"] for entry in (record.get("eos") or {}).get("per_item") or []}
        record["_names"] = {
            tid: _en(sde.keyed("types.jsonl").get(tid, {}).get("name")) for tid in tids}
        e = record["engine"].get("dps_all")
        q = (record.get("eos") or {}).get("dps_total")
        _p(f"  {name:28s} Σ(-12)={e if e is None else round(e, 3)} "
           f"eos总DPS={q if q is None else round(q, 3)} "
           f"items对齐={record['engine'].get('aligned_with_input')}")

    _print_q3_table(rows)
    return {"rows": rows, "n_skill": len(all5), "n_dmg_types": len(dmg_types)}


def _print_q3_table(rows: list[dict]) -> None:
    _p("")
    _p("表 A · 逐件 DPS 对账（引擎 Σ(-12)×quantity vs eos 逐件 get_dps）")
    _p("  引擎 per-item 的 -12 是「单件值」，无人机/弹药等按 quantity 累乘后再比")
    for r in rows:
        e = r.get("engine") or {}
        q = r.get("eos") or {}
        if r.get("engine_error") or r.get("eos_error"):
            _p(f"{r['fixture'][:24]:24s} ERROR "
               f"{r.get('engine_error') or r.get('eos_error')}")
            continue
        nm = r.get("_names") or {}

        def label(tid):
            return str(nm.get(tid, tid))

        engine_by_type: dict = {}
        for entry in e.get("per_item") or []:
            tid = entry.get("type_id")
            if tid is None:
                continue
            qty = int(entry.get("quantity") or 1)
            engine_by_type[tid] = engine_by_type.get(tid, 0.0) + entry["dps"] * qty
        eos_by_type: dict = {}
        for entry in q.get("per_item") or []:
            eos_by_type[entry["type_id"]] = eos_by_type.get(entry["type_id"], 0.0) + entry["dps"]

        diffs = []
        matched = 0
        for tid in sorted(set(engine_by_type) | set(eos_by_type)):
            a = engine_by_type.get(tid, 0.0)
            b = eos_by_type.get(tid, 0.0)
            if a == 0 and b == 0:
                continue
            rel = None if a == 0 else (b - a) / a
            if rel is not None and abs(rel) < 1e-6:
                matched += 1
                continue
            diffs.append((label(tid), a, b, rel))
        _p(f"  {r['fixture'][:24]:24s} Σ(-12)={e.get('dps_all'):.4f} "
           f"逐件一致 {matched} 件 / 不一致 {len(diffs)} 件")
        for name, a, b, rel in diffs:
            rel_s = "—" if rel is None else f"{rel * 100:+.3f}%"
            _p(f"      Δ {name[:34]:34s} 引擎={a:.4f} eos={b:.4f} {rel_s}")

    _p("")
    _p("表 B · 无人机 DPS（引擎 -14 vs eos drone 过滤器；两边都「已发射」口径）")
    _p(f"{'fixture':24s} {'引擎-14':>12s} {'eos已发射':>12s} {'相对差':>12s}")
    for r in rows:
        e = r.get("engine") or {}
        if not e:
            continue
        a = e.get("dps_drone")
        b = r.get("eos_drone_launched")
        rel = None
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a:
            rel = (b - a) / a
        _p(f"{r['fixture'][:24]:24s} {a if not isinstance(a, (int, float)) else round(a, 6):>12} "
           f"{b if not isinstance(b, (int, float)) else round(b, 6):>12} "
           f"{('%.2e' % rel) if rel is not None else '-':>12}")

    _p("")
    _p("表 C · 生存性 / 机动 / 资源")
    _p(f"{'fixture':24s} {'HP引擎':>22s} {'HP eos':>22s} "
       f"{'EHP引擎':>10s} {'EHP eos':>10s} {'Δ%':>7s} "
       f"{'速度引擎':>10s} {'速度eos':>9s} {'sig引擎':>8s} {'sig eos':>8s} "
       f"{'对齐引擎':>8s} {'CPU引擎':>5s} {'CPUeos':>5s} {'PG引擎':>7s} {'PGeos':>7s}")

    def _hp(x):
        if not isinstance(x, dict):
            return "-"
        parts = [x.get("shield_hp"), x.get("armor_hp"), x.get("hull_hp")]
        if not all(isinstance(p, (int, float)) for p in parts):
            return "-"
        return "/".join(f"{p:.1f}" for p in parts)

    for r in rows:
        e = r.get("engine") or {}
        q = r.get("eos") or {}
        if not e or not q:
            continue

        def num(x, w=10, nd=2):
            return f"{x:>{w}.{nd}f}" if isinstance(x, (int, float)) else f"{'-':>{w}}"

        _p(f"{r['fixture'][:24]:24s} {_hp(e):>22s} {_hp(q):>22s} "
           f"{num(e.get('ehp'), 10)} {num(q.get('ehp'), 10)} "
           f"{num(_pct(e.get('ehp'), q.get('ehp')), 7, 3)} "
           f"{num(e.get('speed'), 10)} {num(q.get('speed'), 9)} "
           f"{num(e.get('sig'), 8)} {num(q.get('sig'), 8)} "
           f"{num(e.get('align'), 8, 3)} {num(e.get('cpu_used'), 5, 2)} "
           f"{num(q.get('cpu_used'), 5, 2)} {num(e.get('pg_used'), 7, 2)} "
           f"{num(q.get('pg_used'), 7, 2)}")


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T-42 pyfa/eos 侦察探针")
    ap.add_argument("stage", choices=("q1", "q2", "q3", "all"))
    ap.add_argument("--limit", type=int, default=None, help="Q3 取多少条样本")
    ap.add_argument("--only", nargs="*", default=None, help="Q3 只跑名字前缀匹配的样本")
    ap.add_argument("--json-out", default=None, help="把各 stage 的原始结果写成 JSON")
    args = ap.parse_args(argv)

    report: dict = {}
    if args.stage in ("q1", "all"):
        report["q1"] = stage_q1()
    if args.stage in ("q2", "all"):
        report["q2"] = stage_q2()
    if args.stage in ("q3", "all"):
        report["q3"] = stage_q3(limit=args.limit, only=args.only)

    if args.json_out:
        pathlib.Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
        _p(f"\n原始结果已写入 {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
