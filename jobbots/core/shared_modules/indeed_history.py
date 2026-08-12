"""Deterministic Indeed work-history autofill helpers."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Employer:
    company: str
    title: str
    start_date: str
    end_date: str
    current: bool


WORK_HISTORY = (
    Employer("Vancouver Coastal Health", "Porter", "2022-10-01", "", True),
    Employer("Bell", "Sales Representative", "2018-04-01", "2021-08-01", False),
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_work_history_date(value: str) -> bool:
    return value == "" or bool(_DATE_RE.fullmatch(value))


def work_history_payload() -> list[dict[str, object]]:
    return [employer.__dict__.copy() for employer in WORK_HISTORY]
