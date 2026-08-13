"""Öğrenci Yönetimi — iş kuralları.

Kantin verisi ile Kontrol Merkezi verisinin birleştiği yer. İki taraf da kendi
alanının sahibi; burada kopyalanmaz, eşleştirilir.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import report_dir, write_private

# Kantin'in kabul ettiği alanlar — başka hiçbir şey oraya gönderilmez.
CANTEEN_FIELDS = {"displayName", "parentPhone", "spendingLimit", "isBlocked"}

PROFILE_COLUMNS = {
    "firstName": "first_name",
    "lastName": "last_name",
    "className": "class_name",
    "schoolNo": "school_no",
    "studentPhone": "student_phone",
    "parentName": "parent_name",
    "parentName2": "parent_name2",
    "parentPhone2": "parent_phone2",
    "note": "note",
}

OPAQUE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class StudentService:
    """Kantin öğrencilerini canlı yönetir; kendi profil alanlarını yanına ekler."""

    def __init__(self, *, canteen: Any, store: Any, log: Any,
                 config: dict[str, Any] | None = None,
                 category: str = "Öğrenci", fallback_dir: Path | None = None) -> None:
        self._config = config or {}
        self._category = category
        self._fallback_dir = fallback_dir or Path("data/exports")
        self._canteen = canteen
        self._store = store
        self._log = log
        self._table = store.table("profile")

    # ------------------------------------------------------------- okuma

    async def list_students(self) -> dict[str, Any]:
        """Kantin listesi + bizim alanlarımız. Kantine ulaşılamazsa da yanıt döner."""
        try:
            students = await self._canteen.students()
            connected = True
            error = ""
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; ekran ayakta kalmalı
            students, connected, error = [], False, str(failure)
            self._log.warning("kantin okunamadı", error=error)

        profiles = {
            row["kantin_id"]: row
            for row in await self._store.fetch_all(f"SELECT * FROM {self._table}")
        }

        merged = [self._merge(student, profiles.get(student.get("id", ""))) for student in students]

        # Kantinde olmayan ama bizde profili kalmış kayıtlar: öğrenci kasadan
        # silinmiş olabilir. Sessizce yutmuyoruz, işaretliyoruz.
        known = {student.get("id") for student in students}
        orphans = [row["kantin_id"] for row in profiles.values() if row["kantin_id"] not in known]

        return {
            "connected": connected,
            "error": error,
            "students": merged,
            "orphans": orphans if connected else [],
            "summary": await self._summary(merged, connected),
        }

    async def _summary(self, students: list[dict[str, Any]], connected: bool) -> dict[str, Any]:
        """Üst şeridin rakamları.

        Kasa panosuyla AYNI kaynaktan okunur (`/api/reports/dashboard`) —
        tablette görünen sayıyla burada görünen sayı ayrışmasın. Pano
        okunamazsa öğrenci bakiyelerinden hesaplanan yedeğe düşülür.

        İşaret: kantinde pozitif bakiye = öğrenci BORÇLU (LedgerService).
        """
        debtors = [student for student in students if int(student.get("balance") or 0) > 0]
        credited = [student for student in students if int(student.get("balance") or 0) < 0]

        summary: dict[str, Any] = {
            "totalStudents": len(students),
            "debtorCount": len(debtors),
            "openReceivables": sum(int(student["balance"]) for student in debtors),
            "creditTotal": -sum(int(student["balance"]) for student in credited),
            "blockedStudents": sum(1 for student in students if student.get("isBlocked")),
            "todaySales": None,
            "pendingRequests": None,
            "source": "hesaplanan",
        }

        if not connected:
            return summary

        try:
            dashboard = await self._canteen.dashboard()
        except Exception as failure:  # noqa: BLE001 — pano düşse de liste çalışsın
            self._log.warning("kasa panosu okunamadı", error=str(failure))
            return summary

        summary.update({
            "openReceivables": dashboard.get("openReceivables", summary["openReceivables"]),
            "todaySales": dashboard.get("todaySales"),
            "pendingRequests": dashboard.get("pendingRequests"),
            "blockedStudents": dashboard.get("blockedStudents", summary["blockedStudents"]),
            "totalStudents": dashboard.get("totalStudents", summary["totalStudents"]),
            "source": "kasa panosu",
        })
        return summary

    def _merge(self, canteen: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
        display_name = str(canteen.get("displayName") or "")
        first, last = _split_name(display_name)

        return {
            # kantin otoritesinde
            "kantinId": canteen.get("id"),
            "displayName": display_name,
            "parentPhone": canteen.get("parentPhone") or "",
            "balance": canteen.get("balance", 0),
            "spendingLimit": canteen.get("spendingLimit"),
            "isBlocked": bool(canteen.get("isBlocked")),
            "updatedAt": canteen.get("updatedAt"),
            # bizim kaydımızda
            "firstName": (profile or {}).get("first_name") or first,
            "lastName": (profile or {}).get("last_name") or last,
            "className": (profile or {}).get("class_name") or "",
            "schoolNo": (profile or {}).get("school_no") or "",
            "studentPhone": (profile or {}).get("student_phone") or "",
            "parentName": (profile or {}).get("parent_name") or "",
            "parentName2": (profile or {}).get("parent_name2") or "",
            "parentPhone2": (profile or {}).get("parent_phone2") or "",
            "note": (profile or {}).get("note") or "",
            "hasProfile": profile is not None,
        }

    async def status(self) -> dict[str, Any]:
        try:
            return {"connected": True, "status": await self._canteen.status()}
        except Exception as failure:  # noqa: BLE001
            return {"connected": False, "error": str(failure)}

    async def qr_key(self) -> bytes:
        return await self._canteen.qr_key()

    # ------------------------------------------------------------- yazma

    async def create_student(self, payload: dict[str, Any]) -> dict[str, Any]:
        kantin_id = _new_opaque_id()
        display = _display_name(payload)
        if not display:
            raise ValueError("Ad ve soyad zorunlu.")

        changes: dict[str, Any] = {"displayName": display}
        for field in ("parentPhone", "spendingLimit", "isBlocked"):
            if field in payload:
                changes[field] = payload[field]

        await self._canteen.upsert_student(kantin_id, changes)
        await self._save_profile(kantin_id, payload)
        self._log.info("öğrenci kantine eklendi", kantin_id=kantin_id)
        return {"kantinId": kantin_id}

    async def update_student(self, kantin_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Yalnızca DOKUNULAN alanları yazar.

        Ad/soyad değiştiyse kantindeki tek alanlık `displayName` yeniden
        kurulur; değişmediyse kantine hiç gitmez — tabletin yazdığına
        dokunulmaz.
        """
        changes: dict[str, Any] = {}
        for field in ("parentPhone", "spendingLimit", "isBlocked"):
            if field in payload:
                changes[field] = payload[field]

        if "firstName" in payload or "lastName" in payload or "displayName" in payload:
            profile = await self._store.fetch_one(
                f"SELECT * FROM {self._table} WHERE kantin_id = ?", (kantin_id,)
            )
            merged = {
                "firstName": payload.get("firstName", (profile or {}).get("first_name", "")),
                "lastName": payload.get("lastName", (profile or {}).get("last_name", "")),
                "displayName": payload.get("displayName"),
            }
            display = _display_name(merged)
            if display:
                changes["displayName"] = display

        if changes:
            # Kantin `displayName`i zorunlu tutuyor: yalnız telefon değişse bile
            # mevcut adı birlikte göndermek gerekir.
            if "displayName" not in changes:
                changes["displayName"] = await self._current_display_name(kantin_id)
            await self._canteen.upsert_student(kantin_id, changes)

        await self._save_profile(kantin_id, payload)
        return {"kantinId": kantin_id, "canteenFields": sorted(changes)}

    async def _current_display_name(self, kantin_id: str) -> str:
        for student in await self._canteen.students():
            if student.get("id") == kantin_id:
                return str(student.get("displayName") or "")
        raise ValueError(f"Kantinde bulunamadı: {kantin_id}")

    async def _save_profile(self, kantin_id: str, payload: dict[str, Any]) -> None:
        fields = {key: payload[key] for key in PROFILE_COLUMNS if key in payload}
        if not fields:
            return

        columns = [PROFILE_COLUMNS[key] for key in fields]
        values = [str(fields[key] or "") for key in fields]

        await self._store.execute(
            f"INSERT INTO {self._table} (kantin_id, {', '.join(columns)}, updated_at) "
            f"VALUES (?{', ?' * len(columns)}, ?) "
            f"ON CONFLICT(kantin_id) DO UPDATE SET "
            + ", ".join(f"{column} = excluded.{column}" for column in columns)
            + ", updated_at = excluded.updated_at",
            (kantin_id, *values, datetime.now(UTC).isoformat(timespec="seconds")),
        )


# ------------------------------------------------------------------ yardım


def _split_name(display_name: str) -> tuple[str, str]:
    """Kantin tek alan tutuyor; profil yoksa son kelimeyi soyad sayarız."""
    parts = display_name.strip().split()
    if len(parts) < 2:
        return display_name.strip(), ""
    return " ".join(parts[:-1]), parts[-1]


def _display_name(payload: dict[str, Any]) -> str:
    if payload.get("displayName"):
        return str(payload["displayName"]).strip()[:120]
    name = f"{payload.get('firstName') or ''} {payload.get('lastName') or ''}".strip()
    return name[:120]


def _new_opaque_id() -> str:
    """Kantin'in kendi biçimiyle aynı: STU- + 10 karakter (bkz. StudentFactory)."""
    return "STU-" + "".join(secrets.choice(OPAQUE_ALPHABET) for _ in range(10))

    # ------------------------------------------------------------ kartlar

    async def build_cards(self, kantin_ids: list[str] | None = None) -> dict[str, Any]:
        """Seçili (ya da tüm) öğrenciler için A4 kart PDF'i üretir.

        Kart QR'ı kasadakiyle BİT-UYUMLUDUR: aynı anahtar, aynı kodlayıcı.
        Anahtar kantinden gelir ve arayüze hiç inmez.
        """
        from datetime import UTC, datetime

        from . import qr as qr_codec
        from .cards import QrError, build_cards_pdf

        merged = (await self.list_students())["students"]
        wanted = {str(item) for item in (kantin_ids or [])}
        chosen = [row for row in merged if not wanted or row["kantinId"] in wanted]
        if not chosen:
            return {"ok": False, "error": "Kart basılacak öğrenci yok."}

        try:
            key = await self.qr_key()
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı
            return {"ok": False, "error": f"QR anahtarı alınamadı: {failure}"}

        chosen.sort(key=lambda row: str(row.get("displayName") or "").casefold())
        cards = [
            {
                "kantinId": row["kantinId"],
                "name": row.get("displayName") or "",
                "className": row.get("className") or "",
                "qrText": qr_codec.encode(row["kantinId"], key),
            }
            for row in chosen
        ]

        try:
            content = build_cards_pdf(
                cards,
                columns=int(self._config.get("cards_columns") or 3),
                rows=int(self._config.get("cards_rows") or 4),
            )
        except QrError as failure:
            return {"ok": False, "error": str(failure)}

        # Uygulama geneli hiyerarşi: Masaüstü/Kontrol Merkezi/Raporlar/Öğrenci/<yıl>/<ay>.
        # Her çağrıda yeniden çözülür — ay değişince klasör de değişsin.
        target = report_dir(
            self._category,
            fallback=self._fallback_dir,
            configured=str(self._config.get("export_path") or ""),
        )
        name = f"kantin-kartlari-{len(cards)}-{datetime.now(UTC).astimezone().strftime('%Y%m%d-%H%M')}.pdf"
        # Kart PDF'i öğrenci adı ve QR kimliği taşır — yalnız kullanıcıya okunur.
        path = write_private(target / name, content)

        self._log.info("kart pdf üretildi", count=len(cards), path=str(path))
        return {"ok": True, "count": len(cards), "name": name,
                "path": str(path), "bytes": len(content)}
