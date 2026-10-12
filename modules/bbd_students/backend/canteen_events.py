"""`bbd_students.sync` yeteneği — kantinden gelen öğrenci olaylarının KM karşılığı.

Sözleşme: bbd-akademi `docs/integrations/km-merkezi-senkron.md` §6.2, §6.3.
Tüketici: Kantin Geçidi (`bbd_canteen_api`) olay alıcısı. Modül modülü import
etmez (K3): alıcı bu yeteneği registry'den çözer ve profil tablosuna dokunmaz
(K5) — öğrenci kaydını yalnız bu modül yazar.

    await student_upserted(opaque_id, fields, *, created=False, causation_id=None,
                           within=None)
        → "student.created" | "student.updated" | None
    invalidate()   — dizin önbelleğini HEMEN bırakır (öğrenci / kontrol olayı)
    wake()         — kantin canlı: uzlaştırmayı beklemeden koştur

`within` (eşzamansız, argümansız) aynı işlemin İLK adımı olarak koşar (False
dönerse olay yazılmaz — tekrar): alıcının
`inbox` tekilleştirmesi ile profil satırı ve outbox olayı tek commit'tir. Ağ
çağrısı (taslak ad için kantin okuması) işlemden ÖNCE yapılır (ADR 0029).

KASADAN EKLENEN ÖĞRENCİ (§6.3). `created: true` ve KM'de profil yoksa TASLAK
profil açılır (`source = canteen`, ad kantindeki görünen addan bölünür) ve
`student.created` (köken `canteen`) yazılır; ekranda "Kasadan eklendi —
bilgileri tamamla" rozeti çıkar. Kantin değeri bilinmeden ad bölünemezse
profil adsız açılır (dizin adı kantinden okur).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .directory import split_name
from .sync import Event, StudentSync

#: Kantin alan adı → olaydaki mantıksal alan (geçit sözleşmesi adları).
CANTEEN_FIELDS = {
    "displayName": "displayName",
    "parentPhone": "guardians",
    "spendingLimit": "spendingLimit",
    "isBlocked": "isBlocked",
    "status": "status",
}


class CanteenSync:
    """`bbd_students.sync` — kantin olay alıcısının öğrenci yüzü."""

    def __init__(self, *, sync: StudentSync, directory: Any, canteen: Any, log: Any) -> None:
        self._sync = sync
        self._directory = directory
        self._canteen = canteen
        self._log = log

    async def student_upserted(self, opaque_id: str, fields: list[str] | None, *,
                               created: bool = False,
                               causation_id: str | None = None,
                               within: Callable[[], Awaitable[Any]] | None = None
                               ) -> str | None:
        kantin_id = str(opaque_id or "").strip()
        if not kantin_id:
            return None
        if await self._sync.tombstone(kantin_id) is not None:
            return None  # KM'de silinmiş; kantinin geç gelen olayı yok sayılır
        logical: list[str] = []
        for name in fields or []:
            mapped = CANTEEN_FIELDS.get(str(name), str(name))
            if mapped not in logical:
                logical.append(mapped)
        row = await self._sync.profile(kantin_id)
        draft = created and row is None
        first, last = await self._draft_name(kantin_id) if draft else ("", "")
        async with self._sync._store.transaction():
            if within is not None and await within() is False:
                return None  # alıcı tekrarı gördü (inbox): olay yok
            if draft and await self._sync.profile(kantin_id) is None:
                await self._sync.write(
                    kantin_id,
                    columns={"first_name": first, "last_name": last, "source": "canteen"},
                    events=[Event("student.created")], origin="canteen",
                    causation_id=causation_id,
                    log={"action": "created_at_canteen", "actor": "Kasa"})
                kind = "student.created"
            else:
                await self._sync.write(kantin_id, events=[Event("student.updated", logical)],
                                       origin="canteen", causation_id=causation_id)
                kind = "student.updated"
        self.invalidate()
        return kind

    async def _draft_name(self, kantin_id: str) -> tuple[str, str]:
        try:
            record = await self._canteen.student(kantin_id)
        except Exception as failure:  # noqa: BLE001 — ad olmadan da taslak açılır
            self._log.warning("kasadan eklenen öğrencinin adı okunamadı", student=kantin_id,
                              error=str(failure))
            return "", ""
        return split_name(str((record or {}).get("displayName") or ""))

    def invalidate(self) -> None:
        self._directory.invalidate(canteen=True)

    def wake(self) -> None:
        self._sync.wake()
