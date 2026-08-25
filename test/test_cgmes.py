import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

from anym.anym_cgmes import anonymize_cgmes
from restore.restore_cgmes import restore_cgmes
from utils import cgmes_utils


def get_test_examples(path):
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
    files = cgmes_utils.extract_bundle(path, tmp_dir)
    elems: Dict[str, List] = {
        "Text_fields": [],
        "GPS": [],
        "Times": [],
        "Line_Specs": [],
    }
    for _, path in files:
        tree = cgmes_utils.parse_xml(path)

        for el in tree.iter():
            loc = cgmes_utils.local(el.tag)
            if el.text is None:
                continue

            if loc in cgmes_utils.ANON_TEXT_LOCALS:
                elems["Text_fields"].append(el.text)

            elif loc in cgmes_utils.GPS_X_LOCALS or loc in cgmes_utils.GPS_Y_LOCALS:
                elems["GPS"].append(el.text)

            elif loc in cgmes_utils.TIME_STAMP_LOCALS:
                elems["Times"].append(el.text)

            elif loc in cgmes_utils.LINE_SPECS_LOCALS:
                elems["Line_Specs"].append(el.text)

    return elems


@pytest.mark.dependency()
def test_cgmes_anym():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "cgmes")
    input_file = Path(data_dir, "orig", "Georg_Grid 1.zip")
    output_file = Path(data_dir, "anym", "cgmes_test.zip")
    mapping_file = Path(data_dir, "mapping", "cgmes_mapping_test.json")
    seed = "test_seed"
    anonymize_cgmes(
        in_path=input_file,
        out_path=output_file,
        seed=seed,
        mapping_out_path=mapping_file,
    )

    anym_data = get_test_examples(output_file)
    orig_data = get_test_examples(input_file)

    for orig_type, anym_type in zip(orig_data.values(), anym_data.values()):
        for orig_el, anym_el in zip(orig_type, anym_type):
            assert orig_el != anym_el

    for el in anym_data["Text_fields"]:
        assert el.startswith("ANON_")

    assert mapping_file.exists()


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

    restore_data = get_test_examples(output_file)
    orig_file = Path(data_dir, "orig", "Georg_Grid 1.zip")
    orig_data = get_test_examples(orig_file)

    for orig_type, restore_type in zip(orig_data.values(), restore_data.values()):
        for orig_el, restore_el in zip(orig_type, restore_type):
            assert orig_el == pytest.approx(restore_el)
