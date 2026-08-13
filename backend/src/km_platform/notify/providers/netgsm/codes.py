"""Netgsm'e özgü kod ve biçim eşlemeleri.

Bu dosya sağlayıcıya bağımlı olan her şeyi tek yerde toplar: hata kodları,
tarih biçimleri, İYS değerleri. Adapter bunları kullanır; üst katmanlar
buradaki hiçbir sabiti görmez.

Kaynak: netgsm-sms 1.1.2 hata kodu tabloları ve metot imzaları.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ...contracts import DeliveryStatus
from ...errors import (
    SmsAuthError,
    SmsError,
    SmsProviderError,
    SmsRateLimited,
    SmsRejected,
)

#: Başarı kodu. Bundan farklı her değer hataya çevrilir.
SUCCESS = "00"

#: Kod → (hata sınıfı, Türkçe açıklama).
#: Kaynak: NETGSM_*_ERROR_CODES tabloları.
ERROR_MAP: dict[str, tuple[type[SmsError], str]] = {
    "20": (SmsRejected, "Mesaj metni hatalı veya azami karakter sayısı aşıldı"),
    "30": (
        SmsAuthError,
        "Kullanıcı adı/parola hatalı, API erişimi yok veya istek yetkisiz bir IP'den geldi",
    ),
    "40": (SmsRejected, "Gönderici başlığı sistemde tanımlı değil"),
    "50": (SmsRejected, "Aboneliğiniz İYS kontrollü gönderim yapamıyor"),
    "51": (SmsRejected, "Aboneliğe ait İYS marka bilgisi bulunamadı"),
    "60": (SmsRejected, "Kayıt bulunamadı (rapor ölçütü veya JobID geçersiz)"),
    "70": (SmsRejected, "Parametre hatalı veya zorunlu bir alan eksik"),
    "80": (SmsRateLimited, "Gönderim sınırı aşıldı"),
    "85": (
        SmsRateLimited,
        "Aynı numaraya 1 dakika içinde 20'den fazla görev açılamaz",
    ),
    "100": (SmsProviderError, "Sağlayıcı sistem hatası"),
    "101": (SmsProviderError, "Sağlayıcı sistem hatası"),
    "102": (SmsProviderError, "Sağlayıcı sistem hatası"),
    "103": (SmsProviderError, "Sağlayıcı sistem hatası"),
    "110": (SmsProviderError, "Sağlayıcı sistem hatası"),
}


def raise_for_code(code: str | None, *, context: str) -> None:
    """Yanıt gövdesindeki kodu denetler, hatalıysa tipli istisna atar.

    SDK bunu yapmaz: yalnızca HTTP durumuna bakar, gövdedeki ``code`` alanını
    başarı yolunda hiç okumaz. Bu yüzden denetim burada yapılır — yoksa
    gönderilmemiş bir SMS başarılı sayılır.

    ``code`` yoksa (None) sessiz geçilir: bazı uç noktalar kod döndürmez.
    """
    if code is None or str(code) == SUCCESS:
        return

    code = str(code)
    exc_type, description = ERROR_MAP.get(
        code, (SmsProviderError, "Tanımsız sağlayıcı hata kodu")
    )
    raise exc_type(f"{context}: {description}", provider_code=code)


# ----------------------------------------------------------- tarih biçimleri
# SDK üç farklı biçim bekliyor. Elle string üretmek hata kaynağı olduğu için
# yalnızca datetime kabul edilir, biçimlendirme uç noktaya göre burada yapılır.

#: Netgsm tarih alanlarında saat dilimi taşımaz ve değerleri Türkiye yerel
#: saatiyle yorumlar. Saat dilimi bilgisi taşıyan bir datetime olduğu gibi
#: biçimlendirilirse gönderim saatleri kayar — UTC verilen bir zamanlama
#: yazın 3 saat erken çalışır. Bu yüzden aware datetime önce çevrilir.
PROVIDER_TZ = ZoneInfo("Europe/Istanbul")


def _to_provider_tz(dt: datetime) -> datetime:
    """Saat dilimli datetime'ı sağlayıcının yerel saatine çevirir.

    Naive datetime zaten yerel kabul edilir ve dokunulmaz.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(PROVIDER_TZ)


def fmt_send(dt: datetime) -> str:
    """send() için: ddMMyyyyHHmm — ör. 011220231430"""
    return _to_provider_tz(dt).strftime("%d%m%Y%H%M")


def fmt_report(dt: datetime) -> str:
    """get_report() için: dd.MM.yyyy HH:mm:ss — ör. 01.12.2023 14:30:00"""
    return _to_provider_tz(dt).strftime("%d.%m.%Y %H:%M:%S")


def fmt_inbox(dt: datetime) -> str:
    """get_inbox() için: ddMMyyyyHHmmss — ör. 01122023143000"""
    return _to_provider_tz(dt).strftime("%d%m%Y%H%M%S")


# ------------------------------------------------------------------- İYS

class IysFilter:
    """İleti Yönetim Sistemi filtresi.

    Kurum personeline giden işletimsel uyarılar bilgilendirme içeriklidir →
    ``INFORMATIONAL``. Ticari içerik gönderilecekse İYS onay yönetimi gerekir.
    """

    INFORMATIONAL = "0"
    INDIVIDUAL = "11"
    MERCHANT = "12"


# --------------------------------------------------------- teslim durumu

#: Netgsm rapor durum kodu → sağlayıcıdan bağımsız durum.
STATUS_MAP: dict[int, DeliveryStatus] = {
    0: DeliveryStatus.NOT_DELIVERED,
    1: DeliveryStatus.DELIVERED,
    2: DeliveryStatus.PENDING,
    3: DeliveryStatus.TIMEOUT,
    4: DeliveryStatus.ERROR,
    11: DeliveryStatus.UNREACHABLE,
    12: DeliveryStatus.PROVIDER_ERROR,
    13: DeliveryStatus.PROVIDER_ERROR,
    100: DeliveryStatus.PROVIDER_ERROR,
    101: DeliveryStatus.PROVIDER_ERROR,
    102: DeliveryStatus.PROVIDER_ERROR,
    103: DeliveryStatus.PROVIDER_ERROR,
}


def to_status(raw: object) -> DeliveryStatus:
    """Rapor durum kodunu çevirir; tanımsızsa UNKNOWN döner."""
    try:
        return STATUS_MAP.get(int(str(raw)), DeliveryStatus.UNKNOWN)
    except (TypeError, ValueError):
        return DeliveryStatus.UNKNOWN
