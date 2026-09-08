"""Round-trip tests for PowerFactory project anonymization and restoration."""

import logging
import sys
from pathlib import Path
from typing import Dict

import pytest

sys.path.append(".")
from anym import anym_pf
from restore import restore_pf
from utils import pf_utils, utils

pf = pf_utils.import_powerfactory_module()
ATTRIBUTES = [
    "iStudyTime",
    "sernum",
    "constr",
    "chr_name",
    "dar_src",
    "manuf",
    "for_name",
    "foreignKey",
    "desc",
    "GPSlat",
    "GPSlon",
    "dline",
    "typ_id",
    "rline",
    "xline",
    "rline0",
    "xline0",
]

logger = logging.getLogger("Test_pf")
logging.basicConfig(level=logging.DEBUG, filename="test.log", encoding="utf-8")


def get_example_data(path: Path, app) -> Dict[str, Dict[str, str | None]]:
    """Import a PowerFactory project and collect tracked attribute values for every object."""
    project_name = path.stem

    pf_utils.delete_project_if_exists(app, project_name)
    pf_utils.import_pfd_into_current_user(app, path)
    pf_utils.activate_project(app, project_name)
    objects = pf_utils.collect_unique_objects_for_anonymization(app)
    obj_dict = restore_pf.make_obj_dict(objects)
    attr_dict = {}

    for key, obj in obj_dict.items():
        attr_dict[key] = {}
        for attr in ATTRIBUTES:
            attr_dict[key][attr] = pf_utils.get_str_attr(obj, attr)
    return attr_dict


@pytest.mark.slow
@pytest.mark.parametrize(
    "gps_flag, desc_flag, id_flag", [(True, True, True), (False, False, False)]
)
class TestPowerFactory:
    """Round-trip tests for PowerFactory project anonymization and restoration,
    requiring PowerFactory."""

    @pytest.mark.dependency(name="test_powerfactory_anym")
    def test_powerfactory_anym(self, gps_flag: bool, desc_flag: bool, id_flag: bool):
        """Check that anonymizing a PowerFactory project writes a mapping
        file (skipped if PF is absent)."""
        if pf_utils.get_pf_version() is False:
            pytest.skip("No PowerFactory installed")

        orig_file, anym_file, _, mapping_file = utils.get_test_files(
            "Texas Grid", ".pfd", "PowerFactory", [gps_flag, desc_flag, id_flag]
        )
        seed = "test_seed"

        anym_pf.run_powerfactory_import_export(
            in_path=orig_file,
            out_path=anym_file,
            random_seed=seed,
            mapping_out_path=mapping_file,
            desc=desc_flag,
            gps=gps_flag,
            remap_ids=id_flag,
        )

        assert mapping_file.exists()

    @pytest.mark.dependency(depends=["test_powerfactory_anym"])
    def test_powerfactory_restore(self, gps_flag: bool, desc_flag: bool, id_flag: bool):
        """Check that restoring an anonymized PowerFactory project recovers
        original attribute values."""
        if pf_utils.get_pf_version() is False:
            pytest.skip("No PowerFactory installed")

        orig_file, anym_file, restore_file, mapping_file = utils.get_test_files(
            "Texas Grid", ".pfd", "PowerFactory", [gps_flag, desc_flag, id_flag]
        )

        restore_pf.run_powerfactory_restore(
            in_path=anym_file,
            out_path=restore_file,
            mapping_path=mapping_file,
        )

        app = pf.GetApplication()
        orig_data = get_example_data(orig_file, app)
        restore_data = get_example_data(restore_file, app)

        for obj_key, obj_data in orig_data.items():
            for attr_name, attr_data in obj_data.items():
                attr_restored = restore_data[obj_key][attr_name]
                if attr_data == "":
                    continue
                try:
                    assert attr_data == attr_restored
                except AssertionError:
                    try:
                        assert attr_data == attr_restored.replace(";", " ")
                    except AssertionError:
                        assert attr_data == attr_restored + " "
        utils.delete_test_data([anym_file, restore_file, mapping_file])


@pytest.mark.parametrize(
    "path",
    [
        "Nine-bus System",
        "IEEE 13 Node Feeder",
        "14 Bus System(1)",
        "LV Distribution Network",
        "39 Bus New England System",
    ],
)
def test_powerfactory_load_flow_accuracy(path):
    """Check that load_flow_results correctly loads and parses a flow results graphic file."""
    # SetCluster.CalcCluster
    # ComLdf Execute
    if pf_utils.get_pf_version() is False:
        pytest.skip("No PowerFactory installed")
    orig_path, anym_path, _, mapping_path = utils.get_test_files(
        path, ".pfd", "PowerFactory", []
    )

    seed = "test_seed"

    anym_pf.run_powerfactory_import_export(
        in_path=orig_path,
        out_path=anym_path,
        random_seed=seed,
        mapping_out_path=mapping_path,
        desc=False,
        gps=False,
        remap_ids=False,
    )
    (
        _,
        anon_rev,
        _,
        _,
        _,
        _,
        prefix,
    ) = utils.get_mappings(mapping_path)

    app = pf.GetApplication()
    orig_ldf_results = get_load_flow_results(app, orig_path, anon_rev, prefix)
    anym_ldf_results = get_load_flow_results(app, anym_path, anon_rev, prefix)

    load_flow_asserts(
        orig_data=orig_ldf_results["generators"],
        anym_data=anym_ldf_results["generators"],
    )
    load_flow_asserts(
        orig_data=orig_ldf_results["lines"], anym_data=anym_ldf_results["lines"]
    )
    load_flow_asserts(
        orig_data=orig_ldf_results["busses"], anym_data=anym_ldf_results["busses"]
    )
    utils.delete_test_data([anym_path, mapping_path])


def load_flow_asserts(orig_data, anym_data):
    for name, elems in orig_data.items():
        try:
            for elem_key, elem_value in elems.items():
                assert elem_value == pytest.approx(anym_data[name][elem_key])
        except AttributeError as e:
            if elems == "Unknown":
                pass
            else:
                raise AttributeError from e


def get_load_flow_results(app, path, anon_rev, prefix):
    """Main Parts are taken from https://thesmartinsights.com/run-digsilent-powerfactory-via-the-python-api-jump-start-to-your-powerfactory-automatization/
    and adapted for this use case"""
    project_name = path.stem

    pf_utils.delete_project_if_exists(app, project_name)
    pf_utils.import_pfd_into_current_user(app, path)
    pf_utils.activate_project(app, project_name)

    # get load flow object and execute
    oLoadflow = app.GetFromStudyCase("ComLdf")  # get load flow object
    oLoadflow.Execute()  # execute load flow
    load_flow_results = {"generators": [], "lines": [], "busses": []}

    # get the generators and their active/reactive power and loading
    Generators = app.GetCalcRelevantObjects("*.ElmSym")
    gen_dict: Dict[str, float] = {}

    for gen in Generators:  # loop through list
        name = getattr(gen, "loc_name")  # get name of the generator

        if name.startswith(prefix):
            orig_name = anon_rev[name]
        else:
            orig_name = name

        try:
            actPower = getattr(gen, "c:p")  # get active power
            reacPower = getattr(gen, "c:q")  # get reactive power
            genloading = getattr(gen, "c:loading")  # get loading
            gen_entry = {"P": actPower, "Q": reacPower, "loading": genloading}

        except AttributeError:
            gen_entry = "Unknown"

        gen_dict[orig_name] = gen_entry

    load_flow_results["generators"] = gen_dict

    # get the lines and print their loading
    Lines = app.GetCalcRelevantObjects("*.ElmLne")
    line_dict = {}
    for line in Lines:  # loop through list
        name = getattr(line, "loc_name")  # get name of the line

        if name.startswith(prefix):
            orig_name = anon_rev[name]
        else:
            orig_name = name

        try:
            value = getattr(line, "c:loading")  # get value for the loading
            line_entry = {"loading": value}

        except AttributeError:
            line_entry = "Unknown"

        line_dict[orig_name] = line_entry
    load_flow_results["lines"] = line_dict

    # get the buses and print their voltage
    Buses = app.GetCalcRelevantObjects("*.ElmTerm")
    bus_dict = {}
    for bus in Buses:  # loop through list

        if name.startswith(prefix):
            orig_name = anon_rev[name]
        else:
            orig_name = name

        name = getattr(bus, "loc_name")  # get name of the bus

        try:
            amp = getattr(bus, "m:u1")  # get voltage magnitude
            phase = getattr(bus, "m:phiu")  # get voltage angle
            bus_entry = {"u": amp, "deg": phase}

        except AttributeError:
            bus_entry = "Unknown"
            bus_dict[orig_name] = bus_entry

    load_flow_results["busses"] = bus_dict
    return load_flow_results


# def print_snapshot_of_grid(
#     strng_graphic_name, obj_substat=None, state_indx: int = 0, scaling_fac=1
# ):
#     """
#     Takes a snapshot of the current switching state and exports it as pdf file.

#     Input:
#     - strng_graphic_name: string name of the graphic that is to be exported including the graphic ending ".IntGrfnet" (e.g. "D2.IntGrfnet")
#     - obj_substat: pf object of the substation, if not given, the entire diagram is exported.
#     - scaling_fac: factor to adjust scaling of the pdf print (for large zones a scaling factor between >1-2 is appropriate)

#     """
#     # get active project
#     o_active_project = self.app.GetActiveProject()
#     o_active_project.GetContents()
#     # get networkmodel folder
#     network_model = o_active_project.GetContents("Network Model.IntPrjfolder")
#     # get write command for saving diagrams as e.g. pdf
#     comWr = self.app.GetFromStudyCase("ComWr")
#     # get the correct diagram
#     diagrams = network_model[0].GetContents("Diagrams.IntPrjfolder")
#     diagrams_contents_D2 = diagrams[0].GetContents(strng_graphic_name)
#     diagrams_contents_D2[0].Show()
#     # define save settings
#     comWr = self.app.GetFromStudyCase("ComWr")
#     comWr.SetAttribute("iopt_rd", "pdf")
#     comWr.SetAttribute("iopt_savas", 0)
#     # get scaling factor
#     scaling_fac = self._determine_scaling_for_pdf(self.substats_zone)
#     # if no substation object is given, set initial substation as default
#     if obj_substat == None:
#         obj_substat = self.inital_substat
#     # if given, define selection of the graphic according to given substation
#     if obj_substat != None:
#         str_name_site = obj_substat.GetParent().loc_name
#         # get graphical object of substation object
#         for i in obj_substat.GetParent().GetReferences():
#             if i.GetParent().loc_name == "D2":
#                 graphic_obj = i
#         # get x and y coordinates of site element
#         x_coordinate = graphic_obj.rCenterX
#         y_coordinate = graphic_obj.rCenterY
#         # define subregion to export selection of graphical diagram
#         comWr.exportSubregion = 1
#         # convertion of the objects coordinates to coordinates for the grid
#         # Note: for some reason scaling with *10000 is required to set the comWr attributes correctly
#         comWr.regionTop = y_coordinate - scaling_fac * 1000
#         comWr.regionBottom = y_coordinate + scaling_fac * 1000
#         comWr.regionRight = x_coordinate + scaling_fac * 1000
#         comWr.regionLeft = x_coordinate - scaling_fac * 1000

#     # define path and execute pdf export
#     comWr.SetAttribute(
#         "f",
#         f"Auswertung\zone_{str_name_site}\graphics\graphic_{str_name_site}_state_index{state_indx}.pdf",
#     )
#     comWr.Execute()


if __name__ == "__main__":
    project_dir = Path(__file__).parent.parent.resolve()
    test_dir = Path(project_dir, "test")
    data_dir = Path(test_dir, "test_data", "PowerFactory")
    the_file = Path(data_dir, "orig", "Nine-bus System.pfd")
    test_powerfactory_load_flow_accuracy("Nine-bus System")
