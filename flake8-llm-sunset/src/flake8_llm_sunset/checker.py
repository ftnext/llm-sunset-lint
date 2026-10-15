"""flake8 checker for model IDs with announced shutdown dates."""

from __future__ import annotations

import ast
import calendar
import json
import re
from datetime import date
from importlib.resources import files
from typing import Iterator


ERROR_CODE_PREFIX = "LSG"

# Lifecycle data is bundled with the package; no network access is needed.
_MODEL_DATA = json.loads(
    files(__package__)
    .joinpath("model_shutdowns.json")
    .read_text(encoding="utf-8")
)
MODEL_SHUTDOWNS = {
    model: date.fromisoformat(details["shutdown_date"])
    for model, details in _MODEL_DATA.items()
}
EARLIEST_SHUTDOWNS = {
    model for model, details in _MODEL_DATA.items() if details["earliest"]
}

_MODEL_IDS = sorted(MODEL_SHUTDOWNS, key=len, reverse=True)
_MODEL_ALTERNATIVES = "|".join(re.escape(model) for model in _MODEL_IDS)
_MODEL_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_.-])({_MODEL_ALTERNATIVES})(?![A-Za-z0-9_.-])"
)


def model_family(model: str) -> str:
    """Return the product family used in the diagnostic message."""
    if model.startswith("gemini"):
        return "Gemini"
    if model.startswith("veo"):
        return "Veo"
    return "Google"


def one_month_before(value: date) -> date:
    """Subtract one calendar month, clamping to the target month's last day."""
    year, month = value.year, value.month - 1
    if month == 0:
        year, month = year - 1, 12
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class ModelExpiryChecker:
    """Find expiring Google model IDs in Python string literals."""

    name = "flake8-llm-sunset"
    version = "0.0.6"
    today = staticmethod(date.today)

    def __init__(self, tree: ast.AST) -> None:
        self.tree = tree

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type["ModelExpiryChecker"]]]:
        current_date = self.today()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for match in _MODEL_PATTERN.finditer(node.value):
                model = match.group(1)
                shutdown = MODEL_SHUTDOWNS[model]
                if current_date < one_month_before(shutdown):
                    continue

                if model in EARLIEST_SHUTDOWNS:
                    status = f"may shut down on or after {shutdown.isoformat()}"
                elif current_date >= shutdown:
                    status = f"was shut down on {shutdown.isoformat()}"
                else:
                    days = (shutdown - current_date).days
                    status = (
                        f"shuts down on {shutdown.isoformat()} ({days} days remaining)"
                    )

                code_number = "001" if current_date >= shutdown else "002"
                code = f"{ERROR_CODE_PREFIX}{code_number}"

                # col_offset points to the opening quote; the exact offset inside a
                # string is deliberately not reconstructed from source spelling.
                yield (
                    node.lineno,
                    node.col_offset,
                    f"{code} {model_family(model)} model '{model}' {status}",
                    type(self),
                )
