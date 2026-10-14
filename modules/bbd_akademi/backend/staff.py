"""Mobil yönetici erişimi — KM OTORİTEDİR (PLATFORM §1.1).

Akademi mobil uygulamasına yönetici ya da öğretmen olarak kimin girebileceği
burada, KM'de tutulur ve Akademi'ye TAM LİSTE olarak itilir
(`PUT /v1/admin/staff/sync`, `x-surface: km`). Akademi bu kayıtları
`source=km` olarak saklar ve başka uçtan değiştirilmesine izin vermez.

İTME ÜÇ ANDA OLUR:

1. Kayıt eklenince / değişince / pasifleşince — hemen.
2. Zamanlanmış eşitlemede (`staff_sync_minutes`, varsayılan 30 dk) — Akademi
   kapalıyken yapılan değişiklik de böylece kendiliğinden yerine ulaşır.
3. Panelden "Şimdi eşitle".

AYNI TELEFON AKADEMİ'DE YEREL PERSONELSE (ör. kurulumda akademi-cli ile açılan ilk
yönetici) Akademi o kaydı sahiplenir: KM'nin olur, roller KM'den gelir, çift kayıt
açılmaz (Akademi `staff.adopt`; `phone_conflict` yalnız aynı listede aynı telefon
iki kez gelirse).

Akademi ulaşılamazsa KM'deki kayıt YİNE YAZILIR; eşitleme durumu "başarısız"
olur ve sonraki tur tekrar dener. Akademi'nin satır bazındaki reddi
(`invalid_phone`, `unknown_role`, `phone_conflict`) satırın üstüne yazılır.

İYİMSER KİLİT (ADR 0020): her satır `revision` taşır; düzenleme beklenen
sürümle gelir, araya başka bir değişiklik girdiyse 409.

ANONS ADI (`announce_name`, sözleşme km-ogrenci-gruplari-ve-zil.md §1): zil
çağrısında öğretmenin okunacağı ad ("Hüseyin hoca"). KM tek kaynaktır; boş =
yok. Yüke `announceName` olarak YALNIZ `contract_features.STAFF_ANNOUNCE_NAME`
açıkken girer (sözleşmede 2d91b96 ile var; şalter açık — o dosyanın başlığı).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from . import contract_features
from .gateway import AkademiError, AkademiGateway, on_behalf_of, request_id

ROLES = ("admin", "teacher")
#: Öğretmen = koç (tek kavram, tek Akademi rolü ``teacher``; Akademi rol adı "Öğretmen / Koç").
#: Bir kişi birden çok rol taşır (ör. yönetici + öğretmen / koç); her rol uygulamada ayrı seçilir.
ROLE_LABELS = {"admin": "Yönetici", "teacher": "Öğretmen / Koç"}
PHONE = re.compile(r"^5\d{9}$")
ANNOUNCE_NAME_MAX = 60
SYNC_PATH = "/v1/admin/staff/sync"
SYSTEM_ACTOR = ("system", "Personel eşitleme")


class StaffError(ValueError):
    """Geçersiz girdi — 400/422 olarak döner."""

    def __init__(self, message: str, *, code: str = "validation_error",
                 field: str | None = None, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.status = status


class StaffNotFound(StaffError):
    def __init__(self) -> None:
        super().__init__("Personel kaydı bulunamadı.", code="staff_not_found", status=404)


class StaffConflict(StaffError):
    def __init__(self, message: str, *, code: str = "version_conflict") -> None:
        super().__init__(message, code=code, status=409)


def normalize_phone(raw: Any) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 12 and digits.startswith("90"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StaffService:
    def __init__(self, *, store: Any, gateway: AkademiGateway, log: Any,
                 clock: Callable[[], str] = _now) -> None:
        self._store = store
        self._gateway = gateway
        self._log = log
        self._clock = clock
        self._table = store.table("staff")
        self._state = store.table("sync_state")
        self._lock = asyncio.Lock()
        self._loop: asyncio.Task[None] | None = None

    # ------------------------------------------------------------- okuma

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        roles = [item for item in str(row.get("roles") or "").split(",") if item]
        return {
            "id": row["id"],
            "kmUserId": row.get("km_user_id") or None,
            "displayName": row["display_name"],
            "phone": row["phone"],
            "roles": roles,
            "active": bool(row.get("active")),
            "note": row.get("note") or "",
            "announceName": row.get("announce_name") or "",
            "revision": int(row.get("revision") or 1),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "updatedBy": row.get("updated_by") or "",
            "syncError": ({"code": row["last_error_code"], "message": row["last_error_message"]}
                          if row.get("last_error_code") else None),
        }

    async def rows(self) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._table} ORDER BY active DESC, display_name")
        return [self._row(row) for row in rows]

    async def get(self, staff_id: str) -> dict[str, Any]:
        row = await self._store.fetch_one(f"SELECT * FROM {self._table} WHERE id = ?",
                                          (staff_id,))
        if row is None:
            raise StaffNotFound()
        return self._row(row)

    # ------------------------------------------------------------- yazma

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(str(data.get("displayName") or "").split())
        if len(name) < 2 or len(name) > 80:
            raise StaffError("Ad soyad 2–80 karakter olmalı.", field="displayName")
        phone = normalize_phone(data.get("phone"))
        if not PHONE.match(phone):
            raise StaffError("Cep numarası 10 haneli olmalı ve 5 ile başlamalı (5XXXXXXXXX).",
                             field="phone")
        roles = [str(item) for item in data.get("roles") or []]
        roles = [item for item in ROLES if item in roles]
        if not roles or len(roles) != len(set(data.get("roles") or [])):
            raise StaffError("En az bir Akademi rolü seçin (Yönetici, Öğretmen / Koç).",
                             field="roles")
        km_user_id = str(data.get("kmUserId") or "").strip() or None
        note = str(data.get("note") or "").strip()[:200]
        announce = " ".join(str(data.get("announceName") or "").split())
        if len(announce) > ANNOUNCE_NAME_MAX:
            raise StaffError(f"Anons adı en çok {ANNOUNCE_NAME_MAX} karakter olmalı.",
                             field="announceName")
        return {"display_name": name, "phone": phone, "roles": ",".join(roles),
                "km_user_id": km_user_id, "note": note, "announce_name": announce}

    async def _assert_unique(self, clean: dict[str, Any], own_id: str | None) -> None:
        clash = await self._store.fetch_one(
            f"SELECT id FROM {self._table} WHERE phone = ?", (clean["phone"],))
        if clash and clash["id"] != own_id:
            raise StaffConflict("Bu telefon başka bir personel kaydında var.",
                                code="phone_conflict")
        if clean["km_user_id"]:
            linked = await self._store.fetch_one(
                f"SELECT id FROM {self._table} WHERE km_user_id = ?", (clean["km_user_id"],))
            if linked and linked["id"] != own_id:
                raise StaffConflict("Bu KM kullanıcısı zaten listede.", code="user_conflict")

    async def create(self, actor: str, data: dict[str, Any]) -> dict[str, Any]:
        clean = self._validate(data)
        await self._assert_unique(clean, None)
        staff_id = uuid.uuid4().hex
        now = self._clock()
        await self._store.execute(
            f"INSERT INTO {self._table} (id, km_user_id, display_name, phone, roles, active, "
            "note, announce_name, revision, created_at, created_by, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (staff_id, clean["km_user_id"], clean["display_name"], clean["phone"],
             clean["roles"], 1 if data.get("active", True) else 0, clean["note"],
             clean["announce_name"], now, actor, now, actor),
        )
        return await self.get(staff_id)

    async def update(self, actor: str, staff_id: str, data: dict[str, Any],
                     expected_revision: int) -> dict[str, Any]:
        current = await self.get(staff_id)
        if current["revision"] != int(expected_revision):
            raise StaffConflict("Kayıt siz düzenlerken değişti. Ekranı yenileyip tekrar deneyin.")
        merged = {**current, **{k: v for k, v in data.items() if v is not None}}
        clean = self._validate(merged)
        await self._assert_unique(clean, staff_id)
        active = 1 if merged.get("active", True) else 0
        await self._store.execute(
            f"UPDATE {self._table} SET km_user_id = ?, display_name = ?, phone = ?, roles = ?, "
            "active = ?, note = ?, announce_name = ?, revision = revision + 1, updated_at = ?, "
            "updated_by = ? WHERE id = ? AND revision = ?",
            (clean["km_user_id"], clean["display_name"], clean["phone"], clean["roles"],
             active, clean["note"], clean["announce_name"], self._clock(), actor, staff_id,
             int(expected_revision)),
        )
        fresh = await self.get(staff_id)
        if fresh["revision"] != int(expected_revision) + 1:
            raise StaffConflict("Kayıt siz düzenlerken değişti. Ekranı yenileyip tekrar deneyin.")
        return fresh

    # ---------------------------------------------------------- eşitleme

    async def payload(self) -> dict[str, Any]:
        rows = await self._store.fetch_all(f"SELECT * FROM {self._table} ORDER BY id")
        # Sözleşmede yoksa alan HİÇ gönderilmez (contract_features başlığı).
        announce = contract_features.STAFF_ANNOUNCE_NAME
        items = []
        for row in rows:
            item: dict[str, Any] = {
                "kmUserId": row.get("km_user_id") or f"staff:{row['id']}",
                "displayName": row["display_name"],
                "phone": row["phone"],
                "roleKeys": [item for item in str(row["roles"]).split(",") if item],
                "active": bool(row["active"]),
            }
            if announce:
                # Boş = anons adı yok → Akademi çağrıda `teacherName: null` (§3.2/6).
                item["announceName"] = row.get("announce_name") or None
            items.append(item)
        return {"items": items}

    async def state(self) -> dict[str, Any]:
        row = await self._store.fetch_one(f"SELECT * FROM {self._state} WHERE name = 'staff'")
        if row is None:
            return {"status": "never", "source": "", "attemptedAt": None, "succeededAt": None,
                    "result": None, "error": ""}
        try:
            result = json.loads(row["result"]) if row.get("result") else None
        except ValueError:
            result = None
        return {"status": row["status"], "source": row.get("source") or "",
                "attemptedAt": row.get("attempted_at"), "succeededAt": row.get("succeeded_at"),
                "result": result, "error": row.get("error") or ""}

    async def _write_state(self, *, status: str, source: str, digest: str,
                           result: dict[str, Any] | None, error: str) -> None:
        now = self._clock()
        previous = await self._store.fetch_one(
            f"SELECT succeeded_at FROM {self._state} WHERE name = 'staff'")
        succeeded = now if status == "ok" else (previous or {}).get("succeeded_at")
        await self._store.execute(f"DELETE FROM {self._state} WHERE name = 'staff'")
        await self._store.execute(
            f"INSERT INTO {self._state} (name, status, source, attempted_at, succeeded_at, "
            "payload_hash, result, error) VALUES ('staff', ?, ?, ?, ?, ?, ?, ?)",
            (status, source, now, succeeded, digest,
             json.dumps(result, ensure_ascii=False) if result is not None else "", error),
        )

    async def push(self, *, source: str, actor: tuple[str, str] | None = None) -> dict[str, Any]:
        """Tam listeyi Akademi'ye iter. Hata FIRLATMAZ; sonucu durum olarak döner."""
        async with self._lock:
            body = await self.payload()
            digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            who_id, who_name = actor or SYSTEM_ACTOR
            kind = "user" if actor else "system"
            headers = {"content-type": "application/json", "accept": "application/json",
                       "X-Akademi-On-Behalf-Of": on_behalf_of(who_id, who_name, kind=kind),
                       "X-Request-Id": request_id(None)}
            try:
                response = await self._gateway.send(
                    "PUT", SYNC_PATH, headers=headers,
                    content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
                try:
                    raw = await response.aread()
                finally:
                    await response.aclose()
            except AkademiError as failure:
                await self._write_state(status="failed", source=source, digest=digest,
                                        result=None, error=str(failure))
                self._log.warning("personel eşitlemesi yapılamadı", error=str(failure))
                return await self.state()

            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            if response.status_code >= 400:
                error = payload.get("error") if isinstance(payload, dict) else None
                message = (error or {}).get("message") or f"Akademi {response.status_code} döndü."
                await self._write_state(status="failed", source=source, digest=digest,
                                        result=None, error=str(message))
                return await self.state()

            await self._apply_row_errors(payload.get("errors") or [])
            status = "partial" if payload.get("errors") else "ok"
            await self._write_state(status="ok" if status == "ok" else "partial", source=source,
                                    digest=digest, result=payload, error="")
            return await self.state()

    async def _apply_row_errors(self, errors: list[dict[str, Any]]) -> None:
        await self._store.execute(
            f"UPDATE {self._table} SET last_error_code = '', last_error_message = ''")
        for item in errors:
            key = str(item.get("kmUserId") or "")
            code = str(item.get("code") or "error")[:40]
            message = str(item.get("message") or "")[:200]
            if key.startswith("staff:"):
                await self._store.execute(
                    f"UPDATE {self._table} SET last_error_code = ?, last_error_message = ? "
                    "WHERE id = ?", (code, message, key[6:]))
            else:
                await self._store.execute(
                    f"UPDATE {self._table} SET last_error_code = ?, last_error_message = ? "
                    "WHERE km_user_id = ?", (code, message, key))

    # --------------------------------------------------------- zamanlama

    def start(self, minutes: int) -> None:
        if minutes <= 0:
            return
        if self._loop is None or self._loop.done():
            self._loop = asyncio.get_running_loop().create_task(
                self._run(minutes * 60), name="bbd-akademi-staff-sync")

    async def stop(self) -> None:
        if self._loop is None:
            return
        self._loop.cancel()
        try:
            await self._loop
        except asyncio.CancelledError:
            pass
        self._loop = None

    async def _run(self, interval: float) -> None:
        # Açılışta hemen değil, kısa bir gecikmeyle: çekirdek açılışı beklemesin.
        await asyncio.sleep(min(60.0, interval))
        while True:
            try:
                configured = await self._gateway.configured()
                if configured["enabled"] and configured["hasKey"]:
                    await self.push(source="schedule")
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 — döngü ölmemeli (K7)
                self._log.warning("personel eşitleme turu patladı", error=str(failure))
            await asyncio.sleep(interval)
