"""Müşteriler — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Müşteri kaydı, adres defteri, sipariş
geçmişi, abonelik ve SMS kaydı BLD sunucusunda durur ve buraya `bld.api`
geçidinden gelir (K4); bu modül ham httpx kullanmaz ve UZAK VERİNİN KOPYASINI
TUTMAZ. Yerel tablolar yalnız BLD'de KARŞILIĞI OLMAYAN üç şeyi saklar: KVKK
erişim izi, yazma denemesinin izi ve bu ekrana özel tercih.

Neden kopya tutulmuyor: burası sistemdeki en geniş kişisel veri yüzeyi. Yerel
bir kopya KVKK yüzeyini ikiye katlar, silme talebini iki yerde karşılamayı
gerektirir ve her zaman bir tur geride kalır — "açık" görünen bir hesap aslında
yarım saattir kapalı olabilir.

================== OKUMALAR DA DENETLENİR (`00-genel.md` §9) ==================

Diğer BLD modüllerinde yalnız yazmalar iz bırakır. Burada HER OKUMA iki yere
birden yazılır:

  1. SUNUCUDA — geçit zorunlu `actor` sorgu parametresini gönderir ve BLD
     `customer.read` satırı yazar. Bu satırı biz yazmayız, sunucu yazar.
  2. YERELDE — `mod_bld_customers_access` tablosuna bir satır. Sunucudaki
     kayıt yalnız SUNUCUYA ULAŞAN okumayı bilir; ağ koparsa, imza reddedilirse
     ya da geçit patlarsa "kim kimin kaydını açmak istedi" sorusunun cevabı
     yalnız burada kalır.

YEREL OKUMA SATIRI ÇAĞRIDAN SONRA YAZILIR, yazma izinin aksine. Yazmada
"denendi" satırı çağrıdan ÖNCE düşer çünkü yarıda kalan bir yazma uzakta
uygulanmış olabilir ve o belirsizliğin kaydı gerekir. Okumada böyle bir
belirsizlik yoktur: okuma uzakta bir şey değiştirmez, sonucu ya ekrana geldi ya
gelmedi — ve o sonucu tek satırda `okundu`/`hata` olarak yazmak, her okuma için
iki satır üretmekten daha okunur bir defter bırakır.

AKTÖR GÖVDEDEN/SORGUDAN ALINMAZ, OTURUMDAN GELİR. Sözleşme `actor`ı sorgu
dizesinde taşıyor ama o sınır BLD ile Kontrol Merkezi ARASINDADIR. Panel ile bu
modül arasında böyle bir sınır yok: istemcinin aktör adını yazabilmesi, silinmez
bir deftere istediği adı yazabilmek demek olurdu. `CurrentUser.full_name` tek
kaynaktır.

BU EKRANLAR YOKLANMAZ. Bu dosyada zamanlayıcı, panelde `pollLoop` yoktur ve
konmayacaktır: 15 saniyede bir yoklayan bir ekran, denetim izini günde binlerce
anlamsız satırla doldurup içindeki gerçek erişimi görünmez kılardı.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISINI DEĞİL UCUN SAĞLIĞINI anlatır: uç çalıştı,
cevabı "bağlanamadım"dır. Ayrımı `connected` taşır ve ekran onu OKUMAK
zorundadır — yalnız `ok`a bakan bir panel geçit düştüğünde "müşteri yok" der.

HER YAZMADA `dry_run=` AÇIKÇA GEÇİLİR. Geçidin varsayılanı ayardan gelir ve
`config/local.yaml` git dışıdır; bayrağı atlayan bir çağrı hiçbir şey yazmadan
`{"ok": true}` alabilir ve ekran "kaydedildi" der. Bu modülde `dry_run` hiçbir
çağrıda atlanmaz — değeri `_dry()` üretir ve her zaman gerçek bir `bool`'dur.

SİLME YOKTUR. Hesap `POST /{id}/disable` ile kapanır, kayıt DURUR. Silme ucu
sözleşmede yoktur ve burada uydurulmaz: geçmiş siparişlerin müşterisi olmayan
kayıtlara dönüşmesi muhasebe ve denetim açısından geri alınamaz bir kayıptır.

E-POSTA VE PAROLA YAZILMAZ. `people.FORBIDDEN_FIELDS` bu üç alanı (+ `status`)
kendi gerekçesiyle reddeder ve istek geçide HİÇ GİTMEZ.
"""

from __future__ import annotations

import json
from typing import Any

from . import people

#: Hesap kapatıldığında yayınlanan olay (manifest). Kuru provada YAYINLANMAZ ve
#: başarısız çağrıda da yayınlanmaz. YÜKTE TELEFON, E-POSTA VE ADRES YOKTUR:
#: olay yolu bir kişisel veri kanalı değildir, dinleyicileri de KVKK yüzeyine
#: sokmak istemiyoruz.
DISABLED_EVENT = "bld_customers.account_disabled"

#: Yerel tercih tablosunda tanınan anahtarlar. Listede olmayan anahtar
#: REDDEDİLİR: sessizce yutulan bir tercih, kaydedildiğini sanan kullanıcıya
#: her açılışta eski ekranı gösterirdi.
PREF_KEYS = ("page_size", "status_filter", "sort", "direction")


class CustomersService:
    """Müşteriler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

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

        self._access = store.table("access")
        self._audit = store.table("audit")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci bayrağı HİÇ göndermezse geçerli olan varsayılan.

        KAPALI. Yedek değer de `False`: ayar dosyası okunamadığında ekranın
        "kaydedildi" deyip hiçbir şey yazmaması, açık bir hatadan çok daha
        pahalıdır — müşterinin telefonu eski hâlinde kalır ve bunu kimse fark
        etmez, ta ki aranmayana kadar.
        """
        return people.as_bool(self._config.get("dry_run_default", False))

    @property
    def _page_size(self) -> int:
        return people.clean_per_page(self._config.get("page_size"), people.PER_PAGE_DEFAULT)

    @property
    def _order_page_size(self) -> int:
        return people.clean_per_page(self._config.get("order_page_size"),
                                     people.PER_PAGE_DEFAULT)

    @property
    def _sms_page_size(self) -> int:
        return people.clean_per_page(self._config.get("sms_page_size"),
                                     people.PER_PAGE_DEFAULT)

    @property
    def _access_log_limit(self) -> int:
        return max(1, min(1000, people.as_int(self._config.get("access_log_limit"), 200)))

    @property
    def _audit_limit(self) -> int:
        return max(1, min(500, people.as_int(self._config.get("audit_limit"), 100)))

    def _dry(self, dry_run: bool | None) -> bool:
        """Kuru prova kararı — SONUCU HER ZAMAN `bool`.

        `None` geçide gönderilmez: geçit `None` gördüğünde kendi ayarına düşer
        ve o ayar `config/local.yaml` ile açılmış olabilir. Bayrağın burada
        gerçek bir değere indirgenmesi, "kaydedildi" ile "sessizce atıldı"
        arasındaki farkın tek garantisidir.
        """
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------------ yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçit hata kodu (`BldApiError.code`). Bilinmiyorsa boş dize.

        Ekran HTTP durumuna değil bu koda bakar (`00-genel.md` §7):
        `control_endpoint_missing` "uç henüz sunucuda yayında değil, bekle",
        `conflict` "tazele ve tekrar sor", `actor_required` ise "oturumdaki
        kullanıcının adı yok" demektir ve üçü farklı cümleler ister.
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

    def _blocked(self, message: str) -> dict[str, Any]:
        """Geçide HİÇ GİTMEDEN reddedilen istek.

        `blocked: true` ekranın ayırt etmesi içindir: "sunucu reddetti" ile
        "biz göndermedik" farklı cümlelerdir ve ikincisinde tekrar denemek
        anlamsızdır.
        """
        return {"ok": False, "blocked": True, "error": message, "code": ""}

    # -------------------------------------------------------- KVKK okuma izi

    async def _trace(self, *, scope: str, actor: str, customer_id: int = 0,
                     filters: Any = None, ok: bool = True, error: str = "",
                     action: str = "") -> None:
        """KVKK erişim izi — YEREL. Sunucu da yazıyor, bu ONUN YERİNE DEĞİL.

        `filters` YALNIZ SÜZGEÇLERİ taşır (sözleşme §9.4). Dönen kayıtların
        kendisi ASLA yazılmaz — denetim izini ikinci bir müşteri veritabanına
        çevirirdi. Süzgecin kendisi yazılır çünkü "acme diye aradı" bilgisi
        erişimin kapsamıdır ve sunucu da aynısını yazıyor; iki defter yan yana
        okunabilmeli.

        İZ YAZILAMAZSA OKUMA DURMAZ (K7). Tartışmalı bir karar ve gerekçesi
        şu: sunucu tarafı denetim satırını ZATEN yazdı (okuma oraya ulaştı) ve
        yerel defterin yazılamaması, yöneticiyi ekrandan mahrum bırakmayı
        haklı çıkarmaz. Yazılamayan iz `log.error` ile bildirilir — uyarı
        değil hata seviyesinde, çünkü bu bir gözetim boşluğudur.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._access} "
                "(actor, action, scope, customer_id, filters, result, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (people.text(actor), action or people.READ_ACTION, scope,
                 int(customer_id or 0),
                 json.dumps(filters or {}, ensure_ascii=False),
                 people.READ_OK if ok else people.READ_FAILED,
                 people.text(error)[:500], people.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — K7, ama SESSİZ DEĞİL
            self._log.error("KVKK erişim izi yazılamadı", scope=scope,
                            customerId=customer_id, error=str(failure))

    async def _read(self, scope: str, *, actor: str, customer_id: int = 0,
                    filters: Any = None, action: str = "",
                    call: Any) -> tuple[Any, str, str]:
        """Denetlenen okuma: aktör kapısı → geçit çağrısı → yerel iz.

        `(yük, hata, kod)` döner; istisna DIŞARI SIZMAZ (K7).

        AKTÖR KAPISI ÇAĞRIDAN ÖNCE: geçit de aynı kapıyı taşıyor
        (`_read_actor`) ama oradan çıkan istisna "BLD'ye ulaşılamadı" gibi
        okunur. Buradaki erken denetim, sebebi anlaşılır bir cümleyle söyler ve
        isteği hiç göndermez.
        """
        guard = people.actor_error(actor)
        if guard:
            # Aktörsüz okuma HİÇ YAPILMAZ, ama DENENDİĞİ yazılır: adı olmayan
            # bir oturumun müşteri defterini açmaya çalışması, tam olarak bu
            # defterin kaydetmesi gereken şeydir.
            await self._trace(scope=scope, actor=people.text(actor),
                              customer_id=customer_id, filters=filters, ok=False,
                              error=guard, action=action)
            return None, guard, "actor_required"
        try:
            payload = await call()
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._trace(scope=scope, actor=actor, customer_id=customer_id,
                              filters=filters, ok=False, error=message, action=action)
            self._log.warning("müşteri okuması başarısız", scope=scope,
                              customerId=customer_id, error=message)
            return None, message, self._code(failure)
        await self._trace(scope=scope, actor=actor, customer_id=customer_id,
                          filters=filters, ok=True, action=action)
        return payload, "", ""

    @staticmethod
    def _offline(error: str, code: str, **extra: Any) -> dict[str, Any]:
        """K7 zarfı: uç sağlıklı, veri okunamadı."""
        return {"ok": True, "connected": False, "error": error, "code": code, **extra}

    @staticmethod
    def _online(**extra: Any) -> dict[str, Any]:
        return {"ok": True, "connected": True, "error": "", "code": "", **extra}

    # ------------------------------------------------------- yazma izi/olay

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      customer_id: int = 0, detail: Any = None) -> None:
        """Yerel yazma izi. BLD de `veykemtu_control_audit` tutuyor
        (`00-genel.md` §8); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        `detail` içindeki TELEFON MASKELİDİR (`people.change_log`). Denetim izi
        "ne değişti" sorusuna cevap vermeli, kişisel verinin ikinci bir
        kopyasını tutmamalı.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(customer_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(customer_id or 0), action, people.text(reason), people.text(actor),
                 result, json.dumps(detail or {}, ensure_ascii=False), people.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("yazma izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: hesap BLD'de kapatılmıştır,
        dinleyicinin patlaması onu geri açmaz (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    async def _fresh(self, customer_id: int, *, actor: str,
                     scope: str) -> tuple[dict[str, Any] | None, str, str]:
        """Müşterinin TAZE hâli. `(kayıt, hata, kod)` döner.

        Yazmadan hemen önce okunur çünkü aradan değişmiş olabilir: başka bir
        yönetici hesabı kapatmış, telefonu düzeltmiş ya da müşteri kendi
        ekranından bilgilerini güncellemiş olabilir. Denetim izine yazılacak
        "eski değer" ancak bu okumadan gelir.

        BU OKUMA DA DENETLENİR ve denetlenmesi gerekir: yazmadan önce kaydı
        görmek de bir görmedir. Sözleşme burada ikinci bir istisna tanımlamıyor
        ve biz de tanımlamıyoruz.
        """
        payload, error, code = await self._read(
            scope, actor=actor, customer_id=customer_id,
            call=lambda: self._api.customer(int(customer_id), actor=actor),
        )
        if error:
            return None, error, code
        row = self._record_of(payload, "data", "customer")
        if not row:
            return None, "Müşteri bulunamadı.", "not_found"
        return people.customer_detail(row), "", ""

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Panel açılışı — BLD'YE HİÇ GİTMEZ.

        BU BİLİNÇLİ VE BU EKRANIN EN ÖNEMLİ KARARI. Diğer BLD ekranlarında
        `overview` sayaç toplamak için birkaç `per_page=1` isteği atar. Burada
        atmaz: her istek bir `customer.read` denetim satırı yazar ve açılışta
        dört sayaç çekmek, kimsenin sormadığı bir soru için deftere dört satır
        yazmak olurdu. İzin okunur kalması, sayacın kendisinden değerlidir.

        Sayılar zaten aramanın `meta.total` alanından geliyor — yani yönetici
        listeye baktığında. "Kaç müşterimiz var" sorusunun cevabı da orada ve
        o cevabı almak için atılan istek, yöneticinin BİLİNÇLİ bir eylemidir.

        Bu yüzden yanıt `connected` TAŞIMAZ: bağlantı durumu ancak gerçek bir
        okumada bilinir ve burada uydurulmuş bir `true` ekranı "bağlı" diye
        gösterirdi.
        """
        return {
            "ok": True,
            "filters": people.filter_spec(),
            "prefs": await self.prefs(),
            # Panelin KVKK uyarısı SUNUCUDAN GELİR, panelde yazılmaz: aynı
            # cümlenin iki kopyası zamanla ayrışır ve biri güncellenmez.
            "kvkk_notice": (
                "Bu ekrandaki her arama ve açılan her müşteri kartı denetim izine "
                "yazılır — kim, ne zaman, kimin kaydını açtı. Diğer ekranlarda yalnız "
                "değişiklikler kaydedilir; burada OKUMALAR da kaydedilir. Kayıt hem "
                "BLD sunucusunda hem bu bilgisayarda tutulur ve silinemez."
            ),
            "readonly_notice": (
                "E-posta ve parola bu ekrandan değiştirilemez; sözleşmede yazma yolu "
                "yoktur. Adres defteri salt okunurdur: adres siparişe kopyalanıyor, "
                "bağlanmıyor — buradan düzeltmek geçmiş siparişlerin adresini "
                "değiştirmez. Müşteri silinemez, yalnız hesabı kapatılır."
            ),
        }

    async def customers(self, *, actor: str, q: str = "", status: str = "",
                        has_subscription: bool | None = None, sort: str = "",
                        direction: str = "", page: int = 1,
                        per_page: int = 0) -> dict[str, Any]:
        """Müşteri arama — SUNUCU TARAFINDA SAYFALANIR. **Denetlenir.**

        SÜZGEÇSİZ İSTEK SERBESTTİR ve ilk sayfayı döndürür (sözleşme). Listeyi
        tamamen kapatmak, "kaç müşterimiz var" gibi meşru bir soruyu cevapsız
        bırakırdı; asıl koruma denetim izidir.

        LİSTE MASKELENMEZ. Talep listesindeki maskeleme kuralı buraya
        UYGULANMADI ve sebebi somut: yönetici müşteriyi telefonundan tanır ve
        maskeli bir listede doğru kaydı seçemez, hepsini tek tek açmak zorunda
        kalır — yani her arama için bir düzine denetim satırı doğar. Maskeleme
        burada gizliliği artırmaz, izi bozar.

        `q` EN AZ İKİ KARAKTER; kısası isteğe hiç konmaz (gönderilse sunucu
        `422` verirdi ve kullanıcı yazmaya devam ederken hata görürdü).
        """
        size = people.clean_per_page(per_page, self._page_size)
        number = max(1, people.as_int(page, 1))
        used = {
            "q": people.clean_query(q),
            "status": people.clean_status(status),
            "has_subscription": has_subscription,
            "sort": people.clean_sort(sort),
            "direction": people.clean_direction(direction),
            "page": number,
            "per_page": size,
        }
        empty = {"items": [], "meta": people.page_meta({}, page=number, per_page=size, rows=0),
                 "used": used}

        payload, error, code = await self._read(
            "list", actor=actor, filters=used,
            call=lambda: self._api.customers(
                actor=actor, q=used["q"], status=used["status"],
                has_subscription=has_subscription, sort=used["sort"],
                direction=used["direction"], page=number, per_page=size,
            ),
        )
        if error:
            return self._offline(error, code, **empty)

        rows = [people.customer_row(item) for item in self._rows(payload)]
        return self._online(
            items=rows,
            meta=people.page_meta(self._meta_of(payload), page=number, per_page=size,
                                  rows=len(rows)),
            used=used,
        )

    async def customer(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        """Tek müşteri + istatistikleri. **Denetlenir.**

        `stats` bu yanıtta gelir, ayrı bir uçta değil (sözleşme): müşteri
        kartını açan yönetici zaten bu sayıları görmek istiyor ve ayrı bir
        çağrı ikinci bir denetim satırı yazardı.
        """
        row, error, code = await self._fresh(customer_id, actor=actor, scope="detail")
        if row is None:
            return self._offline(error, code, customer={})
        return self._online(customer=row)

    async def orders(self, customer_id: int, *, actor: str, status: str = "",
                     date_from: str = "", date_to: str = "", page: int = 1,
                     per_page: int = 0) -> dict[str, Any]:
        """Müşterinin sipariş geçmişi (sayfalı). **Denetlenir.**

        Satır biçimi `orders.md` → `GET /` ile AYNIDIR; iki farklı sipariş
        şekli tanımlamak, panelin iki ayrı tablo bileşeni yazması demekti.

        BU EKRAN SİPARİŞİ DEĞİŞTİRMEZ. Revizyon, durum geçişi ve iptal
        `bld_orders`'ın işidir; buradan oraya bir kısayol da konmaz — bir iş
        eylemi tek ekranda durur, yoksa denetim izinde "hangi ekrandan
        yapıldı" sorusu cevapsız kalır.
        """
        size = people.clean_per_page(per_page, self._order_page_size)
        number = max(1, people.as_int(page, 1))
        used = {"status": people.text(status), "from": people.text(date_from),
                "to": people.text(date_to), "page": number, "per_page": size}
        empty = {"items": [], "meta": people.page_meta({}, page=number, per_page=size, rows=0),
                 "used": used}

        payload, error, code = await self._read(
            "orders", actor=actor, customer_id=customer_id, filters=used,
            call=lambda: self._api.customer_orders(
                int(customer_id), actor=actor, status=used["status"] or None,
                date_from=used["from"], date_to=used["to"], page=number, per_page=size,
            ),
        )
        if error:
            return self._offline(error, code, **empty)

        rows = [people.order_row(item) for item in self._rows(payload)]
        return self._online(
            items=rows,
            meta=people.page_meta(self._meta_of(payload), page=number, per_page=size,
                                  rows=len(rows)),
            used=used,
        )

    async def subscriptions(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        """Müşterinin abonelikleri. **Denetlenir.** Sayfalanmaz.

        Bir müşterinin abonelik sayısı tek hanelidir (sözleşme); bu yüzden
        `meta` dörtlüsü BEKLENMEZ ve sayfalayıcı çizilmez. Boş bir `meta`
        yollamak, istemciye olmayan bir sayfalayıcı çizdirirdi.
        """
        payload, error, code = await self._read(
            "subscriptions", actor=actor, customer_id=customer_id,
            call=lambda: self._api.customer_subscriptions(int(customer_id), actor=actor),
        )
        if error:
            return self._offline(error, code, items=[])
        return self._online(items=[people.subscription_row(item)
                                   for item in self._rows(payload)])

    async def addresses(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        """Adres defteri. **Denetlenir. SALT OKUNUR.**

        Adres yazan bir uç YOKTUR ve burada uydurulmaz: adres siparişe
        kopyalanıyor, bağlanmıyor ve defteri panelden düzenlemek geçmiş
        siparişlerin adresini değiştirmez — yönetici değiştirdiğini sanır.
        Adresi müşteri kendi uygulamasından yönetir.
        """
        payload, error, code = await self._read(
            "addresses", actor=actor, customer_id=customer_id,
            call=lambda: self._api.customer_addresses(int(customer_id), actor=actor),
        )
        if error:
            return self._offline(error, code, items=[], read_only=True)
        return self._online(items=[people.address_row(item) for item in self._rows(payload)],
                            read_only=True)

    async def sms(self, customer_id: int, *, actor: str, page: int = 1,
                  per_page: int = 0) -> dict[str, Any]:
        """Müşteriye giden SMS'lerin gönderim kaydı (sayfalı). **Denetlenir.**

        UÇ BAŞKA BİR ALANDA: `control/sms/log`, `control/customers/*` altında
        değil. Bunun iki sonucu var ve ikisi de kullanıcıya söylenir:

          1. SUNUCU BU OKUMA İÇİN `customer.read` SATIRI YAZMAZ. Yerel iz tek
             kayıttır ve bu yüzden `action` alanı `sms.read`'tir — aynı adı
             kullansaydık iki defteri karşılaştıran biri, sunucuda karşılığı
             olmayan satırları "sunucu kayıp vermiş" diye okurdu. Ayrım
             raporlanmıştır.
          2. Uç `actor` İSTEMEZ; yine de aktör kapısından geçiyoruz, çünkü
             yerel iz aktörsüz yazılırsa kimseyi işaret etmez.

        TELEFON SUNUCUDA MASKELİ, GÖVDE 120 KARAKTERDE KIRPIK gelir (`sms.md`):
        gönderim kaydı bir iletişim defterine dönüşmemeli. Panel bunu OLDUĞU
        GİBİ gösterir ve ikinci bir maske uygulamaz.
        """
        size = people.clean_per_page(per_page, self._sms_page_size)
        number = max(1, people.as_int(page, 1))
        used = {"customer_id": int(customer_id), "page": number, "per_page": size}
        empty = {"items": [], "meta": people.page_meta({}, page=number, per_page=size, rows=0),
                 "used": used}

        payload, error, code = await self._read(
            "sms", actor=actor, customer_id=customer_id, filters=used,
            action=people.SMS_READ_ACTION,
            call=lambda: self._api.sms_log(customer_id=int(customer_id), page=number,
                                           per_page=size),
        )
        if error:
            return self._offline(error, code, **empty)

        rows = [people.sms_row(item) for item in self._rows(payload)]
        meta = self._meta_of(payload)
        return self._online(
            items=rows,
            meta=people.page_meta(meta, page=number, per_page=size, rows=len(rows)),
            # `segment_total` sözleşmede `meta` içinde geliyor ve maliyet
            # sorusunun cevabı. Gelmezse -1 ("bilinmiyor") — sıfır yazmak
            # "hiç segment harcanmadı" demek olurdu.
            segment_total=people.as_int(meta.get("segment_total"), -1),
            used=used,
        )

    # ================================================================= yazma

    async def update(self, customer_id: int, *, fields: dict[str, Any], reason: str,
                     actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """İletişim bilgileri + kurum etiketleri — KISMİ yazma.

        YAZILABİLİR ALANLAR SÖZLEŞMEDE SINIRLIDIR ve liste `people`ta durur.
        E-posta, parola, hesap türü ve hesap durumu gönderilirse istek TÜMÜYLE
        reddedilir ve geçide HİÇ GİTMEZ — her biri kendi gerekçesiyle, çünkü
        yönetici NEDEN olmadığını okumalı, yoksa aynı isteği başka bir adla
        tekrar dener.

        DENETİM İZİ ESKİ VE YENİ DEĞERİ BİRLİKTE YAZAR ama telefonlar
        MASKELENİR (sözleşme `PATCH` bölümü, `people.change_log`).
        """
        guard = people.reason_error(reason) or people.actor_error(actor)
        if guard:
            return self._blocked(guard)
        guard = people.patch_error(fields)
        if guard:
            return self._blocked(guard)
        body, guard = people.clean_patch(fields)
        if guard:
            return self._blocked(guard)

        current, error, code = await self._fresh(customer_id, actor=actor, scope="detail")
        if current is None:
            return {"ok": False, "error": error, "code": code}

        changes = people.change_log(current, body)
        if not changes:
            # DEĞİŞMEYEN BİR YAZMA GÖNDERİLMEZ. Gönderilseydi sunucu denetim
            # izine "güncellendi" diye bir satır yazar, o satır hiçbir şeyi
            # anlatmaz ve gerçek değişiklikleri arayan kişi onun içinde
            # kaybolurdu.
            return self._blocked(
                "Hiçbir alan değişmedi: gönderilen değerler kayıttakiyle aynı. "
                "Değişiklik yapmadan gerekçe yazmak, denetim izine anlamsız bir "
                "satır eklerdi.")

        dry = self._dry(dry_run)
        detail = {"customer_id": int(customer_id), "changes": changes}
        await self._record(action="customer.update", reason=reason, actor=actor,
                           result=people.TRIED, customer_id=customer_id, detail=detail)
        try:
            payload = await self._api.update_customer(int(customer_id), reason=reason,
                                                      actor=actor, dry_run=dry, **body)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="customer.update", reason=reason, actor=actor,
                               result=people.FAILED, customer_id=customer_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("müşteri güncellenemedi", customerId=customer_id,
                              error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}

        result = await self._done("customer.update", payload, reason=reason, actor=actor,
                                  dry=dry, customer_id=customer_id, detail=detail,
                                  extra={"changed": [item["field"] for item in changes]})
        # SUNUCUNUN `changed` LİSTESİ BİZİMKİNİ EZER. İkisi normalde aynıdır ama
        # ayrıldıklarında doğru olan sunucununkidir: bizim listemiz "ne
        # göndermeye çalıştık", sunucununki "ne yazıldı" demektir. Sunucu bir
        # alanı reddedip ötekini yazsaydı, ekranda bizim listemiz dursaydı
        # yönetici yazılmamış bir alanı yazılmış sanırdı.
        served = payload.get("changed") if isinstance(payload, dict) else None
        if isinstance(served, list):
            result["changed"] = [str(item) for item in served]
        return result

    async def disable(self, customer_id: int, *, reason: str, actor: str,
                      dry_run: bool | None = None,
                      allow_destructive: bool = False) -> dict[str, Any]:
        """Hesabı kapatır. **YIKICI — ayrı izin ister.**

        Kapalı hesap giriş yapamaz ve sipariş veremez. HESAP KAPATMAK VERİ
        SİLMEZ: kayıt durur, geçmiş siparişler müşterisine bağlı kalır.

        İZİN İKİ KEZ DENETLENİR (K9 — çift kapı): uç noktada ve burada. Uç
        noktanın izni bir gün gevşetilse bile ikinci kapı durur.

        AKTİF ABONELİK ENGEL DEĞİL, UYARIDIR (sözleşme): abonelik üretimi
        durmaz — kural hesaba değil aboneliğe bağlıdır — ve yönetici bunu
        BİLMELİ. Bu yüzden taze okumadan gelen `active_subscription_count`
        yanıta konur; sunucunun kendi `warnings` bloğu da olduğu gibi taşınır.

        ZATEN KAPALIYSA istek yine gönderilir ve `ok: true` döner (sözleşme
        `409` vermiyor); yanıttaki `already` alanı ekranın "kapatıldı" yerine
        "zaten kapalıydı" demesini sağlar.
        """
        guard = people.reason_error(reason) or people.actor_error(actor)
        if guard:
            return self._blocked(guard)
        if not allow_destructive:
            # ÇİFT KAPI. Arayüzde düğmeyi gizlemek yetkilendirme değildir (K9).
            await self._record(action="customer.disable", reason=reason, actor=actor,
                               result=people.BLOCKED, customer_id=customer_id,
                               detail={"customer_id": int(customer_id),
                                       "error": "izin yok"})
            return self._blocked(
                "Hesap kapatma ayrı bir yetki ister (`bld_customers.disable`): kapalı "
                "bir hesap giriş yapamaz ve sipariş veremez, sonucu ilk fark eden "
                "çoğu zaman müşterinin kendisi olur.")

        current, error, code = await self._fresh(customer_id, actor=actor, scope="detail")
        if current is None:
            return {"ok": False, "error": error, "code": code}

        dry = self._dry(dry_run)
        already = not current["status"]
        active_subs = current["stats"]["active_subscription_count"]
        detail = {"customer_id": int(customer_id), "name": current["full_name"],
                  "was_active": current["status"], "active_subscriptions": active_subs}
        await self._record(action="customer.disable", reason=reason, actor=actor,
                           result=people.TRIED, customer_id=customer_id, detail=detail)
        try:
            payload = await self._api.disable_customer(int(customer_id), reason=reason,
                                                       actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="customer.disable", reason=reason, actor=actor,
                               result=people.FAILED, customer_id=customer_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("hesap kapatılamadı", customerId=customer_id,
                              error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}

        result = await self._done("customer.disable", payload, reason=reason, actor=actor,
                                  dry=dry, customer_id=customer_id, detail=detail,
                                  extra={"already": already,
                                         "active_subscriptions": active_subs})
        # OLAY YALNIZ GERÇEKTEN KAPANDIYSA. Kuru provada BLD'de hiçbir şey
        # değişmedi; dinleyicileri "hesap kapatıldı" diye uyandırmak yalan
        # olurdu. Zaten kapalıysa da yayınlanmaz: yeni bir olay olmadı.
        if not result["dry_run"] and not already:
            await self._announce(DISABLED_EVENT, {
                "customerId": int(customer_id),
                "name": current["full_name"],
                "reason": people.text(reason),
                "actor": people.text(actor),
            })
        return result

    async def enable(self, customer_id: int, *, reason: str, actor: str,
                     dry_run: bool | None = None) -> dict[str, Any]:
        """Hesabı yeniden açar.

        AYRI İZİN İSTEMEZ (`manage` yeter): kapatmak yıkıcı, açmak onarıcıdır.
        Açmayı da üçüncü anahtara bağlamak, yanlışlıkla kapatılmış bir hesabı
        düzeltebilecek kişi sayısını azaltırdı — yani hatayı uzatırdı.

        GEREKÇE YİNE ZORUNLU: "neden açıldı" sorusu da denetim izinin sorusudur.
        """
        guard = people.reason_error(reason) or people.actor_error(actor)
        if guard:
            return self._blocked(guard)

        current, error, code = await self._fresh(customer_id, actor=actor, scope="detail")
        if current is None:
            return {"ok": False, "error": error, "code": code}

        dry = self._dry(dry_run)
        already = current["status"]
        detail = {"customer_id": int(customer_id), "name": current["full_name"],
                  "was_active": current["status"]}
        await self._record(action="customer.enable", reason=reason, actor=actor,
                           result=people.TRIED, customer_id=customer_id, detail=detail)
        try:
            payload = await self._api.enable_customer(int(customer_id), reason=reason,
                                                      actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="customer.enable", reason=reason, actor=actor,
                               result=people.FAILED, customer_id=customer_id,
                               detail={**detail, "error": str(failure)})
            self._log.warning("hesap açılamadı", customerId=customer_id, error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}
        return await self._done("customer.enable", payload, reason=reason, actor=actor,
                                dry=dry, customer_id=customer_id, detail=detail,
                                extra={"already": already})

    async def _done(self, action: str, payload: Any, *, reason: str, actor: str, dry: bool,
                    customer_id: int, detail: dict[str, Any],
                    extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Başarılı geçit çağrısının ortak sonu: iz + yanıt zarfı.

        `dry_run` YANITTAN OKUNUR, isteğe yazdığımızdan değil: bir kurulum
        provayı geçidin ayarından açarsa ekran "yapıldı" DEMEMELİ. Sunucu kuru
        provada `would` bloğu döndürüyor ve panel onu gösterir.

        `warnings` OLDUĞU GİBİ TAŞINIR. Sözleşme aktif abonelikli bir hesabın
        kapatılmasında `{"code": "active_subscriptions", "subscription_ids":
        [...]}` döndürüyor; ayıklamak, yarın eklenecek bir uyarı kodunu
        sessizce düşürürdü.
        """
        body = payload if isinstance(payload, dict) else {}
        applied_dry = people.as_bool(body.get("dry_run")) if "dry_run" in body else dry
        await self._record(action=action, reason=reason, actor=actor,
                           result=people.DRY if applied_dry else people.DONE,
                           customer_id=customer_id,
                           detail={**detail, "audit_id": body.get("audit_id")})
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        would = body.get("would") if isinstance(body.get("would"), dict) else {}
        warnings = body.get("warnings") if isinstance(body.get("warnings"), list) else []
        return {"ok": True, "error": "", "code": "", "dry_run": applied_dry,
                "audit_id": body.get("audit_id"), "data": data, "would": would,
                "warnings": warnings, **(extra or {})}

    # ========================================================== yerel kayıtlar

    async def access_log(self, *, customer_id: int = 0, actor: str = "",
                         limit: int = 0) -> dict[str, Any]:
        """YEREL KVKK erişim izi — ağa çıkmaz.

        "Kim, ne zaman, kimin kaydını açtı" sorusunun buradaki cevabı. Sunucuda
        da bir defter var (`control/audit`, `customer.read`) ve ikisi AYNI
        SORUYA cevap vermez: sunucununki yalnız kendisine ULAŞAN okumayı bilir,
        buradaki DENENEN okumayı da bilir.

        BU OKUMANIN KENDİSİ İZE YAZILMAZ. Yazılsaydı defteri açan her kişi
        deftere bir satır eklerdi ve sonsuz bir kuyruk doğardı; üstelik bu
        tablo müşteri verisi taşımıyor — yalnız kim neye baktığını taşıyor.
        """
        count = max(1, min(1000, people.as_int(limit, 0) or self._access_log_limit))
        clauses: list[str] = []
        params: list[Any] = []
        if customer_id:
            clauses.append("customer_id = ?")
            params.append(int(customer_id))
        if people.text(actor):
            clauses.append("actor = ?")
            params.append(people.text(actor))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(count)

        try:
            rows = await self._store.fetch_all(
                f"SELECT id, actor, action, scope, customer_id, filters, result, error, "
                f"created_at FROM {self._access}{where} ORDER BY id DESC LIMIT ?",
                tuple(params),
            )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("erişim izi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": []}
        return {"ok": True, "connected": True, "error": "",
                "items": [self._decode(dict(row), "filters") for row in rows]}

    async def audit_trail(self, *, customer_id: int = 0, limit: int = 0) -> dict[str, Any]:
        """Bu ekrandan yapılan YAZMA DENEMELERİ — yerel tablo, ağa çıkmaz.

        `result` sütunu "denendi"de kalmış bir satır, isteğin gidip gitmediği
        BİLİNMEYEN bir denemedir; sunucunun kendi defteri o satırı hiç bilmez.
        Panel bunu ayrı bir tonla gösterir.
        """
        count = max(1, min(500, people.as_int(limit, 0) or self._audit_limit))
        where = " WHERE customer_id = ?" if customer_id else ""
        params: tuple[Any, ...] = (int(customer_id), count) if customer_id else (count,)
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, customer_id, action, reason, actor, result, detail, "
                f"created_at FROM {self._audit}{where} ORDER BY id DESC LIMIT ?",
                params,
            )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yazma izi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": []}
        return {"ok": True, "connected": True, "error": "",
                "items": [self._decode(dict(row), "detail") for row in rows]}

    @staticmethod
    def _decode(row: dict[str, Any], key: str) -> dict[str, Any]:
        """JSON sütununu çözer; bozuksa BOŞ sözlük ve satır yine döner.

        Çözülemeyen bir satır yüzünden bütün defteri gizlemek, denetim izinin
        işini yapamaması demekti.
        """
        try:
            row[key] = json.loads(row.get(key) or "{}")
        except (TypeError, ValueError):
            row[key] = {}
        return row

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler BLD'Yİ ETKİLEMEZ; yalnız bu ekranın açılışta ne gösterdiğini
        belirler. Bu yüzden yazmaları gerekçe istemez ve `view` izniyle yapılır
        — uzak sistemde hiçbir şey değişmiyor ve KVKK erişim izine de satır
        düşmüyor.
        """
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))
        return {
            "page_size": people.clean_per_page(stored.get("page_size"), self._page_size),
            "status_filter": people.clean_status(stored.get("status_filter")),
            "sort": people.clean_sort(stored.get("sort")),
            "direction": people.clean_direction(stored.get("direction")),
        }

    async def save_prefs(self, values: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Ekran tercihini yazar. TANINMAYAN ANAHTAR REDDEDİLİR.

        Sessizce yutulan bir tercih, kaydettiğini sanan kullanıcıya her
        açılışta eski ekranı gösterirdi.
        """
        if not isinstance(values, dict) or not values:
            return self._blocked("En az bir tercih gönderilmeli.")
        unknown = sorted(key for key in values if key not in PREF_KEYS)
        if unknown:
            return self._blocked(
                f"Tanınmayan tercih: {', '.join(unknown)}. "
                f"Yazılabilenler: {', '.join(PREF_KEYS)}.")

        cleaners = {
            "page_size": lambda value: str(people.clean_per_page(value, self._page_size)),
            "status_filter": lambda value: people.clean_status(value),
            "sort": lambda value: people.clean_sort(value),
            "direction": lambda value: people.clean_direction(value),
        }
        try:
            for key, raw in values.items():
                await self._store.execute(
                    f"INSERT INTO {self._prefs} (key, value, actor, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, actor = excluded.actor, "
                    "updated_at = excluded.updated_at",
                    (key, cleaners[key](raw), people.text(actor), people.now_iso()),
                )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("tercih yazılamadı", error=str(failure))
            return {"ok": False, "error": self._fail(failure), "code": ""}
        return {"ok": True, "error": "", "code": "", **await self.prefs()}
