from pathlib import Path

import pytest

from utils import cgmes_utils


@pytest.mark.parametrize("suffix", [".zip", ".xml", ""])
def test_extract_bundle(suffix):
    test_path = (
        Path(__file__).parent.parent.resolve()
        / "test_data"
        / "general"
        / str("xml_test" + suffix)
    )

    xml_files = cgmes_utils.extract_bundle(test_path, Path("tmp_dir"))
    tmp_path = Path(__file__).parent.parent.resolve() / "tmp_dir"
    assert xml_files
    for file in tmp_path:
        file.unlink()
    tmp_path.rmdir()


def test_extract_empty_bundle():
    test_path = (
        Path(__file__).parent.parent.resolve()
        / "test_data"
        / "general"
        / "empty_folder"
    )

    xml_files = cgmes_utils.extract_bundle(test_path, Path("tmp_dir"))
    tmp_path = Path(__file__).parent.parent.resolve() / "tmp_dir"
    assert xml_files
    for file in tmp_path:
        file.unlink()
    tmp_path.rmdir()


def test_pack_bundle_folder():
    test_path = (
        Path(__file__).parent.parent.resolve() / "test_data" / "general" / "xml_test"
    )
    output_path = (
        Path(__file__).parent.parent.resolve()
        / "test_data"
        / "general"
        / "empty_folder"
    )
    xml_files = [(file.name, file) for file in test_path.iterdir()]

    cgmes_utils.pack_bundle(xml_files, output_path)
    for el in output_path.iterdir():
        if el.is_file() and el.suffix == ".xml":
            assert (output_path / el.name).exists()
            el.unlink()
