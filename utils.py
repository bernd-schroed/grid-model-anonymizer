"""
utils.py - Shared anonymization primitives
============================================

Common, format-agnostic building blocks used by anym_PF.py, anym_cgmes.py,
and anym_csv.py: a deterministic seed-based name anonymizer, mapping
JSON I/O, deterministic UUID generation, and a deterministic GPS
coordinate transform. Keeping these here ensures that the same input
value always maps to the same anonymized output across all three
input formats (PowerFactory, CGMES, CSV), so a given asset's name,
ID, and location stay consistent regardless of which file it appears in.

Contents
--------
- SeededNameAnonymizer
    Stateful, deterministic string -> "ANON_<hash>" anonymizer. Reuses
    an existing mapping if a value was already translated, derives new
    tokens via SHA-256(seed + value), and extends the hash length on
    collision to guarantee a 1:1 mapping. Holds three related but
    separate mapping tables: a unified forward/reverse string mapping
    (`forward`/`reverse`) for names and free-text attributes, a
    CIM RDF ID mapping (`cim_forward`) for UUID-like identifiers,
    and a GPS mapping (`gps_mapping`) keyed by an object's original
    identifier.

- save_mapping_json / load_mapping_json
    Serialize/deserialize a SeededNameAnonymizer's full state
    (seed, prefix, hash length, and all three mapping tables) to/from
    a JSON file, so anonymization can later be reversed. `load_mapping_json`
    also transparently migrates older mapping files that used the
    legacy `loc_name_mapping` / `attr_mappings` keys into the current
    unified `anon_mapping` format.

- _generate_seeded_uuid
    Deterministically derives a CIM-style UUID (`_xxxxxxxx-xxxx-...`)
    from an original ID and the seed, for optionally remapping
    rdf:ID-style identifiers.

- _build_geo_transform
    Builds a deterministic GPS coordinate transform function from the
    seed: a rotation + mirror + translation applied in normalized
    [-1, 1] lat/lon space (to avoid distortion from rotating raw
    degree coordinates), producing a strong but reversible-via-mapping
    geographic displacement (e.g. Europe -> Africa/Asia). Used together
    with `_scale_back_to_valid_geo` (defined locally in each format
    module) to keep results within valid lat/lon bounds.

- _obj_unit_from_name
    Deterministic seed+tag+name -> [0, 1) float, used to derive
    per-object jitter (radius/angle) so nearby objects don't all
    shift identically.

- _meters_to_deg_lat / _meters_to_deg_lon
    Small-distance conversion helpers (meters -> degrees) used to
    apply metric-scale GPS jitter on top of the global transform,
    accounting for longitude convergence at higher latitudes.

All anonymization in this module is deterministic given the same
seed and input: re-running anonymization with the same seed always
reproduces the same anonymized output, and is fully reversible given
the resulting mapping JSON.
"""

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Dict, Tuple


# ----------------------------
# Anonymizer container (UNIFIED STRING MAPPING)
# ----------------------------
class SeededNameAnonymizer:
    """Unified seeded anonymizer for string values and special mappings."""

    def __init__(self, seed: str, prefix: str = "ANON_", length: int = 10):
        self.seed = str(seed)
        self.prefix = prefix
        self.length = int(length)

        # One unified mapping for all ANON_* string anonymizations:
        # original -> anon
        self.forward: Dict[str, str] = {}
        # anon -> original
        self.reverse: Dict[str, str] = {}

        # cimRdfId mapping stays separate (uuid-like, not ANON_*)
        self.cim_forward: Dict[str, str] = {}  # old -> new

        # gps mapping keyed by ORIGINAL cimRdfId (before change)
        self.gps_mapping: Dict[str, dict] = {}

        # mapping which line was used before to restore original length and impedance values
        self.line_mapping: Dict[str, str] = {}

        # mapping when the time for case studies are set
        self.time_adding: int = int(
            _seed_hash(seed=seed, tag="study_casereset") % 10000000000
        )
        if self.time_adding % 2 == 0:
            self.time_adding = -self.time_adding
        self.time_mapping: Dict[int, int] = {}

    def translate_attr(self, attr: str, value: str) -> str:  # type: ignore # pylint:disable=unused-argument
        """
        Anonymize an attribute value via the unified string mapping.

        `attr` is accepted for API compatibility but not used to vary the
        mapping (all attributes share one forward/reverse table).
        """

        # attr is intentionally ignored now (unified mapping)
        old = "" if value is None else str(value)
        return self.translate(old)

    def _hash(self, text: str, length: int) -> str:
        payload = (self.seed + "\n" + str(text).strip()).encode("utf-8")
        return hashlib.sha256(payload).hexdigest().upper()[:length]

    def get_hash(self, text: str, length: int) -> str:
        """Public wrapper around `_hash` for deriving a deterministic hash of arbitrary text."""
        return self._hash(text, length)

    def translate(self, name: str) -> str:
        """
        Deterministically anonymize a string, reusing any existing mapping.

        Returns `name` unchanged if it's falsy or already looks like an
        anonymized token (starts with `prefix`, or is a known anon value).
        Otherwise derives a new "<prefix><hash>" token, extending the hash
        length on collision until a unique token is found, and records the
        mapping for later reversal.
        """

        if not name:
            return name
        if name.startswith(self.prefix):
            return name
        if name in self.reverse:
            # already anon token
            return name
        if name in self.forward:
            return self.forward[name]

        token = self._hash(name, self.length)
        new_name = f"{self.prefix}{token}"

        l = self.length
        while new_name in self.reverse and self.reverse[new_name] != name:
            l += 2
            token = self._hash(name, l)
            new_name = f"{self.prefix}{token}"

        self.forward[name] = new_name
        self.reverse[new_name] = name
        return new_name

    def add_time(self, old_time: int) -> int:
        new_time = old_time + self.time_adding
        if new_time < 0:
            new_time = old_time - self.time_adding
        if new_time >= 2**32:  # internal edge value for time is 2**32
            new_time = self.time_adding
        return new_time


def save_mapping_json(path: Path, anonymizer: SeededNameAnonymizer):
    """Serialize anonymizer state to a JSON mapping file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "seed": anonymizer.seed,
        "prefix": anonymizer.prefix,
        "length": anonymizer.length,
        "time_mapping": anonymizer.time_mapping,
        # unified mapping for all ANON_* strings
        "anon_mapping": anonymizer.forward,
        "line_mapping": anonymizer.line_mapping,
        # keep separate
        "cimRdfId_mapping": anonymizer.cim_forward,
        "gps_mapping": anonymizer.gps_mapping,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_mapping_json(path: Path) -> dict:
    """
    Loads mapping and supports migration from older JSONs that had:
      - loc_name_mapping
      - attr_mappings
    into:
      - anon_mapping
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    if "anon_mapping" not in data:
        merged: Dict[str, str] = {}
        merged.update(data.get("loc_name_mapping", {}) or {})

        attr_maps = data.get("attr_mappings", {}) or {}
        if isinstance(attr_maps, dict):
            for _, mp in attr_maps.items():
                if isinstance(mp, dict):
                    merged.update(mp)

        data["anon_mapping"] = merged

    return data


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


def _u(tag: str, seed: str) -> float:
    h = hashlib.sha256((str(seed) + "|" + tag).encode("utf-8")).hexdigest()
    return (int(h[:16], 16) % 10_000_000) / 10_000_000.0


def _build_geo_transform(seed: str, max_shift_frac: float = 0.45):
    """
    Rotation + Translation im normalisierten Koordinatenraum.

    lat/90 und lon/180 werden auf [-1, 1] normiert, dort wird eine
    seed-basierte Rotation + Verschiebung angewandt, dann zurück auf Grad
    gemappt.  Das vermeidet ungültige Koordinaten durch Rotation im rohen
    Grad-Raum (wo lat/lon kein euklidischer Raum ist) und erzeugt trotzdem
    starke Anonymisierung: Punkte in Europa landen typischerweise in Afrika
    oder Asien.

    max_shift_frac=0.45 entspricht bis zu ±40.5° Lat / ±81° Lon Verschiebung
    zusätzlich zur Rotation.  _scale_back_to_valid_geo fängt Randfälle ab.
    """

    angle = 2.0 * math.pi * _u("gps_angle", seed)
    mirror = _u("gps_mirror", seed) > 0.5
    rescale = math.exp((_u("gps_rescale", seed) - 1) * 2.0)
    dx = (2.0 * _u("gps_dx", seed) - 1.0) * max_shift_frac
    dy = (2.0 * _u("gps_dy", seed) - 1.0) * max_shift_frac
    c, s = math.cos(angle), math.sin(angle)

    def transform(lat: float, lon: float) -> Tuple[float, float]:
        x = lon / 180.0  # normieren auf [-1, 1]
        y = lat / 90.0
        if mirror:
            x = -x  # Achsenspiegelung für zusätzliche Obfuskation
        xr = c * x - s * y  # Rotation im normierten Raum
        yr = s * x + c * y
        xr += dx  # Verschiebung
        yr += dy
        xr = xr * rescale
        yr = yr * rescale
        return yr * 90.0, xr * 180.0  # zurück auf Grad

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


def _scale_back_to_valid_geo(
    old_lat: float,
    old_lon: float,
    new_lat: float,
    new_lon: float,
) -> Tuple[float, float]:
    lat_limit = 89.9
    lon_limit = 179.9
    dlat = new_lat - old_lat
    dlon = new_lon - old_lon
    scale = 1.0
    if dlat > 0 and new_lat > lat_limit:
        scale = min(scale, (lat_limit - old_lat) / dlat)
    elif dlat < 0 and new_lat < -lat_limit:
        scale = min(scale, (-lat_limit - old_lat) / dlat)
    if dlon > 0 and new_lon > lon_limit:
        scale = min(scale, (lon_limit - old_lon) / dlon)
    elif dlon < 0 and new_lon < -lon_limit:
        scale = min(scale, (-lon_limit - old_lon) / dlon)
    scale = max(0.0, scale)
    return old_lat + scale * dlat, old_lon + scale * dlon


def _p(p: Path) -> str:
    return os.fspath(Path(p).resolve())


def _seed_hash(seed: str, tag: str) -> int:
    h = hashlib.sha256((str(seed) + "|" + tag).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _seed_unit(seed: str, tag: str) -> float:
    x = _seed_hash(seed, tag)
    return (x % 10_000_000) / 10_000_000.0
