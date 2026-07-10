from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from futures_copilot.config import load_config  # noqa: E402
from futures_copilot.db.store import Store  # noqa: E402

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "mnq_1m_sample.csv"


@pytest.fixture()
def config():
    return load_config(ROOT / "config.yaml")


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.init_schema()
    yield s
    s.close()


@pytest.fixture()
def fixture_csv():
    assert FIXTURE_CSV.exists(), "run scripts/generate_fixture.py first"
    return FIXTURE_CSV


@pytest.fixture(autouse=True)
def deterministic_default_gate_clock(monkeypatch):
    """Existing strategy fixtures are historical; keep their implicit clock replay-safe.

    Dedicated freshness tests override this clock or pass ``now_ts`` explicitly.
    Production code still defaults to the real wall clock.
    """
    monkeypatch.setattr("futures_copilot.gate.risk_gate._wall_time", lambda: 0.0)
