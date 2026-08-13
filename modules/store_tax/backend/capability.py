"""`store.tax.rates` — vergi oranlarının tek doğru kaynağı.

NEDEN YETENEK. Ürünler, Fatura ve Siparişler ekranları da "bu ürün yüzde kaç
KDV'li" sorusunu soruyor. Üçü de geçide ayrı ayrı gidip oran + vergi kategorisi
ilişkisini kendi kurarsa aynı mantık dört yerde yaşar ve biri düzelirken
diğerleri eski davranışta kalır. İlişkiyi kuran tek yer bu modül; diğerleri
buradan okur (K3: modül modülü import etmez, registry üzerinden geçer).

SALT OKUNURDUR. Yetenek üzerinden oran YAZILAMAZ: yazma gerekçe, geçerlilik
tarihi ve denetim izi ister; onlar ekranın işidir ve HTTP ucundan geçer.

HATA FIRLATMAZ. Tüketen ekranın vergi yüzünden düşmesi kabul edilemez (K7):
mağaza erişilemezse `connected: False` ve boş liste döner, çağıran ekran
"KDV oranı okunamadı" yazıp çalışmaya devam eder.
"""

from __future__ import annotations

import time
from typing import Any


class TaxRates:
    """`store.tax.rates` yeteneğinin uygulaması."""

    def __init__(self, service: Any, log: Any, *, ttl: int = 60) -> None:
        self._service = service
        self._log = log
        # Kısa ömürlü önbellek: dört ekran aynı anda açılınca dört kez oran
        # listesi çekmek dakikada 60 isteklik payı boşa harcıyor. 60 saniye,
        # oran değişikliğinin ekrana yansımasını geciktirmeyecek kadar kısa.
        self._ttl = max(0, int(ttl))
        self._cached: dict[str, Any] | None = None
        self._at = 0.0

    def forget(self) -> None:
        """Önbelleği düşürür. Oran/kategori yazan uç bunu çağırır."""
        self._cached = None
        self._at = 0.0

    async def table(self) -> dict[str, Any]:
        """Oranlar + vergi kategorileri + kategori→oran eşlemesi."""
        now = time.monotonic()
        if self._cached is not None and now - self._at < self._ttl:
            return self._cached
        try:
            result = await self._service.rate_table()
        except Exception as failure:  # noqa: BLE001 — tüketen ekran düşmemeli (K7)
            self._log.warning("vergi oranları yeteneği okunamadı", error=str(failure))
            return {"connected": False, "error": str(failure), "rates": [],
                    "categories": [], "byCategoryId": {}, "membershipKnown": False}
        self._cached = result
        self._at = now
        return result

    async def rates(self) -> list[dict[str, Any]]:
        """Tanımlı oranların listesi. Mağaza erişilemezse BOŞ liste."""
        return (await self.table())["rates"]

    async def for_category(self, tax_category_id: int) -> dict[str, Any] | None:
        """Bir vergi kategorisinin oran künyesi; yoksa `None`.

        `None` "KDV'siz" DEMEK DEĞİLDİR: kategori bilinmiyor demektir. Çağıran
        ekran sıfır varsaymak yerine "oran okunamadı" yazmalıdır.
        """
        table = await self.table()
        return table["byCategoryId"].get(int(tax_category_id or 0))
