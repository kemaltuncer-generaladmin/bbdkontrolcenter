"""Fatura belgesi — SAF yardımcılar. Ağ yok, DB yok, HTTP yok.

Burada yalnız sözleşmedeki gövdeyi ekranın ve PDF üretecinin anlayacağı hâle
çeviren işlevler durur. Ayrı dosya olmasının nedeni test edilebilirlik:
belgenin kâğıda nasıl döküldüğü, geçit ayakta mı diye sormadan sınanabilmeli.

BELGE `snapshot_json`DAN ÜRETİLİR, CANLI TABLODAN DEĞİL (invoices.md). Müşteri
adı, kurum unvanı, kalemler ve fiyatlar sonradan değişse bile basılmış belge
aynı kalmalı; canlı veriden üretilen bir belge, iki farklı zamanda iki farklı
kâğıt üretirdi. Bu dosya bu yüzden `snapshot` bloğunun DIŞINDAN hiçbir tutar
okumaz — üstteki `total_kurus` yalnız liste satırında, karşılaştırma için
kullanılır.

VERGİ SATIRI YOKTUR. KDV hesaplamak belgeye mali değer atfetmek olurdu ve
yanlış hesaplanmış bir KDV, olmayan bir belgeden daha kötüdür.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from km_sdk import money, number

#: Gerekçe sınırları. Alt sınır Kontrol Merkezi'nin ortak kuralı, üst sınır
#: bu alanın sözleşmesi (`invoices.md` → gerekçe 500).
MIN_REASON = 10
MAX_REASON = 500

#: HER ÜRETİLEN BELGEDE geçen zorunlu dipnot. Kaldırılamaz: `build_pdf`
#: çağrısında hem `footer` (her sayfanın altı) hem de ilk `note` olarak gider
#: ve panel bunu kapatılamaz bir bant olarak gösterir.
NOTICE = ("Bu belge mali değeri olmayan bilgi amaçlı bir dokümandır; "
          "fatura yerine geçmez.")

#: Sunucunun `snapshot_json.notice` alanındaki metin AYRI bir dizedir
#: (invoices.md: "Bu belge bilgilendirme amaçlıdır, mali değeri yoktur.") ve
#: belgenin DONMUŞ içeriğine aittir. İkisi de basılır: birincisi Kontrol
#: Merkezi'nin ürettiği kâğıdın dipnotu, ikincisi belgenin kendi metnidir ve
#: sonradan değişse bile eski belge eski cümleyi göstermelidir.

#: Dönem belgesinde aralık tavanı (invoices.md). Aşan istek 422 dönerdi.
MAX_PERIOD_DAYS = 62

#: `status` alanının alabileceği değerler ve Türkçe karşılıkları. RENK TEK
#: BAŞINA ANLAM TAŞIMAZ: rozetin içinde yazı da vardır.
STATUS_LABELS = {"issued": "Geçerli", "void": "İptal"}

#: Üretilebilen rapor türleri (`report.js` → `reportChain`).
REPORT_KINDS = ("invoice", "list")

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Arşiv satırının `kind` sütunu: hangi DOSYA türü üretildi.
KIND_PDF = "pdf"
KIND_HTML = "html"
KIND_LIST = "list"


# ------------------------------------------------------------- dönüştürme

def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def text(value: Any, limit: int = 0) -> str:
    out = str(value if value is not None else "").strip()
    return out[:limit] if limit and len(out) > limit else out


def esc(value: Any) -> str:
    """`build_pdf` hücreleri reportlab'ın mini XML'i ile çizilir.

    TEHLİKE PATLAMAK DEĞİL, SESSİZCE KAYBETMEK: reportlab'ın paragraf
    ayrıştırıcısı kaçırılmamış bir `<...>` parçasını ETİKET SANIP ATAR.
    `"Acme & Co <A.Ş.> Ltd"` kâğıda `"Acme & Co  Ltd"` olarak basılır — unvanın
    yarısı gider ve kimse fark etmez (ölçüldü: `ParaParser.parse`). Kaçış
    burada, veri PDF'e girmeden yapılır; başlık ve alt başlık da dâhil.
    """
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def is_day(value: Any) -> bool:
    """`YYYY-MM-DD` mi? Biçim sözleşmenin kendisidir (`00-genel.md` §6)."""
    try:
        date.fromisoformat(text(value))
    except ValueError:
        return False
    return True


def day_span(start: str, end: str) -> int:
    """Aralığın gün sayısı (iki uç dâhil). Geçersiz tarihte 0."""
    try:
        first = date.fromisoformat(text(start))
        last = date.fromisoformat(text(end))
    except ValueError:
        return 0
    return (last - first).days + 1


def moment(value: Any) -> str:
    """ISO 8601 UTC anı → `16.08.2026 15:00` (yerel saat).

    Sunucu her anı UTC gönderiyor; kullanıcı yerel saati okuyor. Dönüşümü
    ekranda değil burada yapmak, PDF ile paneldeki saatin ayrışmasını önler.
    """
    raw = text(value)
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone().strftime("%d.%m.%Y %H:%M")


def day(value: Any) -> str:
    """`YYYY-MM-DD` → `16.08.2026`. Ayrıştırılamayan değer olduğu gibi kalır."""
    raw = text(value)
    if not is_day(raw):
        return raw
    parts = raw.split("-")
    return f"{parts[2]}.{parts[1]}.{parts[0]}"


# ------------------------------------------------------------- doğrulama

def reason_error(reason: str) -> str:
    """Gerekçe denetimi. Şemada DA var (K9 — çift kapı): arayüzde zorunlu
    göstermek, istemcinin gövdeyi elle kurmasını engellemez."""
    value = text(reason)
    if len(value) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı: belge kesme ve iptal "
                "denetim izine bu metinle geçer.")
    if len(value) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir; {len(value)} karakter verildi."
    return ""


def create_error(*, order_id: int, subscription_id: int,
                 period_start: str, period_end: str) -> str:
    """Belge üretme isteğinin kip denetimi.

    Sunucu da 422 verirdi, ama hata "hangi kipi seçtiğimi bilmiyorum" demezdi
    ve bir hız kovası isteği boşa giderdi.
    """
    has_order = order_id > 0
    has_subscription = subscription_id > 0
    if has_order == has_subscription:
        return ("Belge iki kipten biriyle kesilir: sipariş belgesi (sipariş kimliği) "
                "ya da dönem belgesi (abonelik kimliği + dönem). İkisi birden ya da "
                "hiçbiri gönderilemez.")
    if has_order:
        return ""
    if not is_day(period_start) or not is_day(period_end):
        return "Dönem belgesinde başlangıç ve bitiş tarihi zorunludur (YYYY-MM-DD)."
    span = day_span(period_start, period_end)
    if span <= 0:
        return "Dönem bitişi başlangıcından önce olamaz."
    if span > MAX_PERIOD_DAYS:
        return (f"Dönem aralığı en çok {MAX_PERIOD_DAYS} gün olabilir; {span} gün "
                "istendi. Daha uzun dönem için birden çok belge kesilir.")
    return ""


# --------------------------------------------------------------- görünüm

def source_label(row: dict[str, Any]) -> str:
    """Belgenin kaynağı tek satırda: sipariş mi, dönem mi.

    Ekranda iki ayrı sütun yerine tek sütun duruyor çünkü bir belge ancak
    birine bağlıdır; boş kalan ikinci sütun her satırda tire gösterirdi.
    """
    order_id = as_int(row.get("order_id"))
    if order_id:
        return f"Sipariş #{order_id}"
    subscription_id = as_int(row.get("subscription_id"))
    if not subscription_id:
        return ""
    span = ""
    if row.get("period_start") and row.get("period_end"):
        span = f" · {day(row['period_start'])}–{day(row['period_end'])}"
    return f"Abonelik #{subscription_id}{span}"


def invoice_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Liste satırı. Alan adları sözleşmedeki gibi snake_case kalır."""
    status = text(raw.get("status")) or "issued"
    row = {
        "id": as_int(raw.get("id")),
        "invoice_no": text(raw.get("invoice_no")),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "customer_id": as_int(raw.get("customer_id")),
        "customer_label": text(raw.get("customer_label")),
        "order_id": as_int(raw.get("order_id")) or None,
        "subscription_id": as_int(raw.get("subscription_id")) or None,
        "subscription_payment_id": as_int(raw.get("subscription_payment_id")) or None,
        "period_start": text(raw.get("period_start")),
        "period_end": text(raw.get("period_end")),
        "issued_at": text(raw.get("issued_at")),
        "issued_at_label": moment(raw.get("issued_at")),
        "total_kurus": as_int(raw.get("total_kurus")),
        "void_at": text(raw.get("void_at")),
        "void_at_label": moment(raw.get("void_at")),
        "void_reason": text(raw.get("void_reason")),
        # `html_url` sunucunun yoludur; panel oraya DOĞRUDAN gitmez (K4).
        # Yalnız künyede gösterilir ve "sunucu belgesini kaydet" ucu kullanır.
        "html_url": text(raw.get("html_url")),
        "created_at": text(raw.get("created_at")),
    }
    row["source_label"] = source_label(row)
    return row


def snapshot_view(raw: Any) -> dict[str, Any]:
    """`snapshot_json` → çizilebilir blok. EKSİK ALAN BOŞ GEÇİLİR, uydurulmaz.

    Belgenin donmuş içeriği bu bloktur; ekranda da PDF'te de yalnız buradan
    okunur. Sunucu bir alanı hiç göndermediyse boş kalır — canlı tablodan
    tamamlamak, donmuş belgeyi çözerdi.
    """
    data = raw if isinstance(raw, dict) else {}
    issuer = data.get("issuer") if isinstance(data.get("issuer"), dict) else {}
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    payment = data.get("payment") if isinstance(data.get("payment"), dict) else {}
    lines = []
    for item in data.get("lines") or []:
        if not isinstance(item, dict):
            continue
        lines.append({
            "description": text(item.get("description")),
            "service_date": text(item.get("service_date")),
            "order_number": text(item.get("order_number")),
            "quantity": as_int(item.get("quantity")),
            "unit_price_kurus": as_int(item.get("unit_price_kurus")),
            "line_total_kurus": as_int(item.get("line_total_kurus")),
        })
    return {
        "issuer": {
            "name": text(issuer.get("name")),
            "address": text(issuer.get("address")),
            "phone": text(issuer.get("phone")),
            "email": text(issuer.get("email")),
        },
        "customer": {
            "label": text(customer.get("label")),
            "contact_person": text(customer.get("contact_person")),
            "tax_office": text(customer.get("tax_office")),
            "tax_no": text(customer.get("tax_no")),
            "address": text(customer.get("address")),
            "phone": text(customer.get("phone")),
        },
        "lines": lines,
        "totals": {
            "subtotal_kurus": as_int(totals.get("subtotal_kurus")),
            "delivery_fee_kurus": as_int(totals.get("delivery_fee_kurus")),
            "total_kurus": as_int(totals.get("total_kurus")),
            "currency": text(totals.get("currency")) or "TRY",
        },
        "payment": {
            "method": text(payment.get("method")),
            "status": text(payment.get("status")),
            "paid_at": text(payment.get("paid_at")),
        },
        "notice": text(data.get("notice")),
    }


def invoice_card(raw: dict[str, Any]) -> dict[str, Any]:
    """Tek belge: liste satırı + donmuş içerik."""
    card = invoice_row(raw)
    card["snapshot"] = snapshot_view(raw.get("snapshot_json"))
    return card


PAYMENT_METHODS = {"online": "Online", "cash": "Nakit"}
PAYMENT_STATES = {"paid": "Ödendi", "pending": "Bekliyor", "failed": "Başarısız",
                  "refunded": "İade edildi"}


def payment_label(payment: dict[str, Any]) -> str:
    """Ödeme satırı: `online · Ödendi · 16.08.2026 12:00`.

    BİLİNMEYEN DEĞER OLDUĞU GİBİ YAZILIR: sunucu yeni bir yöntem eklediğinde
    ekranın boş göstermesi, yanlış göstermesinden ayırt edilemezdi.
    """
    parts = []
    method = text(payment.get("method"))
    if method:
        parts.append(PAYMENT_METHODS.get(method, method))
    state = text(payment.get("status"))
    if state:
        parts.append(PAYMENT_STATES.get(state, state))
    paid = moment(payment.get("paid_at"))
    if paid:
        parts.append(paid)
    return " · ".join(parts)


def file_stem(row: dict[str, Any]) -> str:
    """Dosya adının gövdesi. Belge numarası boşsa kimliğe düşülür — adsız bir
    dosya, arşivde hangi belge olduğunu söylemezdi."""
    stem = text(row.get("invoice_no")) or f"belge-{as_int(row.get('id'))}"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem)
    return safe.strip("-") or "belge"


# ------------------------------------------------------------------- PDF

def _pair_rows(pairs: list[tuple[str, str]]) -> list[list[str]]:
    """Etiket/değer tablosu — boş değerler ATILIR.

    Boş satırı tire ile basmak, belgenin yarısını tireyle doldururdu; bir
    kurumun vergi dairesi yoksa o satır hiç olmamalı.
    """
    return [[esc(label), esc(value)] for label, value in pairs if text(value)]


def pdf_sections(card: dict[str, Any]) -> list[dict[str, Any]]:
    """A4 belgenin bölümleri (`km_sdk.build_pdf` sözlüğü).

    FİLİGRAN YOK, YAZI VAR. Sunucunun HTML'i iptal edilmiş belgenin üzerine
    çapraz "İPTAL" filigranı basıyor; `build_pdf` çizim katmanı sunmuyor ve
    uydurma bir bölüm türü eklemek çekirdeği bu modül için değiştirmek olurdu
    (kulvar dışı). Aynı bilgi burada ÜÇ yerde birden yazılır: başlıkta,
    ilk uyarı satırında ve toplam kutusunda. İptal edilmiş bir belgenin temiz
    basılabilmesi, elindeki kâğıdın geçerli olduğunu sanan bir müşteri
    üretirdi.
    """
    snapshot = card.get("snapshot") or {}
    issuer = snapshot.get("issuer") or {}
    customer = snapshot.get("customer") or {}
    totals = snapshot.get("totals") or {}
    voided = card.get("status") == "void"

    sections: list[dict[str, Any]] = [{"kind": "note", "text": esc(NOTICE)}]

    if voided:
        sections.append({"kind": "note", "text":
                         "<b>BU BELGE İPTAL EDİLMİŞTİR.</b> Geçerli değildir; yerine "
                         "kesilmiş yeni bir belge olabilir. İptal: "
                         + esc(moment(card.get("void_at")) or "—")
                         + " · Gerekçe: " + esc(card.get("void_reason") or "—")})

    sections.append({"kind": "tiles", "title": "Belge künyesi", "tiles": [
        ("Belge no", esc(card.get("invoice_no") or "—")),
        ("Durum", esc(STATUS_LABELS.get(str(card.get("status")), str(card.get("status") or "—")))),
        ("Düzenlenme", esc(moment(card.get("issued_at")) or "—")),
        ("Kaynak", esc(source_label(card) or "—")),
    ]})

    party = _pair_rows([
        ("Düzenleyen", issuer.get("name", "")),
        ("Adres", issuer.get("address", "")),
        ("Telefon", issuer.get("phone", "")),
        ("E-posta", issuer.get("email", "")),
    ])
    if party:
        sections.append({"kind": "table", "title": "Düzenleyen",
                         "headers": ["Alan", "Değer"], "align": "LL",
                         "widths": [1, 3], "rows": party})

    buyer = _pair_rows([
        ("Alıcı", customer.get("label", "")),
        ("Yetkili", customer.get("contact_person", "")),
        ("Vergi dairesi", customer.get("tax_office", "")),
        ("Vergi/TC no", customer.get("tax_no", "")),
        ("Adres", customer.get("address", "")),
        ("Telefon", customer.get("phone", "")),
    ])
    if buyer:
        sections.append({"kind": "table", "title": "Alıcı",
                         "headers": ["Alan", "Değer"], "align": "LL",
                         "widths": [1, 3], "rows": buyer})

    lines = snapshot.get("lines") or []
    sections.append({
        "kind": "table", "title": "Kalemler",
        "headers": ["Açıklama", "Servis günü", "Sipariş", "Adet", "Birim", "Tutar"],
        "align": "LLLRRR", "widths": [2.6, 1.1, 1.1, 0.7, 1, 1.1],
        "rows": [[esc(line["description"]), esc(day(line["service_date"])),
                  esc(line["order_number"]), esc(number(line["quantity"])),
                  esc(money(line["unit_price_kurus"])), esc(money(line["line_total_kurus"]))]
                 for line in lines] or [["Kalem yok", "", "", "", "", ""]],
    })

    sections.append({"kind": "tiles", "title": "Toplam", "tiles": [
        ("Ara toplam", esc(money(totals.get("subtotal_kurus", 0)))),
        ("Teslimat", esc(money(totals.get("delivery_fee_kurus", 0)))),
        ("TOPLAM" + (" (İPTAL)" if voided else ""),
         esc(money(totals.get("total_kurus", 0)))),
    ]})

    paid = payment_label(snapshot.get("payment") or {})
    if paid:
        sections.append({"kind": "note", "text": "Ödeme: " + esc(paid)})

    # Belgenin KENDİ ibaresi de basılır: sonradan değişse bile eski belge eski
    # cümleyi göstermeli. Üstteki dipnotla aynıysa tekrar edilmez.
    own = text(snapshot.get("notice"))
    if own and own != NOTICE:
        sections.append({"kind": "note", "text": esc(own)})

    return sections


def list_sections(rows: list[dict[str, Any]], *, meta: dict[str, Any],
                  filter_label: str, truncated: bool) -> list[dict[str, Any]]:
    """Süzgeçlenmiş belge listesinin dökümü.

    TOPLAM SÜZGEÇLENMİŞ KÜMENİNDİR, sayfanın değil (`meta.issued_total_kurus`)
    ve iptal edilmiş belgeler o toplama girmez — sözleşme bunu açıkça
    söylüyor. Sayfadaki satırları toplayıp yazmak, iki sayfada iki farklı
    "genel toplam" üretirdi.
    """
    issued = [row for row in rows if row.get("status") == "issued"]
    sections: list[dict[str, Any]] = [
        {"kind": "note", "text": esc(NOTICE)},
        {"kind": "tiles", "title": "Döküm özeti", "tiles": [
            ("Belge", esc(number(as_int(meta.get("total"), len(rows))))),
            ("Listelenen", esc(number(len(rows)))),
            ("İptal", esc(number(len(rows) - len(issued)))),
            ("Geçerli toplam", esc(money(as_int(meta.get("issued_total_kurus"))))),
        ]},
        {"kind": "note", "text": "Süzgeç: " + esc(filter_label or "tümü")},
        {"kind": "table", "title": "Belgeler",
         "headers": ["Belge no", "Durum", "Müşteri", "Kaynak", "Düzenlenme", "Tutar"],
         "align": "LLLLLR", "widths": [1.3, 0.8, 2, 1.6, 1.2, 1.1],
         "rows": [[esc(row.get("invoice_no")), esc(row.get("status_label")),
                   esc(row.get("customer_label")), esc(source_label(row)),
                   esc(moment(row.get("issued_at"))), esc(money(row.get("total_kurus")))]
                  for row in rows] or [["Belge yok", "", "", "", "", ""]]},
    ]
    if truncated:
        sections.append({"kind": "note", "text":
                         "Liste tavana takıldı: döküm yalnız ilk satırları taşıyor. "
                         "Süzgeci daraltın — özet rakamlar sunucudan geldiği için "
                         "tüm süzgeçlenmiş kümeyi kapsar."})
    return sections
