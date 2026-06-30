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

import math
import shutil
import tempfile
import zipfile
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
    load_mapping_json,
    save_mapping_json,
)

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
    # Step 1: GPS – group x/y children by their parent element
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
        print(f"  [WARN] {skipped} GPS bucket(s) incomplete (x or y missing) – skipped")

    # ------------------------------------------------------------------
    # Step 2: text fields
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Step 3: rdf:ID remapping (optional, off by default)
    # ------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Per-tree restore pass
# ---------------------------------------------------------------------------


def _restore_tree(
    tree: etree._ElementTree,
    *,
    anon_rev: Dict[str, str],
    cim_rev: Dict[str, str],
    gps_map: Dict[str, dict],
    prefix: str,
) -> None:
    """Reverse anonymization in-place."""

    # Restore text fields
    for el in tree.iter():
        loc = _local(el.tag)
        if loc in ANON_TEXT_LOCALS and el.text:
            cur = el.text.strip()
            if cur.startswith(prefix) and cur in anon_rev:
                el.text = anon_rev[cur]

    # Restore rdf:IDs (only relevant when remap_ids was used)
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

    # Restore GPS
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

    print("=== anym_cgmes.py: Start Anonymize ===")

    anonymizer = SeededNameAnonymizer(seed=seed, prefix=prefix, length=hash_length)
    gps_transform = _build_geo_transform(seed)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        xml_files = _extract_bundle(in_path, tmp_dir)
        print(f"  Found {len(xml_files)} XML file(s): {[r for r, _ in xml_files]}")

        trees: List[Tuple[str, Path, etree._ElementTree]] = []
        for rel, path in xml_files:
            try:
                trees.append((rel, path, _parse_xml(path)))
            except etree.XMLSyntaxError as exc:
                print(f"  [WARN] Skipping {rel}: {exc}")

        for rel, path, tree in trees:
            print(f"  Processing {rel} ...")
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
    print(f"  Mapping saved   : {mapping_out_path}")
    print(f"  Output          : {out_path}")
    print(f"  Names anonymized: {len(anonymizer.forward)}")
    print(f"  rdf:IDs remapped: {len(anonymizer.cim_forward)}")
    print(f"  GPS entries     : {len(anonymizer.gps_mapping)}")
    print("=== anym_cgmes.py: Done ===")


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

    print("=== anym_cgmes.py: Start Restore ===")

    data = load_mapping_json(mapping_path)
    prefix: str = str(data.get("prefix", "ANON_") or "ANON_")
    anon_map: Dict[str, str] = data.get("anon_mapping", {}) or {}
    anon_rev: Dict[str, str] = {v: k for k, v in anon_map.items()}
    cim_map: Dict[str, str] = data.get("cimRdfId_mapping", {}) or {}
    cim_rev: Dict[str, str] = {v: k for k, v in cim_map.items()}
    gps_map: Dict[str, dict] = data.get("gps_mapping", {}) or {}

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        xml_files = _extract_bundle(in_path, tmp_dir)
        print(f"  Found {len(xml_files)} XML file(s)")

        trees: List[Tuple[str, Path, etree._ElementTree]] = []
        for rel, path in xml_files:
            try:
                trees.append((rel, path, _parse_xml(path)))
            except etree.XMLSyntaxError as exc:
                print(f"  [WARN] Skipping {rel}: {exc}")

        for rel, path, tree in trees:
            print(f"  Restoring {rel} ...")
            _restore_tree(
                tree,
                anon_rev=anon_rev,
                cim_rev=cim_rev,
                gps_map=gps_map,
                prefix=prefix,
            )
            _serialise_xml(tree, path)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        _pack_bundle([(rel, path) for rel, path, _ in trees], out_path)

    print(f"  Output: {out_path}")
    print("=== anym_cgmes.py: Restore Done ===")
