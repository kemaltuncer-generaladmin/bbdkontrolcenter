"""Menü verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın kararlarının çoğu tek bir sözlüğe bakıp "bu değer
kabul edilir mi, ekranda ne yazar" sorusuna cevap vermekten ibaret: bir tarihin
geçmişte olup olmadığı, bir kesim saatinin `HH:mm` olup olmadığı, bir günün
yayınlanmaya hazır olup olmadığı, bir tavan tablosunun kaç kalemi satılmışın
altında bıraktığı. Servise gömülselerdi tek satırı bile ağ taklidi olmadan
sınanamazdı; burada hepsi girdi→çıktı fonksiyonudur.

ALAN ADLARI snake_case KALIR. `docs/control/menu.md` telin sözlüğünü sabitliyor
ve aynı adlar üç yerde birden yaşıyor: BLD göçünde, `DailyMenuPresenter`
çıktısında ve bu ekranda. Arada camelCase'e çevirmek dördüncü bir sözlük
yaratır ve yanlış çeviri SESSİZ kalır. TEK İSTİSNA istek gövdesindeki `dryRun`
alanıdır (panel→Kontrol Merkezi sınırı, `store_orders` deseni) ve çevirisi
`api/routes.py` içinde tek satırdır.

BEŞ TUZAK — her birinin karşılığı burada bir fonksiyondur:

 1. `null` TAVAN İLE SIFIR TAVAN AYRI ŞEYDİR   → `capacity_error` `None`
    (`null` = sınırsız, `0` = "bugün               kabul eder ve `capacity_view`
    satılmıyor")                                 `null` kalanı `None` bırakır;
                                                 sıfıra çevirmek sınırsız bir
                                                 günü kapatırdı.
 2. `remaining` NEGATİFE DÜŞMEZ                → `stock_line` `max(0, …)`
    (sözleşme: kalan `0` olur, gerçek            uygular ve gerçeği `oversold`
    bilgi `oversold` bayrağıdır)                 bayrağıyla taşır.
 3. `sold` REZERVEDİR, TESLİM DEĞİL            → `stock_line` `sold_orders` ile
    ve abonelik payı AYRI gösterilir             `sold_subscriptions`'ı ayrı
                                                 tutar; toplamak, "34'ün 20'si
                                                 aboneliğe ayrılmış" bilgisini
                                                 yok ederdi.
 4. `sold_out` (mutfağın elle koyduğu işaret)  → `stock_line` ikisini AYRI
    ile `full` (tavan doldu) AYRI şeylerdir      alan olarak taşır.
 5. YAYIN ÖN DENETİMİ EKRANDA DA KOŞAR         → `publish_error`. Sunucu zaten
                                                 reddediyor; buradaki kapı
                                                 kullanıcıya 422 yerine cümle
                                                 gösterir (K9 — çift kapı).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# ============================================================ gerekçe ve aktör

#: Gerekçenin en az uzunluğu (`00-genel.md` §3). BLD tarafı da 10 istiyor;
#: burada TEKRAR doğrulanır çünkü arayüzde alanı zorunlu göstermek
#: yetkilendirme değildir (K9) ve istemci gövdeyi elle kurabilir.
#:
#: SINIR AYNI, KAPSAM DAR: gerekçe artık her yazmada değil, DÖRT uçta isteniyor
#: (yayınlama · yayından çekme · gün silme · kopyalama). Ölçüt `api/routes.py`
#: başlığında. Sınırı gevşetmek yanlış çözüm olurdu — kısa gerekçe sorunu
#: gerekçenin KENDİSİNDE değil, gereksiz yere sorulmasındaydı.
MIN_REASON = 10

#: En çok. Panel uçlarının sınırı 500'dür (`00-genel.md` §3); sipariş
#: revizyonundaki 160'lık sınır BU ALANDA GEÇERLİ DEĞİL ve buraya
#: kopyalanmaz — uydurulmuş bir sınır, sunucunun kabul edeceği bir gerekçeyi
#: reddeder ve kullanıcı nedenini hiçbir yerde okuyamaz.
MAX_REASON = 500

# =================================================================== durumlar

DRAFT = "draft"
PUBLISHED = "published"
STATUSES = (DRAFT, PUBLISHED)

STATUS_LABELS = {
    DRAFT: "Taslak",
    PUBLISHED: "Yayında",
}

#: Rozet tonu. Renk TEK BAŞINA anlam taşımaz (kit kuralı 7); ton yalnız yazının
#: yanında durur ve panel her rozetin içine metni de yazar.
STATUS_TONES = {
    DRAFT: "warn",
    PUBLISHED: "good",
}

#: `DailyMenuService` sabitlerinden gelen sipariş kapalılık nedenleri
#: (`menu.md` → `GET /calendar`). UYDURULMAZ: buraya sunucuda karşılığı olmayan
#: bir değer eklemek, ekranda hiç görünmeyecek bir etiket yazmak olurdu; eksik
#: bırakmak ise gelen değeri ham kod hâlinde göstermek.
NOT_ORDERABLE_REASONS = ("closed_day", "not_published", "cutoff_passed", "past", "too_far")

NOT_ORDERABLE_LABELS = {
    "closed_day": "Kapalı gün",
    "not_published": "Yayında değil",
    "cutoff_passed": "Kesim saati geçti",
    "past": "Geçmiş gün",
    "too_far": "İleri tarih sınırının ötesinde",
}

#: Sunucunun stok yanıtında dönebileceği uyarı kodları (`menu.md` → `PUT stock`).
WARNING_LABELS = {
    "capacity_below_sold": "Tavan satılmışın altında",
}

# ================================================================== biçimler

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

#: Kalem sırası ONAR ONAR artar. Araya kalem sokmayı bütün listeyi yeniden
#: numaralamadan mümkün kılar; sunucu da `sort_order` verilmediğinde mevcut en
#: büyüğe bunu ekliyor (`menu.md` → `POST items`). Sayı burada da duruyor çünkü
#: panel "araya ekle" yaparken kendi sırasını hesaplıyor.
SORT_STEP = 10

#: Alan uzunlukları — kolon genişlikleriyle birebir (`menu.md` → Şema).
MAX_TITLE = 120
MAX_DESCRIPTION = 500
MAX_INTERNAL_NOTE = 255
MAX_LABEL = 120

#: Bir pakette bir kalemden en çok kaç porsiyon. Sözleşme bir tavan yazmıyor;
#: buradaki sayı EKRANIN kapısıdır ve yazım hatasını yakalamak içindir —
#: "1" yerine "11" yazmak bir paketi on bir porsiyonluk yapardı.
MAX_QUANTITY = 99


def now_iso() -> str:
    """Şimdinin ISO 8601 UTC damgası. Yerel iz bunu kullanır."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def text(value: Any, limit: int = 0) -> str:
    """Boşlukları kırpılmış metin. `None` boş dizeye düşer."""
    out = str(value if value is not None else "").strip()
    return out[:limit] if limit and len(out) > limit else out


def as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "evet", "yes", "on"}


def opt_int(value: Any) -> int | None:
    """`None` KORUNUR. `null` tavan "sınırsız", `0` tavan "satılmıyor" demek;
    ikisini aynı sayıya indirmek satışı sessizce kapatır ya da açardı."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ================================================================ doğrulama

def reason_error(reason: str) -> str:
    """Gerekçe backend'de DE doğrulanır (K9): arayüzde zorunlu göstermek,
    istemcinin gövdeyi elle kurmasını engellemez.

    YALNIZ GEREKÇE İSTEYEN DÖRT UÇTAN çağrılır (`MenuService._guard`); gerekçe
    istemeyen uçlar `reason` parametresi bile almıyor."""
    clean = text(reason)
    if len(clean) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı. Denetim izinde "
                "'düzeltme' yazan bir satır, altı ay sonra hiçbir soruyu cevaplamıyor.")
    if len(clean) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir."
    return ""


def date_error(value: str) -> str:
    if not _DATE.match(text(value)):
        return "Tarih `YYYY-MM-DD` biçiminde olmalı."
    try:
        datetime.strptime(text(value), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return f"'{text(value)}' geçerli bir takvim günü değil."
    return ""


def cutoff_error(value: Any) -> str:
    """Güne özel kesim saati. BOŞ BIRAKILABİLİR — `null` "küresel ayar geçerli"
    demektir ve bu, alanın en sık kullanılan hâlidir."""
    if value is None or text(value) == "":
        return ""
    if not _TIME.match(text(value)):
        return "Kesim saati `HH:mm` biçiminde olmalı (00:00–23:59), yerel saat."
    return ""


def price_error(value: Any) -> str:
    """Paket fiyatı KURUŞTUR. `null` "paket satılmıyor", sıfır KABUL EDİLMEZ:
    "bedava paket" anlamına gelirdi ve `LineResolver` onu zaten reddediyor."""
    if value is None:
        return ""
    amount = opt_int(value)
    if amount is None:
        return "Paket fiyatı tam sayı kuruş olmalı."
    if amount <= 0:
        return ("Paket fiyatı sıfırdan büyük olmalı. Paket satılmayacaksa alanı "
                "boş bırakın — sıfır 'bedava paket' demektir ve sipariş yolu onu "
                "zaten reddediyor.")
    return ""


def capacity_error(value: Any) -> str:
    """Porsiyon tavanı. `null` = sınırsız, `0` = "bugün satılmıyor" ve GEÇERLİ."""
    if value is None:
        return ""
    amount = opt_int(value)
    if amount is None:
        return "Tavan tam sayı porsiyon olmalı."
    if amount < 0:
        return "Tavan negatif olamaz. Satışı kapatmak için sıfır yazın."
    return ""


def quantity_error(value: Any) -> str:
    amount = as_int(value, 0)
    if amount < 1:
        return "Porsiyon adedi en az 1 olmalı."
    if amount > MAX_QUANTITY:
        return f"Porsiyon adedi en çok {MAX_QUANTITY} olabilir."
    return ""


def past_error(value: str, today: str) -> str:
    """Geçmişe menü kurulmaz (`menu.md` → `POST /days`). Bugün SERBESTTİR:
    sabah kesim saatinden önce o günün menüsünü kurmak olağan iştir."""
    problem = date_error(value)
    if problem:
        return problem
    if text(value) < text(today):
        return (f"{text(value)} geçmişte kaldı; geçmiş bir güne menü kurulamaz. "
                "Geçmiş günün kaydını düzeltmek gerekiyorsa denetim izine bakın.")
    return ""


def publish_error(day: dict[str, Any]) -> str:
    """Yayın ön denetimi — sunucununkinin AYNASI (`menu.md` → publish).

    Buradaki kapı bir yetkilendirme değil, bir CÜMLE kurma işidir: sunucu aynı
    üç kuralı zaten uyguluyor ve 422 veriyor, ama "VALIDATION_FAILED" gören
    yönetici hangi kuralın çiğnendiğini bilemez. Ürünlerin satışta olup olmadığı
    BURADA DENETLENMEZ — o bilgi bu ekranda yok ve uydurmak, satışta olan bir
    ürünü satıştan kalkmış göstermek olurdu; onu sunucu `ITEM_UNAVAILABLE` ile
    söyler ve panel mesajı olduğu gibi gösterir.
    """
    if day.get("status") == PUBLISHED:
        return "Bu gün zaten yayında."
    if not day.get("items"):
        return ("Yayınlamak için en az bir kalem gerekli. Kalemsiz bir gün, "
                "müşteriye boş bir menü kartı gösterirdi.")
    if day.get("package_price_kurus") is None and not day.get("components_sellable"):
        return ("Paket fiyatı yok ve kalemler tek tek satılmıyor — bu gün hiçbir "
                "şey satılamaz. Ya paket fiyatı girin ya da kalem satışını açın.")
    return ""


def duplicate_error(source: str, target: str, today: str) -> str:
    problem = date_error(source)
    if problem:
        return problem
    problem = past_error(target, today)
    if problem:
        return problem
    if text(source) == text(target):
        return "Kaynak ve hedef aynı gün. Kopyalamanın bir etkisi olmazdı."
    return ""


# ================================================================ görünümler

def item_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Tek menü kalemi (`menu.md` → `DailyMenuItem`).

    `name` SUNUCUDA TÜRETİLİYOR (`label` doluysa o, yoksa ürün adı) ve burada
    yeniden hesaplanmaz: iki yerde hesaplanan bir ad, bir gün ayrışır.
    """
    return {
        "id": as_int(raw.get("id")),
        "menu_id": as_int(raw.get("menu_id")),
        "name": text(raw.get("name")),
        "label": text(raw.get("label")) or None,
        "quantity": as_int(raw.get("quantity"), 1),
        "sort_order": as_int(raw.get("sort_order")),
        "price_override_kurus": opt_int(raw.get("price_override_kurus")),
        "unit_price_kurus": as_int(raw.get("unit_price_kurus")),
        "is_required": as_bool(raw.get("is_required")),
        "sellable_alone": as_bool(raw.get("sellable_alone")),
        "capacity": opt_int(raw.get("capacity")),
        "sold_out": as_bool(raw.get("sold_out")),
    }


def day_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Tek günün tam görünümü (`menu.md` → `DailyMenuDay`).

    Kalemler `sort_order asc, id asc` sırasında gelir ve BU SIRA KORUNUR:
    çorba → ana yemek → pilav → tatlı, mutfağın ve müşterinin gördüğü sıradır.
    Yerelde yeniden sıralamak, sunucudaki sırayı ekranın kendi fikriyle
    değiştirmek olurdu.
    """
    status = text(raw.get("status")) or DRAFT
    items = [item_view(item) for item in raw.get("items") or [] if isinstance(item, dict)]
    return {
        "id": as_int(raw.get("id")),
        "location_id": as_int(raw.get("location_id")),
        "date": text(raw.get("date")),
        "title": text(raw.get("title"), MAX_TITLE) or None,
        "description": text(raw.get("description"), MAX_DESCRIPTION) or None,
        "internal_note": text(raw.get("internal_note"), MAX_INTERNAL_NOTE) or None,
        "package_price_kurus": opt_int(raw.get("package_price_kurus")),
        "components_sellable": as_bool(raw.get("components_sellable")),
        "status": status if status in STATUSES else DRAFT,
        "status_label": STATUS_LABELS.get(status, STATUS_LABELS[DRAFT]),
        "cutoff_time": text(raw.get("cutoff_time")) or None,
        "capacity_total": opt_int(raw.get("capacity_total")),
        "image_path": text(raw.get("image_path")) or None,
        "items_total_kurus": as_int(raw.get("items_total_kurus")),
        "published_at": text(raw.get("published_at")) or None,
        "created_at": text(raw.get("created_at")) or None,
        "updated_at": text(raw.get("updated_at")) or None,
        "items": items,
        "item_count": len(items),
    }


def calendar_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Takvim ızgarasının tek günü.

    MENÜSÜ OLMAYAN GÜN DE GELİR (`id: null`) ve olduğu gibi taşınır: eksik günü
    atmak, ızgarayı çizen ekranı boşlukları kendi hesaplamaya zorlardı ve
    "22 Ağustos'a menü girilmemiş" tam da yöneticinin görmesi gereken şey.
    `has_menu` bu ayrımı tek bir bayrağa indirir; panelin `id === null`
    denemesini ezberlemesine gerek kalmaz.
    """
    day_id = raw.get("id")
    status = text(raw.get("status"))
    reason = text(raw.get("not_orderable_reason"))
    return {
        "date": text(raw.get("date")),
        "id": as_int(day_id) if day_id is not None else None,
        "has_menu": day_id is not None,
        "status": status if status in STATUSES else None,
        "status_label": STATUS_LABELS.get(status, ""),
        "title": text(raw.get("title")) or None,
        "package_price_kurus": opt_int(raw.get("package_price_kurus")),
        "item_count": as_int(raw.get("item_count")),
        "capacity_total": opt_int(raw.get("capacity_total")),
        "sold_total": as_int(raw.get("sold_total")),
        "remaining_total": opt_int(raw.get("remaining_total")),
        "cutoff_time": text(raw.get("cutoff_time")) or None,
        "cutoff_passed": as_bool(raw.get("cutoff_passed")),
        "closed": as_bool(raw.get("closed")),
        "closed_reason": text(raw.get("closed_reason")) or None,
        "orderable": as_bool(raw.get("orderable")),
        "not_orderable_reason": reason or None,
        # Etiket SUNUCUNUN kodundan türetilir; tanınmayan bir kod ham hâliyle
        # geçer. Boş bırakmak, ekranda nedensiz bir "sipariş alınmıyor" rozeti
        # bırakırdı ve yönetici nedenini hiçbir yerde okuyamazdı.
        "not_orderable_label": NOT_ORDERABLE_LABELS.get(reason, reason),
        # TÜKENDİ ROZETİ: tavan var ve kalan sıfır. `remaining_total` `null`
        # iken tavan hiç konmamıştır ve "tükendi" demek yanlış olurdu.
        "sold_out": opt_int(raw.get("remaining_total")) == 0
                    and opt_int(raw.get("capacity_total")) is not None,
    }


def stock_line(raw: dict[str, Any], *, name: str = "") -> dict[str, Any]:
    """Tek stok satırı — gün toplamı ya da kalem, ikisi de aynı şekli taşır.

    KALAN NEGATİFE DÜŞMEZ. Sözleşme açık: tavan satılmışın altına çekilirse
    `remaining` `0` olur ve gerçek bilgiyi `oversold` taşır. Ekranda "-14
    porsiyon kaldı" yazması anlamsızdır; "0 kaldı, 14 fazla satılmış" anlamlı.
    """
    capacity = opt_int(raw.get("capacity"))
    sold = as_int(raw.get("sold"))
    orders = as_int(raw.get("sold_orders"))
    subscriptions = as_int(raw.get("sold_subscriptions"))
    remaining = None if capacity is None else max(0, capacity - sold)
    return {
        "name": text(raw.get("name")) or name,
        "capacity": capacity,
        "sold": sold,
        # ABONELİK PAYI AYRI DURUR (iş kararı 6): "34 porsiyon kaldı" diyen
        # yönetici, bunun 20'sinin aboneliğe ayrıldığını bilmek zorunda.
        "sold_orders": orders,
        "sold_subscriptions": subscriptions,
        "remaining": remaining,
        "full": capacity is not None and sold >= capacity,
        "oversold": capacity is not None and sold > capacity,
        # `sold_out` MUTFAĞIN ELLE KOYDUĞU işarettir (`veykemtu_menu_soldout`);
        # tavan dolması otomatiktir. Bir ürün tavanı dolmadan da tükenmiş
        # olabilir (malzeme bitti) — ikisi karıştırılmaz.
        "sold_out": as_bool(raw.get("sold_out")),
    }


def stock_view(raw: dict[str, Any]) -> dict[str, Any]:
    """`GET /days/{date}/stock` yanıtının ekran görünümü."""
    day = stock_line(raw.get("day") or {}, name="Gün toplamı")
    items: list[dict[str, Any]] = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        line = stock_line(item)
        line["item_id"] = as_int(item.get("item_id"))
        line["menu_id"] = as_int(item.get("menu_id"))
        items.append(line)
    blocking = raw.get("blocking") or {}
    return {
        "date": text(raw.get("date")),
        "day": day,
        "items": items,
        "blocking": {
            "day": as_bool(blocking.get("day")),
            # Tavanı dolmuş ÜRÜN kimlikleri (`menu_id`), kalem kimlikleri değil.
            "items": [as_int(value) for value in blocking.get("items") or []],
        },
    }


def warning_view(raw: dict[str, Any]) -> dict[str, Any]:
    code = text(raw.get("code"))
    return {
        "code": code,
        "label": WARNING_LABELS.get(code, code),
        "item_id": as_int(raw.get("item_id")),
        "capacity": opt_int(raw.get("capacity")),
        "sold": as_int(raw.get("sold")),
    }


def stock_result_view(raw: dict[str, Any]) -> dict[str, Any]:
    """`PUT /days/{date}/stock` yanıtı — kuru provada da aynı şekil gelir.

    `warnings` KURU PROVADA DA HESAPLANIR ve asıl işi budur: yönetici tavanı
    yazmadan önce kaç siparişin altında kaldığını görür.
    """
    day = stock_line(raw.get("day") or {}, name="Gün toplamı")
    items: list[dict[str, Any]] = []
    for item in raw.get("items") or []:
        if not isinstance(item, dict):
            continue
        line = stock_line(item)
        line["item_id"] = as_int(item.get("item_id"))
        # Sunucu `oversold` alanını AÇIKÇA yolluyor; kendi hesabımız yalnız
        # alan hiç gelmediğinde devreye girer. Sunucununkini ezmemek önemli:
        # `sold` iki bileşenli ve sunucu onu bizden iyi biliyor.
        if "oversold" in item:
            line["oversold"] = as_bool(item.get("oversold"))
        items.append(line)
    return {
        "date": text(raw.get("date")),
        "day": day,
        "items": items,
        "warnings": [warning_view(row) for row in raw.get("warnings") or []
                     if isinstance(row, dict)],
    }


# ============================================================== stok gövdesi

def stock_rows(rows: Any, *, known: set[int] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Panelden gelen tavan tablosunu doğrular. `(satırlar, hata)` döner.

    TAM LİSTEDİR, FARK DEĞİL. Gönderilmeyen kalemin tavanı `null`'a düşer ve
    bu bilinçlidir: niyet "bugünün tavan tablosu şudur"dur. Fark göndermek, iki
    sekmede açık iki yöneticinin birbirinin tavanını sessizce koruması demekti.

    `known` verilirse (günün TAZE kalem kimlikleri) eksik ya da fazla satır
    yakalanır. Fazlası sunucuda zaten reddedilir; EKSİĞİ REDDEDİLMEZ ve asıl
    tehlike odur — aradan silinmiş bir kalem yüzünden eksik gönderilen tablo,
    başka bir kalemin tavanını sessizce kaldırırdı.
    """
    if not isinstance(rows, list):
        return [], "Tavan tablosu bir liste olmalı."
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            return [], "Tavan tablosunun her satırı `item_id` ve `capacity` taşımalı."
        item_id = as_int(row.get("item_id"))
        if item_id <= 0:
            return [], ("Tavan satırında `item_id` zorunlu: eksik satır, o kalemin "
                        "tavanını sessizce kaldırırdı (liste TAM listedir).")
        if item_id in seen:
            return [], f"Aynı kalem iki kez gönderildi (kalem {item_id})."
        seen.add(item_id)
        problem = capacity_error(row.get("capacity"))
        if problem:
            return [], problem
        out.append({"item_id": item_id, "capacity": opt_int(row.get("capacity"))})
    if known is not None:
        missing = sorted(known - seen)
        if missing:
            return [], ("Tavan tablosu eksik: liste TAM listedir ve gönderilmeyen "
                        f"kalemin tavanı kalkar. Eksik kalem: {', '.join(map(str, missing))}. "
                        "Ekranı tazeleyip tekrar deneyin.")
        extra = sorted(seen - known)
        if extra:
            return [], ("Tavan tablosunda artık var olmayan kalem var: "
                        f"{', '.join(map(str, extra))}. Ekranı tazeleyip tekrar deneyin.")
    return out, ""


def baseline_of(stock: dict[str, Any]) -> dict[str, Any]:
    """Önizleme anındaki SATILMIŞ porsiyonlar — temel çizgi.

    Tavanları değil satışları saklar: yönetici tavanı satılmışa BAKARAK
    seçiyor. Gerçek uygulama geldiğinde satış sayısı değiştiyse gördüğü tablo
    artık doğru değildir ve karar yeniden verilmelidir.
    """
    return {
        "day_sold": as_int((stock.get("day") or {}).get("sold")),
        "items": {str(as_int(item.get("item_id"))): as_int(item.get("sold"))
                  for item in stock.get("items") or []},
    }


def baseline_drift(before: dict[str, Any], now: dict[str, Any]) -> list[str]:
    """Temel çizgiden bu yana değişen satırların insan okur açıklaması."""
    drift: list[str] = []
    if as_int(before.get("day_sold")) != as_int(now.get("day_sold")):
        drift.append(f"gün toplamı {as_int(before.get('day_sold'))} → "
                     f"{as_int(now.get('day_sold'))} porsiyon")
    old_items = before.get("items") or {}
    new_items = now.get("items") or {}
    for key in sorted(set(old_items) | set(new_items)):
        old = as_int(old_items.get(key))
        new = as_int(new_items.get(key))
        if old != new:
            drift.append(f"kalem {key}: {old} → {new} porsiyon")
    return drift


# ============================================================== ürün seçicisi

def product_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Seçicinin tek satırı — `{id, name, group, meta}` sözleşmesine indirger.

    Fiyat KURUŞ olarak taşınır ve metne çevrilmez: biçimlendirme panelin
    `money()` işidir ve iki yerde biçimlendirilen bir tutar, bir gün ayrışır.
    """
    return {
        "menu_id": as_int(raw.get("menu_id") or raw.get("id")),
        "name": text(raw.get("name")),
        "price_kurus": as_int(raw.get("price_kurus")),
        "category": text(raw.get("category") or raw.get("category_name")),
        "sold_out": as_bool(raw.get("sold_out")),
        "status": as_bool(raw.get("status", True)),
    }


def next_sort_order(items: list[dict[str, Any]]) -> int:
    """Listenin sonuna eklenecek kalemin sırası.

    Sunucu bu hesabı zaten yapıyor (`sort_order` verilmezse en büyük + 10);
    burada tekrarlanmasının sebebi, panelin kalemi eklemeden ÖNCE nereye
    düşeceğini göstermesi. İki hesabın aynı kuralı kullanması şart, aksi hâlde
    ekranda gördüğü sıra kaydettikten sonra değişirdi.
    """
    if not items:
        return SORT_STEP
    return max(as_int(item.get("sort_order")) for item in items) + SORT_STEP
