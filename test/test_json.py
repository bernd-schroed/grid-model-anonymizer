from pathlib import Path

import pytest

from anym.anym_json import anonymize_json_file, restore_json_anonymization


@pytest.mark.dependency()
def test_json_anym():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "JSON")
    input_file = Path(data_dir, "orig", "json_test_data.json")
    output_file = Path(data_dir, "anym", "json_test_data.json")
    mapping_file = Path(data_dir, "mapping", "json_mapping_test.json")
    seed = "test_seed"
    anonymize_json_file(
        input_json=input_file,
        output_json=output_file,
        mapping_output=mapping_file,
        seed=seed,
    )

    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_json_anym"])
def test_json_restore():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "JSON")
    input_file = Path(data_dir, "anym", "json_test_data.json")
    output_file = Path(data_dir, "restore", "json_test_data.json")
    mapping_file = Path(data_dir, "mapping", "json_mapping_test.json")

    restore_json_anonymization(
        input_json=input_file,
        output_json=output_file,
        mapping_input=mapping_file,
    )
