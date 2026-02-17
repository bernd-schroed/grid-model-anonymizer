import argparse
import time
from pathlib import Path

from anym_PF import run_powerfactory_import_export, run_powerfactory_restore


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input_file",
        type=Path,
        required=True,
        help="Input .pfd",
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
        help="Optional: Output .pfd (sonst automatisch)",
    )

    parser.add_argument(
        "--mapping_file",
        type=Path,
        default=None,
        help="Mapping JSON (bei anonymize: Output, bei restore: Input). Default automatisch.",
    )

    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Wenn gesetzt: Restore/Reverse statt Anonymisieren",
    )

    # Flags wie von dir gewünscht:
    # --no-gps bedeutet: GPS löschen => gps=True
    parser.add_argument(
        "--no-gps",
        dest="gps",
        action="store_true",
        help="GPS wird gelöscht (0,0 gesetzt)",
    )
    parser.set_defaults(gps=False)

    # --no-desc bedeutet: Description anfassen => desc=True
    parser.add_argument(
        "--no-desc",
        dest="desc",
        action="store_true",
        help="Description wird verändert (Deleted gesetzt)",
    )
    parser.set_defaults(desc=False)

    args = parser.parse_args()

    if args.input_file.suffix.lower() != ".pfd":
        raise ValueError("Input muss eine .pfd Datei sein")

    # ---------------------------
    # Automatisches Output-File
    # ---------------------------
    if args.output_file is None:
        suffix = "_reverse.pfd" if args.reverse else "_anonym.pfd"
        args.output_file = args.input_file.with_name(args.input_file.stem + suffix)

    # ---------------------------
    # Automatische Mapping-Datei
    # ---------------------------
    if args.mapping_file is None:
        args.mapping_file = args.input_file.with_name(args.input_file.stem + "_mapping.json")

    return args


def main():
    args = parse_args()

    print("input_file:", args.input_file)
    print("output_file:", args.output_file)
    print("mapping_file:", args.mapping_file)
    print("mode:", "reverse" if args.reverse else "anonymize")
    print("seed:", args.seed)
    print("desc (True=anfassen):", args.desc)
    print("gps (True=löschen):", args.gps)

    if args.input_file.suffix.lower() == ".pfd":
        if args.reverse:
            # RESTORE/REVERSE
            run_powerfactory_restore(
                in_path=args.input_file,
                out_path=args.output_file,
                mapping_path=args.mapping_file,
            )
        else:
            # ANONYMIZE
            run_powerfactory_import_export(
                in_path=args.input_file,
                out_path=args.output_file,
                random_seed=args.seed,
                mapping_out_path=args.mapping_file,
                desc=args.desc,
                gps=args.gps,
            )
    else:
        print("Other Files them .pfd not supported yet")



if __name__ == "__main__":
    start = time.time()
    main()
    end = time.time()
    print(f"Dauer: {end - start:.2f} Sekunden")
"""

if __name__ == "__main__":
    run_powerfactory_import_export(
        in_path=Path(r"X:\2024_BWMK_GridAssist\06_TP3\LVN HEO1\Gridanonymisierer_test\timon_test\20kVTP3_mit_SL.pfd"),
        out_path=Path(r"X:\2024_BWMK_GridAssist\06_TP3\LVN HEO1\Gridanonymisierer_test\timon_test\20kVTP3_mit_SL_anonym.pfd.pfd"),
        random_seed="debug123",
        mapping_out_path=Path(r"X:\2024_BWMK_GridAssist\06_TP3\LVN HEO1\Gridanonymisierer_test\timon_test\20kVTP3_mit_SL_mapping.json"),
        desc=False,
        gps=False,
    )
"""
"""
Anonymise (GPS remains transformed, desc remains)   python main.py --input_file "X:\...\model.pfd"


Anonymise + delete GPS + touch desc                 python main.py --input_file "X:\...\model.pfd" --no-gps --no-desc


Reverse (Restore):                                  python main.py --input_file "X:\...\model_anonym.pfd" --reverse --mapping_file "X:\...\model_mapping.json"
"""