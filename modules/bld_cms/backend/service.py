"""Site İçeriği — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Yedi içerik anahtarı, hizmet sayfaları ve
bilgi merkezi yazıları `veykemtu_site_*` tablolarındadır ve buraya `bld.api`
geçidinden gelir (K4); bu modül ham httpx kullanmaz ve UZAK VERİNİN KOPYASINI
TUTMAZ.

YEREL TABLO NEYİ TUTAR VE NEDEN. İki şeyin sunucuda karşılığı yok:

 1. YAZMA DENEMESİ. BLD `veykemtu_control_audit` tutuyor ama o kayıt yalnız
    SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa "kim neyi denedi" sorusunun
    cevabı yalnız burada kalır.
 2. DÜZENLEME GEÇMİŞİ. `PUT /content/{key}` değeri ÜSTÜNE yazıyor ve sunucu
    denetim satırına bilerek yalnız künye koyuyor ("İçeriğin tam kopyasını
    denetime yazmak, tabloyu bir sürüm deposu hâline getirirdi" — cms.md).
    Karar doğru; sonucu şu: sunucuda "dün ne yazıyordu" sorusunun cevabı YOK.
    Bu ekran o cevabı kendi tablosunda tutar. Bu bir YEDEK DEĞİLDİR: geri
    yükleyen bir uç yoktur, eski sürüm düzenleyiciye getirilir ve yönetici
    normal bir yazma olarak — kendi gerekçesiyle — kaydeder.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar.

`ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır: uç çalıştı,
cevabı "bağlanamadım"dır. Ayrımı taşıyan alan `connected`'dır.

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe ve gövde denetimi (K9 — arayüzde göstermek yetmez)
    2. TAZE OKUMA (kayıt aradan değişmiş olabilir; "önceki hâl" de buradan)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı — `dry_run=` HER ZAMAN AÇIKÇA verilir
    5. yerel iz: `ok` / `dry_run` / `hata` + başarılıysa sürüm satırı

Dördüncü adımın notu bu modül için hayatidir: geçidin `config/local.yaml`
dosyası git dışıdır ve orada `dry_run_default: true` yazıyor olabilir. Bayrağı
atlayan bir çağrı hiçbir şey yazmadan `{"ok": true}` alır; ekran "kaydedildi"
der ve site hiç değişmez — yani bu ekranın engellemek için var olduğu tam o
cümle kurulur.

YENİDEN ÇİZDİRME BAŞARISIZLIĞI YAZMAYI BAŞARISIZ YAPMAZ (cms.md) ama SESSİZ DE
KALMAZ: her yazma yanıtı `revalidate` bloğu taşır ve panel onu ekranda gösterir.
"""

from __future__ import annotations

import json
from typing import Any

from . import content as cx

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Yerel iz ve sürüm satırlarının `action` değerleri. Sunucudaki denetim
#: eylemleriyle (cms.md · Denetim eylemleri) AYNI adlar kullanılır: iki iz yan
#: yana konduğunda aynı satırın iki yarısı olduğu görülebilsin.
ACT_CONTENT = "cms.content.update"
ACT_SERVICE_CREATE = "cms.service.create"
ACT_SERVICE_UPDATE = "cms.service.update"
ACT_SERVICE_DELETE = "cms.service.delete"
ACT_POST_CREATE = "cms.post.create"
ACT_POST_UPDATE = "cms.post.update"
ACT_POST_DELETE = "cms.post.delete"
ACT_REVALIDATE = "cms.revalidate"
ACT_IMAGE = "cms.image.upload"

#: Tek yazıyı okuyan bir uç YOK (cms.md yalnız sayfalı liste veriyor). Taze
#: okuma listeyi tarar; tarama BU KADAR sayfada durur. Canlıda 21 yazı var,
#: 20 sayfa × 100 satır büyümeye fazlasıyla yer bırakır ve bozuk bir `meta`
#: yüzünden sonsuz taramayı da engeller.
POST_SCAN_PAGES = 20
POST_SCAN_SIZE = 100

#: Satır içi görseli yükleyecek geçit metodunun adı. BUGÜN YOK: `cms.md`
#: sözleşmesinde görsel yükleme ucu bulunmuyor (`products.md` ürün görseli
#: sunuyor, o başka bir alandır). Metot geçide eklendiği gün burada bir
#: değişiklik gerekmez; eklenmediği gün de ekran çökmez, yalnız düzenleyicideki
#: görsel düğmesi hiç çizilmez ve nedeni yazılır (K7).
IMAGE_METHOD = "upload_site_image"

#: Görsel ön denetimi — sunucu ucu geldiğinde asıl kapı orada olacak; bu
#: değerler yalnız kullanıcıya erken ve anlaşılır bir cevap vermek içindir.
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp")


class CmsService:
    """Site İçeriği ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any,
                 config: dict[str, Any] | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}

        self._audit = store.table("audit")
        self._revisions = store.table("revisions")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci `dryRun` alanını HİÇ göndermezse geçerli olan değer.

        KAPALI. Panel bu alanı göndermiyor (ekranda kuru prova şalteri yok);
        açık bırakmak, panelden yapılan HER yazmayı sessizce bir provaya
        çevirmek olurdu. Yedek değer de `False`: ayar dosyası okunamadığında
        ekranın "kaydedildi" deyip hiçbir şey yazmaması, açık bir hatadan çok
        daha pahalıdır.
        """
        return bool(self._config.get("dry_run_default", False))

    @property
    def _revalidate_default(self) -> bool:
        """Yazma sonrası siteyi tazeleme varsayılanı — AÇIK.

        Kapalı olsaydı olağan akış "kaydettim ama sitede yok" ile biterdi ve
        yönetici eksik adımı ancak siteyi açıp bakınca öğrenirdi.
        """
        return bool(self._config.get("revalidate_after_save", True))

    @property
    def _page_size(self) -> int:
        return max(5, min(200, cx.as_int(self._config.get("page_size"), 25)))

    @property
    def _revision_limit(self) -> int:
        return max(1, min(500, cx.as_int(self._config.get("revision_limit"), 50)))

    @property
    def _revision_max_bytes(self) -> int:
        return max(1024, cx.as_int(self._config.get("revision_max_bytes"),
                                   cx.MAX_CONTENT_BYTES))

    @property
    def _site_url(self) -> str:
        """Sitenin adresi — YALNIZ "sitede gör" bağlantısı için.

        Boşsa bağlantı hiç çizilmez. Uydurulmuş bir adres, yöneticiyi var
        olmayan bir sayfaya göndermek olurdu.
        """
        return cx.text(self._config.get("site_url")).rstrip("/")

    @property
    def _image_max_bytes(self) -> int:
        return max(1, cx.as_int(self._config.get("image_max_mb"), 5)) * 1024 * 1024

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    def _revalidate_flag(self, value: bool | None) -> bool:
        return self._revalidate_default if value is None else bool(value)

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_type: str = cx.TARGET_CONTENT, target_id: int = 0,
                      target_key: str = "", detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor; bu satır
        ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN istekleri bilir. Ağ
        koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim neyi denedi"
        sorusunun cevabı yalnız burada kalır.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, target_key, action, reason, actor, result, "
                "detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, int(target_id or 0), cx.text(target_key), action,
                 cx.text(reason), cx.text(actor), result,
                 json.dumps(detail or {}, ensure_ascii=False), cx.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    def _snapshot(self, value: Any) -> tuple[str, bool]:
        """Sürüm satırına yazılacak JSON — sınırı aşarsa KÜNYEYE düşer.

        Kesilen bir metni "eski hâl" diye saklamak, geri getirildiğinde yarım
        bir sayfa üretirdi. Bu yüzden büyük değer kırpılmaz, tümden künyeye
        indirilir ve satır `truncated` işaretini taşır — ekran "bu sürümün
        gövdesi saklanmadı" der, yarım bir gövde göstermez.
        """
        try:
            raw = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"_unserializable": True}, ensure_ascii=False), True
        if len(raw.encode("utf-8")) > self._revision_max_bytes:
            return json.dumps({"_truncated": True, "bytes": len(raw.encode("utf-8"))},
                              ensure_ascii=False), True
        return raw, False

    async def _revision(self, *, action: str, target_type: str, target_id: int,
                        target_key: str, title: str, before: Any, after: Any,
                        actor: str, reason: str, audit_id: int = 0) -> None:
        """Düzenleme geçmişine bir satır. YALNIZ GERÇEK YAZMADAN SONRA.

        Kuru provada yazılmaz: BLD'de hiçbir şey değişmedi ve geçmişe "şu an
        şöyleydi" diye bir satır koymak, hiç olmamış bir değişikliği kayda
        geçirmek olurdu.
        """
        before_json, cut_before = self._snapshot(before)
        after_json, cut_after = self._snapshot(after)
        try:
            await self._store.execute(
                f"INSERT INTO {self._revisions} "
                "(target_type, target_id, target_key, title, action, before_json, "
                "after_json, truncated, actor, reason, audit_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, int(target_id or 0), cx.text(target_key), cx.text(title),
                 action, before_json, after_json, int(cut_before or cut_after),
                 cx.text(actor), cx.text(reason), int(audit_id or 0), cx.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — geçmiş yazılamadı, iş durmasın (K7)
            self._log.warning("sürüm satırı yazılamadı", action=action,
                              error=str(failure))

    async def revisions(self, *, target_type: str = "", target_id: int = 0,
                        target_key: str = "", limit: int = 0) -> dict[str, Any]:
        """Yerel düzenleme geçmişi. AĞA ÇIKMAZ — BLD düşse de okunur."""
        count = max(1, min(500, int(limit or self._revision_limit)))
        where: list[str] = []
        params: list[Any] = []
        kind = cx.text(target_type)
        if kind in cx.TARGET_TYPES:
            where.append("target_type = ?")
            params.append(kind)
        if int(target_id or 0) > 0:
            where.append("target_id = ?")
            params.append(int(target_id))
        if cx.text(target_key):
            where.append("target_key = ?")
            params.append(cx.text(target_key))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        params.append(count)

        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_type, target_id, target_key, title, action, "
                f"truncated, actor, reason, audit_id, created_at FROM {self._revisions}"
                f"{clause} ORDER BY id DESC LIMIT ?",
                tuple(params),
            )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("düzenleme geçmişi okunamadı", error=str(failure))
            return {"ok": True, "connected": True, "error": self._fail(failure),
                    "items": []}
        return {"ok": True, "connected": True, "error": "",
                "items": [dict(row) for row in rows]}

    async def revision(self, revision_id: int) -> dict[str, Any]:
        """Tek sürümün gövdesi — "eski hâli düzenleyiciye getir" bunu okur."""
        try:
            row = await self._store.fetch_one(
                f"SELECT * FROM {self._revisions} WHERE id = ?", (int(revision_id),))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sürüm okunamadı", revisionId=revision_id,
                              error=str(failure))
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False, "error": "Sürüm bulunamadı."}

        data = dict(row)
        for key in ("before_json", "after_json"):
            data[key] = self._decode(data.get(key))
        return {"ok": True, "error": "", "data": data,
                # Kırpılmış sürüm düzenleyiciye GETİRİLEMEZ ve ekran bunu
                # düğmeyi çizmeden önce bilmeli.
                "restorable": not bool(data.get("truncated"))}

    @staticmethod
    def _decode(raw: Any) -> Any:
        try:
            return json.loads(cx.text(raw) or "null")
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde zorunlu göstermek,
        istemcinin gövdeyi elle kurmasını engellemez."""
        return cx.reason_error(reason)

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit zarfı zaten açıyor; `{"items": …}` ve `{"data": …}` biçimleri de
        kabul edilir çünkü tek bir ada bağlanmak, ad tutmadığında ekranı
        SESSİZCE boş gösterirdi.
        """
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _meta(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return dict(payload["meta"])
        return {}

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Tekil yanıttan kaydı çıkarır (`{"data": {...}}` ya da düz)."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    def _image_support(self) -> dict[str, Any]:
        """Satır içi görsel yüklenebiliyor mu — ve yüklenemiyorsa NEDEN.

        Düzenleyicideki görsel düğmesi bu cevaba bakarak çizilir. Kitin kuralı
        bir düğmenin ya çalışması ya da hiç çizilmemesidir; basınca "yükleme
        ucu yok" diyen bir düğme, bozuk bir düğmedir.
        """
        if getattr(self._api, IMAGE_METHOD, None) is None:
            return {
                "available": False,
                "reason": ("Satır içi görsel yükleme ucu sözleşmede henüz yok "
                           "(docs/control/cms.md görsel yükleme sunmuyor). Görseli "
                           "siteye yükleyip adresini düzenleyicinin \"Kaynak\" "
                           "sekmesinden `<img src=\"/…\">` ile ekleyebilirsiniz; "
                           "adres `/` ile ya da http/https ile başlamalı."),
                "rules": {},
            }
        return {"available": True, "reason": "",
                "rules": {"accept": list(IMAGE_MIMES),
                          "maxBytes": self._image_max_bytes}}

    def screen(self) -> dict[str, Any]:
        """Ekranın YEREL sözleşmesi. Ağa çıkmaz — geçit düşse de form çizilir (K7).

        Panel bu bloğu her okuma yanıtında alır: içerik anahtarlarının listesi,
        beyaz liste, alan sınırları ve görsel yükleme durumu. Paneldeki bir
        kopya, sunucudaki liste değiştiğinde ekranın yanlış sınırı göstermesi
        olurdu.
        """
        return {
            "content_keys": [
                {"key": key, **{k: v for k, v in cx.CONTENT_SPEC[key].items()}}
                for key in cx.CONTENT_KEYS
            ],
            "editor": {
                "allowed_tags": sorted(cx.ALLOWED_TAGS),
                "style_props": sorted(cx.STYLE_PROPS),
                "safe_schemes": list(cx.SAFE_SCHEMES),
            },
            "limits": {
                "reason_min": cx.MIN_REASON,
                "reason_max": cx.MAX_REASON,
                "content_bytes": cx.MAX_CONTENT_BYTES,
                "list_items": cx.MAX_LIST_ITEMS,
                "list_item_chars": cx.MAX_LIST_ITEM_CHARS,
                "revalidate_paths": cx.MAX_REVALIDATE_PATHS,
                "service": dict(cx.SERVICE_LIMITS),
                "post": dict(cx.POST_LIMITS),
            },
            "image_upload": self._image_support(),
            "revalidate_default": self._revalidate_default,
            "site_url": self._site_url,
            "page_size": self._page_size,
        }

    # ================================================================= okuma

    async def content(self) -> dict[str, Any]:
        """Yedi içerik anahtarının tamamı.

        `GET /content/{key}` YOKTUR (cms.md): tek anahtar için ayrı bir uç,
        panelin yedi istek atması demekti.
        """
        screen = self.screen()
        try:
            payload = await self._api.site_content()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("site içeriği okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "keys": list(cx.CONTENT_KEYS), "screen": screen}
        view = cx.content_view(payload)
        return {"ok": True, "connected": True, "error": "", **view, "screen": screen}

    async def services(self, *, published: str = "") -> dict[str, Any]:
        """Hizmet sayfaları. Sayfalanmaz — hizmet sayısı onlarla ifade edilir."""
        screen = self.screen()
        try:
            payload = await self._api.site_services(
                published=cx.published_filter(published))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("hizmet listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "screen": screen}
        rows = [cx.service_row(raw) for raw in self._items(payload)]
        # Sıra sunucudan geliyor ama `sort_order` sitenin çizim sırasıdır ve
        # ekranın da onu göstermesi gerekir; eşitlikte kimlik ayırır.
        rows.sort(key=lambda row: (row["sort_order"], row["id"]))
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "screen": screen}

    async def posts(self, *, q: str = "", category: str = "", published: str = "",
                    page: int = 1, per_page: int = 0) -> dict[str, Any]:
        """Bilgi merkezi yazıları — SAYFALI.

        `meta.categories` sunucunun damıttığı listedir ve açılır kutuyu o
        doldurur: kategori ayrı bir tablo değil, serbest bir metin alanıdır;
        panel kendi listesini tutsaydı yönetici her seferinde yeni bir kategori
        uydururdu.
        """
        screen = self.screen()
        size = max(5, min(200, int(per_page or self._page_size)))
        try:
            payload = await self._api.site_posts(
                q=cx.text(q), category=cx.text(category),
                published=cx.published_filter(published),
                page=max(1, int(page or 1)), per_page=size)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yazı listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "meta": {"page": 1, "per_page": size, "total": 0,
                                          "last_page": 1, "categories": []},
                    "screen": screen}
        meta = self._meta(payload)
        meta.setdefault("page", max(1, int(page or 1)))
        meta.setdefault("per_page", size)
        meta.setdefault("categories", [])
        return {"ok": True, "connected": True, "error": "",
                "items": [cx.post_row(raw) for raw in self._items(payload)],
                "meta": meta, "screen": screen}

    # ------------------------------------------------------------ taze okuma

    async def _fresh_content(self, key: str) -> tuple[dict[str, Any] | None, str]:
        """Anahtarın TAZE hâli. `(satır, hata)` döner."""
        try:
            payload = await self._api.site_content()
        except Exception as failure:  # noqa: BLE001 — K7
            return None, self._fail(failure)
        for row in cx.content_view(payload)["items"]:
            if row["key"] == key:
                return row, ""
        return None, cx.content_key_error(key) or "Anahtar bulunamadı."

    async def _fresh_service(self, service_id: int) -> tuple[dict[str, Any] | None, str]:
        """Hizmetin TAZE hâli. Sözleşmede tek hizmet okuyan uç yok; liste
        sayfalanmadığı için tek istek yeter."""
        try:
            payload = await self._api.site_services(published="all")
        except Exception as failure:  # noqa: BLE001 — K7
            return None, self._fail(failure)
        for raw in self._items(payload):
            if cx.as_int(raw.get("id")) == int(service_id):
                return cx.service_row(raw), ""
        return None, "Hizmet bulunamadı; liste aradan değişmiş olabilir."

    async def _fresh_post(self, post_id: int) -> tuple[dict[str, Any] | None, str]:
        """Yazının TAZE hâli.

        TEK YAZI OKUYAN UÇ YOK (cms.md yalnız sayfalı liste veriyor), bu yüzden
        liste taranır. Tarama `POST_SCAN_PAGES` sayfada durur: bozuk bir `meta`
        yüzünden sonsuza dönmemesi ve paylaşılan saatlik bütçeyi yakmaması için.
        Bulunamazsa "bulunamadı" denir — panelin elindeki eski satır "taze"
        sayılmaz, çünkü o zaman üzerine yazdığımız şeyin ne olduğunu bilmezdik.
        """
        page = 1
        while page <= POST_SCAN_PAGES:
            try:
                payload = await self._api.site_posts(published="all", page=page,
                                                     per_page=POST_SCAN_SIZE)
            except Exception as failure:  # noqa: BLE001 — K7
                return None, self._fail(failure)
            rows = self._items(payload)
            for raw in rows:
                if cx.as_int(raw.get("id")) == int(post_id):
                    return cx.post_row(raw), ""
            meta = self._meta(payload)
            last = cx.as_int(meta.get("last_page"), page)
            if not rows or page >= last:
                break
            page += 1
        return None, "Yazı bulunamadı; liste aradan değişmiş olabilir."

    # ================================================================= yazma

    async def save_content(self, key: str, *, value: Any, reason: str, actor: str,
                           dry_run: bool | None = None,
                           revalidate: bool | None = None) -> dict[str, Any]:
        """Tek içerik anahtarını yazar.

        `value` TAM DEĞERDİR, birleştirilmez (cms.md). Kısmi yazma, iç içe
        geçmiş JSON'da "hangi seviyede birleştiriliyor" sorusunu doğururdu ve
        iki farklı cevabı olan bir kural sessizce veri kaybettirir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean_key = cx.text(key)
        problem = cx.content_key_error(clean_key)
        if problem:
            return {"ok": False, "error": problem}
        problem = cx.content_value_error(clean_key, value)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        fresh = self._revalidate_flag(revalidate)

        current, problem = await self._fresh_content(clean_key)
        if current is None:
            return {"ok": False, "error": problem}
        if current["value"] == value:
            return {"ok": True, "error": "", "dry_run": kuru, "changed": False,
                    "warnings": [], "revalidate": cx.revalidate_view(None, requested=False),
                    "note": "Değer zaten bu; sunucuya istek gönderilmedi."}

        shape_warning = cx.content_shape_warning(clean_key, value)
        detail = {"key": clean_key, "bytes": cx.json_size(value), "dry_run": kuru}
        await self._record(action=ACT_CONTENT, reason=reason, actor=actor, result=TRIED,
                           target_key=clean_key, detail=detail)
        if kuru:
            await self._record(action=ACT_CONTENT, reason=reason, actor=actor, result=DRY,
                               target_key=clean_key, detail=detail)
            return {"ok": True, "error": "", "dry_run": True, "changed": True,
                    "would": {"action": ACT_CONTENT, "key": clean_key},
                    "warnings": [], "revalidate": cx.revalidate_view(None, requested=False)}

        try:
            result = await self._api.set_site_content(
                clean_key, value=value, revalidate=fresh, reason=reason, actor=actor,
                dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_CONTENT, reason=reason, actor=actor,
                               result=FAILED, target_key=clean_key,
                               detail={"key": clean_key, "error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        audit_id = cx.audit_id_of(result)
        await self._record(action=ACT_CONTENT, reason=reason, actor=actor, result=DONE,
                           target_key=clean_key,
                           detail={"key": clean_key, "audit_id": audit_id})
        await self._revision(action=ACT_CONTENT, target_type=cx.TARGET_CONTENT,
                             target_id=0, target_key=clean_key,
                             title=cx.CONTENT_SPEC[clean_key]["label"],
                             before=current["value"], after=value, actor=actor,
                             reason=reason, audit_id=audit_id)

        warnings = cx.warnings_of(result)
        if shape_warning:
            warnings = [*warnings, {"code": "shape_mismatch", "note": shape_warning}]
        return {"ok": True, "error": "", "dry_run": False, "changed": True,
                "audit_id": audit_id, "warnings": warnings,
                "data": self._record_of(result, "data"),
                "revalidate": cx.revalidate_view(result, requested=fresh)}

    # -------------------------------------------------------------- hizmetler

    async def create_service(self, *, fields: dict[str, Any], reason: str, actor: str,
                             dry_run: bool | None = None,
                             revalidate: bool | None = None) -> dict[str, Any]:
        """Yeni hizmet sayfası."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = self._clean_service(fields)
        problem = cx.service_fields_error(body, creating=True)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        fresh = self._revalidate_flag(revalidate)
        slug = cx.text(body.get("slug"))

        # TAZE OKUMA: sunucu `slug` çakışmasını 409 ile reddediyor ama o hata
        # kullanıcıya "CONFLICT" diye ulaşır. Kapı burada da var ki hangi
        # kaydın çakıştığı SÖYLENEBİLSİN.
        try:
            payload = await self._api.site_services(published="all")
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        for raw in self._items(payload):
            if cx.text(raw.get("slug")) == slug:
                await self._record(action=ACT_SERVICE_CREATE, reason=reason, actor=actor,
                                   result=BLOCKED, target_type=cx.TARGET_SERVICE,
                                   target_key=slug, detail={"slug": slug})
                return {"ok": False,
                        "error": f"'{slug}' adresi zaten '{cx.text(raw.get('title'))}' "
                                 "hizmetinde kullanılıyor. Adres tekil olmalı."}

        detail = {"slug": slug, "title": cx.text(body.get("title")), "dry_run": kuru}
        await self._record(action=ACT_SERVICE_CREATE, reason=reason, actor=actor,
                           result=TRIED, target_type=cx.TARGET_SERVICE,
                           target_key=slug, detail=detail)
        if kuru:
            await self._record(action=ACT_SERVICE_CREATE, reason=reason, actor=actor,
                               result=DRY, target_type=cx.TARGET_SERVICE,
                               target_key=slug, detail=detail)
            return {"ok": True, "error": "", "dry_run": True, "warnings": [],
                    "would": {"action": ACT_SERVICE_CREATE, "slug": slug},
                    "revalidate": cx.revalidate_view(None, requested=False)}

        extra = {key: value for key, value in body.items()
                 if key not in ("slug", "title")}
        try:
            result = await self._api.create_site_service(
                slug=slug, title=cx.text(body.get("title")), fields=extra,
                revalidate=fresh, reason=reason, actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_SERVICE_CREATE, reason=reason, actor=actor,
                               result=FAILED, target_type=cx.TARGET_SERVICE,
                               target_key=slug, detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        row = cx.service_row(self._record_of(result, "data", "service"))
        audit_id = cx.audit_id_of(result)
        await self._record(action=ACT_SERVICE_CREATE, reason=reason, actor=actor,
                           result=DONE, target_type=cx.TARGET_SERVICE,
                           target_id=row["id"], target_key=slug,
                           detail={"slug": slug, "audit_id": audit_id})
        await self._revision(action=ACT_SERVICE_CREATE, target_type=cx.TARGET_SERVICE,
                             target_id=row["id"], target_key=slug, title=row["title"],
                             before=None, after=row, actor=actor, reason=reason,
                             audit_id=audit_id)
        return {"ok": True, "error": "", "dry_run": False, "audit_id": audit_id,
                "data": row, "warnings": cx.warnings_of(result),
                # GÖNDERİLEN DEĞİL, GERİ OKUNAN gövde gösterilir: sunucu
                # `body_html` alanını kayıt anında temizliyor ve gönderdiğini
                # geri okumayan bir editör, yapıştırmanın kaybolduğunu fark
                # ettirmezdi.
                "sanitized_note": cx.html_changed_note(body.get("body_html", ""),
                                                       row["body_html"]),
                "revalidate": cx.revalidate_view(result, requested=fresh)}

    async def update_service(self, service_id: int, *, fields: dict[str, Any],
                             reason: str, actor: str, dry_run: bool | None = None,
                             revalidate: bool | None = None) -> dict[str, Any]:
        """Hizmeti günceller — KISMİ. Yalnız gönderilen alanlar değişir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = self._clean_service(fields)
        if not body:
            return {"ok": False,
                    "error": "Değişen alan yok; yalnız gerekçe taşıyan bir güncelleme "
                             "hiçbir şey değiştirmeden denetim izine satır yazardı."}
        problem = cx.service_fields_error(body, creating=False)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        fresh = self._revalidate_flag(revalidate)

        current, problem = await self._fresh_service(service_id)
        if current is None:
            return {"ok": False, "error": problem}

        notice = cx.slug_change_notice(current["slug"], cx.text(body.get("slug")))
        detail = {"fields": sorted(body), "slug": current["slug"], "dry_run": kuru}
        await self._record(action=ACT_SERVICE_UPDATE, reason=reason, actor=actor,
                           result=TRIED, target_type=cx.TARGET_SERVICE,
                           target_id=service_id, target_key=current["slug"],
                           detail=detail)
        if kuru:
            await self._record(action=ACT_SERVICE_UPDATE, reason=reason, actor=actor,
                               result=DRY, target_type=cx.TARGET_SERVICE,
                               target_id=service_id, target_key=current["slug"],
                               detail=detail)
            return {"ok": True, "error": "", "dry_run": True, "data": current,
                    "warnings": [notice] if notice else [],
                    "would": {"action": ACT_SERVICE_UPDATE, "fields": sorted(body)},
                    "revalidate": cx.revalidate_view(None, requested=False)}

        try:
            result = await self._api.update_site_service(
                int(service_id), fields=body, revalidate=fresh, reason=reason,
                actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_SERVICE_UPDATE, reason=reason, actor=actor,
                               result=FAILED, target_type=cx.TARGET_SERVICE,
                               target_id=service_id, target_key=current["slug"],
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        row = cx.service_row(self._record_of(result, "data", "service"))
        if not row["id"]:
            # Yanıt kaydı taşımıyorsa taze okumanın üstüne gönderilen alanlar
            # yazılır — ekranın boş bir satır göstermesi, yazmanın olmadığını
            # düşündürürdü.
            row = {**current, **{k: v for k, v in body.items() if k in current}}
        audit_id = cx.audit_id_of(result)
        await self._record(action=ACT_SERVICE_UPDATE, reason=reason, actor=actor,
                           result=DONE, target_type=cx.TARGET_SERVICE,
                           target_id=service_id, target_key=row["slug"],
                           detail={"fields": sorted(body), "audit_id": audit_id})
        await self._revision(action=ACT_SERVICE_UPDATE, target_type=cx.TARGET_SERVICE,
                             target_id=service_id, target_key=row["slug"],
                             title=row["title"], before=current, after=row,
                             actor=actor, reason=reason, audit_id=audit_id)

        warnings = cx.warnings_of(result)
        if notice and not any(cx.text(item.get("code")) == "slug_changed"
                              for item in warnings):
            warnings = [*warnings, notice]
        return {"ok": True, "error": "", "dry_run": False, "audit_id": audit_id,
                "data": row, "warnings": warnings,
                "sanitized_note": cx.html_changed_note(body.get("body_html", ""),
                                                       row["body_html"])
                if "body_html" in body else "",
                "revalidate": cx.revalidate_view(result, requested=fresh)}

    async def delete_service(self, service_id: int, *, reason: str, actor: str,
                             allow_delete: bool, dry_run: bool | None = None,
                             revalidate: bool | None = None) -> dict[str, Any]:
        """YIKICI. Kayıt gerçekten silinir ve geri gelmez.

        İzin kapısı burada ve uçta iki kez denetlenir (K9 — çift kapı).
        """
        if not allow_delete:
            await self._record(action=ACT_SERVICE_DELETE, reason=reason, actor=actor,
                               result=BLOCKED, target_type=cx.TARGET_SERVICE,
                               target_id=service_id, detail={"missing": "bld_cms.delete"})
            return {"ok": False,
                    "error": "Silme yetkiniz yok (`bld_cms.delete`). Sayfayı sitede "
                             "göstermemek için yayından çıkarabilirsiniz."}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        fresh = self._revalidate_flag(revalidate)

        current, problem = await self._fresh_service(service_id)
        if current is None:
            return {"ok": False, "error": problem}

        detail = {"slug": current["slug"], "title": current["title"], "dry_run": kuru}
        await self._record(action=ACT_SERVICE_DELETE, reason=reason, actor=actor,
                           result=TRIED, target_type=cx.TARGET_SERVICE,
                           target_id=service_id, target_key=current["slug"],
                           detail=detail)
        if kuru:
            await self._record(action=ACT_SERVICE_DELETE, reason=reason, actor=actor,
                               result=DRY, target_type=cx.TARGET_SERVICE,
                               target_id=service_id, target_key=current["slug"],
                               detail=detail)
            return {"ok": True, "error": "", "dry_run": True, "warnings": [],
                    "would": {"action": ACT_SERVICE_DELETE, "slug": current["slug"]},
                    "revalidate": cx.revalidate_view(None, requested=False)}

        try:
            result = await self._api.delete_site_service(
                int(service_id), revalidate=fresh, reason=reason, actor=actor,
                dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_SERVICE_DELETE, reason=reason, actor=actor,
                               result=FAILED, target_type=cx.TARGET_SERVICE,
                               target_id=service_id, target_key=current["slug"],
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        audit_id = cx.audit_id_of(result)
        await self._record(action=ACT_SERVICE_DELETE, reason=reason, actor=actor,
                           result=DONE, target_type=cx.TARGET_SERVICE,
                           target_id=service_id, target_key=current["slug"],
                           detail={"slug": current["slug"], "audit_id": audit_id})
        # Silinen kaydın SON HÂLİ geçmişe yazılır. Geri yükleyen bir uç yok:
        # bu satır bir yedek değil, "ne silindi" sorusunun cevabıdır.
        await self._revision(action=ACT_SERVICE_DELETE, target_type=cx.TARGET_SERVICE,
                             target_id=service_id, target_key=current["slug"],
                             title=current["title"], before=current, after=None,
                             actor=actor, reason=reason, audit_id=audit_id)
        return {"ok": True, "error": "", "dry_run": False, "audit_id": audit_id,
                "warnings": cx.warnings_of(result),
                "revalidate": cx.revalidate_view(result, requested=fresh)}

    # ----------------------------------------------------------------- yazılar

    async def create_post(self, *, fields: dict[str, Any], reason: str, actor: str,
                          dry_run: bool | None = None,
                          revalidate: bool | None = None) -> dict[str, Any]:
        """Yeni yazı. `body_html` ZORUNLUDUR ve boş olamaz."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = self._clean_post(fields)
        problem = cx.post_fields_error(body, creating=True)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        fresh = self._revalidate_flag(revalidate)
        slug = cx.text(body.get("slug"))

        detail = {"slug": slug, "title": cx.text(body.get("title")), "dry_run": kuru}
        await self._record(action=ACT_POST_CREATE, reason=reason, actor=actor,
                           result=TRIED, target_type=cx.TARGET_POST, target_key=slug,
                           detail=detail)
        if kuru:
            await self._record(action=ACT_POST_CREATE, reason=reason, actor=actor,
                               result=DRY, target_type=cx.TARGET_POST, target_key=slug,
                               detail=detail)
            return {"ok": True, "error": "", "dry_run": True, "warnings": [],
                    "would": {"action": ACT_POST_CREATE, "slug": slug},
                    "revalidate": cx.revalidate_view(None, requested=False)}

        extra = {key: value for key, value in body.items()
                 if key not in ("slug", "title", "body_html")}
        try:
            result = await self._api.create_site_post(
                slug=slug, title=cx.text(body.get("title")),
                body_html=cx.text(body.get("body_html")), fields=extra,
                revalidate=fresh, reason=reason, actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_POST_CREATE, reason=reason, actor=actor,
                               result=FAILED, target_type=cx.TARGET_POST,
                               target_key=slug, detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        row = cx.post_row(self._record_of(result, "data", "post"))
        audit_id = cx.audit_id_of(result)
        await self._record(action=ACT_POST_CREATE, reason=reason, actor=actor,
                           result=DONE, target_type=cx.TARGET_POST, target_id=row["id"],
                           target_key=slug, detail={"slug": slug, "audit_id": audit_id})
        await self._revision(action=ACT_POST_CREATE, target_type=cx.TARGET_POST,
                             target_id=row["id"], target_key=slug, title=row["title"],
                             before=None, after=row, actor=actor, reason=reason,
                             audit_id=audit_id)
        return {"ok": True, "error": "", "dry_run": False, "audit_id": audit_id,
                "data": row, "warnings": cx.warnings_of(result),
                "sanitized_note": cx.html_changed_note(body.get("body_html", ""),
                                                       row["body_html"]),
                "revalidate": cx.revalidate_view(result, requested=fresh)}

    async def update_post(self, post_id: int, *, fields: dict[str, Any], reason: str,
                          actor: str, dry_run: bool | None = None,
                          revalidate: bool | None = None) -> dict[str, Any]:
        """Yazıyı günceller — KISMİ."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = self._clean_post(fields)
        if not body:
            return {"ok": False,
                    "error": "Değişen alan yok; yalnız gerekçe taşıyan bir güncelleme "
                             "hiçbir şey değiştirmeden denetim izine satır yazardı."}
        problem = cx.post_fields_error(body, creating=False)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        fresh = self._revalidate_flag(revalidate)

        current, problem = await self._fresh_post(post_id)
        if current is None:
            return {"ok": False, "error": problem}

        notice = cx.slug_change_notice(current["slug"], cx.text(body.get("slug")))
        detail = {"fields": sorted(body), "slug": current["slug"], "dry_run": kuru}
        await self._record(action=ACT_POST_UPDATE, reason=reason, actor=actor,
                           result=TRIED, target_type=cx.TARGET_POST, target_id=post_id,
                           target_key=current["slug"], detail=detail)
        if kuru:
            await self._record(action=ACT_POST_UPDATE, reason=reason, actor=actor,
                               result=DRY, target_type=cx.TARGET_POST,
                               target_id=post_id, target_key=current["slug"],
                               detail=detail)
            return {"ok": True, "error": "", "dry_run": True, "data": current,
                    "warnings": [notice] if notice else [],
                    "would": {"action": ACT_POST_UPDATE, "fields": sorted(body)},
                    "revalidate": cx.revalidate_view(None, requested=False)}

        try:
            result = await self._api.update_site_post(
                int(post_id), fields=body, revalidate=fresh, reason=reason,
                actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_POST_UPDATE, reason=reason, actor=actor,
                               result=FAILED, target_type=cx.TARGET_POST,
                               target_id=post_id, target_key=current["slug"],
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        row = cx.post_row(self._record_of(result, "data", "post"))
        if not row["id"]:
            row = {**current, **{k: v for k, v in body.items() if k in current}}
        audit_id = cx.audit_id_of(result)
        await self._record(action=ACT_POST_UPDATE, reason=reason, actor=actor,
                           result=DONE, target_type=cx.TARGET_POST, target_id=post_id,
                           target_key=row["slug"],
                           detail={"fields": sorted(body), "audit_id": audit_id})
        await self._revision(action=ACT_POST_UPDATE, target_type=cx.TARGET_POST,
                             target_id=post_id, target_key=row["slug"],
                             title=row["title"], before=current, after=row,
                             actor=actor, reason=reason, audit_id=audit_id)

        warnings = cx.warnings_of(result)
        if notice and not any(cx.text(item.get("code")) == "slug_changed"
                              for item in warnings):
            warnings = [*warnings, notice]
        return {"ok": True, "error": "", "dry_run": False, "audit_id": audit_id,
                "data": row, "warnings": warnings,
                "sanitized_note": cx.html_changed_note(body.get("body_html", ""),
                                                       row["body_html"])
                if "body_html" in body else "",
                "revalidate": cx.revalidate_view(result, requested=fresh)}

    async def delete_post(self, post_id: int, *, reason: str, actor: str,
                          allow_delete: bool, dry_run: bool | None = None,
                          revalidate: bool | None = None) -> dict[str, Any]:
        """YIKICI. Yazı gerçekten silinir ve geri gelmez."""
        if not allow_delete:
            await self._record(action=ACT_POST_DELETE, reason=reason, actor=actor,
                               result=BLOCKED, target_type=cx.TARGET_POST,
                               target_id=post_id, detail={"missing": "bld_cms.delete"})
            return {"ok": False,
                    "error": "Silme yetkiniz yok (`bld_cms.delete`). Yazıyı sitede "
                             "göstermemek için yayından çıkarabilirsiniz."}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        fresh = self._revalidate_flag(revalidate)

        current, problem = await self._fresh_post(post_id)
        if current is None:
            return {"ok": False, "error": problem}

        detail = {"slug": current["slug"], "title": current["title"], "dry_run": kuru}
        await self._record(action=ACT_POST_DELETE, reason=reason, actor=actor,
                           result=TRIED, target_type=cx.TARGET_POST, target_id=post_id,
                           target_key=current["slug"], detail=detail)
        if kuru:
            await self._record(action=ACT_POST_DELETE, reason=reason, actor=actor,
                               result=DRY, target_type=cx.TARGET_POST,
                               target_id=post_id, target_key=current["slug"],
                               detail=detail)
            return {"ok": True, "error": "", "dry_run": True, "warnings": [],
                    "would": {"action": ACT_POST_DELETE, "slug": current["slug"]},
                    "revalidate": cx.revalidate_view(None, requested=False)}

        try:
            result = await self._api.delete_site_post(
                int(post_id), revalidate=fresh, reason=reason, actor=actor,
                dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_POST_DELETE, reason=reason, actor=actor,
                               result=FAILED, target_type=cx.TARGET_POST,
                               target_id=post_id, target_key=current["slug"],
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        audit_id = cx.audit_id_of(result)
        await self._record(action=ACT_POST_DELETE, reason=reason, actor=actor,
                           result=DONE, target_type=cx.TARGET_POST, target_id=post_id,
                           target_key=current["slug"],
                           detail={"slug": current["slug"], "audit_id": audit_id})
        await self._revision(action=ACT_POST_DELETE, target_type=cx.TARGET_POST,
                             target_id=post_id, target_key=current["slug"],
                             title=current["title"], before=current, after=None,
                             actor=actor, reason=reason, audit_id=audit_id)
        return {"ok": True, "error": "", "dry_run": False, "audit_id": audit_id,
                "warnings": cx.warnings_of(result),
                "revalidate": cx.revalidate_view(result, requested=fresh)}

    # ------------------------------------------------------ yeniden çizdirme

    async def revalidate(self, *, paths: Any = None, reason: str, actor: str,
                         dry_run: bool | None = None) -> dict[str, Any]:
        """Siteyi yeniden çizdirir.

        Yazma uçlarındaki `revalidate` bayrağı bunun AYNISINI çağırır; bu uç,
        bayrağı kapatıp art arda birkaç anahtar yazan ve sonunda bir kez
        çizdirmek isteyen yönetici içindir. Bir de: bayrak açıkken çizdirme
        BAŞARISIZ OLDUĞUNDA yeniden denemenin tek yolu budur.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        problem = cx.revalidate_paths_error(paths)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        clean = cx.clean_paths(paths)
        detail = {"paths": clean or "all", "dry_run": kuru}
        await self._record(action=ACT_REVALIDATE, reason=reason, actor=actor,
                           result=TRIED, detail=detail)
        if kuru:
            await self._record(action=ACT_REVALIDATE, reason=reason, actor=actor,
                               result=DRY, detail=detail)
            return {"ok": True, "error": "", "dry_run": True,
                    "revalidate": cx.revalidate_view(None, requested=False),
                    "would": {"action": ACT_REVALIDATE, "paths": clean or "all"}}

        try:
            result = await self._api.revalidate_site(
                paths=clean or None, reason=reason, actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_REVALIDATE, reason=reason, actor=actor,
                               result=FAILED, detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        view = cx.revalidate_view(result, requested=True)
        audit_id = cx.audit_id_of(result)
        # ÇİZDİRME HATASI İSTEĞİ BAŞARISIZ YAPMAZ ama izde `hata` olarak durur:
        # "istendi ve olmadı" ile "hiç istenmedi" aynı satıra yazılırsa,
        # sonradan bakan kişi sitenin neden eski göründüğünü bulamaz.
        await self._record(action=ACT_REVALIDATE, reason=reason, actor=actor,
                           result=FAILED if view["status"] == "failed" else DONE,
                           detail={"paths": clean or "all", "status": view["status"],
                                   "audit_id": audit_id})
        return {"ok": True, "error": "", "dry_run": False, "audit_id": audit_id,
                "data": self._record_of(result, "data"), "revalidate": view,
                "warnings": cx.warnings_of(result)}

    # -------------------------------------------------------- satır içi görsel

    async def upload_image(self, *, content: str, filename: str, reason: str,
                           actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Satır içi görseli yükler ve ADRESİNİ döndürür.

        BUGÜN GEÇİTTE KARŞILIĞI YOK ve bu bir arıza değil: `cms.md` görsel
        yükleme ucu tanımlamıyor. Uç eklendiğinde burada bir değişiklik
        gerekmez (`getattr` ile bakılıyor); eklenene kadar ekran ÇALIŞMAYA
        devam eder ve düzenleyicideki görsel düğmesi HİÇ ÇİZİLMEZ — nedeni
        `screen().image_upload.reason` alanında yazılıdır (K7).
        """
        support = self._image_support()
        if not support["available"]:
            return {"ok": False, "code": "control_endpoint_missing",
                    "error": support["reason"]}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        name = cx.text(filename)
        if not name or "/" in name or "\\" in name:
            return {"ok": False, "error": "Dosya adı boş olamaz ve yol içeremez."}
        if not cx.text(content):
            return {"ok": False, "error": "Görsel içeriği boş."}

        kuru = self._dry(dry_run)
        await self._record(action=ACT_IMAGE, reason=reason, actor=actor, result=TRIED,
                           detail={"filename": name, "dry_run": kuru})
        if kuru:
            await self._record(action=ACT_IMAGE, reason=reason, actor=actor, result=DRY,
                               detail={"filename": name})
            return {"ok": True, "error": "", "dry_run": True, "url": ""}

        upload = getattr(self._api, IMAGE_METHOD)
        try:
            result = await upload(content=content, filename=name, reason=reason,
                                  actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=ACT_IMAGE, reason=reason, actor=actor,
                               result=FAILED, detail={"filename": name,
                                                      "error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        row = self._record_of(result, "data", "image")
        url = cx.safe_url(cx.text(row.get("url")) or cx.text(row.get("path")))
        await self._record(action=ACT_IMAGE, reason=reason, actor=actor,
                           result=DONE if url else FAILED,
                           detail={"filename": name, "url": url})
        if not url:
            return {"ok": False,
                    "error": "Yükleme bir adres döndürmedi; görsel eklenmedi."}
        return {"ok": True, "error": "", "dry_run": False, "url": url}

    # ------------------------------------------------------------- temizleme

    @staticmethod
    def _clean_service(fields: dict[str, Any]) -> dict[str, Any]:
        """Gövdeyi sözleşmedeki alanlara indirger ve HTML'i temizler.

        BİLİNMEYEN ALAN DÜŞÜRÜLÜR. Geçit `fields` sözlüğünü olduğu gibi
        taşıyor (bilerek: sözleşmeye yeni alan eklendiğinde sessizce düşmesin)
        ama panelden gelen gövde bu ekranın kendi formudur ve orada olmayan bir
        anahtar yalnız yazım hatasıyla oluşur. Laravel tanımadığı alanı sessizce
        yok sayar; "kaydedildi" diyen bir ekranın arkasında hiçbir yere
        yazılmamış bir değer bırakmak açık bir hatadan pahalıdır.
        """
        known = ("slug", "title", "summary", "intro", "icon", "body_html",
                 "menu_planning", "sort_order", "is_published", *cx.SERVICE_LIST_FIELDS)
        out: dict[str, Any] = {}
        for key in known:
            if key not in fields:
                continue
            value = fields[key]
            if key == "body_html":
                out[key] = cx.sanitize_html(value)
            elif key in cx.SERVICE_LIST_FIELDS:
                out[key] = [cx.text(item) for item in value] if isinstance(value, list) \
                    else []
            elif key == "sort_order":
                out[key] = cx.as_int(value)
            elif key == "is_published":
                out[key] = bool(value)
            else:
                out[key] = cx.text(value)
        return out

    @staticmethod
    def _clean_post(fields: dict[str, Any]) -> dict[str, Any]:
        """Yazı gövdesini sözleşmedeki alanlara indirger ve HTML'i temizler."""
        known = ("slug", "title", "description", "category", "body_html",
                 "published_at", "reading_minutes", "is_published")
        out: dict[str, Any] = {}
        for key in known:
            if key not in fields:
                continue
            value = fields[key]
            if key == "body_html":
                out[key] = cx.sanitize_html(value)
            elif key == "reading_minutes":
                # BOŞ İLE SIFIR AYRI ŞEYLERDİR: boş "sen hesapla" demektir ve
                # sunucu gövdeden hesaplar; sıfır yazmak "bu yazı okunmuyor"
                # anlamına gelen bir sayı üretirdi.
                out[key] = None if value in (None, "") else cx.as_int(value)
            elif key == "is_published":
                out[key] = bool(value)
            else:
                out[key] = cx.text(value)
        return out
