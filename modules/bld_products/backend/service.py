"""Ürün Yönetimi — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Ürün, kategori, görsel ve tükendi işareti BLD
sunucusunda durur ve buraya `bld.api` geçidinden gelir (K4); bu modül ham httpx
kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel tablolar yalnız BLD'de
KARŞILIĞI OLMAYAN iki şeyi saklar: yazma denemesinin izi ve bu ekrana özel
tercih.

Neden kopya tutulmuyor: fiyat ve tükendi işareti mutfaktan da değişiyor
(`Services\\MenuAvailability` işareti KDS koyuyor). Yerel bir kopya her zaman
bir tur geride olur ve "satışta" görünen bir ürün aslında yarım saattir
tükenmiş olabilir.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISINI DEĞİL UCUN SAĞLIĞINI anlatır: uç çalıştı,
cevabı "bağlanamadım"dır. Ayrımı `connected` taşır ve ekran onu OKUMAK
zorundadır — yalnız `ok`a bakan bir panel geçit düştüğünde "katalog boş" der.

HER YAZMADA `dry_run=` AÇIKÇA GEÇİLİR. Geçidin varsayılanı ayardan gelir ve
`config/local.yaml` git dışıdır; bayrağı atlayan bir çağrı hiçbir şey yazmadan
`{"ok": true}` alabilir ve ekran "kaydedildi" der. Bu modülde `dry_run` hiçbir
çağrıda atlanmaz — değeri `_dry()` üretir ve her zaman gerçek bir `bool`'dur.

ANAHTAR ADLARI. Yanıtlar ve gövdeler sözleşmenin snake_case sözlüğünü korur;
TEK İSTİSNA gövdedeki `dryRun` alanıdır (panel→Kontrol Merkezi sınırında
camelCase, `store_orders` deseni). Çeviri tek yerde yapılır: `dryRun` →
geçidin `dry_run` argümanı.

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe ve gövde denetimi (arayüzde zorunlu göstermek yetmez, K9)
    2. TAZE OKUMA (ürün aradan değişmiş, paket ürüne dönmüş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı (açık `dry_run=` ile)
    5. yerel iz: `ok` / `dry_run` / `hata`

Üçüncü adım kritiktir: "ne yapmaya çalıştık" kaydı, çağrı yarıda kaldığında
tek kanıttır. Fiyat yazılırken ağ koparsa yeni fiyatın geçip geçmediği
bilinmez; iz olmasa kimin denediği de bilinmezdi.

SİLME YOKTUR. Ürün `menu_status = 0` ile satıştan kalkar (`retire_product`),
kategori `status = false` ile gizlenir. `DELETE /categories/{id}` sözleşmede
YOKTUR ve burada da uydurulmaz: kategori silmek altındaki ürünleri kategorisiz
bırakır ve site menüsünü sessizce boşaltır.

Kuru provada OLAY YAYINLANMAZ: BLD'de hiçbir şey değişmedi, dinleyicileri
"ürün satıştan kaldırıldı" diye uyandırmak yalan olurdu.
"""

from __future__ import annotations

import json
from typing import Any

from . import catalog as cat

#: Ürün satıştan kaldırıldığında yayınlanan olay (manifest). Kuru provada
#: YAYINLANMAZ ve başarısız çağrıda da yayınlanmaz.
RETIRED_EVENT = "bld_products.product_retired"

#: Yerel tercih tablosunda tanınan anahtarlar. Listede olmayan anahtar
#: REDDEDİLİR: sessizce yutulan bir tercih, kaydedildiğini sanan kullanıcıya
#: her açılışta eski ekranı gösterirdi.
PREF_KEYS = ("page_size", "status_filter", "sort", "direction")


class ProductsService:
    """Ürün Yönetimi ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 publish: Any = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._publish = publish

        self._audit = store.table("audit")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci bayrağı HİÇ göndermezse geçerli olan varsayılan.

        KAPALI. Yedek değer de `False`: ayar dosyası okunamadığında ekranın
        "kaydedildi" deyip hiçbir şey yazmaması, açık bir hatadan çok daha
        pahalıdır — ürünün fiyatı değişmemiştir ve kimse fark etmez.
        """
        return cat.as_bool(self._config.get("dry_run_default", False))

    @property
    def _page_size(self) -> int:
        return cat.clean_per_page(self._config.get("page_size"), cat.PER_PAGE_DEFAULT)

    @property
    def _audit_limit(self) -> int:
        return max(1, min(500, cat.as_int(self._config.get("audit_limit"), 100)))

    def _dry(self, dry_run: bool | None) -> bool:
        """Kuru prova kararı — SONUCU HER ZAMAN `bool`.

        `None` geçide gönderilmez: geçit `None` gördüğünde kendi ayarına düşer
        ve o ayar `config/local.yaml` ile açılmış olabilir. Bayrağın burada
        gerçek bir değere indirgenmesi, "kaydedildi" ile "sessizce atıldı"
        arasındaki farkın tek garantisidir.
        """
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------ yardımcılar

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçit hata kodu (`BldApiError.code`). Bilinmiyorsa boş dize.

        Ekran HTTP durumuna değil bu koda bakar (`00-genel.md` §7): `conflict`
        "tazele ve tekrar sor", `control_endpoint_missing` "uç henüz sunucuda
        yayında değil, bekle" demektir ve ikisi farklı cümleler ister.
        """
        return str(getattr(failure, "code", "") or "")

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit zarfı ZATEN AÇIYOR (`BldApi._list` → `{"items", "meta"}`); bu
        yüzden buradaki iş normalde tek satırlık. `data` ve düz dizi de kabul
        edilir çünkü geçidin bir metodu zarfı açmadan geçebilir ve tek bir ada
        bağlanmak, ad tutmadığında ekranı SESSİZCE boş gösterirdi.
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
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Tekil yanıttan kaydı çıkarır (`{"data": {...}}` ya da düz sözlük)."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _meta_of(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return payload["meta"]
        return {}

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_type: str = "menu", target_id: int = 0,
                      detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor
        (`00-genel.md` §8); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN istekleri bilir. Ağ
        koparsa, geçit patlarsa ya da imza reddedilirse (doğrulama denetleyici
        çalışmadan önce yapılıyor) "kim neyi denedi" sorusunun cevabı yalnız
        burada kalır.

        GÖRSEL İÇERİĞİ YAZILMAZ — yalnız künye (`{"mime", "bytes"}`,
        `00-genel.md` §8.2). Denetim tablosunu megabaytlık base64 dizeleriyle
        doldurmak izi okunamaz ve tabloyu yönetilemez kılardı.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, int(target_id or 0), action, cat.text(reason), actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), cat.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: ürün BLD'de satıştan
        kaldırılmıştır, dinleyicinin patlaması onu geri getirmez (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    async def _fresh_product(self, menu_id: int) -> tuple[dict[str, Any] | None, str]:
        """Ürünün TAZE hâli. `(satır, hata)` döner.

        Yazmadan hemen önce okunur çünkü aradan değişmiş olabilir: başka bir
        yönetici satıştan kaldırmış, mutfak tükendi işaretlemiş ya da ürün
        günün menüsünün paket kalemi hâline gelmiş olabilir. Sonuncusu
        kritiktir — paket ürününe fiyat yazmak sunucuda `422` verir ve
        kullanıcı sebebini ancak burada okuduğumuzda anlar.
        """
        try:
            payload = await self._api.product(int(menu_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return None, self._fail(failure)
        row = self._record_of(payload, "data", "product")
        if not row:
            return None, "Ürün bulunamadı."
        return cat.product_row(row), ""

    def _blocked(self, message: str) -> dict[str, Any]:
        return {"ok": False, "blocked": True, "error": message}

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Panel açılışı: katalog sayaçları + kategori özeti + süzgeç sözleşmesi.

        ÜÇ SAYAÇ, ÜÇ AYRI SORGU AMA BİRER SATIR: sayaçlar `per_page=1` ile
        alınır ve yalnız `meta.total` okunur. Tam listeyi çekip saymak, 80
        ürünlük bir katalogda dört sayfa indirmek demekti ve sayı yine
        sunucudakiyle aynı çıkardı.

        BU EKRAN YOKLANMAZ. Ürün kataloğu haftalarca değişmez; `00-genel.md`
        §2'deki yoklama bütçesi tablosunda ürün ekranı YOKTUR ve buraya bir
        `pollLoop` koymak, paylaşılan 3000/saat kovasını hiç değişmeyen veri
        için yakardı.
        """
        spec = {"filters": cat.filter_spec()}
        counts = {"total": 0, "inactive": 0, "sold_out": 0, "categories": 0,
                  "categories_hidden": 0, "no_image": 0}
        try:
            total = await self._api.products(status="all", page=1, per_page=1)
            inactive = await self._api.products(status="inactive", page=1, per_page=1)
            sold_out = await self._api.products(sold_out=True, page=1, per_page=1)
            categories = await self._api.categories()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("katalog özeti okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "counts": counts, **spec}

        rows = [cat.category_row(item) for item in self._rows(categories)]
        counts["total"] = cat.as_int(self._meta_of(total).get("total"))
        counts["inactive"] = cat.as_int(self._meta_of(inactive).get("total"))
        counts["sold_out"] = cat.as_int(self._meta_of(sold_out).get("total"))
        counts["categories"] = len(rows)
        counts["categories_hidden"] = sum(1 for row in rows if not row["status"])
        # `no_image` SUNUCUDAN GELMEZ ve tahmin edilmez: görselsiz ürün sayısı
        # ancak tam liste taranarak bulunur ve o tarama bu ekranın bütçesini
        # aşar. -1 "bilinmiyor" demektir; panel kutuyu çizmez, sıfır YAZMAZ.
        counts["no_image"] = -1
        return {"ok": True, "connected": True, "error": "", "code": "",
                "counts": counts, **spec}

    async def products(self, *, q: str = "", category_id: int = 0, status: str = "",
                       sold_out: bool | None = None, sort: str = "", direction: str = "",
                       page: int = 1, per_page: int = 0) -> dict[str, Any]:
        """Ürün listesi — SUNUCU TARAFINDA SAYFALANIR.

        Tam listeyi çekip istemcide süzmek bilinçli olarak yapılmaz: sayfalama
        sözleşmenin kendi biçimidir (`00-genel.md` §5) ve arama sunucuda ad ve
        açıklamada birlikte çalışıyor.

        `q` EN AZ İKİ KARAKTER (sözleşme). Tek karakterlik arama isteğe
        KONMAZ; gönderilse sunucu `422` verirdi ve kullanıcı yazmaya devam
        ederken hata görürdü.
        """
        size = cat.clean_per_page(per_page, self._page_size)
        number = max(1, cat.as_int(page, 1))
        query = cat.text(q)
        used = {
            "q": query if len(query) >= 2 else "",
            "category_id": max(0, cat.as_int(category_id)),
            "status": cat.clean_status(status),
            "sold_out": sold_out,
            "sort": cat.clean_sort(sort),
            "direction": cat.clean_direction(direction),
            "page": number,
            "per_page": size,
        }
        spec = {"filters": cat.filter_spec(), "used": used}
        try:
            payload = await self._api.products(
                q=used["q"], category_id=used["category_id"] or None,
                status=used["status"], sold_out=sold_out, sort=used["sort"],
                direction=used["direction"], page=number, per_page=size,
            )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("ürün listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": [],
                    "meta": {"page": number, "per_page": size, "total": 0, "last_page": 1},
                    **spec}
        rows = [cat.product_row(item) for item in self._rows(payload)]
        return {"ok": True, "connected": True, "error": "", "code": "", "items": rows,
                "meta": cat.page_meta(self._meta_of(payload), page=number, per_page=size,
                                      rows=len(rows)),
                **spec}

    async def product(self, menu_id: int) -> dict[str, Any]:
        """Tek ürün, SEÇENEKLERİYLE.

        `options` yalnız burada doludur; listede boş dizi döner (sözleşme:
        "seksen ürünün seçeneklerini her sayfada taşımak, ekranın göstermediği
        veriyi yollamak olurdu"). Seçenekler SALT OKUNURDUR — düzenleme
        TastyIgniter admin panelindedir ve bu tur için sözleşmede yazma ucu
        yok. Panel bunu yazar, düğme çizmez.
        """
        row, error = await self._fresh_product(menu_id)
        if row is None:
            return {"ok": False, "connected": False, "error": error, "product": {},
                    "options_read_only": True}
        return {"ok": True, "connected": True, "error": "", "product": row,
                "options_read_only": True}

    async def categories(self) -> dict[str, Any]:
        """Kategori ağacı. Sayfalanmaz (sözleşme) — ekran hepsini birden çizer.

        Geçit bu listeyi ÖNBELLEĞE ALIYOR ve kategori yazan her metot dalı
        düşürüyor; yeni kategori ilk okumada görünür, TTL beklenmez.
        """
        try:
            payload = await self._api.categories()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("kategori listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": []}
        rows = [cat.category_row(item) for item in self._rows(payload)]
        return {"ok": True, "connected": True, "error": "", "code": "",
                "items": cat.category_tree(rows)}

    async def audit_trail(self, *, limit: int = 0) -> dict[str, Any]:
        """Bu ekrandan yapılan yazma DENEMELERİ — yerel tablo, ağa çıkmaz.

        `result` sütunu "denendi"de kalmış bir satır, isteğin gidip gitmediği
        BİLİNMEYEN bir denemedir; sunucunun kendi defteri o satırı hiç bilmez.
        Panel bunu ayrı bir tonla gösterir.
        """
        count = max(1, min(500, cat.as_int(limit, 0) or self._audit_limit))
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_type, target_id, action, reason, actor, result, "
                f"detail, created_at FROM {self._audit} ORDER BY id DESC LIMIT ?",
                (count,),
            )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yerel iz okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        items = []
        for row in rows:
            data = dict(row)
            try:
                data["detail"] = json.loads(data.get("detail") or "{}")
            except (TypeError, ValueError):
                data["detail"] = {}
            items.append(data)
        return {"ok": True, "connected": True, "error": "", "items": items}

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler BLD'Yİ ETKİLEMEZ; yalnız bu ekranın açılışta ne gösterdiğini
        belirler. Bu yüzden yazmaları gerekçe istemez ve `view` izniyle
        yapılır — uzak sistemde hiçbir şey değişmiyor.
        """
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))
        return {
            "ok": True,
            "page_size": cat.clean_per_page(stored.get("page_size"), self._page_size),
            "status_filter": cat.clean_status(stored.get("status_filter")),
            "sort": cat.clean_sort(stored.get("sort")),
            "direction": cat.clean_direction(stored.get("direction")),
        }

    async def save_prefs(self, values: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Ekran tercihini yazar. TANINMAYAN ANAHTAR REDDEDİLİR.

        Sessizce yutulan bir tercih, kaydettiğini sanan kullanıcıya her
        açılışta eski ekranı gösterirdi — geçidin tanınmayan ayar anahtarını
        reddetmesiyle aynı gerekçe.
        """
        unknown = [key for key in values if key not in PREF_KEYS]
        if unknown:
            return self._blocked(
                f"Tanınmayan tercih: {', '.join(sorted(unknown))}. "
                f"Yazılabilenler: {', '.join(PREF_KEYS)}.")
        cleaners = {
            "page_size": lambda value: str(cat.clean_per_page(value, self._page_size)),
            "status_filter": lambda value: cat.clean_status(value),
            "sort": lambda value: cat.clean_sort(value),
            "direction": lambda value: cat.clean_direction(value),
        }
        try:
            for key, raw in values.items():
                await self._store.execute(
                    f"INSERT INTO {self._prefs} (key, value, actor, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, actor = excluded.actor, "
                    "updated_at = excluded.updated_at",
                    (key, cleaners[key](raw), actor, cat.now_iso()),
                )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("tercih yazılamadı", error=str(failure))
            return {"ok": False, "error": self._fail(failure)}
        return await self.prefs()

    # ================================================================ yazma

    async def create_product(self, *, name: str, price_kurus: int,
                             description: str | None = None, minimum_qty: int = 1,
                             priority: int = 0, status: bool = True,
                             category_ids: list[int] | None = None, reason: str, actor: str,
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni ürün.

        `price_kurus` KURUŞTUR ve SIFIR GEÇERLİDİR: paket bileşeni olarak
        satılan ekmek ve ayran sıfır fiyatlıdır. Negatif tutar reddedilir.

        `category_ids` boş olabilir — kategorisiz ürün sitede görünmez ama
        günlük menüde kullanılabilir. Panel bunu uyarı olarak yazar; burada
        engellenmez, çünkü menü kalemi olarak eklenecek ürün için doğru hâl
        budur.
        """
        guard = cat.reason_error(reason) or cat.name_error(name)
        if guard:
            return self._blocked(guard)
        price = cat.as_int(price_kurus, -1)
        if price < 0:
            return self._blocked("Fiyat kuruş cinsinden ve sıfır ya da daha büyük olmalı.")
        quantity = cat.as_int(minimum_qty, 1)
        if quantity < 0:
            return self._blocked("En az adet negatif olamaz.")

        ids = cat.category_ids(category_ids)
        dry = self._dry(dry_run)
        detail = {"name": cat.text(name), "price_kurus": price, "category_ids": ids,
                  "status": bool(status)}
        await self._record(action="product.create", reason=reason, actor=actor,
                           result=cat.TRIED, detail=detail)
        try:
            payload = await self._api.create_product(
                name=cat.text(name), price_kurus=price,
                description=cat.optional_text(description), minimum_qty=quantity,
                priority=cat.as_int(priority), status=bool(status), category_ids=ids,
                reason=reason, actor=actor, dry_run=dry,
            )
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="product.create", reason=reason, actor=actor,
                               result=cat.FAILED, detail={**detail, "error": str(failure)})
            self._log.warning("ürün açılamadı", error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("product.create", payload, reason=reason, actor=actor,
                                dry=dry, detail=detail)

    async def update_product(self, menu_id: int, *, fields: dict[str, Any], reason: str,
                             actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Ürünü günceller — KISMİ. Yalnız gönderilen alanlar değişir.

        `fields` GÖVDEDE YUVALIDIR: kökte olsalardı `reason` ve `dryRun` ile
        aynı ad alanını paylaşırlardı ve gönderilmeyen alan ile `null` yazılan
        alan ayırt edilemezdi. Anahtarın BULUNMASI "yaz", `null` "boşalt"
        demektir.

        PAKET ÜRÜNÜNE FİYAT YAZILMAZ. `is_package_product: true` olan ürünün
        gerçek fiyatı o günün paket fiyatıdır; buraya bir tutar yazmak günün
        menüsünü yanlış tutara satardı. Sunucu da `422` (`package_product`)
        veriyor — buradaki kapı yalnız sebebi anlaşılır bir cümleyle söyler ve
        isteği hiç göndermez.
        """
        guard = cat.reason_error(reason)
        if guard:
            return self._blocked(guard)
        body, error = self._product_patch(fields)
        if error:
            return self._blocked(error)

        # SATIŞTAN KALDIRMA BU UÇTAN YAPILAMAZ. `PATCH status: false` ile
        # `DELETE /{menu}` sunucuda aynı sonucu üretir (`menu_status = 0`);
        # ikisine farklı izin verip birini serbest bırakmak, `retire` iznini
        # süs hâline getirirdi. Yeniden AÇMAK (`status: true`) serbesttir:
        # ürünü satışa döndürmek yıkıcı değildir ve sözleşmede ayrı bir
        # "restore" ucu yok.
        if body.get("status") is False:
            return self._blocked(
                "Ürünü buradan satıştan kaldıramazsınız: bu işlem ayrı bir yetki "
                "ister. Ürün çekmecesindeki 'Satıştan kaldır' düğmesini kullanın.")

        current, read_error = await self._fresh_product(menu_id)
        if current is None:
            return {"ok": False, "error": read_error}
        if current["price_locked"] and "price_kurus" in body:
            return self._blocked(
                f"'{current['name']}' günün menüsünün paket ürünü: fiyatı o günün "
                "menüsünde tanımlıdır ve buradan yazılamaz. Tutarı değiştirmek için "
                "Günlük Menü ekranındaki paket fiyatını düzenleyin.")

        dry = self._dry(dry_run)
        detail = {"menu_id": int(menu_id), "name": current["name"],
                  "before": {key: current.get(key) for key in body},
                  "after": dict(body)}
        await self._record(action="product.update", reason=reason, actor=actor,
                           result=cat.TRIED, target_id=menu_id, detail=detail)
        try:
            payload = await self._api.update_product(int(menu_id), reason=reason,
                                                     actor=actor, dry_run=dry, **body)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="product.update", reason=reason, actor=actor,
                               result=cat.FAILED, target_id=menu_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("ürün güncellenemedi", menuId=menu_id, error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("product.update", payload, reason=reason, actor=actor,
                                dry=dry, target_id=menu_id, detail=detail)

    def _product_patch(self, fields: Any) -> tuple[dict[str, Any], str]:
        """Kısmi gövdeyi doğrular. `(gövde, hata)` döner.

        TANINMAYAN ANAHTAR REDDEDİLİR — geçidin tanınmayan ayar anahtarını
        reddetmesiyle aynı gerekçe: Laravel bilmediği alanı sessizce yok sayar
        ve "kaydedildi" diyen ekranın arkasında hiçbir yere yazılmamış bir
        değer kalırdı.
        """
        if not isinstance(fields, dict) or not fields:
            return {}, ("En az bir alan gönderilmeli: gönderilmeyen alan değişmez, "
                        "bu yüzden boş bir güncelleme hiçbir şey yapmaz.")
        unknown = [key for key in fields if key not in cat.PRODUCT_PATCH_FIELDS]
        if unknown:
            return {}, (f"Tanınmayan alan: {', '.join(sorted(unknown))}. "
                        f"Yazılabilenler: {', '.join(cat.PRODUCT_PATCH_FIELDS)}.")

        body: dict[str, Any] = {}
        if "name" in fields:
            error = cat.name_error(fields["name"])
            if error:
                return {}, error
            body["name"] = cat.text(fields["name"])
        if "description" in fields:
            body["description"] = cat.optional_text(fields["description"])
        # SAYISAL ALANLARDA `None` BOŞ ALANDIR, SIFIR DEĞİL. Panelde temizlenen
        # bir kutu `null` gönderiyor; onu `as_int` ile 0'a çevirmek, kullanıcının
        # silmek istediği değeri sessizce "sıfır" yapardı — fiyatta bu, ürünü
        # bedava satmak demek.
        for key in ("price_kurus", "minimum_qty", "priority"):
            if key in fields and fields[key] is None:
                return {}, ("Sayı alanı boş bırakılamaz; değeri değiştirmek "
                            "istemiyorsanız alanı olduğu gibi bırakın.")
        if "price_kurus" in fields:
            price = cat.as_int(fields["price_kurus"], -1)
            if price < 0:
                return {}, "Fiyat kuruş cinsinden ve sıfır ya da daha büyük olmalı."
            body["price_kurus"] = price
        if "minimum_qty" in fields:
            quantity = cat.as_int(fields["minimum_qty"], -1)
            if quantity < 0:
                return {}, "En az adet negatif olamaz."
            body["minimum_qty"] = quantity
        if "priority" in fields:
            body["priority"] = cat.as_int(fields["priority"])
        if "status" in fields:
            body["status"] = cat.as_bool(fields["status"])
        if "category_ids" in fields:
            if not isinstance(fields["category_ids"], list | tuple):
                return {}, "Kategori listesi bir dizi olmalı."
            # TAM LİSTEDİR: pivot tablo bu listeye eşitlenir. Boş dizi geçerli
            # ve anlamlıdır — ürünü bütün kategorilerden çıkarır.
            body["category_ids"] = cat.category_ids(fields["category_ids"])
        return body, ""

    async def retire_product(self, menu_id: int, *, reason: str, actor: str,
                             dry_run: bool | None = None,
                             allow_destructive: bool = False) -> dict[str, Any]:
        """Ürünü SATIŞTAN KALDIRIR (`menu_status = 0`). Satır SİLİNMEZ.

        İzin BURADA DA denetlenir (K9 — çift kapı): uç noktadaki `requires`
        kapısı ilk kapıdır, bu ikincisidir. Arayüzde düğmeyi gizlemek
        yetkilendirme değildir ve uç noktanın izni bir gün gevşetilirse
        buradaki kapı hâlâ durur.

        Ürün YAYINLANMIŞ bir günlük menüde kullanılıyorsa sunucu `409` verir
        ve hangi günler olduğunu söyler; yayındaki bir menünün kalemini
        sessizce satıştan kaldırmak o menüyü sepete eklenemez hâle getirirdi.

        Geri açmak ayrı bir uç değildir: `update_product(status=True)`.
        """
        if not allow_destructive:
            await self._record(action="product.delete", reason=reason, actor=actor,
                               result=cat.BLOCKED, target_id=menu_id,
                               detail={"menu_id": int(menu_id)})
            return self._blocked("Ürünü satıştan kaldırmak için `bld_products.retire` "
                                 "izni gerekiyor.")
        guard = cat.reason_error(reason)
        if guard:
            return self._blocked(guard)

        current, read_error = await self._fresh_product(menu_id)
        if current is None:
            return {"ok": False, "error": read_error}
        if not current["status"]:
            # Sonuç odaklı DEĞİL, bilerek: sunucuya gitmeyi engellemek yerine
            # kullanıcıya zaten kaldırılmış olduğunu söylemek, "kaldırdım ama
            # listede duruyor" yanılgısını önler. İstek gönderilmez, denetim
            # izine de gereksiz bir satır düşmez.
            return self._blocked(
                f"'{current['name']}' zaten satıştan kaldırılmış. Yeniden satışa açmak "
                "için ürünü düzenleyip durumunu 'Satışta' yapın.")

        dry = self._dry(dry_run)
        detail = {"menu_id": int(menu_id), "name": current["name"]}
        await self._record(action="product.delete", reason=reason, actor=actor,
                           result=cat.TRIED, target_id=menu_id, detail=detail)
        try:
            payload = await self._api.delete_product(int(menu_id), reason=reason,
                                                     actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="product.delete", reason=reason, actor=actor,
                               result=cat.FAILED, target_id=menu_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("ürün satıştan kaldırılamadı", menuId=menu_id,
                              error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}

        result = await self._done("product.delete", payload, reason=reason, actor=actor,
                                  dry=dry, target_id=menu_id, detail=detail)
        # KURU PROVADA YAYINLANMAZ: BLD'de hiçbir şey değişmedi ve dinleyicileri
        # "ürün satıştan kaldırıldı" diye uyandırmak yalan olurdu.
        if result["ok"] and not result["dry_run"]:
            await self._announce(RETIRED_EVENT, {
                "menuId": int(menu_id), "name": current["name"],
                "reason": cat.text(reason), "actor": actor,
            })
        return result

    async def set_image(self, menu_id: int, *, content: Any, filename: str, reason: str,
                        actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Ürün görseli yükler — BASE64, JSON GÖVDE.

        Multipart KULLANILMAZ: imza kanonik dizesi ham gövdeyi hashliyor ve
        gövdeyi yeniden kodlayan herhangi bir vekil (proxy, gzip, WAF) baytı
        değiştirip imzayı bozardı; arıza sahada "sır yanlış" ya da "saat
        kaymış" gibi görünür. Gerekçe `products.md` ve geçidin `upload.py`
        başlığındadır.

        DOSYAYI BURADA ÇÖZMEYİZ. Base64 denetimi, boyut ve içerikten MIME
        okuma geçidin işidir (`prepare_upload`) ve sınırı aşan dosya için
        istek HİÇ GÖNDERİLMEZ. İkinci bir çözücü yazmak, iki tarafın farklı
        hata vermesi demekti.
        """
        guard = cat.reason_error(reason)
        if guard:
            return self._blocked(guard)
        if not content:
            return self._blocked("Yüklenecek görsel seçilmedi.")

        current, read_error = await self._fresh_product(menu_id)
        if current is None:
            return {"ok": False, "error": read_error}

        dry = self._dry(dry_run)
        # DENETİM İZİNE İÇERİK YAZILMAZ (`00-genel.md` §8.2) — yalnız ad.
        detail = {"menu_id": int(menu_id), "name": current["name"],
                  "filename": cat.text(filename)}
        await self._record(action="product.image", reason=reason, actor=actor,
                           result=cat.TRIED, target_id=menu_id, detail=detail)
        try:
            payload = await self._api.set_product_image(
                int(menu_id), content=content, filename=cat.text(filename),
                reason=reason, actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="product.image", reason=reason, actor=actor,
                               result=cat.FAILED, target_id=menu_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("görsel yüklenemedi", menuId=menu_id, error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        # Geçit yüklenen dosyanın künyesini `upload` altında geri veriyor
        # (`{filename, mime, bytes}`); denetim izine giden de odur.
        stamp = payload.get("upload") if isinstance(payload, dict) else None
        return await self._done("product.image", payload, reason=reason, actor=actor,
                                dry=dry, target_id=menu_id,
                                detail={**detail, **(stamp if isinstance(stamp, dict) else {})})

    async def clear_image(self, menu_id: int, *, reason: str, actor: str,
                          dry_run: bool | None = None) -> dict[str, Any]:
        """Ürün görselini kaldırır.

        Görseli olmayan üründen görsel silmek HATA DEĞİLDİR (sözleşme):
        işlem sonuç odaklıdır, istenen son hâl zaten geçerli. Bu yüzden taze
        okumada `has_image` yanlış çıksa bile istek gönderilir — ekranın
        gördüğü hâl bayat olabilir.
        """
        guard = cat.reason_error(reason)
        if guard:
            return self._blocked(guard)
        dry = self._dry(dry_run)
        detail = {"menu_id": int(menu_id)}
        await self._record(action="product.image.delete", reason=reason, actor=actor,
                           result=cat.TRIED, target_id=menu_id, detail=detail)
        try:
            payload = await self._api.delete_product_image(int(menu_id), reason=reason,
                                                           actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="product.image.delete", reason=reason, actor=actor,
                               result=cat.FAILED, target_id=menu_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("görsel kaldırılamadı", menuId=menu_id, error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("product.image.delete", payload, reason=reason, actor=actor,
                                dry=dry, target_id=menu_id, detail=detail)

    async def mark_sold_out(self, menu_id: int, *, note: str = "", reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Bugün için tükendi işareti.

        BUGÜNE ÖZELDİR ve ertesi gün kendiliğinden düşer (`sold_out_on` tarih
        bazlı). Kalıcı satıştan kaldırma `retire_product`'tır, bu değil —
        panel iki düğmeyi ayrı yerlerde ve ayrı cümlelerle gösterir.

        `reason` `veykemtu_menu_soldout.reason` sütununa DA yazılır: mutfak
        ekranındaki "neden yok" sorusunun cevabı orada görünür. Bu yüzden
        gerekçe kutusunun ipucu "mutfakta da görünecek" der.

        Zaten işaretliyse sunucu `ok: true` döner ve gerekçeyi GÜNCELLER;
        `409` verilmez. İkinci bir gerekçe yazmak isteyen yöneticiyi hata
        ekranına düşürmek anlamsız olurdu.
        """
        guard = cat.reason_error(reason)
        if guard:
            return self._blocked(guard)
        dry = self._dry(dry_run)
        detail = {"menu_id": int(menu_id), "note": cat.text(note)}
        await self._record(action="product.sold_out", reason=reason, actor=actor,
                           result=cat.TRIED, target_id=menu_id, detail=detail)
        try:
            payload = await self._api.mark_product_sold_out(
                int(menu_id), note=cat.optional_text(note), reason=reason, actor=actor,
                dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="product.sold_out", reason=reason, actor=actor,
                               result=cat.FAILED, target_id=menu_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("tükendi işareti konamadı", menuId=menu_id, error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("product.sold_out", payload, reason=reason, actor=actor,
                                dry=dry, target_id=menu_id, detail=detail)

    async def clear_sold_out(self, menu_id: int, *, reason: str, actor: str,
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Tükendi işaretini kaldırır. İşaret yoksa sunucu yine `ok: true` der."""
        guard = cat.reason_error(reason)
        if guard:
            return self._blocked(guard)
        dry = self._dry(dry_run)
        detail = {"menu_id": int(menu_id)}
        await self._record(action="product.sold_out.clear", reason=reason, actor=actor,
                           result=cat.TRIED, target_id=menu_id, detail=detail)
        try:
            payload = await self._api.clear_product_sold_out(int(menu_id), reason=reason,
                                                             actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="product.sold_out.clear", reason=reason, actor=actor,
                               result=cat.FAILED, target_id=menu_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("tükendi işareti kaldırılamadı", menuId=menu_id,
                              error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("product.sold_out.clear", payload, reason=reason, actor=actor,
                                dry=dry, target_id=menu_id, detail=detail)

    # -------------------------------------------------------- kategoriler

    async def create_category(self, *, name: str, description: str | None = None,
                              parent_id: int | None = None, priority: int = 0,
                              status: bool = True, reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni kategori.

        `slug` GÖNDERİLMEZ: `permalink_slug` çekirdeğin `HasPermalink`
        özelliğiyle addan üretilir. Elle slug yazdırmak, sitedeki adresin
        yönetici yazım hatasına bağlı olması demekti.
        """
        guard = cat.reason_error(reason) or cat.name_error(name)
        if guard:
            return self._blocked(guard)
        parent = cat.as_int(parent_id, 0) or None
        dry = self._dry(dry_run)
        detail = {"name": cat.text(name), "parent_id": parent, "status": bool(status)}
        await self._record(action="category.create", reason=reason, actor=actor,
                           result=cat.TRIED, target_type="category", detail=detail)
        try:
            payload = await self._api.create_category(
                name=cat.text(name), description=cat.optional_text(description),
                parent_id=parent, priority=cat.as_int(priority), status=bool(status),
                reason=reason, actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="category.create", reason=reason, actor=actor,
                               result=cat.FAILED, target_type="category",
                               detail={**detail, "error": str(failure)})
            self._log.warning("kategori açılamadı", error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("category.create", payload, reason=reason, actor=actor,
                                dry=dry, target_type="category", detail=detail)

    async def update_category(self, category_id: int, *, fields: dict[str, Any], reason: str,
                              actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Kategoriyi günceller — KISMİ.

        `DELETE /categories/{id}` SÖZLEŞMEDE YOKTUR ve burada da uydurulmaz:
        kategori silmek altındaki ürünleri kategorisiz bırakır ve site
        menüsünü sessizce boşaltır. Gizlemek `status: false` yazmaktır.

        DÖNGÜ ÖNCEDEN YAKALANIR: `parent_id` kendisine ya da kendi alt ağacına
        işaret ediyorsa istek GÖNDERİLMEZ. Sunucu da `422` (`cycle`) veriyor;
        buradaki kapı aynı cevabı ağa çıkmadan verir ve çekirdek `NestedTree`
        bozuk bir ağaçla hiç karşılaşmaz.
        """
        guard = cat.reason_error(reason)
        if guard:
            return self._blocked(guard)
        body, error = self._category_patch(fields)
        if error:
            return self._blocked(error)

        if "parent_id" in body and body["parent_id"] is not None:
            rows, read_error = await self._category_rows()
            if read_error:
                return {"ok": False, "error": read_error}
            if cat.would_cycle(rows, int(category_id), body["parent_id"]):
                return self._blocked(
                    "Bir kategori kendisinin ya da kendi alt kategorisinin altına "
                    "taşınamaz; ağaç kapanır ve site menüsü çizilemez hâle gelir.")

        dry = self._dry(dry_run)
        detail = {"category_id": int(category_id), "after": dict(body)}
        await self._record(action="category.update", reason=reason, actor=actor,
                           result=cat.TRIED, target_type="category", target_id=category_id,
                           detail=detail)
        try:
            payload = await self._api.update_category(int(category_id), reason=reason,
                                                      actor=actor, dry_run=dry, **body)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="category.update", reason=reason, actor=actor,
                               result=cat.FAILED, target_type="category",
                               target_id=category_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("kategori güncellenemedi", categoryId=category_id,
                              error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("category.update", payload, reason=reason, actor=actor,
                                dry=dry, target_type="category", target_id=category_id,
                                detail=detail)

    async def _category_rows(self) -> tuple[list[dict[str, Any]], str]:
        """Kategori satırları — döngü denetimi için. `(satırlar, hata)`.

        Geçit bu listeyi önbelleğe alıyor, yani döngü denetimi çoğu zaman ağa
        hiç çıkmaz.
        """
        try:
            payload = await self._api.categories()
        except Exception as failure:  # noqa: BLE001 — K7
            return [], self._fail(failure)
        return [cat.category_row(item) for item in self._rows(payload)], ""

    def _category_patch(self, fields: Any) -> tuple[dict[str, Any], str]:
        """Kategori kısmi gövdesini doğrular. `(gövde, hata)` döner."""
        if not isinstance(fields, dict) or not fields:
            return {}, ("En az bir alan gönderilmeli: gönderilmeyen alan değişmez, "
                        "bu yüzden boş bir güncelleme hiçbir şey yapmaz.")
        unknown = [key for key in fields if key not in cat.CATEGORY_PATCH_FIELDS]
        if unknown:
            return {}, (f"Tanınmayan alan: {', '.join(sorted(unknown))}. "
                        f"Yazılabilenler: {', '.join(cat.CATEGORY_PATCH_FIELDS)}.")

        body: dict[str, Any] = {}
        if "name" in fields:
            error = cat.name_error(fields["name"])
            if error:
                return {}, error
            body["name"] = cat.text(fields["name"])
        if "description" in fields:
            body["description"] = cat.optional_text(fields["description"])
        if "parent_id" in fields:
            # `null` GERÇEK BİR DEĞERDİR: kategoriyi kök seviyeye taşır.
            # Düşürmek, bir alt kategoriyi kökten ayırmayı imkânsız kılardı.
            body["parent_id"] = cat.as_int(fields["parent_id"], 0) or None
        if "priority" in fields:
            if fields["priority"] is None:
                return {}, "Sıra numarası boş bırakılamaz."
            body["priority"] = cat.as_int(fields["priority"])
        if "status" in fields:
            body["status"] = cat.as_bool(fields["status"])
        return body, ""

    # ------------------------------------------------------------ ortak son

    async def _done(self, action: str, payload: Any, *, reason: str, actor: str, dry: bool,
                    detail: dict[str, Any], target_type: str = "menu",
                    target_id: int = 0) -> dict[str, Any]:
        """Başarılı geçit çağrısının ortak sonu: iz + yanıt zarfı.

        `dry_run` YANITTAN OKUNUR, isteğe yazdığımızdan değil: bir kurulum
        provayı geçidin ayarından açarsa ekran "yapıldı" DEMEMELİ. Sunucu
        kuru provada `would` bloğu döndürüyor ve panel onu gösterir.
        """
        body = payload if isinstance(payload, dict) else {}
        applied_dry = cat.as_bool(body.get("dry_run")) if "dry_run" in body else dry
        if not target_id:
            # AÇMA İŞLEMİNDE KİMLİK ANCAK YANITTA BELLİDİR. İz `denendi`
            # satırını 0 ile yazdı (o an kimlik yoktu); sonuç satırı gerçek
            # kimliği taşır, yoksa yeni ürünün izi hiçbir kayda bağlanamazdı.
            fresh = body.get("data") if isinstance(body.get("data"), dict) else {}
            target_id = cat.as_int(fresh.get("menu_id") or fresh.get("category_id"), 0)
        await self._record(action=action, reason=reason, actor=actor,
                           result=cat.DRY if applied_dry else cat.DONE,
                           target_type=target_type, target_id=target_id,
                           detail={**detail, "audit_id": body.get("audit_id")})
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        would = body.get("would") if isinstance(body.get("would"), dict) else {}
        return {"ok": True, "error": "", "code": "", "dry_run": applied_dry,
                "audit_id": body.get("audit_id"), "data": data, "would": would}
