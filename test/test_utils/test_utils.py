import pytest

from utils import utils


def test_seeded_name_anonyzer() -> None:
    anonymizer = utils.SeededNameAnonymizer("test_seed", "Anon_")
    assert anonymizer.time_adding == 956552995
    assert anonymizer.seed == "test_seed"
    assert anonymizer.prefix == "Anon_"
    assert anonymizer.length == 10


def test_get_hash():
    anonymizer = utils.SeededNameAnonymizer("test_seed", "Anon_")
    hash_str = anonymizer.get_hash("Test_text", 10)
    assert hash_str == "5830EE3D51"


def test_translate():
    anonymizer = utils.SeededNameAnonymizer("test_seed", "Anon_")
    translation = anonymizer.translate("Text")
    assert translation == "Anon_0F2B146C3A"


@pytest.mark.parametrize(
    "time_add,new_time",
    [
        (40000000, 70000000),
        (-10000, 29990000),
        (-40000000, 70000000),
        (2**32, 2**32 - 30000000),
    ],
)
def test_add_time(time_add, new_time):
    old_time = 30000000
    anonymizer = utils.SeededNameAnonymizer("test_seed", "Anon_")

    anonymizer.time_adding = time_add
    new_time = anonymizer.add_time(old_time)
    assert new_time == new_time


def test_generate_seeded_uuid():
    seed = "seed"
    old_id = "Some beautiful ID"
    expected_id = "_97c7089e-f667-7594-554f-73a15dbb9787"
    new_id = utils.generate_seeded_uuid(old_id, seed)
    assert new_id == expected_id


def test_load_mapping_json():
    pass


def test_obj_unit_from_name():
    pass


def test_meters_to_deg_lat():
    meters = 5_000_000
    assert utils.meters_to_deg_lat(meters) == pytest.approx(44.91555875)


def test_meters_to_deg_lon():
    meters = 5_000_000
    lat_deg = 30
    deg_lat_30 = 51.864019869711264
    assert utils.meters_to_deg_lon(meters, lat_deg) == pytest.approx(deg_lat_30)

    lat_deg = 89
    deg_lat_89 = 449.1555874955084
    assert utils.meters_to_deg_lon(meters, lat_deg) == pytest.approx(deg_lat_89)


def test_scale_back_to_valid_geo():
    pass


def test_seed_unit():
    pass


@pytest.mark.parametrize(
    "inpt_obj,output_bool",
    [
        ("Testfolder\\Subfolder", False),
        ("Testfolder\\testfile.test", True),
    ],
)
def test_has_suffix(inpt_obj, output_bool):
    assert utils.has_suffix(inpt_obj) is output_bool

    with pytest.raises(AttributeError):
        utils.has_suffix("This is not a file")
