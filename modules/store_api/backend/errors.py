"""Geçit hatası ve sır maskeleme.

Mağaza belirteci hiçbir koşulda dışarı sızmamalıdır: ne log'a, ne hata
metnine, ne de ekrana dönen yanıta. Bagisto doğrulama hatalarında isteğin
gövdesini yanıtta tekrar edebiliyor; o gövdede parola/anahtar alanı varsa
maskesiz hata mesajı doğrudan ekrana düşerdi. Bu yüzden `StoreApiError`
kendi metnini kurucusunda maskeler — çağıranın maskelemeyi hatırlamasına
GEREK KALMAZ.
"""

from __future__ import annotations

import re
from typing import Any

#: Değeri hiçbir yere yazılmayacak alan adları. Ad İÇİNDE geçmesi yeter:
#: `adminToken`, `netgsm_password`, `X-Api-Key` hepsi yakalanır.
#:
#: TUZAK: buraya kısa parça eklenmez. "pin" eklenmişti; "shipping" içinde
#: geçtiği için kargo alanlarının tamamı maskeleniyordu. Parça, masum bir
#: alan adının içinde geçmeyecek kadar uzun olmalı.
SENSITIVE = ("token", "password", "passwd", "secret", "authorization", "apikey", "api_key",
             "bearer", "credential", "private_key")

MASK = "***"

#: "token": "abc" · token=abc · 'apiKey' => 'abc' biçimlerinin hepsini yakalar.
_PAIR = re.compile(
    r"(?i)(['\"]?[a-z0-9_.\-]*(?:" + "|".join(SENSITIVE) + r")[a-z0-9_.\-]*['\"]?"
    r"\s*(?::|=>|=)\s*)(['\"]?)([^'\"\s,;}\]]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9|._~+/=\-]+")


def mask_text(value: Any) -> str:
    """Metindeki sır değerlerini yıldızlar. Anahtar adı KALIR, değer gider —
    "hangi alan yüzünden patladı" bilgisi teşhis için gereklidir."""
    text = str(value)
    text = _BEARER.sub("Bearer " + MASK, text)
    return _PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)


def mask_mapping(data: Any) -> Any:
    """Sözlüğün sırlı alanlarını maskeler; iç içe sözlük ve listelere iner.

    Denetim izine yazılan istek gövdesi bundan geçer: gövde diske yazılıyor
    ve UDİT ekranında görüntüleniyor.
    """
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        for key, value in data.items():
            lowered = str(key).lower()
            if any(word in lowered for word in SENSITIVE):
                masked[key] = MASK
            else:
                masked[key] = mask_mapping(value)
        return masked
    if isinstance(data, list):
        return [mask_mapping(item) for item in data]
    if isinstance(data, str):
        return mask_text(data)
    return data


class StoreApiError(RuntimeError):
    """Mağaza admin API'si beklenmedik yanıt verdi ya da geçit isteği durdurdu.

    Alanlar:
      `status`  — HTTP kodu; geçit isteği hiç göndermediyse None.
      `code`    — makine tarafından ayrıştırılabilir neden. Ekran buna göre
                  farklı metin gösterebilir:
                  config_missing · read_only · reason_required · unauthorized ·
                  forbidden · not_found · conflict · bbd_endpoint_missing ·
                  bbd_endpoint_by_design · validation · rate_limited ·
                  transport · server · http · payload · redirect ·
                  empty_response · not_json

                  `bbd_endpoint_missing` ile `bbd_endpoint_by_design` AYRI
                  şeylerdir: ilki "uç henüz yayında değil, bekle", ikincisi
                  "bu iş bilerek panelden yapılmıyor, bekleme" demektir.

                  SON ÜÇÜ "2xx/3xx GELDİ AMA VERİ GELMEDİ" AİLESİDİR ve
                  birbirinden ayrı tutulur; üçü de eskiden sessizce boş
                  listeye ya da ham `JSONDecodeError`a düşüyordu (K7):
                    `redirect`       — yanıt 3xx: istek admin API'sine değil
                                       başka bir adrese (çoğunlukla oturum
                                       açma sayfasına) düştü.
                    `empty_response` — 2xx ama gövde BOŞ. "Kayıt yok" DEĞİLDİR;
                                       kayıt yoksa `data` boş dizi gelir.
                    `not_json`       — 2xx ama gövde JSON değil (HTML hata
                                       sayfası, düz metin, bozuk gövde).
      `message` — kullanıcıya gösterilebilir Türkçe metin (maskelenmiş).
      `details` — BBD hata zarfının (`{"error": {"code","message","details"}}`)
                  `details` bloğu; başka kaynaklarda BOŞ sözlüktür.

                  NEDEN GEREKLİ: bazı retler mesajın kendisinden fazlasını
                  taşır. Geliver canlıya geçiş reddi (`GELIVER_PRECHECK_FAILED`)
                  engelleri kod+metin listesi olarak `details.blockers` içinde
                  verir; taşıyıcı ayrışması (`CARRIER_MISMATCH`) beklenen ve
                  bulunan firmayı orada söyler. Bunlar mesaja gömülmediği için
                  `details` düşerse ekran "neden olmadı" sorusunu ancak
                  tahminle yanıtlayabilirdi.

                  MASKEDEN GEÇER (`mask_mapping`): gövde uzak sistemden gelir
                  ve ne taşıyacağını biz belirlemiyoruz.
    """

    def __init__(self, message: str, *, status: int | None = None, code: str = "http",
                 details: Any = None) -> None:
        safe = mask_text(message)
        super().__init__(safe)
        self.message = safe
        self.status = status
        self.code = code
        masked = mask_mapping(details) if isinstance(details, dict) else None
        self.details: dict[str, Any] = masked if isinstance(masked, dict) else {}

    def __str__(self) -> str:
        return self.message
