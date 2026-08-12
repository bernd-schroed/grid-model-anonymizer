"""
anym_pf.py - PowerFactory (.pfd) anonymizer
============================================

Anonymizes a DIgSILENT PowerFactory project (.pfd) in place via the
PowerFactory Python API, using the same seed-based deterministic
approach and mapping JSON shared with anym_cgmes / anym_csv.

Workflow
--------
1. Locate the installed PowerFactory version and append its Python
   API path (`<PF install>\\Python\\<major.minor>`) to sys.path before
   `import powerfactory` is attempted.
2. Import the source .pfd into a temporary PowerFactory project.
3. Walk all relevant network objects (elements, types, switches,
   cubicles, graphics) and:
   - anonymize loc_name and a fixed set of string attributes
     (sernum, constr, chr_name, dar_src, manuf, for_name,
     foreignKey) via deterministic token substitution,
   - anonymize or delete (`desc=True`) object descriptions,
   - transform+jitter or delete (`gps=True`) GPS coordinates,
   - optionally remap CIM RDF identifiers.
4. Export the anonymized project back out as a .pfd and write a
   mapping JSON recording every original -> anonymized value, which
   is later used to fully reverse the process (`run_powerfactory_restore`).

Design rationale
----------------
- All renames/attribute writes go through `safe_set` / `_get_*_attr`
  helpers that tolerate objects which don't support a given attribute
  (PowerFactory classes are heterogeneous), so a missing attribute on
  one object type never aborts the whole run.
- Bulk operations are wrapped in `_pf_bulk_mode_begin/_end` to disable
  GUI/progress-bar updates and enable the write cache, which is
  required for acceptable performance on larger projects.
- GPS anonymization runs as a second pass, after names/cimRdfIds have
  already been changed, so the mapping can key GPS records by the
  *original* cimRdfId / full path even though the object itself has
  since been renamed.
- Restoring is the import/export workflow run with the mapping JSON
  applied in reverse: deleted-GPS records are restored first (while
  identifiers are still resolvable), then loc_name/attributes/desc,
  then cimRdfId, then transformed-GPS records last.

Requires a local PowerFactory installation (searches
`C:\\Program Files\\DIgSILENT` and `C:\\Program Files (x86)\\DIgSILENT`)
and a matching PowerFactory-compatible Python interpreter version;
exits with an error message at import time if no compatible version
is found. Depends on: psutil, utils (SeededNameAnonymizer etc.).
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psutil

from utils import (
    SeededNameAnonymizer,
    _build_geo_transform,
    _generate_seeded_uuid,
    _has_suffix,
    _meters_to_deg_lat,
    _meters_to_deg_lon,
    _obj_unit_from_name,
    _p,
    _scale_back_to_valid_geo,
    _seed_unit,
    get_mappings,
    save_mapping_json,
)

logger = logging.getLogger(" anym_pf.py")

IMPEDANCE_TYPES = [
    "rline",
    "xline",
    "rline0",
    "xline0",
]  # do the 0 impedances actually need to be reset?


# ----------------------------
# power factory version check
# ----------------------------
def get_pf_version() -> Path:
    """
    Locate the newest installed PowerFactory version.

    Scans the standard DIgSILENT install directories
    (Program Files / Program Files (x86)) for "PowerFactory*"
    subfolders, excluding the License Manager, and returns the path
    of the highest version found.

    Returns
    -------
    Path
        Install directory of the most recent PowerFactory version.
    """
    # Getting the PowerFactory Version
    search_paths = [
        Path(r"C:\Program Files\DIgSILENT"),
        Path(r"C:\Program Files (x86)\DIgSILENT"),
    ]

    versions = {}

    for base in search_paths:
        if not base.exists():
            continue

        for entry in base.iterdir():
            if entry.is_dir() and entry.name.startswith("PowerFactory"):
                version = entry.name.replace("PowerFactory", "").strip()

                versions[version] = str(entry)

    versions = {
        version: path
        for version, path in versions.items()
        if "LicenceManager".lower() not in version.lower()
    }
    if not versions:
        return False
    else:
        _, last_path = sorted(versions.items())[-1]
        return Path(last_path)


def check_python_pf_compatibility(powerfactory_path: Path, py_version: str) -> None:
    """
    Verify that the running Python's major.minor version is supported
    by the detected PowerFactory installation.

    Exits the process with an explanatory message listing the
    compatible Python versions if py_version is not among the
    subfolders under "<pf_path>/Python".
    """
    search_path = Path(powerfactory_path, "Python")
    possible_versions = [version.name for version in search_path.iterdir()]
    if not any(version == py_version for version in possible_versions):
        raise RuntimeError(
            f"""\nError: This Python Version {py_version} is not compatible with the current 
            version of PowerFactory. Try one of the following Python versions instead: 
            {possible_versions}.\n"""
        )


pf_path = get_pf_version()
if pf_path is False:
    pf = None  # pylint:disable=invalid-name
    logger.warning("No PowerFactory installation found in standard locations.")
else:
    # set python version
    python_major_version = sys.version_info.major
    python_minor_version = sys.version_info.minor
    PY_VERSION = f"{str(python_major_version)}.{str(python_minor_version)}"

    check_python_pf_compatibility(pf_path, PY_VERSION)

    # PowerFactory Python path
    pf_python_path = Path(pf_path, "Python", PY_VERSION)

    sys.path.append(str(pf_python_path))

    import powerfactory as pf  # type: ignore # pylint: disable=import-error,wrong-import-position,wrong-import-order


# ----------------------------
# PF call wrappers
# ----------------------------
def _call_pf_or_app(app, name: str, *args):
    """
    Call a method by name on whichever of `pf` or `app` defines it.

    PowerFactory exposes some functions on the `powerfactory` module
    itself and others on the application object, depending on version;
    this abstracts over that difference.

    Raises
    ------
    AttributeError
        If neither `pf` nor `app` defines `name`.
    """

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
# PF object collection
# ----------------------------
class PfObjects:
    """
    Collects all network-relevant PowerFactory objects for a project.

    On construction, gathers every object matching a fixed set of class
    patterns (elements, types, switches, cubicles, graphics, project
    folders), deletes any CimModel objects found in the active project,
    and clears (renames to "Deleted") any IntGrf map info objects, since
    these typically carry no anonymization-relevant data but may leak
    project metadata.
    """

    def __init__(self, app):
        """
        Build the object collection for the given PowerFactory application.

        Parameters
        ----------
        app : the PowerFactory application object (from pf.GetApplication()).
        """
        patterns = [
            "*.IntPrjfolder",
            "*.IntQlim",
            "*.Elm*",
            "*.ElmLod",
            "*.Typ*",
            "*.StaSwitch",
            "*.StaCubic",
        ]

        # add all calculation relevant objects
        self.objects = []
        for pat in patterns:
            try:
                self.objects += app.GetCalcRelevantObjects(pat) or []
            except (AttributeError, TypeError):
                pass

        # delete cim models for anonymization
        project = app.GetActiveProject()
        cim_models = project.GetContents("*.CimMdel", 1)
        for cim_model in cim_models:
            try:
                cim_model.Delete()
            except AttributeError:
                pass

        # add certain objects, that are not relevant to calculations e.g. graphics names to objects
        patterns = [
            "*.IntGrfnet",
            "*.IntEvt",
            "*.IntPlannedout",
            "*.EvtShc",
            "*.IntCase",
        ]
        for pat in patterns:
            new_objs = project.GetContents(pat, 1)
            try:
                self.objects += new_objs or []
            except (AttributeError, TypeError) as e:
                logger.error("Error adding %s to objects: %s", pat, e)

        mapsinfos = project.GetContents("*.IntGrf", 1)

        # delete names of graphical elements
        for single_map in mapsinfos:
            try:
                # map.Delete()
                single_map.loc_name = "Deleted"
            except AttributeError:
                pass

    def iter_all_lists(self):
        """Yield every collected PF object."""
        yield from self.objects


def parent_chain_until_network_data(obj) -> List:
    """
    Walk up an object's parent chain, stopping after "Network Data".

    Returns
    -------
    List
        The object plus all ancestors, starting with `obj` itself and
        ending at the first ancestor named "Network Data" (inclusive),
        or at the root if "Network Data" is never reached.
    """
    chain = []
    current = obj
    while current:
        chain.append(current)
        if getattr(current, "loc_name", None) == "Network Data":
            break
        current = current.GetParent()
    return chain


def collect_unique_objects_for_anonymization(app) -> List:
    """
    Build a deduplicated list of all objects to anonymize, including
    their parent chains.

    Collects the base object set via PfObjects, then walks each
    object's parent chain (up to "Network Data") and includes those
    ancestors as well, deduplicating by full name (falling back to
    class+loc_name if GetFullName fails).

    Returns
    -------
    List
        Unique PF objects (original objects plus their relevant ancestors).
    """
    pf_objs = PfObjects(app)
    unique: Dict[str, object] = {}

    for obj in pf_objs.iter_all_lists():
        try:
            key = obj.GetFullName()
        except AttributeError:
            key = f"{obj.GetClassName()}::{getattr(obj, 'loc_name', '')}"
        unique.setdefault(key, obj)

        for parent in parent_chain_until_network_data(obj):
            try:
                pkey = parent.GetFullName()
            except AttributeError:
                pkey = f"{parent.GetClassName()}::{getattr(parent, 'loc_name', '')}"
            unique.setdefault(pkey, parent)

    return list(unique.values())


def make_obj_dict(objects: List) -> Dict[str, object]:
    """
    Create a Dictionary from a list of objects with a clear key
    to search make searching for certain objects easier

    Parameters
    ----------
    objects: List
        The object list

    Returns
    -------
    objects_dict: Dict
        The object list as a dictionary
    """
    objects_dict: Dict[str, object] = {}

    for obj in objects:
        # since one loc_name can be given to multiple loc names
        # the obj_class is added to the key
        obj_name = _get_loc_name(obj)
        obj_class = obj.GetClassName()
        obj_key = obj_name + "." + obj_class
        objects_dict[obj_key] = obj
    return objects_dict


# ----------------------------
# Safe attribute helpers
# ----------------------------
def _get_float_attr(obj, attr: str) -> Optional[float]:
    """
    Safely read a PF attribute as a float.

    Returns None if the attribute doesn't exist, is unset, or can't be
    converted to a float (instead of raising).
    """
    try:
        if not obj.HasAttribute(attr):
            return None
    except AttributeError:
        return None

    try:
        v = obj.GetAttribute(attr)
        if v is None:
            return None
        return float(v)
    except AttributeError:
        try:
            return float(getattr(obj, attr))
        except AttributeError:
            return None


def safe_set(obj, attr, value, *, verbose: bool = False) -> bool:
    """
    Safely set a PF attribute, tolerating objects that don't support it.

    Checks HasAttribute first, then attempts SetAttribute; on a
    TypeError, retries with the value wrapped in a list (PF sometimes
    expects list-typed values for string attributes). Failures are
    swallowed and optionally logged via `verbose`.

    Returns
    -------
    bool
        True if the attribute was successfully set, False otherwise.
    """
    try:
        if not obj.HasAttribute(attr):
            return False
    except AttributeError as e:
        if verbose:
            logger.warning("HasAttribute(%s) failed: %s", attr, e)
        return False

    try:
        obj.SetAttribute(attr, value)
        return True
    except TypeError as e:
        if isinstance(value, str):
            try:
                obj.SetAttribute(attr, [value])
                return True
            except TypeError:
                pass
        if verbose:
            logger.warning(
                "TypeError SetAttribute(%s) on %s (%s): %s",
                attr,
                obj.GetClassName(),
                getattr(obj, "loc_name", ""),
                e,
            )
        return False
    except AttributeError as e:
        if verbose:
            logger.warning(
                "SetAttribute(%s) failed on %s (%s): %s",
                attr,
                obj.GetClassName(),
                getattr(obj, "loc_name", ""),
                e,
            )
        return False


def _get_loc_name(obj) -> str:
    try:
        return obj.GetAttribute("loc_name")
    except AttributeError:
        return getattr(obj, "loc_name", "")


def _set_loc_name_only(obj, new_name: str):
    try:
        return obj.SetAttribute("loc_name", new_name)
    except AttributeError:
        return setattr(obj, "loc_name", new_name)


def _get_str_attr(obj, attr: str) -> Optional[str]:
    try:
        if not obj.HasAttribute(attr):
            return None
    except AttributeError:
        return None

    try:
        v = obj.GetAttribute(attr)
    except AttributeError:
        try:
            v = getattr(obj, attr)
        except AttributeError:
            return None

    if v is None:
        return ""

    # PF attributes may be list-like
    if isinstance(v, (list, tuple)):
        if len(v) == 0:
            return ""
        v0 = v[0]
        return "" if v0 is None else str(v0)

    return str(v)


def _set_str_attr(obj, attr: str, value: str) -> bool:
    return safe_set(obj, attr, str(value), verbose=False)


def _get_cim_rdf_id(obj) -> List[str]:
    try:
        if not obj.HasAttribute("cimRdfId"):
            return []
    except AttributeError:
        return []
    try:
        value = obj.GetAttribute("cimRdfId")
        return value or []
    except AttributeError:
        return []


def _set_cim_rdf_id(obj, new_id: str) -> bool:
    return safe_set(obj, "cimRdfId", [new_id], verbose=False)


def _get_full_name(obj) -> str:
    try:
        return obj.GetFullName()
    except AttributeError:
        return f"{obj.GetClassName()}::{_get_loc_name(obj)}"


def _to_project_relative(full_name: str) -> str:
    marker = r"\Network Model.IntPrjfolder"
    i = full_name.find(marker)
    if i < 0:
        return full_name
    return full_name[i:]


def _search_by_full_name_after(app, full_name_after: str):
    project = app.GetActiveProject()
    if not project:
        return None

    rel = _to_project_relative(full_name_after)
    try:
        return project.SearchObject(rel)
    except AttributeError:
        return None


# ----------------------------
# Anonymize primitives
# ----------------------------
def anonymize_cim_rdf_id(obj, seed: str, anonymizer: SeededNameAnonymizer) -> None:
    """
    Deterministically remap an object's cimRdfId and record the mapping.

    No-op if the object has no cimRdfId. Reuses an existing mapping
    entry if this ID was already remapped.
    """
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


def anonymize_string_fields(
    obj,
    *,
    anonymizer: SeededNameAnonymizer,
    fields: List[str],
    empty_as_zero: bool = True,
):
    """
    Anonymize a fixed set of string attributes on a PF object in place.

    For each field in `fields`: skips fields that don't exist; if the
    value is empty and `empty_as_zero` is True, substitutes a value
    derived from the object's pid_/oid_ before translating (so empty
    fields still get a deterministic anonymized value); otherwise
    leaves genuinely empty fields untouched.
    """
    for attr in fields:
        old = _get_str_attr(obj, attr)
        if old is None:
            continue

        old_s = str(old).strip()

        if not old_s and empty_as_zero:
            old_s = str(int(obj.pid_) + int(obj.oid_))
        elif not old_s:
            continue

        new_s = anonymizer.translate_attr(attr, old_s)

        if new_s != old_s:
            _set_str_attr(obj, attr, new_s)


def _make_unique_if_needed(obj, desired: str, anonymizer: SeededNameAnonymizer) -> str:
    old = _get_loc_name(obj)
    full = _get_full_name(obj)
    exception_list = [
        "IntArea",
        "IntBmu",
        "IntBoundary",
        "IntBbone",
        "IntCircuit",
        "IntDependency",
        "IntFeeders",
        "IntLvscale",
        "IntOperator",
        "IntOwner",
        "IntStyle",
        "IntPath",
        "IntRoute",
        "IntZone",
        "SetFold",
        "Fault.IntCase",
    ]
    if full.endswith(".IntPrjfolder"):
        return
    if full.endswith(tuple(exception_list)):
        return
    try:
        _set_loc_name_only(obj, desired)
        if _get_loc_name(obj) == desired:
            return desired
        raise RuntimeError("PF did not apply loc_name")
    except AttributeError:
        try:
            base = _get_full_name(obj)
        except AttributeError:
            base = f"{obj.GetClassName()}::{old}"
        suffix = anonymizer.get_hash(base, 6)
        candidate = f"{desired}_{suffix}"
        _set_loc_name_only(obj, candidate)
        if _get_loc_name(obj) != candidate:
            logger.warning(
                "Rename failed: %s -> %s (candidate %s not applied)",
                old,
                desired,
                candidate,
            )
        return candidate


# ----------------------------
# DESC handling
# ----------------------------
def _sanitize_desc(obj, desc: bool, anonymizer: SeededNameAnonymizer):
    """
    desc=True  -> delete description (write 'Deleted')
    desc=False -> anonymize description (token-based, reversible via anon_mapping)
    """
    if desc:
        safe_set(obj, "desc", "Deleted", verbose=False)
        return

    old = _get_str_attr(obj, "desc")
    if old is None:
        return

    old_s = str(old)
    if old_s.strip() == "":
        return

    new_s = _desc_anonymize(old_s, anonymizer)
    if new_s != old_s:
        safe_set(obj, "desc", new_s, verbose=False)


def _desc_normalize(s: str) -> str:
    if s is None:
        return ""

    t = str(s).replace("(", "").replace(")", "")

    out = []
    prev_space = False
    for ch in t:
        if ch.isspace():
            if not prev_space:
                out.append(" ")
            prev_space = True
        else:
            out.append(ch)
            prev_space = False

    return "".join(out)


def _desc_tokenize_keep_delims(s: str) -> List[Tuple[str, bool]]:
    s = _desc_normalize(s)

    items: List[Tuple[str, bool]] = []
    buf: List[str] = []

    def flush_token():
        nonlocal buf
        if buf:
            tok = "".join(buf)
            if tok != "":
                items.append((tok, False))
            buf = []

    for ch in s:
        if ch == ";":
            flush_token()
            items.append((";", True))
        elif ch == " ":
            flush_token()
            items.append((" ", True))
        else:
            buf.append(ch)

    flush_token()

    while items and items[0] == (" ", True):
        items.pop(0)
    while items and items[-1] == (" ", True):
        items.pop()

    return items


def _desc_anonymize(desc_value: str, anonymizer: "SeededNameAnonymizer") -> str:
    seq = _desc_tokenize_keep_delims(desc_value)
    if not seq:
        return desc_value if desc_value is not None else ""

    out_parts: List[str] = []
    for text, is_delim in seq:
        if is_delim:
            out_parts.append(text)
        else:
            tok = text.strip()
            if tok == "":
                continue
            out_parts.append(anonymizer.translate(tok))

    out = _desc_normalize("".join(out_parts)).strip()
    out = out.replace(" ", ";")
    while ";;" in out:
        out = out.replace(";;", ";")
    out = out.strip(";")

    return out


def _desc_restore(desc_value: str, anon_rev: Dict[str, str], prefix: str) -> str:
    seq = _desc_tokenize_keep_delims(desc_value)
    if not seq:
        return desc_value if desc_value is not None else ""

    out_parts: List[str] = []
    for text, is_delim in seq:
        if is_delim:
            out_parts.append(text)
        else:
            tok = text.strip()
            if tok == "":
                continue
            if tok.startswith(prefix):
                out_parts.append(anon_rev.get(tok, tok))
            else:
                out_parts.append(tok)

    out = _desc_normalize("".join(out_parts)).strip()
    out = _collapse_semicolons(out)
    return out


# ----------------------------
# GPS handling
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

    try:
        logger.debug("GPS coordinates: %s", obj.GPScoords)
    except AttributeError:
        pass

    try:
        obj.GPScoords = [[0.0, 0.0] for _ in obj.GPScoords]
    except AttributeError:
        pass

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

    new_lat, new_lon = _scale_back_to_valid_geo(old_lat, old_lon, new_lat, new_lon)

    anonymizer.gps_mapping.setdefault(
        orig_cim_id,
        {
            "old": [float(old_lat), float(old_lon)],
            "new": [float(new_lat), float(new_lon)],
        },
    )

    safe_set(obj, "GPSlat", float(new_lat), verbose=False)
    safe_set(obj, "GPSlon", float(new_lon), verbose=False)


# ----------------------------
# Line Resetting Handlers
# ----------------------------


def set_impedances(
    old_type: object,
    new_type: object,
    ratio: float,
    anonymizer: SeededNameAnonymizer,
    ln_name: str,
) -> None:
    """
    For power line type resetting, set the new impedances for that line

    Parameters
    ----------
    old_type, new_type : the line type objects with the the old impedance and the new
    ratio              : the ratio between their impedances
    """

    for impedance_type in IMPEDANCE_TYPES:
        impedance_value_per_km = _get_float_attr(old_type, impedance_type)
        if impedance_value_per_km is None:
            return

        alteration_seed = _seed_unit(
            seed=anonymizer.seed,
            tag=f"impedance_alteration_{impedance_type}_{ln_name}",
        )
        alteration_factor = 1.0 + (alteration_seed - 0.5) * 0.2

        new_impedance_per_km = impedance_value_per_km * ratio * alteration_factor
        safe_set(new_type, impedance_type, float(new_impedance_per_km), verbose=False)


def set_line_length(obj: object, anonymizer: SeededNameAnonymizer) -> None:
    """
    reset the line lengths and the new line type and storing it in the anonymizer
    for the mapping

    Parameters
    ----------
    obj: Line object
        the line object (not the line type object)
    anonymizer: SeededNameAnonymizer
        the used anonymizer object
    """

    # check if the object is a power line and actually needs length resetting
    obj_name = _get_loc_name(obj)
    new_name = obj_name + "LineType"
    old_len = _get_float_attr(obj, "dline")
    if old_len is None:
        return
    if old_len == 1 or old_len == 0:
        return

    # create the new line type from old one
    ln_type = obj.GetType()
    ln_name = _get_loc_name(ln_type)
    new_type = create_new_line_type(ln_type, new_name)

    # reset the impedance, since the new line length is always 1 km the ratio = old length
    impedance_ratio = old_len / 1
    set_impedances(ln_type, new_type, impedance_ratio, anonymizer, ln_name)

    # save the new line in the anonymizer
    anonymizer.line_mapping.setdefault(
        new_name,
        {
            "name": ln_name,
            "length": old_len,
        },
    )
    # anonymizer.line_mapping[new_name]["name"] = ln_name
    # anonymizer.line_mapping[new_name]["length"] = old_len

    # reset the line data
    safe_set(obj, "dline", float(1), verbose=False)
    safe_set(obj, "typ_id", new_type, verbose=False)


def create_new_line_type(old_type, new_name: str):
    """
    create a new line type object as a copy of the old line type

    Parameters
    ----------
    old_type : line type object
        The old line type
    new_name : string
        The name of the new line type object

    Returns
    -------
    new_type : line type object
        The new line type
    """
    parent = old_type.GetParent()
    new_type = parent.AddCopy(old_type, new_name)
    return new_type


# ----------------------------
# Time Anonymization
# ----------------------------


def anonymize_time(obj, anonymizer):
    """
    Set a new anonymized time for powerfactory object.

    Parameters
    ----------
    obj : type object
        The powerfactory object to be time updated
    anonymizer : anonymizer
        The anonymizer object used for the anonymization

    """
    old_time = int(_get_float_attr(obj, "iStudyTime"))
    new_time = anonymizer.add_time(old_time)
    safe_set(obj, "iStudyTime", new_time)


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
    gps_transform = _build_geo_transform(seed)

    # Store original keys for the second pass:
    # python object id -> (orig_cim_id, orig_loc_name)
    orig_keys: Dict[int, Tuple[Optional[str], Optional[str]]] = {}

    _pf_bulk_mode_begin(app)

    try:
        for obj in objects:
            full = obj.GetFullName()
            if not full:
                continue
            if full.endswith(".IntCase"):
                anonymize_time(obj, anonymizer)
                continue

            if (
                full.endswith(".IntPrj")
                or full.endswith(".IntUser")
                or full.startswith(r"\Lib.IntLibrary")
                or full.endswith(".IntFltcases")
                or not _has_suffix(full)
            ):
                continue

            if full.endswith(".ElmLne"):
                obj.GPScoords = [[0.0, 0.0]]  # [[0.0, 0.0] for _ in obj.GPScoords]

            ids = _get_cim_rdf_id(obj)
            orig_cim = ids[0] if ids else None
            orig_loc = _get_loc_name(obj)

            orig_keys[id(obj)] = (orig_cim, orig_loc)

            anonymize_string_fields(
                obj,
                anonymizer=anonymizer,
                fields=[
                    "sernum",
                    "constr",
                    "chr_name",
                    "dar_src",
                    "manuf",
                    "for_name",
                    "foreignKey",
                ],
                empty_as_zero=True,
            )

            # If you want to anonymize cimRdfId too, uncomment:
            # anonymize_cim_rdf_id(obj, seed, anonymizer)

            _sanitize_desc(obj, desc, anonymizer)

            # loc_name
            if isinstance(orig_loc, str) and orig_loc.strip():
                new_name = anonymizer.translate(orig_loc)
                if new_name != orig_loc:
                    _make_unique_if_needed(obj, new_name, anonymizer)

            # (redundant second call removed in original? kept behavior minimal)
            anonymize_string_fields(
                obj,
                anonymizer=anonymizer,
                fields=[
                    "sernum",
                    "constr",
                    "chr_name",
                    "dar_src",
                    "manuf",
                    "for_name",
                    "foreignKey",
                ],
                empty_as_zero=True,
            )

    finally:
        _pf_bulk_mode_end(app)

    _pf_bulk_mode_begin(app)
    try:
        for obj in objects:
            full = obj.GetFullName()
            if (
                full.endswith(".IntPrj")
                or full.endswith(".IntUser")
                or full == ""
                or full is None
            ):
                continue
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

            set_line_length(obj, anonymizer=anonymizer)
    finally:
        _pf_bulk_mode_end(app)

    return anonymizer


def _build_cim_index(objs: List) -> Dict[str, object]:
    idx: Dict[str, object] = {}
    for o in objs:
        ids = _get_cim_rdf_id(o)
        if ids:
            idx[ids[0]] = o
    return idx


# ----------------------------
# Restore procedure (UNIFIED)
# ----------------------------

import re  # pylint:disable=wrong-import-position, wrong-import-order

_ANON_RE = re.compile(r"\bANON_[0-9A-F]{6,}\b")  # 6+ damit auch längere Hashes gehen


def _collapse_semicolons(s: str) -> str:
    s = re.sub(r";{2,}", ";", s)  # ;; oder mehr -> ;
    s = s.strip(";")
    return s


def restore_anon_tokens_in_text(text: str, anon_rev: Dict[str, str]) -> str:
    """
    Replace every ANON_<hash> token found in free text with its
    original value from `anon_rev`, leaving unrecognized tokens as-is.
    """

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        return anon_rev.get(tok, tok)

    return _ANON_RE.sub(repl, text)


def restore_gps(
    app,
    gps_map: Dict[str, Dict],
    cim_index_current: Dict[str, object],
    cim_map: Dict[str, str],
) -> None:
    """
    The restoration of the gps data in a function. This represents
    the first iteration of the gps restoration, that handles

    Parameters
    app: PowerFactory Application

    gps_map: Dict[str, Dict]
        The mapping of the gps data. The key is the cim reference and
        data is a dictionary with old and new gps coordinates
    cim_index_current: Dict[str, object]
        The Cim References corresponding to each object.
    cim_map: Dict[str, str]
        The cim mapping with the old and new cim reference
    """
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
            logger.warning("Deleted-GPS target not found (orig_cim=%s)", orig_cim)
            continue

        safe_set(target, "GPSlat", old_lat, verbose=False)
        safe_set(target, "GPSlon", old_lon, verbose=False)


def restore_times(objects: List, time_rev: Dict):
    """
    A function to iterate through all objects. If they are an "IntCase" they are being restored.

    Parameters
    ----------
    objects : list of objects
        list of all objects in the project
    time_rev : Dict
        Dict of the anonymized times and their original counterparts
    """
    for obj in objects:
        full = _get_full_name(obj)
        if full.endswith("IntCase"):
            restore_timestamp(obj, time_rev)


def restore_timestamp(obj, time_rev: Dict):
    """
    Gets the anonymized time from the object, gets the original time and restores it to the object

    Parameters
    ----------
    obj : powerfactory object
        the object to be reset time
    time_rev : Dict
        Dict of the anonymized times and their original counterparts
    """
    anym_time = int(_get_float_attr(obj, "iStudyTime"))
    orig_time = int(time_rev[str(anym_time)])
    safe_set(obj, "iStudyTime", orig_time, verbose=False)


def get_all_line_types(objects_dict: Dict[str, object]) -> Dict[str, object]:
    """
    Since power Factory only gives the line types, that are currently used in a
    project, this function combines all the unused and used types to a new line type dictionary.

    Parameters
    ----------
    objects_dict: Dict[str, object]
        The objects dictionary with every used object

    Returns
    -------
    all_types_dict: Dict[str, object]
        A Dictionary that contains all the line type objects used and unused
    """
    for obj_key, obj in objects_dict.items():
        # only one instance of "TypLne" is necessary, since we can find all the other "TypLne"
        # with the "GetParent" and "GetChildren" command.
        if obj_key.endswith("LineType.TypLne"):
            type_library = obj.GetParent()
            all_types = type_library.GetChildren(1)
            all_types_dict = make_obj_dict(all_types)
            return all_types_dict

    raise AttributeError(
        "The current project does not use any '.TypLne' ",
        "Objects. Line Type restoring is not possible",
    )


def restore_line_type(
    objects_dict: Dict[str, object], line_map: Dict[str, str]
) -> None:
    """
    Restoring all old line types, line lengths and impedances

    Parameters
    ----------
    objects_dict: Dict[str, object]
        The dictionary with all objects and a clear key
    line_rev:  Dict[str, str]
        The reverse mapping with all the new anonymious linetype names as key
        and old lines as data
    """

    all_types = get_all_line_types(objects_dict)

    for ln_type_key, ln_type_obj in all_types.items():

        if ln_type_key.endswith("LineType.TypLne"):

            # get all the data about the line and line type
            anon_line_name = ln_type_key[:15]

            line_map_key = ln_type_key[:23]
            orig_type = line_map[line_map_key]

            anon_line_key = anon_line_name + ".ElmLne"
            line_obj = objects_dict[anon_line_key]

            orig_type_key = orig_type["name"] + ".TypLne"
            orig_type_obj = all_types[orig_type_key]

            orig_length = orig_type["length"]

            # reset the line information
            safe_set(line_obj, "dline", float(orig_length), verbose=False)
            safe_set(line_obj, "typ_id", orig_type_obj, verbose=False)

            # delete the anon now unused line object
            ln_type_obj.Delete()


def restore_from_mapping(app, mapping_path: Path):
    """
    Restore inside an already imported project using the mapping JSON:
    - Restore GPS first for deleted=true (while anonymized identifiers are still available)
    - Restore loc_name, attributes, desc using unified anon_mapping (reverse lookup by ANON_*)
    - Restore cimRdfId
    - Restore GPS for transformed cases AFTER cim restore
    """
    (
        line_map,
        anon_rev,
        time_rev,
        cim_rev,
        cim_map,
        gps_map,
        prefix,
    ) = get_mappings(mapping_path)

    objects = collect_unique_objects_for_anonymization(app)
    # ---------------------------------------------------------
    # 1) Restore GPS for deleted=true BEFORE renaming anything and reset lines
    # ---------------------------------------------------------
    cim_index_current = _build_cim_index(objects)

    _pf_bulk_mode_begin(app)
    try:
        restore_gps(
            app=app,
            gps_map=gps_map,
            cim_index_current=cim_index_current,
            cim_map=cim_map,
        )
        obj_dict = make_obj_dict(objects)
        restore_line_type(obj_dict, line_map)
    finally:
        _pf_bulk_mode_end(app)

    # ---------------------------------------------------------
    # 2) Restore loc_name, attributes, desc, cimRdfId
    # ---------------------------------------------------------
    fields = [
        "sernum",
        "constr",
        "chr_name",
        "dar_src",
        "manuf",
        "for_name",
        "foreignKey",
    ]
    objects = collect_unique_objects_for_anonymization(app)

    _pf_bulk_mode_begin(app)
    try:
        for obj in objects:
            # restore loc_name by checking for prefix
            cur_name = _get_loc_name(obj)
            if isinstance(cur_name, str) and cur_name.startswith(prefix):
                orig = anon_rev.get(cur_name)
                if orig:
                    try:
                        obj.SetAttribute("loc_name", orig)
                    except AttributeError:
                        pass

            # restore generic string attributes by checking for prefix
            for attr in fields:
                cur_val = _get_str_attr(obj, attr)
                if cur_val is None:
                    continue
                cur_s = str(cur_val).strip()
                if cur_s.startswith(prefix):
                    orig = anon_rev.get(cur_s)
                    if orig is not None:
                        _set_str_attr(obj, attr, orig)

            # restore desc (only if it was anonymized; "Deleted" stays)
            cur_desc = _get_str_attr(obj, "desc")
            if cur_desc is not None:
                cur_desc_s = str(cur_desc)
                if cur_desc_s.strip() != "" and cur_desc_s != "Deleted":
                    restored = restore_anon_tokens_in_text(cur_desc_s, anon_rev)
                    restored = _desc_normalize(restored).strip()
                    if restored != cur_desc_s:
                        safe_set(obj, "desc", restored, verbose=False)

            # restore cimRdfId
            ids = _get_cim_rdf_id(obj)
            if ids:
                cur_id = ids[0]
                if cur_id in cim_rev:
                    _set_cim_rdf_id(obj, cim_rev[cur_id])
    finally:
        _pf_bulk_mode_end(app)

    # ---------------------------------------------------------
    # 3) Restore GPS for transformed cases AFTER cim restore
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # 4) Restore the time stamps for each case
    # ---------------------------------------------------------
    _pf_bulk_mode_begin(app)
    try:
        restore_times(objects, time_rev)
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
            logger.info("[INFO] Project name corrected to: %s", alt_name)
            return app.GetActiveProject()

    user = app.GetCurrentUser()
    prjs = _list_projects(user)

    for p in prjs:
        if getattr(p, "loc_name", "") in (
            project_name,
            project_name.replace("_anonym", ""),
        ):
            if hasattr(p, "Activate"):
                p.Activate()
                return app.GetActiveProject()

    logger.info("Available projects:")
    for p in prjs:
        try:
            logger.info(" - %s", p.loc_name)
        except AttributeError:
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
    """Terminate any running PowerFactory.exe process, if found."""
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            if proc.info.get("name") and "PowerFactory" in proc.info["name"]:
                proc.kill()
                logger.info("PowerFactory terminated.")
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
    if pf is None:
        logger.error("PowerFactory Python API not available. Cannot run.")
        raise RuntimeError("PowerFactory Python API not available.")

    in_path = Path(in_path)
    out_path = Path(out_path)
    mapping_out_path = Path(mapping_out_path)

    kill_powerfactory()

    app = pf.GetApplication()
    if not app:
        raise RuntimeError(
            "PowerFactory Application not available (pf.GetApplication() returned None)."
        )

    app.ClearOutputWindow()
    logger.info("=== anym_PF.py: Start Import/Anonymize/Export ===")

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
    logger.info("Objects to anonymize (unique): %d", len(objects))

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
    logger.info("Mapping saved: %s", mapping_out_path)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _export_project_to_pfd(app, out_path)
        logger.info("Export written: %s", out_path)
    except OSError as e:
        logger.warning("Export not executed: %s", e)
    except RuntimeError as e:
        logger.error("Export failed: %s", e)


def run_powerfactory_restore(
    in_path: Path,
    out_path: Path,
    mapping_path: Path,
):
    """
    Import PFD -> restore from JSON -> export PFD
    """
    if pf is None:
        logger.error(
            "PowerFactory Python API not available (pf is None). Cannot restore."
        )
        raise RuntimeError("PowerFactory Python API not available.")
    logger.info("=== anym_PF.py: Start Reverse ===")
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
    logger.info("=== anym_PF.py: End Reverse ===")
