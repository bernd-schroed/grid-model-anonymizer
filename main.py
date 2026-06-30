# main.py
import argparse
import time
from pathlib import Path

from anym_cgmes import anonymize_cgmes, restore_cgmes
from anym_csv import transform_csv_with_mapping
from anym_PF import run_powerfactory_import_export, run_powerfactory_restore

# from anym_panda_power import stuff


def parse_args():
    parser = argparse.ArgumentParser(
        description="Anonymizer for PowerFactory .pfd, CGMES .zip/.xml, and .csv files"
    )

    parser.add_argument(
        "--input_file",
        type=Path,
        required=True,
        help="Input file: .pfd, .zip (CGMES bundle), .xml (single CGMES file), or .csv",
    )

    parser.add_argument(
        "--seed",
        type=str,
        default="timon123",
        help="Seed for deterministic anonymization (anonymize mode only)",
    )

    parser.add_argument(
        "--output_file",
        type=Path,
        default=None,
        help="Output file (derived automatically if omitted)",
    )

    parser.add_argument(
        "--mapping_file",
        type=Path,
        default=None,
        help="Mapping JSON (output when anonymizing, input when restoring). Derived automatically if omitted.",
    )

    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Restore / reverse anonymization instead of anonymizing",
    )

    # --- PowerFactory / CGMES flags ---
    parser.add_argument(
        "--no-gps",
        dest="gps",
        action="store_true",
        help="Delete GPS coordinates (set to 0,0) instead of applying transform+jitter",
    )
    parser.set_defaults(gps=False)

    parser.add_argument(
        "--no-desc",
        dest="desc",
        action="store_true",
        help="Replace descriptions with 'Deleted' instead of anonymizing them",
    )
    parser.set_defaults(desc=False)

    parser.add_argument(
        "--remap-ids",
        dest="remap_ids",
        action="store_true",
        help=(
            "CGMES only: also remap rdf:ID / rdf:about / rdf:resource values. "
            "Default: off (CGMES IDs are already UUIDs without readable names). "
            "Enable if your IDs contain readable substation or asset names."
        ),
    )
    parser.set_defaults(remap_ids=False)

    # --- CSV options ---
    parser.add_argument(
        "--csv-columns",
        type=str,
        default=None,
        help="Comma-separated CSV column names to anonymize. Default: 'Name Ortsnetzstation' or first column.",
    )

    args = parser.parse_args()

    suf = args.input_file.suffix.lower()
    if suf not in (".pfd", ".csv", ".zip", ".xml"):
        raise ValueError("Input must be a .pfd, .zip, .xml, or .csv file")

    # --- Auto-derive output file ---
    if args.output_file is None:
        stem = args.input_file.stem
        if suf == ".pfd":
            suffix = "_reverse.pfd" if args.reverse else "_anonym.pfd"
        elif suf in (".zip", ".xml"):
            suffix = "_reverse.zip" if args.reverse else "_anonym.zip"
        else:
            suffix = "_reverse.csv" if args.reverse else "_anonym.csv"
        args.output_file = args.input_file.with_name(stem + suffix)

    # --- Auto-derive mapping file ---
    if args.mapping_file is None:
        args.mapping_file = args.input_file.with_name(
            args.input_file.stem + "_mapping.json"
        )

    return args


def _is_cgmes(path: Path) -> bool:
    """True if the file looks like a CGMES bundle (zip or xml)."""
    return path.suffix.lower() in (".zip", ".xml")


def main():
    args = parse_args()

    print("input_file  :", args.input_file)
    print("output_file :", args.output_file)
    print("mapping_file:", args.mapping_file)
    print("mode        :", "restore" if args.reverse else "anonymize")

    suf = args.input_file.suffix.lower()

    # ------------------------------------------------------------------ PFD
    if suf == ".pfd":
        print("seed:", args.seed)
        print("desc (True=delete):", args.desc)
        print("gps  (True=delete):", args.gps)

        if args.reverse:
            run_powerfactory_restore(
                in_path=args.input_file,
                out_path=args.output_file,
                mapping_path=args.mapping_file,
            )
        else:
            run_powerfactory_import_export(
                in_path=args.input_file,
                out_path=args.output_file,
                random_seed=args.seed,
                mapping_out_path=args.mapping_file,
                desc=args.desc,
                gps=args.gps,
            )

    # ----------------------------------------------------------------- CGMES
    elif _is_cgmes(args.input_file):
        print("seed:", args.seed)
        print("desc (True=delete):", args.desc)
        print("gps  (True=delete):", args.gps)

        if args.reverse:
            restore_cgmes(
                in_path=args.input_file,
                out_path=args.output_file,
                mapping_path=args.mapping_file,
            )
        else:
            anonymize_cgmes(
                in_path=args.input_file,
                out_path=args.output_file,
                seed=args.seed,
                mapping_out_path=args.mapping_file,
                desc=args.desc,
                gps=args.gps,
                remap_ids=args.remap_ids,
            )

    # ------------------------------------------------------------------ CSV
    elif suf == ".csv":
        cols = None
        if args.csv_columns:
            cols = [c.strip() for c in args.csv_columns.split(",") if c.strip()]

        transform_csv_with_mapping(
            csv_in=args.input_file,
            csv_out=args.output_file,
            mapping_path=args.mapping_file,
            mode="restore" if args.reverse else "anonymize",
            seed=args.seed,
            columns=cols,
        )

    else:
        print("File type not supported yet.")


if __name__ == "__main__":
    start = time.time()
    main()
    print(f"Duration: {time.time() - start:.2f} s")


# ---------------------------------------------------------------------------
# Usage examples
# ---------------------------------------------------------------------------
# Anonymize CGMES bundle (GPS transformed, descriptions anonymized):
#   python main.py --input_file IEEE39.zip
#
# Anonymize + delete GPS + delete descriptions:
#   python main.py --input_file IEEE39.zip --no-gps --no-desc
#
# Restore CGMES:
#   python main.py --input_file IEEE39_anonym.zip --reverse --mapping_file IEEE39_mapping.json
#
# Anonymize PFD:
#   python main.py --input_file model.pfd
#
# Anonymize PFD (delete GPS, delete desc):
#   python main.py --input_file model.pfd --no-gps --no-desc
#
# Restore PFD:
#   python main.py --input_file model_anonym.pfd --reverse --mapping_file model_mapping.json
#
# CSV:
#   python main.py --input_file remote_anonym.csv --reverse --mapping_file model_mapping.json
