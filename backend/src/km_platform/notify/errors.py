"""Bildirim hataları — sağlayıcıdan bağımsız.

Netgsm'e özgü kodlar bu tiplere eşlenir (providers/netgsm/codes.py). Üst
katmanlar sağlayıcı kodu değil, bu tipleri görür; sağlayıcı değişirse
çağıran taraf değişmez.
"""

from __future__ import annotations


class NotifyError(Exception):
    """Tüm bildirim hatalarının kökü."""

    def __init__(self, message: str, *, provider_code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider_code = provider_code

    def __str__(self) -> str:
        if self.provider_code:
            return f"[{self.provider_code}] {self.message}"
        return self.message


class SmsError(NotifyError):
    """SMS kanalına ait hataların kökü."""


class SmsConfigError(SmsError):
    """Ayar eksik veya tutarsız — istek sağlayıcıya hiç gitmedi.

    Örnek: gönderici başlığı tanımlanmamış, kimlik bilgisi kasada yok.
    """


class SmsInvalidRecipient(SmsError):
    """Telefon numarası geçersiz — sağlayıcıya gönderilmeden reddedildi.

    Numara normalleştirilemedi veya Türk cep numarası biçimine uymuyor.
    """

    def __init__(self, message: str, *, raw: str) -> None:
        super().__init__(message)
        self.raw = raw


class SmsAuthError(SmsError):
    """Kimlik doğrulama başarısız (kod 30 / HTTP 401).

    Kullanıcı adı-parola hatalı, API erişimi kapalı veya istek yetkisiz bir
    IP'den geldi. Yeniden denemek işe yaramaz.
    """


class SmsRejected(SmsError):
    """Sağlayıcı isteği reddetti (kod 20, 40, 50, 51, 70).

    Mesaj metni, gönderici başlığı veya parametre hatalı. Aynı istek tekrar
    denenirse yine reddedilir — düzeltmeden yeniden gönderilmez.
    """


class SmsRateLimited(SmsError):
    """Gönderim sınırı aşıldı (kod 80, 85).

    85: aynı numaraya 1 dakika içinde 20'den fazla görev açılamaz.
    Beklenip yeniden denenebilir.
    """


class SmsProviderError(SmsError):
    """Sağlayıcı tarafında sistem hatası (kod 100, 101 / HTTP 5xx).

    Geçici olabilir; yeniden deneme mantıklıdır.
    """


class SmsTransportError(SmsError):
    """Ağ katmanı hatası — zaman aşımı veya bağlantı kurulamadı.

    İsteğin sağlayıcıya ulaşıp ulaşmadığı **bilinmez**. Yeniden denemeden önce
    rapor sorgusuyla doğrulanmalıdır; aksi halde çift gönderim riski vardır.
    """
