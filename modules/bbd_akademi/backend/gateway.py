"""Akademi API'ye açılan TEK kapı — HTTP istemcisi ve başlık kuralları.

Kimlik: `Authorization: Bearer aksk_<prefix>_<secret>` — anahtar KASADAN gelir
(`secrets` → `bbd_akademi.api_key`, K8). Depoda, ayarda, logda değer yoktur.

Her istek şu başlıkları taşır (sözleşme `integrations/km-akademi-paneli.md` §1):

    Authorization            entegrasyon anahtarı
    X-Akademi-On-Behalf-Of   km:user:<id>:<ad soyad>  (yazmalarda Akademi zorunlu tutar)
    X-Request-Id             gelen istekten ya da yeni üretilir; Akademi traceId olarak döner
    Idempotency-Key          istemciden geldiği gibi (para işlemlerinde zorunlu)
    X-Akademi-Step-Up        yalnız KM PIN'i az önce doğrulandıysa: km-pin

AD SOYAD YÜZDE KODLANIR. HTTP başlığı pratikte ASCII'dir; "Ayşe Yılmaz"
olduğu gibi yazılırsa ara katmanlar baytı bozar ya da isteği reddeder. Ad
UTF-8 olarak yüzde kodlanır (`Ay%C5%9Fe Y%C4%B1lmaz`), toplam 160 karakteri
aşmayacak biçimde ad baştan kısaltılır — kod dizisinin ortasından kesilmez.

ERİŞİLEMEZLİK AYRI HATADIR. Bağlantı kurulamaz ya da süre dolarsa
`AkademiUnavailable` fırlatılır; vekil bunu `503 akademi_unavailable`a çevirir.
Akademi'nin kendi hata yanıtı (4xx/5xx) HATA DEĞİLDİR — yanıttır ve zarfıyla
olduğu gibi geri döner.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

SECRET_KEY = "bbd_akademi.api_key"
ON_BEHALF_MAX = 160
REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

#: İstemciden Akademi'ye geçen başlıklar. Geri kalanı (çerez, KM oturum
#: belirteci, `Host`, `X-Forwarded-*`) BİLEREK düşer: KM oturumu Akademi'ye
#: sızmaz, kimlik yalnız entegrasyon anahtarıdır.
FORWARD_REQUEST_HEADERS = ("content-type", "content-length", "accept", "idempotency-key",
                           "if-none-match", "accept-language")

#: Akademi yanıtından istemciye geçen başlıklar.
FORWARD_RESPONSE_HEADERS = ("content-type", "content-disposition", "etag", "retry-after",
                            "idempotency-replayed", "x-request-id", "cache-control",
                            "last-modified", "location")


class AkademiError(RuntimeError):
    """Vekilin kendi hatası (Akademi'nin yanıtı DEĞİL)."""

    status = 503
    code = "akademi_unavailable"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.details = details


class AkademiUnavailable(AkademiError):
    status = 503
    code = "akademi_unavailable"


class AkademiNotConfigured(AkademiError):
    status = 503
    code = "akademi_not_configured"


class AkademiDisabled(AkademiError):
    status = 503
    code = "akademi_disabled"


class DownloadNotAllowed(AkademiError):
    status = 403
    code = "download_not_allowed"


@dataclass(slots=True)
class GatewayConfig:
    base_url: str = "https://api.benimbasaridunyam.com.tr"
    timeout_seconds: float = 20.0
    upload_timeout_seconds: float = 120.0
    enabled: bool = True
    max_upload_mb: int = 25
    download_hosts: list[str] = field(default_factory=list)
    staff_sync_minutes: int = 30
    step_up_seconds: int = 120
    #: Panel köprüsünde OKUMA (GET) isteklerinin geçici ağ geçidi hatasında (Akademi dağıtımı: 502/503) toplam
    #: yeniden deneme bütçesi (sn). 0 = kapalı. Yazmalar hiç yeniden denenmez.
    read_retry_seconds: float = 20.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> GatewayConfig:
        raw = dict(raw or {})
        base = cls()
        return cls(
            base_url=str(raw.get("base_url") or base.base_url).rstrip("/"),
            timeout_seconds=float(raw.get("timeout_seconds") or base.timeout_seconds),
            upload_timeout_seconds=float(raw.get("upload_timeout_seconds")
                                         or base.upload_timeout_seconds),
            # `False` geçerli bir değerdir; `or` ile yazılsaydı True'ya dönerdi.
            enabled=bool(raw.get("enabled", base.enabled)),
            max_upload_mb=int(raw.get("max_upload_mb") or base.max_upload_mb),
            download_hosts=[str(item).lower() for item in raw.get("download_hosts") or []],
            staff_sync_minutes=int(raw.get("staff_sync_minutes", base.staff_sync_minutes)),
            step_up_seconds=int(raw.get("step_up_seconds") or base.step_up_seconds),
            # 0 geçerli (kapalı); `or` ile yazılsaydı varsayılana dönerdi.
            read_retry_seconds=float(raw.get("read_retry_seconds", base.read_retry_seconds)),
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def on_behalf_of(user_id: str, full_name: str, *, kind: str = "user") -> str:
    """`km:user:<id>:<ad soyad>` — ad yüzde kodlu, toplam ≤ 160 karakter."""
    prefix = f"km:{kind}:{quote(str(user_id), safe='')}:"
    name = " ".join(str(full_name or "").split()) or "-"
    encoded = quote(name, safe=" .-_'")
    while len(prefix) + len(encoded) > ON_BEHALF_MAX and name:
        name = name[:-1].rstrip()
        encoded = quote(name, safe=" .-_'") if name else "-"
    return (prefix + encoded)[:ON_BEHALF_MAX]


def request_id(incoming: str | None) -> str:
    value = (incoming or "").strip()
    return value if REQUEST_ID.match(value) else f"km-{uuid.uuid4().hex}"


class AkademiGateway:
    """Akademi istemcisi. Bağlantı havuzu tek istemcide tutulur."""

    def __init__(self, *, config: GatewayConfig, secrets: Any, log: Any,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self._secrets = secrets
        self._log = log
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------ kimlik

    async def api_key(self) -> str:
        if not self.config.enabled:
            raise AkademiDisabled("Akademi bağlantısı ayarlardan kapatılmış "
                                  "(modules.bbd_akademi.enabled).")
        key = await self._secrets.get(SECRET_KEY) if self._secrets is not None else None
        if not key:
            raise AkademiNotConfigured(
                "Akademi entegrasyon anahtarı tanımlı değil. Anahtar sır kasasına "
                f"`{SECRET_KEY}` adıyla yazılmalı.")
        return str(key).strip()

    async def configured(self) -> dict[str, Any]:
        """Panelin "bağlantı" kartı için: anahtar var mı, ayar açık mı. Değer DÖNMEZ."""
        has_key = False
        if self._secrets is not None:
            try:
                has_key = bool(await self._secrets.get(SECRET_KEY))
            except Exception:  # noqa: BLE001 — kasa okunamıyorsa da kart çizilsin
                has_key = False
        return {"enabled": self.config.enabled, "baseUrl": self.config.base_url,
                "hasKey": has_key}

    # ------------------------------------------------------------- istek

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # TEK İSTEMCİ, KALICI BAĞLANTI (PERFORMANCE §3.8): her istekte yeni
            # TLS el sıkışması panelin her tıklamasına ~100 ms eklerdi.
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout(self.config.timeout_seconds),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20,
                                    keepalive_expiry=60),
                follow_redirects=False,
            )
        return self._client

    @staticmethod
    def _timeout(seconds: float) -> httpx.Timeout:
        """Katı süre: bağlantı kurma en çok 5 sn, okuma/yazma ayardan."""
        return httpx.Timeout(seconds, connect=min(5.0, seconds))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, method: str, path: str, *, query: str = "",
                   headers: Mapping[str, str] | None = None,
                   content: bytes | AsyncIterator[bytes] | None = None,
                   files: Any = None, data: Any = None, json_body: Any = None,
                   upload: bool = False) -> httpx.Response:
        """İsteği gönderir, yanıtı AKIŞ olarak döndürür (çağıran `aclose` eder)."""
        key = await self.api_key()
        url = f"{self.config.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        outgoing = {name: value for name, value in (headers or {}).items() if value}
        outgoing["Authorization"] = f"Bearer {key}"
        timeout = self._timeout(self.config.upload_timeout_seconds if upload
                                else self.config.timeout_seconds)
        client = self._http()
        try:
            request = client.build_request(method, url, headers=outgoing, content=content,
                                           files=files, data=data, json=json_body,
                                           timeout=timeout)
            return await client.send(request, stream=True)
        except httpx.TimeoutException as failure:
            self._log.warning("akademi zaman aşımı", path=path, method=method)
            raise AkademiUnavailable(
                "Akademi sunucusu zamanında yanıt vermedi. Yazma işlemiyse "
                "tamamlanmış olabilir; aynı işlemi tekrarlamadan önce listeyi yenileyin.",
                details={"reason": "timeout"}) from failure
        except httpx.TransportError as failure:
            self._log.warning("akademi erişilemedi", path=path, error=str(failure))
            raise AkademiUnavailable("Akademi sunucusuna ulaşılamıyor.",
                                     details={"reason": "connect"}) from failure

    # ------------------------------------------------------ imzalı indirme

    def download_allowed(self, url: str) -> bool:
        """İmzalı bağlantı yalnız Akademi'nin kendi kökeninden ya da ayarda
        ilan edilmiş depolama alanından indirilir — vekil keyfi adrese gitmez."""
        try:
            target = urlsplit(url)
        except ValueError:
            return False
        if target.scheme != "https" and target.hostname not in ("localhost", "127.0.0.1"):
            return False
        base = urlsplit(self.config.base_url)
        host = (target.hostname or "").lower()
        if host and host == (base.hostname or "").lower() and target.port == base.port:
            return True
        return host in self.config.download_hosts

    async def fetch(self, url: str) -> httpx.Response:
        """İmzalı bağlantıyı indirir. Entegrasyon anahtarı GÖNDERİLMEZ."""
        if not self.download_allowed(url):
            raise DownloadNotAllowed("Bu indirme adresine izin verilmiyor.",
                                     details={"host": urlsplit(url).hostname})
        client = self._http()
        try:
            request = client.build_request("GET", url, timeout=self._timeout(
                self.config.upload_timeout_seconds))
            return await client.send(request, stream=True)
        except httpx.TransportError as failure:
            raise AkademiUnavailable("Dosya indirilemedi; Akademi'ye ulaşılamıyor.") from failure
