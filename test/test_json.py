import pytest

from anym import anym_json
from utils import utils


@pytest.mark.dependency()
def test_json_anym():
    orig_file, anym_file, _, mapping_file = utils.get_test_files(
        "json_test_data", ".json", "JSON"
    )
    seed = "test_seed"
    anym_json.anonymize_json_file(
        input_json=orig_file,
        output_json=anym_file,
        mapping_output=mapping_file,
        seed=seed,
    )

    orig_data = anym_json.load_json_file(orig_file)
    anym_data = anym_json.load_json_file(anym_file)

    for orig_data_point, anym_data_point in zip(orig_data, anym_data):
        for orig_el, anym_el in zip(orig_data_point.values(), anym_data_point.values()):
            assert orig_el != anym_el
            assert anym_el.startswith("ANON_")

    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_json_anym"])
def test_json_restore():
    orig_file, anym_file, restore_file, mapping_file = utils.get_test_files(
        "json_test_data", ".json", "JSON"
    )

    anym_json.restore_json_anonymization(
        input_json=anym_file,
        output_json=restore_file,
        mapping_input=mapping_file,
    )

    orig_data = anym_json.load_json_file(orig_file)
    restore_data = anym_json.load_json_file(restore_file)

    for orig_data_point, restore_data_point in zip(orig_data, restore_data):
        for orig_el, restore_el in zip(
            orig_data_point.values(), restore_data_point.values()
        ):
            assert orig_el == restore_el
