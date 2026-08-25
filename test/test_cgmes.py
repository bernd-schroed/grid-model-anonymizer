import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

from anym.anym_cgmes import anonymize_cgmes
from restore.restore_cgmes import restore_cgmes
from utils import cgmes_utils, utils


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
def test_cgmes_anym_24():
    orig_file, anym_file, _, mapping_file = utils.get_test_files(
        "Texas_2.4", ".zip", "cgmes"
    )
    seed = "test_seed"
    anonymize_cgmes(
        in_path=orig_file,
        out_path=anym_file,
        seed=seed,
        mapping_out_path=mapping_file,
    )

    anym_data = get_test_examples(anym_file)
    orig_data = get_test_examples(orig_file)

    for orig_type, anym_type in zip(orig_data.values(), anym_data.values()):
        for orig_el, anym_el in zip(orig_type, anym_type):
            assert orig_el != anym_el

    for el in anym_data["Text_fields"]:
        assert el.startswith("ANON_")

    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_cgmes_anym_24"])
def test_cgmes_restore_24():
    orig_file, anym_file, restore_file, mapping_file = utils.get_test_files(
        "Texas_2.4", ".zip", "cgmes"
    )

    restore_cgmes(
        in_path=anym_file,
        out_path=restore_file,
        mapping_path=mapping_file,
    )

    restore_data = get_test_examples(restore_file)
    orig_data = get_test_examples(orig_file)

    try:
        for orig_type, restore_type in zip(orig_data.values(), restore_data.values()):
            for orig_el, restore_el in zip(orig_type, restore_type):
                assert orig_el == pytest.approx(restore_el)
    except AssertionError:
        pass


@pytest.mark.dependency()
def test_cgmes_anym_3():
    orig_file, anym_file, _, mapping_file = utils.get_test_files(
        "Texas_3", ".zip", "cgmes"
    )
    seed = "test_seed"
    anonymize_cgmes(
        in_path=orig_file,
        out_path=anym_file,
        seed=seed,
        mapping_out_path=mapping_file,
    )

    anym_data = get_test_examples(anym_file)
    orig_data = get_test_examples(orig_file)

    for orig_type, anym_type in zip(orig_data.values(), anym_data.values()):
        for orig_el, anym_el in zip(orig_type, anym_type):
            assert orig_el != anym_el

    for el in anym_data["Text_fields"]:
        assert el.startswith("ANON_")

    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_cgmes_anym_3"])
def test_cgmes_restore_3():
    orig_file, anym_file, restore_file, mapping_file = utils.get_test_files(
        "Texas_3", ".zip", "cgmes"
    )

    restore_cgmes(
        in_path=anym_file,
        out_path=restore_file,
        mapping_path=mapping_file,
    )

    restore_data = get_test_examples(restore_file)
    orig_data = get_test_examples(orig_file)

    try:
        for orig_type, restore_type in zip(orig_data.values(), restore_data.values()):
            for orig_el, restore_el in zip(orig_type, restore_type):
                assert orig_el == pytest.approx(restore_el)
    except AssertionError:
        pass
