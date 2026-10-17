import ast
from datetime import date

import pytest

from flake8_llm_sunset import ModelExpiryChecker
from flake8_llm_sunset.checker import (
    ModelCatalog,
    model_family,
    one_month_before,
)


@pytest.fixture(autouse=True)
def model_catalog(monkeypatch):
    catalog = ModelCatalog.from_json("""{
        "gemini-test": {"shutdown_date": "2026-10-20", "earliest": false},
        "gemini-test-preview.001": {
            "shutdown_date": "2026-10-20", "earliest": true
        }
    }""")
    monkeypatch.setattr(ModelExpiryChecker, "catalog", catalog)


def check(source: str, today: date):
    checker = ModelExpiryChecker(ast.parse(source))
    checker.today = lambda: today
    return list(checker.run())


@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2026, 9, 19), []),
        (
            date(2026, 9, 20),
            [
                "LSG002 Gemini model 'gemini-test' shuts down on 2026-10-20 "
                "(30 days remaining)",
                "LSG002 Gemini model 'gemini-test-preview.001' "
                "may shut down on or after 2026-10-20",
            ],
        ),
        (
            date(2026, 10, 19),
            [
                "LSG002 Gemini model 'gemini-test' shuts down on 2026-10-20 "
                "(1 days remaining)",
                "LSG002 Gemini model 'gemini-test-preview.001' "
                "may shut down on or after 2026-10-20",
            ],
        ),
        (
            date(2026, 10, 20),
            [
                "LSG001 Gemini model 'gemini-test' "
                "was shut down on 2026-10-20",
                "LSG001 Gemini model 'gemini-test-preview.001' "
                "may shut down on or after 2026-10-20",
            ],
        ),
        (
            date(2026, 10, 21),
            [
                "LSG001 Gemini model 'gemini-test' "
                "was shut down on 2026-10-20",
                "LSG001 Gemini model 'gemini-test-preview.001' "
                "may shut down on or after 2026-10-20",
            ],
        ),
    ],
    ids=[
        "before_window",
        "window_start",
        "day_before",
        "shutdown",
        "day_after",
    ],
)
def test_model_lifecycle(today, expected):
    source = 'MODEL = "gemini-test"\nMODEL = "gemini-test-preview.001"'
    assert check(source, today) == [
        (line, 8, message, ModelExpiryChecker)
        for line, message in enumerate(expected, start=1)
    ]


@pytest.mark.parametrize(
    "model, expected",
    [("gemini-test", "Gemini"), ("veo-test", "Veo"), ("other-test", "Google")],
)
def test_model_family(model, expected):
    assert model_family(model) == expected


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
    errors = check(
        'URL = "https://example.test/models/gemini-test:predict"',
        date(2026, 10, 20),
    )
    assert len(errors) == 1
    assert "model 'gemini-test'" in errors[0][2]


def test_ignores_unknown_model_and_non_string_constants():
    source = 'VALUES = ["unknown-model", 123, None, True]'
    assert check(source, date(2030, 1, 1)) == []


@pytest.mark.parametrize(
    "affix", ["-unknown", ".unknown", "_unknown", "unknown"]
)
@pytest.mark.parametrize("model", ["gemini-test", "gemini-test-preview.001"])
def test_does_not_match_partial_model_id(affix, model):
    source = f'MODELS = ["{model}{affix}", "{affix}{model}"]'
    assert check(source, date(2030, 1, 1)) == []


def test_model_id_dots_are_literal():
    assert check('MODEL = "gemini-test-previewX001"', date(2030, 1, 1)) == []


def test_matches_overlapping_model_ids_without_duplicate_diagnostics():
    source = 'MODELS = ["gemini-test-preview.001", "gemini-test"]'
    errors = check(source, date(2026, 10, 20))

    assert [error[2].split("'")[1] for error in errors] == [
        "gemini-test-preview.001",
        "gemini-test",
    ]
