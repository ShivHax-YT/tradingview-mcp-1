import pytest

from futures_copilot.config import Config
from futures_copilot.errors import ConfigError


def test_config_loads(config):
    assert "MNQ" in config.symbols and "MES" in config.symbols
    assert config.symbols["MNQ"].chart_symbol == "CME_MINI:MNQ1!"
    assert config.symbols["MNQ"].tick_size == 0.25
    assert config.timeframes.tradingview_map["1m"] == "1"
    assert config.risk.min_rr == 1.5


def test_auto_execution_can_never_be_enabled(config):
    raw = config.model_dump()
    raw["risk"]["auto_execution_enabled"] = True
    with pytest.raises(Exception) as exc:
        Config(**raw)
    assert "not permitted" in str(exc.value)


def test_manual_approval_can_never_be_disabled(config):
    raw = config.model_dump()
    raw["risk"]["manual_approval_required"] = False
    with pytest.raises(Exception):
        Config(**raw)


def test_missing_config_raises_config_error(tmp_path):
    from futures_copilot.config import load_config

    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")
