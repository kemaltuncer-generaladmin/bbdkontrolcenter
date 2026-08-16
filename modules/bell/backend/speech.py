"""Anons sesi üretimi — Vertex AI (Gemini TTS).

NEDEN VERTEX: bu kurumun Google projesinde yalnız `aiplatform.googleapis.com`
açık; ayrı `texttospeech.googleapis.com` yok. Ses `generateContent` çağrısının
`responseModalities: ["AUDIO"]` kipiyle üretilir.

NEDEN ELLE JWT: `google-auth` paketi erişim belirtecini almak için `requests`
taşıyıcısını ister — senkron çalışır ve depoya yeni bir bağımlılık zinciri sokar.
Servis hesabı akışı yeterince küçük: JWT'yi `cryptography` ile imzalayıp (kasa
zaten onu kullanıyor) `oauth2.googleapis.com/token` ile değiştiriyoruz. bbdstore
tarafındaki FCM istemcisi de aynı yolu izliyor.

BU DOSYA ÇALMA YOLUNDA DEĞİLDİR. Zil çalarken buraya uğranmaz; ses önceden
üretilip diske yazılır (bkz. `voices.py`). Buradaki her çağrı bir kullanıcı
eyleminin sonucudur: grup eklendi, adı değişti ya da metin düzenlendi.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
import wave
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

#: Vertex çağrıları için gereken tek kapsam.
SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: Belirteç bu kadar erken yenilenir — saat kayması yüzünden süresi dolmuş
#: belirteçle istek atmayalım.
TOKEN_SKEW_SECONDS = 120

#: PCM dönerse varsayılan örnekleme hızı. Yanıttaki `mimeType` bunu genelde
#: kendisi söyler (`audio/L16;codec=pcm;rate=24000`).
DEFAULT_SAMPLE_RATE = 24000

_RATE = re.compile(r"rate=(\d+)")


class SpeechError(RuntimeError):
    """Ses üretilemedi.

    `retryable` yalnız GEÇİCİ arızalar için doğrudur (429, 5xx, ağ). Kalıcı
    hatada (400, 401, 403, 404) yeniden denemek kotayı boşa harcar ve hatayı
    geciktirir — kuyruk bu ayrımı okur.
    """

    def __init__(self, message: str, *, retryable: bool = False,
                 status: int = 0, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


@dataclass(slots=True)
class Speech:
    """Üretilmiş ses: WAV baytları ve süresi."""

    data: bytes
    seconds: float
    model: str
    voice: str


class VertexSpeech:
    """Vertex AI Gemini TTS istemcisi.

    Servis hesabı JSON'u KASADAN gelir (`bell.vertex_service_account`), depoda
    durmaz (K8). Kurulum sırasında ağa çıkılmaz; ilk gerçek üretimde okunur.
    """

    def __init__(self, *, secrets: Any, config: dict[str, Any], log: Any,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._secrets = secrets
        self._log = log
        self._transport = transport

        section = config if isinstance(config, dict) else {}
        self._project = str(section.get("project_id") or "").strip()
        self._location = str(section.get("location") or "us-central1").strip()
        self.model = str(section.get("model") or "gemini-2.5-flash-preview-tts").strip()
        self.voice = str(section.get("voice") or "Kore").strip()
        self._timeout = float(section.get("timeout_seconds") or 60)

        self._account: dict[str, Any] | None = None
        self._token: str = ""
        self._token_expires: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- kimlik

    async def _service_account(self) -> dict[str, Any]:
        if self._account is not None:
            return self._account

        raw = await self._secrets.get("bell.vertex_service_account")
        if not raw:
            raise SpeechError(
                "Vertex servis hesabı kasada yok. `config/local.yaml` içine "
                "`secrets: {\"bell.vertex_service_account\": \"<JSON>\"}` yazılmalı."
            )
        try:
            account = json.loads(raw)
        except json.JSONDecodeError as failure:
            raise SpeechError(f"Servis hesabı JSON'u çözülemedi: {failure}") from None

        for field in ("client_email", "private_key", "token_uri"):
            if not account.get(field):
                raise SpeechError(f"Servis hesabında '{field}' alanı yok.")

        self._account = account
        if not self._project:
            self._project = str(account.get("project_id") or "").strip()
        if not self._project:
            raise SpeechError(
                "Proje kimliği bulunamadı: ne ayarda `vertex.project_id` var, "
                "ne de servis hesabında `project_id`."
            )
        return account

    def _sign(self, account: dict[str, Any]) -> str:
        """Servis hesabı anahtarıyla RS256 imzalı JWT üretir."""
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": account["client_email"],
            "scope": SCOPE,
            "aud": account["token_uri"],
            "iat": now,
            "exp": now + 3600,
        }
        signing_input = b".".join(_b64(json.dumps(part, separators=(",", ":")).encode())
                                  for part in (header, claims))

        key = serialization.load_pem_private_key(
            account["private_key"].encode("utf-8"), password=None
        )
        if not isinstance(key, rsa.RSAPrivateKey):
            raise SpeechError("Servis hesabı anahtarı RSA değil; JWT imzalanamadı.")

        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input.decode()}.{_b64(signature).decode()}"

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token and time.time() < self._token_expires - TOKEN_SKEW_SECONDS:
            return self._token

        account = await self._service_account()
        # İmzalama CPU işidir ve olay döngüsünü bloklar; ayrı iş parçacığına verilir.
        assertion = await asyncio.to_thread(self._sign, account)

        try:
            response = await client.post(
                str(account["token_uri"]),
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as failure:
            raise SpeechError(f"Erişim belirteci alınamadı: {failure}",
                              retryable=True) from None

        if response.status_code != 200:
            # 400 burada genelde "invalid_grant" demektir: makinenin saati kaymış
            # ya da anahtar iptal edilmiş. İkisi de yeniden denemekle düzelmez.
            raise SpeechError(
                f"Erişim belirteci reddedildi ({response.status_code}): "
                f"{response.text[:200]}",
                retryable=response.status_code >= 500,
                status=response.status_code,
            )

        payload = response.json()
        self._token = str(payload.get("access_token") or "")
        if not self._token:
            raise SpeechError("Belirteç yanıtında `access_token` yok.")
        self._token_expires = time.time() + float(payload.get("expires_in") or 3600)
        return self._token

    # -------------------------------------------------------------- üretim

    @property
    def endpoint(self) -> str:
        return (
            f"https://{self._location}-aiplatform.googleapis.com/v1"
            f"/projects/{self._project}/locations/{self._location}"
            f"/publishers/google/models/{self.model}:generateContent"
        )

    async def synthesize(self, text: str) -> Speech:
        """Metni WAV'a çevirir. Tek çağrı, tek deneme — geri çekilme kuyruğun işi."""
        clean = " ".join(str(text or "").split())
        if not clean:
            raise SpeechError("Boş metin seslendirilemez.")

        body = {
            "contents": [{"role": "user", "parts": [{"text": clean}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}
                },
            },
        }

        # Tek uçuşta tek çağrı: aynı anda iki üretim kotayı iki katına çıkarır.
        async with self._lock, httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            token = await self._access_token(client)
            await self._service_account()   # proje kimliği çözülmüş olsun
            try:
                response = await client.post(
                    self.endpoint,
                    json=body,
                    headers={"Authorization": f"Bearer {token}",
                             "Content-Type": "application/json"},
                )
            except httpx.HTTPError as failure:
                raise SpeechError(f"Vertex'e ulaşılamadı: {failure}",
                                  retryable=True) from None

        return self._read(response)

    def _read(self, response: httpx.Response) -> Speech:
        if response.status_code == 429:
            raise SpeechError(
                "Vertex kota sınırı (429). Üretim yavaşlatılıp yeniden denenecek.",
                retryable=True, status=429,
                retry_after=_retry_after(response),
            )
        if response.status_code >= 500:
            raise SpeechError(
                f"Vertex geçici hata ({response.status_code}).",
                retryable=True, status=response.status_code,
                retry_after=_retry_after(response),
            )
        if response.status_code >= 400:
            # Kalıcı hata. Mesajı KISALTMADAN saklamak istemezdik ama ekranda
            # okunabilir kalmalı; tam metin loga düşer.
            detail = _error_message(response)
            hint = ""
            if response.status_code in (401, 403):
                hint = (" Servis hesabının 'Vertex AI User' rolü ve projede "
                        "aiplatform.googleapis.com açık olmalı.")
            elif response.status_code == 404:
                hint = (f" '{self.model}' modeli '{self._location}' bölgesinde "
                        "bulunamadı; ayarı gözden geçirin.")
            raise SpeechError(f"Vertex isteği reddedildi ({response.status_code}): "
                              f"{detail}{hint}", status=response.status_code)

        try:
            payload = response.json()
        except ValueError:
            raise SpeechError("Vertex yanıtı JSON değil.") from None

        inline = _find_inline_audio(payload)
        if inline is None:
            # Model metin döndürmüş olabilir (güvenlik süzgeci, yanlış kip).
            raise SpeechError(
                "Vertex yanıtında ses yok. "
                f"Dönen: {json.dumps(payload, ensure_ascii=False)[:200]}"
            )

        mime = str(inline.get("mimeType") or "")
        try:
            raw = base64.b64decode(str(inline.get("data") or ""), validate=True)
        except (ValueError, TypeError):
            raise SpeechError("Ses verisi base64 olarak çözülemedi.") from None
        if not raw:
            raise SpeechError("Vertex boş ses verisi döndürdü.")

        return _to_wav(raw, mime, model=self.model, voice=self.voice)


# ------------------------------------------------------------------- yardım


def _b64(data: bytes) -> bytes:
    """JWT'nin istediği dolgusuz base64url."""
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After") or 0))
    except (TypeError, ValueError):
        return 0.0


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    return response.text[:200]


def _find_inline_audio(payload: Any) -> dict[str, Any] | None:
    """Yanıt ağacındaki ilk `inlineData` ses parçasını bulur.

    Alan adı sürüme göre `inlineData` ya da `inline_data` olabiliyor; ikisini de
    kabul ediyoruz. Ağaçta gezmek, tek bir yola bel bağlamaktan dayanıklı.
    """
    if isinstance(payload, dict):
        for key in ("inlineData", "inline_data"):
            inline = payload.get(key)
            if isinstance(inline, dict) and inline.get("data"):
                mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
                if not mime or mime.startswith("audio/"):
                    return {"data": inline["data"], "mimeType": mime}
        for value in payload.values():
            found = _find_inline_audio(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_inline_audio(item)
            if found is not None:
                return found
    return None


def _to_wav(raw: bytes, mime: str, *, model: str, voice: str) -> Speech:
    """Ham PCM'i WAV'a sarmalar; zaten WAV ise olduğu gibi bırakır.

    `audio` yeteneği dosyadan çalıyor ve `paplay`/`aplay` başlıksız PCM'i
    tanımaz. Model çoğunlukla `audio/L16;codec=pcm;rate=24000` döndürür —
    başlığı biz yazarız.
    """
    if raw[:4] == b"RIFF":
        seconds = _wav_seconds(raw)
        return Speech(data=raw, seconds=seconds, model=model, voice=voice)

    match = _RATE.search(mime)
    rate = int(match.group(1)) if match else DEFAULT_SAMPLE_RATE

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)          # 16 bit
        handle.setframerate(rate)
        handle.writeframes(raw)

    seconds = round(len(raw) / (2 * rate), 2) if rate else 0.0
    return Speech(data=buffer.getvalue(), seconds=seconds, model=model, voice=voice)


def _wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            rate = handle.getframerate()
            return round(handle.getnframes() / rate, 2) if rate else 0.0
    except (wave.Error, EOFError):
        return 0.0
