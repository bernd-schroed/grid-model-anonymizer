# pylint: disable= wrong-import-position
import logging
import sys
import time
from pathlib import Path

sys.path.append(".")

from anym.anym_cgmes import anonymize_cgmes, restore_cgmes
from anym.anym_csv import transform_csv_with_mapping
from anym.anym_json import anonymize_json_file, restore_json_anonymization
from anym.anym_pf import run_powerfactory_import_export
from restore.restore_pf import run_powerfactory_restore

logger = logging.getLogger("Test Anonymization-Restore")

SEED = "Testing_seed"


def test_anonymization(test_file, type_folder):
    input_file = test_file
    output_file = Path(type_folder, "anon", test_file.name)
    mapping_file = Path(type_folder, "mapping", test_file.stem, ".json")
    try:
        if type_folder.name == "cgmes":
            anonymize_cgmes(
                in_path=input_file,
                out_path=output_file,
                seed=SEED,
                mapping_out_path=mapping_file,
            )

        elif type_folder.name == "CSV":
            transform_csv_with_mapping(
                csv_in=input_file,
                csv_out=output_file,
                mapping_path=mapping_file,
                mode="anonymize",
                seed=SEED,
            )
        elif type_folder.name == "JSON":
            anonymize_json_file(
                input_json=input_file,
                output_json=output_file,
                mapping_output=mapping_file,
                seed=SEED,
            )
        elif type_folder.name == "PowerFactory":
            run_powerfactory_import_export(
                in_path=input_file,
                out_path=output_file,
                random_seed=SEED,
                mapping_out_path=mapping_file,
                desc=False,
                gps=False,
            )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "There was an error while handling the Anonymization of the %s File",
            input_file,
        )
        logger.error(e)
    return output_file


def test_restore(anon_file, type_folder):
    input_file = anon_file
    output_file = Path(type_folder, "restore", anon_file.name)
    mapping_file = Path(type_folder, "mapping", anon_file.stem, ".json")
    try:
        if type_folder.name == "cgmes":
            restore_cgmes(
                in_path=input_file,
                out_path=output_file,
                mapping_path=mapping_file,
            )

        elif type_folder.name == "CSV":
            transform_csv_with_mapping(
                csv_in=input_file,
                csv_out=output_file,
                mapping_path=mapping_file,
                mode="restore",
                seed=SEED,
            )
        elif type_folder.name == "JSON":
            restore_json_anonymization(
                input_json=input_file,
                output_json=output_file,
                mapping_input=mapping_file,
            )
        elif type_folder.name == "PowerFactory":
            run_powerfactory_restore(
                in_path=input_file,
                out_path=output_file,
                mapping_path=mapping_file,
            )
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error(
            "There was an error while handling the Anonymization of the %s File",
            input_file,
        )
        logger.error(e)


def test_file_anon_restore(test_file: Path, type_folder: Path):
    anon_file = test_anonymization(test_file, type_folder)
    test_restore(anon_file, type_folder)
    print(type_folder)


def test_data_type(type_folder: Path):
    orig_data_folder = Path(type_folder, "orig")
    test_files = orig_data_folder.iterdir()
    for file in test_files:
        print(file)
        test_file_anon_restore(file, type_folder)


def test_anon_restore():
    test_dir = Path(__file__).parent.resolve()
    data_type_dirs = test_dir.iterdir()
    for data_type_folder in data_type_dirs:
        if data_type_folder.is_dir():
            test_data_type(data_type_folder)


if __name__ == "__main__":
    start = time.time()
    logging.basicConfig(level=logging.DEBUG, filename="test.log", encoding="utf-8")
    test_anon_restore()
    logger.info(str(f"Duration: {time.time() - start:.2f} s"))
