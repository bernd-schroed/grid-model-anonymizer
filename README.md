# Grid Model Anonymizer for DIgSILENT PowerFactory

A deterministic, reversible anonymization tool for DIgSILENT
PowerFactory `.pfd` models.

------------------------------------------------------------------------

## Overview

This tool anonymizes PowerFactory grid models while preserving
structural integrity and ensuring full reversibility using a secure
mapping file.

It supports:

-   Deterministic renaming of object names (`loc_name`)
-   Deterministic anonymization of string attributes
-   Description (`desc`) token anonymization or deletion
-   CIM RDF ID anonymization (optional)
-   GPS coordinate deletion or deterministic transformation + jitter
-   Full restoration using a mapping JSON
-   Deterministic behavior based on a user-defined seed

------------------------------------------------------------------------

## Security Concept

The anonymization uses:

-   SHA-256 based hashing
-   Seed-based deterministic generation
-   One-way transformation (no reverse possible without mapping)
-   No pattern leakage
-   No sequential numbering

Only the mapping JSON enables restoration. Without it, anonymized values
cannot be reconstructed.

------------------------------------------------------------------------

## Installation

### Requirements

-   Python 3.9 (must match PowerFactory version)
-   DIgSILENT PowerFactory 2024 SP7
-   psutil
-   pypandoc

Install dependencies:

``` bash
pip install psutil pypandoc
```

Ensure the PowerFactory Python path in `anym_PF.py` matches your
installation.

------------------------------------------------------------------------

## Command Line Usage

### Basic Anonymization

``` bash
python main.py --input_file "path\to\model.pfd"
```

Outputs:

-   `model_anonym.pfd`
-   `model_mapping.json`

------------------------------------------------------------------------

### Keep Descriptions (Anonymize Instead of Delete)

``` bash
python main.py --input_file "model.pfd" --no-desc
```

------------------------------------------------------------------------

### Delete GPS Coordinates

``` bash
python main.py --input_file "model.pfd" --no-gps
```

------------------------------------------------------------------------

### Full Restore

``` bash
python main.py \
  --input_file "model_anonym.pfd" \
  --reverse \
  --mapping_file "model_mapping.json"
```

Output:

-   `model_anonym_reverse.pfd`

------------------------------------------------------------------------

## What Gets Anonymized

### 1. loc_name

All object names are deterministically replaced:

    OriginalName → ANON_XXXXXXXXXX

Properties:

-   Same input + same seed = same output
-   Collision-safe
-   Reversible via mapping file

------------------------------------------------------------------------

### 2. String Attributes

The following attributes are anonymized:

-   `sernum`
-   `constr`
-   `chr_name`
-   `dar_src`
-   `manuf`
-   `for_name`

All values are mapped through the same deterministic hashing system.

------------------------------------------------------------------------

### 3. Description Field (`desc`)

Two modes:

#### Default

Descriptions are deleted and replaced with:

    Deleted

#### `--no-desc`

Descriptions are tokenized and anonymized while preserving structure.

Example:

    ORT;ABC;123 → ANON_AAAAA;ANON_BBBBB;ANON_CCCCC

------------------------------------------------------------------------

### 4. CIM RDF ID

If enabled, CIM IDs are replaced with deterministic UUID-like values
generated from:

    seed + original_cim_id

------------------------------------------------------------------------

### 5. GPS Handling

Two modes:

#### Default (Transform + Jitter)

-   Global rotation
-   Optional mirroring
-   Deterministic coordinate shift
-   Object-level deterministic jitter

Mapping entry:

    {"old":[lat,lon], "new":[lat,lon]}

#### `--no-gps` (Delete)

Coordinates are set to 0/0 and stored in mapping:

    {"old":[lat,lon], "deleted":true}

------------------------------------------------------------------------

## Mapping File Structure

The JSON contains:

-   `seed`
-   `prefix`
-   `length`
-   `anon_mapping` (original → anonymized)
-   `cimRdfId_mapping`
-   `gps_mapping`

Example:

    {
      "anon_mapping": {
        "SubstationA": "ANON_48F799127C"
      }
    }

------------------------------------------------------------------------

## Internal Workflow

### Anonymization Process

1.  Import `.pfd`
2.  Regenerate CIM IDs
3.  Collect unique objects
4.  Anonymize string attributes
5.  Anonymize `loc_name`
6.  Process descriptions
7.  Apply GPS handling
8.  Export anonymized model
9.  Save mapping JSON

------------------------------------------------------------------------

### Restore Process

1.  Import anonymized `.pfd`
2.  Restore deleted GPS first
3.  Reverse string attributes
4.  Reverse `loc_name`
5.  Restore descriptions
6.  Restore CIM IDs
7.  Restore transformed GPS
8.  Export restored model

------------------------------------------------------------------------

## Determinism

-   Same seed = identical anonymization across runs
-   Different seed = completely different anonymization
-   No randomness outside seed-based hashing

------------------------------------------------------------------------

## Security Recommendations

-   Never hardcode the seed
-   Never distribute mapping JSON with anonymized models
-   Store mapping securely and separately
-   Restrict mapping access
-   Backup original models

------------------------------------------------------------------------

## Limitations

-   Without mapping file, restoration is impossible
-   If mapping file is lost, anonymization is irreversible
-   Dictionary attacks are only possible if seed and candidate space are
    both known

------------------------------------------------------------------------

## Version

Generated: 2026-02-17

------------------------------------------------------------------------

## Author

Timon Conrad @ FAU with the help of ChatGPT 5.2