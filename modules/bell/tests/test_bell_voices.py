"""Ses önbelleği ve üretim kuyruğu — 429 disiplininin sınandığı yer.

Buradaki testlerin tamamı şu tek soruyu farklı açılardan sorar: Vertex'e kaç
kez gidildi? Cevap yanlışsa ya kota boşa harcanıyor ya da zil sessiz kalıyor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from bell_backend.speech import SpeechError
from bell_backend.voices import PREFIX, VoiceLibrary, digest, file_name, render
from bell_fakes import FakeLog, FakeSpeech, wav_bytes


def build(store: Any, sounds: Path, *, speech: FakeSpeech | None = None,
          **config: Any) -> tuple[VoiceLibrary, FakeSpeech, list[float]]:
    slept: list[float] = []
    engine = speech or FakeSpeech()
    library = VoiceLibrary(
        store=store, log=FakeLog(), speech=engine, sounds_path=sounds,
        config={"min_interval_seconds": 0, **config},
    )

    async def record(seconds: float) -> None:
        # Testte gerçekten beklemeyiz; ne kadar beklendiği ölçülür.
        slept.append(seconds)

    library._sleep = record
    return library, engine, slept


async def drain(library: VoiceLibrary) -> None:
    await library._queue.join()


# ------------------------------------------------------------------ anahtar


def test_ozet_metin_model_ve_sese_bagli() -> None:
    a = digest("Merhaba", "model-1", "Kore")
    assert a == digest("  Merhaba  ", "model-1", "Kore")   # boşluk normalize
    assert a != digest("Merhaba", "model-2", "Kore")       # model değişti
    assert a != digest("Merhaba", "model-1", "Puck")       # ses değişti


def test_dosya_adi_ozetten_uydurulamaz() -> None:
    """Özet dışarıdan gelirse klasör dışına çıkılamamalı."""
    assert file_name("../../etc/passwd").startswith(PREFIX)
    assert "/" not in file_name("../../etc/passwd")
    assert file_name("ABCdef123") == f"{PREFIX}abcdef123.wav"


def test_render_uzun_adi_kirpar() -> None:
    uzun = "A" * 200
    assert len(render("{grup}!", uzun)) == 61      # 60 harf + "!"


# ---------------------------------------------------------------- önbellek


async def test_ayni_metin_ikinci_kez_uretilmez(store: Any, sounds: Path) -> None:
    library, speech, _ = build(store, sounds)

    first = await library.require("Lütfen derse geçiniz.")
    await drain(library)
    second = await library.require("Lütfen derse geçiniz.")
    await drain(library)

    assert first == second
    assert speech.calls == ["Lütfen derse geçiniz."]     # TEK çağrı


async def test_uretilen_dosya_diske_yazilir(store: Any, sounds: Path) -> None:
    library, _, _ = build(store, sounds)
    hash_ = await library.require("Merhaba")
    await drain(library)

    name = await library.ready_file(hash_)
    assert name == file_name(hash_)
    written = (sounds / name)
    assert written.is_file()
    assert written.read_bytes()[:4] == b"RIFF"
    # Geçici `.part` dosyası ortada kalmamalı.
    assert list(sounds.glob("*.part")) == []


async def test_dosya_silinirse_hazir_sayilmaz(store: Any, sounds: Path) -> None:
    """Kayıt "hazır" der ama dosya yoksa hazır DEĞİLDİR."""
    library, _, _ = build(store, sounds)
    hash_ = await library.require("Merhaba")
    await drain(library)
    (sounds / file_name(hash_)).unlink()

    assert await library.ready_file(hash_) == ""

    # `sweep` onu yeniden sıraya alır.
    assert await library.sweep() == 1
    await drain(library)
    assert await library.ready_file(hash_) == file_name(hash_)


async def test_force_var_olani_yeniden_uretir(store: Any, sounds: Path) -> None:
    library, speech, _ = build(store, sounds)
    await library.require("Merhaba")
    await drain(library)
    await library.require("Merhaba", force=True)
    await drain(library)
    assert speech.calls == ["Merhaba", "Merhaba"]


async def test_bos_metin_istek_dogurmaz(store: Any, sounds: Path) -> None:
    library, speech, _ = build(store, sounds)
    assert await library.require("   ") == ""
    await drain(library)
    assert speech.calls == []


# ------------------------------------------------------------- 429 disiplini


async def test_429_ustel_geri_cekilmeyle_yeniden_denenir(
    store: Any, sounds: Path
) -> None:
    speech = FakeSpeech(script=[
        SpeechError("kota", retryable=True, status=429),
        SpeechError("kota", retryable=True, status=429),
        b"",                                    # üçüncü tur başarılı
    ])
    library, engine, slept = build(store, sounds, speech=speech, backoff_seconds=2,
                                   backoff_cap_seconds=32)

    hash_ = await library.require("Merhaba")
    await drain(library)

    assert len(engine.calls) == 3
    assert slept == [2, 4]                      # 2 → 4, üstel
    assert await library.ready_file(hash_) != ""


async def test_retry_after_basligi_yeglenir(store: Any, sounds: Path) -> None:
    """Sunucu "şu kadar bekle" diyorsa kendi hesabımızı dayatmayız."""
    speech = FakeSpeech(script=[
        SpeechError("kota", retryable=True, status=429, retry_after=7.5),
        b"",
    ])
    library, _, slept = build(store, sounds, speech=speech, backoff_seconds=2)
    await library.require("Merhaba")
    await drain(library)
    assert slept == [7.5]


async def test_geri_cekilme_tavani_asilmaz(store: Any, sounds: Path) -> None:
    speech = FakeSpeech(script=[SpeechError("kota", retryable=True, status=429)])
    library, engine, slept = build(store, sounds, speech=speech, backoff_seconds=2,
                                   backoff_cap_seconds=8, max_attempts=6)
    await library.require("Merhaba")
    await drain(library)

    assert len(engine.calls) == 6
    assert slept == [2, 4, 8, 8, 8]             # tavana oturur, büyümez


async def test_kalici_hata_yeniden_denenmez(store: Any, sounds: Path) -> None:
    """403 tekrar denemekle düzelmez; kotayı boşa harcamanın anlamı yok."""
    speech = FakeSpeech(script=[
        SpeechError("yetki yok", retryable=False, status=403)
    ])
    library, engine, slept = build(store, sounds, speech=speech)

    hash_ = await library.require("Merhaba")
    await drain(library)

    assert len(engine.calls) == 1
    assert slept == []
    assert await library.ready_file(hash_) == ""

    entry = await library.entry(hash_)
    assert entry is not None
    assert "yetki yok" in str(entry["error"])


async def test_denemeler_bitince_hata_saklanir(store: Any, sounds: Path) -> None:
    speech = FakeSpeech(script=[SpeechError("kota", retryable=True, status=429)])
    library, engine, _ = build(store, sounds, speech=speech, max_attempts=3)
    hash_ = await library.require("Merhaba")
    await drain(library)

    assert len(engine.calls) == 3
    entry = await library.entry(hash_)
    assert entry is not None
    assert entry["error"] != ""
    assert entry["file"] == ""            # yarım kayıt bırakılmaz


async def test_hatali_kayit_sweep_ile_yeniden_denenir(store: Any, sounds: Path) -> None:
    speech = FakeSpeech(script=[SpeechError("kota", retryable=False)])
    library, engine, _ = build(store, sounds, speech=speech)
    hash_ = await library.require("Merhaba")
    await drain(library)
    assert (await library.entry(hash_))["error"] != ""

    engine.script = [b""]                 # arıza geçti
    assert await library.sweep() == 1
    await drain(library)
    assert await library.ready_file(hash_) != ""


async def test_ayni_metin_kuyrukta_iki_kez_beklemez(store: Any, sounds: Path) -> None:
    """Ekran hızlı hızlı kaydederse aynı ses üst üste sıraya girmemeli."""
    library, _, _ = build(store, sounds)
    library._enqueue("aaa")
    library._enqueue("aaa")
    assert library._queue.qsize() == 1
    await library.stop()


async def test_state_kuyruk_boyunu_bildirir(store: Any, sounds: Path) -> None:
    library, _, _ = build(store, sounds)
    await library.require("Merhaba")
    assert library.state()["queued"] >= 1
    await drain(library)


@pytest.mark.parametrize("text", ["Merhaba", "İlayda, dersiniz başlıyor."])
async def test_uretilen_ses_calinabilir_wav(store: Any, sounds: Path, text: str) -> None:
    """Ürettiğimiz dosya `paplay`in tanıyacağı biçimde olmalı."""
    import wave

    library, _, _ = build(store, sounds)
    hash_ = await library.require(text)
    await drain(library)

    with wave.open(str(sounds / file_name(hash_)), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getnframes() > 0


async def test_bozuk_wav_de_olsa_kayit_tutarlidir(store: Any, sounds: Path) -> None:
    """Model beklenmedik bir bayt yığını dönerse kayıt yine de tutarlı kalmalı."""
    library, _, _ = build(store, sounds,
                          speech=FakeSpeech(script=[wav_bytes(text="özel")]))
    hash_ = await library.require("özel")
    await drain(library)

    entry = await library.entry(hash_)
    assert entry is not None
    assert entry["bytes"] == len(wav_bytes(text="özel"))
    assert entry["error"] == ""
