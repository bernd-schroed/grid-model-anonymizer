"""
anym_cgmes.py  –  CGMES (XML/RDF) anonymizer
==============================================
Anonymizes a CGMES bundle (zip archive, folder, or single XML file) using the
same seed-based SHA-256 approach and mapping JSON as anym_PF / anym_csv.

Design rationale
----------------
- rdf:ID / rdf:about / rdf:resource are UUIDs in conformant CGMES files.
  They carry no human-readable information and are therefore left untouched
  by default.  Use --remap-ids (API flag remap_ids=True) only if the source
  file encodes readable names inside the IDs (e.g. hand-crafted test models
  with IDs like "_SubStation_HamburgNord").

- IdentifiedObject.name / description / shortName / aliasName are
  always anonymized when present (these carry the actual clear-text names).

- md:Model.description in the FullModel header is anonymized (it often
  contains TSO/DSO names or project identifiers).

- GPS coordinates (PositionPoint / CoordinatePair x/yPosition) in the GL
  profile are transformed or deleted.

- Only fields that actually exist in the file are touched.

Depends on: lxml, anym_PF (SeededNameAnonymizer etc.)
"""

# pylint: disable=c-extension-no-member
from __future__ import annotations

import logging
import math
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

from lxml import etree

from utils import (
    SeededNameAnonymizer,
    _build_geo_transform,
    _generate_seeded_uuid,
    _meters_to_deg_lat,
    _meters_to_deg_lon,
    _obj_unit_from_name,
    _scale_back_to_valid_geo,
    get_mappings,
    save_mapping_json,
)

logger = logging.getLogger("anym_cgmes.py")

# ---------------------------------------------------------------------------
# RDF namespace
# ---------------------------------------------------------------------------
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDF_ID = f"{{{RDF_NS}}}ID"
RDF_ABOUT = f"{{{RDF_NS}}}about"
RDF_RESOURCE = f"{{{RDF_NS}}}resource"

# ---------------------------------------------------------------------------
# Local-name suffixes of text elements whose content gets anonymized.
# Matching is done on the local part after the last '}' in the Clark tag.
# ---------------------------------------------------------------------------
ANON_TEXT_LOCALS: Set[str] = {
    "IdentifiedObject.name",
    "IdentifiedObject.description",
    "IdentifiedObject.shortName",
    "IdentifiedObject.aliasName",
    "Model.description",  # md:FullModel header – often contains TSO/DSO name
}

# GPS coordinate element local names (GL profile, CGMES 2.4 and 3.0)
GPS_X_LOCALS: Set[str] = {
    "PositionPoint.xPosition",  # longitude in CGMES GL
    "CoordinatePair.xPosition",  # older profile variant
}
GPS_Y_LOCALS: Set[str] = {
    "PositionPoint.yPosition",  # latitude in CGMES GL
    "CoordinatePair.yPosition",
}

TIME_STAMP_LOCALS: Set[str] = {"Model.scenarioTime"}

LINE_SPECS_LOCALS: Set[str] = {
    "Conductor.length",
    "ACLineSegment.b0ch",
    "ACLineSegment.bch",
    "ACLineSegment.g0ch",
    "ACLineSegment.gch",
    "ACLineSegment.r",
    "ACLineSegment.r0",
    "ACLineSegment.x",
    "ACLineSegment.x0",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Clark notation {ns}localname -> localname."""
    return tag.split("}")[-1] if "}" in tag else tag


def _remap_id(old_id: str, seed: str, cim_forward: Dict[str, str]) -> str:
    """Deterministically remap a single rdf:ID string (for --remap-ids mode)."""
    if old_id in cim_forward:
        return cim_forward[old_id]
    new_id = _generate_seeded_uuid(old_id, seed)
    cim_forward[old_id] = new_id
    return new_id


def _strip_hash(ref: str) -> str:
    """Remove leading '#' from an rdf:resource / rdf:about value."""
    return ref[1:] if ref.startswith("#") else ref


def _get_parent_rdfinfo(element, rdf_tag):
    parent = element.getparent()
    return parent.get(rdf_tag)


# ---------- Courtesy of Claude ------------
def _cgmes_time_to_epoch(timestr: str) -> int:
    """z.B. '1977-01-01T09:00:00Z' -> Sekunden seit 1970-01-01"""
    dt = datetime.strptime(timestr, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _epoch_to_cgmes_time(epoch: int) -> str:
    """Sekunden seit 1970-01-01 -> '1977-01-01T09:00:00Z'"""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------

# ---------------------------------------------------------------------------
# XML I/O
# ---------------------------------------------------------------------------


def _parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_comments=False, remove_blank_text=False)
    return etree.parse(str(path), parser)


def _serialise_xml(tree: etree._ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(
        str(path),
        xml_declaration=True,
        encoding="utf-8",
        pretty_print=True,
    )


# ---------------------------------------------------------------------------
# GPS transform
# ---------------------------------------------------------------------------


def _apply_gps_pair(
    x_el: etree._Element,
    y_el: etree._Element,
    *,
    seed: str,
    gps_delete: bool,
    gps_transform,
    anonymizer: SeededNameAnonymizer,
    parent_id: str,
) -> None:
    """
    Transform or delete a GPS coordinate pair.
    x = longitude, y = latitude (CGMES GL convention).
    Records the change in anonymizer.gps_mapping keyed by parent_id.

    Note: gps_transform is expected to be a pure translation (see
    _build_geo_transform in anym_PF.py).  The _scale_back_to_valid_geo
    call below acts as a safety net for points very close to the poles.
    """
    try:
        old_lon = float((x_el.text or "").strip())
        old_lat = float((y_el.text or "").strip())
    except ValueError:
        return

    # Skip null islands (already zeroed)
    if abs(old_lat) < 1e-12 and abs(old_lon) < 1e-12:
        return

    if gps_delete:
        anonymizer.gps_mapping[parent_id] = {
            "old": [old_lat, old_lon],
            "deleted": True,
        }
        x_el.text = "0.0"
        y_el.text = "0.0"
        return

    # Global rotation + per-object jitter (up to 100 m)
    new_lat, new_lon = gps_transform(old_lat, old_lon)
    r_m = _obj_unit_from_name(seed, "cgmes_gps_r", parent_id) * 100.0
    theta = 2.0 * math.pi * _obj_unit_from_name(seed, "cgmes_gps_theta", parent_id)
    new_lat += _meters_to_deg_lat(r_m * math.sin(theta))
    new_lon += _meters_to_deg_lon(r_m * math.cos(theta), new_lat)

    # Scale back the shift vector if the result falls outside the valid
    # geographic range, preserving the shift direction.
    new_lat, new_lon = _scale_back_to_valid_geo(old_lat, old_lon, new_lat, new_lon)

    anonymizer.gps_mapping[parent_id] = {
        "old": [old_lat, old_lon],
        "new": [new_lat, new_lon],
    }
    x_el.text = f"{new_lon:.6f}"
    y_el.text = f"{new_lat:.6f}"


# ---------------------------------------------------------------------------
# Per-tree anonymization pass
# ---------------------------------------------------------------------------


def _anonymize_tree(
    tree: etree._ElementTree,
    *,
    seed: str,
    gps_delete: bool,
    gps_transform,
    anonymizer: SeededNameAnonymizer,
    desc_delete: bool,
    remap_ids: bool,
) -> None:
    """
    Modifies the parsed XML tree in-place.

    Steps:
    1. GPS: collect x/y element pairs per parent, then transform/delete.
    2. Text fields: anonymize IdentifiedObject.name / description / etc.
    3. rdf:ID remapping (only if remap_ids=True).
    """

    # ------------------------------------------------------------------
    # Step 1: time_stamps
    # ------------------------------------------------------------------
    _anonymize_time(tree, anonymizer=anonymizer)

    # ------------------------------------------------------------------
    # Step 2: GPS – group x/y children by their parent element
    # ------------------------------------------------------------------
    # IMPORTANT: lxml creates a new Python proxy object on every call to
    # el.getparent(), so id(parent) is NOT stable across two calls for the
    # same underlying C node.  Two xPosition/yPosition siblings therefore
    # yield different id() values for the same XML parent → end up in
    # separate buckets → both incomplete → both skipped.
    #
    # Fix: use a deterministic string key derived from the parent element:
    #   1. rdf:ID attribute value  (present in most CGMES elements)
    #   2. stripped rdf:about value
    #   3. tree.getpath(parent)  – lxml XPath, unique & stable (e.g.
    #      '/rdf:RDF/cim:PositionPoint[3]')
    #
    #   gps_buckets : str_key -> {"x_el": element, "y_el": element}

    _anonymize_gps(
        tree=tree,
        seed=seed,
        gps_delete=gps_delete,
        gps_transform=gps_transform,
        anonymizer=anonymizer,
    )

    # ------------------------------------------------------------------
    # Step 3: line_length
    # ------------------------------------------------------------------
    _anonymize_line_length(tree=tree, anonymizer=anonymizer)
    # anonymizer=anonymizer, desc_delete=desc_delete)

    # ------------------------------------------------------------------
    # Step 4: text fields
    # ------------------------------------------------------------------
    _anonymize_text_fields(tree=tree, anonymizer=anonymizer, desc_delete=desc_delete)
    # ------------------------------------------------------------------
    # Step 5: rdf:ID remapping (optional, off by default)
    # ------------------------------------------------------------------
    _anonymize_rdf(
        tree=tree,
        seed=seed,
        anonymizer=anonymizer,
        remap_ids=remap_ids,
    )


def _anonymize_gps(
    tree: etree._ElementTree,
    *,
    seed: str,
    gps_delete: bool,
    gps_transform,
    anonymizer: SeededNameAnonymizer,
):
    def _parent_key(p: etree._Element) -> str:
        v = p.get(RDF_ID) or ""
        if v:
            return v
        v = _strip_hash(p.get(RDF_ABOUT) or "")
        if v:
            return v
        return tree.getpath(p)  # unique XPath fallback

    gps_buckets: Dict[str, Dict] = {}

    for el in tree.iter():
        loc = _local(el.tag)
        if loc not in GPS_X_LOCALS and loc not in GPS_Y_LOCALS:
            continue

        parent = el.getparent()
        if parent is None:
            continue

        key = _parent_key(parent)
        bucket = gps_buckets.setdefault(key, {})
        # A valid PositionPoint has exactly one xPosition and one yPosition,
        # so we see each slot exactly once per bucket.
        if loc in GPS_X_LOCALS:
            bucket["x_el"] = el
        else:
            bucket["y_el"] = el

    skipped = 0
    for key, bucket in gps_buckets.items():
        x_el = bucket.get("x_el")
        y_el = bucket.get("y_el")
        if x_el is None or y_el is None:
            skipped += 1
            continue
        _apply_gps_pair(
            x_el,
            y_el,
            seed=seed,
            gps_delete=gps_delete,
            gps_transform=gps_transform,
            anonymizer=anonymizer,
            parent_id=key,
        )
    if skipped:
        logger.warning(
            "   %d GPS bucket(s) incomplete (x or y missing) – skipped", skipped
        )


def _anonymize_line_length(
    tree: etree._ElementTree,
    *,
    anonymizer: SeededNameAnonymizer,
):
    for el in tree.iter():
        loc = _local(el.tag)
        if loc not in LINE_SPECS_LOCALS:
            continue
        # getting the length of the element
        line_length = float(el.text)
        el.text = str(1)
        rdf_id = _get_parent_rdfinfo(el, RDF_ID)
        anonymizer.line_mapping[rdf_id] = str(line_length)
        print(line_length)


def _anonymize_text_fields(
    tree: etree._ElementTree,
    *,
    anonymizer: SeededNameAnonymizer,
    desc_delete: bool,
):
    for el in tree.iter():
        loc = _local(el.tag)

        if loc not in ANON_TEXT_LOCALS:
            continue
        if not el.text or not el.text.strip():
            continue

        raw = el.text.strip()

        # Description / Model.description: optionally delete
        if loc in ("IdentifiedObject.description", "Model.description") and desc_delete:
            el.text = "Deleted"
            continue

        el.text = anonymizer.translate(raw)


def _anonymize_rdf(
    tree: etree._ElementTree,
    *,
    seed: str,
    anonymizer: SeededNameAnonymizer,
    remap_ids: bool,
):
    if not remap_ids:
        return

    # Collect all current rdf:IDs in this tree first (for resource fixup)
    all_ids_before: Set[str] = set()
    for el in tree.iter():
        raw = el.get(RDF_ID)
        if raw:
            all_ids_before.add(raw)
        raw = el.get(RDF_ABOUT)
        if raw:
            all_ids_before.add(_strip_hash(raw))

    # Build remap table for IDs found in this tree
    for old_id in all_ids_before:
        if old_id not in anonymizer.cim_forward:
            _remap_id(old_id, seed, anonymizer.cim_forward)

    # Apply remaps
    for el in tree.iter():
        raw_id = el.get(RDF_ID)
        if raw_id and raw_id in anonymizer.cim_forward:
            el.set(RDF_ID, anonymizer.cim_forward[raw_id])

        raw_about = el.get(RDF_ABOUT)
        if raw_about:
            bare = _strip_hash(raw_about)
            if bare in anonymizer.cim_forward:
                new_bare = anonymizer.cim_forward[bare]
                el.set(
                    RDF_ABOUT, "#" + new_bare if raw_about.startswith("#") else new_bare
                )

        raw_res = el.get(RDF_RESOURCE)
        if raw_res:
            bare = _strip_hash(raw_res)
            if bare in anonymizer.cim_forward:
                new_bare = anonymizer.cim_forward[bare]
                el.set(
                    RDF_RESOURCE,
                    "#" + new_bare if raw_res.startswith("#") else new_bare,
                )


def _anonymize_time(
    tree: etree._ElementTree,
    *,
    anonymizer: SeededNameAnonymizer,
):
    for el in tree.iter():
        loc = _local(el.tag)
        if loc not in TIME_STAMP_LOCALS:
            continue
        cgmes_timestamp = _cgmes_time_to_epoch(el.text)
        new_epoch_timestamp = anonymizer.add_time(cgmes_timestamp)
        el.text = _epoch_to_cgmes_time(new_epoch_timestamp)


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
        loc = _local(el.tag)
        if loc in ANON_TEXT_LOCALS and el.text:
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
            raw_id = el.get(RDF_ID)
            if raw_id and raw_id in cim_rev:
                el.set(RDF_ID, cim_rev[raw_id])

            raw_about = el.get(RDF_ABOUT)
            if raw_about:
                bare = _strip_hash(raw_about)
                if bare in cim_rev:
                    orig = cim_rev[bare]
                    el.set(RDF_ABOUT, "#" + orig if raw_about.startswith("#") else orig)

            raw_res = el.get(RDF_RESOURCE)
            if raw_res:
                bare = _strip_hash(raw_res)
                if bare in cim_rev:
                    orig = cim_rev[bare]
                    el.set(
                        RDF_RESOURCE, "#" + orig if raw_res.startswith("#") else orig
                    )


def _restore_gps(
    tree: etree._ElementTree,
    *,
    gps_map: Dict[str, dict],
):
    gps_buckets: Dict[str, Dict[str, etree._Element]] = {}
    gps_key_by_elem_id: Dict[int, str] = {}

    for el in tree.iter():
        loc = _local(el.tag)
        if loc not in GPS_X_LOCALS and loc not in GPS_Y_LOCALS:
            continue
        parent = el.getparent()
        if parent is None:
            continue

        pid = id(parent)
        if pid not in gps_key_by_elem_id:
            raw_id = parent.get(RDF_ID) or _strip_hash(parent.get(RDF_ABOUT) or "")
            gps_key_by_elem_id[pid] = raw_id if raw_id else str(pid)
        key = gps_key_by_elem_id[pid]

        bucket = gps_buckets.setdefault(key, {})
        if loc in GPS_X_LOCALS:
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
        loc = _local(el.tag)
        if loc not in LINE_SPECS_LOCALS:
            continue
        # getting the length of the element
        rdf_id = _get_parent_rdfinfo(el, RDF_ID)
        line_length = float(line_map[rdf_id])
        el.text = str(line_length)


def _restore_time(
    tree: etree._ElementTree,
    *,
    time_rev: Dict[str, str],
):
    for el in tree.iter():
        loc = _local(el.tag)
        if loc in TIME_STAMP_LOCALS:
            anon_time_cgmes = el.text.strip()
            anon_time_epoch = _cgmes_time_to_epoch(anon_time_cgmes)
            orig_time_epoch = int(time_rev[str(anon_time_epoch)])
            el.text = _epoch_to_cgmes_time(orig_time_epoch)


# ---------------------------------------------------------------------------
# Bundle I/O helpers
# ---------------------------------------------------------------------------


def _extract_bundle(src: Path, tmp_dir: Path) -> List[Tuple[str, Path]]:
    """
    Extract CGMES bundle (zip, directory, single XML) into tmp_dir.
    Returns list of (relative_name, absolute_path) for all .xml files.
    """
    xml_files: List[Tuple[str, Path]] = []

    if src.is_dir():
        for f in sorted(src.rglob("*.xml")):
            rel = str(f.relative_to(src))
            dest = tmp_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            xml_files.append((rel, dest))

    elif zipfile.is_zipfile(src):
        with zipfile.ZipFile(src, "r") as zf:
            zf.extractall(tmp_dir)
        for f in sorted(tmp_dir.rglob("*.xml")):
            rel = str(f.relative_to(tmp_dir))
            xml_files.append((rel, f))

    else:
        # Treat as a single XML file
        dest = tmp_dir / src.name
        shutil.copy2(src, dest)
        xml_files.append((src.name, dest))

    return xml_files


def _pack_bundle(
    xml_files: List[Tuple[str, Path]],
    out: Path,
) -> None:
    """Pack processed XML files back into a zip or copy to an output directory."""
    if out.suffix.lower() == ".zip":
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel, path in xml_files:
                zf.write(path, rel)
    else:
        out.mkdir(parents=True, exist_ok=True)
        for rel, path in xml_files:
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


# ---------------------------------------------------------------------------
# Public entrypoint: anonymize
# ---------------------------------------------------------------------------


def anonymize_cgmes(
    in_path: Path,
    out_path: Path,
    seed: str,
    mapping_out_path: Path,
    *,
    desc: bool = False,
    gps: bool = False,
    remap_ids: bool = False,
    prefix: str = "ANON_",
    hash_length: int = 10,
) -> None:
    """
    Anonymize a CGMES bundle.

    Parameters
    ----------
    in_path          : zip file, folder, or single XML file
    out_path         : output zip / folder / XML
    seed             : deterministic seed string
    mapping_out_path : path for the lookup-table JSON
    desc             : True  -> replace description text with 'Deleted'
                       False -> anonymize via token mapping
    gps              : True  -> set all GPS coords to 0.0
                       False -> apply rotation + per-object jitter
    remap_ids        : False (default) -> rdf:ID / rdf:about / rdf:resource
                                          are left untouched (they are UUIDs)
                       True            -> remap them as well (use when IDs
                                          encode readable names)
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    mapping_out_path = Path(mapping_out_path)

    logger.info("=== anym_cgmes.py: Start Anonymize ===")

    anonymizer = SeededNameAnonymizer(seed=seed, prefix=prefix, length=hash_length)
    gps_transform = _build_geo_transform(seed)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        xml_files = _extract_bundle(in_path, tmp_dir)
        logger.info(
            "  Found %d XML file(s): %s", len(xml_files), [r for r, _ in xml_files]
        )

        trees: List[Tuple[str, Path, etree._ElementTree]] = []
        for rel, path in xml_files:
            try:
                trees.append((rel, path, _parse_xml(path)))
            except etree.XMLSyntaxError as exc:
                logger.warning("  Skipping %s: %s", rel, exc)

        for rel, path, tree in trees:
            logger.debug("  Processing %s ...", rel)
            _anonymize_tree(
                tree,
                seed=seed,
                gps_delete=gps,
                gps_transform=gps_transform,
                anonymizer=anonymizer,
                desc_delete=desc,
                remap_ids=remap_ids,
            )
            _serialise_xml(tree, path)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        _pack_bundle([(rel, path) for rel, path, _ in trees], out_path)

    save_mapping_json(mapping_out_path, anonymizer)
    logger.info("  Mapping saved   : %s", mapping_out_path)
    logger.info("  Output          : %s", out_path)
    logger.info("  Names anonymized: %d", len(anonymizer.forward))
    logger.info("  rdf:IDs remapped: %d", len(anonymizer.cim_forward))
    logger.info("  GPS entries     : %d", len(anonymizer.gps_mapping))
    logger.info("=== anym_cgmes.py: Done ===")


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

    line_map, anon_rev, time_rev, cim_rev, __, gps_map, prefix = get_mappings(
        mapping_path
    )

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        xml_files = _extract_bundle(in_path, tmp_dir)
        logger.info(
            "  Found %d XML file(s): %s", len(xml_files), [r for r, _ in xml_files]
        )

        trees: List[Tuple[str, Path, etree._ElementTree]] = []
        for rel, path in xml_files:
            try:
                trees.append((rel, path, _parse_xml(path)))
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
            _serialise_xml(tree, path)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        _pack_bundle([(rel, path) for rel, path, _ in trees], out_path)

    logger.info("  Output: %s", out_path)
    logger.info("=== anym_cgmes.py: Restore Done ===")
