"""Vergi verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bagisto'nun vergi kaydı üç parçaya dağılmıştır: ORAN
(`tax_rates`: yüzde + coğrafi kapsam), VERGİ KATEGORİSİ (`tax_categories`:
oranların demeti) ve ÜRÜNÜN `tax_category_id` özniteliği. Hangi ürüne hangi
yüzdenin uygulandığı bu üçünün kesişimidir ve hiçbir uç bunu hazır vermez.
Kesişimi burada kuruyoruz; servise gömülseydi tek satırı test edilemezdi.

YEDİ TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. Oran `20.0000` metnidir       → `rate_value` Decimal ile okur, float ile değil.
 2. Bagisto'da GEÇERLİLİK TARİHİ  → alan yok; yerel not `rate_row` içinde ayrı
    YOKTUR                          tutulur ve mağaza davranışı gibi gösterilmez.
 3. Öncelik/bileşik vergi YOKTUR  → `RATE_FIELDS` bu alanları göndermez; ekran
                                     "Bagisto'da yok" der, boş sütun uydurmaz.
 4. Aynı adresi iki oran karşılar → `conflicts` aynı kategorideki örtüşmeyi
                                     bulur; mağaza hangisini seçtiğini söylemez.
 5. Posta kodu tek değer de olur  → `zip_overlap` tekil/aralık/joker üçünü de
    aralık da joker de                karşılaştırır.
 6. Kısmi PUT alanları boşaltır   → `product_body` OKU-DEĞİŞTİR-YAZ kurar.
 7. YANIT camelCase, İSTEK        → `pick`/`attribute` alanı iki adla da arar;
    snake_case                      gövde kurucular snake_case yazmayı sürdürür.

TUZAK 7 CANLIDA DOĞRULANDI (2026-08-13, bbdstore.com.tr):

    GET  /api/admin/settings/tax-rates    → {"identifier":…, "taxRate":20,
                                             "isZip":false, "zipCode":"", …}
    POST /api/admin/settings/tax-rates    → {"identifier":…, "tax_rate":8.5,
                                             "is_zip":false, "zip_code":…}

Yani mağaza YANITI camelCase verir ama İSTEĞİ snake_case bekler (OpenAPI şeması
`/api/admin/docs` bunu böyle ilan ediyor). Okumayı snake_case yapan kod hiçbir
alanı bulamaz, hata da vermez: tablo "Oran okunamadı" ile dolar. Bu yüzden okuma
`pick()`/`attribute()` üzerinden İKİ ADI DA dener, yazma ise yalnız snake_case
üretir.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit (store_api) da 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Oran değişikliğinin ne yaptığını (ve ne YAPMADIĞINI) anlatan metin.
#: Ekranda her yazma öncesi gösterilir; muhasebenin en sık sorduğu soru budur.
RATE_NOTICE = (
    "Oran değişikliği GEÇMİŞ faturaları etkilemez: kesilmiş fatura hangi "
    "orandan kesildiyse öyle kalır. Yeni oran yalnız bundan sonraki "
    "hesaplamalarda kullanılır."
)

#: Vitrinin yeni oranı göstermesi önbellek/dizin yenilenmesini bekler.
INDEX_NOTICE = "Vitrine yansıması birkaç dakika sürebilir (fiyat önbelleği yenilenir)."

#: Kategori ↔ oran ilişkisi liste ucundan okunamadığında gösterilir. Sessiz
#: kalmak, boş rozeti gerçek sanmak demek olurdu.
MEMBERSHIP_NOTICE = (
    "Vergi kategorilerinin oran bağlantısı mağazanın liste ucundan okunamıyor "
    "(kayıtlar «taxRates: null» dönüyor). Bu yüzden «oransız», «bağlantısız» ve "
    "«çakışan kural» işaretleri bu ekranda GÖSTERİLMEZ: okuyamadığımız ilişkiyi "
    "yok saymak yanlış olurdu. Kategori düzenleme de bu yüzden kapalıdır — kaydetmek "
    "göremediğimiz oranları kategoriden düşürürdü."
)

#: Bagisto vergi oranı kaydında YAZILABİLİR alanlar. Listede olmayan hiçbir
#: alan gövdeye konmaz. `priority` ve `is_compound` BİLEREK yoktur: Bagisto
#: çekirdeğinde öncelikli/bileşik vergi kavramı bulunmuyor, gönderilen alan
#: sessizce yok sayılır ve kullanıcı "önceliği ayarladım" sanır.
RATE_FIELDS = ("identifier", "tax_rate", "country", "state", "is_zip",
               "zip_code", "zip_from", "zip_to")

#: Vergi kategorisinde yazılabilir alanlar.
CATEGORY_FIELDS = ("code", "name", "description", "taxrates")

#: Ürün gövdesinde korunacak alanlar (OKU-DEĞİŞTİR-YAZ). Kısmi PUT bu
#: alanlardan bazılarını NULL'a düşürebiliyor; gövde mevcut değerlerden kurulur.
#:
#: NEDEN BU LİSTE STORE_PRODUCTS'TAKİNİN KOPYASI DEĞİL: burada yalnız
#: `tax_category_id` değişiyor, geri kalan her şey olduğu gibi geri gidiyor.
#: Liste bilerek dardır — tanımadığımız özniteliği geri göndermek, aynı anda
#: Ürünler ekranının yazdığı değeri ezmektir. Modül modülü import edemediği
#: için (K3) ortak bir yardımcı yok; ortaklaştırma gerekirse `km_sdk` işidir.
PRODUCT_KEEP = ("sku", "name", "url_key", "status", "visible_individually",
                "short_description", "description", "price", "weight",
                "tax_category_id")

#: Ülke kodu → Türkçe ad. Tam liste tutulmaz: mağaza tek ülkeye satıyor ve
#: bilinmeyen kod ham gösterilir (uydurma ad, yanlış bölge raporu demektir).
COUNTRY_NAMES = {"TR": "Türkiye", "": "Tüm ülkeler", "*": "Tüm ülkeler"}

ZIP_ANY = ("", "*", "0")


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow().date()` kullanılmaz: geçerlilik tarihi mağazanın takvim
    gününe göre başlar ve gece yarısından sonraki üç saatte UTC bir gün geride
    kalır — "bugünden geçerli" yazan kullanıcı reddedilirdi.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    """`None` ve `0` ayrımını koruyarak metne çevirir."""
    if value is None:
        return ""
    return str(value).strip()


def camel(name: str) -> str:
    """`tax_rate` → `taxRate`. Mağaza yanıtlarındaki alan adı biçimi (TUZAK 7)."""
    head, _, rest = name.partition("_")
    if not rest:
        return name
    return head + "".join(part[:1].upper() + part[1:] for part in rest.split("_"))


def pick(raw: Any, *names: str) -> Any:
    """Alanı hem snake_case hem camelCase adıyla arar; yoksa `None`.

    Mağaza `taxRate`/`isZip`/`createdAt` yazıyor, Bagisto çekirdeği ise
    `tax_rate`/`is_zip` bekliyor. İki adı da denemek, sürüm ya da paket
    değişince ekranın sessizce boşalmasını engeller.
    """
    if not isinstance(raw, dict):
        return None
    for name in names:
        for key in (name, camel(name)):
            if key in raw and raw[key] is not None:
                return raw[key]
    return None


def as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        # `int("False")` patlar; mağaza `isZip` alanını JSON boolean gönderiyor.
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return default


def to_kurus(value: Any) -> int | None:
    """Bagisto'nun ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: burada `float` kullanılmaz. `float("1234.35") * 100` bazı değerlerde
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar. KDV icmalinde bu
    sapma binlerce satır boyunca birikir ve beyanı tutmaz.
    """
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if raw == "":
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else \
            raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_kurus(kurus: Any) -> str:
    """Kuruş → telde gidecek ondalık metin: 123435 → `1234.35`."""
    return f"{Decimal(int(kurus or 0)) / 100:.2f}"


def rate_value(value: Any) -> float | None:
    """Oran yüzdesi: `"20.0000"` → 20.0. Çözülemezse `None`.

    `None` ile 0.0 AYRI şeylerdir: birincisi "oran okunamadı", ikincisi
    "KDV'siz" (kitap, gazete). İkisini birleştirmek muaf ürünü okunamayan
    ürünle aynı kovaya atardı.
    """
    raw = text(value).replace(",", ".")
    if raw == "":
        return None
    try:
        return float(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None


def rate_key(percent: float | None) -> str:
    """Oranın kova anahtarı: 20.0 → `"20"`, 8.5 → `"8.5"`, None → `"?"`."""
    if percent is None:
        return "?"
    return f"{percent:g}"


def rate_label(percent: float | None) -> str:
    """Ekranda görünen etiket: `%20` · `%8,5` · `Oran okunamadı`."""
    if percent is None:
        return "Oran okunamadı"
    return f"%{rate_key(percent).replace('.', ',')}"


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def id_error(value: Any, label: str) -> str:
    """Kayıt kimliği yazılabilir mi.

    SIFIR KİMLİK SESSİZ GEÇMEZ. `PUT /rates/0` yolundan gelen 0, "yeni kayıt"
    gibi görünüyor (`if rate_id:` yanlış) ama geçide `rate_id=0` olarak gidiyor
    ve `0 is None` yanlış olduğu için mağazaya `PUT /settings/tax-rates/0`
    atılıyordu: var olmayan bir kayda yazma denemesi. Kimlik burada kapıda
    durdurulur; K9 gereği doğrulama backend'de yapılır.
    """
    if as_int(value, 0) <= 0:
        return f"{label} kimliği geçersiz (0 ya da negatif olamaz)."
    return ""


def effective_error(value: str, *, today: str = "") -> str:
    """Geçerlilik tarihi kabul edilebilir mi.

    GERİYE DÖNÜK YASAK: geçmiş bir tarih, o tarihten beri kesilen faturaların
    yeni orandan kesildiğini iddia eder. Kesilmemiştir; mağaza değişikliği
    ancak bugünden itibaren uygular.
    """
    day = text(value)
    if day == "":
        return ""
    try:
        datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return "Geçerlilik tarihi YYYY-AA-GG biçiminde olmalı."
    if day < (today or today_iso()):
        return ("Geçerlilik tarihi geçmişe verilemez: mağaza oranı geriye dönük "
                "uygulamaz, kesilmiş faturalar eski oranda kalır.")
    return ""


# ============================================================== oran satırı

def country_label(code: str) -> str:
    value = text(code).upper()
    return COUNTRY_NAMES.get(value, value or "Tüm ülkeler")


def zip_scope(raw: dict[str, Any]) -> dict[str, Any]:
    """Oranın posta kodu kapsamı. Üç biçim var: joker · tekil · aralık."""
    flag = pick(raw, "is_zip")
    is_range = bool(flag) if isinstance(flag, bool) else bool(as_int(flag, 0))
    single = text(pick(raw, "zip_code"))
    start = text(pick(raw, "zip_from"))
    end = text(pick(raw, "zip_to"))
    if is_range and (start or end):
        return {"kind": "range", "from": start, "to": end,
                "label": f"{start or '…'} – {end or '…'}"}
    if single and single not in ZIP_ANY:
        return {"kind": "single", "from": single, "to": single, "label": single}
    return {"kind": "any", "from": "", "to": "", "label": "Tüm posta kodları"}


def zip_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """İki posta kodu kapsamı kesişiyor mu?

    Joker her şeyle kesişir. Aralıklar metin olarak DEĞİL sayı olarak
    karşılaştırılır: `"01000" < "9"` metinde doğrudur ama posta kodunda değildir.
    Sayıya çevrilemeyen kod (harf içeren yabancı kodlar) varsa kesişim
    VARSAYILIR — bilmediğimiz durumda "çakışma yok" demek, çakışmayı gizler.
    """
    if left["kind"] == "any" or right["kind"] == "any":
        return True
    bounds = []
    for scope in (left, right):
        low = as_int(scope["from"], -1)
        high = as_int(scope["to"], -1)
        if low < 0 or high < 0:
            return True
        bounds.append((min(low, high), max(low, high)))
    return bounds[0][0] <= bounds[1][1] and bounds[1][0] <= bounds[0][1]


def rate_row(raw: dict[str, Any], *, effective: dict[str, Any] | None = None,
             category_ids: list[int] | None = None) -> dict[str, Any]:
    """Ham vergi oranı kaydı → tablonun beklediği satır."""
    percent = rate_value(pick(raw, "tax_rate"))
    scope = zip_scope(raw)
    country = text(pick(raw, "country")).upper()
    state = text(pick(raw, "state"))
    note = effective or {}
    return {
        "id": as_int(raw.get("id")),
        "name": text(pick(raw, "identifier")) or f"#{as_int(raw.get('id'))}",
        "percent": percent,
        "percentLabel": rate_label(percent),
        "country": country,
        "countryLabel": country_label(country),
        "state": state,
        "region": region_label(country, state),
        "zip": scope,
        "zipLabel": scope["label"],
        # Geçerlilik tarihi ve notu YERELDİR: Bagisto'da bu alan yok. Ekran
        # bunu "bizim notumuz" diye gösterir, mağaza davranışı diye değil.
        "effectiveFrom": text(note.get("effective_from")),
        "effectiveNote": text(note.get("note")),
        "effectiveActor": text(note.get("actor")),
        "categoryIds": sorted(category_ids or []),
        "categoryCount": len(category_ids or []),
        "createdAt": text(pick(raw, "created_at"))[:19],
        "updatedAt": text(pick(raw, "updated_at"))[:19],
    }


def region_label(country: str, state: str) -> str:
    base = country_label(country)
    return f"{base} · {state}" if text(state) else base


def rate_body(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """OKU-DEĞİŞTİR-YAZ gövdesi (TUZAK 6).

    Bagisto'nun vergi oranı işlemcisi gövdedeki alanları modele doğrudan
    veriyor; yalnız değişen alanı göndermek `country`/`zip_code` gibi zorunlu
    alanları boşaltıp 422 üretiyor. Gövde mevcut kayıttan kurulur.

    `is_zip` gönderilmezse Bagisto tekil posta koduna düşüyor ve aralık sessizce
    kayboluyor; bu yüzden her zaman 0/1 olarak yazılır.
    """
    body: dict[str, Any] = {}
    for key in RATE_FIELDS:
        # Mevcut kayıt camelCase gelir (TUZAK 7), gövde snake_case gider.
        value = pick(current, key)
        if value is not None:
            body[key] = value
    for key, value in (patch or {}).items():
        if key in RATE_FIELDS:
            body[key] = value

    body["is_zip"] = 1 if as_int(body.get("is_zip"), 0) else 0
    if body["is_zip"]:
        body.pop("zip_code", None)
    else:
        body.pop("zip_from", None)
        body.pop("zip_to", None)
        if not text(body.get("zip_code")):
            # Canlıdaki kayıt `zipCode: ""` tutuyor; Bagisto aralık dışı kipte
            # posta kodu bekliyor ve boş metin doğrulamadan dönebilir. Joker
            # hem kapsamı değiştirmez hem de reddedilmez.
            body["zip_code"] = "*"
    body["country"] = text(body.get("country")).upper()
    if body.get("tax_rate") is not None:
        body["tax_rate"] = f"{rate_value(body['tax_rate']) or 0:.4f}"
    return body


def rate_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """Ekranın camelCase gövdesini Bagisto adlarına çevirir.

    Tanınmayan anahtar SESSİZCE DÜŞÜRÜLÜR: bilmediğimiz alanı mağazaya
    iletmek, yazım hatasını veri kaybına çevirir.
    """
    aliases = {"name": "identifier", "percent": "tax_rate", "country": "country",
               "state": "state", "isRange": "is_zip", "zipCode": "zip_code",
               "zipFrom": "zip_from", "zipTo": "zip_to"}
    out: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        target = aliases.get(key)
        if target:
            out[target] = value
    if "is_zip" in out:
        out["is_zip"] = 1 if out["is_zip"] else 0
    return out


def rate_form_error(patch: dict[str, Any]) -> str:
    """Oran gövdesinin iş kuralı denetimi. Mağazaya gitmeden önce."""
    if not text(patch.get("identifier")):
        return "Oran adı zorunlu; muhasebe raporunda bu ad görünür."
    percent = rate_value(patch.get("tax_rate"))
    if percent is None:
        return "Oran yüzdesi okunamadı; 20 ya da 8,5 gibi yazın."
    if percent < 0 or percent > 100:
        return "Oran %0 ile %100 arasında olmalı."
    if not text(patch.get("country")):
        return "Ülke zorunlu; kapsamsız oran hiçbir adrese uymaz."
    if as_int(patch.get("is_zip"), 0):
        start, end = text(patch.get("zip_from")), text(patch.get("zip_to"))
        if not start or not end:
            return "Posta kodu aralığında başlangıç ve bitiş zorunlu."
        if as_int(start, -1) < 0 or as_int(end, -1) < 0:
            return "Posta kodu aralığı sayı olmalı."
        if as_int(start) > as_int(end):
            return "Posta kodu aralığının başlangıcı bitişinden büyük olamaz."
    return ""


# ======================================================== vergi kategorisi

def category_row(raw: dict[str, Any], *, usage: dict[str, Any] | None = None,
                 rates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Ham vergi kategorisi → tablo satırı.

    `productCount` yerel tarama sonucudur ve `scannedAt` ile birlikte verilir:
    sayının ne kadar bayat olduğunu ekran SÖYLER, taze gibi göstermez.

    `ratesKnown` False ise oran ilişkisi OKUNAMADI (liste ucu `taxRates: null`
    döndürüyor — canlıda doğrulandı). Bu durumda "oransız" yazmak yalandır:
    kategorinin oranı olabilir, biz göremiyoruzdur.
    """
    ids = rate_ids_of(raw)
    known_rates = rate_ids_known(raw)
    known = {row["id"]: row for row in (rates or [])}
    percentages = sorted({known[item]["percent"] for item in ids
                          if item in known and known[item]["percent"] is not None})
    counted = usage is not None
    if known_rates:
        percent_label = " · ".join(rate_label(item) for item in percentages) or "—"
    else:
        percent_label = "Okunamadı"
    return {
        "id": as_int(raw.get("id")),
        "code": text(pick(raw, "code")),
        "name": text(pick(raw, "name")) or text(pick(raw, "code")),
        "description": text(pick(raw, "description")),
        "rateIds": ids,
        "rateCount": len(ids) if known_rates else None,
        "ratesKnown": known_rates,
        "percentages": percentages,
        "percentLabel": percent_label,
        "productCount": as_int((usage or {}).get("product_count")) if counted else None,
        "scannedAt": text((usage or {}).get("scanned_at")),
    }


def rate_ids_of(raw: dict[str, Any]) -> list[int]:
    """Kategorinin oran kimlikleri. Bagisto ilişkiyi üç biçimde döndürüyor:
    kimlik listesi, nesne listesi ya da (liste ucunda) `null`."""
    items = _rate_relation(raw)
    if not isinstance(items, list):
        return []
    out: list[int] = []
    for item in items:
        found = as_int(item.get("id")) if isinstance(item, dict) else as_int(item)
        if found:
            out.append(found)
    return sorted(set(out))


def rate_ids_known(raw: dict[str, Any]) -> bool:
    """İlişki gerçekten okunabildi mi?

    CANLIDA (2026-08-13) `GET /settings/tax-categories` her kategori için
    `"taxRates": null` döndürüyor; aynı kategorinin TEKİL ucu ilişkiyi dolu
    veriyor. `null` "oranı yok" demek DEĞİLDİR; boş liste ile karıştırılırsa
    ekran «oransız» rozetini yakar ve kategoriyi kaydeden kullanıcı ilişkiyi
    farkında olmadan siler.
    """
    return isinstance(_rate_relation(raw), list)


def _rate_relation(raw: dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        return None
    for key in ("tax_rates", "taxRates", "taxrates"):
        if key in raw:
            return raw[key]
    return None


def category_body(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Vergi kategorisi gövdesi. `taxrates` TAM liste gönderilir.

    Bagisto ilişkiyi kısmi kabul etmiyor: gönderilmeyen oran kategoriden
    düşüyor. Ekran da tam listeyi gönderir ve kullanıcıya "seçili olmayan
    kaldırılır" der — sessiz kaldırma olmaz.
    """
    body: dict[str, Any] = {
        "code": text(current.get("code")),
        "name": text(current.get("name")),
        "description": text(current.get("description")),
        "taxrates": rate_ids_of(current),
    }
    for key, value in (patch or {}).items():
        if key in CATEGORY_FIELDS:
            body[key] = value
    body["taxrates"] = [int(item) for item in (body.get("taxrates") or [])]
    return body


def category_form_error(body: dict[str, Any]) -> str:
    if not text(body.get("code")):
        return "Kategori kodu zorunlu; ürün özniteliği bu kodla eşleşir."
    if not text(body.get("name")):
        return "Kategori adı zorunlu."
    if not body.get("taxrates"):
        return ("Kategoriye en az bir oran bağlanmalı: oransız kategori atanan "
                "üründen vergi hesaplamaz.")
    return ""


# ================================================================= bölgeler

def region_rows(rates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Oranlardan TÜRETİLMİŞ bölge görünümü.

    Bagisto'da ayrı bir "vergi bölgesi" kaydı YOKTUR — kapsam oranın üzerinde
    durur. Ayrı bir bölge listesi uydurmak yerine oranlar ülke/eyalet kırılımına
    toplanır: "hangi bölgeye hangi oranlar bakıyor" sorusu tek ekranda cevaplanır.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rates:
        key = (row["country"], row["state"])
        bucket = buckets.setdefault(key, {
            "country": row["country"], "countryLabel": row["countryLabel"],
            "state": row["state"], "label": row["region"],
            "rates": [], "percentages": [],
        })
        bucket["rates"].append({"id": row["id"], "name": row["name"],
                                "percent": row["percent"],
                                "percentLabel": row["percentLabel"],
                                "zipLabel": row["zipLabel"]})
        if row["percent"] is not None:
            bucket["percentages"].append(row["percent"])

    out = []
    for bucket in buckets.values():
        percentages = sorted(set(bucket["percentages"]))
        bucket["rateCount"] = len(bucket["rates"])
        bucket["percentages"] = percentages
        bucket["percentLabel"] = " · ".join(rate_label(item) for item in percentages) or "—"
        out.append(bucket)
    return sorted(out, key=lambda item: (item["country"], item["state"]))


# =========================================================== çakışma denetimi

def conflicts(rates: list[dict[str, Any]],
              categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aynı adrese iki farklı oran uyan kuralları bulur (TUZAK 4).

    ÇAKIŞMA NEDEN KATEGORİ İÇİNDE ARANIR: mağaza ürüne önce vergi kategorisini
    bakar, sonra o kategorinin oranları içinden adrese uyanı seçer. Farklı
    kategorilerdeki iki oranın aynı adresi karşılaması normaldir (kitap %0,
    kalem %20). Aynı kategoride iki oran aynı adrese uyuyorsa hangisinin
    uygulanacağı BELİRSİZDİR ve Bagisto bunu söylemez.

    Aynı yüzdeye sahip iki örtüşen oran çakışma sayılmaz: sonuç aynı olduğu
    için müşteri doğru tutarı öder; yalnız gereksiz kayıttır ve `duplicate`
    işaretiyle bildirilir.
    """
    by_id = {row["id"]: row for row in rates}
    out: list[dict[str, Any]] = []
    for category in categories:
        members = [by_id[item] for item in category["rateIds"] if item in by_id]
        for index, left in enumerate(members):
            for right in members[index + 1:]:
                if left["country"] != right["country"]:
                    continue
                if text(left["state"]) and text(right["state"]) \
                        and left["state"] != right["state"]:
                    continue
                if not zip_overlap(left["zip"], right["zip"]):
                    continue
                same = left["percent"] == right["percent"]
                out.append({
                    "categoryId": category["id"],
                    "categoryName": category["name"],
                    "rateIds": [left["id"], right["id"]],
                    "rateNames": [left["name"], right["name"]],
                    "percents": [left["percent"], right["percent"]],
                    "region": left["region"],
                    "kind": "duplicate" if same else "conflict",
                    "message": (
                        f"`{left['name']}` ve `{right['name']}` aynı bölgeyi karşılıyor; "
                        + ("oranları aynı olduğu için tutar değişmez ama kayıtlardan biri "
                           "gereksizdir." if same else
                           f"biri {left['percentLabel']}, diğeri {right['percentLabel']} "
                           "uyguluyor ve mağaza hangisini seçtiğini söylemiyor.")
                    ),
                })
    return out


def mark_flags(rates: list[dict[str, Any]], categories: list[dict[str, Any]],
               clashes: list[dict[str, Any]], *, membership_known: bool = True) -> None:
    """Satırlara süzgeç anahtarlarını YERİNDE yazar.

    `unassigned` üç durumda açılır ve hangisi olduğu `unassignedReason`
    alanında yazar: sayı yoksa "bilinmiyor" denir, sıfır UYDURULMAZ.

    `membership_known` False ise kategori↔oran ilişkisi mağazadan okunamamıştır;
    hiçbir oran "bağlantısız" işaretlenmez — okuyamadığımız şeyi yok saymak
    kullanıcıyı olmayan bir sorunu düzeltmeye koşturur.
    """
    counts = {row["id"]: row["productCount"] for row in categories}
    clashing: set[int] = set()
    for item in clashes:
        if item["kind"] == "conflict":
            clashing.update(item["rateIds"])

    for row in rates:
        row["conflict"] = row["id"] in clashing
        if not membership_known:
            row["unassigned"] = False
            row["unassignedReason"] = ("Vergi kategorisi bağlantısı mağaza liste ucundan "
                                       "okunamıyor.")
            continue
        if not row["categoryIds"]:
            row["unassigned"] = True
            row["unassignedReason"] = "Hiçbir vergi kategorisine bağlı değil."
            continue
        known = [counts.get(item) for item in row["categoryIds"]]
        if any(item is None for item in known):
            row["unassigned"] = False
            row["unassignedReason"] = "Ürün sayısı henüz taranmadı."
            continue
        total = sum(item or 0 for item in known)
        row["unassigned"] = total == 0
        row["unassignedReason"] = ("Bağlı olduğu kategorilerde ürün yok."
                                   if total == 0 else "")


# ============================================================= ürün eşlemesi

def product_row(raw: dict[str, Any], *, categories: dict[int, str]) -> dict[str, Any]:
    """Eşleme tablosunun satırı — ürün + şu anki vergi kategorisi.

    TUZAK: ürün LİSTESİ ucu `tax_category_id` alanını DOLDURMUYOR. Canlıda
    (2026-08-13) 1428 numaralı ürün liste ucunda `"taxCategoryId": null`,
    tekil uçta `"taxCategoryId": 1` dönüyor — yani `null` "atanmamış" demek
    değil, "liste bunu taşımıyor" demek. 0 yazıp "Atanmamış" demek YALAN olurdu.
    `taxKnown` bunu ayırır; ekran bilinmeyeni `?` gösterir ve kesin değer
    önizlemede (ürün tek tek okunurken) gelir.
    """
    raw_value = attribute(raw, "tax_category_id")
    known = raw_value is not None
    current = as_int(raw_value)
    if not known:
        label = "Bilinmiyor"
    elif current:
        label = categories.get(current) or f"#{current}"
    else:
        label = "Atanmamış"
    return {
        "id": as_int(raw.get("id")),
        "sku": text(attribute(raw, "sku")),
        "name": text(attribute(raw, "name")) or "(adsız)",
        "type": text(attribute(raw, "type")) or "simple",
        "taxCategoryId": current,
        "taxCategoryName": label,
        "taxKnown": known,
        "price": to_kurus(attribute(raw, "price")),
    }


def usage_counts(rows: list[dict[str, Any]]) -> tuple[dict[int, int], bool]:
    """Katalog taramasından vergi kategorisi başına ürün sayısı.

    İkinci dönüş değeri: alan HİÇ görülmedi mi. Görülmediyse sayılar
    yazılmaz — sıfırları kaydetmek "hiçbir ürüne atanmamış" süzgecini
    tüm kategoriler için yakar ve kullanıcıyı var olmayan bir soruna koşturur.
    """
    counts: dict[int, int] = {}
    seen = False
    for row in rows:
        value = attribute(row, "tax_category_id")
        if value is None:
            continue
        seen = True
        counts[as_int(value)] = counts.get(as_int(value), 0) + 1
    return counts, seen


def attribute(raw: Any, *names: str) -> Any:
    """Ürün kaydında bir özniteliği nerede olursa olsun bulur.

    Bagisto admin API'si aynı ürünü iki biçimde verebiliyor: düz sözlük ya da
    EAV zarfı (`{"values": {"common": {...}}}`). Hangisinin geleceği ürün
    tipine ve sürüme göre değişiyor. Alan adı da iki biçimde gelir: yanıt
    camelCase (`taxCategoryId`, `urlKey`), istek snake_case (TUZAK 7).
    """
    if not isinstance(raw, dict):
        return None
    values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
    sources: list[dict[str, Any]] = [raw]
    for key in ("common", "channel_locale_specific", "locale_specific", "channel_specific"):
        nested = values.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    if values:
        sources.append(values)
    wanted: list[str] = []
    for name in names:
        for key in (name, camel(name)):
            if key not in wanted:
                wanted.append(key)
    for name in wanted:
        for source in sources:
            if name in source and source[name] not in (None, ""):
                return source[name]
    for name in wanted:
        for source in sources:
            if name in source:
                return source[name]
    return None


def assign_rows(products: list[dict[str, Any]], *, category_id: int,
                category_name: str) -> list[dict[str, Any]]:
    """FARK TABLOSU. Toplu eşleme bu tablo gösterilmeden UYGULANMAZ."""
    out: list[dict[str, Any]] = []
    for row in products:
        before = row["taxCategoryId"]
        skipped = before == int(category_id)
        out.append({
            "id": row["id"],
            "sku": row["sku"],
            "name": row["name"],
            "beforeId": before,
            "before": row["taxCategoryName"],
            "afterId": int(category_id),
            "after": category_name,
            "skipped": skipped,
            "note": "Zaten bu kategoride." if skipped else "",
        })
    return out


def assign_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    changed = [row for row in rows if not row["skipped"]]
    return {
        "total": len(rows),
        "changed": len(changed),
        "skipped": len(rows) - len(changed),
        "fromUnassigned": len([row for row in changed if not row["beforeId"]]),
    }


def product_body(current: dict[str, Any], *, tax_category_id: int, channel: str,
                 locale: str) -> dict[str, Any]:
    """Ürün gövdesi — OKU-DEĞİŞTİR-YAZ, yalnız vergi kategorisi değişir (TUZAK 6).

    `channel` + `locale` HER İSTEKTE gider: Bagisto EAV'de bir öznitelik kanal
    ve dile göre saklanabiliyor ve ikisi verilmezse yazma varsayılan kanala
    düşüyor. `attribute_family_id` hiçbir koşulda gönderilmez — aile
    değişikliği ürünün öznitelik kümesini değiştirir, vergi ekranının işi değil.
    """
    body: dict[str, Any] = {}
    for key in PRODUCT_KEEP:
        value = attribute(current, key)
        if value is None:
            continue
        if key == "price":
            value = from_kurus(to_kurus(value) or 0)
        elif isinstance(value, bool):
            # Yanıt `visibleIndividually: true` veriyor; istek şeması 0/1 bekliyor.
            value = int(value)
        body[key] = value
    body["tax_category_id"] = int(tax_category_id)
    body["channel"] = channel
    body["locale"] = locale
    body.pop("attribute_family_id", None)
    return body
