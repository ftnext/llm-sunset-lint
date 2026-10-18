import ast
import json
from datetime import date
from importlib.resources import files

import pytest

from flake8_llm_sunset import ModelExpiryChecker


MODEL_DATA = json.loads(
    files("flake8_llm_sunset")
    .joinpath("model_shutdowns.json")
    .read_text(encoding="utf-8")
)


def test_model_data_is_not_empty():
    assert MODEL_DATA


@pytest.mark.parametrize("model, details", MODEL_DATA.items(), ids=MODEL_DATA)
def test_model_data_schema(model, details):
    assert isinstance(model, str) and model and model.strip() == model
    assert set(details) == {"shutdown_date", "earliest"}
    assert type(details["earliest"]) is bool
    shutdown_date = details["shutdown_date"]
    assert date.fromisoformat(shutdown_date).isoformat() == shutdown_date


def test_checker_loads_all_bundled_models():
    models = list(MODEL_DATA)
    checker = ModelExpiryChecker(ast.parse(f"MODELS = {models!r}"))
    checker.today = lambda: date.max

    assert [error[2].split("'")[1] for error in checker.run()] == models
