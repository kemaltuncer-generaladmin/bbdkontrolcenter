"""Program görünümü, zil bilgisayarının cevabı ve "bu bilgisayardan da çal" (0.5.x).

KM zil düzeltmesi (13.10.2026, kullanıcı: "görünüm, program, zil saatlerinin
düzeltilmesi — bilgisayara dönüşü yok"):

  · Ekranın "sıradaki zil"i zamanlayıcının iç tetiğinden okunuyordu (bir
    dakika erken "· gönderim" etiketi); Akademi'nin tatil günleri ve saat
    setleri programda görünmüyordu → `schedule_view` tetik tablosuyla AYNI
    kuraldan üretilir.
  · Günlük yalnız "köprü kuyruğuna yazıldı" diyordu; zil bilgisayarının
    çalıp çalmadığı görünmüyordu → `lastAck` günlükteki komuta işlenir.
  · Sunucuda (ADR 0026) ses çalıcı yok; "bu bilgisayardan da çal" açıkken
    ajan arızası gizleniyor, her zilde yerel tetik boşa koşuyordu.

Depo gerçek SQLite (bütün göçler), köprü/zamanlayıcı/ses taklit — AĞA ÇIKILMAZ.
"Bugün" pazartesi 12.10.2026, saat 08:00 (İstanbul).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from bell_fakes import FakeAudio
from test_bell_plan import MIGRATIONS, TODAY, build, day, window

from km_core.store.dialect import concurrent_index_plan, translate_ddl


async def week(service: Any) -> None:
    await service.save_times({
        "mon": [{"time": "08:40", "kind": "ders", "label": "1. ders"},
                {"time": "07:30", "kind": "ders"},
                {"time": "10:00", "kind": "teneffus"}],
        "tue": [{"time": "09:00", "kind": "ders"}],
    }, actor="t")


def test_goc_008_009_lehce_cevirmeninden_gecer() -> None:
    translate_ddl((MIGRATIONS / "008_log_agent.sql").read_text(encoding="utf-8"))
    plan = concurrent_index_plan(
        (MIGRATIONS / "009_log_agent_index.sql").read_text(encoding="utf-8"))
    assert plan is not None and [name for name, _ in plan] == ["mod_bell_log_command"]


# ------------------------------------------------------------------ program


async def test_program_bugun_ve_siradaki_zil(store: Any, sounds: Path) -> None:
    service, _parts = build(store, sounds)
    await week(service)
    view = await service.schedule_view(await service.settings())

    assert view["today"] == "2026-10-12"
    today = view["days"][0]
    assert today["weekday"] == "mon" and today["mode"] == "normal"
    # Saat sırası; geçmiş zil işaretli (saat 08:00).
    assert [(bell["time"], bell["past"]) for bell in today["bells"]] == [
        ("07:30", True), ("08:40", False), ("10:00", False)]
    # Sıradaki zil GERÇEK zil saatidir — iç gönderim tetiği (08:39) değil.
    first = view["next"][0]
    assert (first["date"], first["time"], first["kind"], first["label"]) == (
        "2026-10-12", "08:40", "ders", "1. ders")
    assert first["inMinutes"] == 40
    assert [item["time"] for item in view["next"]] == ["08:40", "10:00", "09:00"]
    assert len(view["days"]) == 7


async def test_program_akademi_tatili_ve_saat_seti(store: Any, sounds: Path) -> None:
    service, _parts = build(store, sounds)
    await week(service)
    await service.save_profile(None, "Yarım gün", [{"time": "10:00", "kind": "ders"}],
                               actor="t")
    await service.apply_days(window(
        day(TODAY, mode="off", reason="Cumhuriyet Bayramı"),
        day(TODAY + timedelta(days=1), mode="profile", profile="yarim-gun"),
        length=14), actor="ak")

    view = await service.schedule_view(await service.settings())
    off, special = view["days"][0], view["days"][1]
    assert (off["mode"], off["reason"], off["source"], off["bells"]) == (
        "off", "Cumhuriyet Bayramı", "akademi", [])
    assert (special["mode"], special["profileName"]) == ("profile", "Yarım gün")
    assert [bell["time"] for bell in special["bells"]] == ["10:00"]
    # Tatil gününde sıradaki zil ertesi güne düşer.
    assert view["next"][0]["date"] == "2026-10-13"


async def test_program_ana_salter_kapaliyken_siradaki_zil_yok(store: Any,
                                                               sounds: Path) -> None:
    service, _parts = build(store, sounds)
    await week(service)
    settings = await service.settings()
    await service.save_settings({**settings, "enabled": False}, actor="t")
    view = await service.schedule_view(await service.settings())
    assert view["next"] == []
    assert view["days"][0]["bells"], "program yine görünür, yalnız çalmaz"


async def test_program_acil_kapatma_gunu(store: Any, sounds: Path) -> None:
    service, _parts = build(store, sounds)
    await week(service)
    await service.override_day(TODAY.isoformat(), "off", actor="Hasan")
    today = (await service.schedule_view(await service.settings()))["days"][0]
    assert (today["mode"], today["source"], today["override"], today["bells"]) == (
        "off", "km", True, [])


# -------------------------------------------------- zil bilgisayarının cevabı


async def test_ajanin_cevabi_gunluge_islenir(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await week(service)
    await service.ring_now(actor="Hasan")
    bridge = parts["bridge"]
    assert len(bridge.sent) == 1

    state = await service.state()
    row = state["log"][0]
    assert (row["command_id"], row["agent_ok"]) == (1, -1)       # cevap henüz yok

    bridge.last_ack = {"id": 1, "ok": True, "detail": "1 ses çaldı",
                       "at": "2026-10-12T08:00:04+03:00"}
    state = await service.state()
    row = state["log"][0]
    assert (row["agent_ok"], row["agent_detail"]) == (1, "1 ses çaldı")
    assert state["agentAck"] == {"id": 1, "ok": True, "detail": "1 ses çaldı",
                                 "at": "2026-10-12T08:00:04+03:00"}


async def test_atlanan_komut_kirmizi_ve_cevap_tekrar_yazilmaz(store: Any,
                                                              sounds: Path) -> None:
    service, parts = build(store, sounds)
    await week(service)
    await service.ring_now(actor="t")
    parts["bridge"].last_ack = {"id": 1, "ok": False,
                                "detail": "zamanı 26 sn geçmiş, atlandı"}
    await service.state()
    # Aynı numara ikinci kez işlenmez; sonradan değişen cevap satırı ezmez.
    parts["bridge"].last_ack = {"id": 1, "ok": True, "detail": "x"}
    service._acked_id = 0
    state = await service.state()
    assert (state["log"][0]["agent_ok"], state["log"][0]["agent_detail"]) == (
        0, "zamanı 26 sn geçmiş, atlandı")


async def test_cevap_satirdan_once_gelirse_sonra_islenir(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    parts["bridge"].last_ack = {"id": 7, "ok": True, "detail": "1 ses çaldı"}
    await service.note_ack(await parts["bridge"].try_status())
    assert service._acked_id == 0                     # satır yok: yeniden denenecek


async def test_eslitleme_dongusu_kitaplik_sayisini_verir(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await week(service)
    state = await service.state()
    assert state["library"] is None                    # eşitleme henüz dönmedi
    await service.sync_sounds()
    wanted = set(parts["bridge"].uploaded)
    parts["bridge"].agent_sounds = sorted(wanted)[:1]
    state = await service.state()
    assert state["library"] == {"wanted": len(wanted), "present": 1}


# ------------------------------------------------ bu bilgisayardan da çal


async def test_calici_yoksa_yerel_cal_ajan_arizasini_gizlemez(store: Any,
                                                              sounds: Path) -> None:
    """Sunucuda (ADR 0026) ses çalıcı yok: "yerel" ayarı açık olsa da yerel
    tetik kurulmaz, elle zilde yerel çalma denenmez, ajan çevrimdışıyken ekran
    "hazır" demez."""
    service, parts = build(store, sounds, play_locally=True)
    audio = FakeAudio(sounds, ready=False)
    service._audio = audio
    parts["bridge"].online = False
    await week(service)

    assert {t.payload["phase"] for t in parts["scheduler"].plans["bell"]} == {"dispatch"}
    await service.ring_now(actor="t")
    assert audio.played == []
    state = await service.state()
    assert state["localAvailable"] is False
    assert state["ready"] is False


async def test_calici_varsa_yerel_cal_eskisi_gibi(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds, play_locally=True)
    await week(service)
    assert {t.payload["phase"] for t in parts["scheduler"].plans["bell"]} == {"dispatch",
                                                                             "local"}
    await service.ring_now(actor="t")
    assert parts["audio"].played
    assert (await service.state())["localAvailable"] is True
