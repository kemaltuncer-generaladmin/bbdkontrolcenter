"""Kantin kasa API istemcisi.

CANLI SİSTEME BAĞLANIR. Üç kural:

1. **Sunucu otoritedir.** Bakiye, cari ve işlem kantinde hesaplanır; burada
   kopyası tutulmaz. Okunan veri anlık gösterilir.
2. **Yalnızca değişen alan yazılır.** Kantin `POST /api/students` ucu, istekte
   BULUNAN alanı günceller; bulunmayanı korur. Kontrol Merkezi bu yüzden tam
   nesne değil, yalnızca dokunduğu alanları gönderir — kasa tabletinin aynı
   anda yazdığı alanın üstüne geçmez.
3. **İkinci cihazız.** Kantin çok cihazlıdır; her cihazın kendi token'ı olur.
   Tabletler etkilenmez, kayıtları değişmez.

Uç noktalar (kantin `routes/api.php`):
    POST /api/auth/device                   cihaz kaydı → token   (enrollment secret)
    GET  /api/device/qr-key                 kart QR anahtarı (base64)
    GET  /api/kiosks                        kiosk listesi (kod DÖNMEZ, yalnız `usable`)
    POST /api/kiosks                        kiosk aç + İLK eşleme kodu
    PATCH /api/kiosks/{id}                  kiosk adını değiştir
    POST /api/kiosks/{id}/pairing-code      yeni eşleme kodu (eskisini geçersiz kılar)
    POST /api/kiosks/{id}/revoke            eşlemeyi iptal et (token silinir)
    GET  /api/status                        kasa üst bilgisi (son yedek zamanı dahil)
    GET  /api/students                      öğrenci listesi + bakiye
    POST /api/students                      öğrenci ekle/güncelle (opaqueId ile idempotent)
    GET  /api/products                      ürün listesi (pasifler dahil)
    POST /api/products                      ürün ekle/güncelle (id→barkod→yeni; görsel multipart)
    POST /api/stock/adjustments             stok düzeltme (localId ile idempotent)
    GET  /api/transactions                  işlem geçmişi (kalem kırılımlı, from/to)
    POST /api/transactions                  tek satış
    POST /api/transactions/batch            toplu satış (en çok 200)
    POST /api/transactions/{id}/reverse     satış iptali — SİLMEZ, ters kayıt yazar
    GET  /api/reports/daily                 günlük özet
    GET  /api/reports/collections           dönemsel tahsilat
    GET  /api/reports/dashboard             pano rakamları

Veli kantin kontrolleri sözleşmesi (bbd-akademi `docs/integrations/
kantin-veli-kontrolleri.md` §1.3/1.5/1.6) ile gelenler:
    GET   /api/students/{id}                tekil öğrenci + spentToday + kontroller
    PATCH /api/students/{id}/controls       borç/günlük limit, engeller, yasaklı ürünler
    POST  /api/payment-requests             isteğe bağlı `amount` (serbest tutar)
    GET   /api/payment-requests?updatedSince=&limit=   ödeme talebi akışı
    GET   /api/notifications?updatedSince=&limit=      veli SMS olay akışı
    GET   /api/settings                     allowPrepayment / maxPrepayment

PARA HER YERDE KURUŞ (tam sayı), ZAMAN EPOCH-MİLİSANİYE. Dönüşüm çağıranın işidir.
Tek istisna yeni uçların `…At` alanlarıdır: sözleşme ISO der, eski kantin
epoch-ms döner; `_iso` ikisini de ISO'ya çevirir.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

#: Kurumun saat dilimi — sözleşme zamanları `+03:00` ile yazar.
_ISTANBUL = ZoneInfo("Europe/Istanbul")

#: `PATCH /api/students/{id}/controls` gövdesinin kabul ettiği alanlar.
CONTROL_FIELDS = frozenset({
    "spendingLimit", "dailyLimit", "isBlocked", "guardianBlocked", "restrictedProductIds",
})

#: Limitlerin kantin doğrulamasındaki aralığı (kuruş).
LIMIT_MAX = 100_000_000

#: Yasaklı ürün listesinin üst sınırı (kantin doğrulaması).
RESTRICTED_MAX = 200

#: Akış uçlarının kantin üst sınırı.
FEED_LIMIT_MAX = 500

#: Veli bildirim akışından dışarı çıkabilecek alanlar. SMS METNİ VE TELEFON
#: YOKTUR (sözleşme A6) — kantin eski sürümse ve döndürse bile burada düşer.
_NOTIFICATION_FIELDS = ("id", "studentId", "kind", "status", "amount", "ref",
                        "createdAt", "updatedAt")

#: Ödeme talebini kimin açtığı (kantin `PaymentRequest::ORIGINS`).
PAYMENT_ORIGINS = ("admin", "bulk", "threshold", "api")

#: İmleç yokken akışın başlangıcı. Akış kipi yalnız `updatedSince` ile açılır.
FEED_START = "1970-01-01T00:00:00Z"

#: Yankı penceresi (sn) ve bellekte tutulan en çok anahtar (§6.2).
ECHO_WINDOW_SECONDS = 2.0
ECHO_MAX_KEYS = 2000


class CanteenError(RuntimeError):
    """Kantin API'si beklenmedik yanıt verdi."""

    def __init__(self, message: str, *, status: int | None = None,
                 reason: str | None = None,
                 payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        # Kantinin iş kuralı kodu: blocked / limit / stock / student_not_found ...
        self.reason = reason
        # Hata gövdesinin kendisi — ör. `exceeds_debt` ile gelen `balance` ve
        # `maxAmount`. Çağıran "en çok ne kadar?" sorusunu buradan yanıtlar.
        # `message`, `errors`, `exception`, `trace` gövdeden düşmüştür.
        self.payload = dict(payload or {})

    @property
    def details(self) -> dict[str, Any]:
        """Gövdenin ayrıntıları (`payload` ile AYNI sözlük).

        Tüketici (mobil geçit) sınıfı import edemez (K3); `{balance, maxAmount}`
        gibi ayrıntıyı bu adla okur ve kendi yanıtına taşır.
        """
        return self.payload


#: Kantinin tek batch isteğinde kabul ettiği en çok kalem (`TransactionController`).
BATCH_LIMIT = 200

#: Bağlantı kurma (TCP + TLS) için en çok bekleme, saniye. Okuma süresi
#: ayardan gelir (`timeout_seconds`); bağlantı kurulamıyorsa kantin kapalıdır
#: ve bunu okuma süresi kadar beklemek gereksizdir.
CONNECT_TIMEOUT = 5.0

#: Kantin bağlantı havuzu. KM tek süreçtir; eşzamanlı kantin isteği sayısı
#: panel + mobil geçit trafiğiyle sınırlıdır. Boşta bağlantı 30 sn tutulur
#: (kantinin önündeki vekil genellikle 60 sn'de kapatır).
POOL_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10,
                           keepalive_expiry=30.0)


class CanteenApi:
    """`canteen.api` yeteneğinin uygulaması."""

    def __init__(self, *, base_url: str, device_name: str, secrets: Any, log: Any,
                 timeout: float = 20.0, connect_timeout: float = CONNECT_TIMEOUT,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._device_name = device_name
        self._secrets = secrets
        self._log = log
        self._timeout = timeout
        self._connect_timeout = min(connect_timeout, timeout)
        # Testler `httpx.MockTransport` verir; üretimde None (gerçek ağ).
        self._transport = transport
        self._token: str | None = None
        self._http: httpx.AsyncClient | None = None
        self._http_loop: asyncio.AbstractEventLoop | None = None
        #: YANKI PENCERESİ (km-merkezi-senkron §6.2): KM'nin kantine az önce
        #: yazdığı öğrenci / kontrol alanları. Kantin aynı değişikliği olay olarak
        #: geri ittiğinde (2 sn içinde) KM yeni olay üretmez. Bellekte, sınırlı.
        self._recent: dict[tuple[str, str], tuple[float, frozenset[str]]] = {}

    def _timeouts(self, read: float | None = None) -> httpx.Timeout:
        """Okuma süresi ayardan (`timeout_seconds`), bağlantı kurma süresi KISA.

        Kantin ulaşılamıyorsa bunu 20 saniyede değil birkaç saniyede bilmek
        gerekir: öğrenci ekranı ve mobil geçit o süre boyunca bekler.
        """
        value = read or self._timeout
        return httpx.Timeout(value, connect=self._connect_timeout,
                             pool=self._connect_timeout)

    def _client(self) -> httpx.AsyncClient:
        """Kantine giden TEK, kalıcı istemci (HTTP keep-alive + bağlantı havuzu).

        Eskiden her istek yeni bir `AsyncClient` açıp kapatıyordu: her çağrı
        yeni TCP + TLS el sıkışması demekti (kantin uzak sunucuda; istek
        başına onlarca milisaniye). Havuz bağlantıyı açık tutar.

        İstemci olay döngüsüne bağlıdır; döngü değişirse (testler, yeniden
        başlatma) yenisi kurulur — eski döngünün bağlantısı başka döngüde
        kullanılamaz.
        """
        loop = asyncio.get_running_loop()
        client = self._http
        if client is None or client.is_closed or self._http_loop is not loop:
            client = httpx.AsyncClient(timeout=self._timeouts(), transport=self._transport,
                                       limits=POOL_LIMITS)
            self._http, self._http_loop = client, loop
        return client

    async def aclose(self) -> None:
        """Havuzdaki bağlantıları kapatır (kapanışta ya da testte)."""
        client, self._http, self._http_loop = self._http, None, None
        if client is not None and not client.is_closed:
            await client.aclose()

    # ------------------------------------------------------------- kimlik

    async def _ensure_token(self) -> str:
        """Cihaz token'ı: kasada varsa kullanılır, yoksa bir kez kayıt olunur."""
        if self._token:
            return self._token

        stored = await self._secrets.get("canteen.device_token")
        if stored:
            self._token = stored
            return stored

        secret = await self._secrets.get("canteen.enrollment_secret")
        if not secret:
            raise CanteenError(
                "Kantin kayıt sırrı yok. config/local.yaml → secrets.canteen.enrollment_secret"
            )

        response = await self._client().post(
            f"{self._base}/api/auth/device",
            json={"name": self._device_name, "enrollmentSecret": secret},
        )
        if response.status_code != 201:
            raise CanteenError(f"Cihaz kaydı reddedildi ({response.status_code}).")

        data = response.json()
        token = str(data.get("token") or "")
        if not token:
            raise CanteenError("Cihaz kaydından token gelmedi.")

        await self._secrets.set("canteen.device_token", token)
        # QR anahtarı da kayıtla birlikte gelir; kasaya ikinci istek atmayalım.
        if data.get("qrKey"):
            await self._secrets.set("canteen.qr_key", str(data["qrKey"]))

        self._token = token
        self._log.info("kantine cihaz olarak kaydolundu", device=self._device_name)
        return token

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._send(method, path, **kwargs)
        return response.json() if response.content else None

    async def _send(self, method: str, path: str, *,
                    headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        """İsteği gönderir; 4xx/5xx'i `CanteenError`a çevirir, yanıtın kendisini döner.

        `_request` gövdeyi çözer; ETag gibi başlık gereken çağrılar (bakiye
        uzlaştırması) bunu doğrudan kullanır. 304 hata değildir.
        """
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                   **(headers or {})}

        # Kantin SQLite üzerinde çalışıyor: eşzamanlı okumalarda kilit yüzünden
        # tek seferlik 5xx görülebiliyor. OKUMA istekleri güvenle yinelenir.
        # YAZMA yinelenmez — idempotency `localId` ile sağlanır, körlemesine
        # tekrar göndermek yanlış olur.
        attempts = 3 if method.upper() == "GET" else 1
        response = None
        for attempt in range(attempts):
            try:
                response = await self._client().request(
                    method, f"{self._base}{path}", headers=headers, **kwargs
                )
            except httpx.TransportError as failure:
                if attempt + 1 >= attempts:
                    raise CanteenError(f"{method} {path} → bağlantı hatası: {failure}") from failure
                await asyncio.sleep(0.4 * (attempt + 1))
                continue

            if response.status_code < 500 or attempt + 1 >= attempts:
                break
            self._log.warning("kantin geçici hata verdi, yineleniyor",
                              path=path, status=response.status_code)
            await asyncio.sleep(0.4 * (attempt + 1))

        if response is None:  # pragma: no cover - yukarıdaki döngü garanti eder
            raise CanteenError(f"{method} {path} → yanıt alınamadı.")

        if response.status_code == 401:
            # Token geçersiz kılınmış olabilir. Kasada YENİ cihaz açmadan önce
            # durumu bildiriyoruz: sessizce ikinci kayıt açmak, kantinde çöp
            # cihaz kayıtları biriktirir.
            raise CanteenError(
                "Kantin cihaz token'ı geçersiz. Kasadan token iptal edilmişse "
                "kasadaki `canteen.device_token` sırrı silinip yeniden kayıt gerekir."
            )
        if response.status_code >= 400:
            # Kantin iş kuralı hatalarını `reason` koduyla döner (422). Kodu ayıklayıp
            # taşıyoruz: çağıran modül "stok yetmedi" ile "limit aşıldı"yı ayırabilsin.
            reason, message = None, response.text[:200]
            body_dict: dict[str, Any] = {}
            try:
                body = response.json()
                if isinstance(body, dict):
                    body_dict = body
                    reason = body.get("reason")
                    message = str(body.get("message") or message)
                    # Laravel'in kendi doğrulama hatası (`errors` sözlüğü) iş
                    # kuralı kodu taşımaz; çağıran onu da ayırt edebilsin.
                    if reason is None and response.status_code == 422 \
                            and isinstance(body.get("errors"), dict):
                        reason = "validation_error"
            except ValueError:
                pass
            raise CanteenError(
                f"{method} {path} → {response.status_code}: {message}",
                status=response.status_code,
                reason=reason,
                payload={key: value for key, value in body_dict.items()
                         if key not in ("message", "errors", "exception", "trace")},
            )

        return response

    # ---------------------------------------------------------------- veri

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/api/status")

    async def dashboard(self) -> dict[str, Any]:
        """Kasa panosunun rakamları: bugünkü satış, açık alacak, bekleyen talep,
        engelli öğrenci, toplam öğrenci. Tablet hangi sayıyı görüyorsa aynısı."""
        return await self._request("GET", "/api/reports/dashboard")

    async def students(self) -> list[dict[str, Any]]:
        """Öğrenciler — kantin otoritesindeki hâliyle (bakiye dahil)."""
        payload = await self._request("GET", "/api/students")
        data = payload.get("data") if isinstance(payload, dict) else payload
        return list(data or [])

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Öğrenciyi ekler/günceller.

        `changes` YALNIZCA dokunulan alanları içermeli: displayName, parentPhone,
        spendingLimit, isBlocked. Gönderilmeyen alan kantinde olduğu gibi kalır.
        """
        allowed = {"displayName", "parentPhone", "spendingLimit", "isBlocked"}
        unknown = set(changes) - allowed
        if unknown:
            raise CanteenError(f"Kantinde olmayan alan gönderilemez: {', '.join(sorted(unknown))}")
        if "displayName" not in changes:
            # Kantin `displayName` alanını zorunlu tutuyor; yoksa istek 422 döner.
            raise CanteenError("displayName zorunlu (kantin doğrulaması).")

        payload = await self._request(
            "POST", "/api/students", json={"opaqueId": opaque_id, **changes}
        )
        self.remember_write(opaque_id, "student", changes)
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def issue_student_access_code(self, opaque_id: str) -> dict[str, Any]:
        """Öğrenciye YENİ 6 haneli giriş kodu verir ve düz hâlini döndürür.

        KOD SUNUCUDA DOĞAR, burada üretilmez. Tekilliği kantinin veritabanı
        indeksi tutuyor (`students.access_code_hash` unique); Kontrol Merkezi
        rastgele bir sayı üretip gönderseydi, iki yönetici aynı anda aynı sayıyı
        seçebilir ve kod kimi açtığı belirsiz bir anahtara dönerdi.

        DÜZ KOD YALNIZ BU YANITTA GÖRÜNÜR. Kantin kodu şifreli saklıyor ve
        listede de veriyor, ama üretim anındaki bu yanıt "yeni kod budur"
        diyen tek yerdir; ekran onu bir kez gösterir.

        Eski kod bu çağrıyla GEÇERSİZLEŞİR.
        """
        payload = await self._request("POST", f"/api/students/{opaque_id}/access-code")
        return dict(payload or {})

    async def lookup_student_by_code(self, code: str) -> dict[str, str]:
        """Resolve a student's canteen access code to a minimal identity.

        The device bearer stays in this server-side client. The canteen's
        lookup response also contains balance, contact details, and the access
        code, so only the opaque student id and display name cross this seam.
        Never log or include the submitted code in an error.
        """
        payload = await self._request(
            "POST", "/api/students/lookup-code", json={"code": code}
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        student_id = data.get("id") if isinstance(data, dict) else None
        display_name = data.get("displayName") if isinstance(data, dict) else None
        if not isinstance(student_id, str) or not student_id.strip() \
                or not isinstance(display_name, str) or not display_name.strip():
            raise CanteenError("Kantin öğrenci kodu yanıtı geçersiz.")

        return {"id": student_id, "displayName": display_name}

    # ---------------------------------------------------------------- kiosk
    #
    # KIOSK ≠ CİHAZ. Sahadaki tablet `devices` üzerinde, paylaşılan
    # `enrollment_secret` ile çalışıyor ve o akış DEĞİŞMEDİ. Kiosk eşlemesi
    # ayrı bir tabloya (`kiosks`) ve ayrı uçlara oturur: tek kullanımlık,
    # süreli, hash'lenmiş kod. İkisini tek istemci metoduna toplamak, hangi
    # akışın çağrıldığını çağıranın gözünden gizlerdi.
    #
    # KOD YALNIZ ÜRETİLDİĞİ ANDA DÖNER. `kiosks()` listesi kodu taşımaz;
    # `pairing.usable` bayrağı vardır. Kantin kodu düz saklamıyor.

    async def kiosks(self) -> list[dict[str, Any]]:
        """Kiosk kayıtları. Alanlar: id, name, platform, appVersion, paired,
        pairedAt, lastSeenAt, revokedAt, revokedReason, createdAt, pairing."""
        payload = await self._request("GET", "/api/kiosks")
        data = payload.get("data") if isinstance(payload, dict) else payload
        return list(data or [])

    async def create_kiosk(self, *, name: str) -> dict[str, Any]:
        """Kiosk kaydı açar ve İLK eşleme kodunu birlikte döndürür.

        Kod ayrı çağrıya bırakılsaydı, kaydı açılıp kodu üretilmemiş
        ("eşlenmeyi bekliyor" sanılan) kiosklar birikirdi.
        """
        payload = await self._request("POST", "/api/kiosks", json={"name": name})
        return dict(payload or {})

    async def rename_kiosk(self, kiosk_id: int, *, name: str) -> dict[str, Any]:
        """YALNIZ ad. Eşleme durumu bu uçtan değişmez."""
        payload = await self._request("PATCH", f"/api/kiosks/{int(kiosk_id)}",
                                      json={"name": name})
        return dict(payload or {})

    async def new_kiosk_pairing_code(self, kiosk_id: int, *,
                                     ttl_minutes: int | None = None) -> dict[str, Any]:
        """Yeni eşleme kodu üretir; kioskun bekleyen ESKİ kodunu geçersiz kılar.

        İptal edilmiş kioska kod üretilmez — kantin 422 `kiosk_revoked` döner ve
        `CanteenError.reason` ile çağırana ulaşır.
        """
        body: dict[str, Any] = {}
        if ttl_minutes is not None:
            body["ttlMinutes"] = int(ttl_minutes)
        payload = await self._request("POST", f"/api/kiosks/{int(kiosk_id)}/pairing-code",
                                      json=body)
        return dict(payload or {})

    async def revoke_kiosk(self, kiosk_id: int, *, reason: str) -> dict[str, Any]:
        """Eşlemeyi iptal eder: token SİLİNİR, bekleyen kod ölür, satır KALIR.

        Gerekçe kantinde de zorunludur (min 10 karakter); "bu kiosk neden
        düştü" sorusunun cevabı aylar sonra da okunabilmeli.
        """
        payload = await self._request("POST", f"/api/kiosks/{int(kiosk_id)}/revoke",
                                      json={"reason": reason})
        return dict(payload or {})

    # --------------------------------------------------------------- ürünler

    async def products(self) -> list[dict[str, Any]]:
        """Ürünler — PASİFLER DAHİL. Kantin adı sırasıyla döner.

        Alanlar: id, barcode, name, imageUrl, price (kuruş), stock, isActive, updatedAt.
        """
        payload = await self._request("GET", "/api/products")
        data = payload.get("data") if isinstance(payload, dict) else payload
        return list(data or [])

    async def upsert_product(
        self,
        fields: dict[str, Any],
        *,
        image: tuple[str, bytes, str] | None = None,
    ) -> dict[str, Any]:
        """Ürünü ekler/günceller — kantinde tek uç, idempotent upsert.

        Kantin hedef kaydı şu sırayla bulur: gönderilen `id` → aynı `barcode` → yeni kayıt.
        `name`, `price` (kuruş) ve `stock` kantin doğrulamasında ZORUNLUDUR; kısmi
        güncellemede bile hepsi gönderilmelidir (uç PATCH değil, upsert'tür).

        `image` verilirse istek multipart olur: (dosya adı, içerik, MIME).
        Görsel bozuksa kantin ÜRÜNÜ yine yazar, yalnız `imageStatus` uyarısı döner —
        o yüzden sonuçta `imageStatus` alanını da geri veriyoruz.
        """
        allowed = {"id", "barcode", "name", "price", "stock", "isActive"}
        unknown = set(fields) - allowed
        if unknown:
            raise CanteenError(f"Kantinde olmayan ürün alanı: {', '.join(sorted(unknown))}")
        for required in ("name", "price", "stock"):
            if required not in fields:
                raise CanteenError(f"{required} zorunlu (kantin doğrulaması).")

        if image is None:
            payload = await self._request("POST", "/api/products", json=fields)
        else:
            # Multipart'ta her alan dizgidir; kantin bunu bekliyor ve normalize ediyor.
            form = {
                key: ("true" if value else "false") if isinstance(value, bool) else str(value)
                for key, value in fields.items()
                if value is not None
            }
            filename, content, mime = image
            payload = await self._request(
                "POST", "/api/products", data=form, files={"image": (filename, content, mime)}
            )

        if not isinstance(payload, dict):
            return {}
        product = dict(payload.get("data") or {})
        product["imageStatus"] = payload.get("imageStatus", "none")
        return product

    async def adjust_stock(self, *, local_id: str, product_id: int, delta: int,
                           reason: str) -> dict[str, Any]:
        """Stok düzeltme. `local_id` idempotency anahtarıdır (aynı id ikinci kez yazmaz).

        `delta` işaretlidir: + giriş, − çıkış. Kantin negatif stoğa düşürmez.
        """
        return await self._request("POST", "/api/stock/adjustments", json={
            "localId": local_id,
            "productId": int(product_id),
            "delta": int(delta),
            "reason": reason,
        })

    # --------------------------------------------------------------- satışlar

    async def sell_batch(self, sales: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Toplu satış. Kantin en çok 200 kabul ettiği için dilimlere bölünür.

        Her kalem: {localId, studentId (opaque), createdAt (epoch-ms, geçmişe tarihleme
        için), items: [{productId, qty, unitPrice}]}.

        Dönüş, gönderilen sırayla: {localId, status: created|duplicate|failed,
        serverId | reason}. `duplicate` HATA DEĞİLDİR — aynı localId daha önce
        işlenmiş demektir; çift borç oluşmadığının kanıtıdır.

        Bir dilim tümüyle patlarsa (ağ/5xx) o dilimin kalemleri `failed/transport`
        olarak işaretlenir; diğer dilimler denenmeye devam eder — yarım kalan iş
        sessizce kaybolmaz.
        """
        results: list[dict[str, Any]] = []
        for start in range(0, len(sales), BATCH_LIMIT):
            chunk = sales[start:start + BATCH_LIMIT]
            try:
                payload = await self._request(
                    "POST", "/api/transactions/batch", json={"transactions": chunk}
                )
                results.extend(list((payload or {}).get("results") or []))
            except CanteenError as failure:
                self._log.warning("batch dilimi gönderilemedi", error=str(failure))
                results.extend(
                    {"localId": item.get("localId"), "status": "failed",
                     "reason": failure.reason or "transport"}
                    for item in chunk
                )
        return results

    async def reverse_sale(self, local_id: str, reason: str) -> dict[str, Any]:
        """Satışı geri alır. SİLMEZ: kantinde ters cari kayıt + stok iadesi + iptal damgası.

        İdempotenttir; `wasReversedNow=False` "zaten iptalliydi" demektir.
        """
        payload = await self._request(
            "POST", f"/api/transactions/{local_id}/reverse", json={"reason": reason}
        )
        if not isinstance(payload, dict):
            return {}
        result = dict(payload.get("data") or {})
        result["wasReversedNow"] = bool(payload.get("wasReversedNow"))
        return result

    async def transactions(self, *, from_ms: int | None = None, to_ms: int | None = None,
                           limit: int | None = None,
                           student_id: str | None = None) -> list[dict[str, Any]]:
        """İşlem geçmişi, kalem kırılımlı. `to_ms` HARİÇTİR (yarı açık aralık).

        Aralık verilmezse son 100 işlem; verilirse en çok 5000 satır döner.
        Her satır: serverId, studentId (opaque), studentName, total, createdAt,
        reversedAt, reversedReason, items[].

        `student_id` verilirse YALNIZ o öğrencinin satırları döner. `studentId`
        sorguya eklenir: öğrenci süzgecini tanıyan kantin (`GET
        /api/transactions?studentId=`, kantin-veli dalı) sunucuda `(student_id,
        id)` indeksiyle süzer; tanımayan eski kantin parametreyi yok sayar.
        Satırlar HER DURUMDA burada da süzülür: başka öğrencinin satırı hiçbir
        sürümde dışarı çıkmaz. Sınır aynıdır: aralıkta en çok 5000 satır
        okunur (eski kantinde kurum geneli, yenisinde öğrencinin); öğrenci
        ekranı için aralığı dar tutun.
        """
        params: dict[str, Any] = {}
        if from_ms is not None:
            params["from"] = int(from_ms)
        if to_ms is not None:
            params["to"] = int(to_ms)
        if limit is not None:
            params["limit"] = int(limit)
        wanted = str(student_id or "").strip()
        if wanted:
            params["studentId"] = wanted
        payload = await self._request("GET", "/api/transactions", params=params)
        data = payload.get("data") if isinstance(payload, dict) else payload
        rows = list(data or [])
        if wanted:
            rows = [row for row in rows
                    if isinstance(row, dict) and str(row.get("studentId") or "") == wanted]
        return rows

    # --------------------------------------------------------------- raporlar

    async def daily(self, date: str | None = None) -> dict[str, Any]:
        """Günlük özet (YYYY-MM-DD; boşsa bugün). İptalli satışlar sayılmaz."""
        params = {"date": date} if date else {}
        return await self._request("GET", "/api/reports/daily", params=params)

    async def collections(self, *, from_ms: int, to_ms: int,
                          student_id: str | None = None) -> dict[str, Any]:
        """Dönemsel tahsilat (ödeme linki + nakit). Tutarlar kuruş."""
        params: dict[str, Any] = {"from": int(from_ms), "to": int(to_ms)}
        if student_id:
            params["studentId"] = student_id
        payload = await self._request("GET", "/api/reports/collections", params=params)
        return dict((payload or {}).get("data") or {})

    async def snapshot(self) -> dict[str, Any]:
        """Öğrenci + ürün + pano üçlüsünü tek çağrıda toplar.

        SIRAYLA okunur, paralel DEĞİL. Kantin SQLite üzerinde çalışıyor ve
        `/reports/dashboard` tüm cari defteri tarıyor; üç isteği aynı anda
        göndermek kilit yüzünden tek seferlik 5xx üretiyordu. Kazanılan birkaç
        yüz milisaniye, güvenilirliği bozmaya değmez.

        Biri düşerse diğerleri yine gelir; hangisinin düştüğü `errors` içinde
        bildirilir — ekran eksik veriyle de ayakta kalır (K7) ama SESSİZ KALMAZ.
        """
        results: dict[str, Any] = {"students": [], "products": [], "dashboard": {}}
        errors: list[str] = []
        for key, call, empty in (
            ("students", self.students, []),
            ("products", self.products, []),
            ("dashboard", self.dashboard, {}),
        ):
            try:
                results[key] = await call()
            except Exception as failure:  # noqa: BLE001 — kantin dışarısı
                results[key] = empty
                errors.append(f"{key}: {failure}")
        return {**results, "errors": errors}

    # ------------------------------------------------------- öğrenci / tahsilat

    async def student(self, opaque_id: str) -> dict[str, Any]:
        """Tek öğrenci — canlı bakiyesiyle. Listeyi baştan çekmeye gerek kalmaz."""
        payload = await self._request("GET", f"/api/students/{opaque_id}")
        return dict((payload or {}).get("data") or {})

    async def collect_cash(self, opaque_id: str, *, amount: int, local_id: str) -> dict[str, Any]:
        """Elden alınan nakit tahsilat — cari deftere CREDIT/CASH.

        `local_id` idempotency anahtarıdır: aynı id ikinci kez alacak yazmaz.
        Kantin kuralları: tutar borçtan **fazla olamaz**, borç yoksa reddedilir.
        Borç tümüyle kapanırsa o öğrencinin BEKLEYEN ödeme talepleri `EXPIRED` olur.

        Hata gövdesi `reason` taşır: `no_debt` | `exceeds_debt` | `invalid_amount`
        | `student_not_found`. `CanteenError.reason` ile çağırana ulaşır.
        """
        return await self._request(
            "POST", f"/api/students/{opaque_id}/collections",
            json={"localId": local_id, "amount": int(amount)},
        )

    async def record_collection(self, student_id: str, amount: int,
                                local_id: str) -> dict[str, Any]:
        """Nakit tahsilat (sözleşme §2.2 "collections") — `collect_cash` ile aynı uç.

        Ön ödeme kantinde açıksa sınır `borç + maxPrepayment` olur (§1.5);
        kapalıyken tutar borcu aşamaz. Sınırı KANTİN uygular; burada yalnız
        tutarın biçimi denetlenir.

        `local_id` idempotency anahtarıdır (uuid önerilir): aynı id ikinci kez
        alacak yazmaz. Dönüş `{localId, amount, balance, smsQueued}`. Red
        `CanteenError.reason`: `no_debt` · `exceeds_debt` · `invalid_amount` ·
        `student_not_found` · `validation_error`; 422'nin `payload`ı `balance`
        ve `maxAmount` taşır.
        """
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1 \
                or amount > LIMIT_MAX:
            raise CanteenError("Tahsilat tutarı geçersiz.", status=422, reason="invalid_amount")
        key = str(local_id or "").strip()
        if not key or len(key) > 64:
            raise CanteenError("Tahsilat kimliği (localId) geçersiz.", status=422,
                               reason="validation_error")
        try:
            payload = await self.collect_cash(student_id, amount=int(amount), local_id=key)
        except CanteenError as failure:
            if failure.status == 404 and not failure.reason:
                failure.reason = "student_not_found"
            raise
        data = payload if isinstance(payload, dict) else {}
        return {
            **data,
            "localId": str(data.get("localId") or key),
            "amount": int(data.get("amount") or amount),
            "balance": int(data.get("balance") or 0),
            "smsQueued": bool(data.get("smsQueued")),
        }

    # ------------------------------------------------------- veli kontrolleri

    async def get_student(self, student_id: str) -> dict[str, Any]:
        """Tekil öğrenci — bakiye, bugünkü harcama ve veli kontrolleriyle.

        Kantin sözleşmesi §1.3 alanları her zaman dolu döner; kantin henüz eski
        sürümse varsayılanları yazılır (`dailyLimit=None`, `guardianBlocked=False`,
        `restrictedProductIds=[]`, `controlsUpdatedAt=None`). `spentToday`
        kantin bildirmiyorsa `None` kalır — 0 yazmak "bugün harcamadı" demek
        olurdu, oysa bilmiyoruz.

        `accessCode` BU YANITTA BULUNMAZ: kantin döndürse bile düşürülür.
        Öğrenci yoksa `CanteenError(reason="student_not_found")`.
        """
        try:
            payload = await self._request("GET", f"/api/students/{student_id}")
        except CanteenError as failure:
            if failure.status == 404:
                failure.reason = failure.reason or "student_not_found"
            raise
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            raise CanteenError("Kantin öğrenci yanıtı geçersiz.", status=404,
                               reason="student_not_found")
        return _student_with_controls(data)

    async def update_controls(self, student_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Veli kantin kontrollerini yazar — `PATCH /api/students/{id}/controls`.

        YALNIZ GÖNDERİLEN ALAN değişir; `None` gönderilen limit temizlenir
        (sınırsız). Alanlar: `spendingLimit`, `dailyLimit` (0–100.000.000 kuruş
        ya da `None`), `isBlocked`, `guardianBlocked` (bool),
        `restrictedProductIds` (en çok 200 tekil ürün kimliği).

        Biçim burada da denetlenir: kantine gitmeden reddedilebilecek bir yama
        ağa çıkmaz. Ürünün VARLIĞINI kantin denetler (422 → `validation_error`).
        Dönüş `get_student` ile aynı biçim (güncel kontroller + spentToday).
        """
        body = _validated_controls(patch)
        try:
            payload = await self._request(
                "PATCH", f"/api/students/{student_id}/controls", json=body
            )
        except CanteenError as failure:
            if failure.status == 404:
                failure.reason = failure.reason or "student_not_found"
            raise
        self.remember_write(student_id, "controls", body)
        data = (payload or {}).get("data", payload) if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not data.get("id"):
            # Yanıt beklenen biçimde değilse güncel hâli ayrıca okunur.
            return await self.get_student(student_id)
        return _student_with_controls(data)

    # ------------------------------------------- merkezi senkron (§6.4, §1.8)

    async def set_student_status(self, student_id: str, status: str,
                                 reason: str | None = None,
                                 at: str | None = None) -> dict[str, Any]:
        """Yaşam döngüsü aynası — `PATCH /api/students/{id}/status`.

        İdempotent (aynı durum → 200, değişiklik yok). `status`: `active` |
        `inactive`; `deleted` bu uçtan verilemez. Eski kantinde uç yoksa
        `CanteenError(reason="not_supported")` — KM çalışmaya devam eder.
        """
        if status not in ("active", "inactive"):
            raise CanteenError("Durum active ya da inactive olmalı.", status=422,
                               reason="validation_error")
        body = {"status": status, "reason": reason, "at": at or _now_iso()}
        try:
            payload = await self._request(
                "PATCH", f"/api/students/{student_id}/status", json=body)
        except CanteenError as failure:
            _mark_unsupported(failure)
            raise
        self.remember_write(student_id, "status", {"status": status})
        data = (payload or {}).get("data", payload) if isinstance(payload, dict) else None
        return dict(data) if isinstance(data, dict) else {"id": student_id, "status": status}

    async def delete_student(self, student_id: str) -> dict[str, Any]:
        """Kalıcı silme — `DELETE /api/students/{id}` (kantin anonimleştirir).

        `409` kodları `CanteenError.reason` ile iletilir: `balance_not_zero`,
        `open_payment_request`. Öğrenci kantinde yoksa `student_not_found`;
        uç yoksa (eski kantin) `not_supported`.
        """
        try:
            payload = await self._request("DELETE", f"/api/students/{student_id}")
        except CanteenError as failure:
            _mark_unsupported(failure)
            raise
        return dict(payload or {}) if isinstance(payload, dict) else {}

    async def balances(self, ids: list[str] | None = None, *,
                       etag: str | None = None) -> dict[str, Any] | None:
        """Bakiye uzlaştırması — `GET /api/students/balances?ids=`.

        Dönüş `{"ledgerSeq", "items": [{"opaqueId", "balance", "ledgerSeq"}],
        "etag"}`; `etag` verildi ve defter değişmediyse `None` (304).
        """
        params: dict[str, Any] = {}
        if ids:
            params["ids"] = ",".join(str(item) for item in ids)
        headers = {"If-None-Match": etag} if etag else None
        try:
            response = await self._send("GET", "/api/students/balances", params=params,
                                        headers=headers)
        except CanteenError as failure:
            _mark_unsupported(failure)
            raise
        if response.status_code == 304:
            return None
        payload = response.json() if response.content else {}
        payload = payload if isinstance(payload, dict) else {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        return {
            "ledgerSeq": int(payload.get("ledgerSeq") or 0),
            "items": [
                {"opaqueId": str(item.get("opaqueId") or item.get("studentId") or ""),
                 "balance": int(item.get("balance") or 0),
                 "ledgerSeq": int(item.get("ledgerSeq") or 0)}
                for item in items if isinstance(item, dict)
            ],
            "etag": response.headers.get("ETag"),
        }

    async def events_after(self, after: int, limit: int = 500) -> dict[str, Any]:
        """Kantin outbox'undan çekme — `GET /api/km/events?after=&limit=` (§6.1 yedek).

        Dönüş `{"items": [{id, type, opaqueId, payload, createdAt}], "lastId",
        "hasMore"}`. Uç yoksa `not_supported`.
        """
        size = max(1, min(int(limit), 500))
        try:
            payload = await self._request(
                "GET", "/api/km/events", params={"after": max(0, int(after)), "limit": size})
        except CanteenError as failure:
            _mark_unsupported(failure)
            raise
        payload = payload if isinstance(payload, dict) else {}
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        clean = [item for item in items if isinstance(item, dict)]
        last = payload.get("lastId")
        return {
            "items": clean,
            "lastId": int(last) if last is not None else
            max([int(item.get("id") or 0) for item in clean] or [int(after)]),
            "hasMore": bool(payload.get("hasMore")),
        }

    # -------------------------------------------------------------- yankı

    def remember_write(self, student_id: str, kind: str, fields: Any) -> None:
        """KM'nin kantine yazdığı alanları kısa süre hatırlar (yankı bastırma)."""
        now = time.monotonic()
        if len(self._recent) >= ECHO_MAX_KEYS:
            self._recent = {key: value for key, value in self._recent.items()
                            if now - value[0] < ECHO_WINDOW_SECONDS}
            if len(self._recent) >= ECHO_MAX_KEYS:
                self._recent.clear()
        key = (str(student_id), kind)
        previous = self._recent.get(key)
        names = frozenset(str(name) for name in (fields or ()))
        if previous is not None and now - previous[0] < ECHO_WINDOW_SECONDS:
            names = names | previous[1]
        self._recent[key] = (now, names)

    def recently_written(self, student_id: str, kind: str, fields: Any,
                         window: float = ECHO_WINDOW_SECONDS) -> bool:
        """`fields` KM'nin son `window` saniyede yazdığı alanların alt kümesi mi."""
        entry = self._recent.get((str(student_id), kind))
        if entry is None or time.monotonic() - entry[0] > window:
            return False
        wanted = frozenset(str(name) for name in (fields or ()))
        return wanted <= entry[1]

    async def canteen_settings_public(self) -> dict[str, Any]:
        """Ön ödeme ayarı — `{"allowPrepayment": bool, "maxPrepayment": int}`.

        Kantin ayar ucundan YALNIZ bu iki alan dışarı çıkar (Netgsm kimlik
        durumu, şablonlar burada yoktur). Kantin eski sürümse ön ödeme kapalı
        sayılır.
        """
        data = await self.settings()
        allow = data.get("allowPrepayment", data.get("allow_prepayment", False))
        maximum = data.get("maxPrepayment", data.get("max_prepayment", 0))
        try:
            maximum_int = max(0, int(maximum or 0))
        except (TypeError, ValueError):
            maximum_int = 0
        return {"allowPrepayment": bool(allow), "maxPrepayment": maximum_int}

    async def payment_request(self, ref: str) -> dict[str, Any]:
        """Tekil ödeme talebi — akış satırıyla aynı gövde, TAZE `payToken` ile.

        Akıştan alınmış token'ın süresi dolduysa uygulama yenisini buradan alır
        (talep hâlâ PENDING ise; değilse `payToken` None). Yan etkisi yoktur.
        Yoksa `CanteenError(reason="not_found")`.
        """
        try:
            payload = await self._request("GET", f"/api/payment-requests/{ref}")
        except CanteenError as failure:
            if failure.status == 404:
                failure.reason = failure.reason or "not_found"
            raise
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not data.get("ref"):
            raise CanteenError("Ödeme talebi yanıtı geçersiz.", status=404, reason="not_found")
        return _payment_request_row(data)

    # ------------------------------------------------------------- akışlar
    #
    # AKIŞ KİPİ YALNIZ `updatedSince` İLE AÇILIR; verilmezse kantin eski "son
    # 100" listesini döner. İlk çekimde imleç yoksa `FEED_START` gönderilir.
    # Sınır DAHİLDİR (>=): sayfa sınırında aynı saniyede güncellenen satır
    # kaybolmasın diye. Çağıran `ref` / `id` ile tekilleştirir.

    async def payment_requests_feed(self, updated_since: str | None = None,
                                    limit: int = 200) -> dict[str, Any]:
        """Kurum geneli ödeme talebi akışı (§1.6) — `updated_at` artan sırada.

        Dönüş tek sayfadır: `{"data": [...], "nextSince": iso, "hasMore": bool}`.
        `nextSince` ve `hasMore` kantinden olduğu gibi geçer (kantin vermezse
        imleç son satırdan türetilir, `hasMore` False). Sonraki sayfa için
        `nextSince` aynen geri gönderilir; sınır dahildir, tekrar gelen satır
        `ref` ile ayıklanır.

        Her satır: ref, studentId, amount, status, amountFixed, linkUrl,
        payToken, origin, createdAt, updatedAt, paidAt (zamanlar ISO, +03:00).
        """
        payload = await self._request("GET", "/api/payment-requests",
                                      params=_feed_params(updated_since, limit))
        rows = payload.get("data") if isinstance(payload, dict) else payload
        items = [_payment_request_row(row) for row in rows or [] if isinstance(row, dict)]
        return _feed_page(payload, items, updated_since)

    async def parent_notifications_feed(self, updated_since: str | None = None,
                                        limit: int = 200) -> dict[str, Any]:
        """Veliye giden kantin SMS olayları (§1.6) — tek sayfa, imleçli.

        Dönüş `{"data": [...], "nextSince": iso, "hasMore": bool}` (bkz.
        `payment_requests_feed`). SMS METNİ VE TELEFON DÖNMEZ (A6): satır yalnız
        `id, studentId, kind, status, amount, ref, createdAt, updatedAt` taşır.
        Kantin fazlasını döndürse bile burada düşer.
        """
        payload = await self._request("GET", "/api/notifications",
                                      params=_feed_params(updated_since, limit))
        rows: Any = payload
        if isinstance(payload, dict):
            rows = payload.get("data") or payload.get("notifications") or []
        items: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            item = {key: row.get(key) for key in _NOTIFICATION_FIELDS if key in row}
            for key in ("createdAt", "updatedAt"):
                if key in item:
                    item[key] = _iso(item[key])
            items.append(item)
        return _feed_page(payload, items, updated_since)

    # ------------------------------------------------------------- ödeme talebi

    async def payment_requests(self, student_id: str | None = None) -> list[dict[str, Any]]:
        """Son 100 ödeme talebi (akış kipi DEĞİL — `updatedSince` gönderilmez).

        `student_id` verilirse yalnız o öğrencinin talepleri döner. `studentId`
        sorguya eklenir: süzgeci tanıyan kantin (kantin-veli dalı) sunucuda
        süzer ve öğrencinin son 100 talebini verir; tanımayan eski kantin
        kurum genelinde son 100 talebi verir, öğrencinin daha eski talebi o
        listede görünmeyebilir. Satırlar HER DURUMDA burada da süzülür. Açık
        (PENDING) talep öğrenci başına tektir ve hep en yenilerdendir.
        """
        wanted = str(student_id or "").strip()
        params = {"studentId": wanted} if wanted else None
        payload = await self._request("GET", "/api/payment-requests", params=params)
        data = payload.get("data") if isinstance(payload, dict) else payload
        rows = list(data or [])
        if wanted:
            rows = [row for row in rows
                    if isinstance(row, dict) and str(row.get("studentId") or "") == wanted]
        return rows

    async def create_payment_request(self, student_id: str, amount: int | None = None,
                                     origin: str = "admin") -> dict[str, Any]:
        """Ödeme talebi açar ve veliye kısa linkli SMS'i kuyruğa alır.

        `amount` VERİLMEZSE bugünkü davranış: kantin öğrencinin o anki borcunu
        kullanır; açık `PENDING` talep varsa YENİSİ AÇILMAZ, mevcut talep güncel
        borca göre yeniden fiyatlanır ve aynı `ref` döner.

        `amount` (kuruş) VERİLİRSE serbest tutarlı talep açılır (sözleşme §1.5):
        en az 100, en çok `borç + izinli ön ödeme`. Tutarlı talep yeniden
        fiyatlanmaz (`amountFixed: true`).

        RED HER ZAMAN `CanteenError` İLE GELİR, `reason` kodu taşır:
        `no_debt` · `no_phone` · `exceeds_debt` · `invalid_amount` ·
        `validation_error` · `student_not_found`. Kantin bazı redleri HTTP 200
        + `{"queued": false, "reason": …}` olarak döndürür; o biçim de burada
        hataya çevrilir — çağıran iki ayrı yolu ayırt etmek zorunda kalmaz.

        `origin` talebi kimin açtığıdır ve kantin kayda yazar: `admin`
        (personelin tek öğrenciye gönderimi — VARSAYILAN, kantinin de
        varsayılanı), `bulk` (KM toplu gönderim), `api` (mobil geçit / Akademi;
        geçit bunu AÇIKÇA gönderir), `threshold` (kantinin kendi eşiği).
        `exceeds_debt` hatasının `details` (= `payload`) sözlüğü `balance` ve
        `maxAmount` taşır. Öğrenci başına tek açık talep vardır (yeniden
        kullanılır).

        Dönüş: `{queued, ref, amount, status, linkUrl, amountFixed, payToken,
        origin, …}` (+ kantinin döndürdüğü diğer alanlar; zamanlar ISO). Eski
        kantin `status`/`linkUrl`/`payToken` döndürmezse `PENDING`/`None`/`None`.
        """
        if origin not in PAYMENT_ORIGINS:
            raise CanteenError(f"Bilinmeyen talep kaynağı: {origin}", status=422,
                               reason="validation_error")
        body: dict[str, Any] = {"studentId": student_id, "origin": origin}
        if amount is not None:
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 100 \
                    or amount > LIMIT_MAX:
                raise CanteenError("Ödeme talebi tutarı en az 1,00 ₺ olmalı.",
                                   status=422, reason="invalid_amount")
            body["amount"] = int(amount)

        payload = await self._request("POST", "/api/payment-requests", json=body)
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            raise CanteenError("Ödeme talebi yanıtı geçersiz.")
        if data.get("queued") is False or not data.get("ref"):
            reason = str(data.get("reason") or "rejected")
            raise CanteenError(f"Ödeme talebi açılamadı ({reason}).", status=409, reason=reason,
                               payload=data)
        for key in ("createdAt", "updatedAt", "paidAt"):
            if key in data:
                data[key] = _iso(data[key])
        # Yankı belleği: kantinin bu talep için iteceği `payment_request.changed`
        # (PENDING) KM'de ikinci bir olay üretmesin (yazan zaten olay yazdı).
        self.remember_write(student_id, "payment_request", [str(data["ref"])])
        return {
            **data,
            "queued": True,
            "ref": str(data["ref"]),
            "amount": int(data.get("amount") or 0),
            "status": str(data.get("status") or "PENDING"),
            "linkUrl": data.get("linkUrl"),
            "amountFixed": bool(data.get("amountFixed", amount is not None)),
            "payToken": data.get("payToken"),
        }

    async def resend_payment_request(self, ref: str) -> dict[str, Any]:
        """Bekleyen talebi güncel borca göre yeniden gönderir. Aynı `ref` korunur.

        `{"queued": false, "reason": "not_found" | "not_pending" | "no_debt" | "no_phone"}`
        """
        return await self._request("POST", f"/api/payment-requests/{ref}/resend")

    async def cancel_payment_request(self, ref: str) -> dict[str, Any]:
        """Bekleyen talebi iptal eder (`EXPIRED`). Satır SİLİNMEZ, link ölür."""
        return await self._request("POST", f"/api/payment-requests/{ref}/cancel")

    # --------------------------------------------------------------------- SMS

    async def send_sms(self, *, student_id: str | None = None, phone: str | None = None,
                       include_debt: bool = False, include_daily: bool = False,
                       message: str | None = None) -> dict[str, Any]:
        """Elle SMS — EŞ ZAMANLI (kuyruğa girmez, Netgsm sonucu anında döner).

        ALICI DAİMA VELİDİR: `student_id` verildiğinde kantin serbest telefonu yok
        sayar ve `students.parent_phone` kullanır. Öğrenci telefonu diye bir alan
        kantinde yoktur (26 Temmuz göçüyle silindi).

        `include_debt`/`include_daily` cümlelerini SUNUCU ekler; metnin başına gelir.
        BAŞARISIZLIKTA DA HTTP 200: `{"sent": false, "reason": "no_phone"|"empty"|"netgsm"}`.
        Başarılıysa gönderilen tam metin `text` alanında döner.
        """
        body: dict[str, Any] = {
            # Kantin bu ikisini `boolean` kuralıyla doğruluyor; None göndermek 422 verir.
            "includeDebt": bool(include_debt),
            "includeDaily": bool(include_daily),
        }
        if student_id:
            body["studentId"] = student_id
        if phone:
            body["phone"] = phone
        if message:
            body["message"] = message
        return await self._request("POST", "/api/sms/send", json=body)

    async def notifications(self, *, state: str | None = None,
                            limit: int = 100) -> dict[str, Any]:
        """SMS kuyruğu — durum, deneme sayısı, sonraki deneme. Numaralar maskeli."""
        params: dict[str, Any] = {"limit": int(limit)}
        if state:
            params["state"] = state
        return await self._request("GET", "/api/notifications", params=params)

    # ------------------------------------------------------------------ ayarlar

    async def settings(self) -> dict[str, Any]:
        """Netgsm kimlik durumu + SMS şablonları. Parola ASLA dönmez, yalnız 'kurulu mu'."""
        payload = await self._request("GET", "/api/settings")
        return dict((payload or {}).get("data") or {})

    async def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Ayarları günceller — YALNIZCA verilen alanlar.

        Şablon değeri `None` YAZILAMAZ: kantin `null` şablonu "Bilgilendirme: {name}."
        varsayılanına düşürür ve ödeme linki SMS'ten sessizce kaybolur.
        """
        allowed = {"netgsmUsercode", "netgsmPassword", "netgsmHeader",
                   "smsTemplates", "smsPaymentConfirmedEnabled"}
        unknown = set(changes) - allowed
        if unknown:
            raise CanteenError(f"Kantinde olmayan ayar alanı: {', '.join(sorted(unknown))}")

        templates = changes.get("smsTemplates")
        if isinstance(templates, dict):
            empty = [key for key, value in templates.items() if not str(value or "").strip()]
            if empty:
                raise CanteenError(
                    "Şablon boş bırakılamaz — kantin boş şablonu varsayılana düşürür ve "
                    f"ödeme linki SMS'ten kaybolur: {', '.join(sorted(empty))}"
                )

        payload = await self._request("PUT", "/api/settings", json=changes)
        return dict((payload or {}).get("data") or {})

    # ------------------------------------------------------------------ yedekler

    async def backups(self) -> dict[str, Any]:
        """Sunucudaki yedek dosyaları — ad, boyut, zaman, sha256."""
        return await self._request("GET", "/api/backups")

    async def create_backup(self) -> dict[str, Any]:
        """Elle yedek aldırır (`db:backup`). Zamanlanmış yedek rotasyonuna karışmaz."""
        return await self._request("POST", "/api/backups")

    async def download_backup(self, name: str) -> tuple[bytes, str]:
        """Yedek dosyasını indirir. Dönüş: (içerik, sunucunun bildirdiği sha256).

        JSON değil ham dosya döndüğü için `_request` kullanılmaz.
        """
        token = await self._ensure_token()
        response = await self._client().get(
            f"{self._base}/api/backups/{name}/download",
            headers={"Authorization": f"Bearer {token}"},
            timeout=self._timeouts(max(self._timeout, 120.0)),
        )
        if response.status_code >= 400:
            raise CanteenError(
                f"Yedek indirilemedi ({response.status_code}): {response.text[:200]}",
                status=response.status_code,
            )
        return response.content, str(response.headers.get("X-Backup-Sha256") or "")

    async def qr_key(self) -> bytes:
        """Kart QR'ının AES-256 anahtarı (ham 32 bayt).

        Anahtar kasada üretilir ve cihazlara verilir; burada kasadan gelir,
        arayüze ASLA gönderilmez.
        """
        import base64

        cached = await self._secrets.get("canteen.qr_key")
        if not cached:
            payload = await self._request("GET", "/api/device/qr-key")
            cached = str(payload.get("qrKey") or "")
            if not cached:
                raise CanteenError("QR anahtarı alınamadı.")
            await self._secrets.set("canteen.qr_key", cached)

        raw = base64.b64decode(cached)
        if len(raw) != 32:
            raise CanteenError("QR anahtarı 32 bayt değil.")
        return raw


# ------------------------------------------------------------------ yardım


def _now_iso() -> str:
    return datetime.now(UTC).astimezone(_ISTANBUL).isoformat(timespec="seconds")


def _mark_unsupported(failure: CanteenError) -> None:
    """Yolun kendisi yoksa (eski kantin, Laravel 404 gövdesi `reason` taşımaz)
    hata `not_supported` olur; öğrenci yoksa `student_not_found` kalır."""
    if failure.status in (404, 405) and not failure.reason:
        failure.reason = "not_supported"


def _iso(value: Any) -> Any:
    """Epoch-ms ya da ISO → ISO (Europe/Istanbul). Boş değer olduğu gibi kalır."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
        return moment.astimezone(_ISTANBUL).isoformat(timespec="seconds")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        return text
    return parsed.astimezone(_ISTANBUL).isoformat(timespec="seconds")


def _feed_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 200
    return max(1, min(FEED_LIMIT_MAX, value))


def _feed_params(updated_since: str | None, limit: int) -> dict[str, Any]:
    return {"updatedSince": str(updated_since or FEED_START), "limit": _feed_limit(limit)}


def _feed_page(payload: Any, items: list[dict[str, Any]],
               updated_since: str | None) -> dict[str, Any]:
    """Akış yanıtının imleci. Kantin `nextSince` vermezse son satırdan türetilir."""
    body = payload if isinstance(payload, dict) else {}
    next_since = body.get("nextSince")
    if not next_since:
        stamps = [item.get("updatedAt") for item in items if item.get("updatedAt")]
        next_since = stamps[-1] if stamps else (updated_since or FEED_START)
    return {"data": items, "nextSince": _iso(next_since),
            "hasMore": bool(body.get("hasMore", False))}


def _payment_request_row(row: dict[str, Any]) -> dict[str, Any]:
    """Talep satırı: zamanlar ISO, `studentName` (yalnız eski liste) düşer."""
    item = {key: value for key, value in row.items() if key != "studentName"}
    for key in ("createdAt", "updatedAt", "paidAt"):
        if key in item:
            item[key] = _iso(item[key])
    item["amountFixed"] = bool(item.get("amountFixed", False))
    return item


def _student_with_controls(data: dict[str, Any]) -> dict[str, Any]:
    """Tekil öğrenci yanıtını sözleşme biçimine getirir; `accessCode` düşer."""
    student = {
        key: value for key, value in data.items()
        if key not in ("accessCode", "accessCodeIssuedAt")
    }
    restricted = data.get("restrictedProductIds")
    spent = data.get("spentToday")
    student.update({
        "balance": int(data.get("balance") or 0),
        "spendingLimit": data.get("spendingLimit"),
        "isBlocked": bool(data.get("isBlocked")),
        "dailyLimit": data.get("dailyLimit"),
        "guardianBlocked": bool(data.get("guardianBlocked")),
        "restrictedProductIds": sorted({int(item) for item in restricted})
        if isinstance(restricted, list) else [],
        "spentToday": int(spent) if isinstance(spent, (int, float)) and not isinstance(spent, bool)
        else None,
        "controlsUpdatedAt": _iso(data.get("controlsUpdatedAt")),
    })
    return student


def _limit_value(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= LIMIT_MAX:
        raise CanteenError(f"{name} 0 ile {LIMIT_MAX} kuruş arasında olmalı.",
                           status=422, reason="validation_error")
    return int(value)


def _validated_controls(patch: dict[str, Any]) -> dict[str, Any]:
    """Kontrol yamasını doğrular; yalnız tanınan alanlar geçer."""
    if not isinstance(patch, dict) or not patch:
        raise CanteenError("Değiştirilecek kontrol yok.", status=422, reason="validation_error")
    unknown = set(patch) - CONTROL_FIELDS
    if unknown:
        raise CanteenError(f"Kantin kontrolünde olmayan alan: {', '.join(sorted(unknown))}",
                           status=422, reason="validation_error")
    body: dict[str, Any] = {}
    for name in ("spendingLimit", "dailyLimit"):
        if name in patch:
            body[name] = _limit_value(name, patch[name])
    for name in ("isBlocked", "guardianBlocked"):
        if name in patch:
            if not isinstance(patch[name], bool):
                raise CanteenError(f"{name} doğru/yanlış olmalı.", status=422,
                                   reason="validation_error")
            body[name] = patch[name]
    if "restrictedProductIds" in patch:
        items = patch["restrictedProductIds"]
        if items is None:
            items = []
        if not isinstance(items, list) or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in items
        ):
            raise CanteenError("restrictedProductIds pozitif tam sayı listesi olmalı.",
                               status=422, reason="validation_error")
        unique = sorted(set(items))
        if len(unique) > RESTRICTED_MAX:
            raise CanteenError(f"En çok {RESTRICTED_MAX} ürün kısıtlanabilir.", status=422,
                               reason="validation_error")
        body["restrictedProductIds"] = unique
    return body
