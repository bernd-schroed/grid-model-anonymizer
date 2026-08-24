from pathlib import Path

import pytest

from anym.anym_cgmes import anonymize_cgmes
from restore.restore_cgmes import restore_cgmes


@pytest.mark.dependency()
def test_cgmes_anym():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "cgmes")
    input_file = Path(data_dir, "orig", "9 bus.zip")
    output_file = Path(data_dir, "anym", "cgmes_test.zip")
    mapping_file = Path(data_dir, "mapping", "cgmes_mapping_test.json")
    seed = "test_seed"
    anonymize_cgmes(
        in_path=input_file,
        out_path=output_file,
        seed=seed,
        mapping_out_path=mapping_file,
    )


@pytest.mark.dependency(depends=["test_cgmes_anym"])
def test_cgmes_restore():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "cgmes")
    input_file = Path(data_dir, "anym", "cgmes_test.zip")
    output_file = Path(data_dir, "restore", "cgmes_test.zip")
    mapping_file = Path(data_dir, "mapping", "cgmes_mapping_test.json")

    restore_cgmes(
        in_path=input_file,
        out_path=output_file,
        mapping_path=mapping_file,
    )
