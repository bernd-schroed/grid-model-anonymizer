import logging
import re
from pathlib import Path
from typing import Dict, List

from utils import pf_utils, utils

pf = pf_utils.import_powerfactory_module()
logger = logging.getLogger("restore_pf.py")


# ----------------------------
# Restore procedure (UNIFIED)
# ----------------------------


_ANON_RE = re.compile(r"\bANON_[0-9A-F]{6,}\b")  # 6+ damit auch längere Hashes gehen


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
        obj_name = pf_utils.get_loc_name(obj)
        obj_class = obj.GetClassName()
        obj_key = obj_name + "." + obj_class
        objects_dict[obj_key] = obj
    return objects_dict


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
                target = pf_utils.search_by_full_name_after(app, fn)

        if target is None:
            logger.warning("Deleted-GPS target not found (orig_cim=%s)", orig_cim)
            continue

        pf_utils.safe_set(target, "GPSlat", old_lat, verbose=False)
        pf_utils.safe_set(target, "GPSlon", old_lon, verbose=False)


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
        full = pf_utils.get_full_name(obj)
        if full.endswith("IntCase"):
            restore_timestamp(obj, time_rev)


def restore_timestamp(obj, time_rev):
    """
    restore the original timestamp of a study case object

    Parameters
    ----------
    obj: object
        The study case object
    time_rev: Dict
        The reverse mapping of the timestamps with the anonymized timestamp as key and the original
    """
    anym_time = int(pf_utils.get_float_attr(obj, "iStudyTime"))
    orig_time = int(time_rev[str(anym_time)])
    pf_utils.safe_set(obj, "iStudyTime", orig_time, verbose=False)


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
            pf_utils.safe_set(line_obj, "dline", float(orig_length), verbose=False)
            pf_utils.safe_set(line_obj, "typ_id", orig_type_obj, verbose=False)

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
    ) = utils.get_mappings(mapping_path)

    objects = pf_utils.collect_unique_objects_for_anonymization(app)
    # ---------------------------------------------------------
    # 1) Restore GPS for deleted=true BEFORE renaming anything and reset lines
    # ---------------------------------------------------------
    cim_index_current = pf_utils.build_cim_index(objects)

    pf_utils.pf_bulk_mode_begin(app)
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
        pf_utils.pf_bulk_mode_end(app)

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
    objects = pf_utils.collect_unique_objects_for_anonymization(app)

    pf_utils.pf_bulk_mode_begin(app)
    try:
        for obj in objects:
            # restore loc_name by checking for prefix
            cur_name = pf_utils.get_loc_name(obj)
            if isinstance(cur_name, str) and cur_name.startswith(prefix):
                orig = anon_rev.get(cur_name)
                if orig:
                    try:
                        obj.SetAttribute("loc_name", orig)
                    except AttributeError:
                        pass

            # restore generic string attributes by checking for prefix
            for attr in fields:
                cur_val = pf_utils.get_str_attr(obj, attr)
                if cur_val is None:
                    continue
                cur_s = str(cur_val).strip()
                if cur_s.startswith(prefix):
                    orig = anon_rev.get(cur_s)
                    if orig is not None:
                        pf_utils.set_str_attr(obj, attr, orig)

            # restore desc (only if it was anonymized; "Deleted" stays)
            cur_desc = pf_utils.get_str_attr(obj, "desc")
            if cur_desc is not None:
                cur_desc_s = str(cur_desc)
                if cur_desc_s.strip() != "" and cur_desc_s != "Deleted":
                    restored = restore_anon_tokens_in_text(cur_desc_s, anon_rev)
                    restored = pf_utils.desc_normalize(restored).strip()
                    if restored != cur_desc_s:
                        pf_utils.safe_set(obj, "desc", restored, verbose=False)

            # restore cimRdfId
            ids = pf_utils.get_cim_rdf_id(obj)
            if ids:
                cur_id = ids[0]
                if cur_id in cim_rev:
                    pf_utils.set_cim_rdf_id(obj, cim_rev[cur_id])
    finally:
        pf_utils.pf_bulk_mode_end(app)

    # ---------------------------------------------------------
    # 3) Restore GPS for transformed cases AFTER cim restore
    # ---------------------------------------------------------
    objects = pf_utils.collect_unique_objects_for_anonymization(app)
    cim_index_orig = pf_utils.build_cim_index(objects)

    pf_utils.pf_bulk_mode_begin(app)
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
                    target = pf_utils.search_by_full_name_after(app, fn)

            if target is None:
                continue

            pf_utils.safe_set(target, "GPSlat", old_lat, verbose=False)
            pf_utils.safe_set(target, "GPSlon", old_lon, verbose=False)
    finally:
        pf_utils.pf_bulk_mode_end(app)

    # ---------------------------------------------------------
    # 4) Restore the time stamps for each case
    # ---------------------------------------------------------
    pf_utils.pf_bulk_mode_begin(app)
    try:
        restore_times(objects, time_rev)
    finally:
        pf_utils.pf_bulk_mode_end(app)


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

    pf_utils.kill_powerfactory()

    app = pf.GetApplication()
    if not app:
        raise RuntimeError("PowerFactory Application not available.")

    if not in_path.exists():
        raise FileNotFoundError(in_path)
    if not mapping_path.exists():
        raise FileNotFoundError(mapping_path)

    project_name = in_path.stem

    pf_utils.delete_project_if_exists(app, project_name)
    pf_utils.import_pfd_into_current_user(app, in_path)
    pf_utils.activate_project(app, project_name)

    restore_from_mapping(app, mapping_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pf_utils.export_project_to_pfd(app, out_path)
    logger.info("=== restore_PF.py: End Reverse ===")
