"""SMS metin ve numara yardımcıları — sağlayıcıdan bağımsız.

İki iş yapar:
  1. Serbest biçimde girilmiş telefon numarasını Netgsm'in beklediği
     10 haneli biçime indirger ve geçersizi erkenden reddeder.
  2. Metnin hangi kodlamayla gideceğini ve kaç SMS parçasına böleceğini
     hesaplar — maliyet önizlemesi için.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------- numara

_NON_DIGIT = re.compile(r"\D")

#: Türkiye cep numarası: 10 hane, 5 ile başlar (5XXXXXXXXX).
_MSISDN = re.compile(r"^5\d{9}$")


def normalize_msisdn(raw: str) -> str:
    """Serbest biçimli numarayı Netgsm biçimine (5XXXXXXXXX) çevirir.

    Kabul edilen girdiler: '0532 123 45 67', '+90 532 123 45 67',
    '90-532-123-45-67', '(0532) 1234567', '5321234567'.

    Raises:
        ValueError: numara Türk cep numarası biçimine uymuyorsa.
    """
    digits = _NON_DIGIT.sub("", raw or "")

    # Ülke kodu ve baştaki sıfırı at. Sıra önemli: önce +90/90, sonra 0.
    if digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]

    if not _MSISDN.match(digits):
        raise ValueError(
            f"Geçersiz cep numarası: {raw!r} → {digits!r}. "
            "Beklenen biçim 5XXXXXXXXX (10 hane, 5 ile başlar)."
        )
    return digits


# ------------------------------------------------------------- kodlama

#: GSM-7 temel karakter kümesi (3GPP TS 23.038). Her karakter 1 septet.
_GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

#: GSM-7 genişletme tablosu — escape ile gönderilir, 2 septet yer kaplar.
_GSM7_EXTENDED = set("^{}\\[~]|€")

#: Türkçe tekli kaydırma tablosundaki karakterler — 2 septet.
#: Bunlardan biri varsa Netgsm'e encoding="tr" gönderilir.
_TURKISH_SHIFT = set("ğĞıİşŞ")

# Not: ç Ç ö Ö ü Ü zaten GSM-7 temel kümesinde ya da yakın eşleniğiyle
# taşınır; encoding'i tetikleyen yalnızca yukarıdaki altı karakterdir.

GSM7_SINGLE = 160
GSM7_MULTI = 153
UCS2_SINGLE = 70
UCS2_MULTI = 67


@dataclass(frozen=True)
class TextPlan:
    """Bir SMS metninin nasıl gideceği.

    Attributes:
        encoding: Netgsm'e gönderilecek ``encoding`` değeri. ``None`` ise
            parametre hiç gönderilmez (SDK notu: Türkçe karakter yoksa
            gönderilmemeli).
        parts: Tahmini SMS parça sayısı. Kesin sayı sağlayıcı raporundan gelir.
        units: Kodlama birimi cinsinden uzunluk (septet veya UCS-2 karakteri).
        unicode: UCS-2'ye düşüldü mü.
    """

    encoding: str | None
    parts: int
    units: int
    unicode: bool

    @property
    def capacity(self) -> int:
        """Mevcut parça sayısına sığan toplam birim."""
        if self.parts == 0:
            return GSM7_SINGLE
        if self.unicode:
            return UCS2_SINGLE if self.parts == 1 else UCS2_MULTI * self.parts
        return GSM7_SINGLE if self.parts == 1 else GSM7_MULTI * self.parts

    @property
    def remaining(self) -> int:
        """Bir sonraki parçaya geçmeden kaç birim daha yazılabilir.

        Ekran bunu canlı gösterir: "14 karakter daha yazarsan 2. SMS başlar"
        bilgisi, gönderdikten sonra iki kredi ödemekten iyidir.
        """
        return max(0, self.capacity - self.units)


def plan_text(text: str) -> TextPlan:
    """Metnin kodlamasını ve parça sayısını hesaplar.

    Parça sayısı **tahmindir** ve maliyet önizlemesi içindir; faturalanan
    kesin sayı Netgsm raporundan okunur.
    """
    if not text:
        return TextPlan(encoding=None, parts=0, units=0, unicode=False)

    units = 0
    has_turkish = False
    gsm7_ok = True

    for ch in text:
        if ch in _GSM7_BASIC:
            units += 1
        elif ch in _GSM7_EXTENDED:
            units += 2
        elif ch in _TURKISH_SHIFT:
            units += 2
            has_turkish = True
        else:
            gsm7_ok = False
            break

    if not gsm7_ok:
        # Emoji vb. — GSM-7'ye sığmıyor, UCS-2'ye düşülür.
        units = len(text)
        single, multi = UCS2_SINGLE, UCS2_MULTI
        parts = 1 if units <= single else -(-units // multi)
        return TextPlan(encoding=None, parts=parts, units=units, unicode=True)

    single, multi = GSM7_SINGLE, GSM7_MULTI
    parts = 1 if units <= single else -(-units // multi)
    return TextPlan(
        encoding="tr" if has_turkish else None,
        parts=parts,
        units=units,
        unicode=False,
    )


# --------------------------------------------------------- sadeleştirme

#: Türkçe harflerin ASCII karşılıkları. 'İ' → 'I' ve 'ı' → 'i' bilinçlidir:
#: okunabilirlik, harf birebirliğinden önemlidir.
_SIMPLIFY = str.maketrans({
    "ğ": "g", "Ğ": "G",
    "ı": "i", "İ": "I",
    "ş": "s", "Ş": "S",
    "ç": "c", "Ç": "C",
    "ö": "o", "Ö": "O",
    "ü": "u", "Ü": "U",
})


def offending(text: str) -> list[str]:
    """Metni pahalılaştıran karakterler — tekrarsız, göründükleri sırada.

    İki sınıf vardır ve ikisi de paraya dokunur:
      * Türkçe kaydırma tablosundaki harfler (2 septet yer kaplar),
      * GSM-7'ye hiç sığmayanlar (emoji vb. — tüm mesajı UCS-2'ye düşürür,
        160 karakterlik sınır 70'e iner).

    Ekran bunları kullanıcıya gösterir: "şu 4 karakter yüzünden 3 SMS gidiyor."
    """
    seen: dict[str, None] = {}
    for ch in text or "":
        if ch in _GSM7_BASIC or ch in _GSM7_EXTENDED:
            continue
        seen.setdefault(ch, None)
    return list(seen)


def simplify(text: str) -> str:
    """Türkçe harfleri ASCII karşılıklarına indirger.

    Tek amacı maliyet: 'Ödemeniz için' 3 SMS'e çıkarken 'Odemeniz icin' 1 SMS'e
    sığar. Metni otomatik değiştirmeyiz — kullanıcıya önerilir, o karar verir.
    """
    return (text or "").translate(_SIMPLIFY)
