import ast
from datetime import date

from flake8_llm_sunset import ModelExpiryChecker


def check(source: str, today: date):
    checker = ModelExpiryChecker(ast.parse(source))
    checker.today = lambda: today
    return list(checker.run())


def test_warns_one_calendar_month_before_shutdown():
    errors = check('MODEL = "gemini-2.5-flash"', date(2026, 9, 16))

    assert len(errors) == 1
    assert errors[0][2] == (
        "LLS001 Gemini model 'gemini-2.5-flash' shuts down on 2026-10-16 "
        "(30 days remaining)"
    )


def test_does_not_warn_before_warning_window():
    assert check('MODEL = "gemini-2.5-flash"', date(2026, 9, 15)) == []


def test_warns_for_already_shutdown_model():
    errors = check(
        'client.models.generate_content(model="gemini-2.0-flash", contents="hi")',
        date(2026, 7, 22),
    )

    assert "was shut down on 2026-06-01" in errors[0][2]


def test_finds_model_inside_larger_string():
    errors = check(
        'URL = "https://example.test/models/gemini-3.1-flash-image:predict"',
        date(2027, 5, 1),
    )

    assert len(errors) == 1


def test_distinguishes_earliest_possible_shutdown_date():
    errors = check('MODEL = "gemini-3.5-flash"', date(2027, 4, 19))

    assert errors[0][2] == (
        "LLS001 Gemini model 'gemini-3.5-flash' may shut down on or after "
        "2027-05-19"
    )


def test_ignores_unknown_or_no_shutdown_date_model():
    source = 'MODELS = ["gemini-3.6-flash", "gemini-something-new"]'
    assert check(source, date(2030, 1, 1)) == []
