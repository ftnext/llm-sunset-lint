"""flake8 checker for model IDs with announced shutdown dates."""

from __future__ import annotations

import ast
import calendar
import json
import re
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from typing import Iterator


ERROR_CODE_PREFIX = "LSG"


@dataclass(frozen=True)
class ModelCatalog:
    shutdowns: dict[str, date]
    earliest: set[str]
    pattern: re.Pattern[str]

    @classmethod
    def from_json(cls, text: str) -> ModelCatalog:
        data = json.loads(text)
        shutdowns = {
            model: date.fromisoformat(details["shutdown_date"])
            for model, details in data.items()
        }
        earliest = {
            model for model, details in data.items() if details["earliest"]
        }
        model_ids = sorted(shutdowns, key=len, reverse=True)
        alternatives = "|".join(re.escape(model) for model in model_ids)
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_.-])({alternatives})(?![A-Za-z0-9_.-])"
        )
        return cls(shutdowns, earliest, pattern)


# Lifecycle data is bundled with the package; no network access is needed.
_MODEL_CATALOG = ModelCatalog.from_json(
    files(__package__)
    .joinpath("model_shutdowns.json")
    .read_text(encoding="utf-8")
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
    catalog = _MODEL_CATALOG

    def __init__(self, tree: ast.AST) -> None:
        self.tree = tree

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type["ModelExpiryChecker"]]]:
        current_date = self.today()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Constant) or not isinstance(
                node.value, str
            ):
                continue
            for match in self.catalog.pattern.finditer(node.value):
                model = match.group(1)
                shutdown = self.catalog.shutdowns[model]
                if current_date < one_month_before(shutdown):
                    continue

                if model in self.catalog.earliest:
                    status = (
                        f"may shut down on or after {shutdown.isoformat()}"
                    )
                elif current_date >= shutdown:
                    status = f"was shut down on {shutdown.isoformat()}"
                else:
                    days = (shutdown - current_date).days
                    status = (
                        f"shuts down on {shutdown.isoformat()} "
                        f"({days} days remaining)"
                    )

                code_number = "001" if current_date >= shutdown else "002"
                code = f"{ERROR_CODE_PREFIX}{code_number}"

                # col_offset points to the opening quote. The offset within
                # the string is not reconstructed from source spelling.
                yield (
                    node.lineno,
                    node.col_offset,
                    f"{code} {model_family(model)} model '{model}' {status}",
                    type(self),
                )
