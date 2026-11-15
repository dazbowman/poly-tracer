from pathlib import Path

import pytest
from pydantic import ValidationError

from polycopy.config import Config

REPO = Path(__file__).resolve().parents[1]


def test_default_yaml_loads_and_matches_code_defaults():
    cfg = Config.load(REPO / "configs" / "default.yaml")
    assert cfg == Config()


def test_config_hash_is_stable_and_sensitive():
    a = Config()
    b = Config.load(REPO / "configs" / "default.yaml")
    assert a.config_hash() == b.config_hash()
    c = a.model_copy(update={"seed": 1730})
    assert c.config_hash() != a.config_hash()


def test_unknown_keys_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({"bankrol_usd": 1})  # typo must not pass silently
