"""Elle sipariş taslağının SAF kuralları — ağ yok, depo yok, yan etki yok.

Buradaki her şey `service.py`den çağrılır ve tek başına sınanabilir. Ayrımın
sebebi, "hangi gövde gönderilir" sorusunun cevabının ağ taklidi kurmadan
okunabilmesi: gövde yanlışsa sunucu 422 döner ve o hata ekranda "BLD'ye
ulaşılamadı"dan ayırt edilemez.

NE VAR: gövde alanlarının erken denetimi, telefonun ulusal biçime indirgenmesi,
kesim saatinin geçip geçmediğinin hesabı, stok tavanı aşımının bulunması.

NE YOK — ve bilerek yok:

  · FİYAT. Kalem fiyatı, paket çözümlemesi, teslimat ücreti ve toplam
    `Services\\OrderFactory` içindedir. Buraya bir kopyası konsaydı panelin
    gösterdiği tutar ile siparişin gerçek tutarı sessizce ayrışırdı; ekran
    yalnız KATALOG FİYATINDAN tahmin gösterir ve tahmin olduğunu yazar.
    ANLAŞMALI SEPET TUTARI BUNUN İSTİSNASI DEĞİL: burada yalnız BİÇİMİ
    denetleniyor (tam sayı, kuruş, aralık). O sayıyla ne yapılacağına — kalem
    toplamının yerine geçmesi, kalemlerin fiyatsız yazılması, teslimat
    ücretinin içinde sayılması — yine sunucu karar veriyor.
  · SİPARİŞ PENCERESİ. Kesim saati, ileri görüş sınırı, sipariş alım şalteri,
    asgari sepet tutarı ve menü üyeliği sunucuda `adminContext: true` ile
    BİLEREK atlanıyor (`orders.md`). Burada yeniden uygulanmaz — atlanan bir
    kuralı ekranda geri koymak, sunucunun verdiği kararı iptal etmekti.
    Kesim saati yalnız SÖYLENİR (`cutoff_state`), engellenmez.
  · STOK KAPISI. `DailyStock::take()` `allowOvershoot: true` ile çağrılıyor;
    tavan aşılabilir ve aşım kayda geçer. `stock_warnings` bunu yalnız YAZIYA
    çevirir.

PARA HER YERDE TAM SAYI KURUŞ. Bu dosyada bölme işlemi yoktur.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

#: Sözleşmenin tanıdığı teslimat türleri (`orders.md` → gövde tablosu).
DELIVERY_TYPES = ("delivery", "pickup")

#: Ödeme yöntemleri. `account` KALDIRILDI (cari hesap iş modelinden çıktı) ve
#: gönderilirse sunucu `422 VALIDATION_FAILED` verir. Liste sunucuda
#: `LocationGate::ALL_PAYMENT_METHODS`; buradaki kopya YALNIZ erken geri
#: bildirim içindir ve ekran asıl listeyi `GET /settings/sales` meta'sından
#: (`available_payment_methods`) okur — ayrışırsa sunucununki kazanır.
PAYMENT_METHODS = ("online", "cash")

#: Kalem adedi sınırları (`items.*.quantity` → `min:1`, `max:999`).
MIN_QUANTITY = 1
MAX_QUANTITY = 999

#: Sunucu sütun sınırları. Burada kesilmez, REDDEDİLİR: sessizce kırpılan bir
#: adres notu, kuryenin okuyamadığı yarım cümle demekti.
MAX_ITEMS = 100
MAX_CUSTOMER_NAME = 120
MAX_CUSTOMER_PHONE = 24
MAX_ADDRESS_LINE = 255
MAX_ADDRESS_PART = 96
MAX_ADDRESS_NOTE = 255
MAX_ITEM_NOTE = 255
MAX_CUSTOMER_NOTE = 500

#: ANLAŞMALI SEPET TUTARI (`agreed_total_kurus`) sınırları — sunucudaki
#: `min:1` / `max:100000000` kuralının aynısı. Tavan bir iş kuralı DEĞİL,
#: fazladan basılmış sıfırlara karşı akıl sınırı: 1.000.000 ₺'lik tek bir
#: telefon siparişi yok. Taban SIFIR DEĞİL BİR — "bedava sipariş" bir fiyat
#: kararı değil, boş bırakılmış bir kutunun sessizce sıfıra düşmesidir ve
#: anlaşma yoksa alan HİÇ gönderilmez.
MIN_AGREED_TOTAL_KURUS = 1
MAX_AGREED_TOTAL_KURUS = 100_000_000

#: Gerekçe bu uçta OPSİYONELDİR (`orders.md` → "KARAR — gerekçe zorunlu
#: değildir"). Gönderilirse yalnız ÜST sınır denetlenir; on karakterlik alt
#: sınır uygulanmaz. Alt sınırı burada da istemek, sözleşmenin bilinçli
#: gevşetmesini panelde geri almak olurdu.
MAX_REASON = 500

#: Türkiye 2016'dan beri kalıcı UTC+3 kullanıyor (yaz saati uygulaması yok).
#: `zoneinfo` veritabanı bulunamazsa bu sabit devreye girer — kesim saati
#: uyarısının hiç çıkmaması, üç saat kaymış çıkmasından da kötüdür.
_FALLBACK_OFFSET = timedelta(hours=3)

_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ----------------------------------------------------------------- çeviriler

def text(value: Any) -> str:
    """`None` ve sayı dâhil her şeyi kırpılmış dizeye çevirir."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def national_phone(raw: Any) -> str:
    """Serbest yazımı ulusal biçime indirger: `0532 123 45 67` → `5321234567`.

    KIRPMA UZUNLUĞA BAĞLIDIR, körü körüne değil: on iki hanenin başındaki
    `90`, on bir hanenin başındaki `0` atılır. Baştan iki karakter kesen bir
    kural, on haneli geçerli bir numaranın ilk hanesini yerdi.

    Kalıba uymayan giriş OLDUĞU GİBİ (yalnız rakamları) döner — sunucu da öyle
    yapıyor. Aynı biçim `POST /api/auth/register` ucunun dayattığı biçimdir;
    ayrışsaydı sipariş listesinin telefon araması (`q`) kaydı hiç bulamazdı.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 12 and digits.startswith("90"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


# ------------------------------------------------------------------ denetim

def date_error(value: Any, *, field: str = "Servis günü") -> str:
    """`YYYY-MM-DD` denetimi. Boş değer de HATADIR (alan zorunlu)."""
    raw = text(value)
    if not raw:
        return f"{field} zorunludur."
    if not _DATE_PATTERN.match(raw):
        return f"{field} YYYY-AA-GG biçiminde olmalı."
    try:
        datetime.strptime(raw, "%Y-%m-%d")  # noqa: DTZ007 — yalnız biçim denetimi
    except ValueError:
        return f"{field} geçerli bir gün değil."
    return ""


def choice_error(value: Any, allowed: tuple[str, ...], *, field: str) -> str:
    raw = text(value)
    if raw not in allowed:
        return f"{field} yalnız şunlardan biri olabilir: {', '.join(allowed)}."
    return ""


def reason_error(reason: Any) -> str:
    """Gerekçe OPSİYONEL; yalnız üst sınır denetlenir (sözleşme kararı)."""
    if len(text(reason)) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir."
    return ""


def customer_error(*, customer_id: int, name: Any, phone: Any) -> str:
    """Müşteri ya KAYITLI (`customer_id`) ya YENİ (`name` + `phone`) olur.

    İKİSİNİ BİRDEN göndermek burada reddedilir. Sunucu `customer_id` doluysa
    `customer` nesnesini yok sayar; sessizce yok sayılan bir ad, personelin
    "yeni müşteri açtım" sandığı ama siparişin BAŞKA bir hesaba yazıldığı
    durumdur ve telefonu kapattıktan sonra fark edilir.
    """
    has_id = as_int(customer_id) > 0
    has_new = bool(text(name) or text(phone))

    if has_id and has_new:
        return ("Hem kayıtlı müşteri seçilmiş hem yeni müşteri bilgisi girilmiş; "
                "birini bırakın.")
    if not has_id and not has_new:
        return "Müşteri seçilmedi: ya kayıtlı müşteriyi seçin ya ad ve telefon girin."
    if has_id:
        return ""

    label = text(name)
    if len(label) < 2:
        return "Yeni müşteri adı en az 2 karakter olmalı."
    if len(label) > MAX_CUSTOMER_NAME:
        return f"Yeni müşteri adı en çok {MAX_CUSTOMER_NAME} karakter olabilir."

    raw_phone = text(phone)
    if len(raw_phone) > MAX_CUSTOMER_PHONE:
        return f"Telefon en çok {MAX_CUSTOMER_PHONE} karakter olabilir."
    # EN AZ BİR RAKAM ŞART: yer tutucu e-posta (`tel-<numara>@bld.invalid`)
    # numaranın rakamlarından türüyor ve rakamsız bir giriş `tel-@bld.invalid`
    # üretirdi — o adres rakamsız girilen İKİNCİ müşteriyle çakışır ve iki
    # ayrı arayan tek kayda düşerdi. Sunucudaki `regex` kuralının aynısı.
    if not re.fullmatch(r"[0-9 ()+\-]*[0-9][0-9 ()+\-]*", raw_phone):
        return "Telefon en az bir rakam içermeli (yalnız rakam, boşluk, +, -, parantez)."
    return ""


def clean_agreed_total(value: Any) -> tuple[int | None, str]:
    """Anlaşmalı SEPET tutarını okur. `(kuruş | None, hata)`.

    `None` DÖNMEK "anlaşma yok" DEMEKTİR ve alanın hiç gönderilmemesiyle
    eşdeğerdir: sunucu o hâlde tutarı katalogdan hesaplar (bugünkü davranış).
    Boş dize de aynı yola çıkar — panel kutusu boş bırakıldığında oradan `""`
    gelebiliyor ve onu sıfıra çevirmek "bedava sipariş" demek olurdu.

    TAM SAYI ZORUNLU, KIRPMA YOK. `int(40000.5)` sessizce 40000 üretir; yarım
    kuruşluk bir girişi kırpmak, personelin yazdığı sayıdan BAŞKA bir tutarın
    siparişe düşmesidir — bu alanın önlemek için var olduğu şeyin ta kendisi.
    Kuruşun altında birim yok, o yüzden cevap kırpmak değil REDDETMEK.

    `bool` ayrıca eleniyor: Python'da `True` bir `int`tir ve `1` kuruşluk bir
    sipariş olarak geçerdi.

    ARALIK SUNUCUDAKİNİN AYNISI (`min:1`, `max:100000000`); buradaki kopya
    yalnız erken geri bildirim içindir ve ayrışırsa sunucununki kazanır.
    """
    if value is None:
        return None, ""
    if isinstance(value, str) and not value.strip():
        return None, ""

    problem = ("Anlaşmalı sepet fiyatı KURUŞ cinsinden TAM SAYI olmalı "
               "(400,00 ₺ → 40000). Anlaşma yoksa alan boş bırakılır.")

    if isinstance(value, bool):
        return None, problem
    if isinstance(value, int):
        kurus = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None, problem
        kurus = int(value)
    elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        kurus = int(value.strip())
    else:
        return None, problem

    if kurus < MIN_AGREED_TOTAL_KURUS:
        # SIFIR AYRI BİR CÜMLE HAK EDİYOR: personel kutuyu boşaltmak isterken
        # "0" yazmış olabilir ve "en az 1 kuruş" mesajı ona ne yapacağını
        # söylemez.
        return None, ("Anlaşmalı sepet fiyatı sıfırdan büyük olmalı. Anlaşma "
                      "yoksa alanı BOŞ bırakın — sıfır yazmak bedava sipariş "
                      "demektir ve sunucu bunu reddeder.")
    if kurus > MAX_AGREED_TOTAL_KURUS:
        # Binlik ayracı ELLE konuyor: `:n` yerel ayara bağlıdır ve sunucuda
        # `LC_ALL=C` iken "1000000" basardı — okunması en zor hâli.
        ceiling = f"{MAX_AGREED_TOTAL_KURUS // 100:,}".replace(",", ".")
        return None, (f"Anlaşmalı sepet fiyatı en çok {ceiling} ₺ olabilir; "
                      "fazladan basılmış bir sıfır olabilir mi?")

    return kurus, ""


def address_error(delivery_type: Any, address: Any) -> str:
    """`delivery` ise `line1`, `district`, `city` zorunlu; `note` isteğe bağlı.

    `pickup` ise adres HİÇ GÖNDERİLMEZ — sunucu da yok sayıyor. Gel-al bir
    siparişe adres yazmak, listede teslimat sanılan bir kayıt üretirdi.
    """
    if text(delivery_type) != "delivery":
        return ""
    fields = address if isinstance(address, dict) else {}

    for key, label, limit in (
        ("line1", "Adres satırı", MAX_ADDRESS_LINE),
        ("district", "İlçe", MAX_ADDRESS_PART),
        ("city", "İl", MAX_ADDRESS_PART),
    ):
        value = text(fields.get(key))
        if not value:
            return f"Adrese teslimde {label.lower()} zorunludur."
        if len(value) > limit:
            return f"{label} en çok {limit} karakter olabilir."

    if len(text(fields.get("note"))) > MAX_ADDRESS_NOTE:
        return f"Adres notu en çok {MAX_ADDRESS_NOTE} karakter olabilir."
    return ""


def clean_items(items: Any) -> tuple[list[dict[str, Any]], str]:
    """Kalemleri denetler. `(liste, hata)`.

    KALEMLER OLDUĞU GİBİ GEÇER, AYIKLANMAZ. Bilinen alanları seçip gerisini
    atan bir dönüşüm `option_value_ids`'i düşürürdü: "ekstra peynir" silinir,
    sipariş ucuzlar, mutfak yanlış yemeği yapar ve hata hiçbir yerde görünmez.
    Aynı gerekçe sunucudaki `store()` ve `storeRevision()` içinde de yazılı.

    AYNI ÜRÜN İKİ SATIRDA olabilir ve BİRLEŞTİRİLMEZ: notları ya da seçenekleri
    farklı iki satır ("biri acısız") tek satıra indirgenirse mutfak ikisini de
    aynı yapar.
    """
    if not isinstance(items, list) or not items:
        return [], "En az bir kalem eklenmeli."
    if len(items) > MAX_ITEMS:
        return [], f"Bir siparişte en çok {MAX_ITEMS} kalem olabilir."

    clean: list[dict[str, Any]] = []
    for index, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            return [], f"{index}. kalem okunamadı."

        menu_id = as_int(raw.get("menu_id"))
        if menu_id <= 0:
            return [], f"{index}. kalemin ürünü seçilmemiş."

        quantity = as_int(raw.get("quantity"))
        if quantity < MIN_QUANTITY or quantity > MAX_QUANTITY:
            return [], (f"{index}. kalemin adedi {MIN_QUANTITY}-{MAX_QUANTITY} "
                        f"arasında olmalı.")

        option_ids = raw.get("option_value_ids")
        if option_ids is not None and not isinstance(option_ids, list):
            return [], f"{index}. kalemin seçenek listesi okunamadı."

        note = text(raw.get("note"))
        if len(note) > MAX_ITEM_NOTE:
            return [], f"{index}. kalemin notu en çok {MAX_ITEM_NOTE} karakter olabilir."

        item: dict[str, Any] = {**raw, "menu_id": menu_id, "quantity": quantity}
        if option_ids is not None:
            item["option_value_ids"] = [as_int(value) for value in option_ids]
        # Boş not GÖNDERİLMEZ: sunucu `nullable` kabul ediyor ama boş dize ile
        # `null` arasındaki farkı denetim izinde okumak anlamsız gürültü olurdu.
        if note:
            item["note"] = note
        else:
            item.pop("note", None)
        clean.append(item)

    return clean, ""


# --------------------------------------------------------------- kesim saati

def _local_now(server_time: Any) -> datetime:
    """Sunucunun UTC damgasını Europe/Istanbul yerel saatine çevirir.

    DAMGA SUNUCUDAN GELİR, İSTEMCİDEN DEĞİL (`00-genel.md` §6): personelin
    makinesinin saati kaymış olabilir ve kesim uyarısı o kayma yüzünden yanlış
    çıkarsa uyarının kendisi güvenilmez olur. Damga okunamazsa yerel saate
    düşülür — uyarıyı hiç göstermemektense yaklaşık göstermek yeğdir.
    """
    stamp = text(server_time)
    parsed: datetime | None = None
    if stamp:
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    if parsed is None:
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    try:
        from zoneinfo import ZoneInfo

        return parsed.astimezone(ZoneInfo("Europe/Istanbul"))
    except Exception:  # noqa: BLE001 — tzdata yoksa sabit kaymayla devam (K7)
        return parsed.astimezone(UTC) + _FALLBACK_OFFSET


def cutoff_state(service_date: Any, *, cutoff: Any, server_time: Any = "") -> dict[str, Any]:
    """Servis gününün sipariş alımı kapandı mı? `{cutoff, closed, label, today}`.

    ETKİN KESİM = `gün.cutoff_time ?? ayar.order_cutoff` (`settings.md` → "İki
    kesim saati vardır"). Birleştirmeyi çağıran yapar; buraya TEK bir saat
    gelir.

    KAPALI OLMAK ENGEL DEĞİLDİR. Sunucu bu ekranın açtığı siparişte pencereyi
    `adminContext: true` ile bilerek atlıyor: personel müşteriyle telefonda
    anlaşmış, istisnayı insan vermiştir. Ekranın işi kararı sorgulamak değil,
    personelin "bu gün normalde kapalı" olduğunu BİLEREK devam etmesini
    sağlamak — sessizce geçmek, kesimden sonra girilen siparişin neden
    listedekilerden farklı davrandığını hiç açıklamazdı.

    Geçmiş bir güne giriş de kapalıdır ve orada uyarı daha da değerlidir:
    yanlış yazılmış bir gün, mutfağın hiç görmeyeceği bir sipariş üretir.
    """
    day = text(service_date)
    local = _local_now(server_time)
    today = local.strftime("%Y-%m-%d")
    saat = text(cutoff)
    valid_cutoff = saat if _TIME_PATTERN.match(saat) else ""

    state: dict[str, Any] = {
        "cutoff": valid_cutoff,
        "today": today,
        "closed": False,
        "label": "",
    }
    if not day or not _DATE_PATTERN.match(day):
        return state

    if day < today:
        state["closed"] = True
        state["label"] = ("Bu gün GEÇMİŞTE kaldı: sipariş alımı kapalı, panelden "
                          "giriyorsunuz. Sipariş yine de açılır ve mutfağa anında düşer.")
        return state

    if day > today:
        # İleri tarihte kesim henüz gelmedi; sipariş o günün kesim anında
        # mutfağa düşer. Kapalılık yok.
        return state

    if not valid_cutoff:
        # Kesim saati tanımsızsa kapanış da yok (`settings.md` tablosu).
        return state

    if local.strftime("%H:%M") > valid_cutoff:
        state["closed"] = True
        state["label"] = (f"Bugünün sipariş alımı {valid_cutoff} kesim saatinde kapandı; "
                          "panelden giriyorsunuz. Sipariş anında mutfağa düşer.")
    return state


# ---------------------------------------------------------------------- stok

def stock_index(payload: Any) -> dict[str, Any]:
    """`GET /menu/days/{date}/stock` gövdesini `menu_id → satır` haritasına çevirir.

    Sipariş kalemi ÜRÜNÜ (`menu_id`) taşır, günün menü satırını (`item_id`)
    değil; stok yanıtı ikisini birden veriyor ve eşleme burada kurulur. Yanlış
    anahtarla kurulmuş bir harita hiçbir kalemi bulamaz ve ekran sessizce
    "tavan aşımı yok" derdi.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    day = data.get("day") if isinstance(data.get("day"), dict) else {}
    rows = data.get("items") if isinstance(data.get("items"), list) else []

    items: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        menu_id = as_int(raw.get("menu_id"))
        if menu_id <= 0:
            continue
        items[str(menu_id)] = {
            "menu_id": menu_id,
            "name": text(raw.get("name")),
            "capacity": raw.get("capacity"),
            "sold": as_int(raw.get("sold")),
            "remaining": raw.get("remaining"),
            "full": bool(raw.get("full")),
            "sold_out": bool(raw.get("sold_out")),
        }

    return {
        "day": {
            "capacity": day.get("capacity"),
            "sold": as_int(day.get("sold")),
            "remaining": day.get("remaining"),
            "full": bool(day.get("full")),
        },
        "items": items,
    }


def stock_warnings(stock: Any, items: list[dict[str, Any]]) -> list[str]:
    """Tavanı aşan kalemleri YAZIYA çevirir. ENGELLEMEZ.

    `DailyStock::take()` `allowOvershoot: true` ile çağrılıyor: personel tavanı
    bilerek aşabilir ("bir porsiyon daha çıkarırız"). Sipariş reddedilmez, aşım
    `veykemtu_daily_menu_stock.sold > capacity` olarak kayda geçer ve menü
    ekranında görünür. Buradaki uyarının tek işi, aşımın KAZA OLMAMASINI
    sağlamak.

    `capacity` `null` ise tavan yoktur ve aşım da yoktur — `remaining` de `null`
    gelir. Sıfır sanıp uyarı basmak, tavansız her kalemde yanlış alarm olurdu.

    `sold_out` AYRI BİR ŞEYDİR: mutfağın elle koyduğu "tükendi" işareti tavanla
    ilgili değil (malzeme bitmiş olabilir) ve sunucu o kalemi `422
    ITEM_UNAVAILABLE` ile reddeder — yani gerçekten engeldir, uyarı değil.
    Ayrımı yazıyla söylemek, personelin "uyarıyı geçtim ama olmadı" yaşamasını
    önler.
    """
    if not isinstance(stock, dict) or not items:
        return []

    rows = stock.get("items") if isinstance(stock.get("items"), dict) else {}
    day = stock.get("day") if isinstance(stock.get("day"), dict) else {}
    warnings: list[str] = []

    total = 0
    for item in items:
        quantity = as_int(item.get("quantity"))
        total += quantity
        row = rows.get(str(as_int(item.get("menu_id"))))
        if not isinstance(row, dict):
            continue

        label = text(row.get("name")) or f"#{as_int(item.get('menu_id'))}"
        if row.get("sold_out"):
            warnings.append(f"{label}: mutfak bu ürünü TÜKENDİ işaretlemiş; "
                            "sunucu siparişi reddedebilir.")

        remaining = row.get("remaining")
        if remaining is None:
            continue
        left = as_int(remaining)
        if quantity > left:
            warnings.append(
                f"{label}: tavan aşılıyor — kalan {left}, istenen {quantity} "
                f"(aşım {quantity - left}). Sipariş yine de açılır ve aşım kayda geçer.")

    day_left = day.get("remaining")
    if day_left is not None and total > as_int(day_left):
        warnings.append(
            f"Gün toplamı aşılıyor — güne kalan {as_int(day_left)}, istenen {total} "
            f"(aşım {total - as_int(day_left)}). Sipariş yine de açılır.")

    return warnings


# ------------------------------------------------------------------- görünüm

def order_row(raw: Any) -> dict[str, Any]:
    """Açılan siparişin satırı — `GET /` listesinin biçiminin aynısı.

    Sunucu bunu bilerek liste biçiminde döndürüyor ("panel siparişi listeye
    eklemek ya da detayına gitmek için ikinci bir istek atmak zorunda
    kalmasın"). Alanlar SEÇİLİR, uydurulmaz: burada olmayan bir ad ekranda boş
    görünür ve boş görünen bir alan, ekranın uydurduğu bir alandan iyidir.
    """
    row = raw if isinstance(raw, dict) else {}
    return {
        "id": as_int(row.get("id")),
        "order_number": text(row.get("order_number")),
        "status": text(row.get("status")),
        "service_date": text(row.get("service_date")),
        "requested_at": text(row.get("requested_at")),
        "delivery_type": text(row.get("delivery_type")),
        "customer_id": as_int(row.get("customer_id")),
        "customer_name": text(row.get("customer_name")),
        "customer_phone": text(row.get("customer_phone")),
        "item_count": as_int(row.get("item_count")),
        "total_kurus": as_int(row.get("total_kurus")),
        "payment_method": text(row.get("payment_method")),
        "payment_status": text(row.get("payment_status")),
        "is_subscription": bool(row.get("is_subscription")),
        "created_at": text(row.get("created_at")),
    }


def customer_row(raw: Any) -> dict[str, Any]:
    """Müşteri arama satırı. Telefon ULUSAL biçimde döner.

    LİSTE MASKELENMEZ (`customers.md`): personel müşteriyi telefonundan tanır
    ve maskeli bir listede doğru kaydı seçemez — hepsini tek tek açmak zorunda
    kalır, yani her arama için bir düzine KVKK denetim satırı doğar. Maskeleme
    burada gizliliği artırmaz, izi bozar.
    """
    row = raw if isinstance(raw, dict) else {}
    first = text(row.get("first_name"))
    last = text(row.get("last_name"))
    name = text(row.get("name")) or " ".join(part for part in (first, last) if part)
    return {
        "id": as_int(row.get("id") or row.get("customer_id")),
        "name": name,
        "phone": national_phone(row.get("telephone") or row.get("phone")),
        "org_name": text(row.get("bld_org_name") or row.get("org_name")),
        "email": text(row.get("email")),
        "status": bool(row.get("status", True)),
    }


def product_row(raw: Any) -> dict[str, Any]:
    """Seçici satırı. `price_kurus` TAM SAYI KURUŞ ve ekranda bölünmez.

    Fiyat yalnız TAHMİNİ toplam içindir: gerçek tutarı `OrderFactory` hesaplar
    (paket çözümlemesi, seçenek farkları, teslimat ücreti). Ekran bunu açıkça
    yazar; yazmasaydı personel müşteriye telefonda YANLIŞ tutar söylerdi.
    """
    row = raw if isinstance(raw, dict) else {}
    return {
        "menu_id": as_int(row.get("menu_id") or row.get("id")),
        "name": text(row.get("name")),
        "price_kurus": as_int(row.get("price_kurus")),
        "category": text(row.get("category") or row.get("category_name")),
        "status": bool(row.get("status", True)),
        "sold_out": bool(row.get("sold_out")),
    }


def screen_contract() -> dict[str, Any]:
    """Ekranın ağa çıkmadan bilebileceği her şey.

    Panel açılışta bunu okur ve geçit düşükken bile form çizilir (K7). Ödeme
    yöntemi listesi burada da var ama SUNUCUNUNKİ KAZANIR: `service_day`
    yanıtındaki `payment_methods` doluysa ekran onu kullanır. İki listeyi eşit
    tutmak yerine hangisinin üstün olduğunu yazmak, ayrıştıklarında ne
    olacağını belirsiz bırakmaz.
    """
    return {
        "delivery_types": [
            {"value": "delivery", "label": "Adrese teslim"},
            {"value": "pickup", "label": "Gel-al"},
        ],
        "payment_methods": [
            {"value": "online", "label": "Online"},
            {"value": "cash", "label": "Nakit"},
        ],
        "quantity": {"min": MIN_QUANTITY, "max": MAX_QUANTITY},
        "limits": {
            "items": MAX_ITEMS,
            "customer_name": MAX_CUSTOMER_NAME,
            "customer_phone": MAX_CUSTOMER_PHONE,
            "customer_note": MAX_CUSTOMER_NOTE,
            "address_line": MAX_ADDRESS_LINE,
            "address_part": MAX_ADDRESS_PART,
            "address_note": MAX_ADDRESS_NOTE,
            "item_note": MAX_ITEM_NOTE,
            "reason": MAX_REASON,
        },
        # Anlaşmalı sepet fiyatının sınırları EKRANA da veriliyor: kutuya gömülü
        # bir sayı, sunucu kuralı değiştiğinde sessizce yalan söylerdi.
        "agreed_total": {
            "min": MIN_AGREED_TOTAL_KURUS,
            "max": MAX_AGREED_TOTAL_KURUS,
        },
        # Gerekçe bu uçta opsiyoneldir; panel kutuyu "isteğe bağlı" diye çizer.
        "reason_required": False,
    }
