"""Journal CLI + store: bias, review, result, mistakes — end to end via main()."""

import json

import yaml

from futures_copilot.cli import main
from futures_copilot.db.store import Store


def _tmp_config(tmp_path):
    """Copy of the repo config with all paths under tmp."""
    root_cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    root_cfg["app"]["db_path"] = "data/copilot.db"
    root_cfg["app"]["packets_dir"] = "packets"
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(root_cfg), encoding="utf-8")
    return p


def _seed_signals(tmp_path, n=2):
    with Store(tmp_path / "data" / "copilot.db") as store:
        store.init_schema()
        for i in range(n):
            store.save_signal(symbol="MNQ", ts=1_782_311_100 + i * 300, session="ny",
                              trading_day="2026-06-24", decision="LONG",
                              setup="session_liquidity_trap", grade="A",
                              entry_lo=22975.0, entry_hi=23000.0, stop=22940.0,
                              tp1=23080.0, tp2=None, rr=1.96, reasons="[]",
                              warnings="[]", invalidation="[]", json_signal="{}")


def test_journal_cli_roundtrip(tmp_path, capsys):
    cfg = _tmp_config(tmp_path)
    _seed_signals(tmp_path)

    assert main(["--config", str(cfg), "journal", "bias", "--day", "2026-06-24",
                 "--bias", "bullish", "--notes", "asia sweep thesis"]) == 0
    assert main(["--config", str(cfg), "journal", "mistake", "--tag", "chased_entry",
                 "--description", "entered 30pts late", "--rule", "wait for retest"]) == 0
    assert main(["--config", str(cfg), "journal", "result", "--signal-id", "1",
                 "--r", "1.8", "--mfe", "2.4", "--mae", "-0.3",
                 "--mistakes", "exited_early", "--notes", "cut winner early"]) == 0
    assert main(["--config", str(cfg), "journal", "review", "--signal-id", "2",
                 "--notes", "skipped: chop"]) == 0

    capsys.readouterr()
    assert main(["--config", str(cfg), "journal", "show", "--day", "2026-06-24"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["daily_bias"]["bias"] == "bullish"
    assert out["mistakes"][0]["tag"] == "chased_entry"
    rr = {r["signal_id"]: r for r in out["recent_reviews"]}
    assert rr[1]["taken"] == 1 and rr[1]["result_r"] == 1.8
    assert rr[2]["taken"] == 0

    with Store(tmp_path / "data" / "copilot.db") as store:
        assert store.get_daily_bias("2026-06-24", "MNQ")["notes"] == "asia sweep thesis"
        assert json.loads(store.review_for_signal(1)["mistake_tags"]) == ["exited_early"]


def test_result_for_missing_signal_fails_loudly(tmp_path, capsys):
    cfg = _tmp_config(tmp_path)
    rc = main(["--config", str(cfg), "journal", "result", "--signal-id", "99", "--r", "1.0"])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err


def test_bias_upsert_overwrites(tmp_path):
    cfg = _tmp_config(tmp_path)
    main(["--config", str(cfg), "journal", "bias", "--day", "2026-06-24", "--bias", "bullish"])
    main(["--config", str(cfg), "journal", "bias", "--day", "2026-06-24", "--bias", "bearish",
          "--notes", "flipped after london"])
    with Store(tmp_path / "data" / "copilot.db") as store:
        b = store.get_daily_bias("2026-06-24", "MNQ")
        assert b["bias"] == "bearish" and "flipped" in b["notes"]
