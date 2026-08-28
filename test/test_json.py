import pytest

from anym import anym_json
from utils import utils

cats = [
    None,
    ["model_name", "event_classification", "station", "feeder"],
    ["event_begin", "event_end", "extract_begin", "extract_end"],
]


@pytest.mark.parametrize("categories", cats)
class Test_JSON:

    @pytest.mark.dependency(name="test_json_anym")
    def test_json_anym(self, categories):
        if categories is None:
            orig_file, anym_file, _, mapping_file = utils.get_test_files(
                "json_test_data",
                ".json",
                "JSON",
                [None],
            )
        else:
            orig_file, anym_file, _, mapping_file = utils.get_test_files(
                "json_test_data",
                ".json",
                "JSON",
                categories,
            )
        seed = "test_seed"
        anym_json.anonymize_json_file(
            input_json=orig_file,
            output_json=anym_file,
            mapping_output=mapping_file,
            seed=seed,
            categories=categories,
        )

        orig_data = anym_json.load_json_file(orig_file)
        anym_data = anym_json.load_json_file(anym_file)

        for orig_data_point, anym_data_point in zip(orig_data, anym_data):
            for (key, orig_el), anym_el in zip(
                orig_data_point.items(), anym_data_point.values()
            ):
                if categories is None:
                    assert orig_el != anym_el
                    assert anym_el.startswith("ANON_")
                elif key in categories:
                    assert orig_el != anym_el
                    assert anym_el.startswith("ANON_")
                else:
                    assert orig_el == anym_el
        assert mapping_file.exists()

    @pytest.mark.dependency(depends=["test_json_anym"])
    def test_json_restore(self, categories):
        if categories is None:
            orig_file, anym_file, restore_file, mapping_file = utils.get_test_files(
                "json_test_data",
                ".json",
                "JSON",
                [None],
            )
        else:
            orig_file, anym_file, restore_file, mapping_file = utils.get_test_files(
                "json_test_data",
                ".json",
                "JSON",
                categories,
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
        utils.delete_test_data(anym_file, restore_file, mapping_file)
