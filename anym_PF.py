# anym_PF.py
from __future__ import annotations

from pathlib import Path
import os
import sys
import json
import hashlib
from typing import Dict, List, Optional, Tuple
import math
import psutil

# PowerFactory Python path
sys.path.append(r"C:\Program Files\DIgSILENT\PowerFactory 2024 SP7\Python\3.9")
import powerfactory as pf  # type: ignore


# ----------------------------
# Small utils
# ----------------------------
def _p(p: Path) -> str:
    return os.fspath(Path(p).resolve())


def _seed_hash(seed: str, tag: str) -> int:
    h = hashlib.sha256((str(seed) + "|" + tag).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _seed_unit(seed: str, tag: str) -> float:
    x = _seed_hash(seed, tag)
    return (x % 10_000_000) / 10_000_000.0


def _build_geo_transform(seed: str, max_shift_deg: float = 2.0):
    """
    Global transform (rotation+mirror+shift) – NOT stored in JSON,
    only used internally for anonymization when gps=False.
    """
    angle = 2.0 * math.pi * _seed_unit(seed, "gps_angle")
    mirror = 1  # or: _seed_hash(seed, "gps_mirror") % 2

    dx = (2 * _seed_unit(seed, "gps_dx") - 1) * max_shift_deg
    dy = (2 * _seed_unit(seed, "gps_dy") - 1) * max_shift_deg

    c = math.cos(angle)
    s = math.sin(angle)

    def transform(lat: float, lon: float) -> Tuple[float, float]:
        x = float(lon)
        y = float(lat)

        if mirror == 1:
            x = -x

        xr = c * x - s * y
        yr = s * x + c * y

        xr += dx
        yr += dy
        return float(yr), float(xr)

    return transform


def _obj_unit_from_name(seed: str, tag: str, name: str) -> float:
    key = f"{seed}|{tag}|{name}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    x = int(h[:16], 16)
    return (x % 10_000_000) / 10_000_000.0


def _meters_to_deg_lat(m: float) -> float:
    return m / 111_320.0


def _meters_to_deg_lon(m: float, lat_deg: float) -> float:
    coslat = abs(math.cos(math.radians(lat_deg)))
    coslat = max(0.1, coslat)
    return m / (111_320.0 * coslat)


# ----------------------------
# PF call wrappers
# ----------------------------
def _call_pf_or_app(app, name: str, *args):
    if hasattr(pf, name):
        return getattr(pf, name)(*args)
    if hasattr(app, name):
        return getattr(app, name)(*args)
    raise AttributeError(f"Neither pf nor app have: {name}")


def _pf_bulk_mode_begin(app):
    _call_pf_or_app(app, "SetProgressBarUpdatesEnabled", 1)
    _call_pf_or_app(app, "SetGuiUpdateEnabled", 1)
    _call_pf_or_app(app, "SetUserBreakEnabled", 1)
    _call_pf_or_app(app, "SetWriteCacheEnabled", 1)


def _pf_bulk_mode_end(app):
    _call_pf_or_app(app, "WriteChangesToDb")
    _call_pf_or_app(app, "SetWriteCacheEnabled", 0)
    _call_pf_or_app(app, "SetUserBreakEnabled", 0)
    _call_pf_or_app(app, "SetGuiUpdateEnabled", 0)
    _call_pf_or_app(app, "SetProgressBarUpdatesEnabled", 0)


# ----------------------------
# Deterministic CIM id
# ----------------------------
def _generate_seeded_uuid(old_id: str, seed: str) -> str:
    clean = str(old_id).lstrip("_")
    payload = (str(seed) + clean).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    hex32 = digest[:32]
    uuid = f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"
    return "_" + uuid


# ----------------------------
# Anonymizer container
# ----------------------------
class SeededNameAnonymizer:
    def __init__(self, seed: str, prefix: str = "ANON_", length: int = 10):
        self.seed = str(seed)
        self.prefix = prefix
        self.length = int(length)

        # loc_name mapping
        self.forward: Dict[str, str] = {}  # old -> new
        self.reverse: Dict[str, str] = {}  # new -> old

        # cimRdfId mapping
        self.cim_forward: Dict[str, str] = {}  # old -> new

        # gps mapping keyed by ORIGINAL cimRdfId (before change)
        # value:
        #   {"old":[lat,lon], "new":[lat,lon]} for gps=False
        #   {"old":[lat,lon], "deleted": True, "cim_after": "...", "full_name_after": "..."} for gps=True
        self.gps_mapping: Dict[str, dict] = {}

    def _hash(self, text: str, length: int) -> str:
        payload = (self.seed + "\n" + str(text).strip()).encode("utf-8")
        return hashlib.sha256(payload).hexdigest().upper()[:length]

    def translate(self, name: str) -> str:
        if not name:
            return name
        if name.startswith(self.prefix):
            return name
        if name in self.reverse:
            return name
        if name in self.forward:
            return self.forward[name]

        token = self._hash(name, self.length)
        new_name = f"{self.prefix}{token}"

        L = self.length
        while new_name in self.reverse and self.reverse[new_name] != name:
            L += 2
            token = self._hash(name, L)
            new_name = f"{self.prefix}{token}"

        self.forward[name] = new_name
        self.reverse[new_name] = name
        return new_name


def save_mapping_json(path: Path, anonymizer: SeededNameAnonymizer):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "seed": anonymizer.seed,
        "prefix": anonymizer.prefix,
        "length": anonymizer.length,
        "loc_name_mapping": anonymizer.forward,
        "cimRdfId_mapping": anonymizer.cim_forward,
        "gps_mapping": anonymizer.gps_mapping,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_mapping_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ----------------------------
# PF object collection
# ----------------------------
# ----------------------------
# PF object collection
# ----------------------------
class PfObjects:
    def __init__(self, app):
        # Patterns to collect (extend as needed)
        patterns = [
            #"*.IntPrjfolder",
            #"*.IntQlim",
            "*.Int*",
            "*.Elm*",
            "*.Typ*",

            #"*.StaSwitch*",
            #"*.StaCubic*",
            "*.Sta*",

            #"*.BlkSig*",
            #"*.BlkDef*",
            "*.Blk*",
        ]

        # Collect everything in one list
        self.objects = []
        for pat in patterns:
            try:
                self.objects += (app.GetCalcRelevantObjects(pat) or [])
            except Exception:
                pass

    def iter_all_lists(self):
        yield from self.objects

"""
class PfObjects:
    def __init__(self, app):
        self.intfolder = app.GetCalcRelevantObjects("*.IntPrjfolder") or []
        self.intqlim = app.GetCalcRelevantObjects("*.IntQlim") or []

        self.coup_switches = app.GetCalcRelevantObjects("*.ElmCoup") or []
        self.terms = app.GetCalcRelevantObjects("*.ElmTerm") or []
        self.substat = app.GetCalcRelevantObjects("*.ElmSubstat") or []
        self.nets = app.GetCalcRelevantObjects("*.ElmXnet") or []
        self.loads = app.GetCalcRelevantObjects("*.ElmLod") or []
        self.bmus = app.GetCalcRelevantObjects("*.ElmBmu") or []
        self.sites = app.GetCalcRelevantObjects("*.ElmSite") or []
        self.lines = app.GetCalcRelevantObjects("*.ElmLne") or []
        self.vac = app.GetCalcRelevantObjects("*.ElmVac") or []
        self.sym = app.GetCalcRelevantObjects("*.ElmSym") or []
        self.area = app.GetCalcRelevantObjects("*.ElmArea") or []

        self.tr2 = app.GetCalcRelevantObjects("*.ElmTr2") or []
        self.zpu = app.GetCalcRelevantObjects("*.ElmZpu") or []
        self.secc = app.GetCalcRelevantObjects("*.ElmSecctrl") or []

        self.cubic = app.GetCalcRelevantObjects("*.StaCubic") or []
        self.sta_switches = app.GetCalcRelevantObjects("*.StaSwitch") or []

        self.tline = app.GetCalcRelevantObjects("*.TypLne") or []
        self.tsym = app.GetCalcRelevantObjects("*.TypSym") or []
        self.ttr2 = app.GetCalcRelevantObjects("*.TypTr2") or []
        self.tlod = app.GetCalcRelevantObjects("*.TypLod") or []

    def iter_all_lists(self):
        yield from self.intqlim
        yield from self.intfolder

        yield from self.coup_switches
        yield from self.terms
        yield from self.substat
        yield from self.sta_switches
        yield from self.nets
        yield from self.loads
        yield from self.bmus
        yield from self.sites
        yield from self.lines
        yield from self.vac
        yield from self.sym
        yield from self.area

        yield from self.tr2
        yield from self.zpu
        yield from self.secc
        yield from self.cubic

        yield from self.tline
        yield from self.tsym
        yield from self.ttr2
        yield from self.tlod
"""

def parent_chain_until_network_data(obj) -> List:
    chain = []
    current = obj
    while current:
        chain.append(current)
        if getattr(current, "loc_name", None) == "Network Data":
            break
        current = current.GetParent()
    return chain


def collect_unique_objects_for_anonymization(app) -> List:
    pf_objs = PfObjects(app)
    unique: Dict[str, object] = {}

    for obj in pf_objs.iter_all_lists():
        try:
            key = obj.GetFullName()
        except Exception:
            key = f"{obj.GetClassName()}::{getattr(obj, 'loc_name', '')}"
        unique.setdefault(key, obj)

        for parent in parent_chain_until_network_data(obj):
            try:
                pkey = parent.GetFullName()
            except Exception:
                pkey = f"{parent.GetClassName()}::{getattr(parent, 'loc_name', '')}"
            unique.setdefault(pkey, parent)

    return list(unique.values())


# ----------------------------
# Safe attribute helpers
# ----------------------------
def _get_float_attr(obj, attr: str) -> Optional[float]:
    try:
        if not obj.HasAttribute(attr):
            return None
    except Exception:
        return None

    try:
        v = obj.GetAttribute(attr)
        if v is None:
            return None
        return float(v)
    except Exception:
        try:
            return float(getattr(obj, attr))
        except Exception:
            return None


def safe_set(obj, attr, value, *, verbose: bool = False) -> bool:
    try:
        if not obj.HasAttribute(attr):
            return False
    except Exception as e:
        if verbose:
            print(f"[WARN] HasAttribute({attr}) failed: {e}")
        return False

    try:
        obj.SetAttribute(attr, value)
        return True
    except TypeError as e:
        if isinstance(value, str):
            try:
                obj.SetAttribute(attr, [value])
                return True
            except Exception:
                pass
        if verbose:
            print(f"[WARN] TypeError SetAttribute({attr}) on {obj.GetClassName()} ({getattr(obj,'loc_name','')}): {e}")
        return False
    except Exception as e:
        if verbose:
            print(f"[WARN] SetAttribute({attr}) failed on {obj.GetClassName()} ({getattr(obj,'loc_name','')}): {e}")
        return False


def _get_loc_name(obj) -> str:
    try:
        return obj.GetAttribute("loc_name")
    except Exception:
        return getattr(obj, "loc_name", "")


def _get_cim_rdf_id(obj) -> List[str]:
    try:
        if not obj.HasAttribute("cimRdfId"):
            return []
    except Exception:
        return []
    try:
        value = obj.GetAttribute("cimRdfId")
        return value or []
    except Exception:
        return []


def _set_cim_rdf_id(obj, new_id: str) -> bool:
    return safe_set(obj, "cimRdfId", [new_id], verbose=False)


def _get_full_name(obj) -> str:
    try:
        return obj.GetFullName()
    except Exception:
        return f"{obj.GetClassName()}::{_get_loc_name(obj)}"


def _to_project_relative(full_name: str) -> str:
    """
    Convert a FullName like:
      \\user\\proj.IntPrj\\Network Model.IntPrjfolder\\...
    into a path relative to the project:
      \\Network Model.IntPrjfolder\\...
    """
    marker = r"\Network Model.IntPrjfolder"
    i = full_name.find(marker)
    if i < 0:
        return full_name
    return full_name[i:]


def _search_by_full_name_after(app, full_name_after: str):
    """
    Locate an object using SearchObject() with a project-relative path.
    """
    project = app.GetActiveProject()
    if not project:
        return None

    rel = _to_project_relative(full_name_after)
    try:
        return project.SearchObject(rel)
    except Exception:
        return None


# ----------------------------
# Anonymize primitives
# ----------------------------
def anonymize_cim_rdf_id(obj, seed: str, anonymizer: SeededNameAnonymizer) -> None:
    ids = _get_cim_rdf_id(obj)
    if not ids:
        return

    old_id = ids[0]
    if old_id in anonymizer.cim_forward:
        new_id = anonymizer.cim_forward[old_id]
    else:
        new_id = _generate_seeded_uuid(old_id, seed)
        anonymizer.cim_forward[old_id] = new_id

    _set_cim_rdf_id(obj, new_id)


def _set_loc_name_only(obj, new_name: str):
    obj.SetAttribute("loc_name", new_name)


def _make_unique_if_needed(obj, desired: str, anonymizer: SeededNameAnonymizer) -> str:
    old = _get_loc_name(obj)
    try:
        _set_loc_name_only(obj, desired)
        if _get_loc_name(obj) == desired:
            return desired
        raise RuntimeError("PF did not apply loc_name")
    except Exception:
        try:
            base = _get_full_name(obj)
        except Exception:
            base = f"{obj.GetClassName()}::{old}"
        suffix = anonymizer._hash(base, 6)
        candidate = f"{desired}_{suffix}"
        _set_loc_name_only(obj, candidate)
        if _get_loc_name(obj) != candidate:
            raise RuntimeError(f"Rename failed: {old} -> {desired} (candidate {candidate} not applied)")
        return candidate


def _sanitize_desc(obj, desc: bool):
    if desc:
        safe_set(obj, "desc", "Deleted", verbose=False)


# ----------------------------
# GPS handling (2nd pass)
# ----------------------------
def _gps_apply_and_record(
    obj,
    *,
    seed: str,
    gps_delete: bool,
    gps_transform,
    anonymizer: SeededNameAnonymizer,
    orig_cim_id: Optional[str],
    orig_loc_name_for_jitter: Optional[str],
):
    """
    Second pass:
    - gps_delete=True: set to 0/0 and record old GPS + identifiers
    - gps_delete=False: transform + jitter and record old/new GPS
    Mapping key is the original cimRdfId (before any change).
    """
    if orig_cim_id is None:
        return

    old_lat = _get_float_attr(obj, "GPSlat")
    old_lon = _get_float_attr(obj, "GPSlon")
    if old_lat is None or old_lon is None:
        return

    if abs(old_lat) < 1e-12 and abs(old_lon) < 1e-12:
        return

    if gps_delete:
        cur_ids = _get_cim_rdf_id(obj)
        cim_after = cur_ids[0] if cur_ids else None

        anonymizer.gps_mapping.setdefault(
            orig_cim_id,
            {
                "old": [float(old_lat), float(old_lon)],
                "deleted": True,
                "cim_after": cim_after,
                "full_name_after": _get_full_name(obj),
            },
        )
        safe_set(obj, "GPSlat", 0.0, verbose=False)
        safe_set(obj, "GPSlon", 0.0, verbose=False)
        return

    new_lat, new_lon = gps_transform(old_lat, old_lon)

    base_name = orig_loc_name_for_jitter or _get_loc_name(obj)
    jitter_m = 100.0

    r = _obj_unit_from_name(seed, "gps_jitter_r", base_name) * jitter_m
    theta = 2.0 * math.pi * _obj_unit_from_name(seed, "gps_jitter_theta", base_name)

    dx_m = r * math.cos(theta)
    dy_m = r * math.sin(theta)

    dlat = _meters_to_deg_lat(dy_m)
    dlon = _meters_to_deg_lon(dx_m, new_lat)

    new_lat += dlat
    new_lon += dlon

    anonymizer.gps_mapping.setdefault(
        orig_cim_id,
        {"old": [float(old_lat), float(old_lon)], "new": [float(new_lat), float(new_lon)]},
    )

    safe_set(obj, "GPSlat", float(new_lat), verbose=False)
    safe_set(obj, "GPSlon", float(new_lon), verbose=False)


# ----------------------------
# Full anonymize procedure
# ----------------------------
def anonymize_objects(
    app,
    objects: List,
    seed: str,
    desc: bool,
    gps: bool,
    prefix: str = "ANON_",
    length: int = 10,
) -> SeededNameAnonymizer:
    """
    gps parameter meaning:
      gps=True  -> delete GPS (0/0)
      gps=False -> transform + jitter
    GPS runs in a second pass (after loc_name / cimRdfId changes).
    """
    anonymizer = SeededNameAnonymizer(seed=seed, prefix=prefix, length=length)
    gps_transform = _build_geo_transform(seed, max_shift_deg=2.0)

    # Store original keys for the second pass:
    # python object id -> (orig_cim_id, orig_loc_name)
    orig_keys: Dict[int, Tuple[Optional[str], Optional[str]]] = {}

    _pf_bulk_mode_begin(app)
    try:
        for obj in objects:
            ids = _get_cim_rdf_id(obj)
            orig_cim = ids[0] if ids else None
            orig_loc = _get_loc_name(obj)
            orig_keys[id(obj)] = (orig_cim, orig_loc)

            anonymize_cim_rdf_id(obj, seed, anonymizer)

            _sanitize_desc(obj, desc)

            if isinstance(orig_loc, str) and orig_loc.strip():
                new_name = anonymizer.translate(orig_loc)
                if new_name != orig_loc:
                    _make_unique_if_needed(obj, new_name, anonymizer)
    finally:
        _pf_bulk_mode_end(app)

    _pf_bulk_mode_begin(app)
    try:
        for obj in objects:
            orig_cim, orig_loc = orig_keys.get(id(obj), (None, None))
            _gps_apply_and_record(
                obj,
                seed=seed,
                gps_delete=gps,
                gps_transform=gps_transform,
                anonymizer=anonymizer,
                orig_cim_id=orig_cim,
                orig_loc_name_for_jitter=orig_loc,
            )
    finally:
        _pf_bulk_mode_end(app)

    return anonymizer


# ----------------------------
# Restore procedure
# ----------------------------
def restore_from_mapping(app, mapping_path: Path):
    """
    Restore inside an already imported project using the mapping JSON:
    - Restore GPS first for deleted=true (while anonymized identifiers are still available)
      -> prefer cim_after, then cim mapping, then SearchObject fallback
    - Restore loc_name
    - Restore cimRdfId
    - Restore GPS for transformed cases using original cimRdfId (after cim restore)
    """
    data = load_mapping_json(mapping_path)

    loc_map: Dict[str, str] = data.get("loc_name_mapping", {}) or {}  # old -> new
    cim_map: Dict[str, str] = data.get("cimRdfId_mapping", {}) or {}  # old -> new
    gps_map: Dict[str, dict] = data.get("gps_mapping", {}) or {}      # key = original cim (old)

    loc_rev = {v: k for k, v in loc_map.items()}  # new -> old
    cim_rev = {v: k for k, v in cim_map.items()}  # new -> old

    objects = collect_unique_objects_for_anonymization(app)

    def _build_cim_index(objs: List) -> Dict[str, object]:
        idx: Dict[str, object] = {}
        for o in objs:
            ids = _get_cim_rdf_id(o)
            if ids:
                idx[ids[0]] = o
        return idx

    cim_index_current = _build_cim_index(objects)

    _pf_bulk_mode_begin(app)
    try:
        for orig_cim, rec in gps_map.items():
            if not rec.get("deleted", False):
                continue

            old = rec.get("old")
            if not (isinstance(old, list) and len(old) == 2):
                continue
            old_lat, old_lon = float(old[0]), float(old[1])

            target = None

            cim_after = rec.get("cim_after")
            if isinstance(cim_after, str) and cim_after:
                target = cim_index_current.get(cim_after)

            if target is None:
                current_cim = cim_map.get(orig_cim)
                if isinstance(current_cim, str) and current_cim:
                    target = cim_index_current.get(current_cim)

            if target is None:
                fn = rec.get("full_name_after")
                if isinstance(fn, str) and fn:
                    target = _search_by_full_name_after(app, fn)

            if target is None:
                print(f"[WARN] deleted-GPS target not found (orig_cim={orig_cim})")
                continue

            safe_set(target, "GPSlat", old_lat, verbose=False)
            safe_set(target, "GPSlon", old_lon, verbose=False)
    finally:
        _pf_bulk_mode_end(app)

    _pf_bulk_mode_begin(app)
    try:
        for obj in objects:
            cur = _get_loc_name(obj)
            if cur in loc_rev:
                try:
                    obj.SetAttribute("loc_name", loc_rev[cur])
                except Exception:
                    pass

            ids = _get_cim_rdf_id(obj)
            if ids:
                cur_id = ids[0]
                if cur_id in cim_rev:
                    _set_cim_rdf_id(obj, cim_rev[cur_id])
    finally:
        _pf_bulk_mode_end(app)

    objects = collect_unique_objects_for_anonymization(app)
    cim_index_orig = _build_cim_index(objects)

    _pf_bulk_mode_begin(app)
    try:
        for orig_cim, rec in gps_map.items():
            if rec.get("deleted", False):
                continue

            old = rec.get("old")
            if not (isinstance(old, list) and len(old) == 2):
                continue
            old_lat, old_lon = float(old[0]), float(old[1])

            target = cim_index_orig.get(orig_cim)
            if target is None:
                fn = rec.get("full_name_after")
                if isinstance(fn, str) and fn:
                    target = _search_by_full_name_after(app, fn)

            if target is None:
                continue

            safe_set(target, "GPSlat", old_lat, verbose=False)
            safe_set(target, "GPSlon", old_lon, verbose=False)
    finally:
        _pf_bulk_mode_end(app)


# ----------------------------
# Import / Activate / Export
# ----------------------------
def _list_projects(user):
    return user.GetContents("*.IntPrj") or []


def _delete_project_if_exists(app, project_name: str):
    user = app.GetCurrentUser()
    prjs = _list_projects(user)

    target = None
    for p in prjs:
        if getattr(p, "loc_name", "") == project_name:
            target = p
            break
    if not target:
        return

    active = app.GetActiveProject()
    if active and active == target:
        active.Deactivate()

    target.Delete()
    app.ClearRecycleBin()
    _call_pf_or_app(app, "WriteChangesToDb")


def _import_pfd_into_current_user(app, in_path: Path):
    user = app.GetCurrentUser()

    import_obj = user.CreateObject("CompfdImport", "Import")
    import_obj.SetAttribute("e:g_file", _p(in_path))
    import_obj.g_target = user

    rc = import_obj.Execute()
    import_obj.Delete()

    app.ClearRecycleBin()
    _call_pf_or_app(app, "WriteChangesToDb")

    if rc != 0:
        raise RuntimeError(f"PFD import failed. Return code: {rc}")


def _activate_project(app, project_name: str):
    rc = app.ActivateProject(project_name)
    if rc == 0:
        return app.GetActiveProject()

    if project_name.endswith("_anonym"):
        alt_name = project_name[:-7]  # remove "_anonym"
        rc2 = app.ActivateProject(alt_name)
        if rc2 == 0:
            print(f"[INFO] Project name corrected to: {alt_name}")
            return app.GetActiveProject()

    user = app.GetCurrentUser()
    prjs = _list_projects(user)

    for p in prjs:
        if getattr(p, "loc_name", "") in (project_name, project_name.replace("_anonym", "")):
            if hasattr(p, "Activate"):
                p.Activate()
                return app.GetActiveProject()

    print("Available projects:")
    for p in prjs:
        try:
            print(" -", p.loc_name)
        except Exception:
            pass

    raise RuntimeError(f"Could not activate project: {project_name} (rc={rc})")


def _export_project_to_pfd(app, out_path: Path):
    g_object = app.GetActiveProject()
    if g_object:
        g_object.Deactivate()

    pfd_export_obj = app.GetFromStudyCase("ComPfdexport")
    if not pfd_export_obj:
        raise RuntimeError("ComPfdexport not found (StudyCase).")

    pfd_export_obj.g_objects = [g_object]
    pfd_export_obj.g_file = _p(out_path)

    pfd_export_obj.exportCurrentState = 1
    pfd_export_obj.g_undo = 0
    pfd_export_obj.exportModBye = 0
    pfd_export_obj.exportExternalFiles = 0
    pfd_export_obj.g_derivedFlat = 0
    pfd_export_obj.g_formerbuild = 0
    pfd_export_obj.g_targetbuild = ""

    pfd_export_obj.Execute()

    g_object.Delete()
    app.ClearRecycleBin()


# ----------------------------
# Process helper
# ----------------------------
def kill_powerfactory():
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            if proc.info.get("name") and "PowerFactory" in proc.info["name"]:
                proc.kill()
                print("PowerFactory terminated.")
                return
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass


# ----------------------------
# Public entrypoints
# ----------------------------
def run_powerfactory_import_export(
    in_path: Path,
    out_path: Path,
    random_seed: str,
    mapping_out_path: Path,
    desc: bool,
    gps: bool,
    prefix: str = "ANON_",
    hash_length: int = 10,
):
    """
    gps=True  -> delete GPS (0/0) + JSON stores old + cim_after + full_name_after
    gps=False -> transform+jitter + JSON stores old/new
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    mapping_out_path = Path(mapping_out_path)

    kill_powerfactory()

    app = pf.GetApplication()
    if not app:
        raise RuntimeError("PowerFactory Application not available (pf.GetApplication() returned None).")

    app.ClearOutputWindow()
    print("=== anym_PF.py: Start Import/Anonymize/Export ===")

    if not in_path.exists():
        raise FileNotFoundError(f"Input PFD not found: {in_path}")

    project_name = in_path.stem

    _delete_project_if_exists(app, project_name)
    _import_pfd_into_current_user(app, in_path)
    _activate_project(app, project_name)

    gridtocim = app.GetFromStudyCase("ComGridtocim")
    if gridtocim:
        gridtocim.AssignCimRdfIds()

    objects = collect_unique_objects_for_anonymization(app)
    print(f"Objects to anonymize (unique): {len(objects)}")

    anonymizer = anonymize_objects(
        app=app,
        objects=objects,
        seed=random_seed,
        desc=desc,
        gps=gps,
        prefix=prefix,
        length=hash_length,
    )

    save_mapping_json(mapping_out_path, anonymizer)
    print(f"Mapping saved: {mapping_out_path}")

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _export_project_to_pfd(app, out_path)
        print(f"Export written: {out_path}")
    except Exception as e:
        print(f"[WARN] Export not executed: {e}")

    print("=== anym_PF.py: Done ===")


def run_powerfactory_restore(
    in_path: Path,
    out_path: Path,
    mapping_path: Path,
):
    """
    Import PFD -> restore from JSON -> export PFD
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    mapping_path = Path(mapping_path)

    kill_powerfactory()

    app = pf.GetApplication()
    if not app:
        raise RuntimeError("PowerFactory Application not available.")

    if not in_path.exists():
        raise FileNotFoundError(in_path)
    if not mapping_path.exists():
        raise FileNotFoundError(mapping_path)

    project_name = in_path.stem

    _delete_project_if_exists(app, project_name)
    _import_pfd_into_current_user(app, in_path)
    _activate_project(app, project_name)

    restore_from_mapping(app, mapping_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _export_project_to_pfd(app, out_path)
