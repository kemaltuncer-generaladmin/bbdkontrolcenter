"""Nitelik (öznitelik) ve aile verisinin saf dönüşümleri — ağa çıkmaz.

NİTELİK KATALOĞUN ŞEMASIDIR, VERİSİ DEĞİL. Ürün silmek bir satır götürür;
nitelik silmek o niteliğin BÜTÜN ürünlerdeki değerini götürür ve geri
alınamaz. Bu dosyadaki kuralların çoğu bunu engellemek için var.

BEŞ KURAL — hepsinin karşılığı burada bir fonksiyondur:

 1. KOD ve TİP oluşturulduktan sonra DEĞİŞMEZ. Bagisto kodu ürün değer
    tablolarında anahtar olarak kullanıyor; tip ise değerin hangi sütunda
    (`text_value`/`integer_value`/…) durduğunu belirliyor. İkisini de
    `attribute_body` güncellemede GÖNDERMEZ, `locked_error` ise deneyeni
    açık bir mesajla durdurur.
 2. SİSTEM NİTELİĞİ (isUserDefined=0) silinmez, kodu/tipi değişmez — sunucu
    403 döner. `delete_verdict` bunu ekrana ret gerekçesi olarak söyler.
 3. KULLANIMDAKİ NİTELİK SİLİNMEZ. Kullanım, niteliğin bağlı olduğu
    ailelerdeki ÜRÜN SAYISIDIR (`usage_index`); bilinmiyorsa sıfır sayılmaz.
 4. SİLME YERİNE PASİFLEŞTİRME (ADR 0012). `deactivate_patch` niteliği ve
    değerlerini yerinde bırakır; yalnız vitrin/süzgeç/zorunluluk bayraklarını
    indirir.
 5. AİLE GÜNCELLENİRKEN `attribute_groups` GÖNDERİLİRSE grup düzeni
    GÖNDERİLENLE DEĞİŞİR. Boş liste göndermek grupları siler; `family_body`
    bu yüzden gruplar açıkça verilmedikçe alanı hiç koymaz.

Uzak uçtan gelen alan adları camelCase (`adminName`, `isRequired`), Bagisto'ya
giden gövde snake_case (`admin_name`, `is_required`). İkisi de burada çevrilir;
ekran hiçbirini bilmez.
"""

from __future__ import annotations

import re
from typing import Any

#: Nitelik tipleri ve Türkçe adları. Ekranda İngilizce tip adı görünmez.
TYPE_LABELS = {
    "text": "Metin",
    "textarea": "Uzun metin",
    "price": "Fiyat",
    "boolean": "Evet/Hayır",
    "select": "Tek seçim",
    "multiselect": "Çok seçim",
    "checkbox": "Onay kutusu",
    "date": "Tarih",
    "datetime": "Tarih ve saat",
    "image": "Görsel",
    "file": "Dosya",
}

#: Seçenek listesi taşıyan tipler. Seçenek yönetimi YALNIZ bunlarda açılır.
OPTION_TYPES = ("select", "multiselect", "checkbox")

#: Kod olarak kullanılamayan kelimeler. `type` ve `attribute_family_id` ürün
#: gövdesinde ayrı anlam taşıyor; nitelik kodu olarak açılırsa ürün kaydetme
#: sessizce bozulur (kaynak: store_api/client.create_attribute).
RESERVED_CODES = ("type", "attribute_family_id", "id", "sku_type", "channel", "locale")

#: Kod deseni. Bagisto kodu sütun/anahtar adı gibi kullanıyor.
_CODE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")

#: Aileden çıkarılamayacak nitelikler. Ürün kaydı bunlar olmadan açılmıyor;
#: aileden düşürmek "ürün eklenemiyor" diye dönen bir arıza üretir.
CORE_CODES = ("sku", "name", "url_key", "status", "visible_individually",
              "short_description", "description", "weight", "guest_checkout")


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def flag(value: Any) -> bool:
    """Bagisto bayrakları 0/1, "0"/"1", true/false olarak gelebiliyor."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    raw = str(value).strip().lower()
    return raw in ("1", "true", "yes", "on")


def pick(raw: Any, *names: str) -> Any:
    """Alanı camelCase ya da snake_case adıyla bulur.

    Liste ucu camelCase veriyor (canlıda doğrulandı), ama aynı kayıt başka
    sürümlerde snake_case gelebiliyor. Çağıranın ikisini de denemesi
    gerekseydi her okuma iki satır olurdu.
    """
    if not isinstance(raw, dict):
        return None
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return None


# ============================================================== nitelik satırı

def attribute_row(raw: dict[str, Any], *, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ham nitelik kaydı → tablonun beklediği satır.

    `usage` `usage_index` çıktısının bu niteliğe ait parçasıdır; verilmezse
    kullanım "bilinmiyor" olarak (`None`) döner — SIFIR DEĞİL. Bilinmeyeni
    sıfır saymak, silme kapısını yanlışlıkla açardı.
    """
    kind = text(pick(raw, "type")) or "text"
    system = not flag(pick(raw, "isUserDefined", "is_user_defined"))
    options = pick(raw, "options")
    option_count = len([item for item in options if isinstance(item, dict)]) \
        if isinstance(options, list) else None

    return {
        "id": as_int(pick(raw, "id")),
        "code": text(pick(raw, "code")),
        "name": text(pick(raw, "adminName", "admin_name", "name")) or text(pick(raw, "code")),
        "type": kind,
        "typeLabel": TYPE_LABELS.get(kind, kind),
        "required": flag(pick(raw, "isRequired", "is_required")),
        "unique": flag(pick(raw, "isUnique", "is_unique")),
        "filterable": flag(pick(raw, "isFilterable", "is_filterable")),
        "configurable": flag(pick(raw, "isConfigurable", "is_configurable")),
        "visibleOnFront": flag(pick(raw, "isVisibleOnFront", "is_visible_on_front")),
        "comparable": flag(pick(raw, "isComparable", "is_comparable")),
        "perLocale": flag(pick(raw, "valuePerLocale", "value_per_locale")),
        "perChannel": flag(pick(raw, "valuePerChannel", "value_per_channel")),
        "position": as_int(pick(raw, "position")),
        "system": system,
        # Kod ve tip HER ZAMAN kilitlidir — sistem niteliğinde de, kullanıcı
        # niteliğinde de. Kayıt açıldıktan sonra ikisi de değiştirilemez.
        "locked": True,
        "hasOptions": kind in OPTION_TYPES,
        "optionCount": option_count,
        "usageProducts": (usage or {}).get("products"),
        "usageFamilies": list((usage or {}).get("families") or []),
        "updatedAt": text(pick(raw, "updatedAt", "updated_at"))[:19],
    }


def option_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Nitelik detayındaki seçenekler. AYRI LİSTE UCU YOK (geçit notu)."""
    options = pick(raw, "options")
    if not isinstance(options, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in options:
        if not isinstance(item, dict):
            continue
        rows.append({
            "id": as_int(pick(item, "id")),
            "name": text(pick(item, "adminName", "admin_name", "label")),
            "sortOrder": as_int(pick(item, "sortOrder", "sort_order")),
            "swatch": text(pick(item, "swatchValue", "swatch_value")),
        })
    rows.sort(key=lambda row: (row["sortOrder"], row["name"].casefold()))
    return rows


# ================================================================== kullanım

def usage_index(families: list[dict[str, Any]],
                counts: dict[int, int]) -> dict[int, dict[str, Any]]:
    """Nitelik → {ürün sayısı, aile adları}.

    NEYİ SAYAR: niteliğin bağlı olduğu ailelerdeki ÜRÜN SAYISINI. "Değeri
    dolu olan ürün" sayısı DEĞİLDİR — onu öğrenmek 1.419 ürünü tek tek
    okumayı gerektirir (29 sayfa + 1.419 detay isteği) ve hız sınırını bir
    ekran açılışına harcar. Silme kararı için doğru ölçü zaten budur: nitelik
    silinince değeri o ailedeki her üründen düşer, dolu olsun olmasın.

    `counts` aile başına ürün toplamıdır; bir aile için sayı yoksa o aile
    toplama KATILMAZ ve nitelik "bilinmiyor" (`None`) kalır.
    """
    index: dict[int, dict[str, Any]] = {}
    for family in families:
        family_id = as_int(pick(family, "id"))
        name = text(pick(family, "name")) or text(pick(family, "code")) or f"#{family_id}"
        known = family_id in counts
        for group in _groups(family):
            for item in group.get("attributes") or []:
                if not isinstance(item, dict):
                    continue
                attribute_id = as_int(pick(item, "id"))
                if not attribute_id:
                    continue
                slot = index.setdefault(attribute_id, {"products": 0, "families": [],
                                                       "unknown": False})
                if name not in slot["families"]:
                    slot["families"].append(name)
                if known:
                    slot["products"] += int(counts[family_id])
                else:
                    slot["unknown"] = True

    for slot in index.values():
        if slot.pop("unknown", False):
            # Bir ailenin ürün sayısı okunamadıysa toplam eksiktir; eksik
            # toplamı "kullanım" diye göstermek silme kapısını yanlış açar.
            slot["products"] = None
    return index


def _groups(family: Any) -> list[dict[str, Any]]:
    groups = pick(family, "attributeGroups", "attribute_groups")
    return [item for item in groups if isinstance(item, dict)] if isinstance(groups, list) else []


# ================================================================ doğrulama

def code_error(code: str) -> str:
    """Yeni nitelik kodu kabul edilebilir mi — kullanıcıya gösterilecek metin."""
    value = text(code)
    if not value:
        return "Nitelik kodu zorunlu; kod sonradan DEĞİŞTİRİLEMEZ, dikkatli seçin."
    if value.lower() in RESERVED_CODES:
        return (f"`{value}` rezerve bir kelime; ürün gövdesinde başka anlam taşıyor ve "
                "kod olarak kullanılırsa ürün kaydetme bozulur.")
    if not _CODE.fullmatch(value):
        return ("Kod küçük harfle başlamalı; yalnız küçük harf, rakam ve alt çizgi "
                "içerebilir (2-50 karakter). Örnek: `raf_kodu`.")
    return ""


def type_error(kind: str) -> str:
    value = text(kind)
    if value not in TYPE_LABELS:
        return (f"Bilinmeyen nitelik tipi: `{value or '(boş)'}`. "
                f"Seçilebilirler: {', '.join(TYPE_LABELS)}.")
    return ""


def locked_error(current: dict[str, Any], patch: dict[str, Any]) -> str:
    """Kod/tip değiştirilmeye çalışıldı mı (K9: ekranda kilitlemek yetmez).

    Ekran alanı kilitler ama kilit arayüzdedir; istek elle de kurulabilir.
    Backend aynı kuralı tekrar uygular ve NEDEN olmadığını söyler.
    """
    wanted_code = text(patch.get("code"))
    if wanted_code and wanted_code != text(pick(current, "code")):
        return ("Nitelik kodu oluşturulduktan sonra değiştirilemez: kod, ürün değer "
                "tablolarında anahtar olarak kullanılıyor ve değiştirilirse o niteliğin "
                "bütün ürünlerdeki değeri öksüz kalır. Yeni kod gerekiyorsa yeni nitelik "
                "açıp değerleri taşımak gerekir.")
    wanted_type = text(patch.get("type"))
    if wanted_type and wanted_type != text(pick(current, "type")):
        return ("Nitelik tipi oluşturulduktan sonra değiştirilemez: tip, değerin hangi "
                "sütunda saklandığını belirliyor; değiştirmek mevcut değerleri okunamaz "
                "hâle getirir.")
    return ""


def delete_verdict(row: dict[str, Any], *, families_known: bool = False) -> dict[str, Any]:
    """Nitelik silinebilir mi — silinemiyorsa NEDEN ve NE YAPILACAĞI ile.

    İKİ AYRI SORU, İKİ AYRI CEVAP — karıştırılırsa kapı yanlış açılır/kapanır:

     1. NİTELİK HANGİ AİLELERDE? (`usageFamilies`) Silmenin veri götürüp
        götürmeyeceğini bu belirler. Hiçbir ailede geçmiyorsa hiçbir ürün
        şemasında yoktur ve silmek ürün verisi götürmez.
     2. O AİLELERDE KAÇ ÜRÜN VAR? (`usageProducts`) Bu yalnızca ETKİNİN
        BÜYÜKLÜĞÜDÜR; kullanıcıya "1.420 ürünü etkiler" diyebilmek için var.

    `families_known` birinci sorunun cevabının GÜVENİLİR olduğunu söyler:
    aile listesi ve HER ailenin nitelik düzeni okunabildi mi. Okunamayan tek
    bir aile bile niteliği taşıyor olabilir; o hâlde boş `usageFamilies`
    "kullanılmıyor" değil "bilinmiyor" demektir ve belirsizlik "hayır"dır.

    Bu ayrım olmadan iki durum birbirinden ayrılamıyordu: "aileler okundu,
    nitelik hiçbirinde yok" (silinebilir) ile "aileler okunamadı" (silinemez).
    İkisi de boş bir kullanım listesi üretiyor.
    """
    if row.get("system"):
        return {"allowed": False,
                "reason": "Bu bir SİSTEM niteliği (mağaza kurulumuyla geliyor); silinemez — "
                          "mağaza isteği reddeder.",
                "alternative": "Vitrinde ve süzgeçte görünmesini istemiyorsanız pasifleştirin."}

    families = [name for name in (row.get("usageFamilies") or []) if name]
    if not families:
        if not families_known:
            return {"allowed": False,
                    "reason": "Ailelerin nitelik düzeni okunamadı; bu niteliğin bir ailede "
                              "kullanılıp kullanılmadığı BİLİNMİYOR. Bilinmeyeni "
                              "“kullanılmıyor” sayıp silmek, geri alınamayan veri kaybı "
                              "riskidir.",
                    "alternative": "Bağlantı düzelince yeniden bakın; şimdilik "
                                   "pasifleştirebilirsiniz."}
        return {"allowed": True,
                "reason": "Bu nitelik hiçbir ailede kullanılmıyor; silmek ürün verisi götürmez.",
                "alternative": ""}

    usage = row.get("usageProducts")
    if usage is None:
        return {"allowed": False,
                "reason": f"Bu nitelik {', '.join(families)} ailesinde tanımlı ama kaç ürünü "
                          "etkileyeceği okunamadı. Bilinmeyeni sıfır sayıp silmek, geri "
                          "alınamayan veri kaybı riskidir.",
                "alternative": "Bağlantı düzelince yeniden bakın; şimdilik pasifleştirebilirsiniz."}
    if usage > 0:
        return {"allowed": False,
                "reason": f"Bu nitelik {usage} ürünün ailesinde tanımlı "
                          f"({', '.join(families)}). Silmek, "
                          "niteliğin o ürünlerdeki bütün değerlerini de siler ve geri alınamaz.",
                "alternative": "Silme yerine PASİFLEŞTİRİN: nitelik ve değerleri yerinde kalır, "
                               "yalnız vitrinden ve süzgeçten çıkar."}
    return {"allowed": True,
            "reason": f"Bu nitelik {', '.join(families)} ailesinde tanımlı ama o ailede hiç "
                      "ürün yok; silmek ürün verisi götürmez.",
            "alternative": ""}


def deactivate_patch(row: dict[str, Any]) -> dict[str, Any]:
    """Pasifleştirme yaması — SİLME YERİNE (ADR 0012).

    Bagisto'da niteliğin "pasif" bayrağı YOKTUR; bu yüzden pasifleştirme
    üç bayrağı indirmek demektir: vitrinde görünme, süzgeçte çıkma ve ürün
    kaydında zorunlu olma. Nitelik ve ürünlerdeki DEĞERLERİ yerinde kalır;
    işlem geri alınabilir (bayrakları geri açmak yeter).

    Zaten hepsi kapalıysa boş sözlük döner ve çağıran "değişen yok" der.
    """
    patch: dict[str, Any] = {}
    if row.get("visibleOnFront"):
        patch["is_visible_on_front"] = 0
    if row.get("filterable"):
        patch["is_filterable"] = 0
    if row.get("required"):
        patch["is_required"] = 0
    return patch


def deactivate_summary(row: dict[str, Any]) -> str:
    """Pasifleştirmenin ne yapacağını tek cümlede söyler; ekran bunu onayda gösterir."""
    parts = []
    if row.get("visibleOnFront"):
        parts.append("vitrinde görünmeyecek")
    if row.get("filterable"):
        parts.append("süzgeçlerden çıkacak")
    if row.get("required"):
        parts.append("ürün kaydında zorunlu olmayacak")
    if not parts:
        return "Bu nitelik zaten pasif: vitrinde görünmüyor, süzgeçte yok ve zorunlu değil."
    return (f"`{row.get('code')}` {', '.join(parts)}. Nitelik ve ürünlerdeki değerleri "
            "SİLİNMEZ; bayrakları geri açarak işlem geri alınabilir.")


# ============================================================== yazma gövdesi

#: Ekranın camelCase adları → Bagisto nitelik alanları.
FIELD_ALIASES = {
    "name": "admin_name",
    "required": "is_required",
    "unique": "is_unique",
    "filterable": "is_filterable",
    "configurable": "is_configurable",
    "visibleOnFront": "is_visible_on_front",
    "comparable": "is_comparable",
    "perLocale": "value_per_locale",
    "perChannel": "value_per_channel",
    "position": "position",
    "defaultValue": "default_value",
    "validation": "validation",
}

_BOOL_FIELDS = ("is_required", "is_unique", "is_filterable", "is_configurable",
                "is_visible_on_front", "is_comparable", "value_per_locale", "value_per_channel")


def attribute_body(patch: dict[str, Any], *, creating: bool = False,
                   code: str = "", kind: str = "") -> dict[str, Any]:
    """Nitelik yazma gövdesi.

    `code` ve `type` YALNIZCA oluştururken konur (KURAL 1). Güncellemede
    gönderilmemesi bir tercih değil kural: Bagisto gönderilen alanı işliyor
    ve sistem niteliğinde 403, kullanıcı niteliğinde sessiz şema değişikliği
    üretiyor.
    """
    body: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        target = FIELD_ALIASES.get(key)
        if not target or value is None:
            continue
        body[target] = (1 if flag(value) else 0) if target in _BOOL_FIELDS else value

    if creating:
        body["code"] = text(code)
        body["type"] = text(kind)
        if text(kind) in OPTION_TYPES:
            # Seçim tipinde seçenek listesi ayrı uçtan eklenir; boş liste
            # göndermek bazı sürümlerde "en az bir seçenek" hatası veriyor.
            body.pop("options", None)
    else:
        body.pop("code", None)
        body.pop("type", None)
    return body


def option_body(name: str, *, sort_order: int = 0, swatch: str = "") -> dict[str, Any]:
    body: dict[str, Any] = {"admin_name": text(name)}
    if sort_order:
        body["sort_order"] = int(sort_order)
    if text(swatch):
        body["swatch_value"] = text(swatch)
    return body


# ==================================================================== aile

def family_row(raw: dict[str, Any], *, product_count: int | None = None) -> dict[str, Any]:
    """Aile listesi satırı.

    LİSTE UCU `attributeGroups` ALANINI NULL VERİYOR (canlıda doğrulandı);
    grup/nitelik sayıları yalnız detay okunduğunda dolar. Bilinmeyen sayı
    `None` döner ve ekran `—` gösterir — sıfır uydurulmaz.
    """
    groups = _groups(raw)
    has_detail = isinstance(pick(raw, "attributeGroups", "attribute_groups"), list)
    attributes = sum(len(group.get("attributes") or []) for group in groups)
    return {
        "id": as_int(pick(raw, "id")),
        "code": text(pick(raw, "code")),
        "name": text(pick(raw, "name")) or text(pick(raw, "code")),
        "groupCount": len(groups) if has_detail else None,
        "attributeCount": attributes if has_detail else None,
        "productCount": product_count,
    }


def family_detail(raw: dict[str, Any]) -> dict[str, Any]:
    """Aile detayı: gruplar ve her grubun nitelikleri, sırayla."""
    groups: list[dict[str, Any]] = []
    for group in _groups(raw):
        items = []
        for item in group.get("attributes") or []:
            if not isinstance(item, dict):
                continue
            kind = text(pick(item, "type")) or "text"
            items.append({
                "id": as_int(pick(item, "id")),
                "code": text(pick(item, "code")),
                "type": kind,
                "typeLabel": TYPE_LABELS.get(kind, kind),
                "required": flag(pick(item, "isRequired", "is_required")),
                "position": as_int(pick(item, "position")),
                "core": text(pick(item, "code")) in CORE_CODES,
            })
        items.sort(key=lambda row: row["position"])
        groups.append({
            "id": as_int(pick(group, "id")),
            "code": text(pick(group, "code")),
            "name": text(pick(group, "name")) or text(pick(group, "code")),
            "column": as_int(pick(group, "column"), 1),
            "position": as_int(pick(group, "position")),
            "attributes": items,
        })
    groups.sort(key=lambda row: (row["position"], row["name"].casefold()))
    return {"id": as_int(pick(raw, "id")), "code": text(pick(raw, "code")),
            "name": text(pick(raw, "name")), "groups": groups}


def family_body(*, name: str = "", code: str = "", creating: bool = False,
                groups: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Aile yazma gövdesi (KURAL 5).

    `groups` VERİLMEDİKÇE `attribute_groups` gövdeye KONMAZ. Boş liste
    göndermek ailenin bütün gruplarını siler; "yalnız adı değiştir" isteği
    kataloğun şemasını boşaltırdı.
    """
    body: dict[str, Any] = {}
    if text(name):
        body["name"] = text(name)
    if creating:
        body["code"] = text(code)
    if groups is None:
        return body

    body["attribute_groups"] = [
        {
            "code": text(group.get("code")) or text(group.get("name")).lower().replace(" ", "_"),
            "name": text(group.get("name")),
            "column": as_int(group.get("column"), 1),
            "position": as_int(group.get("position")),
            "custom_attributes": [{"id": as_int(item)} for item in (group.get("attributeIds") or [])
                                  if as_int(item)],
        }
        for group in groups
    ]
    return body


def family_guard(groups: list[dict[str, Any]]) -> str:
    """Aile düzeni kaydedilebilir mi — çekirdek nitelik düşürülüyorsa reddeder.

    Ürün kaydı `sku`, `name`, `status` gibi nitelikler olmadan açılmıyor.
    Bunları aileden çıkarmak, ekranda görünmeyen ama ürün eklemeyi tümden
    durduran bir arıza üretir; kullanıcı sebebini asla bulamaz.
    """
    if not groups:
        return "Ailede en az bir grup olmalı; boş liste göndermek ailenin şemasını siler."
    codes = {text(item.get("code")) for group in groups
             for item in (group.get("attributes") or []) if isinstance(item, dict)}
    ids = {as_int(item) for group in groups for item in (group.get("attributeIds") or [])}
    if not codes and not ids:
        return "Ailede hiç nitelik kalmadı; ürün kaydı bu aileyle açılamaz."
    if codes:
        missing = [code for code in CORE_CODES if code not in codes]
        if missing:
            return (f"Şu çekirdek nitelikler aileden çıkarılamaz: {', '.join(missing)}. "
                    "Olmadan ürün eklenemez ve sebebi ekranda görünmez.")
    return ""
