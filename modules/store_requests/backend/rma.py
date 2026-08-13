"""Talep (RMA) verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz.

NEDEN AYRI DOSYA. Bu ekranın tek zor işi SLA aritmetiğidir: "kaç saat kaldı"
sorusunun cevabı duruma, önceliğe ve saat dilimine bağlıdır ve yanlış hesap
ekranda kırmızı yanan ama aslında zamanı olan bir talep üretir. Servise
gömülseydi tek satırı test edilemezdi; burada hepsi girdi→çıktı fonksiyonu.

YEDİ KARAR — hepsinin karşılığı burada bir fonksiyondur:

 0. CANLI MAĞAZA camelCase KONUŞUYOR ve saat dilimsiz damga YERELDİR.
    İkisi de 2026-08-13'te canlıya karşı doğrulandı ve ikisi de sessiz hata
    üretiyordu: `order_summary` alanları iki yazımla da arar, `parse_time`
    çıplak damgayı yerel saat sayar (ayrıntı ve kanıt fonksiyonlarında).
 1. Uzak kayıt alan adları OYNAK (`bbd/return-requests` hâlâ yazılıyor).
    → `pick()` bir bilgiyi birden çok olası adda arar, bulamazsa boş bırakır.
 2. SLA saati ÖNCELİKTEN gelir, uzak kayıt `due_at` verirse O KAZANIR.
    → `sla_view` önce uzak tarihe bakar; yoksa açılış + politika saati.
 3. "Müşteri bekleniyor" durumunda SAYAÇ DURUR. Yanıt bizde değilken geçen
    süreyi kendi gecikmemiz saymak, personeli olmayan bir suçla cezalandırır.
 4. Kapanmış talebin SLA'sı YOKTUR (`done`), sıfır değil — sıfır "tam
    zamanında" demektir ve kapanmış talep listesini kırmızıya boyardı.
 5. Renk tek başına anlam taşımaz: `sla_view` her zaman SAYI + YAZI da verir.
 6. Para her yerde kuruş (int). Bagisto ondalık gönderir; `to_kurus` float
    kullanmadan çevirir.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit (store_api) de 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Talep türleri. Anahtar İngilizce (tel), etiket Türkçe (ekran).
TYPE_LABELS = {
    "return": "İade",
    "exchange": "Değişim",
    "defect": "Arıza",
    "info": "Bilgi",
    "complaint": "Şikayet",
    "invoice": "Fatura",
}

#: Durumlar PANO SÜTUN SIRASINDA. Sıra iş akışıdır; alfabetik değildir.
STATUS_ORDER = ("new", "reviewing", "waiting_customer", "approved", "rejected", "closed")

STATUS_LABELS = {
    "new": "Yeni",
    "reviewing": "İnceleniyor",
    "waiting_customer": "Müşteri bekleniyor",
    "approved": "Onaylandı",
    "rejected": "Reddedildi",
    "closed": "Kapandı",
}

#: SLA sayacının DURDUĞU durumlar. Yanıt müşteride: geçen süre bizim
#: gecikmemiz değildir (karar 3).
SLA_PAUSED = ("waiting_customer",)

#: SLA sayacının BİTTİĞİ durumlar. Karar verilmiş talebin kalan süresi yoktur.
SLA_DONE = ("approved", "rejected", "closed")

PRIORITY_ORDER = ("urgent", "high", "normal", "low")

PRIORITY_LABELS = {
    "urgent": "Acil",
    "high": "Yüksek",
    "normal": "Normal",
    "low": "Düşük",
}

CHANNEL_LABELS = {
    "web": "Web",
    "email": "E-posta",
    "phone": "Telefon",
    "whatsapp": "WhatsApp",
}

#: Öncelik başına varsayılan yanıt süresi (saat). Ayarla ezilir.
DEFAULT_SLA_HOURS = {"urgent": 4, "high": 12, "normal": 24, "low": 48}

#: SLA durum etiketleri. Rozetin YANINDA her zaman saat sayısı durur (karar 5).
SLA_TONES = {"overdue": "bad", "today": "warn", "soon": "warn", "ok": "good",
             "paused": "info", "done": "dim", "none": "dim"}


# ===================================================================== temel

def text(value: Any) -> str:
    """`None` ile boş metni ayırmadan güvenli metne çevirir."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return default


def to_kurus(value: Any) -> int | None:
    """Bagisto'nun ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: `float` kullanılmaz. `float("1234.35") * 100` bazı değerlerde
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar — iade tutarında
    binde bir görünen, bulunması imkânsız bir sapmadır.
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


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def pick(raw: Any, *names: str) -> Any:
    """Bir bilgiyi olası adlarının hepsinde arar (karar 1).

    `/api/admin/bbd/return-requests` hâlâ yazılıyor; alan adının `orderId` mi
    `order_id` mi olacağı kesinleşmedi. Tek ada bağlanmak, uç yayınlandığı gün
    ekranın boş sütun göstermesi demekti. Bulunamazsa `None` döner ve ekran
    "—" yazar — uydurma değer üretilmez.
    """
    if not isinstance(raw, dict):
        return None
    nested = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
    sources = [raw, nested] if nested else [raw]
    for name in names:
        for source in sources:
            if name in source and source[name] not in (None, ""):
                return source[name]
    for name in names:
        for source in sources:
            if name in source:
                return source[name]
    return None


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_time(value: Any) -> datetime | None:
    """ISO zaman damgasını saat dilimli `datetime`a çevirir.

    SAAT DİLİMSİZ DAMGA MAĞAZANIN YEREL SAATİDİR, UTC DEĞİL. Bagisto iki
    biçim birden gönderiyor: `/settings/users` damgayı `+03:00` ekiyle verir,
    `/orders` ise çıplak `2026-08-13 18:27:17` yazar. Çıplak olanı UTC saymak
    kanıtlanmış biçimde yanlıştır — 2026-08-13'te canlıdan okunan en yeni
    sipariş `18:27:17` damgalıydı, sunucunun kendi saati ise `16:44 UTC`:
    UTC varsayımı var olan bir siparişi 1 saat 43 dakika GELECEĞE atıyordu.

    Bu, kozmetik bir tarih hatası değil: `sla_view` kalan süreyi bu damgadan
    hesaplar. Üç saat ileri kaydırılmış bir açılış zamanı, 4 saatlik acil bir
    talepte "3,9 saat kaldı" yazarken gerçekte 0,9 saat kalmış olması demektir
    — ekran tam da uyarması gereken anda sakin görünür.

    Çıplak damga MASAÜSTÜNÜN yerel saat diliminde çözülür: mağaza sunucusu da
    Kontrol Merkezi'nin çalıştığı makine de Europe/Istanbul'da. Saat dilimli
    damga geldiğinde ona dokunulmaz.
    """
    raw = text(value)
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(raw[:32])
    except ValueError:
        try:
            parsed = datetime.fromisoformat(raw[:19])
        except ValueError:
            return None
    # Çıplak `datetime.astimezone()` naif damgayı SİSTEM YERELİ sayar ve
    # saat dilimli hâle getirir; `sla_view` aritmetiği aware datetime ister.
    return parsed if parsed.tzinfo else parsed.astimezone()


def local_stamp(value: Any) -> str:
    """Damgayı yerel saatte `2026-08-13 14:20` biçimine getirir."""
    parsed = parse_time(value)
    if parsed is None:
        return ""
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


# ======================================================================= SLA

def sla_hours(config: Any) -> dict[str, int]:
    """Ayardan gelen öncelik→saat eşlemesi; eksik anahtar varsayılanla dolar."""
    out = dict(DEFAULT_SLA_HOURS)
    if isinstance(config, dict):
        for key, value in config.items():
            name = text(key).lower()
            if name in out:
                out[name] = max(1, min(720, as_int(value, out[name])))
    return out


def sla_view(raw: dict[str, Any], *, hours: dict[str, int] | None = None,
             now: datetime | None = None) -> dict[str, Any]:
    """Talebin SLA durumu: kalan saat + durum + okunur etiket.

    Dönüş `hoursLeft` HER ZAMAN sayı ya da `None`'dır; ekran rozetin yanına
    bu sayıyı yazar (karar 5). Renk yalnızca destekler.
    """
    policy = hours or DEFAULT_SLA_HOURS
    moment = now or now_utc()
    status = text(pick(raw, "status", "state")).lower()
    priority = text(pick(raw, "priority")).lower() or "normal"

    if status in SLA_DONE:
        # Kapanmış talebin kalan süresi YOKTUR; sıfır demek "tam zamanında"
        # demektir ve arşivi kırmızıya boyardı (karar 4).
        return {"hoursLeft": None, "state": "done", "tone": SLA_TONES["done"],
                "dueAt": "", "label": "Kapandı — süre işlemiyor"}

    due = parse_time(pick(raw, "due_at", "dueAt", "sla_due_at", "slaDueAt"))
    if due is None:
        opened = parse_time(pick(raw, "created_at", "createdAt", "opened_at"))
        if opened is None:
            return {"hoursLeft": None, "state": "none", "tone": SLA_TONES["none"],
                    "dueAt": "", "label": "Açılış zamanı yok — süre hesaplanamadı"}
        due = opened + timedelta(hours=policy.get(priority, policy.get("normal", 24)))

    if status in SLA_PAUSED:
        # Yanıt müşteride: sayaç durur (karar 3). Kalan süre yine gösterilir
        # ki personel talebi büsbütün unutmasın.
        remaining = round((due - moment).total_seconds() / 3600, 1)
        return {"hoursLeft": remaining, "state": "paused", "tone": SLA_TONES["paused"],
                "dueAt": due.astimezone().isoformat(timespec="minutes"),
                "label": "Yanıt müşteride — sayaç durdu"}

    remaining = round((due - moment).total_seconds() / 3600, 1)
    if remaining < 0:
        state = "overdue"
        label = f"{abs(remaining):.1f} saat gecikti".replace(".", ",")
    elif remaining <= 4:
        state = "today"
        label = f"{remaining:.1f} saat kaldı".replace(".", ",")
    elif remaining <= 12:
        state = "soon"
        label = f"{remaining:.1f} saat kaldı".replace(".", ",")
    else:
        state = "ok"
        label = f"{remaining:.1f} saat kaldı".replace(".", ",")

    return {"hoursLeft": remaining, "state": state, "tone": SLA_TONES[state],
            "dueAt": due.astimezone().isoformat(timespec="minutes"), "label": label}


def sla_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """KPI şeridinin sayıları. Renk değil SAYI taşır."""
    counts = {"total": len(rows), "overdue": 0, "today": 0, "paused": 0, "open": 0,
              "unanswered": 0}
    for row in rows:
        state = (row.get("sla") or {}).get("state")
        if state == "overdue":
            counts["overdue"] += 1
        elif state == "today":
            counts["today"] += 1
        elif state == "paused":
            counts["paused"] += 1
        if row.get("status") not in SLA_DONE:
            counts["open"] += 1
        if row.get("awaitingUs") is True:
            counts["unanswered"] += 1
    return counts


# ================================================================ liste satırı

def request_row(raw: dict[str, Any], *, hours: dict[str, int] | None = None,
                now: datetime | None = None) -> dict[str, Any]:
    """Ham talep kaydı → tablonun/panonun beklediği satır."""
    kind = text(pick(raw, "type", "kind", "request_type", "requestType")).lower() or "return"
    status = text(pick(raw, "status", "state")).lower() or "new"
    priority = text(pick(raw, "priority")).lower() or "normal"
    channel = text(pick(raw, "channel", "source")).lower()

    assignee = pick(raw, "assignee", "assigned_to", "agent")
    if isinstance(assignee, dict):
        assignee_name = text(assignee.get("name") or assignee.get("full_name"))
        assignee_id = as_int(assignee.get("id"))
    else:
        assignee_name = text(assignee)
        assignee_id = as_int(pick(raw, "assignee_id", "assigneeId",
                                  "assigned_to_id", "assignedToId"))

    customer = pick(raw, "customer")
    if isinstance(customer, dict):
        customer_name = text(customer.get("name") or customer.get("full_name"))
        customer_id = as_int(customer.get("id"))
        customer_mail = text(customer.get("email"))
    else:
        customer_name = text(pick(raw, "customer_name", "customerName"))
        customer_id = as_int(pick(raw, "customer_id", "customerId"))
        customer_mail = text(pick(raw, "customer_email", "customerEmail", "email"))

    awaiting = pick(raw, "awaiting_reply", "awaitingReply", "awaiting_us")
    last_from = text(pick(raw, "last_message_from", "lastMessageFrom")).lower()
    if awaiting is None and last_from:
        awaiting = last_from in ("customer", "musteri", "client")
    elif awaiting is not None:
        awaiting = bool(awaiting)

    row = {
        "id": as_int(pick(raw, "id", "request_id", "requestId")),
        "code": text(pick(raw, "code", "number", "reference")) or "",
        "type": kind,
        "typeLabel": TYPE_LABELS.get(kind, kind or "—"),
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status or "—"),
        "priority": priority,
        "priorityLabel": PRIORITY_LABELS.get(priority, priority),
        "channel": channel,
        "channelLabel": CHANNEL_LABELS.get(channel, channel or "—"),
        "subject": text(pick(raw, "subject", "title", "summary")) or "(konusuz)",
        "orderId": as_int(pick(raw, "order_id", "orderId")),
        "orderNumber": text(pick(raw, "order_number", "orderNumber",
                                 "increment_id", "incrementId")),
        "customerId": customer_id,
        "customerName": customer_name or "—",
        "customerEmail": customer_mail,
        "assigneeId": assignee_id,
        "assignee": assignee_name,
        "createdAt": local_stamp(pick(raw, "created_at", "createdAt")),
        "updatedAt": local_stamp(pick(raw, "updated_at", "updatedAt",
                                      "last_activity_at", "lastActivityAt")),
        "returnCode": text(pick(raw, "return_code", "returnCode",
                                "return_tracking", "returnTracking")),
        "awaitingUs": awaiting,
        "messageCount": as_int(pick(raw, "message_count", "messageCount"), 0),
    }
    if not row["code"]:
        row["code"] = f"#{row['id']}" if row["id"] else "—"
    row["sla"] = sla_view(raw, hours=hours, now=now)
    return row


def board_columns(rows: list[dict[str, Any]], totals: dict[str, int] | None = None,
                  ) -> list[dict[str, Any]]:
    """Satırları pano sütunlarına dağıtır. Sütun başlığındaki sayı GERÇEK
    toplamdır (`totals`), gösterilen kart sayısı değil — kullanıcı "8 kart
    görüyorum ama 143 talep var" bilgisini kaybetmez."""
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in STATUS_ORDER}
    for row in rows:
        grouped.setdefault(row.get("status") or "new", []).append(row)
    out: list[dict[str, Any]] = []
    for key in STATUS_ORDER:
        cards = grouped.get(key, [])
        out.append({
            "key": key,
            "label": STATUS_LABELS[key],
            "total": (totals or {}).get(key, len(cards)),
            "shown": len(cards),
            "overdue": len([row for row in cards
                            if (row.get("sla") or {}).get("state") == "overdue"]),
            "cards": cards,
        })
    return out


# ============================================================ yazışma zinciri

def thread_rows(raw: dict[str, Any], notes: list[dict[str, Any]] | None = None,
                ) -> list[dict[str, Any]]:
    """Müşteri yazışması (uzak) + iç not (yerel) tek zaman çizelgesinde.

    İÇ NOT YEREL KALIR. Uzak uca "internal" bayrağıyla yazmak, o bayrağın
    müşteri portalında yanlış yorumlanması hâlinde personelin kendi arasında
    yazdığını müşteriye gösterirdi. Geri alınamaz bir hata; ucun davranışına
    güvenmek yerine not hiç gönderilmiyor.
    """
    out: list[dict[str, Any]] = []
    messages = pick(raw, "messages", "comments", "thread")
    for item in messages if isinstance(messages, list) else []:
        if not isinstance(item, dict):
            continue
        author_type = text(pick(item, "author_type", "authorType", "from")).lower()
        internal = bool(pick(item, "internal", "is_internal"))
        if author_type in ("customer", "musteri", "client"):
            side = "customer"
        elif internal:
            side = "internal"
        else:
            side = "staff"
        out.append({
            "id": as_int(pick(item, "id")),
            "side": side,
            "author": text(pick(item, "author", "author_name", "user")) or (
                "Müşteri" if side == "customer" else "Personel"),
            "body": text(pick(item, "body", "message", "comment", "content")),
            "createdAt": local_stamp(pick(item, "created_at", "createdAt")),
            "sortKey": text(pick(item, "created_at", "createdAt")),
            "attachments": [text(entry.get("url") if isinstance(entry, dict) else entry)
                            for entry in (pick(item, "attachments", "files") or [])],
            "local": False,
        })

    for note in notes or []:
        out.append({
            "id": as_int(note.get("id")),
            "side": "internal",
            "author": text(note.get("actor")) or "Personel",
            "body": text(note.get("body")),
            "createdAt": local_stamp(note.get("created_at")),
            "sortKey": text(note.get("created_at")),
            "attachments": [],
            "local": True,
        })

    out.sort(key=lambda item: item["sortKey"] or "")
    return out


def awaiting_us(thread: list[dict[str, Any]], status: str) -> bool | None:
    """Yanıt bizde mi bekliyor? Zincir boşsa BİLİNMEZ (`None`) — "hayır"
    saymak, hiç yanıtlanmamış talebi temiz gösterirdi."""
    if status in SLA_DONE:
        return False
    visible = [item for item in thread if item["side"] != "internal"]
    if not visible:
        return None
    return visible[-1]["side"] == "customer"


# =============================================================== sipariş özeti

def order_summary(order: Any, *, order_id: int = 0, order_number: str = "",
                  ) -> dict[str, Any]:
    """Geçitten gelen sipariş kaydı → çekmecedeki "Sipariş özeti" satırları.

    TUZAK — CANLI MAĞAZA camelCase KONUŞUYOR. Bagisto yönetici API'si yanıtları
    `AdminCollectionEnvelopeNormalizer` üzerinden geçiriyor: canlı
    `GET /api/admin/orders/{id}` yanıtında `incrementId · grandTotal ·
    createdAt · shippingTitle` var, `increment_id`/`grand_total` YOK. Yalnız
    snake_case aranırsa ekran açılır, kart çizilir ve dört satırın hepsi "—"
    görünür — kimse fark etmez. Her ad İKİ yazımıyla da aranır: bugünkü canlı
    yanıt camelCase, uç sürümü değişirse snake_case yine tutar.
    """
    if not isinstance(order, dict) or not order:
        return {}
    return {
        "id": order_id or as_int(pick(order, "id")),
        "number": order_number or text(pick(order, "increment_id", "incrementId",
                                            "order_number", "orderNumber")),
        "createdAt": local_stamp(pick(order, "created_at", "createdAt")),
        "total": to_kurus(pick(order, "grand_total", "grandTotal",
                               "base_grand_total", "baseGrandTotal")),
        "status": text(pick(order, "status")),
        # Etiket varsa ekran onu yazar: "processing" değil "İşleniyor".
        "statusLabel": text(pick(order, "status_label", "statusLabel")),
        "shipping": text(pick(order, "shipping_title", "shippingTitle",
                              "shipping_method", "shippingMethod",
                              "shipping_description", "shippingDescription")),
    }


# ========================================================= iade edilecek kalem

def return_item_rows(order: dict[str, Any], request: dict[str, Any],
                     ) -> list[dict[str, Any]]:
    """Siparişin kalemleri + talepte seçilmiş adetler.

    `maxQty` sipariş adedinden DAHA ÖNCE iade edilmiş VE İPTAL EDİLMİŞ adet
    düşülerek bulunur: iki kez iade edilen kalem mağazaya iki kez para iade
    ettirir, iptal edilmiş kalem ise hiç gönderilmediği için geri gelemez.
    Canlı sipariş kalemi bu üç sayacı `qtyRefunded · qtyCanceled` (+ ileride
    `qtyReturned`) adlarıyla taşıyor.
    """
    selected: dict[int, int] = {}
    for item in (pick(request, "items", "return_items", "returnItems") or []):
        if not isinstance(item, dict):
            continue
        key = as_int(pick(item, "order_item_id", "orderItemId", "item_id", "id"))
        if key:
            selected[key] = as_int(pick(item, "qty", "quantity"), 0)

    rows: list[dict[str, Any]] = []
    for item in (pick(order, "items", "order_items", "orderItems") or []):
        if not isinstance(item, dict):
            continue
        item_id = as_int(pick(item, "id", "order_item_id", "orderItemId"))
        ordered = as_int(pick(item, "qty_ordered", "qtyOrdered", "quantity", "qty"), 0)
        refunded = as_int(pick(item, "qty_refunded", "qtyRefunded"), 0)
        returned = as_int(pick(item, "qty_returned", "qtyReturned"), 0)
        canceled = as_int(pick(item, "qty_canceled", "qtyCanceled"), 0)
        unit = to_kurus(pick(item, "price", "unit_price", "unitPrice", "base_price",
                             "basePrice")) or 0
        gone = refunded + returned + canceled
        rows.append({
            "itemId": item_id,
            "productId": as_int(pick(item, "product_id", "productId")),
            "sku": text(pick(item, "sku")),
            "name": text(pick(item, "name", "product_name", "productName")) or "(adsız)",
            "qtyOrdered": ordered,
            "qtyReturned": refunded + returned,
            "qtyCanceled": canceled,
            "maxQty": max(0, ordered - gone),
            "unitPrice": unit,
            "qty": min(selected.get(item_id, 0), max(0, ordered - gone)),
        })
    return rows


def refund_estimate(rows: list[dict[str, Any]],
                    selection: dict[int, int] | None = None) -> dict[str, Any]:
    """Seçilen kalemlerin toplamı. TAHMİNDİR: kargo ücreti, kupon dağıtımı ve
    KDV düzeltmesi iade ekranında hesaplanır — burada söz verilmez."""
    total = 0
    count = 0
    for row in rows:
        qty = (selection or {}).get(row["itemId"], row.get("qty") or 0)
        qty = max(0, min(as_int(qty), row.get("maxQty") or 0))
        if not qty:
            continue
        count += qty
        total += qty * int(row.get("unitPrice") or 0)
    return {"items": count, "amount": total,
            "note": "Kalem tutarlarının toplamı. Kargo, kupon payı ve KDV düzeltmesi "
                    "İadeler ekranında hesaplanır."}


def selection_error(rows: list[dict[str, Any]], selection: dict[int, int]) -> str:
    """Seçim sipariş adedini aşıyor mu — YAZMADAN ÖNCE."""
    known = {row["itemId"]: row for row in rows}
    for item_id, qty in (selection or {}).items():
        row = known.get(as_int(item_id))
        if row is None:
            return f"Kalem #{item_id} bu siparişte yok."
        if as_int(qty) < 0:
            return "Adet negatif olamaz."
        if as_int(qty) > (row.get("maxQty") or 0):
            return (f"`{row['name']}` için en çok {row['maxQty']} adet iade edilebilir; "
                    f"{as_int(qty)} istendi.")
    return ""


# ================================================================== süzgeçler

def list_filters(*, q: str = "", kind: str = "", status: str = "", priority: str = "",
                 assignee: str = "", channel: str = "", start: str = "", end: str = "",
                 date_field: str = "created", sla: str = "", product: str = "",
                 awaiting: bool = False) -> dict[str, Any]:
    """Ekran süzgeçlerini uzak ucun sorgu parametrelerine çevirir.

    Boş süzgeç HİÇ GÖNDERİLMEZ: Laravel tanımadığı boş parametreyi bazen
    "eşittir boş" olarak yorumluyor ve liste sessizce boşalıyor.
    """
    filters: dict[str, Any] = {}
    if text(q):
        filters["q"] = text(q)
    if kind:
        filters["type"] = kind
    if status:
        filters["status"] = status
    if priority:
        filters["priority"] = priority
    if assignee:
        filters["assignee"] = assignee
    if channel:
        filters["channel"] = channel
    if product:
        filters["product"] = text(product)
    if start:
        filters["date_from"] = start
    if end:
        filters["date_to"] = end
    if start or end:
        filters["date_field"] = "updated" if date_field == "updated" else "created"
    if sla in ("overdue", "today"):
        filters["sla"] = sla
    if awaiting:
        filters["awaiting_reply"] = 1
    return filters


def apply_local_guards(rows: list[dict[str, Any]], *, sla: str = "",
                       awaiting: bool = False) -> tuple[list[dict[str, Any]], bool]:
    """SLA ve "yanıt bizde" süzgeçleri uzakta uygulanmamış olabilir.

    Bu iki süzgeç TÜRETİLMİŞ bilgidir: uzak uç `sla` parametresini tanımıyorsa
    süzülmemiş liste süzülmüş gibi görünürdü. Sayfa üzerinde tekrar süzeriz ve
    ikinci dönüş değeriyle "bu sayfa yerelde daraltıldı" deriz — ekran bunu
    yazar, sayfalama sayısının artık tam olmadığını gizlemez.
    """
    if not sla and not awaiting:
        return rows, False
    out = rows
    if sla == "overdue":
        out = [row for row in out if (row.get("sla") or {}).get("state") == "overdue"]
    elif sla == "today":
        out = [row for row in out if (row.get("sla") or {}).get("state") in ("today", "overdue")]
    if awaiting:
        out = [row for row in out if row.get("awaitingUs") is True]
    return out, len(out) != len(rows)


def csv_table(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    """Liste CSV'si — başlık + satırlar. Saat sayısı da yazılır, renk değil."""
    headers = ["Talep no", "Açılış", "Tür", "Konu", "Müşteri", "Sipariş", "Öncelik",
               "Atanan", "Son hareket", "SLA kalan (saat)", "SLA durumu", "Durum"]
    table = [[
        row["code"], row["createdAt"], row["typeLabel"], row["subject"], row["customerName"],
        row["orderNumber"] or (f"#{row['orderId']}" if row["orderId"] else ""),
        row["priorityLabel"], row["assignee"] or "—", row["updatedAt"],
        "" if (row.get("sla") or {}).get("hoursLeft") is None
        else f"{row['sla']['hoursLeft']:.1f}".replace(".", ","),
        (row.get("sla") or {}).get("label", ""), row["statusLabel"],
    ] for row in rows]
    return headers, table
