"""Zil servisi — ayar, saatler, gruplar, tetikleyiciler ve çalma."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from bell_backend.service import (
    DEFAULT_CALL_TEXT,
    DEFAULT_LESSON_TEXT,
    DEFAULT_SOLO_TEXT,
    BellService,
    _shift,
    _slug,
    call_text,
    clock,
)
from bell_backend.voices import VoiceLibrary, render
from bell_fakes import (
    FakeAudio,
    FakeBridge,
    FakeLog,
    FakeScheduler,
    FakeSecrets,
    FakeSpeech,
)


def build(store: Any, sounds: Path, *, speech: FakeSpeech | None = None,
          bridge: FakeBridge | None = None,
          audio: FakeAudio | None = None,
          scheduler: FakeScheduler | None = None,
          play_locally: bool = True) -> tuple[BellService, dict[str, Any]]:
    log = FakeLog()
    parts = {
        "audio": audio or FakeAudio(sounds),
        "scheduler": scheduler or FakeScheduler(),
        "bridge": bridge or FakeBridge(),
        "speech": speech or FakeSpeech(),
        "log": log,
    }
    parts["voices"] = VoiceLibrary(
        store=store, log=log, speech=parts["speech"],
        sounds_path=sounds, config={"min_interval_seconds": 0},
    )
    service = BellService(
        store=store, log=log, audio=parts["audio"], scheduler=parts["scheduler"],
        voices=parts["voices"], bridge=parts["bridge"], play_locally=play_locally,
    )
    return service, parts


async def drain(voices: VoiceLibrary) -> None:
    """Üretim kuyruğu boşalana kadar bekler."""
    await voices._queue.join()


# ------------------------------------------------------------------ saf işlev


@pytest.mark.parametrize(("raw", "expected"), [
    ("9:05", "09:05"), ("09:05", "09:05"), ("23:59", "23:59"),
    ("24:00", None), ("9:60", None), ("", None), ("abc", None), ("9", None),
])
def test_clock(raw: str, expected: str | None) -> None:
    assert clock(raw) == expected


def test_shift_gun_sinirini_gecer() -> None:
    # 00:30'dan 60 dakika geri gitmek bir önceki güne düşer — pazartesinin
    # ilk zili için gönderim tetikleyicisi PAZAR akşamına kurulmalı.
    assert _shift("mon", "00:30", 60) == ("sun", "23:30")
    assert _shift("mon", "08:40", 60) == ("mon", "07:40")


def test_slug_turkce_harfleri_cozer() -> None:
    assert _slug("İlayda Şişman", set()) == "ilayda-sisman"
    assert _slug("TYT/AYT Özel", set()) == "tyt-ayt-ozel"
    # Çakışma sayıyla ayrılır; iki grup aynı kimliği taşıyamaz.
    assert _slug("Genel", {"genel"}) == "genel-2"


def test_render_grup_adini_yerlestirir() -> None:
    assert render("{grup}, dersiniz başlıyor.", "İlayda") == "İlayda, dersiniz başlıyor."
    # Yer tutucusuz şablon reddedilmez: kullanıcı sabit cümle yazabilir.
    assert render("Herkes derse.", "İlayda") == "Herkes derse."


# --------------------------------------------------------------------- ayar


async def test_varsayilan_ayar(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    settings = await service.settings()
    assert settings["enabled"] is True
    assert settings["texts"]["lesson"] == DEFAULT_LESSON_TEXT
    assert settings["texts"]["call"] == DEFAULT_CALL_TEXT


async def test_normalize_bozuk_veriyi_toparlar(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    clean = service.normalize({"volume": 999, "texts": "metin değil", "enabled": False,
                               "groups": {"eski": {}}})
    assert clean["volume"] == 100
    assert clean["enabled"] is False
    assert clean["texts"]["lesson"] == DEFAULT_LESSON_TEXT
    # 0.1'in `groups` bloğu taşınmaz, sessizce düşer.
    assert "groups" not in clean


async def test_metin_degisince_ses_yeniden_uretilir(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await service.add_group("İlayda", actor="test")
    await drain(parts["voices"])
    ilk = len(parts["speech"].calls)

    await service.save_settings(
        {"texts": {"lesson": "Ders başlıyor.", "call": DEFAULT_CALL_TEXT}}, actor="test"
    )
    await drain(parts["voices"])
    assert len(parts["speech"].calls) == ilk + 1
    assert "Ders başlıyor." in parts["speech"].calls


async def test_cagri_sablonu_degisince_her_grup_yeniden_uretilir(
    store: Any, sounds: Path
) -> None:
    service, parts = build(store, sounds)
    for name in ("İlayda", "TYT AYT", "Sayısal"):
        await service.add_group(name, actor="test")
    await drain(parts["voices"])
    parts["speech"].calls.clear()

    await service.save_settings(
        {"texts": {"lesson": DEFAULT_LESSON_TEXT, "call": "{grup}, hadi derse.",
                   "solo": DEFAULT_SOLO_TEXT}},
        actor="test",
    )
    await drain(parts["voices"])
    assert sorted(parts["speech"].calls) == [
        "Sayısal, hadi derse.", "TYT AYT, hadi derse.", "İlayda, hadi derse.",
    ]


# ------------------------------------------------------------------ saatler


async def test_saatler_temizlenir_ve_tekrarlar_dusur(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    await service.save_times({
        "mon": [{"time": "8:40", "label": "teneffüs"},
                {"time": "08:40", "label": "kopya"},      # aynı saat — düşer
                {"time": "25:00"},                        # geçersiz — düşer
                {"time": "09:30", "label": "x" * 90}],    # etiket kırpılır
        "pazartesi": [{"time": "10:00"}],                 # bilinmeyen gün — düşer
    }, actor="test")

    week = await service.times()
    assert [item["time"] for item in week["mon"]] == ["08:40", "09:30"]
    assert len(week["mon"][1]["label"]) == 40
    assert week["tue"] == []


async def test_saat_kaydi_tumuyle_degistirir(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    await service.save_times({"mon": [{"time": "08:40"}, {"time": "09:30"}]}, actor="t")
    await service.save_times({"tue": [{"time": "10:00"}]}, actor="t")

    week = await service.times()
    assert week["mon"] == []
    assert [item["time"] for item in week["tue"]] == ["10:00"]


# ------------------------------------------------------------------ gruplar


async def test_grup_ekleme_ve_ses_istegi(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    result = await service.add_group("  İlayda   Şişman ", actor="test")
    assert result["ok"] is True
    await drain(parts["voices"])

    groups = await service.groups()
    assert [item["name"] for item in groups] == ["İlayda Şişman"]
    assert parts["speech"].calls == [
        render(DEFAULT_CALL_TEXT, "İlayda Şişman")
    ]
    # Tür verilmediyse varsayılan "grup" — çoğul hitap.
    assert groups[0]["kind"] == "grup"


async def test_ozel_ders_tekil_hitap_eder(store: Any, sounds: Path) -> None:
    """Tek öğrenciye "dersiniz başlıyor" demek yanlış.

    Bu ayrım grubun ADINDAN tahmin edilemez — "LGS" ile "Zehra" arasındaki
    farkı hiçbir kural güvenilir bilemez. Tür açıkça saklanır.
    """
    service, parts = build(store, sounds)
    await service.add_group("Zehra", kind="ozel", actor="t")
    await service.add_group("LGS", kind="grup", actor="t")
    await drain(parts["voices"])

    uretilen = sorted(parts["speech"].calls)
    assert uretilen == sorted([
        render(DEFAULT_SOLO_TEXT, "Zehra"),
        render(DEFAULT_CALL_TEXT, "LGS"),
    ])
    # Somut kontrol: özel derste tekil, grupta çoğul.
    assert "özel dersin başlıyor" in render(DEFAULT_SOLO_TEXT, "Zehra")
    assert "dersiniz başlıyor" in render(DEFAULT_CALL_TEXT, "LGS")


async def test_gecersiz_tur_gruba_duser(store: Any, sounds: Path) -> None:
    """Tanınmayan tür sessizce kabul edilmez; güvenli tarafa düşer."""
    service, _ = build(store, sounds)
    await service.add_group("Deneme", kind="saçmalık", actor="t")
    assert (await service.groups())[0]["kind"] == "grup"


async def test_ad_degistirmek_turu_sifirlamaz(store: Any, sounds: Path) -> None:
    """Ekrandan ad düzeltmek, özel dersi gruba çevirmemeli."""
    service, parts = build(store, sounds)
    added = await service.add_group("Zehra", kind="ozel", actor="t")
    await drain(parts["voices"])
    parts["speech"].calls.clear()

    await service.rename_group(added["id"], "Zehra Yılmaz", actor="t")
    await drain(parts["voices"])

    assert (await service.groups())[0]["kind"] == "ozel"
    assert parts["speech"].calls == [render(DEFAULT_SOLO_TEXT, "Zehra Yılmaz")]


async def test_tur_degistirilebilir(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    added = await service.add_group("Hidayet", kind="grup", actor="t")
    await drain(parts["voices"])
    parts["speech"].calls.clear()

    await service.rename_group(added["id"], "Hidayet", kind="ozel", actor="t")
    await drain(parts["voices"])

    assert (await service.groups())[0]["kind"] == "ozel"
    assert parts["speech"].calls == [render(DEFAULT_SOLO_TEXT, "Hidayet")]


async def test_ozel_metin_degisince_yalniz_ozel_dersler_uretilir(
    store: Any, sounds: Path
) -> None:
    """Özel ders metnini düzeltmek, on grubun sesini boşuna üretmemeli."""
    service, parts = build(store, sounds)
    await service.add_group("LGS", kind="grup", actor="t")
    await service.add_group("Zehra", kind="ozel", actor="t")
    await drain(parts["voices"])
    parts["speech"].calls.clear()

    settings = await service.settings()
    await service.save_settings(
        {**settings, "texts": {**settings["texts"], "solo": "{grup}, hocaya gel."}},
        actor="t",
    )
    await drain(parts["voices"])

    # LGS'nin metni değişmedi → önbellekten geldi, çağrı doğurmadı.
    assert parts["speech"].calls == ["Zehra, hocaya gel."]


async def test_call_text_dogru_sablonu_secer(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    settings = await service.settings()
    assert call_text(settings, "LGS", "grup") == render(DEFAULT_CALL_TEXT, "LGS")
    assert call_text(settings, "Zehra", "ozel") == render(DEFAULT_SOLO_TEXT, "Zehra")


async def test_ayni_ad_iki_kez_eklenemez(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    await service.add_group("İlayda", actor="test")
    result = await service.add_group("ilayda", actor="test")   # büyük/küçük farkı yok
    assert result["ok"] is False
    assert "zaten var" in result["detail"]


async def test_bos_ad_reddedilir(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    assert (await service.add_group("   ", actor="test"))["ok"] is False


async def test_kaldirilan_grup_satiri_silinmez_ve_geri_gelir(
    store: Any, sounds: Path
) -> None:
    service, parts = build(store, sounds)
    added = await service.add_group("İlayda", actor="test")
    await drain(parts["voices"])
    await service.remove_group(added["id"], actor="test")
    assert await service.groups() == []

    # Satır duruyor, yalnız `deleted_at` yazılmış.
    rows = await store.fetch_all("SELECT id, deleted_at FROM mod_bell_group")
    assert len(rows) == 1
    assert rows[0]["deleted_at"] != ""

    # Aynı adla geri eklenince ESKİ satır canlanır, ikinci kimlik doğmaz.
    parts["speech"].calls.clear()
    again = await service.add_group("İlayda", actor="test")
    await drain(parts["voices"])
    assert again["id"] == added["id"]
    assert len(await store.fetch_all("SELECT id FROM mod_bell_group")) == 1
    # Metin aynı olduğu için ses de yeniden ÜRETİLMEZ.
    assert parts["speech"].calls == []


async def test_ad_degisince_ses_uretilir_eskisi_durur(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    added = await service.add_group("İlayda", actor="test")
    await drain(parts["voices"])

    await service.rename_group(added["id"], "İlayda Şişman", actor="test")
    await drain(parts["voices"])

    # İki ses de önbellekte: eski ada dönülürse yeniden üretilmesin.
    rows = await store.fetch_all("SELECT text FROM mod_bell_voice")
    texts = {str(row["text"]) for row in rows}
    assert render(DEFAULT_CALL_TEXT, "İlayda") in texts
    assert render(DEFAULT_CALL_TEXT, "İlayda Şişman") in texts


# ------------------------------------------------------------- zamanlama


async def test_reschedule_her_saat_icin_iki_tetikleyici(store: Any, sounds: Path) -> None:
    scheduler = FakeScheduler()
    service, _ = build(store, sounds, scheduler=scheduler)
    await service.save_times({"mon": [{"time": "08:40"}, {"time": "09:30"}]}, actor="t")

    triggers = scheduler.plans["bell"]
    assert len(triggers) == 4          # 2 saat × (gönderim + yerel)
    # lead_seconds = 60 → gönderim zilden BİR DAKİKA önce.
    dispatch = [t for t in triggers if t.payload["phase"] == "dispatch"]
    assert {(t.day, t.time) for t in dispatch} == {("mon", "08:39"), ("mon", "09:29")}
    local = [t for t in triggers if t.payload["phase"] == "local"]
    assert {(t.day, t.time) for t in local} == {("mon", "08:40"), ("mon", "09:30")}


async def test_yerel_calma_kapaliysa_tek_tetikleyici(store: Any, sounds: Path) -> None:
    scheduler = FakeScheduler()
    service, _ = build(store, sounds, scheduler=scheduler, play_locally=False)
    await service.save_times({"mon": [{"time": "08:40"}]}, actor="t")
    assert len(scheduler.plans["bell"]) == 1


async def test_ana_salter_kapaliysa_plan_temizlenir(store: Any, sounds: Path) -> None:
    scheduler = FakeScheduler()
    service, _ = build(store, sounds, scheduler=scheduler)
    await service.save_times({"mon": [{"time": "08:40"}]}, actor="t")
    await service.save_settings({"enabled": False}, actor="t")
    assert "bell" in scheduler.cleared


async def test_tetikleyici_zil_ve_anonsu_tek_komutta_yollar(
    store: Any, sounds: Path
) -> None:
    scheduler = FakeScheduler()
    bridge = FakeBridge()
    service, parts = build(store, sounds, scheduler=scheduler, bridge=bridge)
    await service.bootstrap()
    await drain(parts["voices"])
    await service.save_times({"mon": [{"time": "08:40"}]}, actor="t")

    index = next(i for i, t in enumerate(scheduler.plans["bell"])
                 if t.payload["phase"] == "dispatch")
    await scheduler.fire("bell", index)

    assert len(bridge.sent) == 1
    kinds = [item["kind"] for item in bridge.sent[0]["items"]]
    assert kinds == ["zil", "anons"]       # sıra önemli: önce zil, sonra anons
    # DAMGA OFFSET TAŞIR: `_next_occurrence` `now.astimezone()` kullanır ve
    # `2026-08-16T08:40:00+03:00` üretir. Offset kasıtlıdır — yaz saati
    # geçişinde "08:40" tek başına iki ayrı anı gösterebilir; ajan damgayı
    # `datetime.fromisoformat` ile okuduğu için offseti sorunsuz çözer.
    # Bu yüzden sonek değil, DUVAR SAATİ sınanır.
    play_at = bridge.sent[0]["playAt"]
    assert play_at == "" or datetime.fromisoformat(play_at).strftime("%H:%M:%S") == "08:40:00"


# ------------------------------------------------------------------- çalma


async def test_elle_zil_anons_calmaz(store: Any, sounds: Path) -> None:
    bridge = FakeBridge()
    service, parts = build(store, sounds, bridge=bridge)
    await service.bootstrap()
    await drain(parts["voices"])

    result = await service.ring_now(actor="Ahmet")
    assert result["ok"] is True
    assert [item["kind"] for item in bridge.sent[0]["items"]] == ["zil"]


async def test_grup_cagrisi_zil_calmaz(store: Any, sounds: Path) -> None:
    bridge = FakeBridge()
    service, parts = build(store, sounds, bridge=bridge)
    added = await service.add_group("İlayda", actor="t")
    await drain(parts["voices"])

    result = await service.call_group(added["id"], actor="Ahmet")
    assert result["ok"] is True
    assert [item["kind"] for item in bridge.sent[0]["items"]] == ["anons"]


async def test_sesi_hazir_olmayan_grup_cagirilmaz(store: Any, sounds: Path) -> None:
    """Sessizlikle biten bir çağrı, hiç yapılmayan çağrıdan kötüdür."""
    from bell_backend.speech import SpeechError

    speech = FakeSpeech(script=[SpeechError("kota bitti", retryable=False)])
    bridge = FakeBridge()
    service, parts = build(store, sounds, speech=speech, bridge=bridge)
    added = await service.add_group("İlayda", actor="t")
    await drain(parts["voices"])

    result = await service.call_group(added["id"], actor="Ahmet")
    assert result["ok"] is False
    assert "kota bitti" in result["detail"]
    assert bridge.sent == []            # ajana hiç gitmedi

    rows = await store.fetch_all("SELECT ok, kind, detail FROM mod_bell_log")
    assert rows[-1]["ok"] == 0
    assert rows[-1]["kind"] == "cagri"


async def test_kopru_coktuyse_yerel_calma_isi_kurtarir(store: Any, sounds: Path) -> None:
    bridge = FakeBridge()
    bridge.error = "Köprüye ulaşılamadı"
    audio = FakeAudio(sounds)
    service, parts = build(store, sounds, bridge=bridge, audio=audio)
    await service.bootstrap()
    await drain(parts["voices"])

    result = await service.ring_now(actor="Ahmet")
    assert result["ok"] is True                    # yerelden çaldı
    assert audio.played and audio.played[0][0] == "classic_electric.wav"
    assert "Köprüye ulaşılamadı" in result["detail"]


async def test_her_iki_hedef_de_dusunce_gunluge_yazilir(store: Any, sounds: Path) -> None:
    bridge = FakeBridge()
    bridge.error = "köprü yok"
    audio = FakeAudio(sounds)
    audio.fail = "ses aygıtı yok"
    service, parts = build(store, sounds, bridge=bridge, audio=audio)
    await service.bootstrap()
    await drain(parts["voices"])

    result = await service.ring_now(actor="Ahmet")
    assert result["ok"] is False
    rows = await store.fetch_all("SELECT ok, detail FROM mod_bell_log ORDER BY id DESC")
    assert rows[0]["ok"] == 0


async def test_onizleme_ajana_gitmez_ve_sunucuda_calmaz(store: Any, sounds: Path) -> None:
    """Ekranda "dinle" derken okulun hoparlöründen zil çalmamalı.

    ÇALMA DA YAPILMAZ (ADR 0026): backend sunucuda koşuyor ve orada hoparlör
    yok. Uç sesin ADRESİNİ verir, çalma işi kabuğundur. `audio.played` boş
    kalmalı — dolu olsaydı ses veri merkezinde, yani hiç kimsenin duymadığı
    bir yerde çalınıyor demekti.
    """
    bridge = FakeBridge()
    audio = FakeAudio(sounds)
    service, _ = build(store, sounds, bridge=bridge, audio=audio)

    result = await service.preview_source("classic_electric")
    assert result["ok"] is True
    assert result["name"] == "classic_electric.wav"
    assert result["dataUri"].startswith("data:audio/wav;base64,")
    assert audio.played == []
    assert bridge.sent == []


async def test_onizleme_bilinmeyen_sesi_reddeder(store: Any, sounds: Path) -> None:
    """Ad çözülür, birleştirilmez: `../` ile klasör dışına çıkılamaz."""
    service, _ = build(store, sounds, bridge=FakeBridge(), audio=FakeAudio(sounds))
    assert (await service.preview_source("../../etc/passwd"))["ok"] is False


# --------------------------------------------------------------- ajan senkronu


async def test_eksik_sesler_koprüye_yuklenir(store: Any, sounds: Path) -> None:
    bridge = FakeBridge()
    service, parts = build(store, sounds, bridge=bridge)
    await service.add_group("İlayda", actor="t")
    await service.bootstrap()
    await drain(parts["voices"])

    result = await service.sync_sounds()
    assert result["ok"] is True
    # zil + ders anonsu + grup anonsu = 3
    assert result["total"] == 3
    assert result["uploaded"] == 3
    assert len(bridge.uploaded) == 3

    # İkinci turda hiçbir şey yüklenmez: içerik adresli, ajanda zaten var.
    again = await service.sync_sounds()
    assert again["uploaded"] == 0
    assert len(bridge.uploaded) == 3


async def test_ajan_hic_baglanmadiysa_bile_tekrar_yuklenmez(
    store: Any, sounds: Path
) -> None:
    """Karşılaştırma KÖPRÜYLE yapılır, ajanla değil.

    Ajan hiç bağlanmamışken kendi ses listesi boştur. Eşitleme ona bakarsaydı
    her turda bütün sesleri yeniden yükler, ajan kurulana dek her dakika
    boşuna trafik üretirdi.
    """
    bridge = FakeBridge()
    bridge.agent_sounds = []            # ajan daha hiç görünmedi
    service, parts = build(store, sounds, bridge=bridge)
    await service.add_group("İlayda", actor="t")
    await service.bootstrap()
    await drain(parts["voices"])

    first = await service.sync_sounds()
    assert first["uploaded"] == 3

    second = await service.sync_sounds()
    assert second["uploaded"] == 0
    assert bridge.agent_sounds == []    # ajan hâlâ yok, yine de tekrar yok


async def test_gereksiz_kalan_sesler_kopruden_silinir(store: Any, sounds: Path) -> None:
    """Grup adı değişince eski anons köprüde birikmemeli."""
    bridge = FakeBridge()
    service, parts = build(store, sounds, bridge=bridge)
    added = await service.add_group("İlayda", actor="t")
    await service.bootstrap()
    await drain(parts["voices"])
    await service.sync_sounds()
    assert len(bridge.uploaded) == 3

    await service.rename_group(added["id"], "İlayda Şişman", actor="t")
    await drain(parts["voices"])
    result = await service.sync_sounds()

    assert result["uploaded"] == 1      # yeni ad için yeni ses
    assert result["removed"] == 1       # eski ad köprüden düştü
    assert len(bridge.uploaded) == 3


async def test_kopru_durumu_onbellege_alinir(store: Any, sounds: Path) -> None:
    """Ekran on saniyede bir tazeleniyor; her tazeleme köprüye gitmemeli.

    Önbellek gerçek istemcide (`BellBridge`), sahte köprüde değil. Bu yüzden
    burada gerçek sınıf sahte bir aktarımla kurulur.
    """
    import httpx
    from bell_backend.bridge import BellBridge

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"online": True, "lastSeen": "",
                                         "sounds": [], "bridgeSounds": []})

    bridge = BellBridge(
        secrets=FakeSecrets({"bell.bridge_token": "t"}),
        log=FakeLog(), base_url="https://ornek",
        transport=httpx.MockTransport(handler),
    )

    for _ in range(5):
        assert (await bridge.try_status())["online"] is True
    assert len(calls) == 1               # beşi de aynı önbellekten

    bridge.forget_status()
    await bridge.try_status()
    assert len(calls) == 2


async def test_kopru_coktugunde_hata_da_onbellege_girer(store: Any, sounds: Path) -> None:
    """Köprü düştüğünde her tazeleme yeniden zaman aşımı beklememeli."""
    import httpx
    from bell_backend.bridge import BellBridge

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ConnectError("bağlanılamadı")

    bridge = BellBridge(
        secrets=FakeSecrets({"bell.bridge_token": "t"}),
        log=FakeLog(), base_url="https://ornek",
        transport=httpx.MockTransport(handler),
    )

    for _ in range(4):
        result = await bridge.try_status()
        assert result["online"] is False
        assert "ulaşılamadı" in result["error"]
    assert len(calls) == 1


async def test_state_eksik_sesleri_bildirir(store: Any, sounds: Path) -> None:
    from bell_backend.speech import SpeechError

    speech = FakeSpeech(script=[SpeechError("reddedildi", retryable=False)])
    service, parts = build(store, sounds, speech=speech)
    await service.add_group("İlayda", actor="t")
    await drain(parts["voices"])

    state = await service.state()
    assert state["missingVoices"] == ["İlayda"]
    assert state["groups"][0]["voice"]["state"] == "error"
    # Zil sesi seçicisinde anons dosyaları görünmez.
    assert all(not item["name"].startswith("anons-") for item in state["sounds"])


async def test_week_yetenegi_salt_okunur_ozet_verir(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    await service.save_times({"mon": [{"time": "08:40", "label": "teneffüs"}]}, actor="t")
    await service.add_group("İlayda", actor="t")

    week = await service.week()
    assert week["times"]["mon"][0]["time"] == "08:40"
    assert [item["name"] for item in week["groups"]] == ["İlayda"]
