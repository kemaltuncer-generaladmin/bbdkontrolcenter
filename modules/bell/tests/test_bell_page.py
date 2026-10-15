"""Öğrenci çağırma — `bell.announce` (mobil geçit sözleşmesi §5).

Vertex ve köprü taklittir; depo gerçek SQLite (conftest). Ağa çıkılmaz.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bell_backend.announce import BellAnnounce
from bell_backend.service import BellService, content_id, render_page
from bell_backend.speech import SpeechError
from bell_backend.voices import VoiceLibrary
from bell_fakes import FakeAudio, FakeBridge, FakeLog, FakeScheduler, FakeSpeech

TEXT = "Elif Kaya, lütfen danışmaya gel."


def build(store: Any, sounds: Path, *, speech: FakeSpeech | None = None,
          bridge: FakeBridge | None = None) -> tuple[BellService, dict[str, Any]]:
    log = FakeLog()
    parts: dict[str, Any] = {
        "bridge": bridge or FakeBridge(),
        "speech": speech or FakeSpeech(),
        "audio": FakeAudio(sounds),
        "log": log,
    }
    parts["voices"] = VoiceLibrary(store=store, log=log, speech=parts["speech"],
                                   sounds_path=sounds, config={"min_interval_seconds": 0})
    service = BellService(store=store, log=log, audio=parts["audio"],
                          scheduler=FakeScheduler(), voices=parts["voices"],
                          bridge=parts["bridge"], play_locally=False, page_tts_timeout=5)
    return service, parts


async def rows(store: Any, table: str) -> list[dict[str, Any]]:
    return await store.fetch_all(f"SELECT * FROM {table}")


def test_sablon_doldurulur() -> None:
    assert render_page("{ad} {soyad}, lütfen danışmaya gel.", first_name="Elif",
                       last_name="Kaya") == TEXT
    assert render_page("{ad} {soyad} ({sinif})", first_name=" Ali  Can ",
                       last_name="", class_name="8-A") == "Ali Can (8-A)"


async def test_varsayilan_sablon(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    assert await service.page_template() == "{ad} {soyad}, lütfen danışmaya gel."
    saved = await service.save_settings({"texts": {"page": "{ad}, danışmaya."}}, actor="t")
    assert saved["settings"]["texts"]["page"] == "{ad}, danışmaya."
    saved = await service.save_settings({"texts": {"page": ""}}, actor="t")
    assert saved["settings"]["texts"]["page"] == "{ad} {soyad}, lütfen danışmaya gel."


async def test_ses_yoksa_anlik_uretilir_ve_kopruye_gider(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    result = await service.page(TEXT, actor="akademi:staff:42", request_id="r-1")

    assert result["status"] == "queued"
    assert result["voiceCached"] is False
    assert result["etaSeconds"] == 3
    assert parts["speech"].calls == [TEXT]
    sent = parts["bridge"].sent
    assert len(sent) == 1 and sent[0]["playAt"] == ""
    item = sent[0]["items"][0]
    assert item["kind"] == "anons"
    # Ses komuttan ÖNCE köprüye kondu: ajan özeti oradan indirir.
    assert item["hash"] in parts["bridge"].uploaded

    history = await rows(store, "mod_bell_page")
    assert len(history) == 1
    assert history[0]["result"] == "queued" and history[0]["request_id"] == "r-1"
    assert history[0]["actor"] == "akademi:staff:42"
    assert TEXT not in str(history[0])                 # metin değil özeti yazılır


async def test_onbellekteki_ses_hemen_ve_ayni_istek_tekrar_calmaz(store: Any,
                                                                 sounds: Path) -> None:
    service, parts = build(store, sounds)
    await parts["voices"].require(TEXT)
    await parts["voices"]._queue.join()

    first = await service.page(TEXT, request_id="r-2")
    assert first["status"] == "queued" and first["voiceCached"] is True
    assert parts["speech"].calls == [TEXT]             # yalnız önbelleğe alırken

    again = await service.page(TEXT, request_id="r-2")
    assert again["replayed"] is True and again["status"] == "queued"
    assert len(parts["bridge"].sent) == 1              # ikinci kez ÇALINMADI


async def test_kapali_ve_bos_metin(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    assert (await service.page("   "))["status"] == "tts_unavailable"
    await service.save_settings({"enabled": False}, actor="t")
    result = await service.page(TEXT)
    assert result == {"status": "disabled", "voiceCached": False, "etaSeconds": 0,
                      "detail": "Zil sistemi kapalı."}
    assert parts["speech"].calls == [] and parts["bridge"].sent == []


async def test_tts_yoksa_tts_unavailable(store: Any, sounds: Path) -> None:
    speech = FakeSpeech(script=[SpeechError("Servis hesabı yok", retryable=False, status=403)])
    service, parts = build(store, sounds, speech=speech)
    result = await service.page(TEXT)
    assert result["status"] == "tts_unavailable"
    assert "Servis hesabı yok" in result["detail"]
    assert parts["bridge"].sent == []


async def test_kopru_reddederse_failed(store: Any, sounds: Path) -> None:
    bridge = FakeBridge()
    service, _ = build(store, sounds, bridge=bridge)
    bridge.error = "Köprü kapalı."
    result = await service.page(TEXT)
    assert result["status"] == "failed"
    assert "Köprü kapalı" in result["detail"]


async def test_sayfa_sesi_kopruden_ve_diskten_silinmez(store: Any, sounds: Path) -> None:
    """KRİTİK: `sync_sounds` / `prune_voices` sayfa seslerini atmamalı."""
    service, parts = build(store, sounds)
    await service.page(TEXT)
    key = parts["voices"].key(TEXT)
    data = (sounds / str((await parts["voices"].entry(key))["file"])).read_bytes()

    await service.sync_sounds()
    assert content_id(data) in parts["bridge"].uploaded
    assert content_id(data) in await service.needed_sounds()
    await service.prune_voices(actor="t")
    assert await parts["voices"].ready_file(key)

    # 24 saatten eski ve hazırlanmamış sayfa sesi artık tutulmaz.
    old = (datetime.now(UTC) - timedelta(hours=25)).isoformat(timespec="seconds")
    await store.execute("UPDATE mod_bell_page SET at = ?", (old,))
    assert content_id(data) not in await service.needed_sounds()
    await service.sync_sounds()
    assert content_id(data) not in parts["bridge"].uploaded


async def test_hazirlanan_ses_60_gun_tutulur(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    result = await service.prepare_page(TEXT, cache_key="student:STU-1")
    assert result["status"] == "queued"
    await parts["voices"]._queue.join()
    assert (await service.prepare_page(TEXT, cache_key="student:STU-1"))["status"] == "cached"

    key = parts["voices"].key(TEXT)
    assert key in await service._live_voice_keys(await service.settings())
    old = (datetime.now(UTC) - timedelta(days=61)).isoformat(timespec="seconds")
    await store.execute("UPDATE mod_bell_page_voice SET prepared_at = ?", (old,))
    assert key not in await service._live_voice_keys(await service.settings())


async def test_gece_hazirligi_dizinden(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)

    class Directory:
        def __init__(self, complete: bool) -> None:
            self.complete = complete

        async def list(self) -> dict[str, Any]:
            return {"complete": self.complete, "classes": [], "students": [
                {"id": "STU-1", "firstName": "Elif", "lastName": "Kaya", "className": "8-A"},
                {"id": "STU-2", "firstName": "Can", "lastName": "Demir", "className": "9-B"},
            ]}

    assert (await service.prepare_students(Directory(False)))["ok"] is False
    result = await service.prepare_students(Directory(True))
    assert result == {"ok": True, "prepared": 2, "queued": 2}
    await parts["voices"]._queue.join()
    assert sorted(parts["speech"].calls) == sorted([TEXT, "Can Demir, lütfen danışmaya gel."])


async def test_yetenek_hata_yukseltmez(store: Any, sounds: Path) -> None:
    service, _ = build(store, sounds)
    announce = BellAnnounce(service)
    assert announce.render("{ad} {soyad}", first_name="A", last_name="B") == "A B"
    assert await announce.page_template() == "{ad} {soyad}, lütfen danışmaya gel."

    async def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("depo yok")

    service.page = boom  # type: ignore[method-assign]
    result = await announce.page(TEXT)
    assert result["status"] == "failed" and "depo yok" in result["detail"]


# ------------------------------------------------ toplu hazırlık + önizleme (010)


async def test_toplu_hazirlik_olagan_seslerin_arkasindan(store: Any, sounds: Path) -> None:
    """Akademi'nin yüzlerce şablon sesi zil/grup seslerinin önüne geçmez; toplu bekleyen
    ses olağan istenince öne alınır ve İKİ KEZ üretilmez."""
    service, parts = build(store, sounds)
    voices = parts["voices"]
    await voices.stop()
    voices._worker = asyncio.current_task()          # işçi başlamasın, sıra birikmeli
    for n in range(3):
        await service.prepare_page(f"Öğrenci {n}, danışmaya.", cache_key=f"tpl:{n}", bulk=True)
    await voices.require("İyi dersler.")
    await voices.require("Öğrenci 2, danışmaya.")    # toplu bekleyen → öne alınır
    assert voices.state()["queuedBulk"] == 2
    voices._worker = None
    voices.start()
    await voices._queue.join()
    calls = parts["speech"].calls
    assert calls[:2] == ["İyi dersler.", "Öğrenci 2, danışmaya."]
    assert sorted(calls[2:]) == ["Öğrenci 0, danışmaya.", "Öğrenci 1, danışmaya."]


async def test_toplu_ses_diskte_kalir_kopruye_onden_gitmez(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    assert (await service.prepare_page(TEXT, cache_key="akademi:pa:s:1",
                                       bulk=True))["status"] == "queued"
    await parts["voices"]._queue.join()
    key = parts["voices"].key(TEXT)
    data = (sounds / str((await parts["voices"].entry(key))["file"])).read_bytes()
    # "Kullanılmayan sesleri sil" silmez ...
    assert key in await service._live_voice_keys(await service.settings())
    await service.prune_voices(actor="t")
    assert await parts["voices"].ready_file(key)
    # ... ama köprü eşitlemesi önden taşımaz.
    assert content_id(data) not in await service.needed_sounds()
    # Çalınınca köprüye yüklenir ve bekleme olmaz (Vertex'e gidilmez).
    result = await service.page(TEXT, request_id="r-bulk")
    assert result["status"] == "queued" and result["voiceCached"] is True
    assert parts["speech"].calls == [TEXT]
    assert content_id(data) in parts["bridge"].uploaded
    assert content_id(data) in await service.needed_sounds()     # son 24 saat
    # Tekil hazırlık toplu işaretini düşürür → köprüye önden gider.
    await store.execute("DELETE FROM mod_bell_page")
    assert content_id(data) not in await service.needed_sounds()
    await service.prepare_page(TEXT, cache_key="akademi:pa:s:1")
    assert content_id(data) in await service.needed_sounds()


async def test_onizleme_calmaz_sesi_saklar(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    announce = BellAnnounce(service)
    first = await announce.preview("Yarın okul tatil.")
    assert first["status"] == "ready" and first["voiceCached"] is False
    assert first["dataUri"].startswith("data:audio/wav;base64,")
    assert parts["bridge"].sent == []                       # hoparlöre gitmedi
    again = await announce.preview("Yarın  okul tatil.")    # boşluk farkı aynı ses
    assert again["voiceCached"] is True
    played = await service.page("Yarın okul tatil.")
    assert played["voiceCached"] is True
    assert parts["speech"].calls == ["Yarın okul tatil."]   # bir kez seslendirildi
    assert (await announce.preview("  "))["status"] == "tts_unavailable"


async def test_onizleme_tts_yoksa_acik_hata(store: Any, sounds: Path) -> None:
    speech = FakeSpeech(script=[SpeechError("Kota bitti", retryable=False, status=403)])
    service, _ = build(store, sounds, speech=speech)
    result = await BellAnnounce(service).preview("Duyuru")
    assert result["status"] == "tts_unavailable" and "Kota bitti" in result["detail"]


async def test_kopru_ozeti_dosya_degismedikce_yeniden_okunmaz(store: Any, sounds: Path) -> None:
    service, parts = build(store, sounds)
    await service.page(TEXT)
    path = sounds / str((await parts["voices"].entry(parts["voices"].key(TEXT)))["file"])
    first = service._content_id_of(path)
    assert first == content_id(path.read_bytes())
    assert service._content_ids[str(path)][1] == first
    path.unlink()
    assert service._content_id_of(path) == ""
