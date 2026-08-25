import csv
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from anym import anym_csv


def get_example_data(csv_in, columns: Optional[List[str]] = None):
    dialect = anym_csv._detect_csv_dialect(csv_in)
    # elems: Dict[str, List] = {}
    with open(csv_in, encoding="utf-8-sig", newline="") as f_in:
        reader = csv.DictReader(f_in, delimiter=dialect.delimiter)
        # fieldnames = reader.fieldnames or []
        fieldnames = reader.fieldnames or []

        name_cols = anym_csv._pick_columns(fieldnames, columns)
        elems: Dict[str, List] = {key: [] for key in name_cols}
        for row in reader:
            # 1) Station Names

            for col in name_cols:
                val = (row.get(col, "") or "").strip()
                elems[col].append(val)
        return elems


@pytest.mark.dependency()
def test_csv_anym():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "CSV")
    input_file = Path(data_dir, "orig", "csv_test.csv")
    output_file = Path(data_dir, "anym", "csv_test.csv")
    mapping_file = Path(data_dir, "mapping", "csv_mapping_test.json")
    seed = "test_seed"
    anym_csv.transform_csv_with_mapping(
        csv_in=input_file,
        csv_out=output_file,
        mapping_path=mapping_file,
        mode="anonymize",
        seed=seed,
    )

    orig_data = get_example_data(input_file)
    anym_data = get_example_data(output_file)

    for orig_type, anym_type in zip(orig_data.values(), anym_data.values()):
        for orig_el, anym_el in zip(orig_type, anym_type):
            assert orig_el != anym_el
            assert anym_el.startswith("ANON_")
    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_csv_anym"])
def test_csv_restore():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "CSV")
    input_file = Path(data_dir, "anym", "csv_test.csv")
    output_file = Path(data_dir, "restore", "csv_test.csv")
    mapping_file = Path(data_dir, "mapping", "csv_mapping_test.json")
    seed = "test_seed"

    anym_csv.transform_csv_with_mapping(
        csv_in=input_file,
        csv_out=output_file,
        mapping_path=mapping_file,
        mode="restore",
        seed=seed,
    )
    orig_file = Path(data_dir, "orig", "csv_test.csv")
    orig_data = get_example_data(orig_file)
    restore_data = get_example_data(output_file)

    for orig_type, restore_data in zip(orig_data.values(), restore_data.values()):
        for orig_el, restore_data in zip(orig_type, restore_data):
            assert orig_el == restore_data
    assert mapping_file.exists()
