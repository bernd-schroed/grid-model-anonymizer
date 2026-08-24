from pathlib import Path

import pytest

from anym import anym_json


@pytest.mark.dependency()
def test_json_anym():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "JSON")
    input_file = Path(data_dir, "orig", "json_test_data.json")
    output_file = Path(data_dir, "anym", "json_test_data.json")
    mapping_file = Path(data_dir, "mapping", "json_mapping_test.json")
    seed = "test_seed"
    anym_json.anonymize_json_file(
        input_json=input_file,
        output_json=output_file,
        mapping_output=mapping_file,
        seed=seed,
    )

    orig_data = anym_json.load_json_file(input_file)
    anym_data = anym_json.load_json_file(output_file)

    for orig_data_point, anym_data_point in zip(orig_data, anym_data):
        for orig_el, anym_el in zip(orig_data_point.values(), anym_data_point.values()):
            assert orig_el != anym_el
            assert anym_el.startswith("ANON_")

    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_json_anym"])
def test_json_restore():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "JSON")
    input_file = Path(data_dir, "anym", "json_test_data.json")
    output_file = Path(data_dir, "restore", "json_test_data.json")
    mapping_file = Path(data_dir, "mapping", "json_mapping_test.json")

    anym_json.restore_json_anonymization(
        input_json=input_file,
        output_json=output_file,
        mapping_input=mapping_file,
    )

    orig_file = Path(data_dir, "orig", "json_test_data.json")
    orig_data = anym_json.load_json_file(orig_file)
    restore_data = anym_json.load_json_file(output_file)

    for orig_data_point, restore_data_point in zip(orig_data, restore_data):
        for orig_el, restore_el in zip(
            orig_data_point.values(), restore_data_point.values()
        ):
            assert orig_el == restore_el
