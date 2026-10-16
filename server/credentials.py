"""Readable, persistent team credentials; never included in public payloads."""
import secrets

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_team_code(existing=()):
    used = {normalize_team_code(value) for value in existing}
    while True:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
        if raw not in used:
            return "-".join(raw[i:i+4] for i in range(0, 12, 4))


def normalize_team_code(value):
    if not isinstance(value, str) or not value.isascii():
        return ""
    return "".join(value.upper().split()).replace("-", "")


def imported_team_code(value):
    """Accept privately preissued codes, using the same alphabet as generated ones."""
    raw = normalize_team_code(value)
    if len(raw) != 12 or any(char not in ALPHABET for char in raw):
        raise ValueError("點棋碼須為 12 碼英數字，可含連字號；不可含 I、O、0、1")
    return "-".join(raw[i:i+4] for i in range(0, 12, 4))


def matches(expected, supplied):
    return isinstance(supplied, str) and supplied.isascii() and secrets.compare_digest(expected, supplied)


def matches_team_code(expected, supplied):
    value = normalize_team_code(supplied)
    return bool(value) and matches(normalize_team_code(expected), value)
