from pathlib import Path

import pytest

from anym.anym_pf import run_powerfactory_import_export
from restore.restore_pf import run_powerfactory_restore
from utils import pf_utils, utils


@pytest.mark.dependency()
def test_powerfactory_anym():
    if pf_utils.get_pf_version() is False:
        pytest.skip("No PowerFactory installed")

    orig_file, anym_file, _, mapping_file = utils.get_test_files(
        "Texas Grid", ".pfd", "PowerFactory"
    )
    seed = "test_seed"

    run_powerfactory_import_export(
        in_path=orig_file,
        out_path=anym_file,
        random_seed=seed,
        mapping_out_path=mapping_file,
        desc=False,
        gps=False,
    )

    assert mapping_file.exists()


@pytest.mark.dependency(depends=["test_powerfactory_anym"])
def test_powerfactory_restore():
    if pf_utils.get_pf_version() is False:
        pytest.skip("No PowerFactory installed")

    orig_file, anym_file, restore_file, mapping_file = utils.get_test_files(
        "Texas Grid", ".pfd", "PowerFactory"
    )

    run_powerfactory_restore(
        in_path=anym_file,
        out_path=restore_file,
        mapping_path=mapping_file,
    )
