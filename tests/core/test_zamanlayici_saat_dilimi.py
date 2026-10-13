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
from datetime import datetime, timedelta
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


# ------------------------------------------------ bir tetik = bir çalışma


async def test_tetik_gecikme_payinda_iki_kez_calismaz() -> None:
    """BULUNAN ARIZA (13.10.2026, canlı zil günlüğü). Gecikme payı (90 sn) bir
    dakikadan uzun; damga turun dakikasıyken 08:39'luk tetik 08:39:0x'te ve
    08:40:0x'te İKİ KEZ çalışıyordu — her zil köprüye iki komut yazıyordu.

    Zaman 5 sn'lik turlarla 08:38:55'ten 08:41:30'a sürülür: tetik bir kez."""
    scheduler = WeeklyScheduler(FakeLog(), timezone="Europe/Istanbul")
    fired: list[str] = []

    async def kaydet(trigger: Trigger) -> None:
        fired.append(trigger.label)

    scheduler.set_plan("bell", [Trigger(day="mon", time="08:39", label="gönderim")], kaydet)
    an = datetime(2026, 10, 12, 8, 38, 55, tzinfo=ISTANBUL)          # pazartesi
    while an <= datetime(2026, 10, 12, 8, 41, 30, tzinfo=ISTANBUL):
        await scheduler._tick(an)
        an += timedelta(seconds=5)
    assert fired == ["gönderim"]


async def test_ayni_tetik_ertesi_hafta_yine_calisir() -> None:
    scheduler = WeeklyScheduler(FakeLog(), timezone="Europe/Istanbul")
    fired: list[datetime] = []

    async def kaydet(trigger: Trigger) -> None:
        fired.append(an)

    scheduler.set_plan("bell", [Trigger(day="mon", time="08:39", label="x")], kaydet)
    for hafta in (12, 19):
        an = datetime(2026, 10, hafta, 8, 39, 2, tzinfo=ISTANBUL)
        await scheduler._tick(an)
        an = datetime(2026, 10, hafta, 8, 40, 2, tzinfo=ISTANBUL)
        await scheduler._tick(an)
    assert [item.day for item in fired] == [12, 19]


async def test_gec_uyanista_pay_icinde_bir_kez_disinda_hic() -> None:
    """Uyku dönüşü: pay içindeki tetik geç de olsa BİR kez, payı aşan hiç çalışmaz."""
    scheduler = WeeklyScheduler(FakeLog(), timezone="Europe/Istanbul")
    fired: list[str] = []

    async def kaydet(trigger: Trigger) -> None:
        fired.append(trigger.time)

    scheduler.set_plan("bell", [Trigger(day="mon", time="08:39"),
                                Trigger(day="mon", time="08:30")], kaydet)
    await scheduler._tick(datetime(2026, 10, 12, 8, 40, 20, tzinfo=ISTANBUL))
    await scheduler._tick(datetime(2026, 10, 12, 8, 40, 25, tzinfo=ISTANBUL))
    assert fired == ["08:39"]
