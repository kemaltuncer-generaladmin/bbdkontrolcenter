"""Vertex TTS istemcisi. AĞA ÇIKMAZ — `httpx.MockTransport` kullanılır.

Sınanan iki şey:
  · Servis hesabı akışı: JWT imzalandı mı, belirteç alındı mı, istek nereye gitti?
  · Yanıt çözümleme: ham PCM WAV'a sarmalandı mı, hata doğru sınıflandı mı?

İkincisi kritik: `paplay` başlıksız PCM'i tanımaz. Sarmalama yanlışsa dosya
oluşur, ekran "hazır" der ve zil vakti hiçbir ses çıkmaz.
"""

from __future__ import annotations

import base64
import io
import json
import wave

import httpx
import pytest
from bell_backend.speech import (
    SpeechError,
    VertexSpeech,
    _find_inline_audio,
    _to_wav,
)
from bell_fakes import FakeLog, FakeSecrets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PCM = b"\x01\x02" * 1200          # 2400 bayt = 1200 örnek


@pytest.fixture(scope="module")
def account() -> str:
    """Gerçek bir servis hesabı JSON'u (anahtar testte üretilir, sır değil)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return json.dumps({
        "type": "service_account",
        "project_id": "test-proje",
        "client_email": "zil@test-proje.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    })


def audio_response(data: bytes, mime: str = "audio/L16;codec=pcm;rate=24000") -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [
        {"inlineData": {"mimeType": mime,
                        "data": base64.b64encode(data).decode("ascii")}}
    ]}}]}


def build(account: str, handler) -> tuple[VertexSpeech, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    speech = VertexSpeech(
        secrets=FakeSecrets({"bell.vertex_service_account": account}),
        config={"location": "europe-west4", "model": "test-tts", "voice": "Kore"},
        log=FakeLog(),
        transport=httpx.MockTransport(record),
    )
    return speech, seen


def token_then(response: httpx.Response):
    """Belirteç isteğini karşılar, sonrakini `response` ile yanıtlar."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "T", "expires_in": 3600})
        return response
    return handler


# --------------------------------------------------------------- sarmalama


def test_ham_pcm_wave_sarmalanir() -> None:
    speech = _to_wav(PCM, "audio/L16;codec=pcm;rate=24000", model="m", voice="v")
    assert speech.data[:4] == b"RIFF"

    with wave.open(io.BytesIO(speech.data), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 24000
        assert handle.readframes(handle.getnframes()) == PCM
    assert speech.seconds == pytest.approx(1200 / 24000, abs=0.01)


def test_mime_orneklemesi_okunur() -> None:
    speech = _to_wav(PCM, "audio/L16;codec=pcm;rate=16000", model="m", voice="v")
    with wave.open(io.BytesIO(speech.data), "rb") as handle:
        assert handle.getframerate() == 16000


def test_mime_yoksa_varsayilan_hiz() -> None:
    speech = _to_wav(PCM, "", model="m", voice="v")
    with wave.open(io.BytesIO(speech.data), "rb") as handle:
        assert handle.getframerate() == 24000


def test_zaten_wav_ise_dokunulmaz() -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(PCM)
    original = buffer.getvalue()

    speech = _to_wav(original, "audio/wav", model="m", voice="v")
    assert speech.data == original       # ikinci kez sarmalanmaz
    assert speech.seconds == pytest.approx(1200 / 8000, abs=0.01)


# ------------------------------------------------------------- yanıt arama


def test_inline_ses_ic_ice_bulunur() -> None:
    found = _find_inline_audio(audio_response(PCM))
    assert found is not None
    assert base64.b64decode(found["data"]) == PCM


def test_snake_case_alan_adi_da_kabul_edilir() -> None:
    """Alan adı sürüme göre `inlineData` ya da `inline_data` gelebiliyor."""
    payload = {"candidates": [{"content": {"parts": [
        {"inline_data": {"mime_type": "audio/L16;rate=24000",
                         "data": base64.b64encode(PCM).decode()}}]}}]}
    found = _find_inline_audio(payload)
    assert found is not None
    assert base64.b64decode(found["data"]) == PCM


def test_ses_yoksa_none_doner() -> None:
    assert _find_inline_audio({"candidates": [{"content": {"parts": [
        {"text": "Üzgünüm, bunu yapamam."}]}}]}) is None


# ----------------------------------------------------------------- uçtan uca


async def test_basarili_uretim(account: str) -> None:
    speech, seen = build(account, token_then(
        httpx.Response(200, json=audio_response(PCM))))

    result = await speech.synthesize("  Lütfen   derse geçiniz.  ")

    assert result.data[:4] == b"RIFF"
    assert len(seen) == 2                      # belirteç + üretim

    # Belirteç isteği: JWT taşıyan bir jwt-bearer değişimi.
    token_request = seen[0]
    assert "oauth2.googleapis.com" in str(token_request.url)
    body = token_request.content.decode()
    assert "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in body
    assert "assertion=" in body

    # Üretim isteği: bölge, proje ve model adresten okunmalı.
    call = seen[1]
    assert str(call.url) == (
        "https://europe-west4-aiplatform.googleapis.com/v1"
        "/projects/test-proje/locations/europe-west4"
        "/publishers/google/models/test-tts:generateContent"
    )
    assert call.headers["Authorization"] == "Bearer T"

    sent = json.loads(call.content)
    assert sent["generationConfig"]["responseModalities"] == ["AUDIO"]
    assert (sent["generationConfig"]["speechConfig"]["voiceConfig"]
            ["prebuiltVoiceConfig"]["voiceName"] == "Kore")
    # Metin boşlukları normalize edilerek gider.
    assert sent["contents"][0]["parts"][0]["text"] == "Lütfen derse geçiniz."


async def test_belirtec_yeniden_kullanilir(account: str) -> None:
    """Her üretim için yeni belirteç almak gereksiz istek demektir."""
    speech, seen = build(account, token_then(
        httpx.Response(200, json=audio_response(PCM))))

    await speech.synthesize("bir")
    await speech.synthesize("iki")

    tokens = [item for item in seen if "oauth2" in str(item.url)]
    assert len(tokens) == 1


async def test_proje_ayardan_gelirse_hesaptakini_ezer(account: str) -> None:
    speech = VertexSpeech(
        secrets=FakeSecrets({"bell.vertex_service_account": account}),
        config={"project_id": "baska-proje", "location": "us-central1",
                "model": "m", "voice": "Kore"},
        log=FakeLog(),
        transport=httpx.MockTransport(token_then(
            httpx.Response(200, json=audio_response(PCM)))),
    )
    await speech.synthesize("merhaba")
    assert "projects/baska-proje/" in speech.endpoint


# -------------------------------------------------------------------- hata


async def test_kasada_hesap_yoksa_anlasilir_hata() -> None:
    speech = VertexSpeech(secrets=FakeSecrets({}), config={}, log=FakeLog())
    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert "kasada yok" in str(caught.value)
    assert caught.value.retryable is False


async def test_bozuk_json_anlasilir_hata() -> None:
    speech = VertexSpeech(
        secrets=FakeSecrets({"bell.vertex_service_account": "{ bozuk"}),
        config={}, log=FakeLog(),
    )
    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert "çözülemedi" in str(caught.value)


async def test_429_yeniden_denenebilir_isaretlenir(account: str) -> None:
    speech, _ = build(account, token_then(
        httpx.Response(429, headers={"Retry-After": "12"}, json={})))

    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert caught.value.retryable is True
    assert caught.value.status == 429
    assert caught.value.retry_after == 12


async def test_5xx_yeniden_denenebilir(account: str) -> None:
    speech, _ = build(account, token_then(httpx.Response(503, json={})))
    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert caught.value.retryable is True


async def test_403_kalici_ve_yol_gosterir(account: str) -> None:
    """Yetki hatası tekrar denemekle geçmez; ne yapılacağı yazmalı."""
    speech, _ = build(account, token_then(httpx.Response(
        403, json={"error": {"message": "Permission denied"}})))

    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert caught.value.retryable is False
    assert "Vertex AI User" in str(caught.value)


async def test_404_model_ve_bolgeyi_soyler(account: str) -> None:
    speech, _ = build(account, token_then(httpx.Response(
        404, json={"error": {"message": "not found"}})))

    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert "test-tts" in str(caught.value)
    assert "europe-west4" in str(caught.value)


async def test_ses_yerine_metin_donerse_hata(account: str) -> None:
    speech, _ = build(account, token_then(httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": "olmaz"}]}}]})))

    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert "ses yok" in str(caught.value)


async def test_bos_ses_verisi_hata(account: str) -> None:
    speech, _ = build(account, token_then(
        httpx.Response(200, json=audio_response(b""))))
    with pytest.raises(SpeechError):
        await speech.synthesize("merhaba")


async def test_belirtec_reddedilirse_yeniden_denenmez(account: str) -> None:
    """invalid_grant: saat kaymış ya da anahtar iptal. Beklemek düzeltmez."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    speech, _ = build(account, handler)
    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert caught.value.retryable is False


async def test_ag_hatasi_yeniden_denenebilir(account: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bağlanılamadı")

    speech, _ = build(account, handler)
    with pytest.raises(SpeechError) as caught:
        await speech.synthesize("merhaba")
    assert caught.value.retryable is True


async def test_bos_metin_istek_atmaz(account: str) -> None:
    speech, seen = build(account, token_then(
        httpx.Response(200, json=audio_response(PCM))))
    with pytest.raises(SpeechError):
        await speech.synthesize("   ")
    assert seen == []
