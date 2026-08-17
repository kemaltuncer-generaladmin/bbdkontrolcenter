"""Kontrol Paneli — saf yardımcılar. AĞA ÇIKMAZ, DEPOYA YAZMAZ.

Burada yalnız biçim, etiket ve sözleşme sözlükleri var. İş kararı
`service.py`'de, HTTP kapısı `api/routes.py`'de durur; bu dosyanın tamamı yan
etkisizdir ve tek tek sınanabilir.

SAYI BURADA HESAPLANMAZ. `active`, `fill_rate`, `seconds_to_next_cutoff` ve
`pending_tasks` sunucuda üretilir (`BLD/docs/control/dashboard.md` → "Buradaki
sayılar tanımdır, tahmin değil"). Bu dosya onları yalnız GÜVENLİ TİPE çevirir:
eksik alan `None` kalır, `None` "bilinmiyor" demektir ve `0` DEĞİLDİR. Ayrım
kapasitede hayatidir — menü yayınlanmamışken `capacity_total` `null` döner ve
sıfıra çevirmek "gün doldu" demek olurdu.

ETİKET SÖZLÜĞÜ NEDEN BURADA DA VAR. `bld_orders` aynı durum kodlarının Türkçe
karşılığını taşıyor ve o dosya import EDİLMEZ: modül modülü import etmez (K3),
üstelik `bld_orders` kapatılabilir bir modüldür ve kapandığında bu ekranın da
düşmesi K7'yi çiğnerdi. Kodların kendisi sözleşmede dondurulmuştur
(`OrderStatusTransition::CODES`), yani iki sözlüğün ayrışma riski etiket
metniyle sınırlıdır — ekranı yanlış çalıştırmaz, yalnız kelimesi başka olur.

Kod İngilizce+ASCII, yorum Türkçe (depo kuralı).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# ================================================================== biçim


def text(value: Any) -> str:
    """Güvenli dize. `None` boş dizeye döner; `0` "0" olarak kalır."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def opt_int(value: Any) -> int | None:
    """Sayı ya da `None`. SIFIRA ÇEVİRMEZ.

    Sözleşme birkaç alanı bilerek `null` bırakıyor (menü yayınlanmamışsa
    kapasite, sağlık bildirmemiş kasa). `0` yazmak "ölçtük, sıfır çıktı"
    demektir; `null` "hiç ölçülmedi" demektir ve ikisi ekranda aynı görünemez.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool | None:
    """Üç değerli: `True` · `False` · `None` (bilinmiyor)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "evet", "on"}:
        return True
    if lowered in {"0", "false", "no", "hayir", "hayır", "off"}:
        return False
    return None


def now_iso() -> str:
    """Yerel iz için an. ISO 8601 UTC (depo kuralı)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


#: `YYYY-MM-DD`. Sunucu da denetler; buradaki kapı erken geri bildirim (K9).
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def date_error(value: Any, *, field: str = "Tarih") -> str:
    """Boş değer serbesttir (sunucu bugünü kullanır); dolu değer ISO olmalı."""
    raw = text(value)
    if not raw:
        return ""
    if not _ISO_DATE.match(raw):
        return f"{field} `YYYY-MM-DD` biçiminde olmalı."
    try:
        datetime.strptime(raw, "%Y-%m-%d")  # noqa: DTZ007 — yalnız biçim denetimi
    except ValueError:
        return f"{field} geçerli bir gün değil."
    return ""


# =============================================================== sözlükler

#: `OrderStatusTransition::CODES` — sözleşmede AÇIKÇA sayılıdır ve uydurulmaz.
#: Gösterge paneli `by_status` bloğunda YALNIZ terminal olmayan beşini görür;
#: terminal ikisi akış kutusundaki satırlarda geçebilir, o yüzden yedisi de
#: burada durur.
STATUS_CODES: tuple[str, ...] = (
    "yeni", "onaylandi", "hazirlaniyor", "hazir", "yolda", "teslim_edildi", "iptal",
)

#: `by_status` sözlüğünün taşıdığı beş kod (`dashboard.md`): `teslim_edildi` ve
#: `iptal` anahtarları BULUNMAZ — aktif kümenin dışındalar. Sıra ekranda
#: soldan sağa çizilen sıradır ve sipariş yaşam çizgisini izler.
ACTIVE_STATUS_CODES: tuple[str, ...] = (
    "yeni", "onaylandi", "hazirlaniyor", "hazir", "yolda",
)

STATUS_LABELS = {
    "yeni": "Yeni",
    "onaylandi": "Onaylandı",
    "hazirlaniyor": "Hazırlanıyor",
    "hazir": "Hazır",
    "yolda": "Yolda",
    "teslim_edildi": "Teslim edildi",
    "iptal": "İptal",
}

#: Ton TEK BAŞINA anlam taşımaz (kit kuralı 7): her rozetin içinde yazı da var.
STATUS_TONES = {
    "yeni": "info",
    "onaylandi": "info",
    "hazirlaniyor": "warn",
    "hazir": "good",
    "yolda": "good",
    "teslim_edildi": "dim",
    "iptal": "bad",
}

#: `pending_tasks[].level` — sözleşmedeki üç değer ve ekrandaki sırası.
LEVELS: tuple[str, ...] = ("critical", "warning", "info")

LEVEL_LABELS = {"critical": "Kritik", "warning": "Uyarı", "info": "Bilgi"}

#: `alertBox` tonları. `info` seviyesi kutu değil ipucu olarak çizilir; ayrım
#: panelde yapılır, ton adı yine de burada durur ki iki yerde uydurulmasın.
LEVEL_TONES = {"critical": "bad", "warning": "warn", "info": "info"}

#: Sözleşmenin saydığı ondört madde kodu. TANIMLAYICI DEĞİL, AÇIKLAYICIDIR:
#: listede olmayan bir kod gelirse madde YİNE GÖSTERİLİR. Sunucu sözleşmeye
#: yeni bir satır eklediğinde panelin onu sessizce yutması, yöneticinin
#: yapması gereken bir işi hiç görmemesi olurdu.
TASK_CODES: tuple[str, ...] = (
    "ordering_paused", "menu_missing", "no_device_online", "critical_event_open",
    "menu_draft", "capacity_full", "printer_fault", "print_queue_stale",
    "late_orders", "quote_requests_new", "contracts_awaiting", "payments_overdue",
    "subscriptions_pending", "unreleased_orders",
)

#: `monitor.health_status` (`monitor.md` → `GET /summary`). SUNUCUNUN tek
#: cümlelik hükmüdür ve istemcide TÜRETİLMEZ.
HEALTH_LABELS = {"ok": "Sağlıklı", "degraded": "Aksıyor", "down": "Çalışmıyor"}
HEALTH_TONES = {"ok": "good", "degraded": "warn", "down": "bad"}

# ======================================================= panel eşleştirmesi

#: `pending_tasks[].link` → Kontrol Merkezi panel kimliği.
#:
#: EŞLEŞTİRME YOLUN İLK PARÇASINA BAKAR, KODA DEĞİL. Sözleşme "panelin kod
#: eşleştirmesi yazması, yeni bir madde eklendiğinde tıklanamayan bir satır
#: üretirdi" diyor ve haklı: ondört kodu tek tek eşleyen bir tablo, on beşinci
#: kod eklendiğinde sessizce tıklanamaz bir satır doğururdu. Yol öneki ise
#: ALANIN kendisidir ve alan sayısı sabittir — `menu_missing` de `menu_draft`
#: de `/menu/...` ile başlar, ikisi de aynı ekrana gider.
#:
#: Bu tablo kaçınılmazdır: sözleşme bir YOL veriyor, kabuk ise PANEL KİMLİĞİ
#: ile geziniyor (`ctx.open(id, payload)`) ve kabuk modül adı bilmez (K1).
#: Çeviriyi yapacak başka bir yer yok.
PANEL_ROUTES = {
    "menu": "bld_menu",
    "orders": "bld_orders",
    "subscriptions": "bld_subscriptions",
    "customers": "bld_customers",
    "products": "bld_products",
    "invoices": "bld_invoices",
    "sms": "bld_sms",
    "notifications": "bld_notifications",
    "cms": "bld_cms",
    "settings": "bld_sales_settings",
    "monitor": "bld_status_monitor",
    "kds": "bld_kds",
}


def panel_for_link(link: Any) -> dict[str, Any]:
    """`/menu/days/2026-08-17` → hangi panel, hangi bağlam.

    Dönen sözlük:
      `panel`   — kabuğun `ctx.open` ile açacağı modül kimliği; bilinmeyen
                  önekte BOŞ kalır ve panel o satıra atlama düğmesi KOYMAZ.
                  Hiçbir yere gitmeyen bir düğme, bozuk bir düğmedir.
      `payload` — hedef ekrana geçilen bağlam. EN İYİ ÇABADIR: hedef panellerin
                  çoğu bugün `ctx.payload` okumuyor ve okumayan bir panel de
                  doğru ekranda açılır. Atlamanın değeri ekranın kendisinde.
      `link`    — sözleşmenin verdiği ham yol; eşleşme olmasa da gösterilir ki
                  kullanıcı nereye bakması gerektiğini okuyabilsin.
    """
    raw = text(link)
    out: dict[str, Any] = {"panel": "", "payload": {}, "link": raw}
    if not raw.startswith("/"):
        return out

    path, _, query = raw.partition("?")
    parts = [part for part in path.split("/") if part]
    if not parts:
        return out

    out["panel"] = PANEL_ROUTES.get(parts[0].lower(), "")

    payload: dict[str, Any] = {"link": raw, "path": parts[1:]}
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        if key:
            payload[key] = value
    # Yoldaki `YYYY-MM-DD` parçası ekranların en sık beklediği bağlam.
    # `date` sorgu dizesinden gelmişse ÜSTÜNE YAZILMAZ: açık verilmiş bir
    # değer, yoldan tahmin edilenden önce gelir.
    for part in parts[1:]:
        if _ISO_DATE.match(part) and "date" not in payload:
            payload["date"] = part
    out["payload"] = payload
    return out


# ================================================================ bloklar


def sales_block(raw: Any) -> dict[str, Any]:
    """`sales` — satışın açık olup olmadığı ve kesim saatine kalan süre.

    `seconds_to_next_cutoff` SUNUCUDA hesaplanır ve burada yeniden
    hesaplanmaz: geri sayımın tabanı `server_time`'dır, istemcinin kendi saati
    değil. Saati kaymış bir makinede yerel hesap, olmayan bir aciliyet
    yaratırdı (`dashboard.md` → `sales`).
    """
    data = raw if isinstance(raw, dict) else {}
    return {
        "ordering_enabled": as_bool(data.get("ordering_enabled")),
        "paused_until": text(data.get("paused_until")),
        "busy": as_bool(data.get("busy")),
        "cutoff_time": text(data.get("cutoff_time")),
        "cutoff_at": text(data.get("cutoff_at")),
        "cutoff_passed_for_today": as_bool(data.get("cutoff_passed_for_today")),
        "seconds_to_next_cutoff": opt_int(data.get("seconds_to_next_cutoff")),
        "next_cutoff_date": text(data.get("next_cutoff_date")),
    }


def orders_block(raw: Any) -> dict[str, Any]:
    """`orders` — durum dağılımı ve günün sayaçları.

    `by_status` BEŞ KODLA TAMAMLANIR: sözleşme "kalan beş kod sipariş yokken
    bile `0` ile durur" diyor, ama gövde eksik gelirse ekranın savunma yazması
    gerekirdi. Eksiği burada sıfırla doldurmak, yığılmış çubuğun her zaman aynı
    beş dilimi çizmesi demektir — dilim sayısı istekten isteğe değişseydi
    grafik her yoklamada sıçrardı.
    """
    data = raw if isinstance(raw, dict) else {}
    given = data.get("by_status") if isinstance(data.get("by_status"), dict) else {}
    by_status = {code: as_int(given.get(code), 0) for code in ACTIVE_STATUS_CODES}
    return {
        "by_status": by_status,
        "active": opt_int(data.get("active")),
        "delivered_today": opt_int(data.get("delivered_today")),
        "cancelled_today": opt_int(data.get("cancelled_today")),
        "created_today": opt_int(data.get("created_today")),
        "late": opt_int(data.get("late")),
        "revenue_today_kurus": opt_int(data.get("revenue_today_kurus")),
        "unreleased_subscription_orders": opt_int(
            data.get("unreleased_subscription_orders")),
    }


def capacity_block(raw: Any) -> dict[str, Any]:
    """`capacity` — gün tavanı, satılan porsiyon ve doluluk.

    `menu_published: false` ise diğer alanlar `null` DÖNER ve burada sıfıra
    çevrilmez: menü yayınlanmamışsa kapasite diye bir kavram yok, sıfır ise
    "doldu" anlamına gelirdi ve ekran satışın kapandığını sanardı.
    """
    data = raw if isinstance(raw, dict) else {}
    items = data.get("blocked_items")
    blocked = [
        {
            "menu_id": as_int(item.get("menu_id"), 0),
            "name": text(item.get("name")),
            "capacity": opt_int(item.get("capacity")),
            "sold": opt_int(item.get("sold")),
        }
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict)
    ]
    return {
        "menu_published": as_bool(data.get("menu_published")),
        "capacity_total": opt_int(data.get("capacity_total")),
        "sold_total": opt_int(data.get("sold_total")),
        "sold_orders": opt_int(data.get("sold_orders")),
        "sold_subscriptions": opt_int(data.get("sold_subscriptions")),
        "remaining_total": opt_int(data.get("remaining_total")),
        "fill_rate": opt_float(data.get("fill_rate")),
        # Sözleşme en çok 10 kalem söz veriyor; fazlası gelirse kesilir —
        # kutuyu taşıran bir liste, yanındaki doluluk çubuğunu ekrandan atardı.
        "blocked_items": blocked[:10],
    }


def subscriptions_block(raw: Any) -> dict[str, Any]:
    """`subscriptions` — abonelik sayaçları ve ödenmemiş dönemler.

    `overdue_*`, `unpaid_*`'in ALT KÜMESİDİR (sözleşme). Panel ikisini üst üste
    değil, biri diğerinin içinde gösterir; toplamak borcu iki kez sayardı.
    """
    data = raw if isinstance(raw, dict) else {}
    return {
        "active": opt_int(data.get("active")),
        "pending": opt_int(data.get("pending")),
        "paused": opt_int(data.get("paused")),
        "portions_today": opt_int(data.get("portions_today")),
        "contracts_awaiting_signature": opt_int(data.get("contracts_awaiting_signature")),
        "unpaid_periods": opt_int(data.get("unpaid_periods")),
        "unpaid_total_kurus": opt_int(data.get("unpaid_total_kurus")),
        "overdue_periods": opt_int(data.get("overdue_periods")),
        "overdue_total_kurus": opt_int(data.get("overdue_total_kurus")),
    }


def devices_block(raw: Any) -> dict[str, Any]:
    """`devices` — kasa sağlığı ve baskı kuyruğu (`monitor.md` ile aynı sayılar)."""
    data = raw if isinstance(raw, dict) else {}
    return {
        "total": opt_int(data.get("total")),
        "online": opt_int(data.get("online")),
        "revoked": opt_int(data.get("revoked")),
        "printer_fault": opt_int(data.get("printer_fault")),
        "queue_pending": opt_int(data.get("queue_pending")),
        "queue_failed": opt_int(data.get("queue_failed")),
        "queue_oldest_age_minutes": opt_int(data.get("queue_oldest_age_minutes")),
    }


def monitor_block(raw: Any) -> dict[str, Any]:
    """`monitor` — açık hata olayları ve sunucunun sağlık hükmü."""
    data = raw if isinstance(raw, dict) else {}
    status = text(data.get("health_status"))
    return {
        "open_total": opt_int(data.get("open_total")),
        "critical_open": opt_int(data.get("critical_open")),
        "error_open": opt_int(data.get("error_open")),
        "warning_open": opt_int(data.get("warning_open")),
        "health_status": status,
        "health_label": HEALTH_LABELS.get(status, status or "Bilinmiyor"),
        "health_tone": HEALTH_TONES.get(status, "dim"),
    }


def pending_task(raw: Any) -> dict[str, Any]:
    """Bekleyen iş satırı — CÜMLE SUNUCUDAN GELİR.

    `title` ve `detail` olduğu gibi taşınır; panel kendi cümlesini KURMAZ.
    Aynı durumun iki ekranda iki farklı cümleyle anlatılması, sahada telefonda
    konuşan iki kişinin farklı şey söylemesi demektir (`dashboard.md`).
    """
    data = raw if isinstance(raw, dict) else {}
    level = text(data.get("level")).lower()
    if level not in LEVELS:
        # Bilinmeyen seviye "bilgi" sayılır. Kritik saymak, sunucunun yazım
        # hatasını ekranda kırmızı bir alarma çevirirdi.
        level = "info"
    target = panel_for_link(data.get("link"))
    return {
        "code": text(data.get("code")),
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "tone": LEVEL_TONES[level],
        "title": text(data.get("title")),
        "detail": text(data.get("detail")),
        "count": as_int(data.get("count"), 0),
        "link": target["link"],
        "panel": target["panel"],
        "payload": target["payload"],
        # Sözleşmenin saymadığı bir kod geldi mi? Madde yine gösterilir; bu
        # bayrak yalnız panelin "yeni" rozeti koyabilmesi için.
        "known": text(data.get("code")) in TASK_CODES,
    }


def pending_tasks(raw: Any) -> list[dict[str, Any]]:
    """Bekleyen işler — SIRA SUNUCUNUNDUR, yalnız seviye grupları korunur.

    Sözleşme sırayı `critical` → `warning` → `info`, her grup içinde aciliyete
    göre veriyor. Grup içi sırayı burada yeniden hesaplamak, sunucunun bildiği
    aciliyeti (kesim saatine kalan süre gibi) kaybetmek olurdu; bu yüzden
    yalnız seviyeye göre KARARLI biçimde diziyoruz ve grup içindeki geliş
    sırası korunuyor.

    En çok 12 madde: sözleşmenin tavanı. Sunucu fazlasını gönderirse kesilir —
    otuz maddelik bir liste, "bugün ne yapmalıyım" sorusunu cevaplamak yerine
    ekranı doldururdu.
    """
    rows = [pending_task(item) for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict)]
    order = {level: index for index, level in enumerate(LEVELS)}
    rows.sort(key=lambda row: order.get(row["level"], len(LEVELS)))
    return rows[:12]


def flow_row(raw: Any) -> dict[str, Any]:
    """Canlı akış satırı — `orders.md` liste satırının EKRANA GEREKEN kadarı.

    Müşteri telefonu ALINMAZ. Gösterge paneli açık bir ekranda saatlerce durur
    ve bir kişisel veriyi orada tutmanın karşılığı yok: numaraya ihtiyaç
    duyan zaten Sipariş Yönetimi ekranına gider.
    """
    data = raw if isinstance(raw, dict) else {}
    status = text(data.get("status"))
    return {
        "id": as_int(data.get("id"), 0),
        "order_number": text(data.get("order_number")),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status or "—"),
        "status_tone": STATUS_TONES.get(status, "dim"),
        "customer_name": text(data.get("customer_name")),
        "item_count": opt_int(data.get("item_count")),
        "total_kurus": opt_int(data.get("total_kurus")),
        "is_subscription": bool(data.get("is_subscription")),
        "service_date": text(data.get("service_date")),
        "created_at": text(data.get("created_at")),
        "updated_at": text(data.get("updated_at")),
    }


def screen_contract() -> dict[str, Any]:
    """Ekranın AĞA ÇIKMADAN çizebileceği her şey (K7).

    Geçit düşükken bile rozetler, seviye adları ve durum etiketleri
    çizilebilmeli: boş bir ekran "sunucu düştü" ile "bugün hiç sipariş yok"
    arasındaki farkı anlatamaz.
    """
    return {
        "status_codes": list(STATUS_CODES),
        "active_status_codes": list(ACTIVE_STATUS_CODES),
        "status_labels": dict(STATUS_LABELS),
        "status_tones": dict(STATUS_TONES),
        "levels": list(LEVELS),
        "level_labels": dict(LEVEL_LABELS),
        "level_tones": dict(LEVEL_TONES),
        "task_codes": list(TASK_CODES),
        "health_labels": dict(HEALTH_LABELS),
        "health_tones": dict(HEALTH_TONES),
        "panel_routes": dict(PANEL_ROUTES),
    }
