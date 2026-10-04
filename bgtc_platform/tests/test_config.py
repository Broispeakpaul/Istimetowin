import pytest
from pydantic import ValidationError

from screener.config import Config, PROJECT_ROOT, load_config


def test_real_config_loads_with_playbook_defaults():
    cfg = load_config(PROJECT_ROOT / "config.yaml")
    assert cfg.entry.min_day_one_rel_return == 0.08
    assert cfg.entry.min_volume_ratio == 2.0
    assert cfg.entry.min_adv_usd == 50_000_000
    assert cfg.sizing.risk_budget == 0.005 and cfg.sizing.min_weight == 0.04 and cfg.sizing.max_weight == 0.15
    assert cfg.exits.relative_stop == -0.08 and cfg.exits.trim_trigger == 0.18 and cfg.exits.trim_target == 0.15
    assert cfg.exits.time_stop_sessions is None
    assert cfg.regime.vix_threshold == 25 and cfg.regime.sma_window == 50
    assert cfg.themes.group("Semiconductors") == cfg.themes.group("software") == "semis_software"
    assert "NO" in cfg.exchanges  # YAML 'NO' must not be parsed as boolean False


def test_competition_limits_cannot_be_breached():
    with pytest.raises(ValidationError):
        Config(sizing={"max_weight": 0.25})
    with pytest.raises(ValidationError):
        Config(competition={"allow_leverage": True})
    with pytest.raises(ValidationError):
        Config(sizing={"tranche_split": (0.7, 0.4)})


def test_one_line_change_moves_threshold(tmp_path):
    text = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8").replace(
        "min_day_one_rel_return: 0.08", "min_day_one_rel_return: 0.07")
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    assert load_config(p).entry.min_day_one_rel_return == 0.07
