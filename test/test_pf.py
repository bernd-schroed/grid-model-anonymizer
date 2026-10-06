"""Round-trip tests for PowerFactory project anonymization and restoration."""

import itertools
import logging
import math
import sys
from pathlib import Path
from typing import Dict

import matplotlib.pyplot as plt
import pytest
from matplotlib.patches import Patch

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
    project_name = "Texas Grid"

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
        anonymizer = utils.SeededNameAnonymizer(seed=seed)
        anym_pf.run_powerfactory_import_export(
            in_path=orig_file,
            out_path=anym_file,
            random_seed=seed,
            mapping_out_path=mapping_file,
            desc=desc_flag,
            gps=gps_flag,
            remap_ids=id_flag,
            anonymizer=anonymizer,
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
            project_name="Texas Grid",
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
                    assert attr_data == attr_restored or attr_restored == "Deleted"
                except AssertionError:
                    try:
                        assert attr_data == attr_restored.replace(";", " ")
                    except AssertionError:
                        assert attr_data == attr_restored + " "
        utils.delete_test_data([anym_file, restore_file, mapping_file])


@pytest.mark.parametrize(
    "path, alt_factor_percent",
    list(
        itertools.product(
            [
                "Nine-bus System",
                "14 Bus System(1)",
                "39 Bus New England System",
            ],
            [-1, 0, 3, 5, 10],
        ),
    ),
)
def test_powerfactory_load_flow_accuracy(path, alt_factor_percent):
    """Check that load_flow_results correctly loads and parses a flow results graphic file."""
    if pf_utils.get_pf_version() is False:
        pytest.skip("No PowerFactory installed")
    orig_path, anym_path, _, mapping_path = utils.get_test_files(
        path, ".pfd", "PowerFactory", []
    )

    seed = "test_seed"
    anonymizer = utils.SeededNameAnonymizer(
        seed=seed, alteration_factor=alt_factor_percent
    )
    anym_pf.run_powerfactory_import_export(
        in_path=orig_path,
        out_path=anym_path,
        random_seed=seed,
        mapping_out_path=mapping_path,
        desc=False,
        gps=False,
        remap_ids=False,
        anonymizer=anonymizer,
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
    orig_ldf_results = pf_utils.get_load_flow_results(app, orig_path, anon_rev, prefix)
    anym_ldf_results = pf_utils.get_load_flow_results(app, anym_path, anon_rev, prefix)
    load_flow_asserts(
        orig_ldf_results=orig_ldf_results,
        anym_ldf_results=anym_ldf_results,
        alt_factor=alt_factor_percent,
        project_name=orig_path.stem,
    )
    utils.delete_test_data([anym_path, mapping_path])


def load_flow_asserts(
    orig_ldf_results, anym_ldf_results, alt_factor, project_name
) -> float:
    difference_list = []
    for type_key, type_entry in orig_ldf_results.items():
        for elem_key, elem_entry in type_entry.items():

            for value_key, orig_value_entry in elem_entry.items():
                anym_value_entry = anym_ldf_results[type_key][elem_key][value_key]
                try:
                    rel_error = (orig_value_entry - anym_value_entry) / orig_value_entry
                except ZeroDivisionError:
                    rel_error = orig_value_entry - anym_value_entry
                difference_list.append(rel_error)

    square_error = [x**2 for x in difference_list]
    mean_square_error = sum(square_error) / len(square_error)

    rmse = math.sqrt(mean_square_error)
    max_error = math.sqrt(max(square_error))
    with open("Load_flow_test.txt", mode="a", encoding="utf-8") as f:
        f.write(f"Current Network: {project_name} \n")

        f.write(
            f"The maximum deviation of the load flow results is {max_error*100:.2f}% in one of the elements, the Alteration Factor is at {alt_factor:.0f}%.\n",
        )
        if rmse >= 1 / 100:
            f.write(
                f"The averaged error for load flow analysis is larger than 1% with {rmse *100:.2f}%! Use a smaller alteration factor to reduce the error.\n",
            )
        else:
            f.write(
                f"The averaged error for a load flow analysis is at {rmse*100:.2f}%!\n",
            )
        f.write("\n")


def get_load_flow_diff_plots():

    if pf_utils.get_pf_version() is False:
        pytest.skip("No PowerFactory installed")

    seed = "test_seed"
    paths = [
        "Nine-bus System",
        "14 Bus System(1)",
        "39 Bus New England System",
    ]
    alt_factors = [0, 1, 3, 5, 10]

    load_flow_results = {}

    for path in paths:
        load_flow_results[path] = {}

        orig_path, anym_path, _, mapping_path = utils.get_test_files(
            path, ".pfd", "PowerFactory", []
        )
        app = pf.GetApplication()
        orig_ldf_results = pf_utils.get_load_flow_results(
            app, orig_path, anon_rev=None, prefix="Anon_"
        )
        load_flow_results[path]["orig"] = orig_ldf_results
        for alt_factor in alt_factors:

            anonymizer = utils.SeededNameAnonymizer(
                seed=seed, alteration_factor=alt_factor
            )

            anym_pf.run_powerfactory_import_export(
                in_path=orig_path,
                out_path=anym_path,
                random_seed=seed,
                mapping_out_path=mapping_path,
                desc=False,
                gps=False,
                remap_ids=False,
                anonymizer=anonymizer,
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

            anym_ldf_results = pf_utils.get_load_flow_results(
                app, anym_path, anon_rev, prefix
            )

            load_flow_results[path][f"Factor: {alt_factor}"] = anym_ldf_results
            utils.delete_test_data([anym_path, mapping_path])

    # ------------------------------ Courtesy of Claude -----------------------
    # data type -> (title, y-label, category in JSON, value key)
    TYPES = {
        "generator_loading": (
            "Generator Loading",
            "Δ Loading [%]",
            "generators",
            "loading",
        ),
        "line_loading": ("Line Loading", "Δ Loading [%]", "lines", "loading"),
        "bus_voltage": ("Bus Voltage", "Δ Voltage [p.u.]", "busses", "u"),
        "bus_angle": ("Bus Angle", "Δ Angle [°]", "busses", "deg"),
    }

    # Scale factors to per unit (used only for the "all data" plot):
    # loading [%] -> p.u. (/100), voltage is already p.u., angle [°] -> rad
    PU_SCALE = {
        "generators": {"loading": 1 / 100},
        "lines": {"loading": 1 / 100},
        "busses": {"u": 1.0, "deg": math.pi / 180},
    }

    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]

    def deviations(project, factor, category, key):
        """Deviation (factor - orig) per element of a project."""
        orig = load_flow_results[project]["orig"][category]
        fac = load_flow_results[project][factor][category]
        return [fac[name][key] - orig[name][key] for name in orig]

    def plot(types, title, ylabel, filename, to_pu=False):
        fig, ax = plt.subplots(figsize=(12, 5))
        pos = 0
        group_centers, group_names = [], []
        factor_names = []
        for project, runs in load_flow_results.items():
            factors = [k for k in runs if k != "orig"]
            start = pos
            for i, factor in enumerate(factors):
                vals = []
                for category, key in types:
                    scale = PU_SCALE[category][key] if to_pu else 1.0
                    vals += [
                        v * scale for v in deviations(project, factor, category, key)
                    ]
                bp = ax.boxplot(vals, positions=[pos], widths=0.8, patch_artist=True)
                bp["boxes"][0].set_facecolor(colors[i % len(colors)])
                for m in bp["medians"]:
                    m.set_color("black")
                if factor not in factor_names:
                    factor_names.append(factor)
                pos += 1
            group_centers.append((start + pos - 1) / 2)
            group_names.append(project)
            pos += 1  # gap between project groups

        ax.set_xticks(group_centers)
        ax.set_xticklabels(group_names)
        ax.set_xlabel("Original project")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.axhline(0, color="gray", linewidth=0.8, linestyle="--")
        ax.grid(axis="y", alpha=0.3)

        # light legend: outside the plot, no frame, small font
        ax.legend(
            handles=[
                Patch(
                    facecolor=colors[i % len(colors)],
                    edgecolor="black",
                    linewidth=0.8,
                    label=f.replace("Factor: ", ""),
                )
                for i, f in enumerate(factor_names)
            ],
            title="Factor",
            loc="upper left",
            bbox_to_anchor=(1.01, 1),
            frameon=False,
            fontsize=9,
            title_fontsize=9,
            handlelength=1.0,
            handleheight=1.0,
        )
        fig.tight_layout()
        fig.savefig(filename, dpi=150)
        plt.close(fig)

    # One image per data type
    for name, (title, ylabel, category, key) in TYPES.items():
        plot(
            [(category, key)],
            f"Deviation from original: {title}",
            ylabel,
            f"{name}.png",
        )

    # One image over all data
    plot(
        [(c, k) for _, _, c, k in TYPES.values()],
        "Deviation from original: all data types",
        "Δ [p.u.]",
        "all_data.png",
        to_pu=True,
    )


# ------------------------------ Courtesy of Claude -----------------------


def get_plot_data(load_flow_results, object_type="lines", data_type="loading"):
    object_data = {"orig": []}
    objects = tuple(load_flow_results[0]["orig"][object_type].keys())
    object_data["orig"] = [100] * len(objects)
    for load_flow_result in load_flow_results:
        factor = load_flow_result["factor"]
        anym_object = load_flow_result["anym"][object_type]
        orig_object = load_flow_result["orig"][object_type]
        object_data.update({f"Factor: {factor}": []})
        for obj in objects:
            if data_type == "deg":
                object_data["orig"] = [0] * len(objects)
                object_alteration = (
                    orig_object[obj][data_type] - anym_object[obj][data_type]
                )
            else:
                object_alteration = (
                    anym_object[obj][data_type] / orig_object[obj][data_type] * 100
                )
            object_data[f"Factor: {factor}"].append(object_alteration)
        object_data[f"Factor: {factor}"] = tuple(object_data[f"Factor: {factor}"])
    return object_data, objects


def plot_specs(fig, ax, ymin, ymax, default=100):
    ax.set_ylim(ymin - (default - ymin) / 4, ymax + (ymax - default) / 4)
    ax.grid()
    ax.legend()
    ax.tick_params("x", rotation=45, rotation_mode="xtick")
    fig.set_size_inches(17.5, 10.5)


def line_plots(load_flow_results, path):

    line_data, lines = get_plot_data(
        load_flow_results, object_type="lines", data_type="loading"
    )
    ymax = max([max(x) for x in line_data.values()])
    ymin = min([min(x) for x in line_data.values()])

    line_fig, line_ax = plt.subplots()
    line_ax.grouped_bar(line_data, tick_labels=lines)
    plot_specs(line_fig, line_ax, ymin, ymax)
    line_ax.set_title("Difference in Line Loading for Different Alteration Factors")
    line_ax.set_ylabel("Loading p.u. [%]")

    # plt.show()
    line_fig.savefig(f"{path}_line_loading_plot.png", dpi=300)


def buss_plots(load_flow_results, path):
    buss_voltage_data, busses = get_plot_data(
        load_flow_results, object_type="busses", data_type="u"
    )
    buss_degree_data, _ = get_plot_data(
        load_flow_results, object_type="busses", data_type="deg"
    )
    ymax = max([max(x) for x in buss_voltage_data.values()])
    ymin = min([min(x) for x in buss_voltage_data.values()])

    bus_fig, bus_ax = plt.subplots()
    bus_ax.grouped_bar(buss_voltage_data, tick_labels=busses)

    bus_ax.set_title("Difference in Bus Voltage for different alteration factors")
    bus_ax.set_ylabel("Voltage p.u. [%]")
    plot_specs(bus_fig, bus_ax, ymin, ymax)
    # plt.show()
    bus_fig.savefig(f"{path}_bus_voltage_plot.png", dpi=300)

    ymax = max([max(x) for x in buss_degree_data.values()])
    ymin = min([min(x) for x in buss_degree_data.values()])

    bus_deg_fig, bus_deg_ax = plt.subplots()
    bus_deg_ax.grouped_bar(buss_degree_data, tick_labels=busses)

    bus_deg_ax.set_title(
        "Difference in Bus Voltage Angles for different alteration factors"
    )
    bus_deg_ax.set_ylabel("Angle Diffeence [°]")
    plot_specs(bus_deg_fig, bus_deg_ax, ymin, ymax, default=0)
    # plt.show()
    bus_deg_fig.savefig(f"{path}_bus_voltage_angle_plot.png", dpi=300)


def generator_plots(load_flow_results, path):
    generator_data, generators = get_plot_data(
        load_flow_results, object_type="generators", data_type="loading"
    )
    ymax = max([max(x) for x in generator_data.values()])
    ymin = min([min(x) for x in generator_data.values()])

    gen_fig, gen_ax = plt.subplots()
    gen_ax.grouped_bar(generator_data, tick_labels=generators)

    gen_ax.set_title("Difference in Generator Loading for different alteration factors")
    gen_ax.set_ylabel("Loading p.u. [%]")
    plot_specs(gen_fig, gen_ax, ymin, ymax)
    gen_fig.savefig(f"{path}_generator_loading_plot.png", dpi=300)
    # plt.show()


if __name__ == "__main__":
    project_dir = Path(__file__).parent.parent.resolve()
    test_dir = Path(project_dir, "test")
    data_dir = Path(test_dir, "test_data", "PowerFactory")
    the_file = Path(data_dir, "orig", "39 Bus New England System.pfd")
    get_load_flow_diff_plots()
