"""Elle Sipariş — iş kuralları.

VERİ BLD'DEDİR, KARAR SUNUCUDADIR, EKRAN BURADADIR. Sipariş, müşteri, ürün,
satış ayarı ve stok BLD'de durur ve buraya `bld.api` geçidinden gelir (K4); bu
modül ham `httpx` kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Kendi tablosu
yoktur (`module.yaml` başlığı).

SİPARİŞ PENCERESİ BURADA YENİDEN UYGULANMAZ. Uç `OrderFactory::create()`'i
`adminContext: true` ile çağırıyor ve kesim saatini, ileri görüş sınırını,
sipariş alım şalterini, asgari sepet tutarını ve menü üyeliğini BİLEREK
atlıyor: personel müşteriyle telefonda anlaşmış, istisnayı insan vermiştir. Bu
bir onay akışı değil, KAYIT akışıdır. Ekranın işi kararı ikinci kez sorgulamak
değil, atlanan iki şeyi (kesim ve stok tavanı) GÖRÜNÜR kılmak.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır:
ayrımı `connected` taşır ve panelin onu OKUMASI gerekir — yalnız `ok`a bakan
bir ekran, geçit düştüğünde "ürün yok" der ve K7'nin engellemek için var
olduğu yalanı söyler.

HER YAZMADA AÇIK `dry_run=`. Geçidin varsayılanına GÜVENİLMEZ: `config/
local.yaml` git dışıdır ve orada bugün `dry_run_default: true` yazıyor;
bayrağı atlayan bir çağrı hiçbir şey yazmadan `{"ok": true}` alır ve ekran
"sipariş açıldı" der (`bld_api/README.md`). Personel telefonu kapatır, müşteri
aç kalır.

DENETİM İZİ GEÇİTTEDİR. `bld_api` her yazmada, istek ÇIKMADAN ÖNCE
`mod_bld_api_audit` satırını açıyor; ağ koparsa "ne yapmaya çalıştık" kaydı
yerelde kalır. Bu modül ikinci bir defter tutmaz — iki defterin biri eksik
kalırsa hangisinin doğru olduğu sorusu cevapsızdır.
"""

from __future__ import annotations

from typing import Any

from . import draft as dr

#: Geçidin sipariş AÇMA metodunun adı — `bld_api/README.md` §5 donmuş
#: tablosundan: `create_order(*, service_date, delivery_type, payment_method,
#: items, customer_id, customer, address, customer_note, location_id)`
#: (+ `reason`, `actor`, `dry_run`).
#:
#: AD `getattr` İLE ARANIR VE BULUNAMAZSA SESSİZ KALINMAZ. Geçit ile bu ekran
#: ayrı sürümlenebilir modüllerdir ve tablo bu turda ekranla PARALEL yazıldı;
#: eski bir `bld_api` yüklüyse metot yoktur. Doğrudan çağırıp `AttributeError`i
#: K7 yutucusuna bırakmak, eksik metodu DÜŞMÜŞ BİR SUNUCUDAN ayırt edilemez
#: kılardı — ekranda ikisi de "BLD'ye ulaşılamadı" diye görünürdü ve kimse
#: geçidi güncellemeyi akıl etmezdi. `_missing_gateway()` adı yazarak söyler.
CREATE_METHOD = "create_order"


class ManualOrderService:
    """Elle sipariş ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, log: Any, config: dict[str, Any]) -> None:
        self._api = api
        self._log = log
        self._config = config or {}

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci `dryRun` alanını HİÇ göndermezse geçerli olan varsayılan.

        VARSAYILAN KAPALI. Açık bırakmak, panelden açılan HER siparişi sessizce
        provaya çevirmek olurdu: ekran "sipariş açıldı, no: BLD-…" der, mutfakta
        hiçbir şey görünmez ve fark ancak müşteri yemeği bekleyip aradığında
        anlaşılır.
        """
        return bool(self._config.get("dry_run_default", False))

    @property
    def _customer_page_size(self) -> int:
        """Müşteri aramasının sayfa boyutu. Sözleşme tavanı 100."""
        return max(5, min(100, dr.as_int(self._config.get("customer_page_size"), 20)))

    @property
    def _min_search(self) -> int:
        """Aramanın açılması için gereken en az karakter.

        Sunucu da iki karakter istiyor (`customers.md`); daha kısasını hiç
        göndermemek, personel telefonu yazarken her tuşta bir istek ve bir KVKK
        denetim satırı üretmemek demektir.
        """
        return max(2, min(6, dr.as_int(self._config.get("min_search_chars"), 3)))

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçidin hata kodu.

        `BldApiError` import EDİLMEZ (K2/K3): başka bir modülün sınıfına
        bağlanmak, o modül yüklenmediğinde bu modülü de düşürürdü. Üstelik
        import edilmemiş bir sınıfa `instanceof`/`isinstance` sormak sessizce
        `False` döner ve dal hiç çalışmaz. Kod bir dizedir ve `getattr` ile
        okunur.
        """
        return str(getattr(failure, "code", "") or "")

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit sayfalı uçları `{"items": [...], "meta": {...}}` biçiminde
        açıyor; `data` ve düz dizi de kabul edilir çünkü tek bir ada bağlanmak,
        ad tutmadığında ekranı SESSİZCE boş gösterirdi.
        """
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _server_time(payload: Any) -> str:
        if isinstance(payload, dict):
            return dr.text(payload.get("server_time"))
        return ""

    def _missing_gateway(self) -> dict[str, Any]:
        """Geçitte sipariş açma metodu yoksa dönen AÇIK cevap.

        Sessizce `AttributeError` yutmak yerine adı yazıyoruz: yanlış ya da
        henüz eklenmemiş bir metot adı, düşmüş bir sunucudan ancak böyle ayırt
        edilir.
        """
        self._log.error("geçitte sipariş açma metodu yok", method=CREATE_METHOD)
        return {
            "ok": False,
            "error": (f"BLD geçidinde `{CREATE_METHOD}` metodu yok. Sipariş açma ucu "
                      "(`POST /api/control/orders`) geçide eklenene kadar bu ekrandan "
                      "sipariş kaydedilemez; okuma bölümleri çalışmaya devam eder."),
            "code": "gateway_method_missing",
        }

    # ================================================================= okuma

    async def overview(self, *, allow_price_override: bool = False) -> dict[str, Any]:
        """Panel açılışı — SÖZLEŞME VE SINIRLAR, ağa çıkmaz.

        Ekranın açılışta ihtiyacı olan şey zaten sunucuda değil: teslimat
        türleri, ödeme sözlüğü, alan uzunlukları ve adet sınırları. Bunları ağa
        çıkmadan vermek, geçit düşükken bile formun çizilebilmesi demektir
        (K7). Servis gününe bağlı olan her şey (kesim saati, stok, gerçek ödeme
        yöntemi listesi) `service_day()` ucundadır.

        `can_price_override` YETKİLENDİRME DEĞİL, ÇİZİM BİLGİSİDİR. Asıl kapı
        `create()` içindedir (K9); buradaki bayrak yalnız ekranın her seferinde
        reddedilecek bir kutu çizmesini önler. Varsayılanı `False`: bayrağı
        vermeyi unutan bir çağıran kutuyu AÇMAZ, açık bırakmaz.
        """
        return {
            "ok": True,
            "connected": None,   # bu uç ağa çıkmaz; bağlantı diğer uçlardan anlaşılır
            "error": "",
            "contract": dr.screen_contract(),
            "limits": {
                "customer_page_size": self._customer_page_size,
                "min_search_chars": self._min_search,
            },
            "dry_run_default": self._dry_run_default,
            "can_price_override": bool(allow_price_override),
        }

    async def customers(self, *, q: str, actor: str) -> dict[str, Any]:
        """Müşteri araması — KVKK gereği her OKUMA denetlenir.

        `actor` geçide ZORUNLU olarak gider ve sorgu dizesine konur
        (`customers.md`): "kim baktı" sorusunun cevabı hiçbir okumada boş
        kalmaz. BU EKRANDA OTOMATİK YENİLEME KURULMAZ — kendiliğinden dönen bir
        yoklama, kimsenin bakmadığı anlarda denetim satırı üretirdi.

        Kısa sorgu HİÇ GÖNDERİLMEZ: tek harflik bir arama bütün müşteri
        tablosunu döndürür ve personel telefonu yazarken her tuşta bir istek
        çıkardı.
        """
        needle = dr.text(q)
        if len(needle) < self._min_search:
            return {"ok": True, "connected": None, "error": "", "items": [],
                    "query": needle, "too_short": True,
                    "min_chars": self._min_search}

        try:
            payload = await self._api.customers(actor=actor, q=needle, page=1,
                                                per_page=self._customer_page_size)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("müşteri araması yapılamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": [], "query": needle,
                    "too_short": False, "min_chars": self._min_search}

        return {"ok": True, "connected": True, "error": "",
                "items": [dr.customer_row(row) for row in self._rows(payload)],
                "query": needle, "too_short": False, "min_chars": self._min_search}

    async def products(self) -> dict[str, Any]:
        """Seçicinin ürün kataloğu — geçitte ÖNBELLEKLİ referans veri.

        `product_picker()` tüm sayfaları tarar ve sonucu önbelleğe alır: on iki
        panelin aynı listeyi tekrar tekrar istemesi paylaşılan hız kovasını
        (`bld-control-panel`, 3000/saat/IP) boşuna yakardı. Ürün yazan her
        metot bu dalı düşürüyor, yani yeni eklenen ürün ilk okumada görünür.

        YALNIZ SATIŞTAKİLER: satıştan kaldırılmış bir ürün seçiciye konsaydı
        personel onu telefonda müşteriye önerir, sunucu da `422
        ITEM_UNAVAILABLE` ile reddederdi.
        """
        try:
            payload = await self._api.product_picker(only_active=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("ürün kataloğu okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": [], "truncated": False}

        rows = [dr.product_row(row) for row in self._rows(payload)]
        truncated = bool(payload.get("truncated")) if isinstance(payload, dict) else False
        return {"ok": True, "connected": True, "error": "", "items": rows,
                # `truncated` True ise sunucuda daha çok kayıt var ve ekran bunu
                # SÖYLEMELİ: eksik bir seçici, aranan ürünün hiç olmadığını
                # düşündürür.
                "truncated": truncated}

    async def service_day(self, date: str) -> dict[str, Any]:
        """Servis gününün kesim saati, stoğu ve ödeme yöntemleri.

        ÜÇ OKUMA, TEK CEVAP: satış ayarı (genel kesim + teslimat ücreti + ödeme
        listesi), o günün menüsü (güne özel kesim) ve o günün stoğu. Panelin üç
        ayrı istek atması, üç ayrı hata hâli ve üç ayrı "yükleniyor" demekti.

        GÜN MENÜSÜ YOKSA HATA DEĞİLDİR. `adminContext: true` menü üyeliğini
        atlıyor: menüsü tanımlanmamış bir güne de sipariş açılabilir. Menü ve
        stok uçları o günde `404` verir; burada "bilinmiyor"a çevrilir ve
        kırmızı bir kutu çizilmez — olağan bir durumu arıza gibi göstermek,
        personelin gerçek arızayı ciddiye almamasına yol açar.
        """
        problem = dr.date_error(date)
        if problem:
            return {"ok": False, "connected": None, "error": problem,
                    "date": dr.text(date), "cutoff": {}, "stock": {}, "warnings": []}

        day = dr.text(date)
        result: dict[str, Any] = {
            "ok": True, "connected": True, "error": "", "date": day,
            "cutoff": {}, "stock": {}, "menu_missing": False,
            "payment_methods": [], "delivery_fee_kurus": 0, "server_time": "",
        }

        # 1) Satış ayarı — genel kesim saati, teslimat ücreti, ödeme listesi.
        try:
            settings = await self._api.sales_settings()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("satış ayarı okunamadı", error=str(failure))
            return {**result, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure)}

        data = self._record_of(settings, "data")
        server_time = self._server_time(settings)
        general_cutoff = dr.text(data.get("order_cutoff"))
        methods = data.get("payment_methods")
        result["payment_methods"] = [dr.text(value) for value in methods] \
            if isinstance(methods, list) else []
        result["delivery_fee_kurus"] = dr.as_int(data.get("delivery_fee_kurus"))
        result["server_time"] = server_time
        # Satış duraklatılmışsa bu ekran YİNE ÇALIŞIR (şalter `adminContext`
        # ile atlanıyor); yalnız yazıyla söylenir. Gizlemek, personelin
        # vitrindeki kapalı tabelayı bilmemesi olurdu.
        result["ordering_enabled"] = bool(data.get("ordering_enabled", True))
        result["pause_reason"] = dr.text(data.get("pause_reason"))

        # 2) Güne özel kesim saati — GENELİ EZER (`settings.md`).
        day_cutoff = ""
        try:
            menu_day = await self._api.menu_day(day)
        except Exception as failure:  # noqa: BLE001 — K7
            if self._code(failure) != "not_found":
                self._log.warning("gün menüsü okunamadı", date=day, error=str(failure))
            result["menu_missing"] = True
        else:
            menu = self._record_of(menu_day, "data")
            day_cutoff = dr.text(menu.get("cutoff_time"))
            result["menu_title"] = dr.text(menu.get("title"))

        result["cutoff"] = dr.cutoff_state(day, cutoff=day_cutoff or general_cutoff,
                                           server_time=server_time)
        result["cutoff"]["source"] = "day" if day_cutoff else ("general"
                                                               if general_cutoff else "")

        # 3) Stok — TAVAN AŞIMI UYARISININ TEMELİ. Önbelleğe ALINMAZ (geçit
        #    kuralı): bir dakika bayat bir sayı, dolmuş bir günü açık gösterir.
        try:
            stock = await self._api.menu_stock(day)
        except Exception as failure:  # noqa: BLE001 — K7
            if self._code(failure) != "not_found":
                self._log.warning("gün stoğu okunamadı", date=day, error=str(failure))
            result["stock"] = {"day": {}, "items": {}}
            result["stock_missing"] = True
        else:
            result["stock"] = dr.stock_index(stock)
            result["stock_missing"] = False

        return result

    async def stock_check(self, *, service_date: str, items: Any) -> dict[str, Any]:
        """Sepetin gün ve ürün tavanını aşıp aşmadığını söyler. ENGELLEMEZ.

        AYRI BİR UÇ ve bilerek: `service_day()` gün değişince BİR KEZ çağrılıyor
        ve üç okuma yapıyor (ayar + menü + stok). Sepet ise personel telefonda
        konuşurken defalarca değişiyor; her değişikliğe üç okuma harcamak,
        paylaşılan hız kovasını (`bld-control-panel`, 3000/saat/IP) boşuna
        yakardı. Burada TEK okuma var.

        STOK HER SEFERİNDE YENİDEN OKUNUR ve önbelleğe alınmaz: abonelikler
        porsiyonu önceden tutuyor ve personel telefondayken sayı değişiyor. Bir
        dakika bayat bir sayı, dolmuş bir günü açık gösterirdi.

        `POST` seçilmesinin sebebi yetki ya da yan etki DEĞİL, GÖVDE ŞEKLİ:
        kalem listesi bir nesne dizisidir ve sorgu dizesine sığmaz. Uç
        `bld_manual_order.view` ile korunur çünkü yaptığı iş bir OKUMADIR.

        Uyarı üretmemek "aşım yok" demek değildir: stok okunamadıysa
        `connected: false` döner ve panel sessizce güven vermez.
        """
        problem = dr.date_error(service_date)
        if problem:
            return {"ok": False, "connected": None, "error": problem, "warnings": []}

        clean, problem = dr.clean_items(items)
        if problem:
            # Yarım bir sepet uyarı üretemez; bu bir HATA değil, henüz
            # tamamlanmamış bir taslaktır.
            return {"ok": True, "connected": None, "error": "", "warnings": [],
                    "incomplete": True}

        try:
            stock = await self._api.menu_stock(dr.text(service_date))
        except Exception as failure:  # noqa: BLE001 — K7
            if self._code(failure) == "not_found":
                # Menüsü tanımlanmamış günün tavanı da yoktur; aşım da yok.
                return {"ok": True, "connected": True, "error": "", "warnings": [],
                        "stock_missing": True}
            self._log.warning("stok denetimi yapılamadı", date=dr.text(service_date),
                              error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "warnings": []}

        return {"ok": True, "connected": True, "error": "",
                "warnings": dr.stock_warnings(dr.stock_index(stock), clean),
                "stock_missing": False}

    # ================================================================= yazma

    async def create(
        self, *, service_date: str, delivery_type: str, payment_method: str,
        items: Any, actor: str, customer_id: int = 0, customer_name: str = "",
        customer_phone: str = "", address: Any = None, customer_note: str = "",
        reason: str = "", location_id: int = 0, dry_run: bool | None = None,
        agreed_total_kurus: Any = None, allow_manage: bool = False,
        allow_price_override: bool = False,
    ) -> dict[str, Any]:
        """Yeni sipariş açar — `POST /api/control/orders`.

        SİPARİŞ `onaylandi` DOĞAR. `yeni` bırakılsaydı mutfağa hiç düşmez, tek
        belirtisi aç kalan bir müşteri olurdu; telefonda teyit zaten alınmıştır.
        Geçişi sunucuda `OrderStatusTransition::apply()` yapıyor ve fiş
        tetikleyicisi orada.

        GEÇİŞ PATLARSA SİPARİŞ YİNE VARDIR. Sunucu bu hâlde `ok: true` + `201`
        döner ve `warnings` taşır; ekran uyarıyı gösterir ama "olmadı" DEMEZ —
        "olmadı" diyen bir ekran, personelin siparişi ikinci kez girmesine ve
        müşteriye iki kere yemek çıkmasına yol açardı.

        AYNI TELEFONLA İKİNCİ SİPARİŞ İKİNCİ MÜŞTERİ YARATMAZ: yer tutucu
        e-posta (`tel-<ulusal numara>@bld.invalid`) numaranın ulusal biçiminden
        türüyor ve kayıt varsa döndürülüyor. Yanıttaki `customer.created`
        hangisinin olduğunu söyler ve ekran onu yazar.

        ANLAŞMALI SEPET FİYATI (`agreed_total_kurus`) AYRI BİR YETKİ İSTER
        (`bld_manual_order.price_override`). Sipariş AÇMAK ile katalog fiyatını
        KIRMAK aynı iş değil: birincisi rutin bir kayıt, ikincisi ciroyu
        değiştiren bir ticari karar. Tek anahtar olsaydı fiyat kırmayı
        engellemenin yolu sipariş girmeyi engellemekten geçerdi. Ölçüt
        `bld_orders`'ın iptali ayrı anahtara almasıyla aynı: PARA.

        Alan BOŞSA yetki de sorulmaz — kutuya hiç dokunmamış bir personel,
        yetkisi yok diye reddedilmemeli.
        """
        if not allow_manage:
            # ÇİFT KAPI (K9): uç noktada da denetleniyor. Arayüzde düğmeyi
            # gizlemek yetkilendirme değildir; istemci gövdeyi elle kurabilir.
            return {"ok": False, "error": "Sipariş açmak `bld_manual_order.manage` "
                                          "yetkisi ister."}

        clean, problem = self._validate(
            service_date=service_date, delivery_type=delivery_type,
            payment_method=payment_method, items=items, customer_id=customer_id,
            customer_name=customer_name, customer_phone=customer_phone,
            address=address, customer_note=customer_note, reason=reason,
            agreed_total_kurus=agreed_total_kurus)
        if problem:
            return {"ok": False, "error": problem}

        if clean["agreed_total_kurus"] is not None and not allow_price_override:
            # ÇİFT KAPI (K9), İKİNCİ ANAHTAR. Ekran kutuyu gizliyor olabilir
            # ama gövde elle kurulabilir; kapı burada da duruyor.
            return {"ok": False,
                    "error": ("Müşteriye özel sepet fiyatı uygulamak "
                              "`bld_manual_order.price_override` yetkisi ister. "
                              "Siparişi katalog fiyatıyla açabilirsiniz.")}

        call = getattr(self._api, CREATE_METHOD, None)
        if not callable(call):
            return self._missing_gateway()

        dry = self._dry(dry_run)
        payload: dict[str, Any] = {
            "service_date": clean["service_date"],
            "delivery_type": clean["delivery_type"],
            "payment_method": clean["payment_method"],
            "items": clean["items"],
            # GÖVDEYİ GEÇİT KURAR; buradan yalnız alanlar geçer. İkinci bir
            # gövde kurmak, iki yerde ayrışabilen bir sözleşme demek olurdu.
            "actor": actor,
            "reason": clean["reason"],
            # HER YAZMADA AÇIKÇA: geçidin varsayılanına güvenilmez.
            "dry_run": dry,
        }
        if clean["customer_id"]:
            payload["customer_id"] = clean["customer_id"]
        else:
            payload["customer"] = {"name": clean["customer_name"],
                                   "phone": clean["customer_phone"]}
        if clean["address"] is not None:
            payload["address"] = clean["address"]
        if clean["customer_note"]:
            payload["customer_note"] = clean["customer_note"]
        if dr.as_int(location_id) > 0:
            payload["location_id"] = dr.as_int(location_id)
        if clean["agreed_total_kurus"] is not None:
            # YALNIZ DOLUYKEN. Geçit `None`u zaten gövdeye koymuyor; burada da
            # koymamak, "anlaşma yok" hâlinin tek bir biçimi olmasını sağlıyor.
            payload["agreed_total_kurus"] = clean["agreed_total_kurus"]

        try:
            result = await call(**payload)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş açılamadı", serviceDate=clean["service_date"],
                              dryRun=dry, error=str(failure))
            return {"ok": False, "error": self._explain(failure),
                    "code": self._code(failure), "dry_run": dry}

        return self._result(result, dry=dry, items=clean["items"])

    # ------------------------------------------------------------- iç işler

    def _validate(self, **fields: Any) -> tuple[dict[str, Any], str]:
        """Gövdenin erken denetimi. `(temiz, hata)`.

        SUNUCU DA DENETLİYOR (çift kapı) ve orası son sözü söyler; buradaki
        kapı ağ turunu ve telefonda bekleyen bir müşteriyi bir tur beklemekten
        kurtarır. Sunucunun REDDETMEDİĞİ hiçbir şey burada reddedilmez —
        özellikle sipariş penceresi: onu sunucu bilerek atlıyor.
        """
        problem = dr.date_error(fields.get("service_date"))
        if problem:
            return {}, problem

        delivery_type = dr.text(fields.get("delivery_type"))
        problem = dr.choice_error(delivery_type, dr.DELIVERY_TYPES, field="Teslimat türü")
        if problem:
            return {}, problem

        payment_method = dr.text(fields.get("payment_method"))
        problem = dr.choice_error(payment_method, dr.PAYMENT_METHODS,
                                  field="Ödeme yöntemi")
        if problem:
            # `account` cari hesapla birlikte kalktı; mesaj ne yapılacağını
            # söylesin diye geçerli değerleri sayıyor (sunucu da öyle yapıyor).
            return {}, problem

        problem = dr.customer_error(customer_id=dr.as_int(fields.get("customer_id")),
                                    name=fields.get("customer_name"),
                                    phone=fields.get("customer_phone"))
        if problem:
            return {}, problem

        problem = dr.address_error(delivery_type, fields.get("address"))
        if problem:
            return {}, problem

        problem = dr.reason_error(fields.get("reason"))
        if problem:
            return {}, problem

        note = dr.text(fields.get("customer_note"))
        if len(note) > dr.MAX_CUSTOMER_NOTE:
            return {}, f"Müşteri notu en çok {dr.MAX_CUSTOMER_NOTE} karakter olabilir."

        clean_items, problem = dr.clean_items(fields.get("items"))
        if problem:
            return {}, problem

        # BİÇİM DENETİMİ, İŞ KURALI DEĞİL. Tutarın kalem toplamının yerine
        # geçmesine, kalemlerin fiyatsız yazılmasına ve teslimat ücretinin
        # içinde sayılmasına sunucu karar veriyor (`OrderFactory::create()`).
        agreed_total, problem = dr.clean_agreed_total(fields.get("agreed_total_kurus"))
        if problem:
            return {}, problem

        address: dict[str, Any] | None = None
        if delivery_type == "delivery":
            raw = fields.get("address") if isinstance(fields.get("address"), dict) else {}
            address = {
                "line1": dr.text(raw.get("line1")),
                "district": dr.text(raw.get("district")),
                "city": dr.text(raw.get("city")),
            }
            note_value = dr.text(raw.get("note"))
            if note_value:
                address["note"] = note_value

        return {
            "service_date": dr.text(fields.get("service_date")),
            "delivery_type": delivery_type,
            "payment_method": payment_method,
            "items": clean_items,
            "customer_id": dr.as_int(fields.get("customer_id")),
            "customer_name": dr.text(fields.get("customer_name")),
            # TELEFON ULUSAL BİÇİMDE GİDER. Sunucu da indirgiyor ama aynı
            # numaranın iki farklı yazımı gövdede farklı görünürse denetim izi
            # ("ne denendi") iki ayrı arayan gibi okunurdu.
            "customer_phone": dr.national_phone(fields.get("customer_phone")),
            "address": address,
            "customer_note": note,
            "reason": dr.text(fields.get("reason")),
            # `None` = anlaşma yok; alanın hiç gönderilmemesiyle eşdeğerdir.
            "agreed_total_kurus": agreed_total,
        }, ""

    def _explain(self, failure: Exception) -> str:
        """Geçidin hatasını personelin okuyabileceği cümleye çevirir."""
        message = self._fail(failure)
        code = self._code(failure)
        if code == "validation":
            return (f"{message} Ödeme yöntemi, servis günü ve kalemler sunucuda da "
                    "denetleniyor; satışta olmayan bir ürün burada reddedilir.")
        if code == "not_found":
            return f"{message} Seçilen müşteri kaydı BLD'de bulunamadı; aramayı yenileyin."
        return message

    def _result(self, result: Any, *, dry: bool, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Sunucunun cevabını ekranın okuyacağı biçime getirir.

        KURU PROVADA SİPARİŞ NUMARASI YOKTUR ve uydurulmaz: `would` bloğu
        gövdenin nasıl okunduğunu söyler, sipariş doğmamıştır. Ekranın "açıldı"
        demesi ancak `dry_run: false` iken doğrudur.

        KURU PROVA KALEM GEÇERLİLİĞİNİ, FİYATI VE STOĞU DENETLEMEZ (sözleşmede
        bilinçli eksik): `OrderFactory::create()`'in yan etkisiz bir yarısı yok.
        Prova "gövde doğru mu" sorusunun cevabıdır, "sipariş geçecek mi"
        sorusunun değil — ekran bunu yazıyla söyler.
        """
        payload = result if isinstance(result, dict) else {}
        warnings = payload.get("warnings")
        clean_warnings = [dr.text(item) for item in warnings] \
            if isinstance(warnings, list) else []

        if dry:
            return {
                "ok": True, "error": "", "dry_run": True,
                "would": payload.get("would") if isinstance(payload.get("would"), dict)
                else {"item_count": len(items)},
                "order": {}, "customer": {}, "warnings": clean_warnings,
                "note": ("Kuru prova: gövde ve ödeme yöntemi denetlendi, müşteri "
                         "çözümlendi. Kalem geçerliliği, fiyat ve stok DENETLENMEDİ; "
                         "sipariş açılmadı."),
            }

        customer = self._record_of(payload, "customer")
        return {
            "ok": True, "error": "", "dry_run": False,
            "order": dr.order_row(self._record_of(payload, "data", "order")),
            "customer": {"id": dr.as_int(customer.get("id")),
                         "created": bool(customer.get("created"))},
            # SUNUCU UYARISI SESSİZ GEÇİLMEZ: bugün tek bir hâlde doluyor —
            # sipariş yazıldı ama `onaylandi` geçişi patladı. O sipariş vardır,
            # kaybolmaz ve sipariş ekranından elle onaylanır.
            "warnings": clean_warnings,
            "audit_id": dr.as_int(payload.get("audit_id")),
        }
