"""SMS uzunluk ve segment hesabı — GSM-03.38 / UCS-2.

NEDEN ÖNEMLİ: Türkçe metin sessizce pahalıya patlar. `ğ ı İ ş Ş ç` GSM-7
alfabesinde YOKTUR; bir tanesi bile geçse mesaj UCS-2'ye düşer ve segment
**160 değil 70 karakter** olur. 150 karakterlik bir duyuru tek "ş" yüzünden
1 kredi yerine 3 krediye mal olur. 500 veliye gönderildiğinde fark 1000 SMS'tir.

Ekran bunu göndermeden önce yazar; "sadeleştir" seçeneği tek tıkla GSM-7'ye çeker.

Kaynak: GSM 03.38 temel tablo + genişletme tablosu (ESC ile, 2 karakter sayılır).
"""

from __future__ import annotations

#: GSM-7 temel alfabe. Türkçe'den yalnız `ö Ö ü Ü Ç é à` buradadır —
#: `ç` KÜÇÜK hâli tabloda yoktur, `ğ ı İ ş Ş` hiç yoktur.
GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

#: Genişletme tablosu — gönderimde ESC öneki alır, **2 karakter** sayılır.
GSM7_EXTENDED = set("^{}\\[~]|€\f")

#: Tek parça sınırları.
GSM7_SINGLE, GSM7_MULTI = 160, 153
UCS2_SINGLE, UCS2_MULTI = 70, 67

#: "Sadeleştir" eşlemesi — Türkçe özel harfleri ASCII karşılığına çeker.
#: `ö ü` GSM-7'de olsa da eşlemeye dahil: karışık kullanım kafa karıştırıyor,
#: sadeleştirme istendiğinde metnin tamamı ASCII olsun.
SIMPLIFY = str.maketrans({
    "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I", "ş": "s", "Ş": "S",
    "ç": "c", "Ç": "C", "ö": "o", "Ö": "O", "ü": "u", "Ü": "U",
    "â": "a", "Â": "A", "î": "i", "Î": "I", "û": "u", "Û": "U",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...",
})


def is_gsm7(text: str) -> bool:
    """Metin GSM-7 ile kodlanabiliyor mu?"""
    return all(char in GSM7_BASIC or char in GSM7_EXTENDED for char in text)


def simplify(text: str) -> str:
    """Türkçe özel harfleri ASCII'ye çeker — segment maliyetini düşürür."""
    return text.translate(SIMPLIFY)


def offending(text: str) -> list[str]:
    """UCS-2'ye düşüren karakterler (tekil, sırayla) — ekran hangisi olduğunu yazsın."""
    seen: list[str] = []
    for char in text:
        if char not in GSM7_BASIC and char not in GSM7_EXTENDED and char not in seen:
            seen.append(char)
    return seen


def measure(text: str) -> dict[str, object]:
    """Karakter seti, kullanılan uzunluk, segment sayısı ve kalan karakter.

    GSM-7'de genişletme karakterleri (`{ } [ ] ~ ^ \\ | €`) İKİ birim sayılır;
    "uzunluk" bu yüzden ham karakter sayısından farklı olabilir.
    """
    gsm7 = is_gsm7(text)

    if gsm7:
        units = sum(2 if char in GSM7_EXTENDED else 1 for char in text)
        single, multi = GSM7_SINGLE, GSM7_MULTI
    else:
        # UCS-2'de BMP dışı karakterler (emoji) iki birimdir.
        units = sum(2 if ord(char) > 0xFFFF else 1 for char in text)
        single, multi = UCS2_SINGLE, UCS2_MULTI

    if units == 0:
        segments, capacity = 0, single
    elif units <= single:
        segments, capacity = 1, single
    else:
        segments = -(-units // multi)      # yukarı yuvarlama
        capacity = segments * multi

    return {
        "encoding": "GSM-7" if gsm7 else "UCS-2 (Türkçe)",
        "gsm7": gsm7,
        "chars": len(text),
        "units": units,
        "segments": segments,
        "perSegment": single if segments <= 1 else multi,
        "remaining": max(0, capacity - units),
        "offending": offending(text)[:8],
        # Sadeleştirilse kaç segment olurdu — ekran kazancı gösterebilsin.
        "simplifiedSegments": measure(simplify(text))["segments"] if not gsm7 else segments,
    }
