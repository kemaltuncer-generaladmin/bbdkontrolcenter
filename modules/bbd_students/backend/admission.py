"""`bbd_students.admission` — dış kaynaklı öğrenci açılışı (iş 81-km-kulup, göç 015).

KULLANICI (28.10.2026): "deneme kulübü diye sınıf açacaz biz mesela … sanki kurum
öğrencisiymiş gibi olacak." Deneme Kulübü formu dolunca BBD Akademi, mobil geçidin
`POST /students` ucuyla KM'de öğrenci açtırır (bbd-akademi 81-api-taslak §C). Kulüp üyesi
KM'de NORMAL öğrencidir: ayrı öğrenci türü yoktur; adı verilen sınıfa ("Deneme Kulübü",
yoksa açılır) üye olur ve kantine mevcut kuralla yazılır (önce KM, sonra kantin —
`StudentService.create_student`).

Akış (`admit`):

1. `external_ref` daha önce işlendiyse İLK yanıt aynen döner (idempotent; tablo
   `admission`). Aynı anda gelen ikinci istek sahiplenmeyi göremezse 409
   `admission_in_progress` alır (Akademi işi yeniden dener).
2. Çağıran mevcut bir öğrenciyi işaret ettiyse (`student_id`) ya da KM'de ad soyadı VE
   bir telefonu (öğrenci / veli) birebir tutan TEK etkin öğrenci varsa yeni öğrenci
   AÇILMAZ: öğrenci kulüp sınıfına EK sınıf olarak eklenir (ana sınıfı değişmez) →
   `created: False`. Birden çok aday → 409 `ambiguous_match` (Akademi "Eşleşme belirsiz —
   KM'den eşleştirin" der).
3. Yoksa yeni öğrenci: ad, öğrenci telefonu, 1. veli (kantin telefonu) + kimlik bloğu
   (80 K2 şekli; TC şifreli) tek işlemde, sonra sınıf üyeliği; ikinci ve sonraki veliler
   `set_guardians`, onların kimliği `set_identity` ile yazılır. Olay `student.created`
   (outbox, `causationId` = Akademi'nin Idempotency-Key'i).

Öğrenci açıldıktan SONRA biten adım (fazladan veli / kimlik) hata verirse öğrenci geri
alınmaz: yanıt `warnings` taşır, yönetici KM'den tamamlar. Kişisel veri log'a yazılmaz.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .groups import fold
from .phones import normalize_phone

SOURCES = ("club",)
DEFAULT_CLASS = "Deneme Kulübü"
REF_MAX = 120


class AdmissionError(ValueError):
    """Açılış reddedildi. `status` HTTP karşılığı, `code` makine içindir; mesaj Türkçe."""

    def __init__(self, message: str, *, status: int = 422, code: str = "validation_error",
                 field: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.reason = code
        self.field = field
        #: Geçit yalnız alan YOLUNU taşır (değer değil) — `details.path`.
        self.details = {"path": field} if field else {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


class StudentAdmission:
    """Mobil geçidin çözdüğü yetenek (K3) — profil ve sınıf yazması bu modülde kalır (K5)."""

    def __init__(self, *, service: Any, store: Any, directory: Any, log: Any) -> None:
        self._service = service
        self._store = store
        self._directory = directory
        self._log = log
        self._table = store.table("admission")

    # ------------------------------------------------------------ yardımcılar

    async def _row(self, ref: str) -> dict[str, Any] | None:
        return await self._store.fetch_one(
            f"SELECT * FROM {self._table} WHERE external_ref = ?", (ref,))

    @staticmethod
    def _reply(row: dict[str, Any]) -> dict[str, Any]:
        return {"studentId": str(row["kantin_id"]), "created": bool(int(row["created"] or 0)),
                "className": str(row["class_name"] or "")}

    async def _claim(self, ref: str, source: str) -> bool:
        stamp = _now()
        try:
            await self._store.execute(
                f"INSERT INTO {self._table} (external_ref, source, state, created_at, updated_at) "
                f"VALUES (?, ?, 'pending', ?, ?)", (ref, source, stamp, stamp))
        except Exception:  # noqa: BLE001 — birincil anahtar çakışması: başkası sahiplendi
            return False
        return True

    async def _finish(self, ref: str, kantin_id: str, created: bool, class_name: str) -> None:
        await self._store.execute(
            f"UPDATE {self._table} SET kantin_id = ?, created = ?, class_name = ?, "
            f"state = 'done', updated_at = ? WHERE external_ref = ?",
            (kantin_id, 1 if created else 0, class_name, _now(), ref))

    async def _release(self, ref: str) -> None:
        await self._store.execute(
            f"DELETE FROM {self._table} WHERE external_ref = ? AND state = 'pending'", (ref,))

    @staticmethod
    def _guardians(payload: dict[str, Any]) -> list[dict[str, str]]:
        """`guardians [{slot, name, phone, relation}]` → yuva sırasıyla temiz liste."""
        rows = payload.get("guardians") if isinstance(payload.get("guardians"), list) else []
        items: list[tuple[int, dict[str, str]]] = []
        seen: set[str] = set()
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict):
                continue
            phone = normalize_phone(raw.get("phone"))
            if phone is None:
                raise AdmissionError(f"{index + 1}. velinin telefonu geçersiz — cep numarası "
                                     "olmalı.", field=f"guardians[{index}].phone")
            if phone in seen:
                continue
            seen.add(phone)
            slot = raw.get("slot")
            order = int(slot) if isinstance(slot, int) and 1 <= slot <= 4 else 10 + index
            items.append((order, {"name": _clean(raw.get("name"), 80), "phone": phone,
                                  "relation": _clean(raw.get("relation"), 20)}))
        items.sort(key=lambda item: item[0])
        return [item for _, item in items][:4]

    # ------------------------------------------------------------ eşleşme

    async def _match(self, name: str, phones: list[str]) -> list[str]:
        """Ad soyadı (Türkçe katlanmış) ve bir telefonu tutan ETKİN öğrenciler."""
        wanted = fold(name)
        found: dict[str, None] = {}
        for phone in phones:
            try:
                records = await self._directory.find_by_phone(phone)
            except Exception as failure:
                raise AdmissionError("Öğrenci dizini şu an okunamıyor; birazdan yeniden "
                                     "deneyin.", status=503, code="canteen_unavailable") \
                    from failure
            for record in records or []:
                if not isinstance(record, dict) or record.get("status") == "inactive":
                    continue
                full = f"{record.get('firstName') or ''} {record.get('lastName') or ''}"
                if fold(full) == wanted or fold(record.get("displayName") or "") == wanted:
                    found[str(record["id"])] = None
        return list(found)

    async def _join(self, kantin_id: str, class_name: str, actor: str) -> list[str]:
        """Kulüp sınıfına EK üyelik (ana sınıf değişmez). Sınıf = grup değilse (birleştirme
        öncesi eski düzen) ana sınıf ezilmez — uyarıyla atlanır."""
        if not class_name:
            return []
        if not await self._service.own_level():
            return [(f"\"{class_name}\" sınıfına eklenemedi: KM sınıf birleştirmesi tamamlanmamış "
                    "— Öğrenciler panelinden ekleyin.")]
        result = await self._service.set_class([kantin_id], class_name, actor=actor)
        if result.get("failed"):
            return [f"\"{class_name}\" sınıfına eklenemedi."]
        return []

    # ------------------------------------------------------------ ana iş

    async def admit(self, payload: dict[str, Any], *, external_ref: str, source: str = "club",
                    actor: str = "BBD Akademi", causation_id: str | None = None,
                    student_id: str | None = None) -> dict[str, Any]:
        """→ `{studentId, created, className, warnings?}` (ayrıntı modül belgesinde)."""
        ref = _clean(external_ref, REF_MAX)
        if not ref:
            raise AdmissionError("externalRef boş olamaz.", field="externalRef")
        if source not in SOURCES:
            raise AdmissionError("Bilinmeyen kaynak.", field="source")
        first = _clean(payload.get("firstName"), 60)
        last = _clean(payload.get("lastName"), 60)
        if not first or not last:
            raise AdmissionError("Öğrencinin adı ve soyadı zorunlu.",
                                 field="firstName" if not first else "lastName")
        class_name = _clean(payload.get("className"), 20) or DEFAULT_CLASS
        student_phone = None
        if payload.get("studentPhone"):
            student_phone = normalize_phone(payload.get("studentPhone"))
            if student_phone is None:
                raise AdmissionError("Öğrenci telefonu geçersiz — cep numarası olmalı.",
                                     field="studentPhone")
        guardians = self._guardians(payload)
        if not guardians:
            raise AdmissionError("En az bir veli (cep telefonuyla) zorunlu.", field="guardians")

        existing = await self._row(ref)
        if existing is not None and existing["state"] == "done":
            return self._reply(existing)
        if existing is not None or not await self._claim(ref, source):
            raise AdmissionError("Bu kayıt şu anda işleniyor; birkaç saniye sonra yeniden "
                                 "deneyin.", status=409, code="admission_in_progress")
        try:
            return await self._admit(ref, first, last, class_name, student_phone, guardians,
                                     payload, actor=actor, causation_id=causation_id,
                                     student_id=student_id)
        except BaseException:
            await self._release(ref)
            raise

    async def _admit(self, ref: str, first: str, last: str, class_name: str,
                     student_phone: str | None, guardians: list[dict[str, str]],
                     payload: dict[str, Any], *, actor: str, causation_id: str | None,
                     student_id: str | None) -> dict[str, Any]:
        # 2) Mevcut öğrenci
        target = _clean(student_id, 40) if student_id else ""
        if target:
            if await self._service.get_student(target) is None:
                raise AdmissionError("Eşleştirilen öğrenci KM'de yok.", status=404,
                                     code="student_not_found", field="studentId")
        else:
            phones = list(dict.fromkeys([*( [student_phone] if student_phone else []),
                                         *(row["phone"] for row in guardians)]))
            matches = await self._match(f"{first} {last}", phones)
            if len(matches) > 1:
                raise AdmissionError("Eşleşme belirsiz — KM'den eşleştirin.", status=409,
                                     code="ambiguous_match")
            target = matches[0] if matches else ""
        if target:
            warnings = await self._join(target, class_name, actor)
            await self._finish(ref, target, False, class_name)
            self._log.info("dış kayıt mevcut öğrenciye bağlandı", student=target, source="club")
            reply: dict[str, Any] = {"studentId": target, "created": False,
                                     "className": class_name}
            if warnings:
                reply["warnings"] = warnings
            return reply

        # 3) Yeni öğrenci
        primary = guardians[0]
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else None
        first_identity: dict[str, Any] | None = None
        later_identity: list[dict[str, Any]] = []
        if identity:
            people = [row for row in identity.get("guardians") or [] if isinstance(row, dict)]
            primary_rows = [row for row in people
                            if normalize_phone(row.get("phone")) == primary["phone"]
                            or (row.get("slot") == 1 and not row.get("phone"))]
            later_identity = [row for row in people if row not in primary_rows]
            first_identity = {key: value for key, value in identity.items()
                              if key in ("student",)}
            if primary_rows:
                first_identity["guardians"] = [{**primary_rows[0], "slot": 1,
                                                "phone": primary["phone"]}]
        create: dict[str, Any] = {
            "firstName": first, "lastName": last, "className": class_name,
            "parentPhone": primary["phone"], "parentName": primary["name"],
            "parentRelation": primary["relation"],
        }
        if student_phone:
            create["studentPhone"] = student_phone
        if first_identity:
            create["identity"] = first_identity
        result = await self._service.create_student(create, actor=actor,
                                                    causation_id=causation_id)
        kantin_id = str(result["kantinId"])
        await self._finish(ref, kantin_id, True, class_name)
        warnings: list[str] = []
        if result.get("warning"):
            warnings.append(str(result["warning"]))
        if len(guardians) > 1:
            try:
                await self._service.set_guardians(kantin_id, guardians, actor=actor,
                                                  causation_id=causation_id)
            except Exception:  # noqa: BLE001 — öğrenci açıldı; eksik KM'den tamamlanır
                warnings.append("Ek veliler yazılamadı — KM Öğrenciler panelinden ekleyin.")
            else:
                if later_identity:
                    try:
                        await self._service.set_identity(
                            kantin_id, {"guardians": later_identity}, actor=actor)
                    except Exception:  # noqa: BLE001
                        warnings.append("Ek velilerin kimlik bilgisi yazılamadı — KM'den "
                                        "tamamlayın.")
        self._log.info("dış kaynaktan öğrenci açıldı", student=kantin_id, source="club",
                       canteen=result.get("canteenSync"))
        reply = {"studentId": kantin_id, "created": True, "className": class_name}
        if warnings:
            reply["warnings"] = warnings
        return reply
