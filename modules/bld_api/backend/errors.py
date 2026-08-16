"""Geçit hatası ve sır maskeleme.

Kontrol imzasının sırrı (`server.bld.control_secret`) hiçbir koşulda dışarı
sızmamalıdır: ne log'a, ne hata metnine, ne de ekrana dönen yanıta. Laravel
doğrulama hatalarında isteğin gövdesini yanıtta tekrar edebiliyor; o gövdede
sır taşıyan bir alan varsa maskesiz hata mesajı doğrudan ekrana düşerdi. Bu
yüzden `BldApiError` kendi metnini kurucusunda maskeler — çağıranın
maskelemeyi hatırlamasına GEREK KALMAZ.

İKİ KATMANLI MASKELEME. Ad tabanlı maskeleme (`secret: ...`) tek başına
yetmez: imza sırrı rastgele bir dizedir ve sunucu onu alan adı olmadan,
çıplak hâlde yankılayabilir. Bu yüzden istemci ayrıca YÜKLENMİŞ sır
değerinin kendisini metinden siler (`BldApi._scrub`). Burası deseni,
orası bilinen değeri yakalar.
"""

from __future__ import annotations

import re
from typing import Any

#: Değeri hiçbir yere yazılmayacak alan adları. Ad İÇİNDE geçmesi yeter:
#: `control_secret`, `BLD_CONTROL_SECRET`, `X-Api-Key` hepsi yakalanır.
#:
#: TUZAK: buraya kısa parça eklenmez. "pin" eklenseydi "shipping" içinde
#: geçtiği için masum alanlar da maskelenirdi. Parça, masum bir alan adının
#: içinde geçmeyecek kadar uzun olmalı.
SENSITIVE = ("token", "password", "passwd", "secret", "authorization", "apikey", "api_key",
             "bearer", "credential", "private_key")

MASK = "***"

#: "secret": "abc" · secret=abc · 'apiKey' => 'abc' biçimlerinin hepsini yakalar.
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
    ve denetim ekranında görüntüleniyor.
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


class BldApiError(RuntimeError):
    """BLD kontrol API'si beklenmedik yanıt verdi ya da geçit isteği durdurdu.

    Alanlar:
      `status`  — HTTP kodu; geçit isteği hiç göndermediyse None.
      `code`    — makine tarafından ayrıştırılabilir neden. Ekran buna göre
                  farklı metin gösterebilir:
                  config_missing · read_only · reason_required · actor_required ·
                  payload · unauthorized · forbidden · not_found ·
                  control_endpoint_missing · validation · conflict ·
                  rate_limited · transport · server · http

                  `not_found` ile `control_endpoint_missing` AYRI şeylerdir:
                  ilki "uç var, kayıt yok", ikincisi "uç henüz sunucuda
                  yayında değil, bekle" demektir. Ayrımı yanıtın zarfı
                  kanıtlar (bkz. `BldApi._fail`) — tahmin değil, ölçüm.
      `message` — kullanıcıya gösterilebilir Türkçe metin (maskelenmiş).
    """

    def __init__(self, message: str, *, status: int | None = None, code: str = "http") -> None:
        safe = mask_text(message)
        super().__init__(safe)
        self.message = safe
        self.status = status
        self.code = code

    def __str__(self) -> str:
        return self.message
