# anym_PF.py
from __future__ import annotations

from pathlib import Path
import os
import sys
import json
import hashlib
from typing import Dict, List
import math
import psutil

# PowerFactory Python path
sys.path.append(r"C:\Program Files\DIgSILENT\PowerFactory 2024 SP7\Python\3.9")
import powerfactory as pf  # type: ignore


def _p(p: Path) -> str:
    """Path -> str (robust for PF API)."""
    return os.fspath(Path(p).resolve())


def _seed_hash(seed: str, tag: str) -> int:
    h = hashlib.sha256((str(seed) + "|" + tag).encode("utf-8")).hexdigest()
    return int(h[:16], 16)  # 64-bit aus dem Hash

def _seed_unit(seed: str, tag: str) -> float:
    # deterministisch 0..1
    x = _seed_hash(seed, tag)
    return (x % 10_000_000) / 10_000_000.0

def _build_geo_transform(seed: str, max_shift_deg: float = 2.0):
    """
    max_shift_deg: max. Verschiebung in 'Grad' (PF GPSlat/GPSlon sind typischerweise Grad).
    2.0° ~ 222km in Latitude. Passe das an deine Modelle an.
    """
    angle = 2.0 * math.pi * _seed_unit(seed, "gps_angle")  # 0..2pi
    mirror = 1 # _seed_hash(seed, "gps_mirror") % 2            # 0/1

    # shift in Grad (uniform in [-max_shift_deg, +max_shift_deg])
    dx = (2 * _seed_unit(seed, "gps_dx") - 1) * max_shift_deg
    dy = (2 * _seed_unit(seed, "gps_dy") - 1) * max_shift_deg

    c = math.cos(angle)
    s = math.sin(angle)

    def transform(lat: float, lon: float) -> tuple[float, float]:
        # Wir behandeln (lon, lat) als (x, y) in einem flachen Koordinatensystem.
        x = float(lon)
        y = float(lat)

        # Spiegelung an y-Achse (x -> -x) oder keine
        if mirror == 1:
            x = -x

        # Rotation um Ursprung
        xr = c * x - s * y
        yr = s * x + c * y

        # Translation
        xr += dx
        yr += dy

        return float(yr), float(xr)

    return transform, {"angle_deg": angle * 180.0 / math.pi, "mirror": mirror, "dx": dx, "dy": dy}
# ----------------------------
# Helpers: PF call wrappers
# ----------------------------
def _call_pf_or_app(app, name: str, *args):
    if hasattr(pf, name):
        return getattr(pf, name)(*args)
    if hasattr(app, name):
        return getattr(app, name)(*args)
    raise AttributeError(f"Weder pf noch app haben: {name}")


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
# Deterministic ID generators
# ----------------------------
def _generate_seeded_uuid(old_id: str, seed: str) -> str:
    """
    Deterministic UUID-like id with leading '_' based on seed + old_id.
    Keeps format: _xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    """
    clean = str(old_id).lstrip("_")
    payload = (str(seed) + clean).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()

    hex32 = digest[:32]
    uuid = f"{hex32[:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"
    return "_" + uuid


# ----------------------------
# Seeded name anonymizer (loc_name) + CIM mapping storage
# ----------------------------
class SeededNameAnonymizer:
    def __init__(self, seed: str, prefix: str = "ANON_", length: int = 10):
        self.seed = str(seed)
        self.prefix = prefix
        self.length = int(length)

        # loc_name mapping
        self.forward: Dict[str, str] = {}  # old loc_name -> new loc_name
        self.reverse: Dict[str, str] = {}  # new loc_name -> old loc_name

        # cimRdfId mapping
        self.cim_forward: Dict[str, str] = {}  # old cimRdfId -> new cimRdfId

    def _hash(self, text: str, length: int) -> str:
        payload = (self.seed + "\n" + str(text).strip()).encode("utf-8")
        return hashlib.sha256(payload).hexdigest().upper()[:length]

    def translate(self, name: str) -> str:
        if not name:
            return name

        # already anonymized
        if name.startswith(self.prefix):
            return name

        # if it is already a new-name value we used before, don't change it
        if name in self.reverse:
            return name

        # already mapped
        if name in self.forward:
            return self.forward[name]

        token = self._hash(name, self.length)
        new_name = f"{self.prefix}{token}"

        # collision handling (extremely rare)
        L = self.length
        while new_name in self.reverse and self.reverse[new_name] != name:
            L += 2
            token = self._hash(name, L)
            new_name = f"{self.prefix}{token}"

        self.forward[name] = new_name
        self.reverse[new_name] = name
        return new_name


def save_mapping_json(path: Path, anonymizer: SeededNameAnonymizer, geo_info=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "seed": anonymizer.seed,
        "prefix": anonymizer.prefix,
        "length": anonymizer.length,
        "loc_name_mapping": anonymizer.forward,
        "cimRdfId_mapping": anonymizer.cim_forward,
        "gps_transform": geo_info,   # <-- neu
    }

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")



# ----------------------------
# PF object collection
# ----------------------------
class PfObjects:
    def __init__(self, app):
        # project folders / limits
        self.intfolder = app.GetCalcRelevantObjects("*.IntPrjfolder") or []
        self.intqlim = app.GetCalcRelevantObjects("*.IntQlim") or []

        # network model
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

        # types
        self.tline = app.GetCalcRelevantObjects("*.TypLne") or []
        self.tsym = app.GetCalcRelevantObjects("*.TypSym") or []
        self.ttr2 = app.GetCalcRelevantObjects("*.TypTr2") or []
        self.tlod = app.GetCalcRelevantObjects("*.TypLod") or []

    def iter_all_lists(self):
        # include folders/limits first
        yield from self.intqlim
        yield from self.intfolder

        # network objects
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

        # types
        yield from self.tline
        yield from self.tsym
        yield from self.ttr2
        yield from self.tlod


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
def _get_float_attr(obj, attr: str):
    if not obj.HasAttribute(attr):
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
    """
    Robust setter:
    - checks HasAttribute
    - tries SetAttribute(attr, value)
    - if TypeError and value is str -> tries SetAttribute(attr, [value])
    """
    try:
        if not obj.HasAttribute(attr):
            return False
    except Exception as e:
        if verbose:
            print(f"[WARN] HasAttribute({attr}) failed for {obj.GetClassName()}: {e}")
        return False

    try:
        obj.SetAttribute(attr, value)
        return True
    except TypeError as e:
        if isinstance(value, str):
            try:
                obj.SetAttribute(attr, [value])
                return True
            except Exception as e2:
                if verbose:
                    print(f"[WARN] SetAttribute({attr}, [str]) failed: {e2}")
                return False
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


def _get_cim_rdf_id(obj):
    if not obj.HasAttribute("cimRdfId"):
        return []
    try:
        value = obj.GetAttribute("cimRdfId")
        return value or []
    except Exception:
        return []


def _set_cim_rdf_id(obj, new_id: str) -> bool:
    # cimRdfId is usually list[str]
    return safe_set(obj, "cimRdfId", [new_id], verbose=False)


def _sanitize_metadata(obj, desc: bool, gps: bool, *, seed: str, gps_transform):
    # desc
    if desc:
        safe_set(obj, "desc", "Deleted", verbose=False)
    else:
        safe_set(obj, "desc", "ToDo -> yet not supported", verbose=False)
        print("desc adaption todo")

    # GPS
    if gps:
        # löschen
        safe_set(obj, "GPSlat", 0, verbose=False)
        safe_set(obj, "GPSlon", 0, verbose=False)
    else:
        # transformieren (nur wenn Attribute vorhanden UND sinnvolle Werte)
        lat = _get_float_attr(obj, "GPSlat")
        lon = _get_float_attr(obj, "GPSlon")

        if lat is None or lon is None:
            return

        # wenn 0/0 schon gesetzt oder leer, überspringen (optional)
        # (das verhindert, dass "leere" GPS plötzlich irgendwohin springen)
        if abs(lat) < 1e-12 and abs(lon) < 1e-12:
            return

        new_lat, new_lon = gps_transform(lat, lon)
        safe_set(obj, "GPSlat", new_lat, verbose=False)
        safe_set(obj, "GPSlon", new_lon, verbose=False)


def anonymize_cim_rdf_id(obj, seed: str, anonymizer: SeededNameAnonymizer) -> bool:
    ids = _get_cim_rdf_id(obj)
    if not ids:
        return False

    old_id = ids[0]

    # reuse mapping if already seen
    if old_id in anonymizer.cim_forward:
        new_id = anonymizer.cim_forward[old_id]
    else:
        new_id = _generate_seeded_uuid(old_id, seed)
        anonymizer.cim_forward[old_id] = new_id

    return _set_cim_rdf_id(obj, new_id)


def _set_loc_name(obj, desc: bool, gps: bool, new_name: str, *, seed: str, gps_transform):
    _sanitize_metadata(obj, desc, gps, seed=seed, gps_transform=gps_transform)
    obj.SetAttribute("loc_name", new_name)


def _make_unique_if_needed(obj, desc: bool, gps: bool, desired: str, anonymizer: SeededNameAnonymizer, *, seed: str, gps_transform) -> str:
    """
    Setzt loc_name. Wenn PF es nicht akzeptiert, deterministischen Suffix.
    """
    old = _get_loc_name(obj)

    # attempt 1
    try:
        _set_loc_name(obj, desc, gps, desired, seed=seed, gps_transform=gps_transform)
        after = _get_loc_name(obj)
        if after == desired:
            return desired
        raise RuntimeError("PF did not apply loc_name")
    except Exception:
        # attempt 2: deterministic suffix
        try:
            base = obj.GetFullName()
        except Exception:
            base = f"{obj.GetClassName()}::{old}"
        suffix = anonymizer._hash(base, 6)
        candidate = f"{desired}_{suffix}"

        _set_loc_name(obj, desc, gps, candidate, seed=seed, gps_transform=gps_transform)
        after2 = _get_loc_name(obj)
        if after2 != candidate:
            raise RuntimeError(f"Rename failed: {old} -> {desired} (candidate {candidate} not applied)")
        return candidate


def anonymize_objects(app, objects: List, seed: str, desc: bool, gps:bool, prefix: str = "ANON_", length: int = 10) -> SeededNameAnonymizer:
    anonymizer = SeededNameAnonymizer(seed=seed, prefix=prefix, length=length)
    gps_transform, geo_info = _build_geo_transform(seed, max_shift_deg=2.0)
    _pf_bulk_mode_begin(app)
    try:
        for obj in objects:
            # 1) anonymize cimRdfId (and store mapping)
            anonymize_cim_rdf_id(obj, seed, anonymizer)

            # 2) anonymize loc_name (and store mapping)
            old = _get_loc_name(obj)
            if not isinstance(old, str) or not old.strip():
                continue

            new = anonymizer.translate(old)
            if new != old:
                _make_unique_if_needed(obj, desc, gps, new, anonymizer, seed=seed, gps_transform=gps_transform)


    finally:
        _pf_bulk_mode_end(app)

    return anonymizer, geo_info


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
        raise RuntimeError(f"PFD Import fehlgeschlagen. Returncode: {rc}")


def _activate_project(app, project_name: str):
    # ActivateProject returns int in many PF versions: 0 success
    rc = app.ActivateProject(project_name)
    if rc == 0:
        return app.GetActiveProject()

    # Fallback: activate via object
    user = app.GetCurrentUser()
    prjs = _list_projects(user)

    match = None
    for p in prjs:
        if getattr(p, "loc_name", "") == project_name:
            match = p
            break

    if match and hasattr(match, "Activate"):
        match.Activate()
        return app.GetActiveProject()

    # Debug
    print("Projekte nach Import:")
    for p in prjs:
        try:
            print(" -", p.loc_name, "| FullName:", p.GetFullName())
        except Exception:
            print(" -", p.loc_name)

    raise RuntimeError(f"Konnte Projekt nicht aktivieren: {project_name} (rc={rc})")


def _export_project_to_pfd(app, out_path: Path):
    """
    Your working export variant using ComPfdexport (study case).
    """
    g_object = app.GetActiveProject()
    if g_object:
        g_object.Deactivate()

    pfd_export_obj = app.GetFromStudyCase("ComPfdexport")
    if not pfd_export_obj:
        raise RuntimeError("ComPfdexport nicht gefunden (StudyCase).")

    # PF expects lists of objects
    pfd_export_obj.g_objects = [g_object]
    pfd_export_obj.g_file = _p(out_path)

    # options (as you had)
    pfd_export_obj.exportCurrentState = 1
    pfd_export_obj.g_undo = 0
    pfd_export_obj.exportModBye = 0
    pfd_export_obj.exportExternalFiles = 0
    pfd_export_obj.g_derivedFlat = 0
    pfd_export_obj.g_formerbuild = 0
    pfd_export_obj.g_targetbuild = ""

    pfd_export_obj.Execute()

    # Clean up project after export (as you did)
    g_object.Delete()
    app.ClearRecycleBin()


def kill_powerfactory():
    # Gehe durch alle Prozesse und prüfe, ob PowerFactory läuft
    for proc in psutil.process_iter(attrs=['pid', 'name']):
        try:
            # Prüfe, ob der Prozessname 'PowerFactory.exe' ist
            if "PowerFactory" in proc.info['name']:
                proc.kill()  # Beende den Prozess
                print("PowerFactory erfolgreich beendet.")
                return
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
# ----------------------------
# MAIN ENTRY
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
    in_path = Path(in_path)
    out_path = Path(out_path)
    mapping_out_path = Path(mapping_out_path)
    kill_powerfactory()
    app = pf.GetApplication()
    if not app:
        raise RuntimeError("PowerFactory Application nicht verfügbar (pf.GetApplication() gab None zurück).")

    app.ClearOutputWindow()
    print("=== anym_app.py: Start Import/Anonymize/Export ===")

    if not in_path.exists():
        raise FileNotFoundError(f"Input-PFD nicht gefunden: {in_path}")

    project_name = in_path.stem  # without .IntPrj

    # remove existing project with same name
    _delete_project_if_exists(app, project_name)

    # import + activate
    _import_pfd_into_current_user(app, in_path)
    _activate_project(app, project_name)

    # ensure CIM ids exist (your command)
    gridtocim = app.GetFromStudyCase("ComGridtocim")
    if gridtocim:
        result = gridtocim.AssignCimRdfIds()
        if result:
            print("Fehlende CIM IDs gesetzt")

    # collect + anonymize
    objects = collect_unique_objects_for_anonymization(app)
    print(f"Zu anonymisierende Objekte (unique): {len(objects)}")

    anonymizer, geo_info = anonymize_objects(
        app=app,
        objects=objects,
        seed=random_seed,
        prefix=prefix,
        length=hash_length,
        desc=desc,
        gps=gps,
    )

    # save mappings (loc_name + cimRdfId)
    if gps == True:
        geo_info = ["not changed"]


    save_mapping_json(mapping_out_path, anonymizer, geo_info)
    print(f"Mapping gespeichert: {mapping_out_path}")

    # export
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _export_project_to_pfd(app, out_path)
        print(f"Export geschrieben: {out_path}")
    except Exception as e:
        print(f"[WARN] Export nicht durchgeführt: {e}")

    print("=== anym_app.py: Fertig ===")
