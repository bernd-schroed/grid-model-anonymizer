import hashlib
import json
import math
from pathlib import Path
from typing import Dict, Tuple


# ----------------------------
# Anonymizer container (UNIFIED STRING MAPPING)
# ----------------------------
class SeededNameAnonymizer:
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

    def translate_attr(self, attr: str, value: str) -> str:
        # attr is intentionally ignored now (unified mapping)
        old = "" if value is None else str(value)
        return self.translate(old)

    def _hash(self, text: str, length: int) -> str:
        payload = (self.seed + "\n" + str(text).strip()).encode("utf-8")
        return hashlib.sha256(payload).hexdigest().upper()[:length]

    def translate(self, name: str) -> str:
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
        # unified mapping for all ANON_* strings
        "anon_mapping": anonymizer.forward,
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

    def _u(tag: str) -> float:
        h = hashlib.sha256((str(seed) + "|" + tag).encode("utf-8")).hexdigest()
        return (int(h[:16], 16) % 10_000_000) / 10_000_000.0

    angle = 2.0 * math.pi * _u("gps_angle")
    mirror = _u("gps_mirror") > 0.5
    dx = (2.0 * _u("gps_dx") - 1.0) * max_shift_frac
    dy = (2.0 * _u("gps_dy") - 1.0) * max_shift_frac
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
