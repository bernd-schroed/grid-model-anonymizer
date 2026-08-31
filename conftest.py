# conftest.py
import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--skip-slow",
        action="store_true",
        default=False,
        help="Skip Tests, that are marked with @pytest.mark.slow",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks test as slow")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--skip-slow"):
        return
    skip_marker = pytest.mark.skip(reason="skip slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_marker)
