from __future__ import annotations

import json
from pathlib import Path

import pytest

from futures_copilot.cli import main


@pytest.mark.parametrize("command", ["backtest", "review"])
def test_db_dependent_command_help_names_prerequisites(command, capsys):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "existing db" in output
    assert "init-db" in output
    assert "backfill" in output


def test_feature_status_tracks_completed_backtest_review_and_dashboard_resource():
    path = Path(__file__).resolve().parents[1] / "features_status.json"
    status = json.loads(path.read_text(encoding="utf-8"))

    assert status["updated"] == "2026-07-10"
    for name in ("backtest.engine", "review.weekly", "dashboard.mcp_resource"):
        item = status["features"][name]
        assert item["status"] == "done"
        assert item["phase"] > 0
        assert item["notes"]
