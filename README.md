# Grid Model Anonymizer for DIgSILENT PowerFactory

This tool anonymizes DIgSILENT PowerFactory `.pfd` models in a
deterministic and reversible way.

## Features

-   Deterministic renaming of all `loc_name`
-   GPS anonymization (delete or transform)
-   Full restore using mapping JSON
-   Optional description handling

------------------------------------------------------------------------

## Installation

Requirements:

-   Python 3.9 (matching PowerFactory version)
-   DIgSILENT PowerFactory 2024 SP7
-   psutil

Install dependency:

    pip install psutil

Ensure the PowerFactory Python path in `anym_PF.py` matches your
installation.

------------------------------------------------------------------------

## Anonymization

Basic:

    python main.py --input_file "path\to\model.pfd"

Output files:

-   `model_anonym.pfd`
-   `model_mapping.json`

Keep descriptions:

    python main.py --input_file "model.pfd" --no-desc

Delete GPS coordinates:

    python main.py --input_file "model.pfd" --no-gps

------------------------------------------------------------------------

## Restore

    python main.py   --input_file "model_anonym.pfd"   --reverse   --mapping_file "model_mapping.json"

Output:

-   `model_anonym_reverse.pfd`

------------------------------------------------------------------------

## Mapping Structure

The JSON contains:

-   `seed`
-   `loc_name_mapping`
-   `gps_mapping`

GPS mapping:

Transformed:

    {"old":[lat,lon], "new":[lat,lon]}

Deleted:

    {"old":[lat,lon], "deleted":true, "full_name_after":"..."}

------------------------------------------------------------------------

## Workflow

1.  Regenerate CIM IDs\
2.  Rename loc_name\
3.  Apply GPS transformation or deletion\
4.  Export anonymized model

Restore performs the inverse operations using the mapping JSON.

------------------------------------------------------------------------

## Notes

-   Same seed produces identical anonymization.
-   Mapping file is required for restore.
-   Always keep the mapping file secure.
-   Backup original models before anonymizing.
