"""Ortak değer dönüştürücüleri — modüllerin birebir kopyaladığı yardımcılar.

ADR 0033 §7: aynı gövdeli fonksiyon birden fazla modülde yaşıyorsa SDK'ye
taşınır; modül adı korur (`from km_sdk import as_int`), böylece
`orders.as_int` gibi mevcut çağrılar değişmez. Davranış kopyalarla BİREBİR
aynıdır — bir değişiklik bütün tüketicileri birden etkiler, testleri
`tests/core/test_sdk_ortak.py`'dedir.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def as_int(value: Any, default: int = 0) -> int:
    """Gevşek tam sayı: `"12"`, `" 12 "`, `"12.7"`, `"12,7"` → 12; çözülemezse `default`.

    Mağaza API'si sayıları kimi zaman dizgi, kimi zaman ondalık döndürür.
    (16.10.2026'ya kadar 14 mağaza modülünde birebir kopyaydı.)
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return default


def to_kurus(value: Any) -> int | None:
    """Bagisto'nun ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: burada `float` kullanılmaz. `float("1234.35") * 100` bazı değerlerde
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar. Sipariş toplamında
    bir kuruşluk sapma, gün sonu ciro raporunu tutmaz hâle getirir.
    Binlik ayırıcı iki biçimde de çözülür: `1.234,35` ve `1,234.35` → 123435.
    """
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if raw == "":
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else \
            raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def masked_token(value: Any, keep: int = 6) -> str:
    """Tek kullanımlık işlem jetonunun günlüğe yazılabilir hâli: ilk `keep` karakter + `…`.

    Güvenlik bulgusu KM-17 (63-duzeltici): kuru prova → uygula akışının jetonu
    hata günlüğüne TAM yazılıyordu; günlüğü okuyan, süresi dolmamış jetonla
    işlemi kendi adına uygulatabilirdi. İlk altı karakter satırları eşleştirmeye
    yeter, jetonu yeniden kurmaya yetmez. Boş değer `""` döner.
    """
    text = "" if value is None else str(value)
    if len(text) <= keep:
        return "…" if text else ""
    return f"{text[:keep]}…"
