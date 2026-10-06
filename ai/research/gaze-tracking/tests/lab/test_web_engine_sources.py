"""The browser port's generated inputs must match the Python engine.

``web/`` holds a TypeScript port of the head-pose engine.  It reads its
thresholds from ``web/src/engine/defaults.ts`` and is tested against
``web/test/fixtures/parity.json`` -- both generated from this package.  These
tests fail when either is stale, so a change here cannot silently leave the
browser behind (regenerate with ``npm run fixtures`` in ``web/``, then run the
web tests).  The generators live in ``tools/``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
WEB = PROJECT / "web"


def _load(name: str):
    path = PROJECT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tools_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(not WEB.exists(), reason="web/ port not present")


def test_the_web_engine_defaults_are_generated_from_the_current_config():
    exporter = _load("export_config")
    committed = (WEB / "src" / "engine" / "defaults.ts").read_text(encoding="utf-8")
    assert committed == exporter.render(), "run `npm run fixtures` in web/"


def test_the_web_parity_fixtures_match_the_current_python_engine():
    fixtures = _load("make_fixtures")
    committed = json.loads((WEB / "test" / "fixtures" / "parity.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(fixtures.build())), "run `npm run fixtures` in web/"
