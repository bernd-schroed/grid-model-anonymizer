import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psutil

import utils

logger = logging.getLogger("pf_utils.py")


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


def import_powerfactory_module():
    pf_path = get_pf_version()
    if pf_path is False:
        pf_module = None  # pylint:disable=invalid-name
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

        import powerfactory as pf_module  # type: ignore # pylint: disable=import-error,wrong-import-position,wrong-import-order

    return pf_module


pf = import_powerfactory_module()


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


def pf_bulk_mode_begin(app):
    _call_pf_or_app(app, "SetProgressBarUpdatesEnabled", 1)
    _call_pf_or_app(app, "SetGuiUpdateEnabled", 1)
    _call_pf_or_app(app, "SetUserBreakEnabled", 1)
    _call_pf_or_app(app, "SetWriteCacheEnabled", 1)


def pf_bulk_mode_end(app):
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


# ----------------------------
# Safe attribute helpers
# ----------------------------
def get_float_attr(obj, attr: str) -> Optional[float]:
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


def get_loc_name(obj) -> str:
    try:
        return obj.GetAttribute("loc_name")
    except AttributeError:
        return getattr(obj, "loc_name", "")


def _set_loc_name_only(obj, new_name: str):
    try:
        return obj.SetAttribute("loc_name", new_name)
    except AttributeError:
        return setattr(obj, "loc_name", new_name)


def get_str_attr(obj, attr: str) -> Optional[str]:
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


def set_str_attr(obj, attr: str, value: str) -> bool:
    return safe_set(obj, attr, str(value), verbose=False)


def get_cim_rdf_id(obj) -> List[str]:
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


def set_cim_rdf_id(obj, new_id: str) -> bool:
    return safe_set(obj, "cimRdfId", [new_id], verbose=False)


def get_full_name(obj) -> str:
    try:
        return obj.GetFullName()
    except AttributeError:
        return f"{obj.GetClassName()}::{get_loc_name(obj)}"


def _to_project_relative(full_name: str) -> str:
    marker = r"\Network Model.IntPrjfolder"
    i = full_name.find(marker)
    if i < 0:
        return full_name
    return full_name[i:]


def search_by_full_name_after(app, full_name_after: str):
    project = app.GetActiveProject()
    if not project:
        return None

    rel = _to_project_relative(full_name_after)
    try:
        return project.SearchObject(rel)
    except AttributeError:
        return None


def make_unique_if_needed(
    obj, desired: str, anonymizer: utils.SeededNameAnonymizer
) -> str:
    old = get_loc_name(obj)
    full = get_full_name(obj)
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
        if get_loc_name(obj) == desired:
            return desired
        raise RuntimeError("PF did not apply loc_name")
    except AttributeError:
        try:
            base = get_full_name(obj)
        except AttributeError:
            base = f"{obj.GetClassName()}::{old}"
        suffix = anonymizer.get_hash(base, 6)
        candidate = f"{desired}_{suffix}"
        _set_loc_name_only(obj, candidate)
        if get_loc_name(obj) != candidate:
            logger.warning(
                "Rename failed: %s -> %s (candidate %s not applied)",
                old,
                desired,
                candidate,
            )
        return candidate


def build_cim_index(objs: List) -> Dict[str, object]:
    idx: Dict[str, object] = {}
    for o in objs:
        ids = get_cim_rdf_id(o)
        if ids:
            idx[ids[0]] = o
    return idx


def _collapse_semicolons(s: str) -> str:
    s = re.sub(r";{2,}", ";", s)  # ;; oder mehr -> ;
    s = s.strip(";")
    return s


# ----------------------------
# DESC handling
# ----------------------------
def sanitize_desc(obj, desc: bool, anonymizer: utils.SeededNameAnonymizer):
    """
    desc=True  -> delete description (write 'Deleted')
    desc=False -> anonymize description (token-based, reversible via anon_mapping)
    """
    if desc:
        safe_set(obj, "desc", "Deleted", verbose=False)
        return

    old = get_str_attr(obj, "desc")
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


def _desc_anonymize(desc_value: str, anonymizer: utils.SeededNameAnonymizer) -> str:
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
# Import / Activate / Export
# ----------------------------
def _list_projects(user):
    return user.GetContents("*.IntPrj") or []


def delete_project_if_exists(app, project_name: str):
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


def import_pfd_into_current_user(app, in_path: Path):
    user = app.GetCurrentUser()

    import_obj = user.CreateObject("CompfdImport", "Import")
    import_obj.SetAttribute("e:g_file", utils.get_p(in_path))
    import_obj.g_target = user

    rc = import_obj.Execute()
    import_obj.Delete()

    app.ClearRecycleBin()
    _call_pf_or_app(app, "WriteChangesToDb")

    if rc != 0:
        raise RuntimeError(f"PFD import failed. Return code: {rc}")


def activate_project(app, project_name: str):
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


def export_project_to_pfd(app, out_path: Path):
    g_object = app.GetActiveProject()
    if g_object:
        g_object.Deactivate()

    pfd_export_obj = app.GetFromStudyCase("ComPfdexport")
    if not pfd_export_obj:
        raise RuntimeError("ComPfdexport not found (StudyCase).")

    pfd_export_obj.g_objects = [g_object]
    pfd_export_obj.g_file = utils.get_p(out_path)

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
