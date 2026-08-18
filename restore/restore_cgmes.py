import logging
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from lxml import etree

from utils import cgmes_utils, utils

logger = logging.getLogger("restore_cgmes.py")

# ---------------------------------------------------------------------------
# Per-tree restore pass
# ---------------------------------------------------------------------------


def _restore_tree(
    tree: etree._ElementTree,
    *,
    anon_rev: Dict[str, str],
    cim_rev: Dict[str, str],
    time_rev: Dict[str, str],
    gps_map: Dict[str, dict],
    prefix: str,
    line_map: Dict[str, str],
) -> None:
    """Reverse anonymization in-place."""

    # Restore text fields
    _restore_textfields(tree, anon_rev=anon_rev, prefix=prefix)
    # Restore rdf:IDs (only relevant when remap_ids was used)
    _restore_rdfids(tree, cim_rev=cim_rev)
    # Restore GPS
    _restore_gps(tree, gps_map=gps_map)
    # Restore Line Lengths
    _restore_line_length(tree, line_map=line_map)
    # Restore Time
    _restore_time(tree, time_rev=time_rev)


def _restore_textfields(
    tree: etree._ElementTree,
    *,
    anon_rev: Dict[str, str],
    prefix: str,
):
    for el in tree.iter():
        loc = cgmes_utils.local(el.tag)
        if loc in cgmes_utils.ANON_TEXT_LOCALS and el.text:
            cur = el.text.strip()
            if cur.startswith(prefix) and cur in anon_rev:
                el.text = anon_rev[cur]


def _restore_rdfids(
    tree: etree._ElementTree,
    *,
    cim_rev: Dict[str, str],
):
    if cim_rev:
        for el in tree.iter():
            raw_id = el.get(cgmes_utils.RDF_ID)
            if raw_id and raw_id in cim_rev:
                el.set(cgmes_utils.RDF_ID, cim_rev[raw_id])

            raw_about = el.get(cgmes_utils.RDF_ABOUT)
            if raw_about:
                bare = cgmes_utils.strip_hash(raw_about)
                if bare in cim_rev:
                    orig = cim_rev[bare]
                    el.set(
                        cgmes_utils.RDF_ABOUT,
                        "#" + orig if raw_about.startswith("#") else orig,
                    )

            raw_res = el.get(cgmes_utils.RDF_RESOURCE)
            if raw_res:
                bare = cgmes_utils.strip_hash(raw_res)
                if bare in cim_rev:
                    orig = cim_rev[bare]
                    el.set(
                        cgmes_utils.RDF_RESOURCE,
                        "#" + orig if raw_res.startswith("#") else orig,
                    )


def _restore_gps(
    tree: etree._ElementTree,
    *,
    gps_map: Dict[str, dict],
):
    gps_buckets: Dict[str, Dict[str, etree._Element]] = {}
    gps_key_by_elem_id: Dict[int, str] = {}

    for el in tree.iter():
        loc = cgmes_utils.local(el.tag)
        if loc not in cgmes_utils.GPS_X_LOCALS and loc not in cgmes_utils.GPS_Y_LOCALS:
            continue
        parent = el.getparent()
        if parent is None:
            continue

        pid = id(parent)
        if pid not in gps_key_by_elem_id:
            raw_id = parent.get(cgmes_utils.RDF_ID) or cgmes_utils.strip_hash(
                parent.get(cgmes_utils.RDF_ABOUT) or ""
            )
            gps_key_by_elem_id[pid] = raw_id if raw_id else str(pid)
        key = gps_key_by_elem_id[pid]

        bucket = gps_buckets.setdefault(key, {})
        if loc in cgmes_utils.GPS_X_LOCALS:
            bucket["x_el"] = el
        else:
            bucket["y_el"] = el

    for parent_id, bucket in gps_buckets.items():
        rec = gps_map.get(parent_id)
        if rec is None:
            continue
        old = rec.get("old")
        if not (isinstance(old, list) and len(old) == 2):
            continue
        old_lat, old_lon = float(old[0]), float(old[1])
        x_el = bucket.get("x_el")
        y_el = bucket.get("y_el")
        if x_el is not None:
            x_el.text = f"{old_lon:.6f}"
        if y_el is not None:
            y_el.text = f"{old_lat:.6f}"


def _restore_line_length(
    tree: etree._ElementTree,
    *,
    line_map: Dict[str, str],
):
    for el in tree.iter():
        loc = cgmes_utils.local(el.tag)
        if loc not in cgmes_utils.LINE_SPECS_LOCALS:
            continue
        # getting the length of the element
        rdf_id = cgmes_utils.get_parent_rdfinfo(el, cgmes_utils.RDF_ID)
        orig_value = float(line_map[rdf_id][loc])
        el.text = str(orig_value)


def _restore_time(
    tree: etree._ElementTree,
    *,
    time_rev: Dict[str, str],
):
    for el in tree.iter():
        loc = cgmes_utils.local(el.tag)
        if loc in cgmes_utils.TIME_STAMP_LOCALS:
            anon_time_cgmes = el.text.strip()
            anon_time_epoch = cgmes_utils.cgmes_time_to_epoch(anon_time_cgmes)
            orig_time_epoch = int(time_rev[str(anon_time_epoch)])
            el.text = cgmes_utils.epoch_to_cgmes_time(orig_time_epoch)


# ---------------------------------------------------------------------------
# Public entrypoint: restore
# ---------------------------------------------------------------------------


def restore_cgmes(
    in_path: Path,
    out_path: Path,
    mapping_path: Path,
) -> None:
    """
    Reverse a previously anonymized CGMES bundle using the mapping JSON.

    Parameters
    ----------
    in_path      : anonymized zip / folder / XML
    out_path     : restored output
    mapping_path : mapping JSON produced by anonymize_cgmes
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    mapping_path = Path(mapping_path)

    logger.info("=== anym_cgmes.py: Start Restore ===")

    line_map, anon_rev, time_rev, cim_rev, __, gps_map, prefix = utils.get_mappings(
        mapping_path
    )

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        xml_files = cgmes_utils.extract_bundle(in_path, tmp_dir)
        logger.info(
            "  Found %d XML file(s): %s", len(xml_files), [r for r, _ in xml_files]
        )

        trees: List[Tuple[str, Path, etree._ElementTree]] = []
        for rel, path in xml_files:
            try:
                trees.append((rel, path, cgmes_utils.parse_xml(path)))
            except etree.XMLSyntaxError as exc:
                logger.warning("  Skipping %s: %s", rel, exc)

        for rel, path, tree in trees:
            logger.info("  Restoring %s ...", rel)
            _restore_tree(
                tree,
                anon_rev=anon_rev,
                cim_rev=cim_rev,
                time_rev=time_rev,
                gps_map=gps_map,
                prefix=prefix,
                line_map=line_map,
            )
            cgmes_utils.serialise_xml(tree, path)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        cgmes_utils.pack_bundle([(rel, path) for rel, path, _ in trees], out_path)

    logger.info("  Output: %s", out_path)
    logger.info("=== anym_cgmes.py: Restore Done ===")
