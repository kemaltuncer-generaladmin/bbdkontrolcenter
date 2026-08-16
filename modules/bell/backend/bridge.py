"""Windows zil ajanı köprüsü — bbdstore istemcisi.

NEDEN BBDSTORE: okulun zil sistemine bağlı Windows bilgisayarı ile Kontrol
Merkezi aynı ağda değil ve ikisi de sabit adres taşımıyor. Aradaki tek ortak
nokta zaten ayakta duran mağaza sunucusu. Köprü orada, kimsenin elle
dokunmayacağı ayrı bir uç kümesinde durur (`/api/bell/*`).

BU İSTEMCİ MAĞAZAYI YÖNETMEZ. `store_api` geçidiyle karıştırılmamalı: orası
ürün/sipariş yazan, gerekçe ve kuru prova isteyen bir kapıdır. Burada tek iş
"şu sesi şu anda çal" komutunu kuyruğa bırakmak. Bu yüzden ayrı bir belirteç
kullanılır ve mağazanın yönetim belirteci ajana hiç verilmez.

ZAMANLAMA: ajan 3 saniyede bir sorar. Saniye hassasiyeti komutun taşıdığı
`playAt` damgasından gelir — komut zil saatinden `lead_seconds` kadar önce
yazılır, ajan o ana dek bekler. Ajanın kendi planı yoktur; elindeki komut
yarım dakikadan fazla yaşamaz.

KÖPRÜ ÇÖKERSE ÇEKİRDEK DÜŞMEZ (K7). Her hata yakalanır, anlaşılır bir metne
çevrilir ve çağırana `{ok: False, detail: …}` olarak döner; ekran bunu kırmızı
gösterir. Sessizce yutulmaz.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx

#: Kasadaki belirteç anahtarı. `server.<uygulama>.<alan>` DEĞİL: bu belirteç
#: sunucuya değil, mağaza üzerindeki zil köprüsü uygulamasına aittir —
#: `store.admin_token` ile aynı gerekçe.
TOKEN_KEY = "bell.bridge_token"

BASE_PATH = "/api/bell"

#: Durum sorgusunun üst sınırı (saniye). Ekranın açılış yolunda olduğu için
#: genel zaman aşımından bağımsız ve kısa tutulur.
STATUS_TIMEOUT = 6.0

#: Durum bu kadar süre önbellekte tutulur. Panel on saniyede bir tazeleniyor;
#: iki kullanıcı aynı anda bakarken köprüye iki kat istek gitmesin.
STATUS_CACHE_SECONDS = 5.0


class BridgeError(RuntimeError):
    """Köprüye ulaşılamadı ya da köprü reddetti."""


class BellBridge:
    """Köprüye komut yazan ve ajan durumunu okuyan ince istemci."""

    def __init__(self, *, secrets: Any, log: Any, base_url: str,
                 timeout: float = 20.0, lead_seconds: int = 30,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._secrets = secrets
        self._log = log
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self.lead_seconds = max(0, int(lead_seconds))
        self._transport = transport
        self._token: str | None = None
        self._status_cache: tuple[float, dict[str, Any]] | None = None

    # ------------------------------------------------------------- kimlik

    async def _bearer(self) -> str:
        if self._token is None:
            token = await self._secrets.get(TOKEN_KEY)
            if not token:
                raise BridgeError(
                    "Köprü belirteci kasada yok. `config/local.yaml` içine "
                    f"`secrets: {{\"{TOKEN_KEY}\": \"…\"}}` yazılmalı."
                )
            self._token = str(token)
        return self._token

    async def _call(self, method: str, path: str, *,
                    body: dict[str, Any] | None = None,
                    timeout: float | None = None) -> dict[str, Any]:
        token = await self._bearer()
        url = f"{self._base}{BASE_PATH}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout or self._timeout,
                                         transport=self._transport) as client:
                response = await client.request(
                    method, url, json=body,
                    headers={"Authorization": f"Bearer {token}",
                             "Accept": "application/json"},
                )
        except httpx.HTTPError as failure:
            raise BridgeError(f"Köprüye ulaşılamadı: {failure}") from None

        if response.status_code == 503:
            raise BridgeError(
                "Köprü kapalı. Mağaza sunucusunda `BBD_BELL_ENABLED=true` olmalı."
            )
        if response.status_code in (401, 403):
            raise BridgeError("Köprü belirteci reddedildi (401/403).")
        if response.status_code >= 400:
            raise BridgeError(
                f"Köprü hatası ({response.status_code}): {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError:
            raise BridgeError("Köprü yanıtı JSON değil.") from None
        return payload if isinstance(payload, dict) else {"data": payload}

    # -------------------------------------------------------------- komut

    async def send(self, items: list[dict[str, Any]], *, play_at: str = "") -> dict[str, Any]:
        """Sırayla çalınacak sesleri kuyruğa yazar.

        `items`: `[{"kind": "zil"|"anons", "hash": "<özet>", "volume": 90}]`
        Tek komut, birden çok ses taşır: zil ve arkasından gelen anons aynı
        komutta gider. İkisi ayrı komut olsaydı ajan araya başka bir çalma
        sokabilir ya da birini kaçırabilirdi.
        """
        return await self._call(
            "POST", "/command", body={"playAt": play_at, "items": items},
        )

    async def status(self) -> dict[str, Any]:
        """Ajan görünüyor mu, yerelinde hangi sesler var.

        ZAMAN AŞIMI KISA. Bu çağrı ekranın açılış yolunda: panel her on
        saniyede durumu tazeliyor. Genel 20 saniyelik süre burada kullanılsaydı,
        köprü düştüğünde ekran her tazelemede yirmi saniye kilitlenirdi. Bir
        durum sorgusu bu kadar beklemeye değmez — cevap gelmiyorsa ajan zaten
        "görünmüyor" sayılır.
        """
        return await self._call("GET", "/status", timeout=min(self._timeout, STATUS_TIMEOUT))

    async def upload(self, name: str, data: bytes) -> dict[str, Any]:
        """Ses dosyasını köprüye koyar; ajan oradan indirir.

        Dosya base64 ile gider. Tauri kabuğunda dosya sistemi eklentisi yok ve
        zil sesleri birkaç yüz kilobayt — multipart kurmanın karşılığı yok.

        `name` yalnız günlük içindir: köprü kimliği İÇERİKTEN üretir, gönderilen
        ada güvenmez.
        """
        return await self._call(
            "POST", "/sound",
            body={"name": name, "data": base64.b64encode(data).decode("ascii")},
        )

    async def prune(self, keep: list[str]) -> dict[str, Any]:
        """Köprüde artık gerekmeyen sesleri siler.

        Karar Kontrol Merkezi'nindir: hangi seslerin hâlâ gerektiğini bilen
        taraf odur. Köprü kendi başına silmeye kalksaydı, kullanılan bir sesi
        atma riski olurdu.
        """
        return await self._call("POST", "/prune", body={"keep": keep})

    # ------------------------------------------------------------ yardımcı

    async def try_send(self, items: list[dict[str, Any]], *,
                       play_at: str = "") -> dict[str, Any]:
        """`send` ama patlamaz: sonucu `{ok, detail}` olarak döner.

        Zil tetikleyicisi bunun içinden geçer. Köprü çöktü diye zamanlayıcı
        turunun patlaması, o turdaki diğer işleri de düşürürdü.
        """
        try:
            result = await self.send(items, play_at=play_at)
        except BridgeError as failure:
            return {"ok": False, "detail": str(failure)}
        return {"ok": True, "detail": f"köprü kuyruğu #{result.get('id', '?')}"}

    async def try_status(self) -> dict[str, Any]:
        """Ajan durumu. İKİ AYRI ses listesi döner, karıştırılmamalı:

        · `sounds`       — AJANIN yerelindeki dosyalar. Ekran "ajanda 7/7 ses"
                           derken bunu okur; ajan hiç bağlanmadıysa boştur.
        · `bridgeSounds` — KÖPRÜDEKİ dosyalar. Yükleme kararı buna bakar.

        İkisini karıştırmak, ajan bağlanana kadar her turda bütün sesleri
        yeniden yüklemek demekti.
        """
        now = time.monotonic()
        if self._status_cache is not None:
            stamp, cached = self._status_cache
            if now - stamp < STATUS_CACHE_SECONDS:
                return cached

        try:
            payload = await self.status()
        except BridgeError as failure:
            result = {"online": False, "error": str(failure),
                      "sounds": [], "bridgeSounds": []}
        else:
            result = {
                "online": bool(payload.get("online")),
                "lastSeen": str(payload.get("lastSeen") or ""),
                "lastAck": payload.get("lastAck") or {},
                "sounds": [str(item) for item in (payload.get("sounds") or [])],
                "bridgeSounds": [str(item) for item in (payload.get("bridgeSounds") or [])],
                "error": "",
            }

        # Hata da önbelleğe girer: köprü düştüğünde her ekran tazelemesinin
        # yeniden zaman aşımı beklemesi, arızayı iki katına çıkarırdı.
        self._status_cache = (now, result)
        return result

    def forget_status(self) -> None:
        """Önbelleği düşürür — bir şey yükledikten hemen sonra taze durum gerekir."""
        self._status_cache = None
