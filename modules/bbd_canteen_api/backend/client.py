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

PARA HER YERDE KURUŞ (tam sayı), ZAMAN EPOCH-MİLİSANİYE. Dönüşüm çağıranın işidir.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class CanteenError(RuntimeError):
    """Kantin API'si beklenmedik yanıt verdi."""

    def __init__(self, message: str, *, status: int | None = None,
                 reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        # Kantinin iş kuralı kodu: blocked / limit / stock / student_not_found ...
        self.reason = reason


#: Kantinin tek batch isteğinde kabul ettiği en çok kalem (`TransactionController`).
BATCH_LIMIT = 200


class CanteenApi:
    """`canteen.api` yeteneğinin uygulaması."""

    def __init__(self, *, base_url: str, device_name: str, secrets: Any, log: Any,
                 timeout: float = 20.0) -> None:
        self._base = base_url.rstrip("/")
        self._device_name = device_name
        self._secrets = secrets
        self._log = log
        self._timeout = timeout
        self._token: str | None = None

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

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
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
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        # Kantin SQLite üzerinde çalışıyor: eşzamanlı okumalarda kilit yüzünden
        # tek seferlik 5xx görülebiliyor. OKUMA istekleri güvenle yinelenir.
        # YAZMA yinelenmez — idempotency `localId` ile sağlanır, körlemesine
        # tekrar göndermek yanlış olur.
        attempts = 3 if method.upper() == "GET" else 1
        response = None
        for attempt in range(attempts):
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    response = await client.request(
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
            try:
                body = response.json()
                if isinstance(body, dict):
                    reason = body.get("reason")
                    message = str(body.get("message") or message)
            except ValueError:
                pass
            raise CanteenError(
                f"{method} {path} → {response.status_code}: {message}",
                status=response.status_code,
                reason=reason,
            )

        return response.json() if response.content else None

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
        return payload.get("data", payload) if isinstance(payload, dict) else payload

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
                           limit: int | None = None) -> list[dict[str, Any]]:
        """İşlem geçmişi, kalem kırılımlı. `to_ms` HARİÇTİR (yarı açık aralık).

        Aralık verilmezse son 100 işlem; verilirse en çok 5000 satır döner.
        Her satır: serverId, studentId (opaque), studentName, total, createdAt,
        reversedAt, reversedReason, items[].
        """
        params: dict[str, Any] = {}
        if from_ms is not None:
            params["from"] = int(from_ms)
        if to_ms is not None:
            params["to"] = int(to_ms)
        if limit is not None:
            params["limit"] = int(limit)
        payload = await self._request("GET", "/api/transactions", params=params)
        data = payload.get("data") if isinstance(payload, dict) else payload
        return list(data or [])

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

    # ------------------------------------------------------------- ödeme talebi

    async def payment_requests(self) -> list[dict[str, Any]]:
        """Son 100 ödeme talebi. Kantin ucu süzgeç/sayfalama kabul etmez; süzme ekranda."""
        payload = await self._request("GET", "/api/payment-requests")
        data = payload.get("data") if isinstance(payload, dict) else payload
        return list(data or [])

    async def create_payment_request(self, student_id: str) -> dict[str, Any]:
        """Ödeme talebi açar ve veliye kısa linkli SMS'i kuyruğa alır.

        TUTAR GÖNDERİLEMEZ — kantin her zaman öğrencinin o anki borcunu kullanır.
        Açık `PENDING` talep varsa YENİSİ AÇILMAZ: mevcut talep güncel borca göre
        yeniden fiyatlanır ve aynı `ref` döner.

        BAŞARISIZLIKTA DA HTTP 200 döner — `queued` alanına bakılır:
        `{"queued": false, "reason": "student_not_found" | "no_debt" | "no_phone"}`.
        """
        return await self._request("POST", "/api/payment-requests", json={"studentId": student_id})

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
        async with httpx.AsyncClient(timeout=max(self._timeout, 120.0)) as client:
            response = await client.get(
                f"{self._base}/api/backups/{name}/download",
                headers={"Authorization": f"Bearer {token}"},
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
