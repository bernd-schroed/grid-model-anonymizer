import argparse
import time
from pathlib import Path

from anym_PF import run_powerfactory_import_export, run_powerfactory_restore
from anym_csv import transform_csv_with_mapping


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input_file",
        type=Path,
        required=True,
        help="Input .pfd oder .csv",
    )

    parser.add_argument(
        "--seed",
        type=str,
        default="timon123",
        help="Seed für deterministische Anonymisierung (nur im anonymize mode)",
    )

    parser.add_argument(
        "--output_file",
        type=Path,
        default=None,
        help="Optional: Output Datei (sonst automatisch)",
    )

    parser.add_argument(
        "--mapping_file",
        type=Path,
        default=None,
        help="Mapping JSON (bei anonymize: Output/Update, bei restore: Input). Default automatisch.",
    )

    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Wenn gesetzt: Restore/Reverse statt Anonymisieren",
    )

    # PF flags
    parser.add_argument("--no-gps", dest="gps", action="store_true", help="GPS wird gelöscht (0,0 gesetzt)")
    parser.set_defaults(gps=False)

    parser.add_argument("--no-desc", dest="desc", action="store_true", help="Description wird verändert (Deleted gesetzt)")
    parser.set_defaults(desc=False)

    # CSV options
    parser.add_argument(
        "--csv-columns",
        type=str,
        default=None,
        help="CSV Spaltennamen (kommagetrennt), die umgewandelt werden sollen. Default: 'Name Ortsnetzstation' oder erste Spalte.",
    )

    args = parser.parse_args()

    suf = args.input_file.suffix.lower()
    if suf not in (".pfd", ".csv"):
        raise ValueError("Input muss eine .pfd oder .csv Datei sein")

    # ---------------------------
    # Automatisches Output-File
    # ---------------------------
    if args.output_file is None:
        if suf == ".pfd":
            suffix = "_reverse.pfd" if args.reverse else "_anonym.pfd"
            args.output_file = args.input_file.with_name(args.input_file.stem + suffix)
        else:
            suffix = "_reverse.csv" if args.reverse else "_anonym.csv"
            args.output_file = args.input_file.with_name(args.input_file.stem + suffix)

    # ---------------------------
    # Automatische Mapping-Datei
    # ---------------------------
    if args.mapping_file is None:
        # gleiche Logik wie bei dir
        args.mapping_file = args.input_file.with_name(args.input_file.stem + "_mapping.json")

    return args

def main():
    args = parse_args()

    print("input_file:", args.input_file)
    print("output_file:", args.output_file)
    print("mapping_file:", args.mapping_file)
    print("mode:", "reverse" if args.reverse else "anonymize")

    suf = args.input_file.suffix.lower()

    if suf == ".pfd":
        print("seed:", args.seed)
        print("desc (True=anfassen):", args.desc)
        print("gps (True=löschen):", args.gps)

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
        print("Other files not supported yet")

if __name__ == "__main__":
    start = time.time()
    main()
    end = time.time()
    print(f"Dauer: {end - start:.2f} Sekunden")

"""
Anonymise (GPS remains transformed, desc remains)   python main.py --input_file "X:\...\model.pfd"


Anonymise + delete GPS + touch desc                 python main.py --input_file "X:\...\model.pfd" --no-gps --no-desc


Reverse (Restore):                                  python main.py --input_file "X:\...\model_anonym.pfd" --reverse --mapping_file "X:\...\model_mapping.json"

CSV:                                                python main.py --input_file remote_anonym.csv --reverse --mapping_file model_mapping.json
"""