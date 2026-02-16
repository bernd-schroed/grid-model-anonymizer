import argparse
import time
from pathlib import Path


from anym_PF import run_powerfactory_import_export


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input_file",
        type=Path,
        default=Path(r"X:\2024_BWMK_GridAssist\06_TP3\LVN HEO1\Gridanonymisierer_test\20kVTP3_mit_SL.pfd"),
    )

    parser.add_argument(
        "--output_file",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--seed",
        type=str,
        default="timon123",
    )

    parser.add_argument(
        "--desc",
        help="Wenn gesetzt, werden Beschreibungen gelöscht",
        default=True,
    )

    parser.add_argument(
        "--gps",
        help="Wenn gesetzt, wird GPS gelöscht",
        #default=True,
        default=False,
    )

    parser.add_argument(
        "--output_file_dct",
        type=Path,
        default=None,
    )

    args = parser.parse_args()

    # ---------------------------
    # Automatisches Output-File
    # ---------------------------
    if args.output_file is None:
        args.output_file = args.input_file.with_name(
            args.input_file.stem + "_anonym.pfd"
        )

    # ---------------------------
    # Automatische Mapping-Datei
    # ---------------------------
    if args.output_file_dct is None:
        args.output_file_dct = args.input_file.with_name(
            args.input_file.stem + "_mapping.json"
        )

    print("input_file:", args.input_file)
    print("output_file:", args.output_file)
    print("seed:", args.seed)
    print("output_file_dct:", args.output_file_dct)

    return (
        args.input_file,
        args.output_file,
        args.seed,
        args.output_file_dct,
        args.desc,
        args.gps,
    )


def main():
    input_file, output_file, seed, output_file_dct, desc, gps = parse_args()

    if input_file.suffix.lower() == ".pfd":
        run_powerfactory_import_export(
            in_path=input_file,
            out_path=output_file,
            random_seed=seed,
            mapping_out_path=output_file_dct,
            desc=desc,
            gps = gps
        )
    else:
        print("File not supported")


if __name__ == "__main__":
    start = time.time()
    main()
    end = time.time()
    # time.sleep(3000)
    print(f"Dauer: {end - start:.2f} Sekunden")
