"""Shared US grade-level helpers for OptiLearn."""
from __future__ import annotations

VALID_GRADE_LEVELS = [
    "Pre-K", "K", "1st", "2nd", "3rd", "4th", "5th", "6th",
    "7th", "8th", "9th", "10th", "11th", "12th",
]

GRADE_TO_AGE = {
    "Pre-K": 4,
    "K": 5,
    "1st": 6,
    "2nd": 7,
    "3rd": 8,
    "4th": 9,
    "5th": 10,
    "6th": 11,
    "7th": 12,
    "8th": 13,
    "9th": 14,
    "10th": 15,
    "11th": 16,
    "12th": 17,
}

_NUMERIC_TO_GRADE = {
    0: "K",
    1: "1st",
    2: "2nd",
    3: "3rd",
    4: "4th",
    5: "5th",
    6: "6th",
    7: "7th",
    8: "8th",
    9: "9th",
    10: "10th",
    11: "11th",
    12: "12th",
    13: "12th",
}


def normalize_grade_level(value: str | int | None, default: str = "7th") -> str:
    """Return a valid OptiLearn grade label, preserving legacy numeric grades."""
    if value is None:
        return default
    if isinstance(value, int):
        return _NUMERIC_TO_GRADE.get(value, default)

    raw = str(value).strip()
    if not raw:
        return default
    folded = raw.lower().replace("grade", "").strip()
    aliases = {
        "pre-k": "Pre-K",
        "prek": "Pre-K",
        "pre k": "Pre-K",
        "prekindergarten": "Pre-K",
        "kindergarten": "K",
        "k": "K",
        "1": "1st",
        "1st": "1st",
        "2": "2nd",
        "2nd": "2nd",
        "3": "3rd",
        "3rd": "3rd",
        "4": "4th",
        "4th": "4th",
        "5": "5th",
        "5th": "5th",
        "6": "6th",
        "6th": "6th",
        "7": "7th",
        "7th": "7th",
        "8": "8th",
        "8th": "8th",
        "9": "9th",
        "9th": "9th",
        "10": "10th",
        "10th": "10th",
        "11": "11th",
        "11th": "11th",
        "12": "12th",
        "12th": "12th",
        "13": "12th",
    }
    return aliases.get(folded, raw if raw in VALID_GRADE_LEVELS else default)


def age_for_grade(grade_level: str | int | None) -> int:
    """Infer the stored age value from a grade label."""
    grade = normalize_grade_level(grade_level)
    return GRADE_TO_AGE[grade]


def grade_number_for_curriculum(grade_level: str | int | None) -> int:
    """Map a grade label to a simple 1-12 number for legacy curriculum search."""
    grade = normalize_grade_level(grade_level)
    if grade in {"Pre-K", "K"}:
        return 1
    return max(1, min(12, int(grade[:-2])))
