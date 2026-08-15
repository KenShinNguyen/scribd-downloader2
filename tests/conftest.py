"""Load the hyphenated entry script as an importable module for tests."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scribd-downloader.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("scribd_downloader", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def downloader():
    return _load_module()
