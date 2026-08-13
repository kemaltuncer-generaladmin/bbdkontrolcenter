"""Denetim kaydının saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın işi tek bir şey: İKİ AYRI KAYNAĞI tek bir zaman
çizgisine dizmek. Kaynaklar birbirine benzemiyor:

  · Uzak `admin_api_audits` — Bagisto'nun kendi denetim tablosu. YALNIZ
    başarılı yazmaları, alan farkıyla birlikte tutar. Gerekçe alanı YOKTUR.
  · Yerel `mod_store_api_audit` — geçidin izi. İstek ÇIKMADAN yazılır; bu
    yüzden reddedilen, zaman aşımına uğrayan ve kuru prova kalan denemeler
    de burada durur. Gerekçe BURADADIR.

Bu iki kaydı birleştirmek üç ayrı tuzak doğurur; hepsinin karşılığı burada bir
fonksiyon ve testlerde adı tuzağı söyleyen bir testtir:

 1. SAAT EKSENİ. Geçit UTC damgası yazar (`...+00:00`), mağaza kendi yerel
    saatini yazar ve çoğu zaman ek bilgi vermez. İkisini ham metin olarak
    sıralamak Türkiye'de üç saatlik bir kayma üretir → `normalize_stamp`.
 2. ÇİFT KAYIT. Başarılı bir yazma HER İKİ kaynakta da vardır. İstek kimliği
    (`X-Bbd-Request-Id`) ikisini birbirine bağlar: uzak satır alan farkını,
    yerel satır gerekçeyi verir → `join_reason` + `local_only`.
 3. ALAN ADI KARARSIZLIĞI. Uzak kaydın alan adları hem BİÇİM hem AD olarak
    değişir. Biçim: Bagisto'nun yönetici zarfı sütunları camelCase'e çeviriyor
    (canlı mağazada doğrulandı — `incrementId`, `grandTotal`, `createdAt`),
    yani beklenen ad `oldValues`/`auditableType`/`ipAddress`'tır; ham sütun adı
    (`old_values`…) yalnız zarfsız bir sürümde gelir. Ad: `old_values`/`before`,
    `auditable_type`/`subject_type` gibi eşdeğerler sürümle değişir. Tek ada bel
    bağlamak ekranı SESSİZCE boşaltır — satır gelir, fark tablosu boş çıkar,
    "Varlık" ve "IP" sütunları "—" dolar → `first_of`.

Ekran SALT OKUNURDUR: burada hiçbir fonksiyon yazma gövdesi üretmez.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime
from typing import Any

#: Sayfa başına kayıt. Ekranın tek istisnası: sunucu tarafı imleçli sayfalama.
DEFAULT_PAGE_SIZE = 100

#: Damgası okunamayan satırın sıralama anahtarı. Boş metin, ayraç '|'
#: rakamlardan büyük olduğu için satırı listenin başına atardı.
NO_STAMP = "0000-00-00T00:00:00"

#: Denetim satırında gövde/ham JSON bu boyutta kırpılır. Denetim izi arşiv
#: değildir; 100 satırlık sayfada kırpılmamış gövde birkaç megabayt eder ve
#: ekran sayfayı çizmeden donar.
RAW_LIMIT = 4_000

#: Fark tablosunda gösterilecek en çok alan. Toplu ayar yazmalarında `after`
#: yüzlerce anahtar taşıyabiliyor; tablo okunmaz hâle geliyordu.
DIFF_LIMIT = 80

#: Değeri MASKELENECEK alan adları (parça eşleşmesi, küçük harfte).
#: Denetim ekranı bir sır sızdırma yüzeyidir: belirteç/parola alanı bir kez
#: kayda düşmüşse, ekranda gösterilmesi onu 20 kişilik personele açar.
SECRET_HINTS = ("token", "password", "secret", "authorization", "api_key",
                "apikey", "pin", "card", "cvv", "iban", "signature")

#: Yıkıcı sayılan eylem parçaları. Renk değil, AYRI BİR SÜZGEÇ anahtarı
#: üretirler: "bugün ne silindi" sorusu bu ekranın en sık sorulan sorusudur.
DESTRUCTIVE_HINTS = ("delete", "remove", "destroy", "cancel", "refund", "revoke",
                     "deactivate", "disable", "restore", "purge", "truncate", "reset",
                     "wipe", "sil", "iptal")

#: Eylem etiketleri. Ekranda İngilizce eylem adı görünmez.
ACTION_LABELS = {
    "create": "Oluşturma",
    "created": "Oluşturma",
    "store": "Oluşturma",
    "update": "Güncelleme",
    "updated": "Güncelleme",
    "delete": "Silme",
    "deleted": "Silme",
    "destroy": "Silme",
    "login": "Giriş",
    "logout": "Çıkış",
    "export": "Dışa aktarma",
    "import": "İçe aktarma",
    "configuration": "Ayar değişikliği",
    "settings": "Ayar değişikliği",
    "cancel": "İptal",
    "refund": "İade",
    "status": "Durum değişikliği",
}

#: HTTP yöntemi → eylem. Uzak kayıtta `action` boşsa yöntemden okunur.
METHOD_ACTIONS = {"POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}

#: Varlık etiketleri ve panel eşlemesi ekranda (panel `TARGETS`) durur; burada
#: yalnız kanonik ad üretilir. Kanonik ad İngilizce ASCII'dir (API yolu gibi).
ENTITY_LABELS = {
    "order": "Sipariş",
    "product": "Ürün",
    "customer": "Müşteri",
    "invoice": "Fatura",
    "refund": "İade",
    "shipment": "Kargo",
    "category": "Kategori",
    "cms_page": "Sayfa",
    "review": "Yorum",
    "cart_rule": "Sepet kuralı",
    "catalog_rule": "Katalog kuralı",
    "tax_rate": "Vergi oranı",
    "tax_category": "Vergi sınıfı",
    "configuration": "Ayar",
    "channel": "Kanal",
    "attribute": "Öznitelik",
    "customer_group": "Müşteri grubu",
    "transaction": "Ödeme işlemi",
    "backup": "Yedek",
    "bundle": "Set",
    "carousel": "Vitrin görseli",
    "notification": "Bildirim",
    "return_request": "Talep",
    "trial_exam": "Deneme sınavı",
    "admin_user": "Yönetici",
    "session": "Oturum",
    # Aşağıdakiler de geçidin YAZMA yollarında geçiyor; etiketi olmayan varlık
    # tabloda ham İngilizce ada ya da "—"ye düşerdi.
    "role": "Rol",
    "attribute_family": "Öznitelik ailesi",
    "campaign": "Kampanya",
    "email_template": "E-posta şablonu",
    "subscriber": "Abone",
    "search_term": "Arama terimi",
    "search_synonym": "Arama eşanlamı",
    "sitemap": "Site haritası",
    "url_rewrite": "Adres yönlendirme",
    "exchange_rate": "Döviz kuru",
    "currency": "Para birimi",
    "locale": "Dil",
    "inventory_source": "Stok kaynağı",
    "theme": "Tema",
    "carrier": "Taşıyıcı",
    "shipping_rate": "Kargo tarifesi",
    "catalog": "Katalog",
    "ai_run": "Yapay zekâ işi",
    "bld_job": "BLD işi",
    "mobile_setting": "Mobil ayar",
}

#: API yolundan varlık çıkarımı. Sıra ÖNEMLİ: ilk eşleşen kazanır, bu yüzden
#: uzun/özel yollar yukarıda durur (`/customers/reviews` ile `/customers`).
#:
#: YOLLAR UYDURULMAZ, GEÇİTTEN OKUNUR. Bu listenin karşılaştığı tek yol,
#: `store_api/backend/client.py` içindeki yollardır: geçit izine (`path`) yazılan
#: değer odur. Canlı mağazada doğrulandı — Bagisto yönetici uçları `/sales/*`,
#: `/promotions/*`, `/settings/taxes/*` ve `/reviews` altında DEĞİLDİR (hepsi
#: 404); doğruları `/orders`, `/invoices`, `/refunds`, `/shipments`,
#: `/transactions`, `/marketing/*`, `/settings/tax-rates`,
#: `/settings/tax-categories` ve `/customers/reviews`'dır. Yanlış desen sessizce
#: boş varlık üretir: tablodaki "Varlık" sütunu "—" dolar ve `[Kayda git]`
#: düğmesi ömür boyu pasif kalır — kimse de bunu hata sanmaz.
PATH_ENTITIES: tuple[tuple[str, str], ...] = (
    (r"/bbd/return-requests", "return_request"),
    (r"/bbd/shipments", "shipment"),
    (r"/bbd/payments", "transaction"),
    (r"/bbd/backups", "backup"),
    (r"/bbd/bundles", "bundle"),
    (r"/bbd/carousel", "carousel"),
    (r"/bbd/notifications", "notification"),
    (r"/bbd/trial-club", "trial_exam"),
    (r"/bbd/bld/orders", "order"),          # `/bbd/bld` deseninden ÖNCE
    (r"/bbd/bld", "bld_job"),
    (r"/bbd/review-requests", "review"),
    (r"/bbd/carriers", "carrier"),
    (r"/bbd/shipping", "shipping_rate"),    # `/bbd/shipments` ile karışmaz
    (r"/bbd/catalog", "catalog"),
    (r"/bbd/mobile", "mobile_setting"),
    (r"/bbd/home", "carousel"),
    (r"/bbd/ai", "ai_run"),
    (r"/catalog/products", "product"),
    (r"/catalog/categories", "category"),
    (r"/catalog/attributes", "attribute"),
    (r"/catalog/families", "attribute_family"),
    (r"/customers/reviews", "review"),
    (r"/customers/groups", "customer_group"),
    (r"/customers/gdpr-requests", "customer"),
    (r"/customers", "customer"),
    (r"/marketing/cart-rules", "cart_rule"),
    (r"/marketing/catalog-rules", "catalog_rule"),
    (r"/marketing/campaigns", "campaign"),
    (r"/marketing/templates", "email_template"),
    (r"/marketing/subscribers", "subscriber"),
    (r"/marketing/search-synonyms", "search_synonym"),
    (r"/marketing/search-terms", "search_term"),
    (r"/marketing/sitemaps", "sitemap"),
    (r"/marketing/url-rewrites", "url_rewrite"),
    (r"/cms/pages", "cms_page"),
    (r"/cms", "cms_page"),
    (r"/settings/tax-rates", "tax_rate"),
    (r"/settings/tax-categories", "tax_category"),
    (r"/settings/channels", "channel"),
    (r"/settings/users", "admin_user"),
    (r"/settings/roles", "role"),
    (r"/settings/exchange-rates", "exchange_rate"),
    (r"/settings/currencies", "currency"),
    (r"/settings/locales", "locale"),
    (r"/settings/inventory-sources", "inventory_source"),
    (r"/settings/themes", "theme"),
    (r"/configuration", "configuration"),
    # `/orders` BURADA, `/invoices` ve `/refunds`tan ÖNCE durmalı: fatura ve
    # iade oluşturma yolları `/orders/{id}/invoices` biçimindedir ve yoldaki
    # numara SİPARİŞİN numarasıdır. Sıra ters olsaydı satır "Fatura #12" derdi,
    # oysa 12 sipariş numarasıdır ve `[Kayda git]` yanlış kaydı açardı.
    (r"/orders", "order"),
    (r"/invoices", "invoice"),
    (r"/refunds", "refund"),
    (r"/shipments", "shipment"),
    (r"/transactions", "transaction"),
)

#: Sonuç etiketleri ve tonu. Renk TEK BAŞINA anlam taşımaz: etiket her zaman
#: yazıyla gider (ADR 0011, kit kuralı 7).
RESULT_VIEW = {
    "ok": ("Başarılı", "good"),
    "dry_run": ("Kuru prova", "info"),
    "blocked": ("Reddedildi", "warn"),
    "failed": ("Hata", "bad"),
    "error": ("Hata", "bad"),
    "unknown": ("Bilinmiyor", "warn"),
}

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})

_STAMP = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")


# ===================================================================== temel

def text(value: Any) -> str:
    """`None` ve `0` ayrımını koruyarak metne çevirir."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def fold(value: Any) -> str:
    """Aksansız arama anahtarı: "Öğrenci" → "ogrenci".

    Kitin `foldText` eşidir ama BİR ADIM İLERİ gider: şapkalı harfleri de
    (â/î/û) düzler. Gerekçe metni serbest yazıdır ve "şikâyet" yazan bir
    gerekçeyi "sikayet" arayan personel bulamıyordu; süzme sunucuda yapıldığı
    için burada doğru olmak yeterlidir.
    """
    folded = text(value).translate(_TR_MAP).lower()
    stripped = unicodedata.normalize("NFD", folded)
    return "".join(char for char in stripped if not unicodedata.combining(char))


def first_of(raw: Any, *names: str) -> Any:
    """İlk DOLU alanı döndürür.

    Uzak kaydın alan adları Bagisto sürümüne göre değişiyor. Tek ada bel
    bağlamak ekranı sürüm yükseltmesinde sessizce boşaltırdı: satır gelir,
    hücreler boş görünür ve kimse bunun bir çeviri hatası olduğunu anlamaz.
    """
    if not isinstance(raw, dict):
        return None
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    return None


def as_mapping(value: Any) -> dict[str, Any]:
    """Sözlük ya da JSON metni olarak gelen alanı sözlüğe çevirir.

    Bagisto `old_values`/`new_values` alanlarını kimi sürümde JSON METNİ
    olarak döndürüyor. Metni sözlük sanıp `.items()` çağırmak ekranı düşürür.
    """
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


# ================================================================ zaman ekseni

def normalize_stamp(value: Any, *, assume_utc: bool = False) -> str:
    """Damgayı YEREL saate çevirip `YYYY-MM-DDTHH:MM:SS` biçimine getirir.

    TUZAK 1. Geçit UTC yazar (`2026-08-13T07:22:31+00:00`), mağaza kendi
    yerel saatini yazar (`2026-08-13 10:22:31`) ve saat dilimi bilgisi
    vermez. İkisini ham metin olarak sıralamak Türkiye'de üç saatlik kayma
    üretir: geçidin izi mağaza kaydının üç saat gerisine düşer ve aynı işlemin
    iki satırı listede birbirinden kopar.

    `assume_utc` yalnız GEÇİT satırları için True verilir; saat dilimi
    taşımayan mağaza damgası zaten yerel kabul edilir.
    """
    raw = text(value)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        match = _STAMP.match(raw)
        if not match:
            return ""
        year, month, day, hour, minute, second = match.groups()
        return (f"{year}-{month}-{day}T{hour or '00'}:{minute or '00'}:{second or '00'}")
    if parsed.tzinfo is None:
        if not assume_utc:
            return parsed.strftime("%Y-%m-%dT%H:%M:%S")
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def range_bounds(start: Any, end: Any) -> tuple[str, str, str]:
    """Tarih aralığını sınırlara açar. Dönen üçüncü değer HATA metnidir.

    TUZAK. Bitiş "2026-08-13" olarak gelirse ve olduğu gibi karşılaştırılırsa
    o günün bütün kayıtları (saat 00:00'dan sonrası) listeden düşer —
    kullanıcı "bugün hiçbir şey olmamış" sanır. Bitiş her zaman gün SONUNA
    genişletilir.
    """
    first = text(start)
    last = text(end)
    if not first or not last:
        return "", "", "Tarih aralığı zorunludur: başlangıç ve bitiş seçin."
    if len(first) == 10:
        first = f"{first}T00:00:00"
    if len(last) == 10:
        last = f"{last}T23:59:59"
    first = _pad_stamp(first)
    last = _pad_stamp(last)
    if not first or not last:
        return "", "", "Tarih aralığı okunamadı; günü GG.AA.YYYY, saati SS:DD verin."
    if first > last:
        return "", "", "Başlangıç bitişten sonra olamaz."
    return first, last, ""


def _pad_stamp(value: str) -> str:
    match = _STAMP.match(value)
    if not match:
        return ""
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day}T{hour or '00'}:{minute or '00'}:{second or '00'}"


def range_days(start: str, end: str) -> int:
    """Aralığın gün sayısı. Aralık tavanı denetimi için."""
    try:
        first = datetime.fromisoformat(start)
        last = datetime.fromisoformat(end)
    except ValueError:
        return 0
    return int((last - first).days) + 1


# ==================================================================== varlık

def entity_from_type(value: Any) -> str:
    """`Webkul\\Sales\\Models\\Order` → `order`.

    PHP sınıf adı ekranda gösterilmez: kullanıcı "Sipariş" görmek ister,
    ad alanı yolu görmek istemez.
    """
    raw = text(value)
    if not raw:
        return ""
    tail = re.split(r"[\\/]", raw)[-1]
    tail = re.sub(r"(Proxy|Model)$", "", tail)
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", tail).lower()
    return snake.strip("_")


def entity_from_path(path: Any) -> str:
    """API yolundan varlık çıkarır. Bulunamazsa boş döner — UYDURMAZ.

    Geçit satırında `auditable_type` yoktur; elimizde yalnız istek yolu var.
    Yol tanınmıyorsa "bilinmiyor" demek, yanlış varlık adı yazmaktan iyidir:
    yanlış ad `[Kayda git]` düğmesini yanlış panele yollar.
    """
    raw = text(path).lower()
    if not raw:
        return ""
    for pattern, name in PATH_ENTITIES:
        if pattern in raw:
            return name
    return ""


def entity_id_from_path(path: Any) -> int:
    """Yoldaki İLK sayısal parçayı kayıt numarası sayar (`/products/5` → 5).

    TUZAK. "Son sayısal parça" sezgisel olarak doğru görünür ama yanlıştır:
    `/catalog/products/1428/images/7` yolunda son sayı GÖRSELİN numarasıdır,
    ürünün değil — satır "Ürün #7" derdi ve `[Kayda git]` var olmayan bir ürünü
    açardı. Yönetici uçlarında sahip kaydın numarası her zaman önce gelir
    (`/orders/12/cancel`, `/customers/9/notes`, `/bbd/shipments/5/purchase`).
    """
    for part in text(path).split("/"):
        if part.isdigit():
            return int(part)
    return 0


def entity_label(name: str) -> str:
    return ENTITY_LABELS.get(name, name.replace("_", " ").title() if name else "—")


def action_label(action: str) -> str:
    key = fold(action)
    if key in ACTION_LABELS:
        return ACTION_LABELS[key]
    for hint, label in ACTION_LABELS.items():
        if hint in key:
            return label
    return action or "—"


def is_destructive(action: Any, method: Any = "") -> bool:
    """Yıkıcı mı? Yöntem DELETE ise ya da eylem adı yıkıcı bir fiil taşıyorsa.

    Bu bayrak izin denetimi DEĞİLDİR (bu ekran zaten salt okunur); yalnız
    "bugün ne silindi" sorusunu tek tıkla yanıtlayan süzgeç anahtarıdır.
    """
    if text(method).upper() == "DELETE":
        return True
    key = fold(action)
    return any(hint in key for hint in DESTRUCTIVE_HINTS)


# =================================================================== maskeleme

def mask_value(key: Any, value: Any) -> Any:
    """Sır taşıyan alanın değerini gizler. Alanın VARLIĞI gizlenmez.

    Değeri saklayıp anahtarı göstermek bilinçli: "belirteç değişti" bilgisi
    denetim için gereklidir, belirtecin kendisi değildir.
    """
    if any(hint in fold(key) for hint in SECRET_HINTS):
        return "•••" if value not in (None, "") else value
    return value


def mask_mapping(values: Any) -> dict[str, Any]:
    return {str(key): mask_value(key, value) for key, value in as_mapping(values).items()}


# ==================================================================== fark

def diff_fields(before: Any, after: Any, *, limit: int = DIFF_LIMIT) -> list[dict[str, Any]]:
    """ALAN ALAN fark tablosu: öncesi → sonrası, değişenler işaretli.

    Ekranın kalbi budur. "Güncellendi" demek denetim değildir; hangi alanın
    hangi değerden hangi değere gittiği denetimdir.

    Yalnız `after` varsa (oluşturma) `before` boş görünür; yalnız `before`
    varsa (silme) `after` boş görünür — ikisi de bilgidir, satır atlanmaz.
    """
    old = mask_mapping(before)
    new = mask_mapping(after)
    keys = sorted(set(old) | set(new))
    rows: list[dict[str, Any]] = []
    for key in keys[:limit]:
        was = old.get(key)
        now = new.get(key)
        rows.append({
            "field": key,
            "before": _flat(was),
            "after": _flat(now),
            # `key in old` denetimi ŞART: `None` ile "hiç yoktu" farklı
            # şeylerdir ve oluşturma kaydında hepsi None görünür.
            "changed": (key in old) != (key in new) or was != now,
        })
    return rows


def _flat(value: Any) -> str:
    """Değeri tek satırlık okunur metne indirir. `None` → boş."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "evet" if value else "hayır"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:400]
    return str(value)


def summary_text(diff: list[dict[str, Any]], *, limit: int = 4) -> str:
    """Liste satırındaki "ne değişti" özeti: değişen alan adları."""
    changed = [row["field"] for row in diff if row.get("changed")]
    if not changed:
        return ""
    if len(changed) <= limit:
        return ", ".join(changed)
    return f"{', '.join(changed[:limit])} +{len(changed) - limit}"


# =============================================================== satır kurma

def remote_row(raw: Any) -> dict[str, Any]:
    """Uzak `admin_api_audits` satırı → ekran satırı.

    ALAN ADLARI camelCase DE OLABİLİR — hatta ÖNCELİKLE öyledir. Bagisto'nun
    yönetici zarfı (`AdminCollectionEnvelopeNormalizer`) veritabanı sütunlarını
    camelCase'e çevirerek döndürüyor; canlı mağazada doğrulandı
    (`incrementId`, `grandTotal`, `createdAt`, `attributeFamilyId`…). Yalnız
    `old_values`/`auditable_type`/`ip_address` yazmak, ekranı SESSİZCE boşaltan
    türden bir hatadır: satırlar gelir, fark tablosu boş çıkar, "Varlık" ve "IP"
    sütunları "—" dolar ve kimse bunu bir çeviri hatası sanmaz.
    """
    data = raw if isinstance(raw, dict) else {}
    action = text(first_of(data, "action", "event", "operation", "type"))
    method = text(first_of(data, "method", "httpMethod", "http_method", "verb")).upper()
    if not action:
        action = METHOD_ACTIONS.get(method, "")
    path = text(first_of(data, "path", "url", "route", "uri"))

    entity = entity_from_type(first_of(data, "auditableType", "auditable_type", "subjectType",
                                       "subject_type", "entityType", "entity_type",
                                       "model", "entity"))
    if not entity:
        entity = entity_from_path(path)
    entity_id = as_int(first_of(data, "auditableId", "auditable_id", "subjectId", "subject_id",
                                "entityId", "entity_id", "recordId", "record_id"))
    if not entity_id:
        entity_id = entity_id_from_path(path)

    before = as_mapping(first_of(data, "oldValues", "old_values", "before", "old", "original"))
    after = as_mapping(first_of(data, "newValues", "new_values", "after", "new", "changes"))
    diff = diff_fields(before, after)

    status = as_int(first_of(data, "status", "statusCode", "status_code", "httpStatus",
                             "http_status"), 0)
    result = text(first_of(data, "result", "outcome"))
    if not result:
        # Uzak tablo yalnız BAŞARILI yazmaları tutar; durum kodu yoksa
        # "başarılı" varsaymak burada doğrudur, uydurma değildir.
        result = "ok" if status in (0, 200, 201, 204) else "failed"

    row_id = as_int(first_of(data, "id", "auditId", "audit_id"))
    at = normalize_stamp(first_of(data, "createdAt", "created_at", "occurredAt", "occurred_at",
                                  "loggedAt", "logged_at", "timestamp"))
    return _row(
        key=f"r:{row_id}" if row_id else f"r:{at}-{entity}-{entity_id}",
        source="store",
        row_id=row_id,
        request_id=text(first_of(data, "requestId", "request_id", "correlationId",
                                 "correlation_id")),
        at=at,
        user=text(first_of(data, "userName", "user_name", "adminName", "admin_name", "actor",
                           "causerName", "causer_name")
                  or _named(first_of(data, "user", "admin", "causer"))),
        action=action,
        method=method,
        path=path,
        entity=entity,
        entity_id=entity_id,
        reason=text(first_of(data, "reason", "note")),
        ip=text(first_of(data, "ipAddress", "ip_address", "ip", "clientIp", "client_ip")),
        result=result,
        status=status,
        dry_run=False,
        diff=diff,
        raw=data,
    )


def local_row(raw: Any) -> dict[str, Any]:
    """Yerel `mod_store_api_audit` satırı (geçidin izi) → ekran satırı.

    Bu satırların değeri, uzak tabloda KARŞILIĞI OLMAYANLARINDADIR: gerekçe,
    kuru prova, reddedilen istek ve "gönderildi mi belli değil" durumu.
    """
    data = raw if isinstance(raw, dict) else {}
    path = text(data.get("path"))
    method = text(data.get("method")).upper()
    action = text(data.get("action")) or METHOD_ACTIONS.get(method, "")
    result = text(data.get("result")) or "unknown"
    body = as_mapping(data.get("body"))
    return _row(
        key=f"g:{text(data.get('request_id')) or as_int(data.get('id'))}",
        source="gateway",
        row_id=as_int(data.get("id")),
        request_id=text(data.get("request_id")),
        # Geçit damgası UTC'dir — yerel saate çevrilmezse mağaza kaydından
        # üç saat geride görünür (TUZAK 1).
        at=normalize_stamp(data.get("created_at"), assume_utc=True),
        user=text(data.get("actor")),
        action=action,
        method=method,
        path=path,
        entity=entity_from_path(path),
        entity_id=entity_id_from_path(path),
        reason=text(data.get("reason")),
        ip="",
        result=result,
        status=as_int(data.get("status")),
        dry_run=bool(as_int(data.get("dry_run"))),
        # Geçit satırı GÖNDERİLEN gövdeyi taşır; "öncesi" bilgisi yoktur.
        # Fark tablosunda tek sütun dolu görünür ve bu doğrudur.
        diff=diff_fields({}, body),
        raw=data,
    )


def _named(value: Any) -> str:
    """`{"id":3,"name":"Ayşe"}` → "Ayşe"."""
    if isinstance(value, dict):
        return text(first_of(value, "name", "fullName", "full_name", "email", "userName",
                             "username"))
    return text(value)


def _row(*, key: str, source: str, row_id: int, request_id: str, at: str, user: str,
         action: str, method: str, path: str, entity: str, entity_id: int, reason: str,
         ip: str, result: str, status: int, dry_run: bool, diff: list[dict[str, Any]],
         raw: Any) -> dict[str, Any]:
    label, tone = RESULT_VIEW.get(result.split(":")[0], (result or "Bilinmiyor", "warn"))
    changed = [item["field"] for item in diff if item.get("changed")]
    return {
        "key": key,
        "source": source,
        "sourceLabel": "Mağaza" if source == "store" else "Geçit",
        "id": row_id,
        "requestId": request_id,
        "at": at,
        "user": user or "—",
        "action": action,
        "actionLabel": action_label(action),
        "method": method,
        "path": path,
        "entity": entity,
        "entityLabel": entity_label(entity),
        "entityId": entity_id,
        "summary": summary_text(diff),
        "changed": changed,
        "fieldCount": len(diff),
        "reason": reason,
        "ip": ip,
        "result": result,
        "resultLabel": label,
        "resultTone": tone,
        "status": status,
        "dryRun": dry_run,
        "destructive": is_destructive(action, method),
        "diff": diff,
        "raw": json.dumps(raw, ensure_ascii=False, default=str)[:RAW_LIMIT],
        # Sıralama anahtarı: damga + kaynak + kimlik. Damga eşit olduğunda
        # sıra RASTGELE olmamalı, yoksa aynı sayfa iki kez çekilince satırlar
        # yer değiştirir ve imleç kayar.
        #
        # DAMGASIZ SATIR EN SONA. `at` boş kalabilir (uzak uç `createdAt` yerine
        # tanımadığımız bir ad kullanmışsa ya da değer bozuksa). Ham "" ile
        # başlayan anahtar, ayraç '|' rakamlardan büyük olduğu için AZALAN
        # sıralamada listenin BAŞINA otururdu: tarihsiz bir satır her sayfanın
        # en üstünde belirir ve imleç sınırını da bozardı.
        "sortKey": f"{at or NO_STAMP}|{source}|{row_id:012d}|{key}",
    }


def join_reason(rows: list[dict[str, Any]], local: list[dict[str, Any]]) -> None:
    """Uzak satıra, aynı istek kimliğini taşıyan geçit satırının GEREKÇESİNİ ekler.

    TUZAK 2'nin yarısı. Bagisto gerekçe tutmuyor; gerekçe geçidin
    `X-Bbd-Reason` başlığında gitti ve yalnız yerel izde durdu. İki satırı
    birleştirmezsek ekran "kim, neyi değiştirdi"yi gösterir ama "neden"i
    gösteremez — bu ekranın var oluş sebebi tam olarak odur.
    """
    by_request = {row["requestId"]: row for row in local if row.get("requestId")}
    for row in rows:
        if row["source"] != "store" or row["reason"]:
            continue
        mate = by_request.get(row.get("requestId") or "")
        if mate:
            row["reason"] = mate["reason"]
            row["actorSource"] = "gateway"
            if not row["user"] or row["user"] == "—":
                row["user"] = mate["user"]


def local_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Uzak tabloda KARŞILIĞI OLMAYACAK geçit satırları.

    TUZAK 2'nin diğer yarısı. Başarılı bir yazma iki kaynakta da vardır; ikisini
    de listelemek her satırı çiftler. Uzak tablo yalnız BAŞARILI yazmaları
    tuttuğu için ayrım kesindir: sonucu `ok` olan geçit satırının uzakta ikizi
    vardır ve yalnız gerekçe kaynağı olarak kullanılır; gerisi (kuru prova,
    reddedilen, hata, sonucu bilinmeyen) yalnız burada durur.
    """
    return [row for row in rows if row.get("result") != "ok"]


# =================================================================== süzme

def matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Satır süzgece uyuyor mu. İSTEMCİDE DEĞİL, sunucuda çalışır.

    Aynı süzgeç uzak sunucuya da gönderilir; buradaki kopya iki iş yapar:
    (a) geçit satırlarını süzer — uzak sunucu onları görmez,
    (b) uzak sunucunun süzgeci UYGULAYIP UYGULAMADIĞINI doğrular. Laravel
        tanımadığı sorgu parametresini sessizce yok sayar; süzülmemiş listeyi
        süzülmüş gibi göstermek denetim ekranında kabul edilemez.
    """
    start = text(filters.get("start"))
    end = text(filters.get("end"))
    at = row.get("at") or ""
    if start and at < start:
        return False
    if end and at > end:
        return False

    user = fold(filters.get("user"))
    if user and user not in fold(row.get("user")):
        return False

    action = fold(filters.get("action"))
    if action and action not in fold(row.get("action")):
        return False

    entity = text(filters.get("entity"))
    if entity and entity != row.get("entity"):
        return False

    entity_id = as_int(filters.get("entityId"))
    if entity_id and entity_id != row.get("entityId"):
        return False

    ip = text(filters.get("ip"))
    if ip and ip not in text(row.get("ip")):
        return False

    result = text(filters.get("result"))
    if result and not text(row.get("result")).startswith(result):
        return False

    source = text(filters.get("source"))
    if source and source != row.get("source"):
        return False

    if filters.get("destructive") and not row.get("destructive"):
        return False

    if filters.get("reasoned") and not text(row.get("reason")):
        return False

    query = fold(filters.get("q"))
    if query:
        haystack = " ".join([
            fold(row.get("reason")), fold(row.get("user")), fold(row.get("action")),
            fold(row.get("actionLabel")), fold(row.get("entityLabel")),
            fold(row.get("summary")), fold(row.get("path")), fold(row.get("ip")),
            str(row.get("entityId") or ""),
        ])
        if query not in haystack:
            return False
    return True


def remote_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Uzak uca gönderilecek sorgu parametreleri.

    Adlar mağaza tarafındaki `GET /api/admin/bbd/audit` ucunun sözleşmesidir
    (README'de yazılıdır). Uç bunları YOK SAYARSA ekran çökmez: `matches`
    aynı süzgeci burada da uygular ve kullanıcıya "sunucu süzmedi, sayfa
    içinde süzüldü" der.
    """
    out: dict[str, Any] = {}
    mapping = (("start", "from"), ("end", "to"), ("q", "q"), ("user", "user"),
               ("action", "action"), ("entity", "entity"), ("ip", "ip"), ("result", "result"))
    for key, name in mapping:
        value = text(filters.get(key))
        if value:
            out[name] = value
    if as_int(filters.get("entityId")):
        out["entity_id"] = as_int(filters.get("entityId"))
    if filters.get("destructive"):
        out["destructive"] = 1
    return out


#: `remote_filters` ile mağazaya GİDEN süzgeç anahtarları.
#: `source` ve `reasoned` bu listede YOKTUR: ikisi de mağazaya hiç gönderilmez
#: (biri kaydın hangi kaynaktan geldiğini söyler, diğeri gerekçeyi arar ve
#: gerekçe mağazada hiç durmaz). Onlar yüzünden elenen satırı "sunucu süzgeci
#: uygulamadı" diye saymak, doğru çalışan bir uca haksız uyarı astırırdı.
REMOTE_KEYS: tuple[str, ...] = ("start", "end", "q", "user", "action", "entity",
                                "entityId", "ip", "result", "destructive")


def remote_scope(filters: dict[str, Any]) -> dict[str, Any]:
    """Süzgecin YALNIZ uzağa gönderilen parçası.

    `matches(row, remote_scope(filters))` False dönüyorsa satır, mağazaya
    gönderdiğimiz bir süzgece uymuyor demektir: uç o süzgeci uygulamamıştır.
    Kanıt budur — "gönderdim, herhalde uygulanmıştır" varsayımı denetim
    ekranında kabul edilemez.
    """
    return {key: filters[key] for key in REMOTE_KEYS if key in filters}


# ==================================================================== imleç

def encode_cursor(remote_offset: int, local_offset: int, after: str) -> str:
    """İmleç. Kullanıcıya AÇIK EDİLMEZ; sayfa numarası değildir.

    NEDEN SAYFA NUMARASI DEĞİL. İki kaynak birleştiriliyor ve ikisi de farklı
    sayfalanıyor (uzak taraf ofsetli, yerel iz tek listede). Sayfa numarasıyla
    gezinmek, iki kaynağın kayıt sayıları değiştiğinde satır tekrarlatır ya da
    satır atlatır. İmleç her iki kaynaktaki konumu ve `after` sınırını birlikte
    taşır; `after`, sayfa çekilirken araya giren YENİ kayıtların önceki sayfayı
    tekrar ettirmesini engeller.
    """
    payload = json.dumps({"r": int(remote_offset), "l": int(local_offset), "a": after},
                         separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(token: Any) -> tuple[int, int, str] | None:
    """İmleci çözer. Bozuksa `None` — sessizce başa dönmez, ekran haber verir."""
    raw = text(token)
    if not raw:
        return 0, 0, ""
    padded = raw + "=" * (-len(raw) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return max(0, as_int(data.get("r"))), max(0, as_int(data.get("l"))), text(data.get("a"))


def merge_page(remote: list[dict[str, Any]], local: list[dict[str, Any]], *,
               size: int = DEFAULT_PAGE_SIZE, after: str = "",
               keep: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    """İki sıralı (zamana göre azalan) pencereyi tek sayfaya diker.

    ELENEN SATIR DA SAYILIR. `after` sınırına ya da `keep` süzgecine takılan
    satır sayfaya girmez ama ofseti ilerletir: ofsetler kaynaktaki GERÇEK
    konumu göstermek zorundadır, yoksa bir sonraki sayfa elenen satırları
    yeniden getirir ve liste kendini tekrar eder.

    `after`, sayfa çekilirken araya giren YENİ kayda karşı emniyet supabıdır:
    yeni kayıt her iki kaynağı da bir satır aşağı iter ve ofset tek başına
    önceki sayfanın son satırını tekrar ettirirdi.
    """
    rows: list[dict[str, Any]] = []
    ri = li = 0
    while len(rows) < size and (ri < len(remote) or li < len(local)):
        take_remote = li >= len(local) or (
            ri < len(remote) and remote[ri]["sortKey"] >= local[li]["sortKey"])
        row = remote[ri] if take_remote else local[li]
        if take_remote:
            ri += 1
        else:
            li += 1
        if after and row["sortKey"] >= after:
            continue
        if keep is not None and not keep(row):
            continue
        rows.append(row)
    return {
        "rows": rows,
        "usedRemote": ri,
        "usedLocal": li,
        "more": ri < len(remote) or li < len(local),
    }
