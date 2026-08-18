import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

from lxml import etree

import utils

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


def local(tag: str) -> str:
    """Clark notation {ns}localname -> localname."""
    return tag.split("}")[-1] if "}" in tag else tag


def remap_id(old_id: str, seed: str, cim_forward: Dict[str, str]) -> str:
    """Deterministically remap a single rdf:ID string (for --remap-ids mode)."""
    if old_id in cim_forward:
        return cim_forward[old_id]
    new_id = utils.generate_seeded_uuid(old_id, seed)
    cim_forward[old_id] = new_id
    return new_id


def strip_hash(ref: str) -> str:
    """Remove leading '#' from an rdf:resource / rdf:about value."""
    return ref[1:] if ref.startswith("#") else ref


def get_parent_rdfinfo(element, rdf_tag):
    parent = element.getparent()
    return parent.get(rdf_tag)


# ---------- Courtesy of Claude ------------
def cgmes_time_to_epoch(timestr: str) -> int:
    """z.B. '1977-01-01T09:00:00Z' -> Seconds since 1970-01-01"""
    dt = datetime.strptime(timestr, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def epoch_to_cgmes_time(epoch: int) -> str:
    """Seconds since 1970-01-01 -> '1977-01-01T09:00:00Z'"""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------

# ---------------------------------------------------------------------------
# XML I/O
# ---------------------------------------------------------------------------


def parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_comments=False, remove_blank_text=False)
    return etree.parse(str(path), parser)


def serialise_xml(tree: etree._ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(
        str(path),
        xml_declaration=True,
        encoding="utf-8",
        pretty_print=True,
    )


# ---------------------------------------------------------------------------
# Bundle I/O helpers
# ---------------------------------------------------------------------------


def extract_bundle(src: Path, tmp_dir: Path) -> List[Tuple[str, Path]]:
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


def pack_bundle(
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
