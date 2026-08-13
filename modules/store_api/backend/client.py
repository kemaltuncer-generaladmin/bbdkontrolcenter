"""BBD Store yönetici API'si — mağazaya açılan TEK kapı (K4).

CANLI SİSTEME BAĞLANIR: https://bbdstore.com.tr · Bagisto 2.4.8 +
`bagisto/bagisto-api` v2.3.1. Yönetici REST yüzeyi `/api/admin/*` altında
286 uç sunar; BBD'ye özel uçlar `/api/admin/bbd/*` altındadır ve şu an
YAZILMAKTADIR — henüz yayında değil (bkz. "BBD uçları" bölümü).

KİMLİK
    Authorization: Bearer <id>|<düz metin>   (admin_personal_access_tokens)
    Storefront anahtarı GEREKMEZ. Belirteç kasadan gelir:
    `secrets` yeteneği → `store.admin_token`. Depoda, log'da, hata metninde
    ve ekrana dönen yanıtta ASLA görünmez.

DÖNÜŞ BİÇİMİ
    Koleksiyonlar `{data: [...], meta: {...}}` zarfıyla gelir; bu sınıfın
    liste metotları `{"items": [...], "meta": {...}}` döndürür. Tekil kayıt
    düz sözlüktür. Para alanları Bagisto'da ONDALIK gelir (kuruş değil);
    kuruşa çevirmek çağıranın işidir — burada sessiz dönüşüm yapılmaz.

BEŞ POLİTİKA — hepsi geçitte, ekranların iyi niyetine bırakılmadan uygulanır

1. ACİL FREN (`read_only`, VARSAYILAN AÇIK). Açıkken GET dışı her istek
   geçitte reddedilir; uzağa hiç gitmez. Canlı mağazada çalışıldığı için
   varsayılan güvenli taraftadır: yazmayı açmak bilinçli bir karardır.

2. KURU PROVA (`dry_run`, VARSAYILAN AÇIK). Yazma metotlarının hepsi
   `dry_run` alır.
   TUZAK — BAGISTO ÇEKİRDEĞİ `dryRun` BİLMEZ. Gövdeye `dryRun: true` koyup
   `/api/admin/catalog/products` ucuna göndermek provayı değil GERÇEK
   yazmayı yapardı: Laravel bilmediği alanı yok sayar. Bu yüzden çekirdek
   uçlarında kuru prova İSTEĞİ HİÇ GÖNDERMEZ; ne gönderileceğini anlatan
   sentetik bir yanıt döner (`{"sent": false, "dryRun": true, ...}`).
   `/api/admin/bbd/*` uçları `dryRun`u anlayacak şekilde yazılıyor; orada
   bayrak gövdeyle birlikte gider ve istek gerçekten gönderilir.

3. GEREKÇE TAŞIMA. Yazma metotları `reason` ve `actor` alır. Değerler hem
   `X-Bbd-Reason` / `X-Bbd-Actor` / `X-Bbd-Request-Id` başlıklarıyla gider
   hem de istek çıkmadan `mod_store_api_audit` tablosuna yazılır — ağ
   koparsa "ne yapmaya çalıştık" kaydı yerelde kalır.

4. YİNELEME. GET üç kez denenir (0.4 · (n+1) sn bekleme). YAZMA YİNELENMEZ:
   zaman aşımına uğrayan yazma uzakta uygulanmış olabilir; körlemesine
   tekrarlamak ikinci fatura, ikinci kargo, ikinci iade demektir.
   İdempotency `X-Bbd-Request-Id` ile sağlanır. 429 istisnadır: hız sınırı
   isteği denetleyiciye HİÇ ulaştırmadan reddeder, yan etkisi yoktur —
   `Retry-After` kadar beklenip bir kez yinelenir.

5. HIZ KOVASI. Sunucu sınırı belirteç başına dakikada 60
   (`RateLimiter::for('admin-api')`). Kova 55'te tutar: kalan pay, aynı
   belirteci kullanan başka bir aracın (cron, mobil) sıkışmaması içindir.

DOSYA YÜKLEME
    Görsel uçları JSON değil **multipart/form-data** ister. Panel dosyayı
    base64 olarak yollar (Tauri kabuğunda fs eklentisi yok); geçit çözer,
    türü ve boyutu doğrular, `httpx`'in `files=` parçasını kurar. BEŞ POLİTİKA
    yüklemede de aynen işler: acil fren, gerekçe, kuru prova, hız kovası,
    denetim izi. Boyut sınırı `max_upload_mb` (varsayılan 24) ile uç sınırının
    küçüğüdür ve aşılırsa istek HİÇ GÖNDERİLMEZ (bkz. `upload.py`).

SAYFALAMA
    Sunucu `per_page` değerini 50'ye kırpar (`MAX_PER_PAGE`). Tam katalog
    taraması (1.419 ürün ≈ 29 sayfa) SIRAYLA yapılır — paralel istek kantin
    deneyiminde kilit yüzünden 5xx üretmişti. `all_pages=True` verildiğinde
    `paging.collect_all` devreye girer.

BBD UÇLARI
    `/api/admin/bbd/*` paralel olarak yazılıyor. Uç henüz yoksa 404 döner ve
    bu sınıf onu `bbd_endpoint_missing` koduyla, "uç henüz yayında değil"
    diyen anlaşılır bir hataya çevirir — ekran çöker değil, durumu anlatır
    (K7).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from .audit import AuditTrail
from .cache import ReferenceCache, SnapshotCache
from .errors import StoreApiError, mask_mapping, mask_text
from .paging import clamp_page_size, collect_all, envelope, page_params
from .upload import (
    DEFAULT_MAX_UPLOAD_MB,
    IMAGE_EXTS,
    IMAGE_MIMES,
    PRODUCT_IMAGE_MAX_BYTES,
    describe,
    max_upload_bytes,
    multipart_files,
    prepare_upload,
)

DEFAULT_BASE_URL = "https://bbdstore.com.tr"
ADMIN = "/api/admin"
BBD = "/api/admin/bbd"

#: Kasadaki belirteç anahtarı. Adlandırma `server.<uygulama>.<alan>` değil
#: `store.admin_token`: bu belirteç sunucuya değil MAĞAZA UYGULAMASINA aittir.
TOKEN_KEY = "store.admin_token"

#: Yazma gerekçesinin en az uzunluğu. "ok", "düzeltme" gibi metinler denetim
#: izini işe yaramaz kılıyor; ADR 0012 ekranlarda da 10 karakter istiyor.
MIN_REASON = 10

GET_ATTEMPTS = 3
RETRY_AFTER_CAP = 30.0


class _RateBucket:
    """Kayan pencereli hız kovası: son 60 saniyede en çok `limit` istek.

    Kilit uykuyu da kapsar; bekleyenler sıraya girer. Kasıtlıdır: geçidin
    tamamı sıralı davranmalı, aynı anda uyanan on istek sunucuya salkım
    hâlinde gitmemeli.
    """

    def __init__(self, limit: int, sleeper: Callable[[float], Any] = asyncio.sleep) -> None:
        self._limit = max(1, int(limit))
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._sleep = sleeper

    async def take(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] >= 60:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                await self._sleep(max(0.05, 60 - (now - self._stamps[0])))


def _first_object(payload: Any) -> dict[str, Any]:
    """Tekil kaydı liste içinde döndüren uçlar için zarf açıcı.

    `/configuration` sayfalama kapalı olduğu için `{data, meta}` yerine düz
    `[{...}]` veriyor. `_item` bunu sözlük sanmadığı için BOŞ döndürüyordu;
    ekran "ayar bulunamadı" der, oysa ayar oradaydı.
    """
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return dict(data[0]) if data and isinstance(data[0], dict) else {}
        return dict(data) if isinstance(data, dict) else dict(payload)
    if isinstance(payload, list):
        return dict(payload[0]) if payload and isinstance(payload[0], dict) else {}
    return {}


class StoreApi:
    """`store.api` yeteneğinin uygulaması.

    Liste metotları:  `{"items": [...], "meta": {...}}`
    Tekil metotlar:   düz sözlük
    Yazma metotları:  sunucunun yanıtı; kuru provada
                      `{"ok": True, "dryRun": True, "sent": False, ...}`
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        secrets: Any,
        log: Any,
        store: Any = None,
        timeout: float = 30.0,
        read_only: bool = True,
        dry_run_default: bool = True,
        require_reason: bool = True,
        page_size: int = 50,
        requests_per_minute: int = 55,
        reference_ttl: int = 900,
        snapshot_ttl: int = 1800,
        max_items: int = 5000,
        max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB,
        transport: Any = None,
    ) -> None:
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._secrets = secrets
        self._log = log
        self._timeout = float(timeout)
        self._read_only = bool(read_only)
        self._dry_run_default = bool(dry_run_default)
        self._require_reason = bool(require_reason)
        self._page_size = clamp_page_size(page_size)
        self._max_items = max(1, int(max_items))
        self._max_upload = max_upload_bytes(max_upload_mb)
        self._transport = transport
        self._token: str | None = None

        self._sleep: Callable[[float], Any] = asyncio.sleep
        self._bucket = _RateBucket(requests_per_minute, sleeper=lambda s: self._sleep(s))
        self._audit = AuditTrail(store, log)
        self._reference = ReferenceCache(reference_ttl)
        self._snapshot = SnapshotCache(store, log, snapshot_ttl)

    # ================================================================ durum

    def state(self) -> dict[str, Any]:
        """Geçidin o anki kuralları. Ekranlar acil freni bu bilgiyle gösterir."""
        return {
            "baseUrl": self._base,
            "readOnly": self._read_only,
            "dryRunDefault": self._dry_run_default,
            "pageSize": self._page_size,
            "requireReason": self._require_reason,
            "maxUploadBytes": self._max_upload,
        }

    async def health(self) -> dict[str, Any]:
        """Mağaza ayakta mı — GET /api/admin/settings/channels?per_page=1.

        `/api/admin/docs` 347 KB'lık OpenAPI şeması döndürdüğü için sağlık
        yoklaması olarak KULLANILMAZ; kanal listesi tek satırla en ucuz
        yetkili çağrıdır (belirtecin geçerliliğini de sınar).
        """
        started = time.monotonic()
        try:
            await self._request("GET", f"{ADMIN}/settings/channels", params={"per_page": 1})
        except StoreApiError as failure:
            return {"ok": False, "error": failure.message, "code": failure.code,
                    "status": failure.status, "elapsedMs": int((time.monotonic() - started) * 1000)}
        return {"ok": True, "error": "", "code": "", "status": 200,
                "elapsedMs": int((time.monotonic() - started) * 1000)}

    async def audit_trail(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Geçidin YEREL yazma izi (`mod_store_api_audit`). Uzak sisteme gitmez."""
        return await self._audit.recent(limit=limit)

    # ============================================================= alt yapı

    async def _bearer(self) -> str:
        if self._token:
            return self._token
        value = await self._secrets.get(TOKEN_KEY)
        if not value:
            raise StoreApiError(
                "Mağaza yönetici belirteci kasada yok. config/local.yaml içine "
                f"secrets.{TOKEN_KEY} yazılmalı (Bagisto → Entegrasyon → Belirteçler).",
                code="config_missing",
            )
        self._token = str(value).strip()
        return self._token

    @staticmethod
    def _header_safe(value: str) -> str:
        """Başlık değerini yüzde kodlar.

        TUZAK: HTTP başlıkları latin-1'dir. "Müşteri iptal istedi" içindeki
        `ş`/`ğ` ham gönderilirse httpx `UnicodeEncodeError` fırlatır ve
        gerekçe yazan personel anlamsız bir hata görür. Gerekçenin ham hâli
        gövdeye ve yerel denetim izine ayrıca yazılır.
        """
        return quote(str(value or "")[:400], safe=" .,:;()/-_")

    @staticmethod
    def _merge(*sources: dict[str, Any] | None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for source in sources:
            for key, value in (source or {}).items():
                if value not in (None, ""):
                    params[key] = value
        return params

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        reason: str = "",
        actor: str = "",
        dry_run: bool | None = None,
        action: str = "",
        raw: bool = False,
    ) -> Any:
        verb = method.upper()
        if verb == "GET":
            return await self._send(verb, path, params=params, raw=raw)

        return await self._write(
            verb, path, params=params, body=body, reason=reason, actor=actor,
            dry_run=dry_run, action=action, raw=raw,
        )

    async def _write(
        self, verb: str, path: str, *, params: dict[str, Any] | None, body: Any,
        reason: str, actor: str, dry_run: bool | None, action: str, raw: bool,
        files: dict[str, Any] | None = None, form: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
    ) -> Any:
        """Yazma zinciri: gerekçe → denetim izi → acil fren → kuru prova → istek.

        `files` verildiğinde gövde JSON değil multipart olur; `form` metin
        alanlarını, `summary` ise denetim izine ve kuru prova yanıtına yazılacak
        ÖZETİ taşır. Ham baytın denetim izine girmesi hem anlamsız hem de
        imkânsızdır (JSON'a çevrilemez) — bu yüzden özet ayrı verilir.
        """
        reason = str(reason or "").strip()
        if self._require_reason and len(reason) < MIN_REASON:
            raise StoreApiError(
                "Gerekçe zorunlu: mağazada yapılan her değişiklik en az "
                f"{MIN_REASON} karakterlik bir gerekçeyle kayda geçer.",
                code="reason_required",
            )

        dry = self._dry_run_default if dry_run is None else bool(dry_run)
        request_id = AuditTrail.new_request_id()
        payload = dict(body) if isinstance(body, dict) else body
        fields = dict(form or {})
        if path.startswith(BBD):
            # Gerekçe gövdeye YALNIZCA BBD uçlarında konur; o uçlar gerekçeyi
            # kendi tablolarına yazacak şekilde yazılıyor. Bagisto çekirdek
            # uçlarının işlemcileri `request()->all()` okuyup doğrudan modele
            # veriyor — tanımadıkları alanı göndermek gereksiz risktir.
            # Çekirdek uçlarda gerekçe başlıkla ve yerel denetim iziyle taşınır.
            if files is not None:
                fields.setdefault("reason", reason)
            elif isinstance(payload, dict):
                payload.setdefault("reason", reason)

        #: Denetim izi ve kuru prova yanıtı için gövde. Multipart'ta ham bayt
        #: yerine dosyanın özeti ve metin alanları yazılır.
        recorded: Any = {**(summary or {}), **fields} if files is not None else payload

        await self._audit.before(
            request_id=request_id, method=verb, path=path, action=action or path,
            reason=reason, actor=actor, dry_run=dry, body=recorded,
        )

        if self._read_only:
            await self._audit.after(request_id, result="blocked")
            raise StoreApiError(
                "Acil fren açık: mağazaya yazma kapalı, istek gönderilmedi. "
                "Ayar: modules.store_api.read_only",
                code="read_only",
            )

        if dry and not path.startswith(BBD):
            # Bagisto çekirdeği `dryRun` bilmez; göndermek gerçek yazma olurdu.
            await self._audit.after(request_id, result="dry_run")
            return {
                "ok": True, "dryRun": True, "sent": False, "requestId": request_id,
                "method": verb, "path": path, "body": mask_mapping(recorded),
                "message": "Kuru prova: istek gönderilmedi. Uygulamak için dry_run=False verin.",
            }

        if dry:
            if files is not None:
                fields["dryRun"] = "1"     # multipart alanı metin taşır
            elif isinstance(payload, dict):
                payload["dryRun"] = True

        try:
            result = await self._send(
                verb, path, params=params, body=payload, raw=raw,
                files=files, form=fields or None,
                headers={
                    "X-Bbd-Request-Id": request_id,
                    "X-Bbd-Reason": self._header_safe(reason),
                    "X-Bbd-Actor": self._header_safe(actor),
                },
            )
        except StoreApiError as failure:
            await self._audit.after(request_id, result=f"error:{failure.code}",
                                    status=failure.status)
            raise
        await self._audit.after(request_id, result="ok", status=200)
        if isinstance(result, dict):
            result.setdefault("requestId", request_id)
            result.setdefault("dryRun", dry)
        return result

    async def _send(
        self, verb: str, path: str, *, params: dict[str, Any] | None = None,
        body: Any = None, raw: bool = False, headers: dict[str, str] | None = None,
        files: dict[str, Any] | None = None, form: dict[str, Any] | None = None,
    ) -> Any:
        token = await self._bearer()
        request_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream" if raw else "application/json",
        }
        request_headers.update(headers or {})

        url = f"{self._base}{path}"
        attempts = GET_ATTEMPTS if verb == "GET" else 1
        rate_retry = 1
        attempt = 0
        response: httpx.Response | None = None

        while True:
            await self._bucket.take()
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport, follow_redirects=False
                ) as client:
                    # `Content-Type` ELLE KURULMAZ: multipart sınırını httpx
                    # üretir. `files` bayt taşıdığı için 429 sonrası tek
                    # yineleme aynı gövdeyi bir daha kurabilir.
                    response = await client.request(
                        verb, url, headers=request_headers, params=params or None,
                        json=body if body is not None and files is None else None,
                        files=files or None, data=form or None,
                    )
            except httpx.TransportError as failure:
                attempt += 1
                if verb == "GET" and attempt < attempts:
                    await self._sleep(0.4 * attempt)
                    continue
                # Yazma yinelenmez: zaman aşımına uğrayan istek uzakta
                # uygulanmış olabilir, tekrarı çift kayıt üretir.
                raise StoreApiError(
                    f"{verb} {path} → mağazaya ulaşılamadı: {failure}",
                    code="transport",
                ) from failure

            if response.status_code == 429 and rate_retry > 0:
                rate_retry -= 1
                await self._sleep(self._retry_after(response))
                continue

            if response.status_code >= 500 and verb == "GET":
                attempt += 1
                if attempt < attempts:
                    self._log.warning("mağaza geçici hata verdi, yineleniyor",
                                      path=path, status=response.status_code)
                    await self._sleep(0.4 * attempt)
                    continue
            break

        if response.status_code >= 400:
            self._fail(verb, path, response)
        if raw:
            return response.content
        return response.json() if response.content else None

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            wait = float(response.headers.get("Retry-After") or 1.0)
        except ValueError:
            wait = 1.0
        return min(max(wait, 0.1), RETRY_AFTER_CAP)

    def _fail(self, verb: str, path: str, response: httpx.Response) -> None:
        status = response.status_code
        message = mask_text(response.text[:300])
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in ("message", "detail", "hydra:description", "error", "title"):
                if data.get(key):
                    message = mask_text(str(data[key]))
                    break

        if status == 401:
            # Belirteç iptal edilmiş olabilir; bir sonraki çağrı kasadan
            # yeniden okusun. Kendiliğinden yeni belirteç ÜRETİLMEZ.
            self._token = None
            raise StoreApiError(
                "Mağaza belirteci geçersiz ya da süresi dolmuş. Bagisto → "
                f"Entegrasyon → Belirteçler'den yenileyip kasadaki {TOKEN_KEY} "
                "değerini güncelleyin.",
                status=status, code="unauthorized",
            )
        if status == 403:
            raise StoreApiError(
                f"Mağaza bu işleme izin vermedi ({verb} {path}). Belirtecin bağlı "
                "olduğu yönetici rolünde bu yetki yok.",
                status=status, code="forbidden",
            )
        if status == 404 and path.startswith(BBD):
            raise StoreApiError(
                f"BBD'ye özel uç henüz yayında değil: {path}. Mağaza tarafındaki "
                "paket yayınlanınca bu ekran kendiliğinden çalışacak.",
                status=status, code="bbd_endpoint_missing",
            )
        if status == 404:
            raise StoreApiError(f"Kayıt bulunamadı ({verb} {path}).",
                                status=status, code="not_found")
        if status == 409:
            # Öznitelik silmede "bu öznitelik ailelerde kullanılıyor" yanıtı
            # buradan gelir; genel HTTP metni yerine nedeni söylemek gerekir.
            raise StoreApiError(f"Mağaza bu işlemi çakışma yüzünden reddetti: {message}",
                                status=status, code="conflict")
        if status == 422:
            raise StoreApiError(f"Mağaza isteği doğrulayamadı: {message}",
                                status=status, code="validation")
        if status == 429:
            raise StoreApiError(
                "Mağaza hız sınırı doldu (dakikada 60 istek). Biraz sonra yeniden deneyin.",
                status=status, code="rate_limited",
            )
        if status >= 500:
            raise StoreApiError(f"Mağaza hata verdi ({status}): {message}",
                                status=status, code="server")
        raise StoreApiError(f"{verb} {path} → {status}: {message}", status=status, code="http")

    # ------------------------------------------------------------ yardımcı

    async def _collection(
        self, path: str, filters: dict[str, Any] | None = None, *,
        page: int = 1, per_page: int | None = None, all_pages: bool = False,
    ) -> dict[str, Any]:
        size = clamp_page_size(per_page or self._page_size)
        if not all_pages:
            payload = await self._request(
                "GET", path, params=self._merge(filters, page_params(page, size))
            )
            items, meta = envelope(payload)
            return {"items": items, "meta": meta, "truncated": False}

        async def fetch(number: int, count: int) -> Any:
            return await self._request(
                "GET", path, params=self._merge(filters, page_params(number, count))
            )

        result = await collect_all(fetch, page_size=size, max_items=self._max_items)
        return {
            "items": result["items"],
            "meta": {"total": result["total"], "perPage": size, "currentPage": 1,
                     "lastPage": result["pages"]},
            "truncated": result["truncated"],
        }

    async def _item(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = await self._request("GET", path, params=self._merge(params))
        if isinstance(payload, dict):
            data = payload.get("data")
            return dict(data) if isinstance(data, dict) else dict(payload)
        return {}

    async def _cached(self, key: str, path: str, filters: dict[str, Any] | None = None,
                      *, all_pages: bool = True) -> dict[str, Any]:
        """Referans listeler için L1 önbellekli okuma (900 sn)."""
        hit = self._reference.get(key)
        if hit is not None:
            return hit
        return self._reference.put(key, await self._collection(path, filters, all_pages=all_pages))

    async def _upload(
        self, method: str, path: str, *, content: Any, filename: str, mime: str = "",
        field: str = "image", data: dict[str, Any] | None = None,
        max_bytes: int | None = None, allowed_mimes: tuple[str, ...] = IMAGE_MIMES,
        allowed_exts: tuple[str, ...] = IMAGE_EXTS, reason: str, actor: str = "",
        dry_run: bool | None = None, action: str = "",
    ) -> dict[str, Any]:
        """multipart/form-data yükleme — beş politikanın tamamı işler.

        Dosya İSTEK KURULMADAN ÖNCE çözülür ve doğrulanır: sınırı aşan ya da
        desteklenmeyen dosya için hız kovasından pay bile harcanmaz.
        `max_bytes` verilmezse ayar sınırı geçerlidir; verilirse ikisinin
        KÜÇÜĞÜ uygulanır — uç sınırı ayardan büyük olamaz.
        """
        limit = self._max_upload if max_bytes is None else min(self._max_upload, int(max_bytes))
        part = prepare_upload(
            content, filename=filename, mime=mime, field=field, max_bytes=limit,
            allowed_mimes=allowed_mimes, allowed_exts=allowed_exts,
        )
        # Metin alanları multipart'ta da metindir: `position=1` int gönderilirse
        # httpx gövdeye "1" yazar ama None/bool sessizce bozulur; burada
        # dönüşüm açıkça yapılır.
        fields = {key: ("1" if value is True else "0" if value is False else str(value))
                  for key, value in (data or {}).items() if value is not None}

        result = await self._write(
            method.upper(), path, params=None, body=None, reason=reason, actor=actor,
            dry_run=dry_run, action=action or path, raw=False,
            files=multipart_files(part), form=fields, summary=describe(part),
        )
        return result if isinstance(result, dict) else {"ok": True, "response": result}

    def forget_reference(self, prefix: str = "") -> None:
        """Referans önbelleğini düşürür. Ayar yazan ekran kaydettikten sonra çağırır."""
        self._reference.drop(prefix)

    # =========================================================== 1 · PANO

    async def dashboard_stats(self, *, kind: str = "over-all", start: str = "", end: str = "",
                              channel: str = "") -> dict[str, Any]:
        """Pano rakamları — GET /api/admin/dashboard/stats.

        `kind`: over-all · today · stock-threshold-products · total-sales ·
        total-visitors · top-selling-products · top-customers.
        Aralık verilmezse sunucu son 30 günü kullanır.
        """
        payload = await self._request("GET", f"{ADMIN}/dashboard/stats", params=self._merge(
            {"type": kind, "start": start, "end": end, "channel": channel}
        ))
        items, _ = envelope(payload)
        if items:
            return dict(items[0])
        return dict(payload) if isinstance(payload, dict) else {}

    async def reporting_stats(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Rapor özeti — GET /api/admin/reporting/stats."""
        return await self._item(f"{ADMIN}/reporting/stats", filters)

    async def reporting(self, area: str, filters: dict[str, Any] | None = None, *,
                        view: bool = False) -> dict[str, Any]:
        """Rapor dalı — GET /api/admin/reporting/{sales|customers|products}[/view].

        `view=True` ayrıntı kırılımını verir (admin ekranındaki "detay" görünümü).
        """
        if area not in ("sales", "customers", "products"):
            raise StoreApiError(f"Bilinmeyen rapor dalı: {area}", code="payload")
        path = f"{ADMIN}/reporting/{area}" + ("/view" if view else "")
        return await self._item(path, filters)

    async def reporting_export(self, area: str,
                               filters: dict[str, Any] | None = None) -> bytes:
        """Rapor CSV'si — GET /api/admin/reporting/{area}/export (ham bayt)."""
        if area not in ("sales", "customers", "products"):
            raise StoreApiError(f"Bilinmeyen rapor dalı: {area}", code="payload")
        return await self._request("GET", f"{ADMIN}/reporting/{area}/export",
                                   params=self._merge(filters), raw=True)

    # ======================================================== 2 · SİPARİŞ

    async def orders(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                     per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """Sipariş listesi — GET /api/admin/orders.

        Süzgeçler doğrudan geçer: order_id · status · grand_total_from/to ·
        channel · customer · email · date_range · date_from/date_to · sort · order.
        """
        return await self._collection(f"{ADMIN}/orders", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def order(self, order_id: int) -> dict[str, Any]:
        """Sipariş detayı (kalem, adres, fatura, gönderi) — GET /api/admin/orders/{id}."""
        return await self._item(f"{ADMIN}/orders/{int(order_id)}")

    async def orders_export(self, filters: dict[str, Any] | None = None) -> bytes:
        """Sipariş listesi CSV — GET /api/admin/orders/export (ham bayt)."""
        return await self._request("GET", f"{ADMIN}/orders/export",
                                   params=self._merge(filters, {"format": "csv"}), raw=True)

    async def order_comments(self, order_id: int, *, page: int = 1,
                             per_page: int | None = None) -> dict[str, Any]:
        """Sipariş notları — GET /api/admin/orders/{orderId}/comments."""
        return await self._collection(f"{ADMIN}/orders/{int(order_id)}/comments",
                                      page=page, per_page=per_page)

    async def add_order_comment(self, order_id: int, *, comment: str, notify: bool = False,
                                reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Sipariş notu ekler — POST /api/admin/orders/{orderId}/comments.

        `notify=True` müşteriye e-posta gönderir; not iç yazışma değilse dikkat.
        """
        return await self._request(
            "POST", f"{ADMIN}/orders/{int(order_id)}/comments",
            body={"comment": comment, "customerNotified": bool(notify)},
            reason=reason, actor=actor, dry_run=dry_run, action="add_order_comment",
        )

    async def cancel_order(self, order_id: int, *, reason: str, actor: str = "",
                           dry_run: bool | None = None) -> dict[str, Any]:
        """Siparişi iptal eder — POST /api/admin/orders/{id}/cancel. GERİ ALINAMAZ."""
        return await self._request("POST", f"{ADMIN}/orders/{int(order_id)}/cancel",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="cancel_order")

    async def create_invoice(self, order_id: int, *, items: dict[str, int] | None = None,
                             reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Fatura keser — POST /api/admin/orders/{orderId}/invoices.

        `items`: {siparişKalemId: adet}. Boş bırakılırsa siparişin tamamı faturalanır.
        """
        return await self._request("POST", f"{ADMIN}/orders/{int(order_id)}/invoices",
                                   body={"invoice": {"items": items or {}}}, reason=reason,
                                   actor=actor, dry_run=dry_run, action="create_invoice")

    async def create_shipment(self, order_id: int, *, payload: dict[str, Any], reason: str,
                              actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Gönderi kaydı açar — POST /api/admin/orders/{orderId}/shipments.

        Bagisto'nun KENDİ gönderi kaydıdır (kaynak/adet/takip no). Gerçek kargo
        etiketi Geliver üzerinden `bbd_purchase_shipment` ile alınır.
        """
        return await self._request("POST", f"{ADMIN}/orders/{int(order_id)}/shipments",
                                   body=payload, reason=reason, actor=actor,
                                   dry_run=dry_run, action="create_shipment")

    async def refund_preview(self, order_id: int, *,
                             items: dict[str, int]) -> dict[str, Any]:
        """İade tutarı önizlemesi — POST /api/admin/orders/{orderId}/refunds/preview.

        SUNUCUYA YAZMAZ; hesabı sunucu yapar. POST olduğu için gerekçe ve kuru
        prova zincirinden geçmez — ama ACİL FREN BURAYA DA UYGULANIR: fren
        açıkken GET dışı hiçbir istek çıkmaz, istisna açmak kuralı delik yapar.
        """
        if self._read_only:
            raise StoreApiError(
                "Acil fren açık: iade önizlemesi de mağazaya istek gönderir, kapalı.",
                code="read_only",
            )
        payload = await self._send("POST", f"{ADMIN}/orders/{int(order_id)}/refunds/preview",
                                   body={"refund": {"items": items}})
        return dict(payload or {})

    async def create_refund(self, order_id: int, *, items: dict[str, int],
                            adjustments: dict[str, Any] | None = None, reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """İade oluşturur — POST /api/admin/orders/{orderId}/refunds. PARA HAREKETİDİR."""
        refund: dict[str, Any] = {"items": items}
        refund.update(adjustments or {})
        return await self._request("POST", f"{ADMIN}/orders/{int(order_id)}/refunds",
                                   body={"refund": refund}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="create_refund")

    async def reorder(self, order_id: int, *, reason: str, actor: str = "",
                      dry_run: bool | None = None) -> dict[str, Any]:
        """Siparişi yeniden oluşturur (sepete taşır) — POST /api/admin/orders/{id}/reorder."""
        return await self._request("POST", f"{ADMIN}/orders/{int(order_id)}/reorder", body={},
                                   reason=reason, actor=actor, dry_run=dry_run, action="reorder")

    async def place_order(self, cart_id: int, *, reason: str, actor: str = "",
                          dry_run: bool | None = None) -> dict[str, Any]:
        """Taslak sepeti siparişe çevirir — POST /api/admin/orders/place/{cartId}."""
        return await self._request("POST", f"{ADMIN}/orders/place/{int(cart_id)}", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="place_order")

    # ======================================================== 3 · FATURA

    async def invoices(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                       per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """Fatura listesi — GET /api/admin/invoices."""
        return await self._collection(f"{ADMIN}/invoices", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def invoice(self, invoice_id: int) -> dict[str, Any]:
        """Fatura detayı (kalemler dahil) — GET /api/admin/invoices/{id}."""
        return await self._item(f"{ADMIN}/invoices/{int(invoice_id)}")

    async def invoice_pdf(self, invoice_id: int) -> bytes:
        """Fatura PDF'i — GET /api/admin/invoices/{id}/print (ham bayt)."""
        return await self._request("GET", f"{ADMIN}/invoices/{int(invoice_id)}/print", raw=True)

    async def invoices_export(self, filters: dict[str, Any] | None = None) -> bytes:
        """Fatura listesi CSV — GET /api/admin/invoices/export."""
        return await self._request("GET", f"{ADMIN}/invoices/export",
                                   params=self._merge(filters), raw=True)

    async def send_invoice_copy(self, invoice_id: int, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Fatura kopyasını müşteriye e-postalar —
        POST /api/admin/invoices/{id}/send-duplicate."""
        return await self._request("POST", f"{ADMIN}/invoices/{int(invoice_id)}/send-duplicate",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="send_invoice_copy")

    async def update_invoice_status(self, invoice_ids: list[int], *, status: str, reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Toplu fatura durumu — POST /api/admin/invoices/mass-update-status."""
        return await self._request("POST", f"{ADMIN}/invoices/mass-update-status",
                                   body={"indices": [int(i) for i in invoice_ids],
                                         "status": status},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_invoice_status")

    # ============================================ 4 · İADE · GÖNDERİ · POS

    async def refunds(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                      per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """İade listesi — GET /api/admin/refunds."""
        return await self._collection(f"{ADMIN}/refunds", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def refund(self, refund_id: int) -> dict[str, Any]:
        """İade detayı — GET /api/admin/refunds/{id}."""
        return await self._item(f"{ADMIN}/refunds/{int(refund_id)}")

    async def refunds_export(self, filters: dict[str, Any] | None = None) -> bytes:
        """İade listesi CSV — GET /api/admin/refunds/export."""
        return await self._request("GET", f"{ADMIN}/refunds/export",
                                   params=self._merge(filters), raw=True)

    async def shipments(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                        per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """Bagisto gönderi listesi — GET /api/admin/shipments.

        Kargo firmasındaki gerçek hareketler `bbd_shipments` altındadır;
        burası siparişin "kargolandı" kaydıdır.
        """
        return await self._collection(f"{ADMIN}/shipments", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def shipment(self, shipment_id: int) -> dict[str, Any]:
        """Gönderi detayı — GET /api/admin/shipments/{id}."""
        return await self._item(f"{ADMIN}/shipments/{int(shipment_id)}")

    async def transactions(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                           per_page: int | None = None,
                           all_pages: bool = False) -> dict[str, Any]:
        """Ödeme işlemleri — GET /api/admin/transactions."""
        return await self._collection(f"{ADMIN}/transactions", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def transaction(self, transaction_id: int) -> dict[str, Any]:
        """İşlem detayı — GET /api/admin/transactions/{id}."""
        return await self._item(f"{ADMIN}/transactions/{int(transaction_id)}")

    async def record_transaction(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Elle ödeme kaydı — POST /api/admin/transactions (havale/EFT tahsilatı)."""
        return await self._request("POST", f"{ADMIN}/transactions", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="record_transaction")

    # ========================================================= 5 · KATALOG

    async def products(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                       per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """Ürün listesi — GET /api/admin/catalog/products.

        Süzgeçler: product_id · sku · name · type · status · attribute_family ·
        channel · locale · price_from/price_to · sort · order.
        1.419 ürünün tamamı için `all_pages=True` — 29 sayfa SIRAYLA çekilir.
        """
        return await self._collection(f"{ADMIN}/catalog/products", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def product(self, product_id: int) -> dict[str, Any]:
        """Ürün detayı (tip duyarlı; varyant, görsel, öznitelik) —
        GET /api/admin/catalog/products/{id}."""
        return await self._item(f"{ADMIN}/catalog/products/{int(product_id)}")

    async def products_export(self, filters: dict[str, Any] | None = None) -> bytes:
        """Ürün CSV'si — GET /api/admin/catalog/products/export."""
        return await self._request("GET", f"{ADMIN}/catalog/products/export",
                                   params=self._merge(filters, {"format": "csv"}), raw=True)

    async def product_lookup(self, filters: dict[str, Any] | None = None, *,
                             per_page: int | None = None) -> dict[str, Any]:
        """Seçici (picker) için hafif ürün listesi — GET /api/admin/products."""
        return await self._collection(f"{ADMIN}/products", filters, per_page=per_page)

    async def create_product(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Ürün açar (1. adım: tip, aile, SKU) — POST /api/admin/catalog/products."""
        return await self._request("POST", f"{ADMIN}/catalog/products", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_product")

    async def update_product(self, product_id: int, *, payload: dict[str, Any], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Ürünü günceller — PUT /api/admin/catalog/products/{id}.

        Bagisto bu uçta GÖNDERİLEN alanları işler; dokunulmayan öznitelik
        korunur. Yine de tam nesne göndermeyin: aynı anda başka ekranın
        yazdığı alanın üstüne geçersiniz.
        """
        return await self._request("PUT", f"{ADMIN}/catalog/products/{int(product_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_product")

    async def update_product_status(self, product_ids: list[int], *, active: bool, reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Toplu aktif/pasif — POST /api/admin/catalog/products/mass-update-status.

        SİLME YERİNE BU KULLANILIR (ADR 0012): pasif ürün siparişlerde ve
        raporlarda durmaya devam eder.
        """
        return await self._request("POST", f"{ADMIN}/catalog/products/mass-update-status",
                                   body={"indices": [int(i) for i in product_ids],
                                         "value": 1 if active else 0},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_product_status")

    async def copy_product(self, product_id: int, *, reason: str, actor: str = "",
                           dry_run: bool | None = None) -> dict[str, Any]:
        """Ürünü kopyalar — POST /api/admin/catalog/products/{sourceId}/copy."""
        return await self._request("POST", f"{ADMIN}/catalog/products/{int(product_id)}/copy",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="copy_product")

    async def product_inventories(self, product_id: int) -> dict[str, Any]:
        """Kaynak bazlı stok — GET /api/admin/catalog/products/{productId}/inventories."""
        return await self._collection(
            f"{ADMIN}/catalog/products/{int(product_id)}/inventories")

    async def update_inventory(self, product_id: int, *, quantities: dict[str, int],
                               reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Stok yazar — PUT /api/admin/catalog/products/{productId}/inventories.

        `quantities`: {envanterKaynakId: adet}. MUTLAK DEĞERDİR, fark değil.
        """
        return await self._request("PUT", f"{ADMIN}/catalog/products/{int(product_id)}"
                                          "/inventories",
                                   body={"inventories": quantities}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="update_inventory")

    async def add_product_image(self, product_id: int, *, payload: dict[str, Any], reason: str,
                                actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """`upload_product_image` ile aynı iş — ESKİ AD, sözlük imzalı.

        BU UÇ JSON KABUL ETMEZ: vendor kaynağında işlem `inputFormats:
        ['multipart' => ['multipart/form-data']]` ile tanımlı ve dosyayı
        `request()->file('image')` diye okuyor. Eski gövde olduğu gibi
        gönderilseydi sunucu "görsel zorunlu" diyen bir 422 döndürürdü —
        ekranda anlamsız bir hata. Bu yüzden sözlük burada açılır ve gerçek
        yükleme yoluna verilir.

        Beklenen anahtarlar: `content` (ya da `image`/`data`) · `filename` ·
        `mime` · `position`.
        """
        content = payload.get("content", payload.get("image", payload.get("data")))
        if content is None:
            raise StoreApiError(
                "Ürün görseli yüklemek için dosya içeriği gerekiyor "
                "(content/image alanı). upload_product_image kullanın.",
                code="payload",
            )
        return await self.upload_product_image(
            product_id, content=content, filename=str(payload.get("filename") or "gorsel"),
            mime=str(payload.get("mime") or ""), position=payload.get("position"),
            reason=reason, actor=actor, dry_run=dry_run,
        )

    async def upload_product_image(self, product_id: int, *, content: Any, filename: str,
                                   mime: str = "", position: int | None = None, reason: str,
                                   actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Ürün görseli yükler — POST /api/admin/catalog/products/{productId}/images.

        DOĞRULANMADI (uç canlıda YAZMA gerektiriyor; canlı mağazaya yazma
        isteği atılmadı). Alan adları vendor kaynağından okundu:
        `AdminCatalogProductImage` → multipart, dosya alanı **`image`**,
        isteğe bağlı metin alanı `position`. `images[]` ya da `file` DEĞİL.

        `content` bayt ya da base64 metin olabilir (panel base64 yollar).
        Sunucu bmp/jpeg/jpg/png/webp kabul eder ve **4 MB** üstünü 422 ile
        reddeder; sınır burada bilindiği için aşan dosya HİÇ GÖNDERİLMEZ.
        `position` verilmezse sunucu en sona ekler.

        Yanıt: `{id, productId, path, position, url, success, message}` (201).
        """
        return await self._upload(
            "POST", f"{ADMIN}/catalog/products/{int(product_id)}/images",
            content=content, filename=filename, mime=mime, field="image",
            data={"position": position}, max_bytes=PRODUCT_IMAGE_MAX_BYTES,
            reason=reason, actor=actor, dry_run=dry_run, action="upload_product_image",
        )

    async def delete_product_image(self, product_id: int, image_id: int, *, reason: str,
                                   actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Görseli siler — DELETE /api/admin/catalog/products/{productId}/images/{id}.

        DOĞRULANMADI (yazma ucu; canlıda denenmedi). Hem satır hem de diskteki
        dosya gider. Geçidin izin verdiği tek DELETE ailesi görsel/video/
        kupondur: bunlar iş kaydı değil ektir ve pasifleştirilemez.
        """
        return await self._request("DELETE", f"{ADMIN}/catalog/products/{int(product_id)}"
                                             f"/images/{int(image_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_product_image")

    async def upload_media(self, *, content: Any, filename: str, mime: str = "",
                           slot: str = "", position: int | None = None, reason: str,
                           actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Ana ekran görseli yükler — POST /api/admin/bbd/home/slides.

        DOĞRULANMADI — UÇ HENÜZ YOK: canlıda 404 döndü (2026-08-13). Geçit bunu
        `bbd_endpoint_missing` koduyla "uç henüz yayında değil" diyen anlaşılır
        bir hataya çevirir; ekran çökmez, durumu anlatır (K7).

        Ürün görselinin 4 MB'lık sunucu sınırı BURADA GEÇERSİZDİR (başka bir
        işlemci); yalnız ayar sınırı (`max_upload_mb`) uygulanır. Uç yayına
        girdiğinde gerçek sınır öğrenilip buraya yazılmalıdır.
        """
        return await self._upload(
            "POST", f"{BBD}/home/slides", content=content, filename=filename, mime=mime,
            field="image", data={"slot": slot, "position": position},
            reason=reason, actor=actor, dry_run=dry_run, action="upload_media",
        )

    async def reorder_product_images(self, product_id: int, *, order: list[int], reason: str,
                                     actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Görsel sırası — PUT /api/admin/catalog/products/{productId}/images/reorder.

        Listenin İLK elemanı kapak görselidir; `position` 1'den başlar.

        GÖVDE BİÇİMİ — düz kimlik listesi DEĞİL, satır satır sözlük:

            {"order": [{"id": 47, "position": 1}, {"id": 48, "position": 2}]}

        Vendor işlemcisi her satırı `is_array($row) && isset($row['id'])` ile
        denetliyor ve konumu `$row['position']` alanından okuyor
        (`AdminCatalogProductImageProcessor`). Düz `[47, 48]` gönderilirse uç
        HER ZAMAN 422 döner — istek biçimsel olarak geçerli göründüğü için bu
        hata ancak gerçek çağrıda ortaya çıkar.

        Çağıran taraf yine sıralı kimlik listesi verir; konum burada üretilir,
        böylece "ilk eleman kapaktır" kuralı tek yerde durur.
        """
        rows = [{"id": int(image_id), "position": index}
                for index, image_id in enumerate(order, start=1)]
        return await self._request("PUT", f"{ADMIN}/catalog/products/{int(product_id)}"
                                          "/images/reorder",
                                   body={"order": rows}, reason=reason,
                                   actor=actor, dry_run=dry_run, action="reorder_product_images")

    async def remove_product_image(self, product_id: int, image_id: int, *, reason: str,
                                   actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """`delete_product_image` ile aynı iş — ESKİ AD, uyumluluk için durur.

        Yeni yazılan ekran `delete_product_image` çağırsın. İki gövde değil tek
        gövde: politika zinciri iki yerde ayrı ayrı bakım göremez.
        """
        return await self.delete_product_image(product_id, image_id, reason=reason,
                                               actor=actor, dry_run=dry_run)

    async def customer_group_prices(self, product_id: int) -> dict[str, Any]:
        """Grup fiyatları — GET /api/admin/catalog/products/{productId}/customer-group-prices."""
        return await self._collection(
            f"{ADMIN}/catalog/products/{int(product_id)}/customer-group-prices")

    async def save_customer_group_price(self, product_id: int, *, payload: dict[str, Any],
                                        price_id: int | None = None, reason: str,
                                        actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        """Grup fiyatı ekler/günceller —
        POST|PUT /api/admin/catalog/products/{productId}/customer-group-prices[/{id}]."""
        base = f"{ADMIN}/catalog/products/{int(product_id)}/customer-group-prices"
        if price_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_group_price")
        return await self._request("PUT", f"{base}/{int(price_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_group_price")

    # -------------------------------------------------------------- kategori

    async def categories(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                         per_page: int | None = None, all_pages: bool = True) -> dict[str, Any]:
        """Kategori listesi — GET /api/admin/catalog/categories (41 kategori)."""
        return await self._collection(f"{ADMIN}/catalog/categories", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def category_tree(self) -> dict[str, Any]:
        """Kategori ağacı — GET /api/admin/catalog/categories/tree. 900 sn önbellekli."""
        return await self._cached("category_tree", f"{ADMIN}/catalog/categories/tree",
                                  all_pages=False)

    async def category(self, category_id: int) -> dict[str, Any]:
        """Kategori detayı — GET /api/admin/catalog/categories/{id}."""
        return await self._item(f"{ADMIN}/catalog/categories/{int(category_id)}")

    async def create_category(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Kategori açar — POST /api/admin/catalog/categories."""
        self._reference.drop("category")
        return await self._request("POST", f"{ADMIN}/catalog/categories", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_category")

    async def update_category(self, category_id: int, *, payload: dict[str, Any], reason: str,
                              actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Kategoriyi günceller — PUT /api/admin/catalog/categories/{id}."""
        self._reference.drop("category")
        return await self._request("PUT", f"{ADMIN}/catalog/categories/{int(category_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_category")

    async def update_category_status(self, category_ids: list[int], *, active: bool, reason: str,
                                     actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Toplu kategori aktif/pasif —
        POST /api/admin/catalog/categories/mass-update-status."""
        self._reference.drop("category")
        return await self._request("POST", f"{ADMIN}/catalog/categories/mass-update-status",
                                   body={"indices": [int(i) for i in category_ids],
                                         "value": 1 if active else 0},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_category_status")

    # ------------------------------------------------------------ öznitelik

    async def attributes(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                         per_page: int | None = None, all_pages: bool = True) -> dict[str, Any]:
        """Öznitelikler — GET /api/admin/catalog/attributes. CANLIDA DOĞRULANDI.

        Süzgeçler: id · code · type · admin_name · is_required · is_unique ·
        is_filterable · is_configurable · is_visible_on_front · is_user_defined ·
        value_per_locale · value_per_channel · locale · sort · order.

        Süzgeçsiz ve sayfasız çağrı 900 sn önbelleklidir (36 öznitelik, ayda
        bir değişir). SÜZGEÇLİ ÇAĞRI ÖNBELLEĞE GİRMEZ: önbellek anahtarı
        süzgeci taşımıyor, taşısaydı da "renk" araması sonrası açılan ekran
        eksik listeyle dolardı.
        """
        if not filters and page == 1 and per_page is None and all_pages:
            return await self._cached("attributes", f"{ADMIN}/catalog/attributes")
        return await self._collection(f"{ADMIN}/catalog/attributes", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def attribute(self, attribute_id: int) -> dict[str, Any]:
        """Öznitelik detayı (çeviri ve seçenekleriyle) —
        GET /api/admin/catalog/attributes/{id}. CANLIDA DOĞRULANDI."""
        return await self._item(f"{ADMIN}/catalog/attributes/{int(attribute_id)}")

    async def create_attribute(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Öznitelik açar — POST /api/admin/catalog/attributes. DOĞRULANMADI (yazma).

        Zorunlu: `code` · `admin_name` · `type`. `type`: text · textarea ·
        price · boolean · select · multiselect · checkbox · date · datetime ·
        image · file. `code` benzersiz olmalı, harf/rakam/alt çizgi içermeli;
        `type` ve `attribute_family_id` REZERVE kelimelerdir, kod olarak
        kullanılamaz. Seçim tiplerinde `options` listesi gövdeyle gidebilir.
        """
        self._reference.drop("attributes")
        return await self._request("POST", f"{ADMIN}/catalog/attributes", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_attribute")

    async def update_attribute(self, attribute_id: int, *, payload: dict[str, Any], reason: str,
                               actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Özniteliği günceller — PUT /api/admin/catalog/attributes/{id}. DOĞRULANMADI.

        `code` ve `type` sistem özniteliklerinde değiştirilemez; sunucu 403
        döner. Yalnız değişen alanları gönderin.
        """
        self._reference.drop("attributes")
        return await self._request("PUT", f"{ADMIN}/catalog/attributes/{int(attribute_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_attribute")

    async def delete_attribute(self, attribute_id: int, *, reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Özniteliği siler — DELETE /api/admin/catalog/attributes/{id}. DOĞRULANMADI.

        Sunucu SİSTEM özniteliğini silmez (403) ve bir ailede kullanılıyorsa
        reddeder (409 → `conflict`). Öznitelik ürün verisinin şemasıdır:
        silmek o özniteliğin bütün ürünlerdeki değerini götürür. Ekran bunu
        ayrı izinle ve açık uyarıyla sormalı.
        """
        self._reference.drop("attributes")
        return await self._request("DELETE", f"{ADMIN}/catalog/attributes/{int(attribute_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_attribute")

    async def attribute_options(self, attribute_id: int) -> dict[str, Any]:
        """Öznitelik seçenekleri. CANLIDA DOĞRULANDI (detay ucu üzerinden).

        AYRI LİSTE UCU YOK: `AdminAttributeOption` yalnız POST/PUT/DELETE
        tanımlıyor. Seçenekler öznitelik detayının `options` alanından okunur;
        buradaki dönüş yine `{items, meta}` biçimindedir ki ekran diğer
        listelerle aynı kodu kullanabilsin.
        """
        detail = await self.attribute(attribute_id)
        options = detail.get("options")
        items = [dict(option) for option in options if isinstance(option, dict)] \
            if isinstance(options, list) else []
        return {"items": items, "meta": {"total": len(items), "currentPage": 1,
                                         "perPage": len(items), "lastPage": 1},
                "truncated": False}

    async def create_attribute_option(self, attribute_id: int, *, payload: dict[str, Any],
                                      reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """Seçenek ekler — POST /api/admin/catalog/attributes/{attributeId}/options.

        DOĞRULANMADI (yazma). Zorunlu: `admin_name`. İsteğe bağlı: `sort_order`,
        `swatch_value`, `translations`.
        """
        self._reference.drop("attributes")
        return await self._request("POST",
                                   f"{ADMIN}/catalog/attributes/{int(attribute_id)}/options",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_attribute_option")

    async def update_attribute_option(self, attribute_id: int, option_id: int, *,
                                      payload: dict[str, Any], reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """Seçeneği günceller —
        PUT /api/admin/catalog/attributes/{attributeId}/options/{optionId}. DOĞRULANMADI."""
        self._reference.drop("attributes")
        return await self._request("PUT", f"{ADMIN}/catalog/attributes/{int(attribute_id)}"
                                          f"/options/{int(option_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_attribute_option")

    async def delete_attribute_option(self, attribute_id: int, option_id: int, *, reason: str,
                                      actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """Seçeneği siler —
        DELETE /api/admin/catalog/attributes/{attributeId}/options/{optionId}.

        DOĞRULANMADI. Seçeneği silmek o değeri taşıyan ÜRÜNLERDEN de düşürür;
        ekran kaç ürünün etkilendiğini söylemeden sormamalı.
        """
        self._reference.drop("attributes")
        return await self._request("DELETE", f"{ADMIN}/catalog/attributes/{int(attribute_id)}"
                                             f"/options/{int(option_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_attribute_option")

    # ---------------------------------------------------------------- aile

    async def families(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                       per_page: int | None = None, all_pages: bool = True) -> dict[str, Any]:
        """Öznitelik aileleri — GET /api/admin/catalog/families. CANLIDA DOĞRULANDI.

        Süzgeçler: id · code · name · sort · order. Mağazada iki aile var
        (`default`, `kitap`). Süzgeçsiz çağrı 900 sn önbelleklidir.
        LİSTEDE `attributeGroups` NULL GELİR; grupları görmek için `family(id)`.
        """
        if not filters and page == 1 and per_page is None and all_pages:
            return await self._cached("families", f"{ADMIN}/catalog/families")
        return await self._collection(f"{ADMIN}/catalog/families", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def attribute_families(self) -> dict[str, Any]:
        """`families()` ile aynı iş — ESKİ AD, uyumluluk için durur.

        `SNAPSHOT_PARTS` bu adı `getattr` ile çağırıyor; adı değiştirmek anlık
        görüntüyü sessizce boşaltırdı.
        """
        return await self.families()

    async def family(self, family_id: int) -> dict[str, Any]:
        """Aile detayı (grupları ve grup içindeki öznitelikleriyle) —
        GET /api/admin/catalog/families/{id}. CANLIDA DOĞRULANDI."""
        return await self._item(f"{ADMIN}/catalog/families/{int(family_id)}")

    async def create_family(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Aile açar — POST /api/admin/catalog/families. DOĞRULANMADI (yazma).

        Zorunlu: `code` · `name`. `attribute_groups` listesi grup başına
        `{code, name, column, position, custom_attributes: [{id}]}` alır.
        """
        self._reference.drop("families")
        return await self._request("POST", f"{ADMIN}/catalog/families", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_family")

    async def update_family(self, family_id: int, *, payload: dict[str, Any], reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Aileyi günceller — PUT /api/admin/catalog/families/{id}. DOĞRULANMADI.

        TUZAK: `attribute_groups` GÖNDERİLİRSE ailenin grup düzeni gönderilen
        listeyle değiştirilir. Yalnız adı değiştirmek istiyorsanız o alanı
        HİÇ göndermeyin; boş liste göndermek grupları siler.
        """
        self._reference.drop("families")
        return await self._request("PUT", f"{ADMIN}/catalog/families/{int(family_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_family")

    async def delete_family(self, family_id: int, *, reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Aileyi siler — DELETE /api/admin/catalog/families/{id}. DOĞRULANMADI.

        Sunucu SON aileyi ve ürün bağlı aileyi silmez (400 döner). Mağazada iki
        aile var ve ikisinde de ürün var; bu çağrının pratikte reddedilmesi
        beklenir — ekran hatayı olduğu gibi göstermeli.
        """
        self._reference.drop("families")
        return await self._request("DELETE", f"{ADMIN}/catalog/families/{int(family_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_family")

    # ======================================================== 6 · MÜŞTERİ

    async def customers(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                        per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """Müşteri listesi — GET /api/admin/customers.

        Süzgeçler: name · email · phone · customer_group_id · status ·
        channel_id · sort · order.
        """
        return await self._collection(f"{ADMIN}/customers", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def customer(self, customer_id: int) -> dict[str, Any]:
        """Müşteri detayı — GET /api/admin/customers/{id}."""
        return await self._item(f"{ADMIN}/customers/{int(customer_id)}")

    async def create_customer(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Müşteri açar — POST /api/admin/customers."""
        return await self._request("POST", f"{ADMIN}/customers", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="create_customer")

    async def update_customer(self, customer_id: int, *, payload: dict[str, Any], reason: str,
                              actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Müşteriyi günceller — PUT /api/admin/customers/{id}."""
        return await self._request("PUT", f"{ADMIN}/customers/{int(customer_id)}", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_customer")

    async def update_customer_status(self, customer_ids: list[int], *, active: bool, reason: str,
                                     actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Toplu aktif/pasif — POST /api/admin/customers/mass-update-status.

        Müşteri SİLİNMEZ, pasifleştirilir: siparişleri ve faturaları öksüz kalmaz.
        """
        return await self._request("POST", f"{ADMIN}/customers/mass-update-status",
                                   body={"indices": [int(i) for i in customer_ids],
                                         "value": 1 if active else 0},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_customer_status")

    async def customer_addresses(self, customer_id: int) -> dict[str, Any]:
        """Adresler — GET /api/admin/customers/{customerId}/addresses."""
        return await self._collection(f"{ADMIN}/customers/{int(customer_id)}/addresses")

    async def save_customer_address(self, customer_id: int, *, payload: dict[str, Any],
                                    address_id: int | None = None, reason: str, actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Adres ekler/günceller —
        POST|PUT /api/admin/customers/{customerId}/addresses[/{id}]."""
        base = f"{ADMIN}/customers/{int(customer_id)}/addresses"
        if address_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_address")
        return await self._request("PUT", f"{base}/{int(address_id)}", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_address")

    async def customer_notes(self, customer_id: int) -> dict[str, Any]:
        """Müşteri notları — GET /api/admin/customers/{customerId}/notes."""
        return await self._collection(f"{ADMIN}/customers/{int(customer_id)}/notes")

    async def add_customer_note(self, customer_id: int, *, note: str, reason: str,
                                actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Not ekler — POST /api/admin/customers/{customerId}/notes."""
        return await self._request("POST", f"{ADMIN}/customers/{int(customer_id)}/notes",
                                   body={"note": note}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="add_customer_note")

    async def customer_orders(self, customer_id: int) -> dict[str, Any]:
        """Müşterinin son sipariş kalemleri —
        GET /api/admin/customers/{customerId}/recent-order-items."""
        return await self._collection(
            f"{ADMIN}/customers/{int(customer_id)}/recent-order-items")

    async def customer_cart_items(self, customer_id: int) -> dict[str, Any]:
        """Terk edilmiş sepet kalemleri — GET /api/admin/customers/{customerId}/cart-items."""
        return await self._collection(f"{ADMIN}/customers/{int(customer_id)}/cart-items")

    async def customer_wishlist(self, customer_id: int) -> dict[str, Any]:
        """İstek listesi — GET /api/admin/customers/{customerId}/wishlist-items."""
        return await self._collection(f"{ADMIN}/customers/{int(customer_id)}/wishlist-items")

    async def customer_groups(self) -> dict[str, Any]:
        """Müşteri grupları — GET /api/admin/customers/groups. 900 sn önbellekli."""
        return await self._cached("customer_groups", f"{ADMIN}/customers/groups")

    async def save_customer_group(self, *, payload: dict[str, Any], group_id: int | None = None,
                                  reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Grup ekler/günceller — POST|PUT /api/admin/customers/groups[/{id}]."""
        self._reference.drop("customer_groups")
        base = f"{ADMIN}/customers/groups"
        if group_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_customer_group")
        return await self._request("PUT", f"{base}/{int(group_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_customer_group")

    # --------------------------------------------------------------- KVKK

    async def gdpr_requests(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                            per_page: int | None = None) -> dict[str, Any]:
        """KVKK talepleri — GET /api/admin/customers/gdpr-requests."""
        return await self._collection(f"{ADMIN}/customers/gdpr-requests", filters, page=page,
                                      per_page=per_page)

    async def gdpr_request(self, request_id: int) -> dict[str, Any]:
        """KVKK talebi detayı — GET /api/admin/customers/gdpr-requests/{id}."""
        return await self._item(f"{ADMIN}/customers/gdpr-requests/{int(request_id)}")

    async def process_gdpr_request(self, request_id: int, *, payload: dict[str, Any],
                                   reason: str, actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """KVKK talebini işler — POST /api/admin/customers/gdpr-requests/{id}/process.

        Anonimleştirme GERİ ALINAMAZ; ekran ayrı izin ve gerekçe ister.
        """
        return await self._request("POST",
                                   f"{ADMIN}/customers/gdpr-requests/{int(request_id)}/process",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="process_gdpr_request")

    async def gdpr_download_data(self, customer_id: int, *, reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Müşteri veri paketi üretir —
        POST /api/admin/customers/{customerId}/gdpr-download-data."""
        return await self._request("POST",
                                   f"{ADMIN}/customers/{int(customer_id)}/gdpr-download-data",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="gdpr_download_data")

    # ========================================================= 7 · YORUM

    async def reviews(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                      per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """Ürün yorumları — GET /api/admin/customers/reviews.

        Süzgeçler: status (pending|approved|disapproved) · rating · product_id ·
        customer_id · created_at_from/to. Yorum ekranı YOKTUR; Müşteriler
        ekranının sekmesidir.
        """
        return await self._collection(f"{ADMIN}/customers/reviews", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def review(self, review_id: int) -> dict[str, Any]:
        """Yorum detayı (ürün + müşteri + görseller) —
        GET /api/admin/customers/reviews/{id}."""
        return await self._item(f"{ADMIN}/customers/reviews/{int(review_id)}")

    async def update_review(self, review_id: int, *, payload: dict[str, Any], reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Yorumu günceller (durum/yanıt) — PUT /api/admin/customers/reviews/{id}."""
        return await self._request("PUT", f"{ADMIN}/customers/reviews/{int(review_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_review")

    async def update_review_status(self, review_ids: list[int], *, status: str, reason: str,
                                   actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Toplu moderasyon — POST /api/admin/customers/reviews/mass-update-status.

        `status`: approved · disapproved · pending. Yorum SİLİNMEZ; reddedilen
        yorum mağazada görünmez ama kayıt durur.
        """
        return await self._request("POST", f"{ADMIN}/customers/reviews/mass-update-status",
                                   body={"indices": [int(i) for i in review_ids],
                                         "value": status},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_review_status")

    # ===================================================== 8 · PROMOSYON

    async def cart_rules(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                         per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """Sepet kuralları — GET /api/admin/marketing/cart-rules."""
        return await self._collection(f"{ADMIN}/marketing/cart-rules", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def cart_rule(self, rule_id: int) -> dict[str, Any]:
        """Sepet kuralı detayı — GET /api/admin/marketing/cart-rules/{id}."""
        return await self._item(f"{ADMIN}/marketing/cart-rules/{int(rule_id)}")

    async def save_cart_rule(self, *, payload: dict[str, Any], rule_id: int | None = None,
                             reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Sepet kuralı ekler/günceller — POST|PUT /api/admin/marketing/cart-rules[/{id}]."""
        base = f"{ADMIN}/marketing/cart-rules"
        if rule_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_cart_rule")
        return await self._request("PUT", f"{base}/{int(rule_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_cart_rule")

    async def copy_cart_rule(self, rule_id: int, *, reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Kuralı kopyalar — POST /api/admin/marketing/cart-rules/{id}/copy."""
        return await self._request("POST", f"{ADMIN}/marketing/cart-rules/{int(rule_id)}/copy",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="copy_cart_rule")

    async def coupons(self, rule_id: int, *, page: int = 1,
                      per_page: int | None = None) -> dict[str, Any]:
        """Kuponlar — GET /api/admin/marketing/cart-rules/{cartRuleId}/coupons."""
        return await self._collection(f"{ADMIN}/marketing/cart-rules/{int(rule_id)}/coupons",
                                      page=page, per_page=per_page)

    async def create_coupon(self, rule_id: int, *, payload: dict[str, Any], reason: str,
                            actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Tek kupon — POST /api/admin/marketing/cart-rules/{cartRuleId}/coupons."""
        return await self._request("POST", f"{ADMIN}/marketing/cart-rules/{int(rule_id)}/coupons",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_coupon")

    async def generate_coupons(self, rule_id: int, *, payload: dict[str, Any], reason: str,
                               actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Toplu kupon üretir —
        POST /api/admin/marketing/cart-rules/{cartRuleId}/coupons/generate."""
        return await self._request("POST",
                                   f"{ADMIN}/marketing/cart-rules/{int(rule_id)}"
                                   "/coupons/generate",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="generate_coupons")

    async def delete_coupons(self, rule_id: int, *, coupon_ids: list[int], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Kupon kodlarını siler —
        POST /api/admin/marketing/cart-rules/{cartRuleId}/coupons/mass-delete.

        Kupon kodu iş kaydı değildir; kullanılmış kuponun izi siparişte kalır.
        """
        return await self._request("POST",
                                   f"{ADMIN}/marketing/cart-rules/{int(rule_id)}"
                                   "/coupons/mass-delete",
                                   body={"indices": [int(i) for i in coupon_ids]}, reason=reason,
                                   actor=actor, dry_run=dry_run, action="delete_coupons")

    async def catalog_rules(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                            per_page: int | None = None) -> dict[str, Any]:
        """Katalog kuralları — GET /api/admin/marketing/catalog-rules."""
        return await self._collection(f"{ADMIN}/marketing/catalog-rules", filters, page=page,
                                      per_page=per_page)

    async def catalog_rule(self, rule_id: int) -> dict[str, Any]:
        """Katalog kuralı detayı — GET /api/admin/marketing/catalog-rules/{id}."""
        return await self._item(f"{ADMIN}/marketing/catalog-rules/{int(rule_id)}")

    async def save_catalog_rule(self, *, payload: dict[str, Any], rule_id: int | None = None,
                                reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Katalog kuralı ekler/günceller —
        POST|PUT /api/admin/marketing/catalog-rules[/{id}]."""
        base = f"{ADMIN}/marketing/catalog-rules"
        if rule_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_catalog_rule")
        return await self._request("PUT", f"{base}/{int(rule_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_catalog_rule")

    # ================================================ 9 · PAZARLAMA · CMS

    async def campaigns(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """E-posta kampanyaları — GET /api/admin/marketing/campaigns."""
        return await self._collection(f"{ADMIN}/marketing/campaigns", filters)

    async def send_campaign(self, campaign_id: int, *, reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Kampanyayı gönderir — POST /api/admin/marketing/campaigns/{id}/send.

        TOPLU E-POSTADIR; geri alınamaz. Ekran ayrı izin ve gerekçe ister.
        """
        return await self._request("POST", f"{ADMIN}/marketing/campaigns/{int(campaign_id)}/send",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="send_campaign")

    async def email_templates(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """E-posta şablonları — GET /api/admin/marketing/templates."""
        return await self._collection(f"{ADMIN}/marketing/templates", filters)

    async def save_email_template(self, *, payload: dict[str, Any],
                                  template_id: int | None = None, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Şablon ekler/günceller — POST|PUT /api/admin/marketing/templates[/{id}]."""
        base = f"{ADMIN}/marketing/templates"
        if template_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_email_template")
        return await self._request("PUT", f"{base}/{int(template_id)}", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_email_template")

    async def marketing_events(self) -> dict[str, Any]:
        """Pazarlama olayları — GET /api/admin/marketing/events."""
        return await self._collection(f"{ADMIN}/marketing/events")

    async def subscribers(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                          per_page: int | None = None) -> dict[str, Any]:
        """Bülten aboneleri — GET /api/admin/marketing/subscribers."""
        return await self._collection(f"{ADMIN}/marketing/subscribers", filters, page=page,
                                      per_page=per_page)

    # ------------------------------------------------------- arama ve SEO

    async def search_terms(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                           per_page: int | None = None) -> dict[str, Any]:
        """Arama terimleri (sonuçsuz aramalar dahil) —
        GET /api/admin/marketing/search-terms. CANLIDA DOĞRULANDI (19 kayıt).

        Süzgeçler: term · channel_id · locale · sort (id|term|uses|results) ·
        order. `results: 0` olan satırlar "aradı, bulamadı" demektir — katalog
        boşluğunu gösteren en değerli veri odur.
        """
        return await self._collection(f"{ADMIN}/marketing/search-terms", filters, page=page,
                                      per_page=per_page)

    async def search_term(self, term_id: int) -> dict[str, Any]:
        """Terim detayı — GET /api/admin/marketing/search-terms/{id}. DOĞRULANMADI."""
        return await self._item(f"{ADMIN}/marketing/search-terms/{int(term_id)}")

    async def update_search_term(self, term_id: int, *, payload: dict[str, Any], reason: str,
                                 actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Terimi günceller — PUT /api/admin/marketing/search-terms/{id}. DOĞRULANMADI.

        Zorunlu: `term`. `redirect_url` verilirse o terimi arayan doğrudan
        oraya gider — sonuçsuz aramayı kurtarmanın yolu budur. EKLEME UCU YOK:
        terimler aramadan doğar, elle açılmaz.
        """
        return await self._request("PUT", f"{ADMIN}/marketing/search-terms/{int(term_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_search_term")

    async def delete_search_term(self, term_id: int, *, reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Terimi siler — DELETE /api/admin/marketing/search-terms/{id}. DOĞRULANMADI.

        Arama istatistiğidir, iş kaydı değil: silinmesi bir siparişi ya da
        müşteriyi öksüz bırakmaz.
        """
        return await self._request("DELETE", f"{ADMIN}/marketing/search-terms/{int(term_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_search_term")

    async def search_synonyms(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                              per_page: int | None = None) -> dict[str, Any]:
        """Arama eş anlamlıları — GET /api/admin/marketing/search-synonyms.

        CANLIDA DOĞRULANDI (şu an 0 kayıt). Süzgeçler: name · terms · sort · order.
        """
        return await self._collection(f"{ADMIN}/marketing/search-synonyms", filters, page=page,
                                      per_page=per_page)

    async def search_synonym(self, synonym_id: int) -> dict[str, Any]:
        """Eş anlamlı grubu — GET /api/admin/marketing/search-synonyms/{id}. DOĞRULANMADI."""
        return await self._item(f"{ADMIN}/marketing/search-synonyms/{int(synonym_id)}")

    async def create_search_synonym(self, *, payload: dict[str, Any], reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Eş anlamlı ekler — POST /api/admin/marketing/search-synonyms. DOĞRULANMADI.

        Zorunlu: `name` · `terms`. `terms` VİRGÜLLE AYRILMIŞ TEK METİNDİR,
        liste değil: `"kalem,tükenmez,pen"`.
        """
        return await self._request("POST", f"{ADMIN}/marketing/search-synonyms", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_search_synonym")

    async def update_search_synonym(self, synonym_id: int, *, payload: dict[str, Any],
                                    reason: str, actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Eş anlamlıyı günceller —
        PUT /api/admin/marketing/search-synonyms/{id}. DOĞRULANMADI."""
        return await self._request("PUT",
                                   f"{ADMIN}/marketing/search-synonyms/{int(synonym_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_search_synonym")

    async def delete_search_synonym(self, synonym_id: int, *, reason: str, actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Eş anlamlıyı siler —
        DELETE /api/admin/marketing/search-synonyms/{id}. DOĞRULANMADI."""
        return await self._request("DELETE",
                                   f"{ADMIN}/marketing/search-synonyms/{int(synonym_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_search_synonym")

    async def url_rewrites(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                           per_page: int | None = None) -> dict[str, Any]:
        """301/302 yönlendirmeleri — GET /api/admin/marketing/url-rewrites.

        CANLIDA DOĞRULANDI (şu an 0 kayıt). Süzgeçler: entity_type
        (product|category|cms_page) · request_path · redirect_type · locale ·
        sort · order.
        """
        return await self._collection(f"{ADMIN}/marketing/url-rewrites", filters, page=page,
                                      per_page=per_page)

    async def url_rewrite(self, rewrite_id: int) -> dict[str, Any]:
        """Yönlendirme detayı — GET /api/admin/marketing/url-rewrites/{id}. DOĞRULANMADI."""
        return await self._item(f"{ADMIN}/marketing/url-rewrites/{int(rewrite_id)}")

    async def create_url_rewrite(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Yönlendirme açar — POST /api/admin/marketing/url-rewrites. DOĞRULANMADI.

        Zorunlu alanların HEPSİ: `entity_type` (product|category|cms_page) ·
        `request_path` (eski yol) · `target_path` (yeni yol) · `redirect_type`
        ("301" ya da "302", METİN) · `locale`.
        """
        return await self._request("POST", f"{ADMIN}/marketing/url-rewrites", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_url_rewrite")

    async def update_url_rewrite(self, rewrite_id: int, *, payload: dict[str, Any], reason: str,
                                 actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Yönlendirmeyi günceller —
        PUT /api/admin/marketing/url-rewrites/{id}. DOĞRULANMADI."""
        return await self._request("PUT", f"{ADMIN}/marketing/url-rewrites/{int(rewrite_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_url_rewrite")

    async def delete_url_rewrite(self, rewrite_id: int, *, reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Yönlendirmeyi siler — DELETE /api/admin/marketing/url-rewrites/{id}.

        DOĞRULANMADI. Yayındaki bir 301'i silmek arama motorundaki eski bağlantıyı
        404'e düşürür; ekran bunu söylemeli.
        """
        return await self._request("DELETE",
                                   f"{ADMIN}/marketing/url-rewrites/{int(rewrite_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_url_rewrite")

    async def save_url_rewrite(self, *, payload: dict[str, Any], rewrite_id: int | None = None,
                               reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """`create_url_rewrite`/`update_url_rewrite` ikilisine yönlendirir —
        ESKİ AD, uyumluluk için durur."""
        if rewrite_id is None:
            return await self.create_url_rewrite(payload=payload, reason=reason, actor=actor,
                                                 dry_run=dry_run)
        return await self.update_url_rewrite(rewrite_id, payload=payload, reason=reason,
                                             actor=actor, dry_run=dry_run)

    async def sitemaps(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                       per_page: int | None = None) -> dict[str, Any]:
        """Site haritaları — GET /api/admin/marketing/sitemaps.

        CANLIDA DOĞRULANDI (şu an 0 kayıt — mağazanın hiç site haritası yok).
        Süzgeçler: file_name · sort · order.
        """
        return await self._collection(f"{ADMIN}/marketing/sitemaps", filters, page=page,
                                      per_page=per_page)

    async def sitemap(self, sitemap_id: int) -> dict[str, Any]:
        """Site haritası detayı — GET /api/admin/marketing/sitemaps/{id}. DOĞRULANMADI."""
        return await self._item(f"{ADMIN}/marketing/sitemaps/{int(sitemap_id)}")

    async def create_sitemap(self, *, payload: dict[str, Any], reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Site haritası tanımlar — POST /api/admin/marketing/sitemaps. DOĞRULANMADI.

        Zorunlu: `file_name` (ör. "sitemap.xml") · `path` (ör. "/"). TANIM
        DOSYAYI ÜRETMEZ; üretim `generate_sitemap` ile olur.
        """
        return await self._request("POST", f"{ADMIN}/marketing/sitemaps", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_sitemap")

    async def update_sitemap(self, sitemap_id: int, *, payload: dict[str, Any], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Tanımı günceller — PUT /api/admin/marketing/sitemaps/{id}. DOĞRULANMADI."""
        return await self._request("PUT", f"{ADMIN}/marketing/sitemaps/{int(sitemap_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_sitemap")

    async def delete_sitemap(self, sitemap_id: int, *, reason: str, actor: str = "",
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Tanımı siler — DELETE /api/admin/marketing/sitemaps/{id}. DOĞRULANMADI."""
        return await self._request("DELETE", f"{ADMIN}/marketing/sitemaps/{int(sitemap_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="delete_sitemap")

    async def generate_sitemap(self, sitemap_id: int, *, reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Site haritasını üretir — POST /api/admin/marketing/sitemaps/{id}/generate.

        DOĞRULANMADI. Her yayındaki kategori, ürün ve sayfayı dolaşıp XML'i
        yeniden yazar: 1.419 ürünle AĞIR İŞTİR, zaman aşımına yaklaşabilir.
        Yanıt `{sitemapId, indexFile, generatedSitemaps, generatedAt}`.
        """
        return await self._request("POST",
                                   f"{ADMIN}/marketing/sitemaps/{int(sitemap_id)}/generate",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="generate_sitemap")

    async def cms_pages(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                        per_page: int | None = None, all_pages: bool = False) -> dict[str, Any]:
        """CMS sayfaları — GET /api/admin/cms/pages (yasal metinler dahil)."""
        return await self._collection(f"{ADMIN}/cms/pages", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def cms_page(self, page_id: int) -> dict[str, Any]:
        """CMS sayfası — GET /api/admin/cms/pages/{id}."""
        return await self._item(f"{ADMIN}/cms/pages/{int(page_id)}")

    async def save_cms_page(self, *, payload: dict[str, Any], page_id: int | None = None,
                            reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        """CMS sayfası ekler/günceller — POST|PUT /api/admin/cms/pages[/{id}]."""
        base = f"{ADMIN}/cms/pages"
        if page_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_cms_page")
        return await self._request("PUT", f"{base}/{int(page_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_cms_page")

    # =========================================== 10 · VERGİ · KANAL · AYAR

    async def tax_categories(self) -> dict[str, Any]:
        """Vergi kategorileri — GET /api/admin/settings/tax-categories. 900 sn önbellekli."""
        return await self._cached("tax_categories", f"{ADMIN}/settings/tax-categories")

    async def tax_rates(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Vergi oranları — GET /api/admin/settings/tax-rates."""
        return await self._collection(f"{ADMIN}/settings/tax-rates", filters, all_pages=True)

    async def tax_rate(self, rate_id: int) -> dict[str, Any]:
        """Vergi oranı detayı — GET /api/admin/settings/tax-rates/{id}."""
        return await self._item(f"{ADMIN}/settings/tax-rates/{int(rate_id)}")

    async def save_tax_rate(self, *, payload: dict[str, Any], rate_id: int | None = None,
                            reason: str, actor: str = "",
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Vergi oranı ekler/günceller — POST|PUT /api/admin/settings/tax-rates[/{id}].

        Oran değişikliği GEÇMİŞ faturaları etkilemez; ekran bunu yazar.
        """
        self._reference.drop("tax")
        base = f"{ADMIN}/settings/tax-rates"
        if rate_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_tax_rate")
        return await self._request("PUT", f"{base}/{int(rate_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_tax_rate")

    async def save_tax_category(self, *, payload: dict[str, Any], category_id: int | None = None,
                                reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Vergi kategorisi ekler/günceller —
        POST|PUT /api/admin/settings/tax-categories[/{id}]."""
        self._reference.drop("tax")
        base = f"{ADMIN}/settings/tax-categories"
        if category_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_tax_category")
        return await self._request("PUT", f"{base}/{int(category_id)}", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_tax_category")

    async def channels(self) -> dict[str, Any]:
        """Satış kanalları — GET /api/admin/settings/channels. 900 sn önbellekli."""
        return await self._cached("channels", f"{ADMIN}/settings/channels")

    async def channel(self, channel_id: int) -> dict[str, Any]:
        """Kanal detayı (tema, dil, para, ana sayfa) —
        GET /api/admin/settings/channels/{id}."""
        return await self._item(f"{ADMIN}/settings/channels/{int(channel_id)}")

    async def update_channel(self, channel_id: int, *, payload: dict[str, Any], reason: str,
                             actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Kanalı günceller — PUT /api/admin/settings/channels/{id}."""
        self._reference.drop("channels")
        return await self._request("PUT", f"{ADMIN}/settings/channels/{int(channel_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="update_channel")

    async def currencies(self) -> dict[str, Any]:
        """Para birimleri — GET /api/admin/settings/currencies. 900 sn önbellekli."""
        return await self._cached("currencies", f"{ADMIN}/settings/currencies")

    async def exchange_rates(self) -> dict[str, Any]:
        """Kurlar — GET /api/admin/settings/exchange-rates."""
        return await self._collection(f"{ADMIN}/settings/exchange-rates")

    async def refresh_exchange_rates(self, *, reason: str, actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Kurları sağlayıcıdan çeker —
        POST /api/admin/settings/exchange-rates/update-rates."""
        self._reference.drop("currencies")
        return await self._request("POST", f"{ADMIN}/settings/exchange-rates/update-rates",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="refresh_exchange_rates")

    async def locales(self) -> dict[str, Any]:
        """Diller — GET /api/admin/settings/locales. 900 sn önbellekli."""
        return await self._cached("locales", f"{ADMIN}/settings/locales")

    async def inventory_sources(self) -> dict[str, Any]:
        """Envanter kaynakları (depo) — GET /api/admin/settings/inventory-sources."""
        return await self._cached("inventory_sources", f"{ADMIN}/settings/inventory-sources")

    async def themes(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Tema özelleştirmeleri (ana sayfa şeritleri) — GET /api/admin/settings/themes."""
        return await self._collection(f"{ADMIN}/settings/themes", filters)

    async def save_theme(self, *, payload: dict[str, Any], theme_id: int | None = None,
                         reason: str, actor: str = "",
                         dry_run: bool | None = None) -> dict[str, Any]:
        """Tema bloğu ekler/günceller — POST|PUT /api/admin/settings/themes[/{id}].

        Ana Ekran Görselleri ekranının slider/banner slotları burada yaşar.
        """
        base = f"{ADMIN}/settings/themes"
        if theme_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="create_theme")
        return await self._request("PUT", f"{base}/{int(theme_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_theme")

    async def admin_users(self) -> dict[str, Any]:
        """Mağaza yöneticileri — GET /api/admin/settings/users."""
        return await self._collection(f"{ADMIN}/settings/users")

    async def admin_roles(self) -> dict[str, Any]:
        """Mağaza rolleri — GET /api/admin/settings/roles."""
        return await self._collection(f"{ADMIN}/settings/roles")

    async def admin_permissions(self) -> dict[str, Any]:
        """Belirtecin yetkileri — GET /api/admin/permissions.

        "Bu işlemi neden yapamıyoruz" sorusunun cevabı buradadır.
        """
        return await self._cached("permissions", f"{ADMIN}/permissions", all_pages=False)

    async def admin_menu(self) -> dict[str, Any]:
        """Mağaza yönetim menüsü — GET /api/admin/menu."""
        return await self._cached("menu", f"{ADMIN}/menu", all_pages=False)

    # -------------------------------------------------- core_config (ayar)

    async def configuration(self, slug: str, *, channel: str = "",
                            locale: str = "") -> dict[str, Any]:
        """Ayar grubu — GET /api/admin/configuration?slug=<yol>. CANLIDA DOĞRULANDI.

        SLUG ZORUNLUDUR. Parametresiz çağrı 422 "Bad Request" +
        `configuration.slug-required` döndürüyor (canlıda denendi); o hatanın
        metni çevrilmemiş bir anahtar olduğu için kullanıcıya hiçbir şey
        anlatmaz — bu yüzden boş slug BURADA, istek çıkmadan reddedilir.

        Slug'ı KEŞFETMEK için `configuration_slugs()` (düz liste, `hasFields`
        alanı hangi düğümün yazılabilir alanı olduğunu söyler) ya da
        `configuration_menu()` (alan tanımlarıyla birlikte ağaç) kullanılır.
        Canlıda görülen örnekler: `sales.order_settings` ·
        `general.general.locale_options` · `general.content.footer` ·
        `general.design.admin_logo`.

        Dönüş: `{slug, channel, locale, values}`. TUZAK: sunucu bu ucu TEK
        ELEMANLI LİSTE olarak veriyor (`[{...}]`); zarf burada açılır.

        `values` anahtarları TAM NOKTALI KODDUR
        (`sales.order_settings.reorder.admin`), değerler METİNDİR ("1"/"0");
        tanımsız ayar `null` gelir.
        """
        text = str(slug or "").strip()
        if not text:
            raise StoreApiError(
                "Ayar grubu (slug) verilmeden yapılandırma okunamaz. "
                "Geçerli değerler için configuration_slugs() çağrılır.",
                code="payload",
            )
        payload = await self._request(
            "GET", f"{ADMIN}/configuration",
            params=self._merge({"slug": text, "channel": channel, "locale": locale}),
        )
        return _first_object(payload)

    async def update_configuration(self, slug: str, *, values: dict[str, Any], reason: str,
                                   actor: str = "", channel: str = "", locale: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Ayar grubunu yazar — POST /api/admin/configuration. DOĞRULANMADI (yazma).

        YALNIZCA gönderilen alanlar yazılır. Bakım modu gibi yıkıcı ayarlar
        ekranda `confirmWithReason` ister.

        TUZAK — SLUG ELLE VERİLİR, KODDAN TÜRETİLMEZ. Alan kodu
        `<slug>.<alan adı>` biçimindedir ama alan adının kendisi de nokta
        içerebiliyor: `sales.order_settings.reorder.admin` içinde slug
        `sales.order_settings`, alan `reorder.admin`; buna karşılık
        `general.general.locale_options.weight_unit` içinde slug ÜÇ parçalı.
        Koddan slug çıkarmaya çalışan her kural bir grupta yanılır — bu yüzden
        bu metot düz bir "değişiklikler" sözlüğü almaz, grubu çağırandan ister.

        `values` anahtarları tam noktalı koddur, değerleri metin olmalıdır.
        """
        text = str(slug or "").strip()
        if not text:
            raise StoreApiError(
                "Ayar grubu (slug) verilmeden yapılandırma yazılamaz.", code="payload")
        self._reference.drop("configuration")
        body: dict[str, Any] = {"slug": text, "values": values}
        if channel:
            body["channel"] = channel
        if locale:
            body["locale"] = locale
        return await self._request("POST", f"{ADMIN}/configuration", body=body, reason=reason,
                                   actor=actor, dry_run=dry_run, action="update_configuration")

    async def configuration_menu(self, *, slug: str = "", include_values: bool = False,
                                 channel: str = "", locale: str = "") -> dict[str, Any]:
        """Ayar ağacı — GET /api/admin/configuration/menu. CANLIDA DOĞRULANDI.

        Ağaç düğümleri alan tanımlarını da taşır: `{name, code, title, type,
        default, channelBased, localeBased, validation, options, depends}`.
        Bir ekranın ayar formunu ELLE yazmak yerine buradan üretmesi
        mümkündür. `include_values=True` etkin değerleri de gömer.

        Süzgeçsiz çağrı 900 sn önbelleklidir; süzgeçli çağrı önbelleğe girmez.
        """
        if not slug and not include_values and not channel and not locale:
            return await self._cached("configuration_menu", f"{ADMIN}/configuration/menu",
                                      all_pages=False)
        return await self._collection(
            f"{ADMIN}/configuration/menu",
            {"slug": slug, "include_values": "1" if include_values else "",
             "channel": channel, "locale": locale},
            all_pages=False,
        )

    async def configuration_slugs(self) -> dict[str, Any]:
        """Geçerli ayar slug'ları — GET /api/admin/configuration/slugs. CANLIDA DOĞRULANDI.

        Tek elemanlı liste döner: `[{id: "configuration-slugs", slugs: [...]}]`;
        her satır `{slug, name, sort, hasFields, hasChildren}`. `hasFields:
        true` olan slug `configuration(slug)` ile okunabilir; `false` olanlar
        yalnız başlıktır. 900 sn önbellekli.
        """
        return await self._cached("configuration_slugs", f"{ADMIN}/configuration/slugs",
                                  all_pages=False)

    # ================================================= 11 · ANLIK GÖRÜNTÜ

    #: Anlık görüntüye giren referans listeler. Sipariş/ürün/müşteri BURAYA
    #: GİRMEZ: taze olması gereken veri önbelleğe alınmaz.
    SNAPSHOT_PARTS = ("channels", "currencies", "locales", "customer_groups",
                      "tax_categories", "attribute_families", "inventory_sources")

    async def snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        """Referans kümesinin tamamı — 30 dk önbellekli, SIRAYLA çekilir.

        Dönüş: `{parts, errors, stale, storedAt, ageSeconds}`. Mağazaya
        ulaşılamazsa son bilinen anlık görüntü `stale: True` ile döner —
        ekran ayakta kalır (K7) ama verinin bayat olduğunu SÖYLER.
        """
        # Kayıt HER ZAMAN okunur (yerel, ucuz): `refresh=True` yalnız tazeliği
        # yok sayar. Okumasaydık tazeleme sırasında mağaza düşerse elimizde
        # gösterilecek hiçbir şey kalmazdı.
        if refresh:
            # Tazeleme L1'i de düşürmeli; yoksa "yenile" düğmesi 900 saniye
            # boyunca aynı bellek kopyasını geri verirdi.
            self._reference.drop()
        cached = await self._snapshot.get("reference")
        if not refresh and cached and not cached["stale"]:
            return {**cached["payload"], "stale": False, "storedAt": cached["storedAt"],
                    "ageSeconds": cached["ageSeconds"]}

        parts: dict[str, Any] = {}
        errors: list[str] = []
        # SIRAYLA: paralel istek kilit yüzünden 5xx üretiyordu (kantin dersi).
        for name in self.SNAPSHOT_PARTS:
            try:
                parts[name] = (await getattr(self, name)())["items"]
            except Exception as failure:  # noqa: BLE001 - uzak sistem
                parts[name] = []
                errors.append(f"{name}: {failure}")

        if errors and cached:
            # Hepsi ya da bir kısmı gelmediyse bayat görüntü hâlâ daha iyidir.
            return {**cached["payload"], "stale": True, "storedAt": cached["storedAt"],
                    "ageSeconds": cached["ageSeconds"], "errors": errors}

        payload = {"parts": parts, "errors": errors}
        await self._snapshot.put("reference", payload)
        return {**payload, "stale": False, "storedAt": "", "ageSeconds": 0}

    # ============================================ 12 · BBD ÖZEL — KARGO

    async def bbd_shipments(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                            per_page: int | None = None,
                            all_pages: bool = False) -> dict[str, Any]:
        """Geliver gönderileri — GET /api/admin/bbd/shipments."""
        return await self._collection(f"{BBD}/shipments", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def bbd_shipment(self, shipment_id: int) -> dict[str, Any]:
        """Gönderi + hareket geçmişi — GET /api/admin/bbd/shipments/{id}."""
        return await self._item(f"{BBD}/shipments/{int(shipment_id)}")

    async def bbd_create_shipment(self, order_id: int, *, payload: dict[str, Any], reason: str,
                                  actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Gönderi taslağı açar — POST /api/admin/bbd/orders/{orderId}/shipments.

        Desi/ağırlık, paket sayısı, ödeme tipi taslağa yazılır; ETİKET SATIN
        ALINMAZ — o `bbd_purchase_shipment` ile ve ayrı izinle olur.
        """
        return await self._request("POST", f"{BBD}/orders/{int(order_id)}/shipments",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_create_shipment")

    async def bbd_shipment_offers(self, shipment_id: int) -> dict[str, Any]:
        """Taşıyıcı teklifleri — GET /api/admin/bbd/shipments/{id}/offers."""
        return await self._collection(f"{BBD}/shipments/{int(shipment_id)}/offers")

    async def bbd_purchase_shipment(self, shipment_id: int, *, offer_id: str, reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Etiket satın alır — POST /api/admin/bbd/shipments/{id}/purchase. PARA HARCAR."""
        return await self._request("POST", f"{BBD}/shipments/{int(shipment_id)}/purchase",
                                   body={"offerId": offer_id}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="bbd_purchase_shipment")

    async def bbd_cancel_shipment(self, shipment_id: int, *, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Gönderiyi iptal eder — POST /api/admin/bbd/shipments/{id}/cancel."""
        return await self._request("POST", f"{BBD}/shipments/{int(shipment_id)}/cancel", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_cancel_shipment")

    async def bbd_return_shipment(self, shipment_id: int, *, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """İade gönderisi açar — POST /api/admin/bbd/shipments/{id}/return."""
        return await self._request("POST", f"{BBD}/shipments/{int(shipment_id)}/return", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_return_shipment")

    async def bbd_sync_shipment(self, shipment_id: int, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Taşıyıcıdan durumu tazeler — POST /api/admin/bbd/shipments/{id}/sync."""
        return await self._request("POST", f"{BBD}/shipments/{int(shipment_id)}/sync", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_sync_shipment")

    async def bbd_shipment_label(self, shipment_id: int) -> bytes:
        """Kargo etiketi PDF — GET /api/admin/bbd/shipments/{id}/label (ham bayt)."""
        return await self._request("GET", f"{BBD}/shipments/{int(shipment_id)}/label", raw=True)

    async def bbd_carriers(self) -> dict[str, Any]:
        """Taşıyıcı tanımları (kimlikler MASKELİ döner) — GET /api/admin/bbd/carriers."""
        return await self._collection(f"{BBD}/shipments/desi-rates")

    async def bbd_test_carrier(self, carrier: str, *, reason: str, actor: str = "",
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Taşıyıcı bağlantısını sınar — POST /api/admin/bbd/carriers/{carrier}/test."""
        return await self._request("POST", f"{BBD}/shipments/desi-rates/{carrier}/test", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_test_carrier")

    async def bbd_shipping_rates(self) -> dict[str, Any]:
        """Desi→ücret matrisi ve ücretsiz kargo eşiği — GET /api/admin/bbd/shipping/rates."""
        return await self._item(f"{BBD}/shipping/rates")

    async def bbd_update_shipping_rates(self, *, payload: dict[str, Any], reason: str,
                                        actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        """Kargo ücretlendirmesini yazar — PUT /api/admin/bbd/shipping/rates."""
        return await self._request("PUT", f"{BBD}/shipping/rates", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run,
                                   action="bbd_update_shipping_rates")

    async def bbd_refresh_price_list(self, *, reason: str, actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Taşıyıcı fiyat listesini tazeler —
        POST /api/admin/bbd/shipping/price-list/refresh."""
        return await self._request("POST", f"{BBD}/shipping/price-list/refresh", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_refresh_price_list")

    # ======================================== 13 · BBD ÖZEL — POS · ÖDEME

    async def bbd_payment_attempts(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                                   per_page: int | None = None) -> dict[str, Any]:
        """Sanal POS işlem denemeleri — GET /api/admin/bbd/payments/attempts.

        Kart verisi sunucuda maskelidir; geçit ayrıca maskeleme yapmaz.
        """
        return await self._collection(f"{BBD}/payments/attempts", filters, page=page,
                                      per_page=per_page)

    async def bbd_payment_attempt(self, attempt_id: int) -> dict[str, Any]:
        """İşlem detayı (3D sonucu, banka yanıtı) —
        GET /api/admin/bbd/payments/attempts/{id}."""
        return await self._item(f"{BBD}/payments/attempts/{int(attempt_id)}")

    async def bbd_payment_links(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ödeme linkleri — GET /api/admin/bbd/payments/links."""
        return await self._collection(f"{BBD}/payment-links", filters)

    async def bbd_create_payment_link(self, *, order_id: int, amount: int, reason: str,
                                      actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """Ödeme linki üretir — POST /api/admin/bbd/payments/links.

        `amount` KURUŞTUR (tam sayı). Mağaza tarafı ondalığa kendisi çevirir.
        """
        return await self._request("POST", f"{BBD}/payment-links",
                                   body={"orderId": int(order_id), "amount": int(amount)},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_create_payment_link")

    async def bbd_cancel_payment_link(self, token: str, *, reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """Linki iptal eder — POST /api/admin/bbd/payments/links/{token}/cancel.

        Kayıt SİLİNMEZ; link ölür (BBD veri silme yasağı).
        """
        return await self._request("POST", f"{BBD}/payment-links/{token}/cancel", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_cancel_payment_link")

    async def bbd_refund_payment(self, attempt_id: int, *, amount: int, reason: str,
                                 actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """POS iadesi — POST /api/admin/bbd/payments/attempts/{id}/refund.

        `amount` kuruş; kısmi iade için sipariş tutarından küçük verilir.
        PARA HAREKETİDİR: ayrı izin + gerekçe + kuru prova zinciri geçerlidir.
        """
        return await self._request("POST", f"{BBD}/payments/attempts/{int(attempt_id)}/refund",
                                   body={"amount": int(amount)}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="bbd_refund_payment")

    async def bbd_pos_terminals(self) -> dict[str, Any]:
        """POS tanımları (anahtarlar MASKELİ) — GET /api/admin/bbd/payments/terminals."""
        return await self._collection(f"{BBD}/payments/terminals")

    async def bbd_update_pos_terminal(self, terminal_id: int, *, payload: dict[str, Any],
                                      reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """POS tanımını günceller — PUT /api/admin/bbd/payments/terminals/{id}.

        Canlı modda kritik ayar değişikliği ekranda `confirmWithReason` ister.
        """
        return await self._request("PUT", f"{BBD}/payments/terminals/{int(terminal_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_update_pos_terminal")

    async def bbd_reconciliation(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Banka mutabakatı — GET /api/admin/bbd/payments/reconciliation."""
        return await self._item(f"{BBD}/payments/reconciliation", filters)

    # ========================================== 14 · BBD ÖZEL — BLD FİŞİ

    async def bbd_bld_jobs(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                           per_page: int | None = None) -> dict[str, Any]:
        """BLD baskı işleri — GET /api/admin/bbd/bld/jobs."""
        return await self._collection(f"{BBD}/bld/jobs", filters, page=page, per_page=per_page)

    async def bbd_retry_bld_job(self, job_id: int, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Baskı işini yineler — POST /api/admin/bbd/bld/jobs/{id}/retry."""
        return await self._request("POST", f"{BBD}/bld/jobs/{int(job_id)}/retry", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_retry_bld_job")

    async def bbd_reprint_order(self, order_id: int, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Sipariş fişini yeniden bastırır —
        POST /api/admin/bbd/bld/orders/{orderId}/reprint."""
        return await self._request("POST", f"{BBD}/bld/orders/{int(order_id)}/reprint", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_reprint_order")

    async def bbd_bld_test(self, *, reason: str, actor: str = "",
                           dry_run: bool | None = None) -> dict[str, Any]:
        """BLD köprüsünü sınar — POST /api/admin/bbd/bld/test."""
        return await self._request("POST", f"{BBD}/bld/test", body={}, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_bld_test")

    # ==================================== 15 · BBD ÖZEL — DENEME KULÜBÜ

    async def bbd_trial_exams(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Denemeler — GET /api/admin/bbd/trial-club/exams."""
        return await self._collection(f"{BBD}/deneme-kulubu/overview", filters)

    async def bbd_trial_members(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                                per_page: int | None = None,
                                all_pages: bool = False) -> dict[str, Any]:
        """Katılımcılar — GET /api/admin/bbd/trial-club/members."""
        return await self._collection(f"{BBD}/deneme-kulubu/registrations", filters, page=page,
                                      per_page=per_page, all_pages=all_pages)

    async def bbd_trial_results(self, exam_id: int, *, page: int = 1,
                                per_page: int | None = None) -> dict[str, Any]:
        """Sonuçlar — GET /api/admin/bbd/trial-club/exams/{id}/results."""
        return await self._collection(f"{BBD}/trial-club/exams/{int(exam_id)}/results",
                                      page=page, per_page=per_page)

    async def bbd_save_trial_exam(self, *, payload: dict[str, Any], exam_id: int | None = None,
                                  reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Deneme ekler/günceller — POST|PUT /api/admin/bbd/trial-club/exams[/{id}]."""
        base = f"{BBD}/deneme-kulubu/overview"
        if exam_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="bbd_create_trial_exam")
        return await self._request("PUT", f"{base}/{int(exam_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_update_trial_exam")

    async def bbd_upload_trial_results(self, exam_id: int, *, rows: list[dict[str, Any]],
                                       reason: str, actor: str = "",
                                       dry_run: bool | None = None) -> dict[str, Any]:
        """Sonuç yükler — POST /api/admin/bbd/trial-club/exams/{id}/results.

        Kuru provada sunucu EŞLEŞTİRME ÖNİZLEMESİ döner (kaç satır eşleşti,
        kaçı eşleşmedi); onaydan sonra `dry_run=False` ile yazılır.
        """
        return await self._request("POST", f"{BBD}/trial-club/exams/{int(exam_id)}/results",
                                   body={"rows": rows}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="bbd_upload_trial_results")

    async def bbd_publish_trial_results(self, exam_id: int, *, reason: str, actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        """Sonuçları yayınlar —
        POST /api/admin/bbd/trial-club/exams/{id}/results/publish."""
        return await self._request("POST",
                                   f"{BBD}/trial-club/exams/{int(exam_id)}/results/publish",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_publish_trial_results")

    # ================================ 16 · BBD ÖZEL — SET · ANA EKRAN

    async def bbd_bundles(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Setler — GET /api/admin/bbd/bundles (bileşen ve kâr hesabıyla)."""
        return await self._collection(f"{BBD}/storefront/sets", filters)

    async def bbd_bundle(self, bundle_id: int) -> dict[str, Any]:
        """Set detayı — GET /api/admin/bbd/bundles/{id}."""
        return await self._item(f"{BBD}/storefront/sets/{int(bundle_id)}")

    async def bbd_save_bundle(self, *, payload: dict[str, Any], bundle_id: int | None = None,
                              reason: str, actor: str = "",
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Set ekler/günceller — POST|PUT /api/admin/bbd/bundles[/{id}]."""
        base = f"{BBD}/storefront/sets"
        if bundle_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="bbd_create_bundle")
        return await self._request("PUT", f"{base}/{int(bundle_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_update_bundle")

    async def bbd_carousel(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ana ekran şeritleri/slider — GET /api/admin/bbd/carousel."""
        return await self._collection(f"{BBD}/storefront/carousels", filters)

    async def bbd_save_carousel_slot(self, *, payload: dict[str, Any], slot_id: int | None = None,
                                     reason: str, actor: str = "",
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Slot ekler/günceller — POST|PUT /api/admin/bbd/carousel[/{id}].

        Görsel base64 olarak `image` alanında gider (Tauri'de dosya seçici yok).
        """
        base = f"{BBD}/storefront/carousels"
        if slot_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="bbd_create_carousel_slot")
        return await self._request("PUT", f"{base}/{int(slot_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_update_carousel_slot")

    async def bbd_reorder_carousel(self, *, order: list[int], reason: str, actor: str = "",
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Slot sırasını yazar — PUT /api/admin/bbd/carousel/reorder."""
        return await self._request("PUT", f"{BBD}/storefront/carousels/reorder",
                                   body={"order": [int(i) for i in order]}, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_reorder_carousel")

    # ==================================== 17 · BBD ÖZEL — YEDEK · SAĞLIK

    async def bbd_backups(self) -> dict[str, Any]:
        """Yedek envanteri — GET /api/admin/bbd/backups (ad, boyut, sha256, kapsam)."""
        return await self._collection(f"{BBD}/backups")

    async def bbd_create_backup(self, *, scope: list[str], note: str = "", reason: str,
                                actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Yedek aldırır — POST /api/admin/bbd/backups.

        `scope`: database · uploads · config. Zamanlanmış yedek rotasyonuna
        karışmaz.
        """
        return await self._request("POST", f"{BBD}/backups",
                                   body={"scope": list(scope), "note": note}, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_create_backup")

    async def bbd_verify_backup(self, name: str, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Sağlama toplamını doğrular — POST /api/admin/bbd/backups/{name}/verify."""
        return await self._request("POST", f"{BBD}/backups/{quote(name)}/verify", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_verify_backup")

    async def bbd_download_backup(self, name: str) -> bytes:
        """Yedeği indirir — GET /api/admin/bbd/backups/{name}/download (ham bayt).

        Büyük dosya: zaman aşımı geçici olarak 5 dakikaya çıkarılır.
        """
        original = self._timeout
        self._timeout = max(original, 300.0)
        try:
            return await self._request("GET", f"{BBD}/backups/{quote(name)}/download", raw=True)
        finally:
            self._timeout = original

    async def bbd_restore_backup(self, name: str, *, reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Yedeği geri yükler — POST /api/admin/bbd/backups/{name}/restore.

        EN YIKICI İŞLEM. Mağaza tarafı geri yüklemeden önce otomatik güvenlik
        yedeği alır; yine de ekran ayrı izin, gerekçe ve açık uyarı ister.
        """
        return await self._request("POST", f"{BBD}/backups/{quote(name)}/restore", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_restore_backup")

    async def bbd_catalog_health(self) -> dict[str, Any]:
        """Katalog sağlığı — GET /api/admin/bbd/catalog/health.

        Görselsiz ürün, kırık bağlantı, SEO eksiği, fiyatsız varyant sayıları.
        """
        return await self._item(f"{BBD}/catalog/health")

    async def bbd_catalog_issues(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                                 per_page: int | None = None) -> dict[str, Any]:
        """Sağlık bulguları listesi — GET /api/admin/bbd/catalog/issues."""
        return await self._collection(f"{BBD}/catalog/health", filters, page=page,
                                      per_page=per_page)

    async def bbd_reindex_catalog(self, *, reason: str, actor: str = "",
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Arama dizinini yeniler — POST /api/admin/bbd/catalog/reindex. AĞIR İŞTİR."""
        return await self._request("POST", f"{BBD}/catalog/reindex", body={}, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_reindex_catalog")

    # ========================== 18 · BBD ÖZEL — AI · BİLDİRİM · DENETİM

    async def bbd_ai_tools(self) -> dict[str, Any]:
        """Açık AI araçları ve bütçe durumu — GET /api/admin/bbd/ai/tools."""
        return await self._collection(f"{BBD}/ai/tools")

    async def bbd_ai_run(self, tool: str, *, payload: dict[str, Any], reason: str,
                         actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """AI aracını çalıştırır — POST /api/admin/bbd/ai/tools/{tool}/run.

        ÖNERİ ÜRETİR, HİÇBİR ŞEYİ YAZMAZ. Uygulama `bbd_ai_apply` ile ve
        kullanıcı onayından sonra olur — AI ekranının kimliği budur.
        """
        return await self._request("POST", f"{BBD}/ai/tools/{tool}/run", body=payload,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_ai_run")

    async def bbd_ai_apply(self, run_id: int, *, selections: list[Any], reason: str,
                           actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        """Onaylanan önerileri uygular — POST /api/admin/bbd/ai/runs/{id}/apply."""
        return await self._request("POST", f"{BBD}/ai/runs/{int(run_id)}/apply",
                                   body={"selections": selections}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="bbd_ai_apply")

    async def bbd_ai_runs(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                          per_page: int | None = None) -> dict[str, Any]:
        """AI çalışma geçmişi — GET /api/admin/bbd/ai/runs."""
        return await self._collection(f"{BBD}/ai/runs", filters, page=page, per_page=per_page)

    async def bbd_ai_usage(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Jeton/maliyet kullanımı — GET /api/admin/bbd/ai/usage."""
        return await self._item(f"{BBD}/ai/usage", filters)

    async def bbd_notifications(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                                per_page: int | None = None) -> dict[str, Any]:
        """Bildirim gönderim geçmişi — GET /api/admin/bbd/notifications."""
        return await self._collection(f"{BBD}/notifications", filters, page=page,
                                      per_page=per_page)

    async def bbd_send_notification(self, *, payload: dict[str, Any], reason: str,
                                    actor: str = "",
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Bildirim gönderir — POST /api/admin/bbd/notifications.

        Kanal: email · sms · push. SMS'in kendi maliyeti vardır; kuru prova
        alıcı sayısını ve tahmini maliyeti döndürür.
        """
        return await self._request("POST", f"{BBD}/notifications", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="bbd_send_notification")

    async def bbd_notification_rules(self) -> dict[str, Any]:
        """Olay→şablon kuralları — GET /api/admin/bbd/notifications/rules."""
        return await self._collection(f"{BBD}/notifications/rules")

    async def bbd_save_notification_rule(self, *, payload: dict[str, Any],
                                         rule_id: int | None = None, reason: str,
                                         actor: str = "",
                                         dry_run: bool | None = None) -> dict[str, Any]:
        """Kural ekler/günceller — POST|PUT /api/admin/bbd/notifications/rules[/{id}]."""
        base = f"{BBD}/notifications/rules"
        if rule_id is None:
            return await self._request("POST", base, body=payload, reason=reason, actor=actor,
                                       dry_run=dry_run, action="bbd_create_notification_rule")
        return await self._request("PUT", f"{base}/{int(rule_id)}", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run,
                                   action="bbd_update_notification_rule")

    async def bbd_mobile_settings(self) -> dict[str, Any]:
        """Mobil uygulama ayarları — GET /api/admin/bbd/mobile/settings."""
        return await self._item(f"{BBD}/settings/mobile-app")

    async def bbd_update_mobile_settings(self, *, payload: dict[str, Any], reason: str,
                                         actor: str = "",
                                         dry_run: bool | None = None) -> dict[str, Any]:
        """Mobil ayarları yazar — PUT /api/admin/bbd/mobile/settings."""
        return await self._request("PUT", f"{BBD}/settings/mobile-app", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run,
                                   action="bbd_update_mobile_settings")

    async def bbd_review_requests(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Yorum davetleri — GET /api/admin/bbd/review-requests."""
        return await self._collection(f"{BBD}/review-requests", filters)

    async def bbd_send_review_request(self, order_id: int, *, reason: str, actor: str = "",
                                      dry_run: bool | None = None) -> dict[str, Any]:
        """Yorum daveti gönderir — POST /api/admin/bbd/review-requests."""
        return await self._request("POST", f"{BBD}/review-requests",
                                   body={"orderId": int(order_id)}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="bbd_send_review_request")

    async def bbd_return_requests(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                                  per_page: int | None = None) -> dict[str, Any]:
        """İade/değişim talepleri (RMA) — GET /api/admin/bbd/return-requests.

        Bagisto çekirdeğinde RMA YOKTUR; Talepler ekranının kaynağı budur.
        """
        return await self._collection(f"{BBD}/return-requests", filters, page=page,
                                      per_page=per_page)

    async def bbd_return_request(self, request_id: int) -> dict[str, Any]:
        """Talep detayı + yazışma zinciri — GET /api/admin/bbd/return-requests/{id}."""
        return await self._item(f"{BBD}/return-requests/{int(request_id)}")

    async def bbd_update_return_request(self, request_id: int, *, payload: dict[str, Any],
                                        reason: str, actor: str = "",
                                        dry_run: bool | None = None) -> dict[str, Any]:
        """Talebi günceller (durum, atama, yanıt) —
        PUT /api/admin/bbd/return-requests/{id}."""
        return await self._request("PUT", f"{BBD}/return-requests/{int(request_id)}",
                                   body=payload, reason=reason, actor=actor, dry_run=dry_run,
                                   action="bbd_update_return_request")

    async def bbd_audit(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                        per_page: int | None = None) -> dict[str, Any]:
        """Mağaza denetim kayıtları — GET /api/admin/bbd/audit.

        Bagisto `admin_api_audits` tablosunu okur (UDİT ekranı). Salt okunur:
        kayıt silinemez, düzenlenemez — yazma metodu BİLEREK yoktur.
        """
        return await self._collection(f"{BBD}/audits", filters, page=page, per_page=per_page)

    async def bbd_audit_entry(self, entry_id: int) -> dict[str, Any]:
        """Denetim kaydı detayı (öncesi/sonrası farkı) —
        GET /api/admin/bbd/audit/{id}."""
        return await self._item(f"{BBD}/audits/{int(entry_id)}")
