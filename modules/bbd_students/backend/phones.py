"""Telefon normalizasyonu — `5XXXXXXXXX` (10 hane) ya da None.

Sözleşme (mobil geçit, "Ortak kurallar"): telefon normalize biçimde döner,
normalize edilemeyen telefon `null` döner, asla ham metin dönmez. Kural tek
yerdedir; dizin yeteneği ve telefonla arama buradan geçer.

Kabul edilen yazımlar: `5321234567`, `05321234567`, `+90 532 123 45 67`,
`0090 (532) 123-45-67`, `90 532 1234567`. Cep telefonu olmayan (5 ile
başlamayan) numara normalize EDİLMEZ — sabit hat SMS/OTP alamaz.
"""

from __future__ import annotations

import re
from typing import Any

_DIGITS = re.compile(r"\D+")


def normalize_phone(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    digits = _DIGITS.sub("", raw)
    if digits.startswith("0090"):
        digits = digits[4:]
    elif digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10 and digits.startswith("5"):
        return digits
    return None
