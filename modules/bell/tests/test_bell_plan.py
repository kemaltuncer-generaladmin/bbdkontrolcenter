"""Zil planı, saat setleri, acil kapatma, oturum çağrıları ve grup taşıma (0.5).

Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §3, §6.
Depo gerçek SQLite (bütün göçler), Vertex/köprü/zamanlayıcı taklit — AĞA
ÇIKILMAZ. "Bugün" pazartesi 12.10.2026, saat 08:00 (İstanbul) sabittir.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from bell_backend import plan as planning
from bell_backend.api import routes
from bell_backend.plan import PlanError, render_call
from bell_backend.plan_capability import BellPlan
from bell_backend.service import (
    DEFAULT_CALL_TEXT,
    BellService,
    generic_text,
    session_text,
    template_warnings,
)
from bell_backend.voices import VoiceLibrary
from bell_fakes import FakeAudio, FakeBridge, FakeLog, FakeScheduler, FakeSpeech

from km_core.store.dialect import concurrent_index_plan, translate_ddl

ISTANBUL = ZoneInfo("Europe/Istanbul")
TODAY = date(2026, 10, 12)                       # pazartesi
MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"


class FakeGroups:
    """`bbd_students.groups` yeteneğinin test yüzü."""

    def __init__(self) -> None:
        self.groups: list[dict[str, Any]] = []
        self.imported: list[str] = []

    async def list(self, since_revision: int | None = None) -> dict[str, Any]:
        return {"revision": len(self.groups), "full": True, "complete": True,
                "groups": [dict(group) for group in self.groups]}

    async def import_named(self, name: str, *, kind: str, call_name: str = "",
                           actor: str) -> dict[str, Any]:
        self.imported.append(name)
        for group in self.groups:
            if group["name"].casefold() == name.casefold():
                return {"id": group["id"], "created": False}
        group = {"id": f"g{len(self.groups) + 1}", "name": name, "callName": call_name,
                 "kind": kind, "archived": False, "members": []}
        self.groups.append(group)
        return {"id": group["id"], "created": True}


class Clock:
    def __init__(self) -> None:
        self.today = TODAY
        self.now = datetime(2026, 10, 12, 8, 0, tzinfo=ISTANBUL)


def build(store: Any, sounds: Path, *, speech: FakeSpeech | None = None,
          groups: FakeGroups | None = None,
          play_locally: bool = False) -> tuple[BellService, dict[str, Any]]:
    log = FakeLog()
    clock = Clock()
    parts: dict[str, Any] = {
        "audio": FakeAudio(sounds), "scheduler": FakeScheduler(), "bridge": FakeBridge(),
        "speech": speech or FakeSpeech(), "log": log, "clock": clock, "groups": groups,
    }
    parts["voices"] = VoiceLibrary(store=store, log=log, speech=parts["speech"],
                                   sounds_path=sounds, config={"min_interval_seconds": 0})
    service = BellService(
        store=store, log=log, audio=parts["audio"], scheduler=parts["scheduler"],
        voices=parts["voices"], bridge=parts["bridge"], play_locally=play_locally,
        student_groups=(lambda: groups) if groups is not None else None,
        today=lambda: clock.today, now=lambda: clock.now,
    )
    return service, parts


async def drain(voices: VoiceLibrary) -> None:
    await voices._queue.join()


def call(ref: str, at: str, group: str, *, teacher: str | None = "Hüseyin hoca",
         kind: str = "group", group_ref: str = "", room: str | None = None,
         subject: str | None = "Matematik") -> dict[str, Any]:
    return {"ref": ref, "at": at, "groupKind": kind, "groupName": group,
            "groupRef": group_ref or f"km_group:{group}", "teacherName": teacher,
            "room": room, "subject": subject, "kind": "lesson"}


def day(on: date, *, mode: str = "normal", revision: int = 1, plan_hash: str | None = None,
        calls: list[dict[str, Any]] | None = None, profile: str | None = None,
        reason: str | None = None) -> dict[str, Any]:
    return {"date": on.isoformat(), "mode": mode, "profileId": profile, "reason": reason,
            "revision": revision, "planHash": plan_hash or f"h{revision}-{on}",
            "calls": calls or []}


def push(*days: dict[str, Any]) -> dict[str, Any]:
    dates = sorted(item["date"] for item in days)
    return {"from": dates[0], "to": dates[-1], "days": list(days)}


def window(*overrides: dict[str, Any], start: date = TODAY, length: int = 1) -> dict[str, Any]:
    """`length` günlük itme; verilen günler yerini alır, kalanlar boş `normal`."""
    by_date = {item["date"]: item for item in overrides}
    days = []
    for offset in range(length):
        on = start + timedelta(days=offset)
        days.append(by_date.get(on.isoformat(), day(on)))
    return push(*days)


async def mondays_bells(service: BellService) -> None:
    await service.save_times({
        "mon": [{"time": "08:40", "kind": "ders"}, {"time": "09:30", "kind": "teneffus"}],
        "tue": [{"time": "08:40", "kind": "ders"}],
    }, actor="t")


def triggers(parts: dict[str, Any], phase: str = "dispatch") -> list[Any]:
    return [t for t in parts["scheduler"].plans.get("bell", []) if t.payload["phase"] == phase]


async def fire(parts: dict[str, Any], trigger: Any) -> None:
    plan = parts["scheduler"].plans["bell"]
    await parts["scheduler"].fire("bell", plan.index(trigger))


# ------------------------------------------------------------------- göç


def test_goc_lehce_cevirmeninden_gecer() -> None:
    translate_ddl((MIGRATIONS / "006_plan.sql").read_text(encoding="utf-8"))
    indexes = concurrent_index_plan(
        (MIGRATIONS / "007_plan_indexes.sql").read_text(encoding="utf-8"))
    assert indexes is not None and len(indexes) == 3


# ---------------------------------------------------------------- metin


def test_yer_tutucular_ve_genel_sablon() -> None:
    assert render_call("{grup}, {hoca} ile {ders} dersiniz {derslik} sınıfında.",
                       group="TYT grubu", teacher="Zeynep hoca", room="B-2",
                       subject="Fizik") == "TYT grubu, Zeynep hoca ile Fizik dersiniz B-2 sınıfında."
    # Boş yer tutucu kırıntı bırakmaz.
    assert render_call("{grup}, {derslik} dersiniz başlıyor.", group="LGS") == \
        "LGS, dersiniz başlıyor."


async def test_sabit_hoca_sablonu_degismez_uyari_ve_oneri(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    settings = await service.settings()
    # Bugünkü şablon KENDİLİĞİNDEN değişmez (kullanıcı onayı gerekir).
    assert settings["texts"]["call"] == DEFAULT_CALL_TEXT
    assert "Hüseyin hoca" in DEFAULT_CALL_TEXT
    warnings = {item["key"]: item for item in template_warnings(settings)}
    assert warnings["call"]["suggestion"] == DEFAULT_CALL_TEXT.replace("Hüseyin hoca", "{hoca}")
    assert "solo" in warnings

    # Öğretmen adı yoksa genel şablon; varsa şablon (sabit adlı da olsa) okunur.
    row = call("r1", "09:00", "TYT grubu", teacher=None)
    assert session_text(settings, row) == "TYT grubu, dersiniz başlıyor. Lütfen sınıfınıza geçiniz."
    solo = call("r2", "09:00", "İlayda", teacher=None, kind="individual")
    assert session_text(settings, solo) == "İlayda, özel dersin başlıyor."

    await service.save_settings({**settings, "texts": {
        **settings["texts"], "call": warnings["call"]["suggestion"]}}, actor="t")
    settings = await service.settings()
    assert session_text(settings, call("r3", "09:00", "TYT grubu", teacher="Zeynep hoca")) == \
        "TYT grubu, Zeynep hoca ile dersiniz başlıyor. Lütfen hocanın yanına geçiniz."
    assert "call" not in {item["key"] for item in template_warnings(settings)}
    # Şablon {hoca} taşıyınca öğretmeni bilinmeyen eski grup çağrısı genel şablona düşer.
    from bell_backend.service import call_text
    assert call_text(settings, "LGS", "grup") == generic_text(settings, "LGS", "grup")


# ------------------------------------------------------- plan uygulama kuralı


async def test_dogrulama_eksik_gun_ve_aralik(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    body = push(day(TODAY), day(TODAY + timedelta(days=2)))
    with pytest.raises(PlanError) as caught:
        await service.apply_days(body, actor="akademi")
    assert caught.value.details["missing"] == ["2026-10-13"]

    long = {"from": "2026-10-12", "to": "2026-10-27", "days": []}
    with pytest.raises(PlanError):
        await service.apply_days(long, actor="akademi")
    bad_call = push(day(TODAY, calls=[{**call("r", "9:00", "TYT")}]))
    with pytest.raises(PlanError) as caught:
        await service.apply_days(bad_call, actor="akademi")
    assert caught.value.details["path"] == "days[0].calls[0].at"


async def test_revizyon_ve_ozet_kurallari(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    first = await service.apply_days(push(day(TODAY, revision=5, plan_hash="a")), actor="ak")
    assert first["days"] == [{"date": "2026-10-12", "result": "applied", "revision": 5}]
    assert first["bellEnabled"] is True

    same = await service.apply_days(push(day(TODAY, revision=5, plan_hash="a")), actor="ak")
    assert same["days"][0]["result"] == "unchanged"
    # Özet aynı, revizyon ileri: unchanged ama imleç ilerler.
    moved = await service.apply_days(push(day(TODAY, revision=6, plan_hash="a")), actor="ak")
    assert moved["days"][0]["result"] == "unchanged"
    status = await service.plan_status("2026-10-12", "2026-10-12")
    assert status["days"][0]["revision"] == 6

    stale = await service.apply_days(push(day(TODAY, revision=4, plan_hash="b")), actor="ak")
    assert stale["days"][0]["result"] == "stale"
    conflict = await service.apply_days(push(day(TODAY, revision=6, plan_hash="c")), actor="ak")
    assert conflict["days"][0] == {"date": "2026-10-12", "result": "rejected", "revision": 6,
                                   "error": "revision_conflict"}
    newer = await service.apply_days(push(day(TODAY, revision=7, plan_hash="d")), actor="ak")
    assert newer["days"][0]["result"] == "applied"
    status = await service.plan_status("2026-10-12", "2026-10-12")
    assert (status["days"][0]["planHash"], status["days"][0]["source"]) == ("d", "akademi")


async def test_bilinmeyen_saat_seti_yalniz_o_gunu_reddeder(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    body = window(day(TODAY, mode="profile", profile="yok"), length=2)
    result = await service.apply_days(body, actor="ak")
    assert result["days"][0]["result"] == "rejected"
    assert result["days"][0]["error"] == "unknown_profile"
    assert result["days"][0]["details"] == {"profileId": "yok"}
    assert result["days"][1]["result"] == "applied"
    # Reddedilen gün haftalık saatlerle çalar.
    assert {t.payload["at"] for t in triggers(parts) if t.payload["date"] == "2026-10-12"} == \
        {"08:40", "09:30"}


# ------------------------------------------------------------- tetik üretimi


async def test_kip_off_profile_normal_tetikleri(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    saved = await service.save_profile(None, "Yarım gün", [
        {"time": "10:00", "kind": "ders"}, {"time": "10:40", "kind": "teneffus"}], actor="t")
    assert saved["ok"] and saved["id"] == "yarim-gun"

    body = window(
        day(TODAY, mode="off", reason="Cumhuriyet Bayramı"),
        day(TODAY + timedelta(days=1), mode="profile", profile="yarim-gun"),
        length=14)
    await service.apply_days(body, actor="ak")

    by_date: dict[str, set[str]] = {}
    for trigger in triggers(parts):
        by_date.setdefault(trigger.payload["date"], set()).add(trigger.payload["at"])
    assert "2026-10-12" not in by_date                        # tatil: tetik yok
    assert by_date["2026-10-13"] == {"10:00", "10:40"}        # saat seti
    # Pencere yedi gündür (12–18.10): gelecek pazartesi henüz kurulmaz; o güne
    # ait tetik gece yenilemesiyle gelir.
    assert set(by_date) == {"2026-10-13"}
    parts["clock"].today = TODAY + timedelta(days=7)
    await service.reschedule()
    assert {t.payload["at"] for t in triggers(parts)
            if t.payload["date"] == "2026-10-19"} == {"08:40", "09:30"}
    # Her hafta gününe tek tarih düşer; tetik etiketi tarihi taşır (çift tetik yok).
    labels = [t.label for t in parts["scheduler"].plans["bell"]]
    assert len(labels) == len(set(labels))


async def test_plan_yokken_bugunku_gibi_haftalik_calar(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    dates = {t.payload["date"] for t in triggers(parts)}
    assert dates == {"2026-10-12", "2026-10-13"}
    status = await service.plan_status("2026-10-12", "2026-10-25")
    assert {item["source"] for item in status["days"]} == {"none"}


async def test_tarih_kaymasi_tetigi_atlanir(store: Any, sounds: Path) -> None:
    """Hafta dönüşü: geçen pazartesinin tetiği bu pazartesi ateşlenirse ÇALMAZ."""
    service, parts = build(store, sounds)
    await mondays_bells(service)
    old = triggers(parts)[0]
    parts["clock"].today = TODAY + timedelta(days=7)       # gece yenilemesi kaçmış
    await fire(parts, old)
    assert parts["bridge"].sent == []
    assert "tarih bugün değil" in parts["log"].text()
    # Tablo yeniden kuruldu: artık yeni haftanın tarihlerini taşır.
    assert "2026-10-19" in {t.payload["date"] for t in triggers(parts)}


async def test_gece_yarisini_gecen_gonderim_tetigi(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await service.save_times({"tue": [{"time": "00:00", "kind": "ders"}]}, actor="t")
    trigger = triggers(parts)[0]
    assert (trigger.day, trigger.time) == ("mon", "23:59")
    assert trigger.payload["date"] == "2026-10-13"
    assert trigger.payload["fireDate"] == "2026-10-12"


# --------------------------------------------------------- çalma ve çağrılar


async def enable_calls(service: BellService, **extra: Any) -> None:
    settings = await service.settings()
    await service.save_settings({**settings, "sessionCalls": {
        **settings["sessionCalls"], "enabled": True, **extra}}, actor="t")


async def test_zil_anons_ve_cagrilar_sirayla(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await enable_calls(service, gapSeconds=2)
    await service.apply_days(push(day(TODAY, calls=[
        call("r1", "08:40", "TYT grubu"),
        call("r2", "08:40", "AYT grubu", teacher="Zeynep hoca"),
    ])), actor="ak")
    await drain(parts["voices"])

    trigger = next(t for t in triggers(parts) if t.payload["at"] == "08:40")
    await fire(parts, trigger)
    sent = parts["bridge"].sent[0]
    assert [item["kind"] for item in sent["items"]] == ["zil", "anons", "anons", "anons"]
    # Çağrılar grup adına göre: AYT önce; ikinci çağrının önünde boşluk.
    settings = await service.settings()
    ayt = await parts["voices"].ready_file(parts["voices"].key(
        session_text(settings, call("r2", "08:40", "AYT grubu", teacher="Zeynep hoca"))))
    assert sent["items"][2]["name"] == ayt
    assert "pauseBefore" not in sent["items"][2]
    assert sent["items"][3]["pauseBefore"] == 2
    assert sent["playAt"] == "2026-10-12T08:40:00+03:00"

    status = await service.plan_status("2026-10-12", "2026-10-12")
    assert status["days"][0]["calls"] == 2 and status["days"][0]["voicesPending"] == 0
    assert status["days"][0]["lastDispatchAt"]
    rows = await store.fetch_all(f"SELECT state FROM {store.table('call')}")
    assert {row["state"] for row in rows} == {"dispatched"}
    log = await store.fetch_all(f"SELECT kind FROM {store.table('log')} WHERE kind = 'cagri-plan'")
    assert log


async def test_oturum_cagrilari_kapaliyken_yalniz_zil(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await service.apply_days(push(day(TODAY, calls=[call("r1", "08:40", "TYT")])), actor="ak")
    await drain(parts["voices"])
    await fire(parts, next(t for t in triggers(parts) if t.payload["at"] == "08:40"))
    assert [item["kind"] for item in parts["bridge"].sent[0]["items"]] == ["zil", "anons"]


async def test_ses_hazir_degil_genel_o_da_yoksa_yalniz_zil(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await enable_calls(service)
    await drain(parts["voices"])
    settings = await service.settings()
    # Yalnız GENEL ses hazır: kesin metnin üretimi henüz bitmedi.
    await parts["voices"].require(generic_text(settings, "TYT", "group"))
    await drain(parts["voices"])
    parts["speech"].script = [RuntimeError("kota doldu")]
    await service.apply_days(push(day(TODAY, calls=[call("r1", "08:40", "TYT"),
                                                     call("r2", "08:40", "LGS")])), actor="ak")
    await drain(parts["voices"])

    await fire(parts, next(t for t in triggers(parts) if t.payload["at"] == "08:40"))
    items = parts["bridge"].sent[0]["items"]
    assert [item["kind"] for item in items] == ["zil", "anons", "anons"]   # LGS düştü
    generic = await parts["voices"].ready_file(
        parts["voices"].key(generic_text(settings, "TYT", "group")))
    assert items[2]["name"] == generic
    rows = {row["group_name"]: row for row in await store.fetch_all(
        f"SELECT group_name, state, detail FROM {store.table('call')}")}
    assert rows["TYT"]["state"] == "dispatched" and "genel" in rows["TYT"]["detail"]
    assert rows["LGS"]["state"] == "failed"
    failures = await store.fetch_all(
        f"SELECT * FROM {store.table('log')} WHERE kind = 'anons' AND ok = 0")
    assert failures, "çağrının neden çalmadığı günlüğe yazılmalı"


async def test_kapasite_asilinca_atlanir(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await enable_calls(service, maxPerSlot=2)
    await service.apply_days(push(day(TODAY, calls=[
        call(f"r{index}", "08:40", f"Grup {index}") for index in range(4)])), actor="ak")
    await drain(parts["voices"])
    await fire(parts, next(t for t in triggers(parts) if t.payload["at"] == "08:40"))
    assert len(parts["bridge"].sent[0]["items"]) == 2 + 2
    rows = await store.fetch_all(f"SELECT state, detail FROM {store.table('call')}")
    skipped = [row for row in rows if row["state"] == "skipped"]
    assert len(skipped) == 2 and skipped[0]["detail"] == "atlandı: kapasite"


async def test_yalniz_cagri_tetigi_ve_iptal_edilen_oturum(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await enable_calls(service, offsetSeconds=5)
    await service.apply_days(push(day(TODAY, revision=1, calls=[
        call("etut", "16:10", "İlayda", kind="individual")])), actor="ak")
    await drain(parts["voices"])
    only = [t for t in triggers(parts) if t.payload["at"] == "16:10"]
    assert len(only) == 1 and only[0].payload["bell"] is False
    await fire(parts, only[0])
    sent = parts["bridge"].sent[0]
    assert [item["kind"] for item in sent["items"]] == ["anons"]      # zil yok
    assert sent["playAt"] == "2026-10-12T16:10:05+03:00"            # kayma

    # Oturum iptal edildi: Akademi çağrısız günü iter, çağrı ve tetik düşer.
    await service.apply_days(push(day(TODAY, revision=2, calls=[])), actor="ak")
    assert not [t for t in triggers(parts) if t.payload["at"] == "16:10"]
    assert await store.fetch_all(f"SELECT id FROM {store.table('call')}") == []


async def test_ates_aninda_yerel_plan_yeniden_okunur(store: Any, sounds: Path) -> None:
    """Tetik kurulduktan sonra gelen acil kapatma da geçerli: zil çalmaz."""
    service, parts = build(store, sounds)
    await mondays_bells(service)
    trigger = next(t for t in triggers(parts) if t.payload["date"] == "2026-10-12")
    await service._plan.set_override(TODAY, "off", actor="t")     # tetik tablosu eski
    await fire(parts, trigger)
    assert parts["bridge"].sent == []


# ------------------------------------------------------------- acil kapatma


async def test_acil_kapatma_onceligi(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await service.apply_days(push(day(TODAY, revision=1)), actor="ak")
    with pytest.raises(PlanError) as caught:
        await service.override_day("2026-10-13", actor="Ayşe")
    assert caught.value.code == "override_today_only"

    await service.override_day("2026-10-12", actor="Ayşe")
    assert not [t for t in triggers(parts) if t.payload["date"] == "2026-10-12"]
    status = await service.plan_status("2026-10-12", "2026-10-12")
    assert status["days"][0]["source"] == "km"
    assert status["days"][0]["override"]["by"] == "Ayşe"

    # Akademi planı gelir: saklanır ama sonuç `overridden`; zil yine çalmaz.
    result = await service.apply_days(push(day(TODAY, revision=2)), actor="ak")
    assert result["days"][0]["result"] == "overridden"
    assert not [t for t in triggers(parts) if t.payload["date"] == "2026-10-12"]

    await service.clear_override("2026-10-12", actor="Ayşe")
    assert [t for t in triggers(parts) if t.payload["date"] == "2026-10-12"]
    status = await service.plan_status("2026-10-12", "2026-10-12")
    assert (status["days"][0]["source"], status["days"][0]["revision"]) == ("akademi", 2)


# -------------------------------------------------------------- ses ve köprü


async def test_needed_sounds_plan_seslerini_kapsar(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await service.apply_days(window(
        day(TODAY + timedelta(days=13), calls=[call("r9", "09:00", "TYT")]), length=14),
        actor="ak")
    await drain(parts["voices"])
    settings = await service.settings()
    exact = parts["voices"].key(session_text(settings, call("r9", "09:00", "TYT")))
    generic = parts["voices"].key(generic_text(settings, "TYT", "group"))
    wanted = {path.name for path in (await service.needed_sounds()).values()}
    assert await parts["voices"].ready_file(exact) in wanted
    assert await parts["voices"].ready_file(generic) in wanted


async def test_sablon_degisince_satir_ses_anahtarlari_yenilenir(store: Any,
                                                              sounds: Path) -> None:
    service, parts = build(store, sounds)
    await service.apply_days(push(day(TODAY, calls=[call("r1", "09:00", "TYT")])), actor="ak")
    settings = await service.settings()
    await service.save_settings({**settings, "texts": {
        **settings["texts"], "call": "{grup}, {hoca} seni bekliyor."}}, actor="t")
    row = (await store.fetch_all(f"SELECT voice_key FROM {store.table('call')}"))[0]
    assert row["voice_key"] == parts["voices"].key("TYT, Hüseyin hoca seni bekliyor.")


# ---------------------------------------------------------- saat setleri


async def test_saat_seti_kurallari_ve_hafta_yaniti(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    published: list[int] = []

    async def changed() -> None:
        published.append(1)

    service._on_week_change = changed
    bad = await service.save_profile(None, "Boş", [], actor="t")
    assert bad["ok"] is False
    made = await service.save_profile(None, "Deneme günü", [
        {"time": "9:00", "kind": "ders"}, {"time": "09:00"}, {"time": "11:00",
                                                              "kind": "teneffus"}], actor="t")
    assert made["ok"] and published
    twin = await service.save_profile(None, "deneme GÜNÜ", [{"time": "10:00"}], actor="t")
    assert twin["ok"] is False

    week = await service.week()
    assert week["profiles"] == [{"id": "deneme-gunu", "name": "Deneme günü", "times": [
        {"time": "09:00", "label": "", "kind": "ders"},
        {"time": "11:00", "label": "", "kind": "teneffus"}]}]
    assert [item["date"] for item in week["days"]][:2] == ["2026-10-12", "2026-10-13"]
    assert len(week["days"]) == 7

    await service.apply_days(push(day(TODAY + timedelta(days=1), mode="profile",
                                      profile="deneme-gunu")) | {"from": "2026-10-13"},
                             actor="ak")
    refused = await service.delete_profile("deneme-gunu", actor="t")
    assert refused["ok"] is False and "2026-10-13" in refused["detail"]
    del parts


# ------------------------------------------------------------ grup taşıma


async def test_gruplari_tasi_idempotent(store: Any, sounds: Path) -> None:
    groups = FakeGroups()
    service, parts = build(store, sounds, groups=groups)
    await service.add_group("TYT", actor="t")
    await service.add_group("İlayda", kind="ozel", actor="t")

    first = await service.migrate_groups(actor="Ayşe")
    assert first["ok"] and first["created"] == 2
    assert {g["name"]: g["kind"] for g in groups.groups} == {"TYT": "group",
                                                             "İlayda": "individual"}
    second = await service.migrate_groups(actor="Ayşe")
    assert second["created"] == 0 and len(groups.groups) == 2

    # Taşıma sonrası: zil grubu düzenleme kapalı, hafta yanıtında grup yok,
    # elle çağrı listesi öğrenci gruplarından; satırlar SİLİNMEDİ.
    assert (await service.add_group("Yeni", actor="t"))["ok"] is False
    assert (await service.week())["groups"] == []
    state = await service.state()
    assert state["groupsMigrated"] is True
    assert [g["name"] for g in state["studentGroups"]] == ["İlayda", "TYT"]
    rows = await store.fetch_all(f"SELECT student_group_id FROM {store.table('group')}")
    assert len(rows) == 2 and all(row["student_group_id"] for row in rows)
    # Ayar ekranından taşıma damgası geri alınamaz.
    settings = await service.settings()
    await service.save_settings({**settings, "groupsMigratedAt": ""}, actor="t")
    assert (await service.settings())["groupsMigratedAt"]
    del parts


async def test_tasima_sonrasi_elle_cagri(store: Any, sounds: Path) -> None:
    groups = FakeGroups()
    service, parts = build(store, sounds, groups=groups)
    await service.add_group("TYT", actor="t")
    await service.migrate_groups(actor="Ayşe")
    group_id = groups.groups[0]["id"]
    await drain(parts["voices"])

    # Planda oturum yok: genel metin.
    result = await service.call_group(group_id, actor="Ayşe")
    assert result["ok"]
    settings = await service.settings()
    generic = await parts["voices"].ready_file(
        parts["voices"].key(generic_text(settings, "TYT", "group")))
    assert parts["bridge"].sent[-1]["items"][0]["name"] == generic
    assert parts["bridge"].sent[-1]["items"][0]["kind"] == "anons"   # zil yok

    # Bugün 08:20'de oturum var (±30 dk): oturumun öğretmenli metni.
    await service.apply_days(push(day(TODAY, calls=[
        call("r1", "08:20", "TYT", group_ref=f"km_group:{group_id}",
             teacher="Zeynep hoca")])), actor="ak")
    await drain(parts["voices"])
    await service.call_group(group_id, actor="Ayşe")
    exact = await parts["voices"].ready_file(parts["voices"].key(session_text(
        settings, call("r1", "08:20", "TYT", teacher="Zeynep hoca"))))
    assert parts["bridge"].sent[-1]["items"][0]["name"] == exact


class ClassViewGroups(FakeGroups):
    """Sınıf = grup sonrası `bbd_students.groups`: eski `className`'den dönüşen sınıf yalnız
    `view="classes"` görünümünde gelir."""

    def __init__(self) -> None:
        super().__init__()
        self.views: list[str] = []

    async def list(self, since_revision: int | None = None, *,  # type: ignore[override]
                   view: str = "legacy") -> dict[str, Any]:
        self.views.append(view)
        listing = await super().list(since_revision)
        if view == "classes":
            listing["groups"].append({"id": "c11a", "name": "11-A", "callName": "", "kind": "group",
                                      "archived": False, "members": [], "legacyClassName": "11-A"})
        return listing


async def test_tasima_sonrasi_elle_cagri_butun_siniflar(store: Any, sounds: Path) -> None:
    # Sınıf = grup (iş 23): elle çağrı listesi dönüşen sınıfları da içerir (Akademi planı onları
    # `km_group:<id>` ile çağırır).
    groups = ClassViewGroups()
    service, parts = build(store, sounds, groups=groups)
    await service.add_group("TYT", actor="t")
    await service.migrate_groups(actor="Ayşe")
    state = await service.state()
    assert [g["name"] for g in state["studentGroups"]] == ["11-A", "TYT"]
    assert "classes" in groups.views
    del parts


async def test_plan_yetenegi_sozlesme_yuzu(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    capability = BellPlan(service)
    result = await capability.apply_days(push(day(TODAY)), actor="akademi:bell-plan")
    assert set(result) == {"days", "voicesPending", "bellEnabled"}
    status = await capability.status("2026-10-12", "2026-10-13")
    assert set(status) == {"bellEnabled", "sessionCallsEnabled", "agent", "days"}
    assert set(status["days"][0]) >= {"date", "mode", "source", "revision", "planHash",
                                      "calls", "voicesReady", "voicesPending",
                                      "lastDispatchAt", "override"}
    with pytest.raises(ValueError) as caught:
        await capability.status("2026-10-01", "2026-11-15")
    assert getattr(caught.value, "code", None) == "validation_error"
    assert planning.STATUS_MAX_DAYS == 31


# ---------------------------------------------------------------- izinler


def _declared(route: Any) -> set[str]:
    found: set[str] = set()
    for param in route.dependant.dependencies:
        for cell in getattr(param.call, "__closure__", None) or ():
            content = cell.cell_contents
            if isinstance(content, tuple) and all(isinstance(x, str) for x in content):
                found.update(content)
    return found


def test_yeni_uclar_izin_ilan_eder_acil_kapatma_pin_ister() -> None:
    expected = {
        ("POST", "/profiles"): {"bell.manage"},
        ("PUT", "/profiles/{profile_id}"): {"bell.manage"},
        ("POST", "/profiles/{profile_id}/delete"): {"bell.manage"},
        ("GET", "/plan/status"): {"bell.view"},
        ("POST", "/plan/override"): {"bell.manage"},
        ("POST", "/plan/override/clear"): {"bell.manage"},
        ("POST", "/groups/migrate"): {"bell.manage"},
    }
    found = {}
    for route in routes.router.routes:
        for method in route.methods:  # type: ignore[attr-defined]
            found[(method, route.path)] = route  # type: ignore[attr-defined]
    for key, permissions in expected.items():
        assert _declared(found[key]) == permissions, key
    for key, route in found.items():
        assert _declared(route), f"{key} izin ilan etmiyor (K9)"
    assert "confirm_pin(" in inspect.getsource(routes.override_today)


async def test_silinen_sinifin_planli_cagrilari_iptal(store: Any, sounds: Path) -> None:
    # İş 30: Öğrenci Yönetimi arşivdeki sınıfı kalıcı sildi → o sınıfın bugün ve sonrasındaki
    # planlı çağrıları `cancelled` (çalınmaz, satır kalır), diğer sınıfınkiler sürer.
    service, parts = build(store, sounds)
    await mondays_bells(service)
    await service.apply_days(push(day(TODAY, calls=[
        call("r1", "08:20", "TYT", group_ref="km_group:g-sil"),
        call("r2", "08:20", "LGS", group_ref="km_group:g-kal")])), actor="ak")
    assert await service.group_deleted("g-sil") == 1
    rows = {row["ref"]: row for row in await service._plan.calls(TODAY, TODAY)}
    assert rows["r1"]["state"] == "cancelled" and "silindi" in rows["r1"]["detail"]
    assert rows["r2"]["state"] == "planned"
    assert [row["ref"] for row in await service._plan.calls_at(TODAY, "08:20")] == ["r2"]
    assert await service.group_deleted("g-sil") == 0
    del parts
