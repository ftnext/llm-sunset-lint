"""flake8 checker for model IDs with announced shutdown dates."""

from __future__ import annotations

import ast
import calendar
import re
from datetime import date
from typing import Iterator


ERROR_CODE_PREFIX = "LSG"

# Source:
# https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions.md.txt
# Models whose retirement date has not been announced are omitted. The Markdown
# source is used because it includes the contents of collapsed tabs and sections.
MODEL_SHUTDOWNS = {
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
    "veo-3.1-generate-001": date(2026, 11, 17),
    "veo-3.1-fast-generate-001": date(2026, 11, 17),
    "veo-3.0-generate-001": date(2026, 6, 30),
    "veo-3.0-fast-generate-001": date(2026, 6, 30),
    "veo-2.0-generate-001": date(2026, 6, 30),
    "gemini-embedding-001": date(2028, 5, 20),
    "text-embedding-005": date(2027, 4, 1),
    "text-embedding-004": date(2027, 4, 1),
    "text-multilingual-embedding-002": date(2027, 4, 1),
    "multimodalembedding@001": date(2027, 4, 1),
    "gemini-2.0-flash": date(2026, 6, 1),
    "gemini-2.0-flash-lite": date(2026, 6, 1),
    "gemini-1.5-pro-001": date(2025, 5, 24),
    "gemini-1.5-pro-002": date(2025, 9, 24),
    "gemini-1.5-flash-001": date(2025, 5, 24),
    "gemini-1.5-flash-002": date(2025, 9, 24),
    "gemini-1.0-pro-001": date(2025, 4, 21),
    "gemini-1.0-pro-002": date(2025, 4, 21),
    "gemini-1.0-pro-vision-001": date(2025, 4, 21),
    "textembedding-gecko@003": date(2025, 5, 24),
    "textembedding-gecko-multilingual@001": date(2025, 5, 24),
    "text-bison": date(2025, 4, 21),
    "chat-bison": date(2025, 4, 21),
    "code-gecko": date(2025, 4, 21),
    "textembedding-gecko@002": date(2025, 4, 21),
    "textembedding-gecko@001": date(2025, 4, 21),
    "imagetext": date(2025, 9, 24),
}

# Google Cloud publishes these as the earliest possible retirement dates ("on
# or after"), rather than as fixed shutdown dates.
EARLIEST_SHUTDOWNS = {
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "veo-3.1-generate-001",
    "veo-3.1-fast-generate-001",
    "gemini-embedding-001",
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
    version = "0.0.2"
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
