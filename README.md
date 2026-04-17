# Grid Model Anonymizer

**DIgSILENT PowerFactory · CGMES 2.4 · CSV**  
Version 2.0 – April 2026

---

## Overview

Deterministic, reversible anonymization tool for electrical grid models. Supports three formats and produces a reusable mapping JSON that enables full restoration at any time.

| Format | Description |
|---|---|
| `.pfd` | DIgSILENT PowerFactory grid models |
| `.zip` / `.xml` | CGMES 2.4 bundles (EQ, GL, TP, SSH, SV, DL, DY) |
| `.csv` | Tabular asset lists (substation names, Fernwirk-IDs) |

---

## Security Concept

- SHA-256 based deterministic hashing – same input + seed always produces the same output
- One-way transformation – no reverse possible without the mapping JSON
- No sequential numbering – no pattern leakage
- Seed isolation – different seeds produce completely independent anonymizations

> **Never distribute the mapping JSON together with an anonymized model. Store it separately with restricted access.**

---

## Installation

### Requirements

- Python 3.9 (must match the PowerFactory Python version)
- DIgSILENT PowerFactory 2024 SP7
- `lxml` (CGMES XML processing)
- `psutil` (PowerFactory process management)

```bash
pip install psutil lxml
```

Ensure the PowerFactory Python path at the top of `anym_PF.py` matches your local installation:

```python
sys.path.append(r"C:\Program Files\DIgSILENT\PowerFactory 2024 SP7\Python\3.9")
```

---

## File Structure

| File | Purpose |
|---|---|
| `main.py` | CLI entry point – routes input to the correct anonymizer |
| `anym_PF.py` | PowerFactory anonymizer (import / anonymize / export `.pfd`) |
| `anym_cgmes.py` | CGMES anonymizer (XML/RDF bundles, GPS, rdf:ID) |
| `anym_csv.py` | CSV anonymizer (substation names, Fernwirk-IDs) |

---

## Command Line Usage

### PowerFactory (`.pfd`)

```bash
# Anonymize
python main.py --input_file "path\to\model.pfd"

# Anonymize – delete GPS and descriptions
python main.py --input_file "model.pfd" --no-gps --no-desc

# Restore
python main.py --input_file "model_anonym.pfd" --reverse --mapping_file "model_mapping.json"
```

### CGMES 2.4 (`.zip` / `.xml`)

```bash
# Anonymize bundle
python main.py --input_file "IEEE39.zip"

# Anonymize – remap rdf:IDs as well
python main.py --input_file "IEEE39.zip" --remap-ids

# Anonymize – delete GPS and descriptions
python main.py --input_file "IEEE39.zip" --no-gps --no-desc

# Restore
python main.py --input_file "IEEE39_anonym.zip" --reverse --mapping_file "IEEE39_mapping.json"
```

### CSV

```bash
# Anonymize
python main.py --input_file "assets.csv"

# Anonymize specific columns
python main.py --input_file "assets.csv" --csv-columns "Name Ortsnetzstation,Bezeichnung"

# Restore
python main.py --input_file "assets_anonym.csv" --reverse --mapping_file "assets_mapping.json"
```

### All CLI Flags

| Flag | Description |
|---|---|
| `--input_file` | Input file (`.pfd`, `.zip`, `.xml`, `.csv`) – required |
| `--seed` | Seed string for deterministic anonymization (default: `timon123`) |
| `--output_file` | Output file path (derived automatically if omitted) |
| `--mapping_file` | Mapping JSON – output when anonymizing, input when restoring |
| `--reverse` | Restore mode: reverse a previous anonymization |
| `--no-gps` | Delete GPS coordinates (set to 0,0) instead of transforming |
| `--no-desc` | Replace descriptions with `Deleted` instead of anonymizing |
| `--remap-ids` | CGMES only: also remap `rdf:ID` / `rdf:about` / `rdf:resource` |
| `--csv-columns` | CSV: comma-separated column names to anonymize |

---

## What Gets Anonymized

| Field / Attribute | Format | Treatment |
|---|---|---|
| `loc_name` / `IdentifiedObject.name` | PFD, CGMES, CSV | SHA-256 token `ANON_XXXXXXXXXX` – deterministic, collision-safe, reversible |
| `IdentifiedObject.description` / `desc` | PFD, CGMES | **Default:** tokenized and anonymized, delimiters preserved. `--no-desc`: replaced with `Deleted` |
| `IdentifiedObject.shortName` / `aliasName` | CGMES | Same token mapping as name |
| `md:Model.description` | CGMES | FullModel header – anonymized or deleted |
| `sernum`, `constr`, `chr_name`, `dar_src`, `manuf`, `for_name`, `foreignKey` | PFD | Unified `anon_mapping` |
| Fernwirk-IDs (regex-matched) | CSV | In-cell IDs anonymized; status tokens (EIN/AUS) stripped |
| GPS / `PositionPoint` coordinates | PFD, CGMES | Rotation + jitter in normalised space (see below), or 0,0 with `--no-gps` |
| `rdf:ID` / `rdf:about` / `rdf:resource` | CGMES | Only with `--remap-ids`; deterministic UUID remapping |
| `cimRdfId` | PFD | Only when explicitly enabled; deterministic UUID remapping |

### Description tokenization detail

Without `--no-desc`, descriptions are split on spaces and semicolons; each token is independently anonymized while delimiters are preserved:

```
ORT;Hauptstraße;123  →  ANON_E3F1A2;ANON_9B4C71;ANON_0D52A8
```

Restoration uses a regex pass that replaces every `ANON_*` token found in the mapping.

---

## GPS Coordinate Transformation

The default GPS mode applies a seed-derived transformation that moves grid elements to geographically distant but always valid locations.

### Algorithm

1. Normalise coordinates: `lat / 90` and `lon / 180` → both in `[−1, 1]`
2. Apply a seed-derived rotation (full 0–360°) and optional axis mirror in the normalised space
3. Add a seed-derived translation of up to ±0.45 normalised units (≈ ±40° lat / ±81° lon)
4. Map back to degrees
5. If the result exceeds the valid range (e.g. near the poles), the shift vector is scaled back proportionally – direction is preserved, only magnitude is reduced
6. Add a per-object deterministic jitter of up to 100 m

This approach avoids the invalid-coordinate problem of rotating in raw lat/lon space (which is not a Euclidean space).

### Example – seed `timon123`

| Location | Original | Anonymized |
|---|---|---|
| Germany | 51.2°N, 10.4°E | ~4°S, 39°E (Kenya) |
| Texas | 28.4°N, 96.5°W | ~50°N, 4°W (Ireland) |
| Japan | 35.7°N, 139.7°E | ~69°S, 5°E (Antarctic Ocean) |

GPS coordinates are always restored from the `gps_mapping` entry in the JSON – never from the written values.

---

## Mapping File Structure

```json
{
  "seed":             "timon123",
  "prefix":           "ANON_",
  "length":           10,
  "anon_mapping":     { "SubstationA": "ANON_48F799127C" },
  "cimRdfId_mapping": { "_oldUUID": "_newUUID" },
  "gps_mapping": {
    "_elemId_transform": { "old": [51.2, 10.4], "new": [-4.3, 39.2] },
    "_elemId_deleted":   { "old": [51.2, 10.4], "deleted": true }
  }
}
```

When anonymizing a CSV after a PFD or CGMES model, point `--mapping_file` to the existing JSON. New entries are merged in, enabling a single mapping file for all formats of the same grid.

---

## Internal Workflow

### Anonymization

| Step | PowerFactory / CGMES | CSV |
|---|---|---|
| 1 | Import file | Read CSV with auto-detected dialect |
| 2 | Collect all unique objects | Detect name + Fernwirk columns |
| 3 | Anonymize string attributes | Anonymize name column |
| 4 | Anonymize `loc_name` / `IdentifiedObject.name` | Strip status tokens, anonymize IDs |
| 5 | Handle descriptions | Write output CSV |
| 6 | Transform / delete GPS | Update mapping JSON |
| 7 | Export anonymized file | – |
| 8 | Save mapping JSON | – |

### Restoration

1. Import anonymized file
2. Restore deleted GPS first (while anonymized identifiers are still in place)
3. Reverse string attributes and `loc_name` via `anon_mapping` reverse lookup
4. Restore descriptions (regex-replace `ANON_*` tokens)
5. Restore `cimRdfId` / `rdf:ID` if they were remapped
6. Restore transformed GPS from `gps_mapping`
7. Export restored file

---

## Determinism and Security

| Property | Behaviour |
|---|---|
| Same seed, same input | Identical anonymization across all runs |
| Different seed | Completely independent anonymization |
| Shared mapping | PFD + CGMES + CSV of the same grid can share one JSON |
| No randomness | Only seed-based SHA-256, no RNG calls |
| Dictionary attack | Possible only if seed AND candidate names are both known |

### Security Recommendations

- Never hardcode the seed – pass it via environment variable or secure parameter store
- Never distribute the mapping JSON together with anonymized models
- Store the mapping securely and separately from the model
- Restrict access to the mapping file (it alone enables restoration)
- Backup original models before anonymizing

---

## Limitations

- Without the mapping JSON, restoration is impossible
- If the mapping JSON is lost, anonymization is irreversible
- CGMES `rdf:ID` remapping is off by default – standard UUID IDs carry no readable names
- GPS transformation preserves relative topology (relative distances between points are maintained); only the absolute location is hidden
- PowerFactory export requires an active PowerFactory 2024 SP7 license

---

## Authors

Timon Conrad – FAU Erlangen-Nürnberg (EES/LEES)  
AI assistance: ChatGPT 5.2 + Claude
Version 2.0 – April 2026
