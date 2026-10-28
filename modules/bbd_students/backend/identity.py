"""Sözleşme kimlik bilgileri — kural TEK YERDE (göç 014, iş 80-km-kimlik).

Kullanıcı (28.10.2026): "eksikleri de ekleriz öğrenciler panelinde düzenleriz",
"akademi ile km birebir senkron olsun". Kayıt sözleşmesi, MEB formu ve senet için
gereken kimlik / adres bilgileri öğrencinin KM ana kaydındadır; Akademi mobil
geçidin `identity` bloğuyla aynalar (bbd-akademi 80-ortak K2 şekli birebir).

ÖĞRENCİ: TC kimlik no, pasaport / yabancı kimlik no, doğum tarihi, cinsiyet,
kayıt tarihi, okul adı, ev telefonu, adres (açık adres + ilçe + il), anne adı,
baba adı. VELİ (yuva 1–4): yasal ad soyad (= MEVCUT veli ad alanı), TC, yabancı
kimlik, meslek, iş telefonu, e-posta, ev adresi, iş adresi; yakınlık ve cep
telefonu zaten velidedir.

KVKK.
  · TC ve yabancı kimlik no veritabanına ŞİFRELİ yazılır: AES-256-GCM, anahtar sır
    kasasında (`secrets` → `bbd_students.identity_key`, ilk kullanımda üretilir),
    AAD = öğrenci kimliği + alan (satırlar arası kopyalanan şifreli değer çözülmez).
    Son 4 hane ayrı tutulur: listede maske (`*******0146`) şifre çözmeden çizilir.
  · Tam değer YALNIZ tek öğrencinin düzenleme okumasında (`bbd_students.manage`) ve
    yetkili geçit anahtarının `identity` bloğunda döner. Log / hata / olay yüküne TC
    yazılmaz: hata metinleri değeri tekrar etmez, olay yalnız alan adını taşır
    (`student.updated` → `["identity"]`), denetim izi yalnız alan adlarını yazar.

VELİ KİMLİĞİ KİŞİYLE GİDER. `guardian_identity` JSON'u velinin CEP TELEFONUNA
göre tutulur (öğrencide aynı telefon iki velide olamaz): veli sırası / birincil
veli değişince kimlik kendiliğinden yeni yuvada okunur. Velinin telefonu
düzeltilince servis girişi yeni telefona taşır (`rekey_guardians`).
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .guardians import NAME_MAX, RELATIONS, extra_items, extra_text
from .phones import normalize_phone

#: Sır kasasındaki anahtarın adı (32 bayt, base64url).
KEY_NAME = "bbd_students.identity_key"
#: Şifreli değerin öneki — biçim değişirse sürüm artar, eski değer okunmaya devam eder.
SEALED = "v1:"

GENDERS: dict[str, str] = {"female": "Kadın", "male": "Erkek"}

FOREIGN_MAX = 20
ADDRESS_MAX = {"line": 250, "district": 60, "city": 60}
SCHOOL_MAX = 120
OCCUPATION_MAX = 80
EMAIL_MAX = 120

#: Öğrencinin düz metin alanları: API anahtarı → (sütun, en çok, etiket).
STUDENT_TEXT: dict[str, tuple[str, int, str]] = {
    "schoolName": ("school_name", SCHOOL_MAX, "Okul adı"),
    "motherName": ("mother_name", NAME_MAX, "Anne adı"),
    "fatherName": ("father_name", NAME_MAX, "Baba adı"),
}

#: Bu modülün yazdığı kimlik sütunları (olaydaki mantıksal alan: `identity`).
COLUMNS = (
    "national_id", "national_id_tail", "foreign_id", "foreign_id_tail", "birth_date",
    "gender", "registered_on", "school_name", "home_phone", "address_line",
    "address_district", "address_city", "mother_name", "father_name", "guardian_identity",
)

#: Senet / sözleşme için ZORUNLU olanların etiketleri (Akademi `MissingField` ile aynı).
LABELS = {
    ("student", "fullName"): "Öğrenci ad soyad",
    ("student", "nationalId"): "Öğrenci TC kimlik no",
    ("guardian", "guardian"): "Veli (ödeme sorumlusu)",
    ("guardian", "fullName"): "Veli ad soyad",
    ("guardian", "nationalId"): "Veli TC kimlik no",
    ("guardian", "homeAddress"): "Veli ev adresi",
    ("guardian", "phone"): "Veli cep telefonu",
}

_DIGITS = re.compile(r"[0-9]+")
_NON_DIGIT = re.compile(r"\D+")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_FOREIGN = re.compile(r"^[0-9A-Za-zÇĞİÖŞÜçğıöşü./ -]+$")


class IdentityError(ValueError):
    """Kimlik bilgisi reddedildi. `details` alan alan Türkçe hata; DEĞER TAŞIMAZ."""

    def __init__(self, message: str, *, details: list[dict[str, Any]] | None = None,
                 status: int = 422, code: str = "validation_error") -> None:
        super().__init__(message)
        self.details = list(details or [])
        self.status = status
        self.code = code


class CipherUnavailable(RuntimeError):
    """Sır kasası okunamadı — şifreli alan yazılamaz / çözülemez."""


# ================================================================== kurallar


def national_id_problem(value: str) -> str | None:
    """TC kimlik no kuralı: 11 hane, ilk hane ≠ 0, resmî sağlama (10. ve 11. hane)."""
    if not _DIGITS.fullmatch(value):
        return "TC kimlik no yalnız rakam olmalı."
    if len(value) != 11:
        return "TC kimlik no 11 hane olmalı."
    if value[0] == "0":
        return "TC kimlik no 0 ile başlayamaz."
    d = [int(char) for char in value]
    tenth = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    if tenth != d[9] or sum(d[:10]) % 10 != d[10]:
        return "TC kimlik no geçersiz — doğrulama haneleri tutmuyor."
    return None


def valid_national_id(value: Any) -> bool:
    return national_id_problem(str(value or "")) is None


def mask(tail: str, *, national: bool = True) -> str | None:
    """Son 4 hane görünür: TC `*******0146`, yabancı kimlik `****AB12`."""
    if not tail:
        return None
    return ("*" * 7 if national else "*" * 4) + tail


def any_phone(value: Any) -> str | None:
    """Ev / iş telefonu: sabit hat da olur. `XXXXXXXXXX` (10 hane, 2–5 ile başlar) ya da None."""
    digits = _NON_DIGIT.sub("", str(value or ""))
    if digits.startswith("0090"):
        digits = digits[4:]
    elif digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "2345":
        return digits
    return None


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if match:
        text = f"{match.group(3)}-{int(match.group(2)):02d}-{int(match.group(1)):02d}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("Tarih GG.AA.YYYY biçiminde olmalı.") from None


def _address(value: Any) -> dict[str, str]:
    if value is None:
        return {"line": "", "district": "", "city": ""}
    if not isinstance(value, dict):
        raise ValueError("Adres okunamadı.")  # noqa: TRY004 — alan hatası olarak toplanır
    return {part: _text(value.get(part)) for part in ("line", "district", "city")}


def address_out(line: Any, district: Any, city: Any) -> dict[str, str | None] | None:
    """Üç parça boşsa None (sözleşme: alan yoksa `null`)."""
    parts = {"line": str(line or "") or None, "district": str(district or "") or None,
             "city": str(city or "") or None}
    return parts if any(parts.values()) else None


def address_complete(value: dict[str, Any] | None) -> bool:
    """Senet / sözleşme için yeterli adres: açık adres + il (ilçe isteğe bağlı)."""
    return bool(value and str(value.get("line") or "").strip()
                and str(value.get("city") or "").strip())


# ================================================================== şifre


async def _settle(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class IdentityCipher:
    """AES-256-GCM; anahtar sır kasasında. Anahtar süreç boyunca bellekte tutulur.

    Kasa yoksa (yetenek yüklenmemiş) ya da okunamazsa `CipherUnavailable`: TC
    yazılmaz (düz metne DÜŞÜLMEZ), okumada tam değer yerine maske döner.
    """

    def __init__(self, vault: Callable[[], Any]) -> None:
        self._vault = vault
        self._key: bytes | None = None
        self._lock = asyncio.Lock()

    async def key(self) -> bytes:
        if self._key is not None:
            return self._key
        async with self._lock:
            if self._key is not None:
                return self._key
            vault = self._vault()
            if vault is None:
                raise CipherUnavailable("Kimlik anahtarı okunamadı: sır kasası yok.")
            try:
                stored = await _settle(vault.get(KEY_NAME))
                if not stored:
                    stored = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
                    await _settle(vault.set(KEY_NAME, stored))
                    # Yazılanı geri oku: kasada ne duruyorsa anahtar odur.
                    stored = await _settle(vault.get(KEY_NAME)) or stored
                key = base64.urlsafe_b64decode(str(stored).encode("ascii"))
            except CipherUnavailable:
                raise
            except Exception as failure:
                raise CipherUnavailable("Kimlik anahtarı okunamadı.") from failure
            if len(key) != 32:
                raise CipherUnavailable("Kimlik anahtarı bozuk.")
            self._key = key
            return key

    async def pair(self) -> tuple[Callable[[str, str, str], str], Callable[[str, str, str], str]]:
        """`(seal, unseal)` — anahtar bir kez alınır, işlevler senkrondur."""
        key = await self.key()
        aead = AESGCM(key)

        def aad(student: str, field: str) -> bytes:
            return f"bbd_students|{student}|{field}".encode()

        def seal(student: str, field: str, plain: str) -> str:
            if not plain:
                return ""
            nonce = os.urandom(12)
            sealed = aead.encrypt(nonce, plain.encode("utf-8"), aad(student, field))
            return SEALED + base64.urlsafe_b64encode(nonce + sealed).decode("ascii").rstrip("=")

        def unseal(student: str, field: str, token: str) -> str:
            if not token:
                return ""
            if not token.startswith(SEALED):
                return token  # şifresiz eski / elle girilmiş değer okunur kalsın
            body = token[len(SEALED):]
            raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
            try:
                return aead.decrypt(raw[:12], raw[12:], aad(student, field)).decode("utf-8")
            except InvalidTag:
                raise CipherUnavailable("Kimlik alanı çözülemedi.") from None

        return seal, unseal


Seal = Callable[[str, str, str], str]
Unseal = Callable[[str, str, str], str]


# ================================================================ veli kimliği


def guardian_map(profile: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """`guardian_identity` → {cep telefonu: giriş} (bozuksa boş — kayıt okunur kalsın)."""
    text = str((profile or {}).get("guardian_identity") or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(phone): entry for phone, entry in loaded.items()
            if isinstance(entry, dict) and normalize_phone(phone)}


def guardian_text(entries: dict[str, dict[str, Any]]) -> str:
    clean = {phone: entry for phone, entry in entries.items() if any(
        value for key, value in entry.items())}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")) \
        if clean else ""


def rekey_guardians(profile: dict[str, Any] | None, before: list[str],
                    after: list[str]) -> str | None:
    """Veli listesi yazılırken kimlik girişlerini yeni telefonlara uydurur.

    `before` / `after` sıralı cep telefonları. Telefonu korunan veli kimliğini
    korur; aynı SIRADA telefonu değişen (eski telefon listeden çıkmış) veli
    kimliğini yeni telefona taşır; listeden çıkan velinin kimliği SİLİNİR (KVKK:
    gereksiz kişisel veri tutulmaz). Değişiklik yoksa None.
    """
    entries = guardian_map(profile)
    if not entries:
        return None
    out: dict[str, dict[str, Any]] = {}
    for index, phone in enumerate(after):
        if phone in entries:
            out[phone] = entries[phone]
        elif index < len(before) and before[index] not in after and before[index] in entries:
            out[phone] = entries[before[index]]
    text = guardian_text(out)
    return None if text == (str((profile or {}).get("guardian_identity") or "")) else text


# ===================================================================== yazma


def _field_error(details: list[dict[str, Any]], scope: str, slot: int | None, field: str,
                 message: str) -> None:
    details.append({"scope": scope, "slot": slot, "field": field, "message": message})


def apply(kantin_id: str, profile: dict[str, Any] | None, guardians: list[dict[str, Any]],
          body: dict[str, Any], *, seal: Seal | None, unseal: Unseal | None,
          today: date | None = None) -> tuple[dict[str, Any], list[str]]:
    """Gövde → değişen sütunlar + değişen alan adları (denetim için; DEĞER YOK).

    `guardians`: öğrencinin ŞU ANKİ velileri `[{slot, phone (10 hane), name}]`.
    Gövdede yalnız VERİLEN anahtar değişir; `null` / boş metin temizler.
    Hata varsa hepsi birlikte `IdentityError` ile döner (alan alan).
    """
    profile = profile or {}
    today = today or datetime.now(UTC).date()
    details: list[dict[str, Any]] = []
    columns: dict[str, Any] = {}
    changed: list[str] = []

    def put(column: str, value: str, name: str) -> None:
        if str(profile.get(column) or "") != value:
            columns[column] = value
            changed.append(name)

    def sealed_put(column: str, tail_column: str, field: str, plain: str, name: str) -> None:
        current_token = str(profile.get(column) or "")
        if plain and current_token and unseal is not None:
            try:
                if unseal(kantin_id, field, current_token) == plain:
                    return
            except CipherUnavailable:
                pass
        if not plain and not current_token:
            return
        if plain and seal is None:
            raise CipherUnavailable("Kimlik anahtarı okunamadı.")
        columns[column] = seal(kantin_id, field, plain) if (plain and seal) else ""
        columns[tail_column] = plain[-4:] if plain else ""
        changed.append(name)

    student = body.get("student")
    if student is not None and not isinstance(student, dict):
        raise IdentityError("Öğrenci kimlik bilgisi okunamadı.")
    student = student or {}

    if "nationalId" in student:
        value = re.sub(r"\s+", "", str(student.get("nationalId") or ""))
        problem = national_id_problem(value) if value else None
        if problem:
            _field_error(details, "student", None, "nationalId", problem)
        else:
            sealed_put("national_id", "national_id_tail", "student.nationalId", value,
                       "student.nationalId")
    if "foreignId" in student:
        value = _text(student.get("foreignId")).upper()
        if len(value) > FOREIGN_MAX or (value and not _FOREIGN.fullmatch(value)):
            _field_error(details, "student", None, "foreignId",
                         f"Pasaport / yabancı kimlik no en çok {FOREIGN_MAX} harf ve rakam olmalı.")
        else:
            sealed_put("foreign_id", "foreign_id_tail", "student.foreignId", value,
                       "student.foreignId")
    if "birthDate" in student:
        try:
            day = _day(student.get("birthDate"))
            if day is not None and not (date(1900, 1, 1) <= day < today):
                raise ValueError("Doğum tarihi geçmişte olmalı.")
            put("birth_date", day.isoformat() if day else "", "student.birthDate")
        except ValueError as failure:
            _field_error(details, "student", None, "birthDate", str(failure))
    if "registeredOn" in student:
        try:
            day = _day(student.get("registeredOn"))
            if day is not None and not (date(2000, 1, 1) <= day <= today + timedelta(days=366)):
                raise ValueError("Kayıt tarihi geçerli bir gün olmalı.")
            put("registered_on", day.isoformat() if day else "", "student.registeredOn")
        except ValueError as failure:
            _field_error(details, "student", None, "registeredOn", str(failure))
    if "gender" in student:
        value = str(student.get("gender") or "").strip().lower()
        if value and value not in GENDERS:
            _field_error(details, "student", None, "gender", "Cinsiyet Kadın ya da Erkek olmalı.")
        else:
            put("gender", value, "student.gender")
    if "homePhone" in student:
        raw = str(student.get("homePhone") or "").strip()
        phone = any_phone(raw) if raw else ""
        if raw and phone is None:
            _field_error(details, "student", None, "homePhone",
                         "Ev telefonu 0xxx xxx xx xx biçiminde olmalı.")
        else:
            put("home_phone", phone or "", "student.homePhone")
    for key, (column, limit, label) in STUDENT_TEXT.items():
        if key in student:
            value = _text(student.get(key))
            if len(value) > limit:
                _field_error(details, "student", None, key, f"{label} en çok {limit} karakter.")
            else:
                put(column, value, f"student.{key}")
    if "address" in student:
        try:
            parts = _address(student.get("address"))
            for part, limit in ADDRESS_MAX.items():
                if len(parts[part]) > limit:
                    raise ValueError(f"Adres alanı en çok {limit} karakter.")
            before = (profile.get("address_line"), profile.get("address_district"),
                      profile.get("address_city"))
            if tuple(str(item or "") for item in before) != \
                    (parts["line"], parts["district"], parts["city"]):
                columns.update({"address_line": parts["line"],
                                "address_district": parts["district"],
                                "address_city": parts["city"]})
                changed.append("student.address")
        except ValueError as failure:
            _field_error(details, "student", None, "address", str(failure))

    people = body.get("guardians")
    if people is not None:
        if not isinstance(people, list):
            raise IdentityError("Veli kimlik bilgisi okunamadı.")
        _apply_guardians(kantin_id, profile, guardians, people, columns, changed, details,
                         seal=seal, unseal=unseal)

    if details:
        first = details[0]["message"]
        message = first if len(details) == 1 else f"{len(details)} alan düzeltilmeli: {first}"
        raise IdentityError(message, details=details)
    return columns, changed


def _apply_guardians(kantin_id: str, profile: dict[str, Any], guardians: list[dict[str, Any]],
                     people: list[Any], columns: dict[str, Any], changed: list[str],
                     details: list[dict[str, Any]], *, seal: Seal | None,
                     unseal: Unseal | None) -> None:
    by_slot = {int(row["slot"]): row for row in guardians if row.get("slot")}
    entries = {phone: dict(entry) for phone, entry in guardian_map(profile).items()}
    before_entries = json.dumps(entries, sort_keys=True)
    names: dict[int, str] = {}
    for raw in people:
        if not isinstance(raw, dict):
            raise IdentityError("Veli kimlik satırı okunamadı.")
        try:
            slot = int(str(raw.get("slot")))
        except (TypeError, ValueError):
            raise IdentityError("Veli yuvası okunamadı.") from None
        current = by_slot.get(slot)
        phone = normalize_phone((current or {}).get("phone"))
        if current is None or phone is None:
            raise IdentityError(f"{slot}. veli bulunamadı — önce veliyi telefonuyla ekleyin.",
                                status=409, code="guardians_changed")
        sent_phone = raw.get("phone")
        if sent_phone and normalize_phone(sent_phone) != phone:
            raise IdentityError("Veli listesi bu arada değişti; kartı yenileyip tekrar deneyin.",
                                status=409, code="guardians_changed")
        entry = entries.setdefault(phone, {})
        tag = f"guardian{slot}"
        if "fullName" in raw:
            name = _text(raw.get("fullName"))
            if len(name) > NAME_MAX:
                _field_error(details, "guardian", slot, "fullName",
                             f"{slot}. velinin adı en çok {NAME_MAX} karakter.")
            elif name != _text(current.get("name")):
                names[slot] = name
                changed.append(f"{tag}.fullName")
        for field in ("nationalId", "foreignId"):
            if field not in raw:
                continue
            if field == "nationalId":
                value = re.sub(r"\s+", "", str(raw.get(field) or ""))
                problem = national_id_problem(value) if value else None
            else:
                value = _text(raw.get(field)).upper()
                problem = (f"Pasaport / yabancı kimlik no en çok {FOREIGN_MAX} harf ve rakam olmalı."
                           if len(value) > FOREIGN_MAX or (value and not _FOREIGN.fullmatch(value))
                           else None)
            if problem:
                _field_error(details, "guardian", slot, field, f"{slot}. veli: {problem}")
                continue
            token = str(entry.get(field) or "")
            aad_field = f"guardian.{field}"
            same = False
            if value and token and unseal is not None:
                try:
                    same = unseal(kantin_id, aad_field, token) == value
                except CipherUnavailable:
                    same = False
            if same or (not value and not token):
                continue
            if value and seal is None:
                raise CipherUnavailable("Kimlik anahtarı okunamadı.")
            entry[field] = seal(kantin_id, aad_field, value) if (value and seal) else ""
            entry[f"{field}Tail"] = value[-4:] if value else ""
            changed.append(f"{tag}.{field}")
        if "occupation" in raw:
            value = _text(raw.get("occupation"))
            if len(value) > OCCUPATION_MAX:
                _field_error(details, "guardian", slot, "occupation",
                             f"{slot}. velinin mesleği en çok {OCCUPATION_MAX} karakter.")
            elif value != str(entry.get("occupation") or ""):
                entry["occupation"] = value
                changed.append(f"{tag}.occupation")
        if "workPhone" in raw:
            text = str(raw.get("workPhone") or "").strip()
            number = any_phone(text) if text else ""
            if text and number is None:
                _field_error(details, "guardian", slot, "workPhone",
                             f"{slot}. velinin iş telefonu 0xxx xxx xx xx biçiminde olmalı.")
            elif (number or "") != str(entry.get("workPhone") or ""):
                entry["workPhone"] = number or ""
                changed.append(f"{tag}.workPhone")
        if "email" in raw:
            value = str(raw.get("email") or "").strip().lower()
            if value and (len(value) > EMAIL_MAX or not _EMAIL.fullmatch(value)):
                _field_error(details, "guardian", slot, "email",
                             f"{slot}. velinin e-postası geçersiz (ör. ad@alan.com).")
            elif value != str(entry.get("email") or ""):
                entry["email"] = value
                changed.append(f"{tag}.email")
        for field in ("homeAddress", "workAddress"):
            if field not in raw:
                continue
            try:
                parts = _address(raw.get(field))
                for part, limit in ADDRESS_MAX.items():
                    if len(parts[part]) > limit:
                        raise ValueError(f"Adres alanı en çok {limit} karakter.")
            except ValueError as failure:
                _field_error(details, "guardian", slot, field, f"{slot}. veli: {failure}")
                continue
            old = entry.get(field) if isinstance(entry.get(field), dict) else {}
            if any(parts.values()):
                if {key: str((old or {}).get(key) or "") for key in parts} != parts:
                    entry[field] = parts
                    changed.append(f"{tag}.{field}")
            elif old and any(old.values()):
                entry.pop(field, None)
                changed.append(f"{tag}.{field}")

    if json.dumps(entries, sort_keys=True) != before_entries:
        text = guardian_text(entries)
        if text != str(profile.get("guardian_identity") or ""):
            columns["guardian_identity"] = text
    if names:
        extra = extra_items(profile)
        for slot, name in names.items():
            if slot == 1:
                columns["parent_name"] = name
            elif slot == 2:
                columns["parent_name2"] = name
            elif 0 <= slot - 3 < len(extra):
                extra[slot - 3] = {**extra[slot - 3], "name": name}
        if any(slot >= 3 for slot in names):
            columns["guardians_extra"] = extra_text(extra)


# ===================================================================== okuma


def _entry_for(profile: dict[str, Any] | None, phone: Any) -> dict[str, Any]:
    number = normalize_phone(phone)
    return guardian_map(profile).get(number or "", {}) if number else {}


def _secret(kantin_id: str, field: str, token: str, tail: str, unseal: Unseal | None, *,
            national: bool) -> tuple[str | None, str | None]:
    """(tam değer ya da None, maske). Çözülemezse tam değer None."""
    masked = mask(tail, national=national)
    if not token:
        return None, None
    if unseal is None:
        return None, masked
    try:
        return unseal(kantin_id, field, token) or None, masked
    except CipherUnavailable:
        return None, masked


def edit_view(kantin_id: str, profile: dict[str, Any] | None, guardians: list[dict[str, Any]],
              *, unseal: Unseal | None, display_name: str = "") -> dict[str, Any]:
    """Öğrenci kartındaki "Kimlik ve adres" bölümü. `unseal` yoksa TC/yabancı kimlik MASKELİ."""
    profile = profile or {}
    national, national_masked = _secret(kantin_id, "student.nationalId",
                                        str(profile.get("national_id") or ""),
                                        str(profile.get("national_id_tail") or ""), unseal,
                                        national=True)
    foreign, foreign_masked = _secret(kantin_id, "student.foreignId",
                                      str(profile.get("foreign_id") or ""),
                                      str(profile.get("foreign_id_tail") or ""), unseal,
                                      national=False)
    student = {
        "nationalId": national, "nationalIdMasked": national_masked,
        "foreignId": foreign, "foreignIdMasked": foreign_masked,
        "birthDate": str(profile.get("birth_date") or "") or None,
        "gender": str(profile.get("gender") or "") or None,
        "registeredOn": str(profile.get("registered_on") or "") or None,
        "schoolName": str(profile.get("school_name") or "") or None,
        "homePhone": str(profile.get("home_phone") or "") or None,
        "motherName": str(profile.get("mother_name") or "") or None,
        "fatherName": str(profile.get("father_name") or "") or None,
        "address": address_out(profile.get("address_line"), profile.get("address_district"),
                               profile.get("address_city")),
    }
    people = []
    for row in sorted(guardians, key=lambda item: int(item.get("slot") or 0)):
        entry = _entry_for(profile, row.get("phone"))
        g_national, g_national_masked = _secret(
            kantin_id, "guardian.nationalId", str(entry.get("nationalId") or ""),
            str(entry.get("nationalIdTail") or ""), unseal, national=True)
        g_foreign, g_foreign_masked = _secret(
            kantin_id, "guardian.foreignId", str(entry.get("foreignId") or ""),
            str(entry.get("foreignIdTail") or ""), unseal, national=False)
        relation = str(row.get("relation") or "")
        people.append({
            "slot": int(row.get("slot") or 0),
            "fullName": str(row.get("name") or "") or None,
            "phone": normalize_phone(row.get("phone")),
            "relation": relation or None,
            "relationLabel": RELATIONS.get(relation, ""),
            "nationalId": g_national, "nationalIdMasked": g_national_masked,
            "foreignId": g_foreign, "foreignIdMasked": g_foreign_masked,
            "occupation": str(entry.get("occupation") or "") or None,
            "workPhone": str(entry.get("workPhone") or "") or None,
            "email": str(entry.get("email") or "") or None,
            "homeAddress": _address_entry(entry.get("homeAddress")),
            "workAddress": _address_entry(entry.get("workAddress")),
        })
    return {
        "studentId": kantin_id,
        "revealed": unseal is not None,
        "student": student,
        "guardians": people,
        "missing": missing_fields(profile, guardians, display_name),
        "updatedAt": str(profile.get("identity_updated_at") or "") or None,
        # Velilerde anne / baba yakınlığı varsa adı öneri olarak (öğrenci alanı elle değişir).
        "suggestions": {
            "motherName": next((str(row.get("name") or "") for row in guardians
                                if row.get("relation") == "mother" and row.get("name")), None),
            "fatherName": next((str(row.get("name") or "") for row in guardians
                                if row.get("relation") == "father" and row.get("name")), None),
        },
    }


def _address_entry(value: Any) -> dict[str, str | None] | None:
    if not isinstance(value, dict):
        return None
    return address_out(value.get("line"), value.get("district"), value.get("city"))


def gateway_block(kantin_id: str, profile: dict[str, Any] | None,
                  guardians: list[dict[str, Any]], *, unseal: Unseal) -> dict[str, Any]:
    """Mobil geçidin `identity` bloğu — 80-ortak K2 şekli BİREBİR (camelCase, yoksa null).

    `guardians`: dizin kaydının velileri `[{slot, name, phone, relation}]`.
    Şifre çözülemezse `CipherUnavailable` yükselir: çağıran bloğu HİÇ yazmaz
    (Akademi aynası değişmez — `null` göndermek aynayı silerdi).
    """
    profile = profile or {}

    def plain(column: str, field: str) -> str | None:
        token = str(profile.get(column) or "")
        return (unseal(kantin_id, field, token) or None) if token else None

    student = {
        "nationalId": plain("national_id", "student.nationalId"),
        "foreignId": plain("foreign_id", "student.foreignId"),
        "birthDate": str(profile.get("birth_date") or "") or None,
        "gender": str(profile.get("gender") or "") or None,
        "registeredOn": str(profile.get("registered_on") or "") or None,
        "schoolName": str(profile.get("school_name") or "") or None,
        "homePhone": str(profile.get("home_phone") or "") or None,
        "motherName": str(profile.get("mother_name") or "") or None,
        "fatherName": str(profile.get("father_name") or "") or None,
        "address": address_out(profile.get("address_line"), profile.get("address_district"),
                               profile.get("address_city")),
    }
    people = []
    for row in sorted(guardians, key=lambda item: int(item.get("slot") or 0)):
        phone = normalize_phone(row.get("phone"))
        if phone is None:
            continue
        entry = _entry_for(profile, phone)

        def secret(field: str, entry: dict[str, Any] = entry) -> str | None:
            token = str(entry.get(field) or "")
            return (unseal(kantin_id, f"guardian.{field}", token) or None) if token else None

        relation = str(row.get("relation") or "")
        people.append({
            "slot": int(row.get("slot") or 0),
            "fullName": str(row.get("name") or "") or None,
            "nationalId": secret("nationalId"),
            "foreignId": secret("foreignId"),
            "relation": relation if relation in RELATIONS else None,
            "phone": phone,
            "occupation": str(entry.get("occupation") or "") or None,
            "workPhone": str(entry.get("workPhone") or "") or None,
            "email": str(entry.get("email") or "") or None,
            "homeAddress": _address_entry(entry.get("homeAddress")),
            "workAddress": _address_entry(entry.get("workAddress")),
        })
    stamp = str(profile.get("identity_updated_at") or "") or str(profile.get("updated_at") or "")
    return {"updatedAt": zulu(stamp), "student": student, "guardians": people}


def zulu(stamp: str) -> str | None:
    """ISO damga → `2026-10-28T12:00:00Z` (sözleşme örneğiyle aynı biçim)."""
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def missing_fields(profile: dict[str, Any] | None, guardians: list[dict[str, Any]],
                   display_name: str = "") -> list[dict[str, Any]]:
    """Sözleşme / senet basımı için EKSİK zorunlu alanlar (80-api taslağı `MissingField`).

    Öğrenci: ad soyad + (TC ya da yabancı kimlik). Ödeme sorumlusu veli: ad soyad +
    (TC ya da yabancı kimlik) + ev adresi (açık adres + il) + cep. KM'de ödeme
    sorumlusu henüz seçilmemiştir: bilgisi TAM olan ilk veli (yuva sırasıyla) yeterli
    sayılır; hiçbiri tam değilse birincil velinin eksikleri listelenir.
    """
    profile = profile or {}
    out: list[dict[str, Any]] = []

    def add(scope: str, slot: int | None, field: str) -> None:
        out.append({"scope": scope, "slot": slot, "field": field,
                    "label": LABELS[(scope, field)]})

    name = display_name.strip() or \
        f"{profile.get('first_name') or ''} {profile.get('last_name') or ''}".strip()
    if len(name.split()) < 2:
        add("student", None, "fullName")
    if not profile.get("national_id") and not profile.get("foreign_id"):
        add("student", None, "nationalId")

    people = sorted((row for row in guardians if row.get("slot")),
                    key=lambda item: int(item.get("slot") or 0))
    if not people:
        add("guardian", None, "guardian")
        return out

    def gaps(row: dict[str, Any]) -> list[str]:
        entry = _entry_for(profile, row.get("phone"))
        found = []
        if len(_text(row.get("name")).split()) < 2:
            found.append("fullName")
        if not entry.get("nationalId") and not entry.get("foreignId"):
            found.append("nationalId")
        if not address_complete(entry.get("homeAddress") if isinstance(
                entry.get("homeAddress"), dict) else None):
            found.append("homeAddress")
        if normalize_phone(row.get("phone")) is None:
            found.append("phone")
        return found

    checks = [(row, gaps(row)) for row in people]
    if any(not found for _, found in checks):
        return out
    row, found = checks[0]
    for field in found:
        add("guardian", int(row.get("slot") or 0), field)
    return out


def summary(profile: dict[str, Any] | None, guardians: list[dict[str, Any]],
            display_name: str = "") -> dict[str, Any]:
    """Liste satırının kimlik özeti — şifre ÇÖZÜLMEZ (maske son 4 haneden)."""
    profile = profile or {}
    missing = missing_fields(profile, guardians, display_name)
    return {
        "nationalIdMasked": mask(str(profile.get("national_id_tail") or ""))
        if profile.get("national_id") else None,
        "contractMissing": [item["label"] for item in missing],
    }
