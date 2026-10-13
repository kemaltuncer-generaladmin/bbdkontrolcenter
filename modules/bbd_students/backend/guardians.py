"""Veli yönetimi — kural tek yerde (göç 007).

Öğrencinin en çok `MAX_GUARDIANS` velisi olur; sıra önceliktir ve İLK veli
BİRİNCİLDİR (yuva 1). Yuvalar:

    1  telefon kantinde (`parentPhone`, kantin otoritesi), ad `parent_name`,
       yakınlık `parent_relation`
    2  `parent_name2`, `parent_phone2`, `parent_relation2`
    3+ `guardians_extra` (JSON dizi)

Telefon ZORUNLUDUR ve Türkiye cep numarası olmalıdır (`5XXXXXXXXX`; yazımda
`+90`, `0`, boşluk serbest — `phones.normalize_phone`). Aynı öğrencide aynı
telefon iki kez olamaz; AYNI TELEFON BİRDEN ÇOK ÖĞRENCİNİN VELİSİ OLABİLİR
(kardeşler) — engellenmez, ekranda bilgi olarak gösterilir.

Olaylar servisin olağan yolundan çıkar (`service.update_student`): yuva 1–2
telefonu `guardian.linked|unlinked|phone_changed` (`data.slot`), ad / yakınlık /
ek veli değişikliği `student.updated` (`fields: ["guardians"]`). Kişisel veri
olaya yazılmaz.
"""

from __future__ import annotations

import json
from typing import Any

from .phones import normalize_phone

MAX_GUARDIANS = 4
#: Mobil geçit sözleşmesinin bugün taşıdığı yuvalar (uygulama girişi bunlarla).
APP_SLOTS = (1, 2)
NAME_MAX = 80

RELATIONS: dict[str, str] = {
    "mother": "Anne",
    "father": "Baba",
    "guardian": "Vasi",
    "grandparent": "Büyükanne / büyükbaba",
    "sibling": "Kardeş",
    "other": "Diğer",
}


class GuardianError(ValueError):
    """Veli listesi reddedildi; mesaj Türkçe ve ekranda gösterilebilir."""

    def __init__(self, message: str, *, index: int | None = None, field: str | None = None) -> None:
        super().__init__(message)
        self.index = index
        self.field = field


def parse_relation(value: Any) -> str:
    text = str(value or "").strip()
    if text and text not in RELATIONS:
        raise GuardianError(f"Bilinmeyen yakınlık: {text}.", field="relation")
    return text


def clean_items(items: Any) -> list[dict[str, str]]:
    """Ekrandan gelen sıralı liste → doğrulanmış `[{name, phone, relation}]`."""
    if not isinstance(items, list):
        raise GuardianError("Veli listesi okunamadı.")
    if len(items) > MAX_GUARDIANS:
        raise GuardianError(f"En çok {MAX_GUARDIANS} veli eklenebilir.")
    seen: dict[str, int] = {}
    clean: list[dict[str, str]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise GuardianError("Veli satırı okunamadı.", index=index)
        order = index + 1
        phone_text = str(raw.get("phone") or "").strip()
        if not phone_text:
            raise GuardianError(f"{order}. velinin telefonu boş.", index=index, field="phone")
        phone = normalize_phone(phone_text)
        if phone is None:
            raise GuardianError(f"{order}. velinin telefonu geçersiz — cep numarası olmalı "
                                "(+90 5xx xxx xx xx).", index=index, field="phone")
        if phone in seen:
            raise GuardianError(f"{order}. velinin telefonu {seen[phone]}. veliyle aynı.",
                                index=index, field="phone")
        seen[phone] = order
        name = " ".join(str(raw.get("name") or "").split())
        if len(name) > NAME_MAX:
            raise GuardianError(f"{order}. velinin adı en çok {NAME_MAX} karakter olabilir.",
                                index=index, field="name")
        try:
            relation = parse_relation(raw.get("relation"))
        except GuardianError as failure:
            raise GuardianError(str(failure), index=index, field="relation") from failure
        clean.append({"name": name, "phone": phone, "relation": relation})
    return clean


def extra_text(extra: list[dict[str, str]]) -> str:
    """3.+ veliler → `guardians_extra` sütunu ('' = yok)."""
    return json.dumps(extra, ensure_ascii=False, separators=(",", ":")) if extra else ""


def extra_items(profile: dict[str, Any] | None) -> list[dict[str, str]]:
    """`guardians_extra` sütunu → liste (bozuksa boş — kayıt okunur kalsın)."""
    text = str((profile or {}).get("guardians_extra") or "").strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except ValueError:
        return []
    out: list[dict[str, str]] = []
    for item in loaded if isinstance(loaded, list) else []:
        if isinstance(item, dict) and str(item.get("phone") or "").strip():
            out.append({"name": str(item.get("name") or ""), "phone": str(item.get("phone") or ""),
                        "relation": str(item.get("relation") or "")})
    return out[: MAX_GUARDIANS - 2]


def to_payload(items: list[dict[str, str]]) -> dict[str, Any]:
    """Doğrulanmış liste → `update_student` gövdesi (yuva 1 kantine, 2 ve 3+ profile)."""
    first = items[0] if len(items) > 0 else {"name": "", "phone": "", "relation": ""}
    second = items[1] if len(items) > 1 else {"name": "", "phone": "", "relation": ""}
    return {
        "parentPhone": first["phone"],
        "parentName": first["name"],
        "parentRelation": first["relation"],
        "parentName2": second["name"],
        "parentPhone2": second["phone"],
        "parentRelation2": second["relation"],
        "guardiansExtra": extra_text(items[2:]),
    }


def view(profile: dict[str, Any] | None, parent_phone: Any) -> list[dict[str, Any]]:
    """Profil + kantindeki 1. veli telefonu → ekranın veli listesi (yuva sırasıyla)."""
    profile = profile or {}
    rows: list[dict[str, Any]] = []

    def add(slot: int, name: Any, phone: Any, relation: Any) -> None:
        raw = str(phone or "").strip()
        if not raw and not str(name or "").strip():
            return
        rows.append({
            "slot": slot,
            "name": str(name or ""),
            "phone": normalize_phone(raw) or raw,
            "phoneValid": normalize_phone(raw) is not None,
            "relation": str(relation or ""),
            "relationLabel": RELATIONS.get(str(relation or ""), ""),
            "primary": slot == 1,
            "appAccess": slot in APP_SLOTS,
        })

    add(1, profile.get("parent_name"), parent_phone, profile.get("parent_relation"))
    add(2, profile.get("parent_name2"), profile.get("parent_phone2"), profile.get("parent_relation2"))
    for offset, item in enumerate(extra_items(profile)):
        add(3 + offset, item["name"], item["phone"], item["relation"])
    return rows
