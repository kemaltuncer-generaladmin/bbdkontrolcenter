"""Seviye, alan ve hedef sınav — doğrulama ve sınıf adından ÖNERİ.

Kontrol Merkezi bu alanların ana kaynağıdır; BBD Akademi müfredatı buradan
türetir. Kural tek yerde durur: HTTP uçları, toplu işlem ve dizin yeteneği
hepsi buradan geçer.

    gradeLevel   5..12 (tam sayı) ya da "graduate"     · None = girilmemiş
    track        "SAY" | "EA" | "SOZ" | "DIL"           · None
    examTarget   "LGS" | "YKS"                          · None

**Alan 10. sınıf altında tutulmaz.** Seviye 10'un altındaysa alan girilemez;
seviye 10'un altına çekilirse alan temizlenir. Seviye bilinmiyorsa alan
girilebilir (sınıf adı "SAY" taşıyıp seviyesi henüz girilmemiş öğrenci).

**Öneri otomatik yazılmaz.** `suggest()` yalnız ekrana ipucu verir; personel
tek tıkla kabul eder.
"""

from __future__ import annotations

import re
from typing import Any

GRADES = tuple(range(5, 13))
GRADUATE = "graduate"
TRACKS = ("SAY", "EA", "SOZ", "DIL")
EXAMS = ("LGS", "YKS")

#: Alanın tutulabildiği en düşük seviye.
TRACK_MIN_GRADE = 10

#: Payload anahtarı → tablo sütunu.
CURRICULUM_COLUMNS = {
    "gradeLevel": "grade_level",
    "track": "track",
    "examTarget": "exam_target",
}

# Sınıf adında alanı anlatan sözcükler. Eski müfredat adları (MF, TM, TS) da
# tanınır: okullar hâlâ kullanıyor.
_TRACK_WORDS = {
    "SAY": "SAY", "SAYISAL": "SAY", "MF": "SAY",
    "EA": "EA", "ESIT": "EA", "ESITAGIRLIK": "EA", "TM": "EA",
    "SOZ": "SOZ", "SOZEL": "SOZ", "TS": "SOZ",
    "DIL": "DIL", "YDL": "DIL",
}
_GRADUATE_WORDS = {"MEZUN", "MEZ", "GRADUATE"}

_ASCII = str.maketrans("ÇĞİIÖŞÜçğıiöşü", "CGIIOSUCGIIOSU")


class CurriculumError(ValueError):
    """Seviye/alan/hedef sınav kurala uymuyor. Mesaj Türkçe, ekrana gider."""


def parse_grade(value: Any) -> int | str | None:
    """5..12 ya da "graduate". Boş → None. Geçersiz → `CurriculumError`."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise CurriculumError("Seviye 5–12 ya da mezun olmalı.")
    if isinstance(value, str):
        text = value.strip()
        if text.lower() == GRADUATE:
            return GRADUATE
        if not text.isdigit():
            raise CurriculumError("Seviye 5–12 ya da mezun olmalı.")
        value = int(text)
    if isinstance(value, int) and value in GRADES:
        return value
    raise CurriculumError("Seviye 5–12 ya da mezun olmalı.")


def parse_track(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().upper().translate(_ASCII)
    if text not in TRACKS:
        raise CurriculumError("Alan SAY, EA, SOZ ya da DIL olmalı.")
    return text


def parse_exam(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().upper()
    if text not in EXAMS:
        raise CurriculumError("Hedef sınav LGS ya da YKS olmalı.")
    return text


def track_allowed(grade: int | str | None) -> bool:
    """Bu seviyede alan tutulabilir mi? Bilinmeyen seviyede evet."""
    return grade is None or grade == GRADUATE or (isinstance(grade, int) and grade >= TRACK_MIN_GRADE)


def from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    """Tablo satırı → `{"gradeLevel", "track", "examTarget"}`. Bozuk değer None olur."""
    row = row or {}
    try:
        grade = parse_grade(row.get("grade_level"))
    except CurriculumError:
        grade = None
    try:
        track = parse_track(row.get("track"))
    except CurriculumError:
        track = None
    try:
        exam = parse_exam(row.get("exam_target"))
    except CurriculumError:
        exam = None
    return {"gradeLevel": grade, "track": track, "examTarget": exam}


def resolve(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Mevcut değerler + yama → yazılacak SÜTUNLAR (yalnız değişenler + gerekli temizlik).

    `patch` yalnız GÖNDERİLEN anahtarları taşır; gönderilmeyen alan değişmez.
    Dönüş `{"grade_level": "8" | "graduate" | None, …}` — yalnız yazılacak
    sütunlar.
    """
    changes: dict[str, Any] = {}
    grade = current.get("gradeLevel")
    track = current.get("track")

    if "gradeLevel" in patch:
        grade = parse_grade(patch["gradeLevel"])
        changes["grade_level"] = None if grade is None else str(grade)
    if "track" in patch:
        track = parse_track(patch["track"])
        if track is not None and not track_allowed(grade):
            raise CurriculumError("Alan yalnız 10. sınıf ve üstünde ya da mezunda girilir.")
        changes["track"] = track
    elif track is not None and not track_allowed(grade):
        # Seviye 10'un altına çekildi: alan tutulmaz (modül başlığı).
        changes["track"] = None
    if "examTarget" in patch:
        changes["exam_target"] = parse_exam(patch["examTarget"])
    return changes


def suggest(class_name: str | None) -> dict[str, Any] | None:
    """Sınıf adından öneri: "8-A" → 8 + LGS, "12-SAY" → 12 + SAY + YKS.

    Hiçbir şey çıkarılamıyorsa None. ÖNERİDİR, yazılmaz.
    """
    text = str(class_name or "").strip().upper().translate(_ASCII)
    if not text:
        return None
    words = [word for word in re.split(r"[^A-Z0-9]+", text) if word]

    grade: int | str | None = None
    if any(word in _GRADUATE_WORDS for word in words):
        grade = GRADUATE
    else:
        match = re.match(r"^\s*(\d{1,2})(?!\d)", text)
        if match and int(match.group(1)) in GRADES:
            grade = int(match.group(1))

    track: str | None = None
    for word in words:
        letters = re.sub(r"\d", "", word)
        if letters in _TRACK_WORDS:
            track = _TRACK_WORDS[letters]
            break
    if track is not None and not track_allowed(grade):
        track = None

    exam: str | None = None
    if grade == 8:
        exam = "LGS"
    elif grade == GRADUATE or grade in (11, 12):
        exam = "YKS"

    if grade is None and track is None:
        return None
    return {"gradeLevel": grade, "track": track, "examTarget": exam}


# ---------------------------------------------------------------- sınıf seviyeyi taşır
#
# Kullanıcı kararı (2026-10-13): seviye / alan / hedef sınav ANA SINIF tanımında
# girilir (`mod_bbd_students_class`, göç 008). Öğrencinin geçerli değeri:
#
#   sınıfın seviyesi tanımlı  → seviye, alan, hedef sınav SINIFTAN;
#                               `level_override` açıksa öğrencinin DOLU alanı / hedef
#                               sınavı sınıfınkinin yerine geçer (seviye değil)
#   sınıfa seviye girilmemiş  → öğrencinin kendi değerleri (göç 002; geriye uyum)
#
# `source`: "class" | "override" | "student" | None (hiçbiri yok).


def class_from_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Sınıf satırı → `{"gradeLevel", "track", "examTarget"}`; seviyesi yoksa None."""
    if not row:
        return None
    plan = from_row(row)
    if plan["gradeLevel"] is None:
        return None
    if plan["track"] is not None and not track_allowed(plan["gradeLevel"]):
        plan["track"] = None
    return plan


def resolve_class(current: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    """Sınıf tanımı yaması → yazılacak SÜTUNLAR (tam satır). Seviye zorunlu; alan yalnız 10+ / mezun.

    Gönderilmeyen alan mevcut değerini korur; seviye 10'un altına inerse alan temizlenir.
    """
    base = from_row(current) if current else {"gradeLevel": None, "track": None, "examTarget": None}
    columns = resolve(base, patch)
    grade = parse_grade(patch["gradeLevel"]) if "gradeLevel" in patch else base["gradeLevel"]
    if grade is None:
        raise CurriculumError("Sınıfın seviyesini seçin (5–12 ya da mezun).")
    row = {
        "grade_level": str(grade),
        "track": columns.get("track", base["track"]),
        "exam_target": columns.get("exam_target", base["examTarget"]),
    }
    if not track_allowed(grade):
        row["track"] = None
    return row


def resolve_group(patch: dict[str, Any]) -> dict[str, Any]:
    """Sınıf / GRUP müfredat seviyesi (Akademi README §14 madde 102): seviye girilirse `resolve_class` kuralı; seviye
    boşsa yalnız hedef sınav ve/veya alan da olabilir ("TYT GRUBU" → hedef YKS, seviye karışık). Alan LGS ile olmaz."""
    if patch.get("gradeLevel") not in (None, ""):
        return resolve_class(None, patch)
    track = parse_track(patch.get("track"))
    exam = parse_exam(patch.get("examTarget"))
    if track is None and exam is None:
        raise CurriculumError("Seviye, hedef sınav ya da alan seçin.")
    if track is not None and exam == "LGS":
        raise CurriculumError("Alan LGS hedefiyle seçilmez.")
    return {"grade_level": None, "track": track, "exam_target": exam}


def group_from_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Sınıf / grup satırının müfredat seviyesi — seviyesiz de olabilir (yalnız hedef / alan); hiçbiri yoksa None."""
    if not row:
        return None
    plan = from_row(row)
    if plan["gradeLevel"] is None and plan["track"] is None and plan["examTarget"] is None:
        return None
    if plan["gradeLevel"] is not None and plan["track"] is not None and not track_allowed(plan["gradeLevel"]):
        plan["track"] = None
    return plan


def effective(profile: dict[str, Any] | None, class_plan: dict[str, Any] | None) -> dict[str, Any]:
    """Öğrencinin GEÇERLİ seviyesi (dizin, liste, Akademi bunu görür) + kaynağı."""
    own = from_row(profile)
    override = bool(int((profile or {}).get("level_override") or 0))
    if class_plan is not None:
        plan = dict(class_plan)
        source = "class"
        if override:
            # Yalnız öğrencide DOLU olan alan sınıfınkinin yerine geçer (boşsa sınıftan).
            if own["track"] is not None and track_allowed(plan["gradeLevel"]):
                plan["track"] = own["track"]
            if own["examTarget"] is not None:
                plan["examTarget"] = own["examTarget"]
            source = "override"
        return {**plan, "levelSource": source}
    source = "student" if any(own[key] is not None for key in ("gradeLevel", "track", "examTarget")) else None
    return {**own, "levelSource": source}


def class_label(name: str, plan: dict[str, Any] | None) -> str:
    """"11-A · 11. sınıf · SAY · YKS"; seviyesizse "11-A · seviye seçin"."""
    if plan is None:
        return f"{name} · seviye seçin"
    parts = [name, "Mezun" if plan["gradeLevel"] == GRADUATE else f"{plan['gradeLevel']}. sınıf"]
    if plan.get("track"):
        parts.append({"SOZ": "SÖZ", "DIL": "DİL"}.get(plan["track"], plan["track"]))
    if plan.get("examTarget"):
        parts.append(plan["examTarget"])
    return " · ".join(parts)
