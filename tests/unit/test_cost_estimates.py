"""Owner-accepted projections are per model; charges remain independently observed."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.llm.factory import load_tariffs
from app.llm.runtime import load_runtime, quote_stages

MODEL = "deepseek/deepseek-v4.1-flash"
CONFIG = Path("config/llm-tariffs.json")


def changed(tmp_path, mutate):
    data = json.loads(CONFIG.read_text())
    mutate(data["tariffs"])
    path = tmp_path / "tariffs.json"
    path.write_text(json.dumps(data))
    return path


def test_estimate_is_accepted_for_all_models_but_only_deepseek_was_measured():
    raw = json.loads(CONFIG.read_text())["tariffs"]
    assert all(row["multiplier"] == "2.23" for row in raw)
    assert all("cost_estimate" not in row and "evidence" not in row for row in raw)
    t = load_tariffs(CONFIG)[MODEL]
    assert t.estimate_confirmed
    assert t.quote(input_tokens=1000, max_output_tokens=1000) == Decimal("0.031000")
    assert t.estimate(input_tokens=1000, max_output_tokens=1000) == Decimal("0.069130")
    # Round once after calibration, rather than multiplying an already rounded charge.
    assert t.estimate(input_tokens=1, max_output_tokens=1) == Decimal("0.000070")
    quotes = quote_stages(load_runtime(), "adaptive", load_tariffs(CONFIG))
    assert sum(Decimal(q["maximum_reserve_rub"]) for q in quotes) == Decimal("0.680373")
    assert sum(Decimal(q["nominal_tariff_rub"]) for q in quotes) == Decimal("0.305100")


def test_models_have_independent_calibration_and_changed_pins(tmp_path):
    old = load_tariffs(CONFIG)
    path = changed(tmp_path, lambda rows: rows[0].update(multiplier="3"))
    new = load_tariffs(path)
    gemma = next(iter(new))
    assert new[gemma].estimate_multiplier == 3
    assert new[gemma].version != old[gemma].version
    assert new[MODEL] == old[MODEL]
    assert new[gemma].quote(input_tokens=1000, max_output_tokens=1000) == old[gemma].quote(
        input_tokens=1000, max_output_tokens=1000
    )


@pytest.mark.parametrize("multiplier", ["0", "-1", "NaN", "Infinity"])
def test_invalid_multiplier_blocks_before_network(tmp_path, multiplier):
    path = changed(tmp_path, lambda rows: rows[0].update(multiplier=multiplier))
    with pytest.raises(AppError) as error:
        load_tariffs(path)
    assert error.value.code.value == "budget_unknown"
