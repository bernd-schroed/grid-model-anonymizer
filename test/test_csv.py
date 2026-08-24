from pathlib import Path

import pytest

from anym.anym_csv import transform_csv_with_mapping


@pytest.mark.dependency()
def test_csv_anym():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "CSV")
    input_file = Path(data_dir, "orig", "csv_test.csv")
    output_file = Path(data_dir, "anym", "csv_test.csv")
    mapping_file = Path(data_dir, "mapping", "csv_mapping_test.json")
    seed = "test_seed"
    transform_csv_with_mapping(
        csv_in=input_file,
        csv_out=output_file,
        mapping_path=mapping_file,
        mode="anonymize",
        seed=seed,
    )

    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_csv_anym"])
def test_csv_restore():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "CSV")
    input_file = Path(data_dir, "anym", "csv_test.csv")
    output_file = Path(data_dir, "restore", "csv_test.csv")
    mapping_file = Path(data_dir, "mapping", "csv_mapping_test.json")
    seed = "test_seed"

    transform_csv_with_mapping(
        csv_in=input_file,
        csv_out=output_file,
        mapping_path=mapping_file,
        mode="restore",
        seed=seed,
    )
