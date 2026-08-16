"""Yapılandırma sekmesinin saf haritası — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN BEYAZ LİSTE. Bagisto'nun ayar ağacı bu kurulumda **345 alan** taşıyor;
`GET /api/admin/configuration/menu?include_values=1` yanıtı 158 KB (yeniden
ölçüldü 2026-08-16; bir dönem burada 344 alan / 156 KB yazıyordu — sayı
mağazaya eklenti/alan girdikçe kayar, bu yüzden gerekçe sayıya değil BÜYÜKLÜK
MERTEBESİNE dayanır). Tamamını ekrana dökmek işletmeye yardımcı olmaz: aradığı
beş ayarı üç yüz küsur satırın içinde kaybeder ve yanlışlıkla ödeme
sağlayıcısının test kipini açar. Bu dosya işletmenin gerçekten dokunduğu
alanları adıyla sayar; ağacın geri kalanı okunur ama ekrana ÇIKMAZ.

ANAHTARLAR CANLIDAN ÖĞRENİLDİ, KODDAN VARSAYILMADI (2026-08-13). İki tuzak
canlıda ortaya çıktı:

  · `general.content.shop_information.*` ve `general.content.maintenance_mode.*`
    BU KURULUMDA YOKTUR. Bagisto 2.4.8 mağaza kimliğini `sales.shipping.origin`
    altına, bakım modunu ise `core_config` yerine SATIŞ KANALI kaydına
    (`isMaintenanceOn`) taşımış. Eski adlara yazmak `core_config` içinde
    hiçbir şeyin okumadığı bir satır açar ve kullanıcı ayarı değiştirdiğini
    sanır — sessiz başarısızlık.
  · Alan kodundan slug ÇIKARILMAZ. `sales.order_settings.reorder.admin`
    içinde slug iki, `general.general.locale_options.weight_unit` içinde üç
    parçalıdır. Slug her zaman ağacın DÜĞÜM ANAHTARINDAN okunur.

KANAL SÜZGECİ KOD İSTER, KİMLİK DEĞİL — SİPARİŞ UCUNUN TAM TERSİ (2026-08-13
ölçüldü, 2026-08-16 CANLIDA YENİDEN DOĞRULANDI: kural aynı, yalnız sayılar
kaydı). `/api/admin/configuration/menu?include_values=1&locale=tr` için:

    channel=default  → 345 alan, etkin değerlerle       (DOĞRU)
    channel=1        → 345 alan, 53 alan varsayılana düşmüş
    channel=zzzyok   → channel=1 ile BİREBİR AYNI yanıt (md5 eşit)

Yani kanal KİMLİĞİ göndermek, olmayan bir kanal göndermekle aynı şeydir ve
HATA VERMEZ: `sales.payment_methods.kuveytturk.active` `"1"` yerine `"False"`,
`kuveytturk.title` mağazadaki başlık yerine ilan edilen varsayılan gelir.
Ekran o zaman saklanmış bir ayara "varsayılan" rozeti takar, kullanıcı
"Varsayılana dön"e basıp hiçbir şeyin değişmediğini görür ve en kötüsü,
YANLIŞ KANALDAN OKUNMUŞ değerin üzerine yazar.

`store_orders` bunun TERSİNİ yazıyor (`/api/admin/orders?channel=` kimlik
ister, kod göndermek listeyi boşaltır). İki ucun kuralı farklıdır; birini
diğerine benzetmek için "düzeltme" yapılmamalıdır. Bu yüzden bu modül her
zaman kanal KODUNU (`config/default.yaml` → `channel: "default"`, ya da aynı
biçimdeki yerel tercih) gönderir ve testi bunu kilitler.

KAYNAK ROZETİ NE SÖYLER, NE SÖYLEMEZ. Menü ucu her alan için hem `default`
(mağazanın ilan ettiği varsayılan) hem `value` (etkin değer) veriyor. Bagisto
"bu değerin `core_config` içinde satırı var mı" sorusunu API'den
CEVAPLAMIYOR: değer yoksa varsayılana düşüyor ve ikisi aynı görünüyor. Bu
yüzden rozet dürüst olanı söyler — değer varsayılanla aynı mı, değil mi:

    unset    → mağaza da varsayılan da boş; ayar hiç tanımlanmamış
    default  → etkin değer ilan edilen varsayılanla AYNI
    stored   → etkin değer varsayılandan FARKLI, yani mağazada saklanıyor

`stored`/`default` ayrımı "satır var mı"yı değil "varsayılandan sapmış mı"yı
anlatır; ekran bunu aynen yazar. Uydurulmuş bir kesinlik göstermek, sonradan
"ama ekran veritabanından diyordu" demeye yol açardı.
"""

from __future__ import annotations

from typing import Any

#: Bu ekranda düzenlenebilen alan tipleri. Beyaz listedeki bir alan mağazada
#: başka bir tiple ilan edilmişse salt okunur gösterilir: `select`in
#: seçeneklerini bilmeden metin yazmak geçersiz değer üretir.
EDITABLE_TYPES = ("boolean", "text", "textarea", "number")

#: Beyaz listeye yanlışlıkla girse bile değeri ne EKRANA GİDER ne YAZILIR.
#: Sanal POS parolası ve kargo API belirteci bu tiplerle ilan ediliyor; geçit
#: sırları maskeliyor ama bu ekranın onları hiç istememesi daha doğru.
SECRET_TYPES = ("password", "image", "file")

#: Mağaza tipinden panel alan tipine.
FORM_KINDS = {
    "boolean": "checkbox",
    "text": "text",
    "textarea": "textarea",
    "number": "number",
}

#: Kapatılması PARA AKIŞINI DURDURAN anahtarlar. Ekran bunları kapatan bir
#: kaydetmede onay metnini sertleştirir ve hangi yöntemin kararacağını sayar.
CRITICAL_CODES = frozenset({
    "sales.payment_methods.kuveytturk.active",
    "sales.payment_methods.kuveytturk.go_live",
    "sales.payment_methods.moneytransfer.active",
    "sales.payment_methods.cashondelivery.active",
    "sales.carriers.free.active",
    "sales.carriers.flatrate.active",
    "sales.carriers.geliver.active",
    "sales.carriers.geliver.go_live",
})

#: Tek kaydetmede kabul edilen en çok alan. Beyaz liste zaten ~30 alan; daha
#: fazlası ekranın gönderdiği bir gövde değil, biçimi bozuk bir istektir.
MAX_CHANGES = 60

#: Gruplar. Her alan: (kod, ekran etiketi, ipucu).
#: Etiketler EKRANIN kendi etiketidir, mağazanınki değil: mağaza sekiz ayrı
#: yerde "Başlık" ve "Durum" diyor; arama kutusu etiketlerde aradığı için
#: "KuveytTürk" yazan biri o satırı bulabilmeli.
GROUPS: tuple[dict[str, Any], ...] = (
    {
        "key": "identity",
        "title": "Mağaza kimliği",
        "note": "Kargo etiketinde, faturada ve iletişim sayfasında görünür. Bu kurulumda "
                "mağaza kimliği `sales.shipping.origin` altındadır — Bagisto 2.4.8'de "
                "ayrı bir \"mağaza bilgileri\" bölümü yoktur.",
        "fields": (
            ("sales.shipping.origin.store_name", "Mağaza adı",
             "Kargo gönderilerinde gönderici adı olarak kullanılır."),
            ("sales.shipping.origin.contact", "Mağaza telefonu", ""),
            ("emails.configure.email_settings.contact_email", "İletişim e-postası",
             "Müşteri iletişim formu buraya düşer."),
            ("sales.shipping.origin.address", "Adres", ""),
            ("sales.shipping.origin.city", "Şehir", ""),
            ("sales.shipping.origin.zipcode", "Posta kodu", ""),
            ("sales.shipping.origin.vat_number", "VKN / KDV numarası", ""),
        ),
    },
    {
        "key": "payment",
        "title": "Ödeme yöntemleri",
        "note": "Kapatılan yöntem ödeme adımında GÖRÜNMEZ; sepetteki müşteri o yöntemle "
                "ödeyemez.",
        "fields": (
            ("sales.payment_methods.kuveytturk.active", "KuveytTürk sanal POS — açık",
             "Kapalıyken kredi kartıyla ödeme alınmaz."),
            ("sales.payment_methods.kuveytturk.go_live", "KuveytTürk — müşteriye açık (canlı)",
             "İkinci kapı: yöntem açık olsa da bu kapalıyken vitrinde görünmez."),
            ("sales.payment_methods.kuveytturk.title", "KuveytTürk — ödeme adımı başlığı", ""),
            ("sales.payment_methods.moneytransfer.active", "Havale / EFT — açık", ""),
            ("sales.payment_methods.moneytransfer.title", "Havale / EFT — başlık", ""),
            ("sales.payment_methods.cashondelivery.active", "Kapıda ödeme — açık", ""),
            ("sales.payment_methods.cashondelivery.title", "Kapıda ödeme — başlık", ""),
        ),
    },
    {
        "key": "shipping",
        "title": "Kargo yöntemleri",
        "note": "Ücretsiz kargo eşiği Geliver entegrasyonunun ayarıdır: sepet tutarı bu "
                "değeri geçince kargo ücreti alınmaz. 0 yazmak eşiği KAPATMAZ, sıfır lira "
                "üstü her sepeti ücretsiz yapar.",
        "fields": (
            ("sales.carriers.geliver.active", "Geliver kargo — entegrasyon kurulu", ""),
            ("sales.carriers.geliver.go_live", "Geliver kargo — müşteriye açık (canlı)", ""),
            ("sales.carriers.geliver.title", "Geliver kargo — ödeme adımı başlığı", ""),
            ("sales.carriers.geliver.free_shipping_threshold", "Ücretsiz kargo eşiği (TL)",
             "Sepet tutarı bu değeri geçerse kargo ücretsizdir."),
            ("sales.carriers.free.active", "Ücretsiz kargo yöntemi — açık", ""),
            ("sales.carriers.free.title", "Ücretsiz kargo yöntemi — başlık", ""),
            ("sales.carriers.flatrate.active", "Sabit ücretli kargo — açık", ""),
            ("sales.carriers.flatrate.title", "Sabit ücretli kargo — başlık", ""),
        ),
    },
    {
        "key": "stock",
        "title": "Stok",
        "note": "Kritik eşik vitrini etkiler: stok bu değerin altına inen ürün \"tükendi\" "
                "sayılır.",
        "fields": (
            ("catalog.inventory.stock_options.out_of_stock_threshold", "Kritik stok eşiği",
             "Stok bu değere ya da altına inince ürün tükenmiş sayılır."),
            ("catalog.inventory.stock_options.back_orders",
             "Tükenen üründe geri siparişe izin ver",
             "Açıkken stok bitse de sipariş alınır; kapalıyken ürün sepete eklenemez."),
        ),
    },
    {
        "key": "checkout",
        "title": "Sepet ve ödeme adımı",
        "note": "",
        "fields": (
            ("sales.checkout.shopping_cart.allow_guest_checkout", "Misafir alışverişine izin ver",
             "Kapalıyken müşteri ödeme adımına geçmek için hesap açmak zorundadır."),
            ("sales.order_settings.minimum_order.enable", "Minimum sepet tutarı uygulansın", ""),
            ("sales.order_settings.minimum_order.minimum_order_amount", "Minimum sepet tutarı",
             "Yukarıdaki anahtar kapalıyken bu değer hiçbir şey yapmaz."),
        ),
    },
    {
        "key": "email",
        "title": "E-posta",
        "note": "Sipariş, fatura ve kargo bildirimleri bu adla ve adresle gider. SMTP "
                "sunucusu ve parolası bu ekranda GÖSTERİLMEZ.",
        "fields": (
            ("emails.configure.email_settings.sender_name", "Gönderen adı", ""),
            ("emails.configure.email_settings.sender_email", "Gönderen e-posta adresi",
             "Alan adı doğrulanmamış bir adres yazmak postaları çöp kutusuna düşürür."),
            ("emails.configure.email_settings.admin_email", "Yönetici e-posta adresi",
             "Yeni sipariş ve iade bildirimleri buraya gelir."),
        ),
    },
)

#: Kod → (grup anahtarı, etiket). Yazma sırasında beyaz liste denetimi bununla.
FLAT: dict[str, tuple[str, str]] = {
    code: (group["key"], label)
    for group in GROUPS
    for code, label, _hint in group["fields"]
}

MISSING_REASON = (
    "Bu anahtar mağazanın ayar ağacında ilan edilmemiş. Yazılsaydı `core_config` içinde "
    "hiçbir şeyin okumadığı bir satır açılır, ayarı değiştirdiğinizi sanırdınız ve mağaza "
    "eskisi gibi çalışmaya devam ederdi; alan bu yüzden salt okunur."
)


def norm(value: Any) -> str:
    """Karşılaştırma için metin normalizasyonu.

    TUZAK: aynı ayar üç biçimde gelebiliyor — `allow_guest_checkout` etkin
    değeri `"1"` (metin) ama ilan edilen varsayılanı `1` (sayı);
    `sales.carriers.geliver.active` ise `False` (mantıksal) geliyor. Ham
    karşılaştırma bu üçünü farklı sanır ve rozet "veritabanından" derdi.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value).strip()


def source_of(value: Any, default: Any) -> str:
    """Kaynak rozeti: `unset` · `default` · `stored`. Dosya başlığındaki uyarıyı oku."""
    if value is None:
        return "unset" if default is None else "default"
    return "default" if norm(value) == norm(default) else "stored"


def declared_fields(tree: Any) -> dict[str, dict[str, Any]]:
    """Ayar ağacını `kod → {slug, type, title, default, value}` düzüne indirger.

    `slug` DÜĞÜM ANAHTARINDAN alınır; koddan türetilmez (dosya başlığındaki
    ikinci tuzak). Burada olmayan bir kod mağazada ilan edilmemiştir ve
    YAZILMAZ.
    """
    out: dict[str, dict[str, Any]] = {}

    def walk(nodes: Any) -> None:
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            slug = str(node.get("key") or "")
            for field in node.get("fields") or []:
                if not isinstance(field, dict):
                    continue
                code = str(field.get("code") or "")
                if not code:
                    continue
                out[code] = {
                    "code": code,
                    "slug": slug,
                    "type": str(field.get("type") or ""),
                    "title": str(field.get("title") or ""),
                    "default": field.get("default"),
                    "value": field.get("value"),
                }
            walk(node.get("children"))

    walk(tree)
    return out


def field_view(code: str, label: str, hint: str,
               declared: dict[str, Any] | None) -> dict[str, Any]:
    """Tek alanın ekran görünümü. Bulunmayan/tehlikeli alan salt okunur döner."""
    view: dict[str, Any] = {
        "code": code, "label": label, "hint": hint, "slug": "", "storeLabel": "",
        "kind": "text", "value": "", "default": "", "hasDefault": False,
        "source": "unset", "found": False, "editable": False, "reason": "",
        "critical": code in CRITICAL_CODES,
    }
    if declared is None:
        view["reason"] = MISSING_REASON
        return view

    kind = declared["type"]
    view["found"] = True
    view["slug"] = declared["slug"]
    view["storeLabel"] = declared["title"]

    if kind in SECRET_TYPES:
        # Değer ne gönderilir ne yazılır; sırrın ekrana uğraması bile gereksiz.
        view["reason"] = (f"Mağaza bu alanı `{kind}` tipiyle ilan etmiş: sır ya da dosya "
                          "taşıyor, bu ekranda gösterilmez ve yazılmaz.")
        return view

    view["value"] = norm(declared["value"])
    view["default"] = norm(declared["default"])
    view["hasDefault"] = declared["default"] is not None
    view["source"] = source_of(declared["value"], declared["default"])

    if kind not in EDITABLE_TYPES:
        view["reason"] = (f"Mağaza bu alanı `{kind}` tipiyle ilan etmiş; geçerli "
                          "seçenekleri bilinmeden yazmak bozuk değer üretirdi.")
        return view

    view["kind"] = FORM_KINDS[kind]
    view["editable"] = True
    return view


def build_view(declared: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Beyaz listeyi ekranın beklediği grup listesine çevirir."""
    return [
        {
            "key": group["key"],
            "title": group["title"],
            "note": group["note"],
            "fields": [field_view(code, label, hint, declared.get(code))
                       for code, label, hint in group["fields"]],
        }
        for group in GROUPS
    ]


def to_store(raw: Any, kind: str) -> str:
    """Panel değerini mağazanın beklediği METNE çevirir.

    `core_config` her şeyi metin tutuyor: mantıksal alanlar "1"/"0", sayılar
    nokta ayraçlı. Mantıksal alana `true` göndermek Bagisto'da "true" METNİ
    olarak saklanır ve `(bool)` dönüşümünde her zaman doğru çıkar — kapatmak
    isteyen kullanıcı yöntemi açık bırakırdı.
    """
    if kind == "boolean":
        return "0" if norm(raw) in ("", "0", "false", "off", "hayır") else "1"
    if kind == "number":
        text = norm(raw).replace(",", ".")
        return text
    return norm(raw)


def resolve_writes(changes: Any,
                   declared: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, str]],
                                                                 list[dict[str, str]]]:
    """Değişiklikleri slug'a göre kümeler; yazılamayanı GEREKÇESİYLE ayırır.

    Üç kapı: beyaz listede mi, mağazada ilan edilmiş mi, tipi yazılabilir mi.
    Üçünden geçmeyen alan sessizce atılmaz — çağıran ekrana neden yazılmadığını
    söyler.
    """
    writes: dict[str, dict[str, str]] = {}
    skipped: list[dict[str, str]] = []

    for code, raw in (changes or {}).items():
        code = str(code)
        spec = FLAT.get(code)
        if spec is None:
            skipped.append({"code": code, "label": code,
                            "reason": "Bu alan Yapılandırma ekranının listesinde yok."})
            continue
        label = spec[1]
        field = declared.get(code)
        if field is None:
            skipped.append({"code": code, "label": label, "reason": MISSING_REASON})
            continue
        kind = field["type"]
        if kind in SECRET_TYPES or kind not in EDITABLE_TYPES:
            skipped.append({"code": code, "label": label,
                            "reason": f"Mağaza bu alanı `{kind}` tipiyle ilan etmiş; bu "
                                      "ekrandan yazılmaz."})
            continue
        writes.setdefault(field["slug"], {})[code] = to_store(raw, kind)

    return writes, skipped


def going_dark(changes: Any, declared: dict[str, dict[str, Any]]) -> list[str]:
    """Bu kaydetmeyle KAPANACAK ödeme/kargo yöntemlerinin etiketleri.

    Ekran onay metnini bununla sertleştirir: "üç alan değişti" demek, kredi
    kartıyla ödemenin kapandığını söylemez.
    """
    out: list[str] = []
    for code, raw in (changes or {}).items():
        code = str(code)
        if code not in CRITICAL_CODES or code not in declared:
            continue
        if to_store(raw, "boolean") == "0":
            out.append(FLAT[code][1])
    return sorted(out)
