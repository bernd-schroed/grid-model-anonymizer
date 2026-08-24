from pathlib import Path

import pytest

from anym.anym_pf import run_powerfactory_import_export
from restore.restore_pf import run_powerfactory_restore


@pytest.mark.dependency()
def test_powerfactory_anym():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "PowerFactory")
    input_file = Path(data_dir, "orig", "Texas Grid.pfd")
    output_file = Path(data_dir, "anym", "Texas Grid.pfd")
    mapping_file = Path(data_dir, "mapping", "pfd_mapping_test.json")
    seed = "test_seed"

    run_powerfactory_import_export(
        in_path=input_file,
        out_path=output_file,
        random_seed=seed,
        mapping_out_path=mapping_file,
        desc=False,
        gps=False,
    )


@pytest.mark.dependency(depends=["test_powerfactory_anym"])
def test_powerfactory_restore():
    test_dir = Path(__file__).parent.resolve()
    data_dir = Path(test_dir, "test_data", "PowerFactory")
    input_file = Path(data_dir, "anym", "Texas Grid.pfd")
    output_file = Path(data_dir, "restore", "Texas Grid.pfd")
    mapping_file = Path(data_dir, "mapping", "pfd_mapping_test.json")

    run_powerfactory_restore(
        in_path=input_file,
        out_path=output_file,
        mapping_path=mapping_file,
    )
