import ast
import json
from datetime import date, timedelta
from importlib.resources import files

import pytest

from flake8_llm_sunset import ModelExpiryChecker
from flake8_llm_sunset.checker import one_month_before


MODEL_DATA = json.loads(
    files("flake8_llm_sunset")
    .joinpath("model_shutdowns.json")
    .read_text(encoding="utf-8")
)


def check(source: str, today: date):
    checker = ModelExpiryChecker(ast.parse(source))
    checker.today = lambda: today
    return list(checker.run())


def test_model_data_is_not_empty():
    assert MODEL_DATA


@pytest.mark.parametrize("model, details", MODEL_DATA.items(), ids=MODEL_DATA)
def test_model_data_schema(model, details):
    assert isinstance(model, str) and model and model.strip() == model
    assert set(details) == {"shutdown_date", "earliest"}
    assert type(details["earliest"]) is bool
    shutdown_date = details["shutdown_date"]
    assert date.fromisoformat(shutdown_date).isoformat() == shutdown_date


@pytest.mark.parametrize("model, details", MODEL_DATA.items(), ids=MODEL_DATA)
@pytest.mark.parametrize(
    "stage",
    ["before_window", "window_start", "day_before", "shutdown", "day_after"],
)
def test_model_lifecycle(model, details, stage):
    shutdown = date.fromisoformat(details["shutdown_date"])
    window_start = one_month_before(shutdown)
    today = {
        "before_window": window_start - timedelta(days=1),
        "window_start": window_start,
        "day_before": shutdown - timedelta(days=1),
        "shutdown": shutdown,
        "day_after": shutdown + timedelta(days=1),
    }[stage]

    errors = check(f'MODEL = "{model}"', today)

    if stage == "before_window":
        assert errors == []
        return

    if details["earliest"]:
        status = f"may shut down on or after {shutdown.isoformat()}"
    elif stage in {"shutdown", "day_after"}:
        status = f"was shut down on {shutdown.isoformat()}"
    else:
        status = (
            f"shuts down on {shutdown.isoformat()} "
            f"({(shutdown - today).days} days remaining)"
        )
    if model.startswith("gemini"):
        family = "Gemini"
    elif model.startswith("veo"):
        family = "Veo"
    else:
        family = "Google"
    code = "LSG001" if stage in {"shutdown", "day_after"} else "LSG002"
    assert errors == [
        (1, 8, f"{code} {family} model '{model}' {status}", ModelExpiryChecker)
    ]


@pytest.mark.parametrize(
    "value, expected",
    [
        (date(2026, 10, 20), date(2026, 9, 20)),
        (date(2026, 8, 31), date(2026, 7, 31)),
        (date(2027, 3, 31), date(2027, 2, 28)),
        (date(2028, 3, 31), date(2028, 2, 29)),
        (date(2027, 1, 15), date(2026, 12, 15)),
    ],
)
def test_one_month_before(value, expected):
    assert one_month_before(value) == expected


def test_finds_model_inside_larger_string():
    model = next(iter(MODEL_DATA))
    shutdown = date.fromisoformat(MODEL_DATA[model]["shutdown_date"])
    errors = check(
        f'URL = "https://example.test/models/{model}:predict"', shutdown
    )

    assert len(errors) == 1
    assert f"model '{model}'" in errors[0][2]


def test_ignores_unknown_model_and_non_string_constants():
    source = 'VALUES = ["unknown-model", 123, None, True]'
    assert check(source, date(2030, 1, 1)) == []


@pytest.mark.parametrize("suffix", ["-unknown", ".unknown", "_unknown"])
def test_does_not_match_model_prefix(suffix):
    model = next(iter(MODEL_DATA))
    assert check(f'MODEL = "{model}{suffix}"', date(2030, 1, 1)) == []


def test_matches_overlapping_model_ids_without_duplicate_diagnostics():
    models = list(MODEL_DATA)
    source = f"MODELS = {models!r}"
    errors = check(source, date.max)

    assert len(errors) == len(models)
    assert [error[2].split("'")[1] for error in errors] == models
