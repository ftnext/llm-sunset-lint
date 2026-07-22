"""flake8 checker for model IDs with announced shutdown dates."""

from __future__ import annotations

import ast
import calendar
import re
from datetime import date
from typing import Iterator


# Source: https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions
# Models whose retirement date has not been announced are omitted.
GEMINI_SHUTDOWNS = {
    "gemini-3.5-flash": date(2027, 5, 19),
    "gemini-3.5-flash-lite": date(2027, 7, 21),
    "gemini-3.1-flash-lite": date(2027, 5, 7),
    "gemini-live-2.5-flash-native-audio": date(2026, 12, 13),
    "gemini-2.5-pro": date(2026, 10, 16),
    "gemini-2.5-flash": date(2026, 10, 16),
    "gemini-2.5-flash-lite": date(2026, 10, 16),
    "gemini-3.1-flash-image": date(2027, 5, 28),
    "gemini-3-pro-image": date(2027, 5, 28),
    "gemini-2.5-flash-image": date(2026, 10, 2),
    "gemini-embedding-001": date(2028, 5, 20),
    "gemini-2.0-flash": date(2026, 6, 1),
    "gemini-2.0-flash-lite": date(2026, 6, 1),
    "gemini-1.5-pro-001": date(2025, 5, 24),
    "gemini-1.5-pro-002": date(2025, 9, 24),
    "gemini-1.5-flash-001": date(2025, 5, 24),
    "gemini-1.5-flash-002": date(2025, 9, 24),
    "gemini-1.0-pro-001": date(2025, 4, 21),
    "gemini-1.0-pro-002": date(2025, 4, 21),
    "gemini-1.0-pro-vision-001": date(2025, 4, 21),
}

# Google Cloud publishes these as the earliest possible retirement dates ("on
# or after"), rather than as fixed shutdown dates.
GEMINI_EARLIEST_SHUTDOWNS = {
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "gemini-embedding-001",
}

_MODEL_IDS = sorted(GEMINI_SHUTDOWNS, key=len, reverse=True)
_MODEL_ALTERNATIVES = "|".join(re.escape(model) for model in _MODEL_IDS)
_MODEL_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_.-])({_MODEL_ALTERNATIVES})(?![A-Za-z0-9_.-])"
)


def one_month_before(value: date) -> date:
    """Subtract one calendar month, clamping to the target month's last day."""
    year, month = value.year, value.month - 1
    if month == 0:
        year, month = year - 1, 12
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class ModelExpiryChecker:
    """Find expiring Gemini model IDs in Python string literals."""

    name = "flake8-llm-sunset"
    version = "0.0.1"
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
                shutdown = GEMINI_SHUTDOWNS[model]
                if current_date < one_month_before(shutdown):
                    continue

                if model in GEMINI_EARLIEST_SHUTDOWNS:
                    status = f"may shut down on or after {shutdown.isoformat()}"
                elif current_date >= shutdown:
                    status = f"was shut down on {shutdown.isoformat()}"
                else:
                    days = (shutdown - current_date).days
                    status = (
                        f"shuts down on {shutdown.isoformat()} ({days} days remaining)"
                    )

                # col_offset points to the opening quote; the exact offset inside a
                # string is deliberately not reconstructed from source spelling.
                yield (
                    node.lineno,
                    node.col_offset,
                    f"LLS001 Gemini model '{model}' {status}",
                    type(self),
                )
