"""Zamanlayıcı OKULUN saatine göre çalar, makinenin saatine göre değil.

BULUNAN ARIZA (18.08.2026). `now_local()` `datetime.now().astimezone()`
kullanıyordu, yani konteynerin duvar saatini. Çekirdek artık sunucuda koşuyor
(ADR 0026) ve sunucu imajı ne `TZ` ne `tzdata` taşıyordu: konteyner UTC'de.
İstanbul UTC+3 olduğu için 08:40'a kurulan zil, konteynerin saati 08:40 olunca
— İSTANBUL'DA 11:40'ta — çalıyordu.

Belirti "zil çalmıyor"du ve teşhisi zordu: ELLE çalma anlık olduğu için
sorunsuz çalışıyor, arızayı gizliyordu. Kullanıcı "manuel tetikleyince
çalışıyor" diyordu ve bu doğruydu.

KURAL: kullanıcı 10:40 girdiyse zil TÜRKİYE SAATİYLE 10:40'ta çalar.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from km_platform.scheduler.weekly import (
    DEFAULT_TIMEZONE,
    Trigger,
    WeeklyScheduler,
    business_zone,
    now_local,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, message: str, **fields: Any) -> None:
        self.records.append(("info", message, fields))

    def warning(self, message: str, **fields: Any) -> None:
        self.records.append(("warning", message, fields))

    def error(self, message: str, **fields: Any) -> None:
        self.records.append(("error", message, fields))


# ------------------------------------------------------------- saat dilimi


def test_varsayilan_dilim_istanbul() -> None:
    # Ürün Türkiye'de çalışıyor; varsayılanın makineye bırakılması tam da
    # düzeltilen arızaydı.
    assert DEFAULT_TIMEZONE == "Europe/Istanbul"


def test_makine_utc_olsa_da_is_saati_istanbul(monkeypatch: Any) -> None:
    """Konteyner UTC'de koşarken bile "şimdi" İstanbul saatidir."""
    monkeypatch.setenv("TZ", "UTC")
    if hasattr(os, "tzset"):
        os.tzset()

    an = now_local(business_zone("Europe/Istanbul"))
    ofset = an.utcoffset()
    assert ofset is not None
    # Türkiye yıl boyu UTC+3 (2016'dan beri yaz saati uygulanmıyor).
    assert ofset.total_seconds() == 3 * 3600


def test_scheduler_dilimini_bildirir() -> None:
    scheduler = WeeklyScheduler(FakeLog(), timezone="Europe/Istanbul")
    durum = scheduler.state()
    # EKRAN HANGİ SAATE GÖRE ÇALDIĞINI YAZMALI: sessiz kalan bir saat dilimi
    # yanlış saatte çalan zili "hiç çalmıyor" gibi gösteriyordu.
    assert durum["timezone"] == "Europe/Istanbul"
    assert datetime.fromisoformat(durum["now"]).utcoffset() is not None


def test_taninmayan_dilim_gunluge_yazilir() -> None:
    # Sessiz yedek, düzeltilen arızayı geri getirirdi.
    log = FakeLog()
    WeeklyScheduler(log, timezone="Mars/Olympus")
    assert any(seviye == "warning" for seviye, _, _ in log.records)


# ------------------------------------------------- girilen saat = çalan saat


def test_girilen_saat_turkiye_saatiyle_calar() -> None:
    """Kullanıcı 10:40 girdi; sıradaki tetikleyici İstanbul'da 10:40 olmalı."""
    scheduler = WeeklyScheduler(FakeLog(), timezone="Europe/Istanbul")

    async def _hicbir_sey(trigger: Trigger) -> None:
        return None

    scheduler.set_plan("bell", [Trigger(day="mon", time="10:40", label="ders")],
                       _hicbir_sey)

    # Pazartesi 10:00 İstanbul. Sıradaki zil 40 dakika sonra olmalı —
    # arıza sürerken bu 3 saat 40 dakika sonrasını gösteriyordu.
    simdi = datetime(2026, 8, 17, 10, 0, tzinfo=ISTANBUL)
    sirada = scheduler.next_triggers(simdi, limit=1)
    assert sirada, "tetikleyici bulunamadı"

    an = datetime.fromisoformat(str(sirada[0]["at"]))
    yerel = an.astimezone(ISTANBUL)
    assert (yerel.hour, yerel.minute) == (10, 40)
    assert (yerel - simdi).total_seconds() == 40 * 60
