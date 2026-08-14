"""Müşteri aşama SMS'inin TETİKLEYİCİ tarafı — saf mantık, ağa çıkmaz.

Metni, freni ve tekrar engelini Bildirimler modülü tutar; burası yalnız
"hangi sipariş, hangi aşamaya geçti ve künyesi ne" sorusunu cevaplar. İki
modül birbirini IMPORT ETMEZ (K3): aralarındaki tek bağ `store.notify.stage`
yeteneği ve aşağıdaki KÜNYE SÖZLEŞMESİDİR.

KÜNYE SÖZLEŞMESİ — `stage_order()` bu alanları üretir, karşı taraf bunları bekler:
    orderId · orderNo · customer · phone · total · date · carrier · track · trackUrl

DÖRT TUZAK:

 1. `₺` SMS'İ ÜÇ KATINA ÇIKARIR. Çekirdeğin `money()` yardımcısı `12.345,67 ₺`
    üretiyor; `₺` GSM-7 kümesinde YOK ve tek başına mesajı UCS-2'ye düşürüyor
    (160 karakter sınırı 70'e iner). Aşama SMS'ine giden tutar `money_sms()`
    ile yazılır ve para birimi `TL`dir.
 2. TAŞIYICI DURUM YAZIMI TEK DEĞİL. Geliver/taşıyıcı "delivered", "completed",
    "DONE", "teslim edildi" diye de yazabiliyor; tek yazıma bağlanan kod teslim
    edilmiş gönderiyi hiç görmez ve müşteri "teslim edildi" SMS'ini hiç almaz.
    `is_delivered` katlayarak bakar ve TANIMADIĞINI TESLİM SAYMAZ.
 3. TOPLU "KARGOYA VER" TAKİP NUMARASI ÜRETMEZ. Bagisto gönderisi boş takip
    numarasıyla açılıyor (her paketin numarası ayrıdır); numara taşıyıcının
    kendi kaydındadır. Künye önce siparişin üzerindeki numaraya, o boşsa
    taşıyıcı kaydına bakar — ikisi de boşsa alan BOŞ kalır ve karşı taraf
    "gönderilemedi: bilgi eksik" der. Yarım bir kargo SMS'i gönderilmez.
 4. GERİYE DÖNÜK TOPLU GÖNDERİM. Aşama SMS'i ilk kez açıldığında tarama
    geçmişteki her siparişi "yeni" görürdü: 1.800 kişiye "siparişiniz alındı"
    gitmesi geri alınamaz. `within_window` taramayı gün penceresiyle sınırlar.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from . import orders as ord_

#: Aşama anahtarları. Bildirimler modülüyle ORTAK SÖZLEŞMEDİR; o modül import
#: EDİLMEZ (K3), yalnız dizeler taşınır — geçit hata kodlarında olduğu gibi.
STAGE_PLACED = "order_placed"
STAGE_SHIPPED = "shipped"
STAGE_DELIVERED = "delivered"

STAGES = (STAGE_PLACED, STAGE_SHIPPED, STAGE_DELIVERED)

STAGE_LABELS = {
    STAGE_PLACED: "Sipariş alındı",
    STAGE_SHIPPED: "Kargoya verildi",
    STAGE_DELIVERED: "Teslim edildi",
}

#: Taramayla tetiklenebilen aşamalar. "Kargoya verildi" taramaya GİRMEZ: onun
#: tetikleyicisi personelin kendi eylemidir (`ship`/`batch_apply`), tarama onu
#: ikinci kez yakalamaya çalışırsa aynı işi iki yerden yapmış oluruz.
SWEEP_STAGES = (STAGE_PLACED, STAGE_DELIVERED)

#: "Teslim edildi" sayılan durum yazımları (TUZAK 2). Katlanmış hâlleriyle
#: aranır: boşluk/tire alt çizgiye döner, Türkçe harfler ASCII'ye iner.
DELIVERED_WORDS = frozenset({
    "delivered", "completed", "done", "teslim_edildi", "teslimedildi",
    "teslim", "delivered_to_consignee", "delivery_completed",
})

#: Sipariş bu durumdaysa "siparişiniz alındı" SMS'i ATILMAZ. İptal edilmiş ya
#: da sahte işaretlenmiş bir siparişin müşterisine teşekkür etmek, ekranın
#: söylediğiyle müşterinin duyduğunu ayrıştırır.
DEAD_STATUSES = ("canceled", "cancelled", "closed", "fraud")


def money_sms(kurus_value: Any) -> str:
    """Kuruş → `1.249,90 TL` (TUZAK 1).

    `₺` KULLANILMAZ: GSM-7 kümesinde yok, tek karakteri mesajı UCS-2'ye
    düşürüp 160 karakterlik sınırı 70'e indiriyor ve her siparişte üç kredi
    harcatıyordu.
    """
    amount = Decimal(ord_.as_int(kurus_value)) / 100
    whole, _, fraction = f"{amount:.2f}".partition(".")
    negative = whole.startswith("-")
    digits = whole.lstrip("-")
    groups: list[str] = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return f"{'-' if negative else ''}{'.'.join(groups)},{fraction} TL"


def is_delivered(raw: Any) -> bool:
    """Taşıyıcı durumu "teslim edildi" mi (TUZAK 2).

    TANIMADIĞI DURUMU TESLİM SAYMAZ. Bilinmeyen bir kelimeyi teslim kabul
    etmek, yoldaki bir gönderi için müşteriye "teslim edildi" yazmaktır ve o
    mesaj geri alınamaz.
    """
    key = ord_.fold(raw).replace(" ", "_").replace("-", "_")
    return key in DELIVERED_WORDS


def shipment_view(raw: Any) -> dict[str, Any]:
    """Taşıyıcı gönderi kaydı → künyeye giren üç alan + durum.

    ALAN ADLARI İKİ YAZIMDA DA ARANIR (`pick`): geçit kimi uçta snake_case,
    kimi uçta camelCase döndürüyor ve tek yazıma bakan kod hiçbir şey bulmadan
    İSTİSNA DA ATMAZ — takip numarası sessizce boş kalırdı.
    """
    if not isinstance(raw, dict):
        return {"shipmentId": 0, "orderId": 0, "carrier": "", "track": "", "trackUrl": "",
                "status": "", "delivered": False}
    status = ord_.text(ord_.pick(raw, "status"))
    return {
        "shipmentId": ord_.as_int(ord_.pick(raw, "id")),
        "orderId": ord_.as_int(ord_.pick(raw, "order_id")),
        "carrier": ord_.text(ord_.pick(raw, "carrier_title", "carrier_name", "carrier",
                                       "provider")),
        "track": ord_.text(ord_.pick(raw, "tracking_number", "tracking_no", "track_number",
                                     "barcode")),
        "trackUrl": ord_.text(ord_.pick(raw, "tracking_url", "track_url")),
        "status": status,
        "delivered": is_delivered(status),
    }


def stage_order(row: dict[str, Any], *, shipment: dict[str, Any] | None = None,
                carrier: str = "", track: str = "") -> dict[str, Any]:
    """Sipariş satırı (+ varsa taşıyıcı kaydı) → aşama SMS'inin künyesi.

    KAYNAK SIRASI (TUZAK 3): elle verilen değer → siparişin üzerindeki gönderi
    → taşıyıcının kendi kaydı. Toplu "kargoya ver" takip numarası üretmediği
    için sıradaki son basamak çoğu zaman tek dolu olandır; hiçbiri yoksa alan
    BOŞ kalır ve karşı taraf mesajı göndermez.
    """
    ship = shipment or {}
    return {
        "orderId": ord_.as_int(row.get("id")),
        "orderNo": ord_.text(row.get("orderNo")) or ord_.text(row.get("incrementId")),
        "customer": ord_.text(row.get("customer")),
        "phone": ord_.text(row.get("phone")),
        "total": money_sms(row.get("grandTotal")),
        "date": ord_.text(row.get("createdDay")) or ord_.text(row.get("createdAt"))[:10],
        "carrier": ord_.text(carrier) or ord_.text(row.get("carrier")) \
            or ord_.text(ship.get("carrier")),
        "track": ord_.text(track) or ord_.text(row.get("track")) or ord_.text(ship.get("track")),
        "trackUrl": ord_.text(ship.get("trackUrl")),
    }


def placed_block(row: dict[str, Any]) -> str:
    """"Siparişiniz alındı" bu satır için atlanmalı mı — nedeni ya da boş."""
    status = ord_.text(row.get("status"))
    if status in DEAD_STATUSES:
        return f"Sipariş durumu “{ord_.status_label(status)}”; alındı mesajı gönderilmez."
    return ""


def within_window(day: str, *, days: int, today: str = "") -> bool:
    """Sipariş tarama penceresinin içinde mi (TUZAK 4).

    Aşama SMS'i ilk açıldığında pencere olmasaydı tarama geçmişteki BÜTÜN
    siparişleri "yeni" görürdü: canlıda 1.800 kişiye "siparişiniz alındı"
    gitmesi geri alınamaz ve parası harcanmıştır.

    `days <= 0` → pencere yok (bilerek istenmişse). Tarih çözülemezse satır
    pencere DIŞINDA sayılır: bilinmeyen tarihli bir kaydı taze varsaymak,
    tam olarak yukarıdaki kazayı üretir.
    """
    if days <= 0:
        return True
    stamp = ord_.day_of(day) or ord_.text(day)[:10]
    if not stamp:
        return False
    try:
        moment = datetime.fromisoformat(stamp).date()
        now = datetime.fromisoformat(today or ord_.today_iso()).date()
    except ValueError:
        return False
    return moment >= now - timedelta(days=max(0, days - 1))


def sweep_stage_error(stage: str) -> str:
    if ord_.text(stage) not in SWEEP_STAGES:
        names = ", ".join(STAGE_LABELS[item] for item in SWEEP_STAGES)
        return (f"Taramayla tetiklenemeyen aşama: {ord_.text(stage) or '(boş)'}. "
                f"Beklenen: {names}. “Kargoya verildi” mesajı kargoya verme eyleminin "
                "kendisinden çıkar.")
    return ""
