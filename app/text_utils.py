from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation


AMOUNT_RE = re.compile(
    r"(?P<amount>-?\s*(?:\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,\d{2}|\.\d{2})\s*-?)"
)


def normalize_key(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = ascii_value.lower()
    ascii_value = re.sub(r"[^a-z0-9]+", " ", ascii_value)
    return re.sub(r"\s+", " ", ascii_value).strip()


def clean_line(value: str) -> str:
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_amount(value: str) -> Decimal | None:
    raw = value.strip().replace(" ", "")
    negative = raw.startswith("-") or raw.endswith("-")
    raw = raw.strip("-")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)
