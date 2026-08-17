"""Merkezî kimlik servisiyle konuşan ince istemci.

`httpx` FONKSİYON İÇİNDE import edilir. Gerekçe `km_core/files/reports.py`
ile aynı: paket kurulu değilse çekirdek yine ayağa kalkar, yalnız bu yetenek
anlaşılır hata döner (K7). Merkez henüz kurulmamışken bir import hatasının tüm
uygulamayı düşürmesi, ADR 0021'in "servis yoksa hiçbir yetenek gerilemez"
sonucuyla çelişirdi.

İçeride import edilmesi ilan edilmediği anlamına GELMEZ (K11): `httpx`
`backend/pyproject.toml` içinde `identity-sync` extra'sı altında ilan edilir ve
`all` listesine katılır. `notify` de httpx getirir; ikisi ayrı ilan edilir çünkü
biri kurulmadan öteki kullanılabilir.
"""

from __future__ import annotations

from typing import Any

import structlog

from .errors import IdentitySyncError, PairRejected

log = structlog.get_logger("km.identity_sync")

# Kurulumun kendini tanıttığı başlık. Merkez bunu kayda geçirir; kurulum
# listesindeki "sürüm" sütunu buradan gelir.
CLIENT_VERSION = "0.1.0"


def _httpx() -> Any:
    try:
        import httpx
    except ImportError as error:  # pragma: no cover — ortamda kurulu
        raise IdentitySyncError(
            "httpx kurulu değil; kimlik senkronu çalışamaz."
        ) from error
    return httpx


class IdentityClient:
    """Tek sorumluluk: HTTP. Karar vermez, önbellek bilmez, kasa görmez."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------ yardım

    def _headers(self, token: str | None, actor_id: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if actor_id:
            # Kişi ayrı bildirilir: eşleme token'ı kullanıcı oturumu DEĞİLDİR
            # (ADR 0021 §4).
            headers["X-KM-Actor-Id"] = actor_id
        return headers

    async def _request(self, method: str, path: str, *, token: str | None = None,
                       actor_id: str | None = None, json_body: Any = None,
                       params: dict[str, Any] | None = None) -> Any:
        httpx = _httpx()
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.request(
                    method, url, headers=self._headers(token, actor_id),
                    json=json_body, params=params,
                )
        except Exception as error:
            # httpx'in hata ağacı geniş (bağlantı, DNS, TLS, zaman aşımı) ve
            # hepsinin ekrandaki karşılığı aynı: merkeze ulaşılamadı. Tek tek
            # yakalamak, bir gün eklenen yeni bir türü sessizce çekirdeğe
            # sızdırırdı (K7).
            raise IdentitySyncError(f"Kimlik servisine ulaşılamadı: {error}") from error

        if response.status_code >= 400:
            message = _message_of(response)
            raise IdentityResponseError(response.status_code, message)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # -------------------------------------------------------------- uçlar

    async def health(self) -> dict[str, Any]:
        return dict(await self._request("GET", "/health"))

    async def pair(self, *, code: str, public_key: str, machine_name: str,
                   platform: str, version: str = CLIENT_VERSION) -> dict[str, Any]:
        try:
            result = await self._request(
                "POST", "/pair",
                json_body={
                    "code": code,
                    "publicKey": public_key,
                    "machineName": machine_name,
                    "platform": platform,
                    "version": version,
                },
            )
        except IdentityResponseError as error:
            if error.status in (401, 422):
                raise PairRejected("Eşleme kodu geçersiz.") from error
            raise
        return dict(result)

    # --------------------------------------------------------- kurulumlar
    #
    # BU ÜÇÜ YÖNETİM TOKEN'IYLA KONUŞUR, kurulum token'ıyla değil. Merkezde
    # `/installations*` uçlarının kapısı `require_admin`dir (`services/identity/
    # app/auth.py`): kurulum token'ı "bu makine bizim" der, "bu makine yeni
    # makineler kaydedebilir" demez. İkisini aynı anahtara bağlamak, eşlenmiş
    # HER makineyi yeni makine eşleyebilir hâle getirirdi.

    async def create_pair_code(self, admin_token: str, *,
                               note: str | None = None) -> dict[str, Any]:
        """Tek kullanımlık, süreli kod üretir. SÜREYİ MERKEZ SÖYLER: `expiresAt`
        yanıtta gelir, burada hesaplanmaz."""
        return dict(await self._request(
            "POST", "/installations/pair-code", token=admin_token,
            json_body={"note": note},
        ))

    async def installations(self, admin_token: str) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/installations", token=admin_token)
        rows = payload.get("installations") if isinstance(payload, dict) else None
        return list(rows or [])

    async def revoke_installation(self, admin_token: str,
                                  installation_id: str) -> dict[str, Any]:
        """Kurulumu iptal eder. SATIR SİLİNMEZ — merkez `status` alanını
        değiştirir ve `revokedAt` yazar."""
        payload = await self._request(
            "POST", f"/installations/{installation_id}/revoke", token=admin_token
        )
        return dict((payload or {}).get("installation") or {})

    async def roster(self, token: str, *, known_revision: int | None = None) -> dict[str, Any]:
        params = {} if known_revision is None else {"known_revision": known_revision}
        return dict(await self._request("GET", "/roster", token=token, params=params))

    async def provisioning(self, token: str, *,
                           known_revision: int | None = None) -> dict[str, Any]:
        """Kurulum paketi: dağıtılacak sırlar ve modül ayarları (ADR 0025).

        KURULUM TOKEN'IYLA çekilir, yönetim token'ıyla değil: paket makinenin
        kendi kurulumudur ve sidecar onu henüz kimse giriş yapmamışken kurmak
        zorundadır. Merkezde kapı `require_installation`dır — iptal edilmiş bir
        kurulum 401 alır ve hiçbir sır göremez.

        `known_revision` gönderilir: değişmemişse merkez sırları HİÇ TAŞIMAZ.
        """
        params = {} if known_revision is None else {"known_revision": known_revision}
        return dict(await self._request("GET", "/provisioning", token=token, params=params))

    async def create_user(self, token: str, actor_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return dict(await self._request(
            "POST", "/users", token=token, actor_id=actor_id, json_body=body
        ))

    async def update_user(self, token: str, actor_id: str, user_id: str,
                          body: dict[str, Any]) -> dict[str, Any]:
        return dict(await self._request(
            "PUT", f"/users/{user_id}", token=token, actor_id=actor_id, json_body=body
        ))

    async def set_status(self, token: str, actor_id: str, user_id: str,
                         status: str) -> dict[str, Any]:
        return dict(await self._request(
            "POST", f"/users/{user_id}/status", token=token, actor_id=actor_id,
            json_body={"status": status},
        ))

    async def push_audit(self, token: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        return dict(await self._request(
            "POST", "/audit", token=token, json_body={"entries": entries}
        ))

    async def acquire_lock(self, token: str, *, resource: str, holder_name: str,
                           ttl_seconds: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"resource": resource, "holderName": holder_name}
        if ttl_seconds is not None:
            body["ttlSeconds"] = ttl_seconds
        return dict(await self._request("POST", "/locks", token=token, json_body=body))

    async def release_lock(self, token: str, lock_id: str) -> dict[str, Any]:
        return dict(await self._request("DELETE", f"/locks/{lock_id}", token=token))


class IdentityResponseError(IdentitySyncError):
    """Merkez isteği reddetti. Merkezin CÜMLESİ taşınır — kendi cümlemizle
    değiştirmek, sunucunun anlattığı durumu gizlerdi."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _message_of(response: Any) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"İstek başarısız ({response.status_code})."
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if payload.get("detail"):
            return str(payload["detail"])
    return f"İstek başarısız ({response.status_code})."
