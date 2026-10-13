"""Küresel öğrenci araması — kabuğun üst çubuğundaki "Ara" kutusu.

KULLANICI İSTEĞİ (13.10.2026): "Adam bir öğrenciyi güncellemek için saatlerce
uğraşıp durmasın." Her ekrandan `/` ile açılan arama; ad, okul numarası, veli
ya da öğrenci telefonu, kantin kartı (öğrenci kimliği ya da kartın QR metni)
ile öğrenciyi bulur, tek tıkla tek kartını açar.

KAYNAK DİZİNDİR (`directory.py`): kantin listesi + profil, `CACHE_SECONDS`
boyunca bellekte. Arama ne kantine ne veritabanına gider (sıcak yol); dizin
soğuksa tek kantin isteği. Bu dosya saf işlevdir (DOM'suz, G/Ç'siz) — sıralama
kuralı burada sınanır.

SIRALAMA (küçük puan önce):
    0  kimlik / okul no / tam telefon BİREBİR
    1  ad soyad aramayla başlar
    2  bir kelimesi aramayla başlar (soyad, ikinci ad)
    3  adın içinde geçer
    4  okul no / telefon / sınıf aramayla başlar ya da içerir
Eşitlikte etkin öğrenci pasiften, sonra ad (Türkçe) önce gelir.

KİŞİSEL VERİ: sonuç kartı açmaya yetecek kadar alan taşır (ad, sınıf, okul no,
eşleşen alanın adı). Telefon sonuçta MASKELİ döner (son dört hane); tam
numara kartta, izinle görülür.
"""

from __future__ import annotations

import re
from typing import Any

#: Bir aramada dönen en çok sonuç (ekranda tek bakış).
DEFAULT_LIMIT = 8
MAX_LIMIT = 25

_DIGITS = re.compile(r"\D+")
_SPACES = re.compile(r"\s+")

_FOLD = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                       "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
                       "â": "a", "î": "i", "û": "u"})


def fold(text: Any) -> str:
    """Türkçe harfleri sadeleştirir, küçültür, boşlukları teke indirir:
    "Çağrı  ÖZTÜRK" → "cagri ozturk". Kullanıcı "ozturk" yazsa da bulur."""
    raw = str(text or "").translate(_FOLD).lower()
    return _SPACES.sub(" ", raw).strip()


def _phone_tail(value: str) -> str:
    digits = _DIGITS.sub("", value)
    if digits.startswith("0090"):
        digits = digits[4:]
    elif digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return digits


def mask_phone(phone: str | None) -> str:
    """`5321234567` → `•••• •• 45 67` (son dört hane)."""
    digits = _DIGITS.sub("", str(phone or ""))
    if len(digits) < 4:
        return ""
    return f"•••• •• {digits[-4:-2]} {digits[-2:]}"


def _score(record: dict[str, Any], needle: str, digits: str) -> tuple[int, str] | None:
    """(puan, eşleşen alan) ya da None. `needle` katlanmış metin, `digits` yalnız rakam."""
    ident = str(record.get("id") or "")
    school = str(record.get("schoolNo") or "")
    name = fold(record.get("displayName"))
    if not name:
        name = fold(f"{record.get('firstName') or ''} {record.get('lastName') or ''}")
    phones = [str(row.get("phone") or "") for row in record.get("guardians") or []]
    student_phone = str(record.get("studentPhone") or "")

    if needle and (fold(ident) == needle or (school and fold(school) == needle)):
        return 0, "card" if fold(ident) == needle else "schoolNo"
    if len(digits) == 10 and (digits in phones or digits == student_phone):
        return 0, "guardianPhone" if digits in phones else "studentPhone"

    if needle and name:
        if name.startswith(needle):
            return 1, "name"
        if any(word.startswith(needle) for word in name.split(" ")[1:]):
            return 2, "name"
        if len(needle) >= 2 and needle in name:
            return 3, "name"

    if needle and fold(ident).startswith(needle) and len(needle) >= 4:
        return 4, "card"
    if school and needle and fold(school).startswith(needle):
        return 4, "schoolNo"
    # Telefon parçası: en az 3 rakam (ör. son dört hane "4567").
    if len(digits) >= 3:
        for phone in phones:
            if phone and digits in phone:
                return 4, "guardianPhone"
        if student_phone and digits in student_phone:
            return 4, "studentPhone"
    klass = fold(record.get("className"))
    if needle and klass and klass.replace(" ", "") == needle.replace(" ", ""):
        return 4, "className"
    return None


def search(records: list[dict[str, Any]], query: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Dizin kayıtlarında arar; en iyi `limit` sonucu kart açmaya yetecek alanlarla döner."""
    text = str(query or "").strip()
    if not text:
        return []
    needle = fold(text)
    digits = _phone_tail(text) if _DIGITS.sub("", text) and not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]", text) else ""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))

    found: list[tuple[tuple[int, int, str], dict[str, Any], str]] = []
    for record in records:
        hit = _score(record, needle, digits)
        if hit is None:
            continue
        score, field = hit
        inactive = 1 if record.get("status") == "inactive" else 0
        found.append(((score, inactive, fold(record.get("displayName"))), record, field))
    found.sort(key=lambda item: item[0])

    out: list[dict[str, Any]] = []
    for _, record, field in found[:limit]:
        guardians = record.get("guardians") or []
        primary = guardians[0] if guardians else {}
        out.append({
            "id": str(record.get("id") or ""),
            "displayName": str(record.get("displayName") or "") or "Adsız öğrenci",
            "className": str(record.get("className") or ""),
            "schoolNo": str(record.get("schoolNo") or ""),
            "status": record.get("status") or "active",
            "match": field,
            "guardian": {"name": str(primary.get("name") or ""),
                         "phone": mask_phone(primary.get("phone"))} if primary else None,
        })
    return out


def looks_like_card(text: str) -> bool:
    """Kart QR metni mi (base64url, ≥ 40 karakter, boşluksuz)? Okuyucu klavye gibi yazar."""
    value = str(text or "").strip()
    return len(value) >= 40 and re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None
