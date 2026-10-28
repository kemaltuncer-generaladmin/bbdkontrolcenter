"""Öğrenci Yönetimi — iş kuralları.

Kantin verisi ile Kontrol Merkezi verisinin birleştiği yer. İki taraf da kendi
alanının sahibi; burada kopyalanmaz, eşleştirilir.
"""

from __future__ import annotations

import asyncio
import base64
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import report_dir, write_private

from . import curriculum
from . import guardians as guardian_rules
from . import identity as identity_rules
from .directory import split_name, student_classes, student_record
from .groups import fold
from .lifecycle import describe_status
from .phones import normalize_phone
from .sync import (
    LOGICAL_FIELDS,
    STALE_AFTER,
    Event,
    StudentSync,
    guardian_event,
    iso,
    pending_upsert,
)

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
    # Veli yönetimi (göç 007). `guardiansExtra` gövdeden DEĞİL, yalnız
    # `set_guardians` üzerinden yazılır (PATCH şeması onu taşımaz).
    "parentRelation": "parent_relation",
    "parentRelation2": "parent_relation2",
    "guardiansExtra": "guardians_extra",
    # Sözleşme kimliği (göç 014): velinin kimlik girişleri telefonla anahtarlı JSON.
    # Gövdeden DEĞİL, yalnız `set_guardians` (telefon değişince taşıma) yazar.
    "guardianIdentity": "guardian_identity",
}

#: Veli adı / telefonu / yakınlığı değişince kimlik bloğunun damgası da değişir.
_GUARDIAN_FIELDS = frozenset({"guardians"})

#: Sınıf adı en çok bu kadar karakter (PATCH şemasıyla aynı).
CLASS_NAME_MAX = 20

OPAQUE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

#: Toplu şifre sıfırlamada iki çağrı arasındaki bekleme (saniye). Kantin
#: tarafındaki `auth:sanctum` grubu `throttle:120,1` sınırı taşıyor
#: (bbdkantin backend/routes/api.php); büyük seçimlerde (100+ öğrenci) bu
#: sınıra değmemek için sıralı çağrılar arasına küçük bir boşluk konur.
RESET_DELAY_SECONDS = 0.6


#: Kantin ürün listesinin bellekte tutulduğu süre (saniye) — yasaklı ürün
#: seçicisi her öğrencide listeyi yeniden çekmesin (sözleşme: 5 dk önbellek).
PRODUCTS_CACHE_SECONDS = 300.0

#: Veli kantin kontrollerinden bu ekranın yazabildikleri.
CONTROL_KEYS = ("spendingLimit", "dailyLimit", "isBlocked", "guardianBlocked",
                "restrictedProductIds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Dosya yine de kaydedilmiştir."""


class ControlsError(ValueError):
    """Kontrol yaması reddedildi. `status` HTTP karşılığıdır, mesaj Türkçe."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def _settled(call: Callable[[], Any]) -> Any:
    """Kantin çağrısının sonucu ya da hatası — yükseltmez (ekran ayakta kalmalı)."""
    try:
        return await call()
    except Exception as failure:  # noqa: BLE001 — kantin dışarısı
        return failure


class StudentService:
    """Kantin öğrencilerini canlı yönetir; kendi profil alanlarını yanına ekler."""

    def __init__(self, *, canteen: Any, store: Any, log: Any,
                 config: dict[str, Any] | None = None,
                 category: str = "Öğrenci", fallback_dir: Path | None = None,
                 on_change: Callable[..., None] | None = None,
                 sync: StudentSync | None = None, directory: Any = None,
                 cipher: identity_rules.IdentityCipher | None = None) -> None:
        self._config = config or {}
        #: Sözleşme kimliği şifresi (göç 014; anahtar sır kasasında). Yoksa TC yazılamaz.
        self._cipher = cipher
        # Öğrenci yazıldığında dizin yeteneğinin önbelleğini bırakır
        # (`StudentDirectory.invalidate`). `canteen=False`: yalnız profil değişti.
        self._on_change = on_change
        self._category = category
        self._fallback_dir = fallback_dir or Path("data/exports")
        self._canteen = canteen
        self._store = store
        self._log = log
        self._table = store.table("profile")
        #: Sınıf tanımları (göç 008) — birleştirmeden (göç 009) sonra yalnız tarihçe.
        self._classes = store.table("class")
        #: Sınıflar (= eski gruplar, göç 009) ve birleştirmenin durumu.
        self._groups = store.table("group")
        self._merge_state = store.table("class_merge")
        #: Toplu "sınıfa ekle" / eski `className` yazması üyelik yoluna gider
        #: (`GroupService`; modül kurulurken `attach_groups` ile bağlanır).
        self._group_service: Any = None
        self._products_cache: tuple[float, list[dict[str, Any]]] | None = None
        #: Önce-KM-sonra-kantin yazma yolu (km-merkezi-senkron §7). Testler
        #: vermezse olaysız yerel bir eşi kurulur.
        self._sync = sync or StudentSync(store=store, canteen=canteen, log=log,
                                         on_change=on_change)
        #: Öğrencinin şu anki kaydı (eski değer karşılaştırması: veli telefonu, ad).
        self._directory = directory

    @property
    def _export_dir(self) -> Path:
        """Çıktı klasörü — her çağrıda YENİDEN çözülür (ay değişince değişsin)."""
        return report_dir(
            self._category, fallback=self._fallback_dir,
            configured=str(self._config.get("export_path") or ""),
        )

    # ------------------------------------------------------------- okuma

    def _changed(self, *, canteen: bool) -> None:
        if self._on_change is not None:
            self._on_change(canteen=canteen)

    def attach_groups(self, groups: Any) -> None:
        """Sınıf servisi (`GroupService`) — toplu sınıf ataması üyelikten yürür."""
        self._group_service = groups

    async def own_level(self) -> bool:
        """Sınıf birleştirmesi bitti mi? Bittiyse seviye öğrencinin KENDİSİNDEDİR."""
        row = await self._store.fetch_one(
            f"SELECT state FROM {self._merge_state} WHERE id = 1")
        return row is not None and str(row["state"] or "") == "done"

    async def class_names(self) -> dict[str, str]:
        """Canlı sınıf kimliği → ad (öğrencinin `classes[]`'i için)."""
        rows = await self._store.fetch_all(
            f"SELECT id, name FROM {self._groups} WHERE archived_at = ''")
        return {str(row["id"]): str(row["name"]) for row in rows}

    async def list_students(self) -> dict[str, Any]:
        """Kantin listesi + bizim alanlarımız. Kantine ulaşılamazsa da yanıt döner.

        Kantin listesi, kasa panosu ve profil tablosu BİRBİRİNDEN BAĞIMSIZDIR;
        üçü aynı anda istenir (eskiden sırayla: liste bitmeden pano başlamazdı).
        """
        (students_call, dashboard_call, profile_rows, tomb_rows, plans, names,
         own) = await asyncio.gather(
            _settled(self._canteen.students), _settled(self._canteen.dashboard),
            self._store.fetch_all(f"SELECT * FROM {self._table}"),
            self._store.fetch_all(f"SELECT kantin_id FROM {self._store.table('tombstone')}"),
            self.class_plans(), self.class_names(), self.own_level(),
        )
        if isinstance(students_call, BaseException):
            students, connected, error = [], False, str(students_call)
            self._log.warning("kantin okunamadı", error=error)
        else:
            students, connected, error = students_call, True, ""

        profiles = {row["kantin_id"]: row for row in profile_rows}
        deleted = {str(row["kantin_id"]) for row in tomb_rows}
        stale_cut = iso(datetime.now(UTC) - STALE_AFTER)

        merged = [self._merge(student, profiles.get(student.get("id", "")), stale_cut=stale_cut,
                              plans=plans, names=names, own=own)
                  for student in students if str(student.get("id") or "") not in deleted]

        # KM kaydı olup kantine HENÜZ yazılamamış öğrenciler (§7): listede
        # görünür, kantin alanları bekleyen gövdeden gelir.
        known = {student.get("id") for student in students}
        for row in profiles.values():
            if row["kantin_id"] not in known and row.get("canteen_sync") == "pending":
                merged.append(self._merge({"id": row["kantin_id"], "balance": 0},
                                          row, stale_cut=stale_cut, canteen_missing=True,
                                          plans=plans, names=names, own=own))

        # Kantinde olmayan ama bizde profili kalmış kayıtlar: öğrenci kasadan
        # silinmiş olabilir. Sessizce yutmuyoruz, işaretliyoruz.
        orphans = [row["kantin_id"] for row in profiles.values()
                   if row["kantin_id"] not in known and row.get("canteen_sync") != "pending"]

        return {
            "connected": connected,
            "error": error,
            "students": merged,
            "orphans": orphans if connected else [],
            "summary": await self._summary(merged, connected, dashboard=dashboard_call),
        }

    async def _summary(self, students: list[dict[str, Any]], connected: bool, *,
                       dashboard: Any = None) -> dict[str, Any]:
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

        if dashboard is None:
            try:
                dashboard = await self._canteen.dashboard()
            except Exception as failure:  # noqa: BLE001 — pano düşse de liste çalışsın
                dashboard = failure
        if isinstance(dashboard, BaseException):
            self._log.warning("kasa panosu okunamadı", error=str(dashboard))
            return summary
        if not isinstance(dashboard, dict):
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

    def _merge(self, canteen: dict[str, Any], profile: dict[str, Any] | None, *,
               stale_cut: str = "", canteen_missing: bool = False,
               plans: dict[str, dict[str, Any] | None] | None = None,
               names: dict[str, str] | None = None, own: bool = False) -> dict[str, Any]:
        # Kantine henüz yazılmamış KM değerleri (bekleyen gövde) kantin satırını ezer.
        overlay = pending_upsert(profile)
        if overlay:
            canteen = {**canteen, **overlay}
        display_name = str(canteen.get("displayName") or "")
        if not display_name and profile:
            display_name = f"{profile.get('first_name') or ''} {profile.get('last_name') or ''}".strip()
        first, last = _split_name(display_name)
        class_name = (profile or {}).get("class_name") or ""
        # Sınıf seviyeyi taşır (göç 008): geçerli değer sınıftan; istisna / eski kayıt öğrenciden.
        # Birleştirmeden (göç 009) sonra seviye ÖĞRENCİDEDİR (`own`).
        class_plan = (plans or {}).get(class_name) if class_name else None
        plan = curriculum.effective(profile, None if own else class_plan)
        own_plan = curriculum.from_row(profile)
        classes = student_classes(profile, names) if own else (
            [{"id": "", "name": class_name}] if class_name else [])
        people = guardian_rules.view(profile, canteen.get("parentPhone"))

        return {
            # kantin otoritesinde
            "kantinId": canteen.get("id"),
            "displayName": display_name,
            "parentPhone": canteen.get("parentPhone") or "",
            "balance": canteen.get("balance", 0),
            "spendingLimit": canteen.get("spendingLimit"),
            "isBlocked": bool(canteen.get("isBlocked")),
            "updatedAt": canteen.get("updatedAt"),
            # "tek şifre" kipinin 6 haneli giriş kodu — kantin zaten listede
            # veriyor (bkz. bbd_canteen_api/backend/client.py:students()
            # yorumu); burada yalnızca geçiriliyor, üretilmiyor.
            "accessCode": canteen.get("accessCode") or "",
            "accessCodeIssuedAt": canteen.get("accessCodeIssuedAt"),
            # bizim kaydımızda
            "firstName": (profile or {}).get("first_name") or first,
            "lastName": (profile or {}).get("last_name") or last,
            # SINIF = GRUP (göç 009): öğrenci birden çok sınıfa eşit üye. `className`
            # ekranlar için GÖRÜNEN ad (sınıflar virgülle); tek tek `classes` /
            # `classNames`. Eski tekil ad (eski Akademi uyumu) `legacyClassName`.
            "className": ", ".join(item["name"] for item in classes),
            "classes": classes,
            "classNames": [item["name"] for item in classes],
            "legacyClassName": class_name,
            "schoolNo": (profile or {}).get("school_no") or "",
            "studentPhone": (profile or {}).get("student_phone") or "",
            "parentName": (profile or {}).get("parent_name") or "",
            "parentName2": (profile or {}).get("parent_name2") or "",
            "parentPhone2": (profile or {}).get("parent_phone2") or "",
            "parentRelation": (profile or {}).get("parent_relation") or "",
            "parentRelation2": (profile or {}).get("parent_relation2") or "",
            # Bütün veliler yuva sırasıyla (birincil önce); kantin 1. veli telefonu dahil.
            "guardians": people,
            "note": (profile or {}).get("note") or "",
            # Sözleşme kimliği (göç 014): YALNIZ maske + eksik listesi — tam TC listede yok.
            **identity_rules.summary(profile, people, display_name),
            "hasProfile": profile is not None,
            # Seviye / alan / hedef sınav — GEÇERLİ değer (sınıftan; istisna varsa öğrenciden).
            "gradeLevel": plan["gradeLevel"],
            "track": plan["track"],
            "examTarget": plan["examTarget"],
            # "class" | "override" | "student" (sınıfa seviye girilmemiş, eski kayıt) | None
            "levelSource": plan["levelSource"],
            "classLevel": class_plan,
            "levelOverride": bool(int((profile or {}).get("level_override") or 0)),
            # Öğrencinin kendi (istisna) alanı ve hedef sınavı — yalnız `levelOverride` açıkken geçerli.
            "ownTrack": own_plan["track"],
            "ownExamTarget": own_plan["examTarget"],
            # Seviye öğrencide mi (birleştirme bitti) — ekran seviye alanını açar.
            "levelOnStudent": own,
            # Sınıf adından ÖNERİ — YAZILMAZ; ekran tek tıkla kabul ettirir.
            "curriculumSuggestion": curriculum.suggest(
                class_name or (classes[0]["name"] if classes else "")),
            # Kantin veli kontrolleri listede de taşınır (yeni kantin sürümü).
            "dailyLimit": canteen.get("dailyLimit"),
            "guardianBlocked": bool(canteen.get("guardianBlocked")),
            # Merkezi senkron (km-merkezi-senkron §5, §7).
            **describe_status(profile),
            "revision": int((profile or {}).get("revision") or 0),
            "canteenSync": str((profile or {}).get("canteen_sync") or "ok"),
            "canteenStale": bool(
                (profile or {}).get("canteen_sync") == "pending" and stale_cut
                and str((profile or {}).get("canteen_since") or "") < stale_cut),
            "canteenError": (profile or {}).get("canteen_error"),
            "canteenMissing": canteen_missing,
            "source": str((profile or {}).get("source") or "km"),
        }

    # ------------------------------------------------- veli kantin kontrolleri

    async def canteen_controls(self, kantin_id: str) -> dict[str, Any]:
        """Öğrencinin veli kantin kontrolleri + yasaklı ürün seçicisinin listesi.

        Kontroller KANTİNDE durur ve satış anında orada uygulanır
        (kantin-veli-kontrolleri sözleşmesi); burada kopyası tutulmaz.
        Kantine ulaşılamazsa hata yükseltilmez: `available: false` + sebep.
        """
        try:
            student = await self._canteen.get_student(kantin_id)
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; ekran ayakta kalmalı
            reason = getattr(failure, "reason", None)
            if reason == "student_not_found":
                return {"available": False, "notFound": True,
                        "error": "Öğrenci kantinde bulunamadı."}
            self._log.warning("kantin kontrolleri okunamadı", student=kantin_id,
                              error=str(failure))
            return {"available": False, "error": str(failure)}
        products, products_error = await self._products()
        return {
            "available": True,
            "controls": _controls_view(student),
            "products": products,
            "productsError": products_error,
        }

    async def update_canteen_controls(self, kantin_id: str, patch: dict[str, Any], *,
                                      actor: str,
                                      causation_id: str | None = None) -> dict[str, Any]:
        """Kontrolleri kantine yazar (`canteen.api.update_controls`).

        VELİ ENGELİ YALNIZ KALDIRILIR. Kantini kapatmak velinin kararıdır
        (F-P3.4); personel onu görür ve gerekirse kaldırır, kendisi koymaz —
        kurumun kendi engeli `isBlocked`tır.
        """
        clean = {key: patch[key] for key in CONTROL_KEYS if key in patch}
        if not clean:
            raise ControlsError("Değiştirilecek kontrol yok.")
        if clean.get("guardianBlocked") is True:
            raise ControlsError("Veli engeli yalnız veli tarafından açılır; burada "
                                "yalnız kaldırılabilir. Kurum engeli için “Kurum "
                                "engeli”ni kullanın.")
        try:
            student = await self._canteen.update_controls(kantin_id, clean)
        except Exception as failure:  # kantin dışarısı; sebebi taşınır
            reason = getattr(failure, "reason", None)
            if reason == "student_not_found":
                raise ControlsError("Öğrenci kantinde bulunamadı.", status=404) from failure
            if reason == "validation_error":
                raise ControlsError(f"Kantin reddetti: {failure}", status=422) from failure
            raise ControlsError(f"Kantine ulaşılamadı: {failure}", status=503) from failure
        self._changed(canteen=True)
        # Merkezi senkron: kontroller kantindedir; olay ancak kantin kabul
        # ettikten sonra yazılır (KM'de aynı işlemde yazılacak satır yok).
        try:
            await self._sync.emit(kantin_id, [Event("canteen.controls_changed", sorted(clean))],
                                  revision=None, causation_id=causation_id)
        except Exception as failure:  # noqa: BLE001 — olay yazılamasa da kantin yazıldı
            self._log.warning("kontrol olayı yazılamadı", error=str(failure))
        # Denetim: kim, hangi alanları. Değerler kişisel veri değil ama gereksiz
        # de değil — limit değişikliği sonradan sorulur.
        self._log.info("veli kantin kontrolleri değişti", student=kantin_id, actor=actor,
                       fields=sorted(clean), values={
                           key: value for key, value in clean.items()
                           if key != "restrictedProductIds"},
                       restricted=len(clean.get("restrictedProductIds") or []))
        return {"available": True, "controls": _controls_view(student)}

    async def _products(self) -> tuple[list[dict[str, Any]], str]:
        now = time.monotonic()
        if self._products_cache is not None and \
                now - self._products_cache[0] < PRODUCTS_CACHE_SECONDS:
            return self._products_cache[1], ""
        try:
            rows = await self._canteen.products()
        except Exception as failure:  # noqa: BLE001 — ürünsüz de kontroller düzenlenebilmeli
            return [], str(failure)
        products = sorted(
            (
                {"id": int(row["id"]), "name": str(row.get("name") or ""),
                 "price": int(row.get("price") or 0),
                 "active": bool(row.get("isActive", row.get("active", True)))}
                for row in rows if isinstance(row, dict) and row.get("id") is not None
            ),
            key=lambda item: (not item["active"], item["name"].casefold()),
        )
        self._products_cache = (now, products)
        return products, ""

    async def status(self) -> dict[str, Any]:
        try:
            return {"connected": True, "status": await self._canteen.status()}
        except Exception as failure:  # noqa: BLE001
            return {"connected": False, "error": str(failure)}

    async def qr_key(self) -> bytes:
        return await self._canteen.qr_key()

    # ------------------------------------------------------------- yazma

    async def create_student(self, payload: dict[str, Any], *,
                             actor: str = "", causation_id: str | None = None) -> dict[str, Any]:
        """Yeni öğrenci — ÖNCE KM, sonra kantin (km-merkezi-senkron §7).

        Profil + revizyon + `student.created` tek işlemde yazılır; kantine
        sonra gidilir. Kantin reddederse (4xx) profil silinir ve
        `student.deleted` (köken `system`) yazılır; ulaşılamazsa kayıt
        `pending` kalır ve uzlaştırma yineler — yarım kayıt kalmaz.
        """
        kantin_id = _new_opaque_id()
        display = _display_name(payload)
        if not display:
            raise ValueError("Ad ve soyad zorunlu.")
        # Sınıf = grup (göç 009): birleştirmeden sonra sınıf, ÜYELİKTİR — öğrenci kaydı
        # yazıldıktan sonra o sınıfa eklenir (yoksa açılır); `class_name` türetilir.
        join_class = ""
        if "className" in payload and self._group_service is not None \
                and await self.own_level():
            payload = dict(payload)
            join_class = " ".join(str(payload.pop("className") or "").split())
        # Sözleşme kimliği (göç 014, isteğe bağlı): "Yeni öğrenci" penceresinin açılır
        # bölümü. Aynı işlemde yazılır; kural ihlali kantine gidilmeden reddedilir.
        identity_body = payload.get("identity") if isinstance(payload.get("identity"), dict) \
            else None
        if "identity" in payload:
            payload = {key: value for key, value in payload.items() if key != "identity"}
        # Kural ihlali kantine yazılmadan ÖNCE yakalanır.
        columns = self._profile_columns(None, payload)
        identity_fields: list[str] = []
        if identity_body:
            phone = normalize_phone(payload.get("parentPhone"))
            people = [{"slot": 1, "phone": phone, "name": str(payload.get("parentName") or ""),
                       "relation": str(payload.get("parentRelation") or "")}] if phone else []
            seal, unseal = await self._seal_pair(identity_body)
            identity_columns, identity_fields = identity_rules.apply(
                kantin_id, None, people, identity_body, seal=seal, unseal=unseal)
            columns.update(identity_columns)
            if identity_fields:
                columns["identity_updated_at"] = iso(datetime.now(UTC))

        changes: dict[str, Any] = {"displayName": display}
        for field in ("parentPhone", "spendingLimit", "isBlocked"):
            if field in payload:
                changes[field] = payload[field]

        await self._sync.write(
            kantin_id, columns={**columns, "source": "km"},
            canteen_ops={"upsert": changes}, events=[Event("student.created")],
            causation_id=causation_id, log={"action": "created", "actor": actor})
        outcome = await self._sync.push(kantin_id)
        if outcome.state == "rejected":
            await self._compensate_create(kantin_id)
            raise ValueError(f"Kantin öğrenciyi kabul etmedi: {outcome.error}")
        self._log.info("öğrenci eklendi", kantin_id=kantin_id, canteen=outcome.state)
        if identity_fields:
            await self._identity_log(kantin_id, actor, "created", identity_fields)
        if join_class:
            await self._join_class([kantin_id], join_class, actor=actor or "Öğrenci Yönetimi")
        result: dict[str, Any] = {"kantinId": kantin_id,
                                  "canteenSync": "ok" if outcome.state == "ok" else "pending"}
        if outcome.state != "ok":
            result["warning"] = "Kantine iletilemedi — otomatik yeniden denenecek."
        return result

    async def _compensate_create(self, kantin_id: str) -> None:
        """Kantinin reddettiği yeni kayıt geri alınır (§7 adım 4)."""
        moment = datetime.now(UTC)
        async with self._store.transaction():
            revision = await self._sync.bump()
            await self._store.execute(f"DELETE FROM {self._table} WHERE kantin_id = ?",
                                      (kantin_id,))
            await self._store.execute(
                f"INSERT OR REPLACE INTO {self._store.table('tombstone')} (kantin_id, deleted_at, "
                f"canteen_sync, canteen_attempts, canteen_error) VALUES (?, ?, 'ok', 0, NULL)",
                (kantin_id, iso(moment)))
            await self._sync.emit(kantin_id, [Event("student.deleted", [], {
                "erasedAt": moment.astimezone().isoformat(timespec="seconds")})],
                revision=revision, origin="system")
        self._changed(canteen=False)

    async def update_student(self, kantin_id: str, payload: dict[str, Any], *,
                             actor: str = "", causation_id: str | None = None) -> dict[str, Any]:
        """Yalnızca DOKUNULAN alanları yazar — ÖNCE KM, sonra kantin.

        Ad/soyad değiştiyse kantindeki tek alanlık `displayName` yeniden
        kurulur; kantin alanı değişmediyse kantine hiç gidilmez — tabletin
        yazdığına dokunulmaz. Olaylar: `student.updated` (değişen mantıksal
        alanlar), veli telefonu için `guardian.*` (`data.slot`), limit/engel
        için `canteen.controls_changed`.
        """
        if await self._sync.tombstone(kantin_id) is not None:
            raise ValueError("Öğrenci silinmiş.")
        if "className" in payload and self._group_service is not None \
                and await self.own_level():
            # Sınıf = grup (göç 009): `className` yazmak "o sınıfa da ekle"dir (ana sınıf yok;
            # boş ad bir şey yapmaz — sınıftan çıkarmak üyelik ekranındandır).
            payload = dict(payload)
            name = " ".join(str(payload.pop("className") or "").split())
            if name:
                await self._join_class([kantin_id], name, actor=actor or "Öğrenci Yönetimi")
            if not payload:
                return {"kantinId": kantin_id, "canteenFields": [], "canteenSync": "ok"}
        existing = await self._sync.profile(kantin_id)
        # Seviye/alan kuralı kantine gitmeden denetlenir (yarım yazma olmasın).
        columns = self._profile_columns(existing, payload)
        await self._guard_level(existing, columns, payload)

        changes: dict[str, Any] = {}
        for field in ("parentPhone", "spendingLimit", "isBlocked"):
            if field in payload:
                changes[field] = payload[field]

        record = await self._current_record(kantin_id) \
            if (changes or {"firstName", "lastName", "displayName"} & set(payload)) else None
        current_display = self._display_of(record, existing)

        if "firstName" in payload or "lastName" in payload or "displayName" in payload:
            # Profilde ad yoksa (kantinden gelen eski kayıt) görünen ad bölünür:
            # yalnız ad düzeltilince soyad kaybolmasın.
            fallback_first, fallback_last = split_name(current_display)
            merged = {
                "firstName": payload.get("firstName", (existing or {}).get("first_name")
                                         or fallback_first),
                "lastName": payload.get("lastName", (existing or {}).get("last_name")
                                        or fallback_last),
                "displayName": payload.get("displayName"),
            }
            display = _display_name(merged)
            if display and display != current_display:
                changes["displayName"] = display

        if changes and "displayName" not in changes:
            # Kantin `displayName`i zorunlu tutuyor: yalnız telefon değişse bile
            # mevcut adı birlikte göndermek gerekir.
            if not current_display:
                raise ValueError(f"Kantinde bulunamadı: {kantin_id}")
            changes["displayName"] = current_display

        events = self._update_events(existing, record, columns, changes, payload)
        events = await self._effective_level_events(existing, columns, events)
        if not columns and not changes:
            return {"kantinId": kantin_id, "canteenFields": [], "canteenSync": "ok"}
        if (existing or {}).get("source") == "canteen":
            # Kasadan eklenen taslak KM'de düzenlendi: rozet kalkar.
            columns["source"] = "km"
        if any(event.type.startswith("guardian.") or _GUARDIAN_FIELDS & set(event.fields)
               for event in events):
            # Veli adı / telefonu / yakınlığı geçidin `identity` bloğundadır (göç 014).
            columns["identity_updated_at"] = iso(datetime.now(UTC))

        await self._sync.write(kantin_id, columns=columns,
                               canteen_ops={"upsert": changes} if changes else None,
                               events=events, causation_id=causation_id)
        state = "ok"
        if changes:
            outcome = await self._sync.push(kantin_id)
            if outcome.state == "rejected":
                await self._compensate_update(kantin_id, existing, columns, events)
                raise ValueError(f"Kantin değişikliği kabul etmedi: {outcome.error}")
            state = "ok" if outcome.state == "ok" else "pending"
        return {"kantinId": kantin_id, "canteenFields": sorted(changes), "canteenSync": state}

    def _update_events(self, existing: dict[str, Any] | None, record: dict[str, Any] | None,
                       columns: dict[str, Any], changes: dict[str, Any],
                       payload: dict[str, Any]) -> list[Event]:
        fields: list[str] = []
        for column, value in columns.items():
            logical = LOGICAL_FIELDS.get(column)
            before = (existing or {}).get(column)
            # 3.–4. veli: telefon değişikliği yuva olayıdır (aşağıda); `guardians` alanı
            # yalnız ad / yakınlık değişince ya da veli yuva değiştirince.
            if column == "guardians_extra" and \
                    _extra_people(existing) == _extra_people({column: value}):
                continue
            if logical and (before or "") != (value or "") and logical not in fields:
                fields.append(logical)
        if "displayName" in changes and changes["displayName"] != self._display_of(
                record, existing):
            fields.append("displayName")
        events: list[Event] = []
        if fields:
            events.append(Event("student.updated", fields))
        if "parentPhone" in payload:
            old = _guardian_phone(record, 1)
            event = guardian_event(1, old if record is not None else None,
                                   normalize_phone(payload.get("parentPhone"))
                                   or str(payload.get("parentPhone") or "").strip())
            if event is not None:
                events.append(event)
        if "parent_phone2" in columns:
            event = guardian_event(
                2, normalize_phone((existing or {}).get("parent_phone2"))
                or str((existing or {}).get("parent_phone2") or "").strip(),
                normalize_phone(columns["parent_phone2"]) or str(columns["parent_phone2"]))
            if event is not None:
                events.append(event)
        if "guardians_extra" in columns:
            # Yuva 3–4 (göç 007) — yuva 1–2 ile aynı olaylar (`data.slot` 3|4; sözleşme §2).
            before_extra = _extra_phones(existing)
            after_extra = _extra_phones({"guardians_extra": columns["guardians_extra"]})
            for offset in range(guardian_rules.MAX_GUARDIANS - 2):
                event = guardian_event(3 + offset, before_extra[offset], after_extra[offset])
                if event is not None:
                    events.append(event)
        controls = [key for key in ("spendingLimit", "isBlocked") if key in changes]
        if controls:
            events.append(Event("canteen.controls_changed", controls))
        return events

    async def _compensate_update(self, kantin_id: str, existing: dict[str, Any] | None,
                                 columns: dict[str, Any], events: list[Event]) -> None:
        """Kantin reddetti: önceki değerler geri yazılır + `student.updated` (köken `system`)."""
        restore = {column: (existing or {}).get(column, "" if column in _TEXT_COLUMNS else None)
                   for column in columns}
        fields = sorted({name for event in events for name in event.fields})
        async with self._store.transaction():
            await self._sync.write(kantin_id, columns=restore,
                                   events=[Event("student.updated", fields)], origin="system")
            await self._sync.clear_pending(kantin_id)

    async def _current_record(self, kantin_id: str) -> dict[str, Any] | None:
        """Dizindeki güncel kayıt (eski veli telefonu, ad). Kantin yoksa None."""
        try:
            if self._directory is None:
                # Dizinsiz kurulum (testler): kantinin tekil kaydı.
                row = await self._canteen.student(kantin_id)
                return student_record(row, None) if isinstance(row, dict) else None
            found = await self._directory.get(kantin_id)
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı
            self._log.warning("öğrenci kaydı okunamadı", student=kantin_id, error=str(failure))
            return None
        return dict(found) if isinstance(found, dict) else None

    @staticmethod
    def _display_of(record: dict[str, Any] | None, existing: dict[str, Any] | None) -> str:
        pending = pending_upsert(existing).get("displayName")
        if pending:
            return str(pending)
        if record and record.get("displayName"):
            return str(record["displayName"])
        return f"{(existing or {}).get('first_name') or ''} " \
               f"{(existing or {}).get('last_name') or ''}".strip()

    def _profile_columns(self, existing: dict[str, Any] | None,
                         payload: dict[str, Any]) -> dict[str, Any]:
        """Gövdedeki profil alanları → sütunlar. Seviye/alan kuralı burada (yükseltir)."""
        fields = {key: payload[key] for key in PROFILE_COLUMNS if key in payload}
        for key in ("parentRelation", "parentRelation2"):
            if key in fields:
                fields[key] = guardian_rules.parse_relation(fields[key])
        plan_patch = _curriculum_patch(payload)
        plan_columns: dict[str, Any] = {}
        if plan_patch:
            plan_columns = curriculum.resolve(curriculum.from_row(existing), plan_patch)
        # Eski metin sütunları NOT NULL '' tutar; seviye/alan/hedef sınav
        # sütunları NULL tutar ("girilmemiş" ile "boş metin" ayrı şeyler).
        columns = {PROFILE_COLUMNS[key]: str(fields[key] or "") for key in fields}
        columns.update(plan_columns)
        if "levelOverride" in payload:
            columns["level_override"] = 1 if payload["levelOverride"] else 0
        return columns

    # ------------------------------------------------- tek öğrenci, veliler, sınıf

    async def get_student(self, kantin_id: str) -> dict[str, Any] | None:
        """Tek öğrencinin birleşik kaydı (liste satırıyla aynı biçim) — öğrenci kartı.

        Silinmişse `None`. Kantine ulaşılamazsa ve kayıt kantine henüz yazılmamışsa
        profil + bekleyen gövdeyle döner (`canteenMissing`); ikisi de yoksa hata.
        """
        if await self._sync.tombstone(kantin_id) is not None:
            return None
        profile = await self._sync.profile(kantin_id)
        plans = await self.class_plans((profile or {}).get("class_name") or None)
        names, own = await self.class_names(), await self.own_level()
        try:
            row = await self._canteen.get_student(kantin_id)
        except Exception as failure:
            if getattr(failure, "reason", None) == "student_not_found" and profile is None:
                return None
            if profile is not None and profile.get("canteen_sync") == "pending":
                return self._merge({"id": kantin_id, "balance": 0}, profile,
                                   stale_cut=iso(datetime.now(UTC) - STALE_AFTER),
                                   canteen_missing=True, plans=plans, names=names, own=own)
            if getattr(failure, "reason", None) == "student_not_found":
                return None
            # Kantine şu an ulaşılamıyor: dizinin son görüntüsünden (≤ 20 sn) kurulur;
            # bakiye bilinmez. O da yoksa hata — "bilmiyorum" boş kayıt değildir.
            cached = await self._cached_canteen_row(kantin_id)
            if cached is None:
                raise
            merged = self._merge(cached, profile, plans=plans, names=names, own=own)
            merged["canteenUnavailable"] = True
            return merged
        return self._merge(row if isinstance(row, dict) else {"id": kantin_id}, profile,
                           stale_cut=iso(datetime.now(UTC) - STALE_AFTER), plans=plans,
                           names=names, own=own)

    async def _cached_canteen_row(self, kantin_id: str) -> dict[str, Any] | None:
        cached = getattr(self._directory, "cached_row", None)
        return cached(kantin_id) if callable(cached) else None

    async def guardians(self, kantin_id: str) -> dict[str, Any]:
        """Öğrencinin velileri + aynı telefonun velisi olduğu DİĞER öğrenciler (kardeş)."""
        student = await self.get_student(kantin_id)
        if student is None:
            raise LookupError("Öğrenci bulunamadı.")
        items = [dict(item) for item in student["guardians"]]
        for item in items:
            item["siblings"] = await self._siblings(kantin_id, item.get("phone"))
        return {
            "studentId": kantin_id,
            "items": items,
            "max": guardian_rules.MAX_GUARDIANS,
            "appSlots": list(guardian_rules.APP_SLOTS),
            "relations": [{"value": key, "label": label}
                          for key, label in guardian_rules.RELATIONS.items()],
        }

    async def search(self, query: str, limit: int = 8) -> dict[str, Any]:
        """Küresel öğrenci araması (`search.py`). Kart QR metni okutulursa
        (okuyucu klavye gibi yazar) kasanın anahtarıyla çözülür ve kimlikle aranır;
        anahtar okunamazsa metin olduğu gibi aranır."""
        from . import qr as qr_codec
        from .search import looks_like_card

        if self._directory is None:
            return {"items": [], "complete": False}
        text = str(query or "").strip()
        if looks_like_card(text):
            try:
                decoded = qr_codec.decode(text, await self.qr_key())
            except Exception:  # noqa: BLE001 — anahtar yoksa düz arama
                decoded = None
            if decoded and decoded.get("sid"):
                text = str(decoded["sid"])
        result: dict[str, Any] = await self._directory.search(text, limit)
        return result

    async def _siblings(self, kantin_id: str, phone: Any) -> list[dict[str, str]]:
        number = str(phone or "")
        if self._directory is None or len(number) != 10:
            return []
        try:
            found = await self._directory.find_by_phone(number)
        except Exception:  # noqa: BLE001 — kardeş bilgisi süs; okunamazsa boş
            return []
        return [{"id": str(row.get("id") or ""), "displayName": str(row.get("displayName") or ""),
                 "className": str(row.get("className") or "")}
                for row in found if str(row.get("id") or "") != kantin_id][:10]

    async def set_guardians(self, kantin_id: str, items: Any, *, actor: str = "",
                            causation_id: str | None = None) -> dict[str, Any]:
        """Veli listesini TÜMÜYLE yazar (sıra = öncelik, ilk veli birincil).

        Kural `guardians.py`; yazma `update_student` yolundan geçer (önce KM, sonra
        kantin; yuva 1 telefonu kantine; olaylar orada). Hiçbir şey değişmediyse
        yazılmaz ve olay çıkmaz.
        """
        clean = guardian_rules.clean_items(items)
        current = await self.get_student(kantin_id)
        if current is None:
            raise LookupError("Öğrenci bulunamadı.")
        before = [(row["name"], row["phone"], row["relation"]) for row in current["guardians"]]
        after = [(row["name"], row["phone"], row["relation"]) for row in clean]
        if before == after:
            return {"kantinId": kantin_id, "changed": False, "canteenSync": current["canteenSync"],
                    "guardians": current["guardians"]}
        payload = guardian_rules.to_payload(clean)
        # Veli kimliği (göç 014) telefonla anahtarlıdır: telefonu düzeltilen velinin kimliği
        # yeni telefona taşınır, listeden çıkan velinin kimliği silinir — aynı işlemde.
        moved = identity_rules.rekey_guardians(
            await self._sync.profile(kantin_id),
            [normalize_phone(row["phone"]) or str(row["phone"]) for row in current["guardians"]],
            [row["phone"] for row in clean])
        if moved is not None:
            payload["guardianIdentity"] = moved
        # Kantine yalnız 1. veli telefonu DEĞİŞTİYSE gidilir (tabletin yazdığına dokunma).
        if (normalize_phone(current.get("parentPhone"))
                or str(current.get("parentPhone") or "")) == payload["parentPhone"]:
            payload.pop("parentPhone")
        result = await self.update_student(kantin_id, payload, actor=actor,
                                           causation_id=causation_id)
        fresh = await self.get_student(kantin_id)
        self._log.info("veliler güncellendi", student=kantin_id, count=len(clean), actor=actor)
        return {**result, "changed": True,
                "guardians": fresh["guardians"] if fresh else []}

    # ------------------------------------------- sözleşme kimliği (göç 014)

    async def _seal_pair(self, body: dict[str, Any]) -> tuple[Any, Any]:
        """Gövde TC / yabancı kimlik taşıyorsa şifre işlevleri; taşımıyorsa (None, None).

        Kasa okunamazsa `CipherUnavailable` — TC düz metne düşülmez, yazma reddedilir.
        """
        people = body.get("guardians") if isinstance(body.get("guardians"), list) else []
        student = body.get("student") if isinstance(body.get("student"), dict) else {}
        touches = any(key in student for key in ("nationalId", "foreignId")) or any(
            isinstance(row, dict) and ("nationalId" in row or "foreignId" in row)
            for row in people)
        if not touches:
            return None, None
        if self._cipher is None:
            raise identity_rules.CipherUnavailable("Kimlik anahtarı okunamadı: sır kasası yok.")
        return await self._cipher.pair()

    async def _identity_log(self, kantin_id: str, actor: str, action: str,
                            fields: list[str]) -> None:
        """Denetim izi: kim, ne zaman, HANGİ ALANLAR — değer yazılmaz (KVKK)."""
        await self._store.execute(
            f"INSERT INTO {self._store.table('identity_log')} (kantin_id, at, actor, action, "
            f"fields) VALUES (?, ?, ?, ?, ?)",
            (kantin_id, iso(datetime.now(UTC)), actor[:160], action,
             ",".join(dict.fromkeys(fields))[:1000]))

    async def get_identity(self, kantin_id: str, *, reveal: bool,
                           actor: str = "") -> dict[str, Any]:
        """Öğrenci kartının "Kimlik ve adres (sözleşme için)" bölümü.

        `reveal` (çağıranda `bbd_students.manage`): TC / yabancı kimlik TAM; değilse
        maskeli. Kasa okunamazsa maskeli döner + `cipherError`.
        """
        student = await self.get_student(kantin_id)
        if student is None:
            raise LookupError("Öğrenci bulunamadı.")
        profile = await self._sync.profile(kantin_id)
        unseal = None
        cipher_error = ""
        if reveal:
            try:
                if self._cipher is None:
                    raise identity_rules.CipherUnavailable("Sır kasası yok.")
                _, unseal = await self._cipher.pair()
            except identity_rules.CipherUnavailable as failure:
                cipher_error = f"Kimlik numaraları şu an açılamıyor ({failure})."
        view = identity_rules.edit_view(kantin_id, profile, student["guardians"], unseal=unseal,
                                        display_name=str(student.get("displayName") or ""))
        view["cipherError"] = cipher_error or None
        view["displayName"] = student.get("displayName") or ""
        if unseal is not None and (view["student"]["nationalId"] or any(
                row["nationalId"] for row in view["guardians"])):
            # Erişim izi (değer yok): tam kimlik no'yu kim, ne zaman açtı.
            self._log.info("kimlik bilgisi açıldı", student=kantin_id, actor=actor)
        return view

    async def set_identity(self, kantin_id: str, body: dict[str, Any], *,
                           actor: str = "") -> dict[str, Any]:
        """Kimlik ve adres bilgilerini yazar — YALNIZ verilen alanlar.

        Tek işlem: profil sütunları + revizyon + `student.updated` (`["identity"]`; veli
        adı değiştiyse `guardians` da) + denetim izi (alan adları). Kantine gidilmez.
        Akademi olayla 1–3 sn'de geçitten (`identity` bloğu) yeniden okur.
        """
        if await self._sync.tombstone(kantin_id) is not None:
            raise ValueError("Öğrenci silinmiş.")
        student = await self.get_student(kantin_id)
        if student is None:
            raise LookupError("Öğrenci bulunamadı.")
        existing = await self._sync.profile(kantin_id)
        seal, unseal = await self._seal_pair(body)
        columns, changed = identity_rules.apply(kantin_id, existing, student["guardians"], body,
                                                seal=seal, unseal=unseal)
        if not columns:
            return {"kantinId": kantin_id, "changed": False, "fields": []}
        columns["identity_updated_at"] = iso(datetime.now(UTC))
        fields: list[str] = []
        for column, value in columns.items():
            logical = LOGICAL_FIELDS.get(column)
            if logical and ((existing or {}).get(column) or "") != (value or "") \
                    and logical not in fields:
                fields.append(logical)
        async with self._store.transaction():
            revision = await self._sync.write(
                kantin_id, columns=columns,
                events=[Event("student.updated", sorted(fields))] if fields else [])
            await self._identity_log(kantin_id, actor, "updated", changed)
        self._log.info("kimlik bilgileri güncellendi", student=kantin_id, actor=actor,
                       fields=changed)
        return {"kantinId": kantin_id, "changed": True, "fields": changed, "revision": revision}

    async def set_class(self, kantin_ids: list[str], class_name: str, *,
                        actor: str = "") -> dict[str, Any]:
        """Toplu sınıf ataması (eski "Ana sınıf").

        SINIF = GRUP (göç 009): birleştirmeden sonra bu yol "SINIFA EKLE"dir —
        öğrenciler adı verilen sınıfa bugünden üye olur (yoksa sınıf açılır);
        başka sınıflarındaki üyelikleri SÜRER (ana sınıf yok). Boş ad reddedilir
        (sınıftan çıkarmak sınıfın üyelik ekranındandır). Birleştirme öncesi
        (ya da geri alındıysa) eski davranış: tek ana sınıf, her öğrenci ayrı
        `student.updated` (`className`); boş ad temizler.
        """
        ids = [str(item).strip() for item in kantin_ids if str(item).strip()]
        if not ids:
            raise ValueError("Öğrenci seçilmedi.")
        if self._group_service is not None and await self.own_level():
            return await self._join_class(ids, class_name, actor=actor or "Öğrenci Yönetimi")
        # Var olan sınıf adı olduğu gibi (ör. canlıdaki "Mezun"), yenisi normalize.
        name = await self._class_key(class_name)
        updated: list[str] = []
        failed: list[dict[str, str]] = []
        for kantin_id in dict.fromkeys(ids):
            if await self._sync.tombstone(kantin_id) is not None:
                failed.append({"studentId": kantin_id, "error": "Öğrenci silinmiş."})
                continue
            await self._save_profile(kantin_id, {"className": name})
            updated.append(kantin_id)
        self._log.info("toplu sınıf ataması", updated=len(updated), failed=len(failed))
        return {"updated": updated, "failed": failed, "className": name}

    async def _join_class(self, ids: list[str], class_name: str, *,
                          actor: str) -> dict[str, Any]:
        """Öğrencileri adı verilen CANLI sınıfa bugünden ekler; sınıf yoksa açar."""
        trimmed = " ".join(str(class_name or "").split())
        if not trimmed:
            raise ValueError("Sınıf adı boş olamaz.")
        row = await self._store.fetch_one(
            f"SELECT id, name FROM {self._groups} WHERE name_fold = ? AND archived_at = '' "
            f"ORDER BY created_at LIMIT 1", (fold(trimmed),))
        failed: list[dict[str, str]] = []
        live: list[str] = []
        for kantin_id in dict.fromkeys(ids):
            if await self._sync.tombstone(kantin_id) is not None:
                failed.append({"studentId": kantin_id, "error": "Öğrenci silinmiş."})
            else:
                live.append(kantin_id)
        if not live:
            return {"updated": [], "failed": failed, "className": trimmed}
        if row is None:
            record = await self._group_service.create(
                {"name": normalize_class(trimmed) if _CLASS.match(trimmed.upper()) else trimmed,
                 "studentIds": live}, actor=actor)
            name, added = str(record["name"]), live
        else:
            result = await self._group_service.add_members(str(row["id"]), live, actor=actor)
            name = str(row["name"])
            added = [student for student in live if student not in result["skipped"]]
        self._log.info("toplu sınıfa ekleme", sinif=name, added=len(added), failed=len(failed))
        return {"updated": added, "failed": failed, "className": name,
                "skipped": [student for student in live if student not in added]}

    async def reset_access_code(self, kantin_id: str) -> dict[str, Any]:
        """Öğrenciye tek tuşla yeni giriş kodu verir.

        KOD BURADA ÜRETİLMEZ, kantinden istenir (K4: tek kapı). Sebep tekillik:
        "kimsede olmayan" güvencesini yalnızca kantinin `access_code_hash` unique
        indeksi verebilir. Kontrol Merkezi kendi rastgele sayısını üretip
        gönderseydi, iki yönetici aynı anda aynı sayıyı seçebilir ve kodun hangi
        öğrenciyi açtığı belirsizleşirdi.

        Düz kod YALNIZ bu yanıtta döner; yerel tabloya YAZILMAZ. Yazılsaydı
        Kontrol Merkezi'nin veritabanı, kantinin bilerek şifreli tuttuğu bir sırrı
        düz metin taşıyor olurdu.
        """
        result = await self._canteen.issue_student_access_code(kantin_id)
        code = str(result.get("accessCode") or "")
        if not code:
            raise ValueError("Kantin kod döndürmedi.")

        # Denetim izi: KOD YAZILMAZ, yalnız "kim ne zaman sıfırladı" bilgisi.
        self._log.info("öğrenci giriş kodu sıfırlandı", kantin_id=kantin_id)

        return {
            "kantinId": kantin_id,
            "studentName": str(result.get("studentName") or ""),
            "accessCode": code,
        }

    async def access_code_list(self, kantin_ids: list[str]) -> dict[str, Any]:
        """Seçili öğrencilerin ŞU ANKİ kodlarını döner — HİÇBİR ŞEY DEĞİŞTİRMEZ.

        Kodu olmayan öğrenci ayrı bir `missing` listesinde döner (bu akış
        "olduğu gibi göster" akışıdır, eksik kod burada tamamlanmaz — onun
        yolu `reset_access_codes`'tur).
        """
        wanted = {str(item) for item in kantin_ids}
        merged = (await self.list_students())["students"]
        chosen = [row for row in merged if row["kantinId"] in wanted]
        chosen.sort(key=lambda row: str(row.get("displayName") or "").casefold())

        codes: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for row in chosen:
            entry = {
                "studentId": row["kantinId"],
                "studentName": row.get("displayName") or "",
                "className": row.get("className") or "",
            }
            code = str(row.get("accessCode") or "")
            if code:
                codes.append({**entry, "accessCode": code})
            else:
                missing.append(entry)

        return {"codes": codes, "missing": missing}

    async def reset_access_codes(self, kantin_ids: list[str]) -> dict[str, Any]:
        """Seçili öğrencilerin HEPSİNE yeni kod verir. Boş seçim reddedilir.

        "Boş = tümü" YOK — kart PDF'indeki `CardsBody` davranışının kasıtlı
        tersi: burada boş liste yanlışlıkla tüm okulun kodunu sıfırlama
        riski taşır.

        Her öğrenci `reset_access_code`'un AYNI tekli kantin ucuna sırayla
        bağlanır (o metodun tekillik/güvenlik gerekçesi burada da geçerli).
        Tek bir öğrenci başarısız olursa döngü DURMAZ — kalanlar denenir,
        başarısız olan `failed` listesinde raporlanır.
        """
        ids = [str(item) for item in kantin_ids if str(item).strip()]
        if not ids:
            raise ValueError("Sıfırlanacak öğrenci seçilmedi.")

        class_by_id = {
            row["kantinId"]: row.get("className") or ""
            for row in (await self.list_students())["students"]
        }

        issued: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for index, kantin_id in enumerate(ids):
            if index:
                await asyncio.sleep(RESET_DELAY_SECONDS)
            try:
                result = await self.reset_access_code(kantin_id)
            except Exception as failure:  # noqa: BLE001 — kantin dışarısı, diğerleri devam etsin
                failed.append({"studentId": kantin_id, "error": str(failure)})
                continue
            issued.append({
                "studentId": result["kantinId"],
                "studentName": result["studentName"],
                "className": class_by_id.get(kantin_id, ""),
                "accessCode": result["accessCode"],
            })

        issued.sort(key=lambda row: str(row.get("studentName") or "").casefold())
        return {"issued": issued, "failed": failed}

    async def set_curriculum(self, kantin_ids: list[str],
                             patch: dict[str, Any]) -> dict[str, Any]:
        """Seçili öğrencilere seviye/alan/hedef sınav yazar (toplu işlem).

        Yalnız GÖNDERİLEN alan değişir. Kural her öğrenci için ayrı uygulanır:
        biri reddedilirse (ör. 9. sınıfa alan) diğerleri yazılır, reddedilen
        `failed` listesinde gerekçesiyle döner. Boş seçim reddedilir.
        """
        ids = [str(item).strip() for item in kantin_ids if str(item).strip()]
        if not ids:
            raise ValueError("Öğrenci seçilmedi.")
        clean = _curriculum_patch(patch)
        if not clean:
            raise ValueError("Değiştirilecek alan yok.")
        # Biçim hatası (ör. "13") tüm seçim için aynıdır: baştan reddedilir.
        curriculum.resolve({}, {key: value for key, value in clean.items() if key != "track"})
        if "track" in clean:
            curriculum.parse_track(clean["track"])

        updated: list[str] = []
        failed: list[dict[str, str]] = []
        plans = await self.class_plans()
        own = await self.own_level()
        for kantin_id in dict.fromkeys(ids):
            body = dict(clean)
            profile = await self._sync.profile(kantin_id)
            name = (profile or {}).get("class_name") or ""
            if not own and name and plans.get(name) is not None \
                    and ({"track", "examTarget"} & set(body)):
                # Sınıfı seviyeli öğrencide alan / hedef sınav öğrenciye özel İSTİSNA olur.
                body["levelOverride"] = True
            try:
                await self._save_profile(kantin_id, body)
            except curriculum.CurriculumError as failure:
                failed.append({"studentId": kantin_id, "error": str(failure)})
                continue
            updated.append(kantin_id)
        # Her öğrenci ayrı `student.updated` (km-merkezi-senkron §2.2).
        self._log.info("toplu seviye/alan", updated=len(updated), failed=len(failed))
        return {"updated": updated, "failed": failed}

    async def _save_profile(self, kantin_id: str, payload: dict[str, Any]) -> None:
        """Yalnız KM alanları (kantine gidilmez) + `student.updated`, tek işlem."""
        existing = await self._sync.profile(kantin_id)
        columns = self._profile_columns(existing, payload)
        await self._guard_level(existing, columns, payload)
        if not columns:
            return
        fields: list[str] = []
        for column, value in columns.items():
            logical = LOGICAL_FIELDS.get(column)
            if logical and ((existing or {}).get(column) or "") != (value or "") \
                    and logical not in fields:
                fields.append(logical)
        events = await self._effective_level_events(
            existing, columns, [Event("student.updated", fields)] if fields else [])
        await self._sync.write(kantin_id, columns=columns, events=events)

    # ------------------------------------------------- sınıf seviyeyi taşır (göç 008)

    async def class_plans(self, name: str | None = None) -> dict[str, dict[str, Any] | None]:
        """ESKİ sınıf adı → seviye planı (seviyesiz → None). `name` verilirse yalnız o sınıf.

        Birleştirmeden sonra kaynak sınıf kaydıdır (`legacy_class_name`); göç 008
        tanımı yalnız kaydı olmayan adlar için.
        """
        if name is None:
            rows = await self._store.fetch_all(f"SELECT * FROM {self._classes}")
            legacy = await self._store.fetch_all(
                f"SELECT * FROM {self._groups} WHERE legacy_class_name != ''")
        else:
            rows = await self._store.fetch_all(f"SELECT * FROM {self._classes} WHERE name = ?",
                                               (name,))
            legacy = await self._store.fetch_all(
                f"SELECT * FROM {self._groups} WHERE legacy_class_name = ?", (name,))
        plans = {str(row["name"]): curriculum.class_from_row(row) for row in rows}
        if legacy and await self.own_level():
            for row in sorted(legacy, key=lambda item: bool(item["archived_at"]), reverse=True):
                plans[str(row["legacy_class_name"])] = curriculum.class_from_row(row)
        return plans

    async def _guard_level(self, existing: dict[str, Any] | None, columns: dict[str, Any],
                           payload: dict[str, Any]) -> None:
        """Sınıfının seviyesi tanımlı öğrencide seviye öğrenciye yazılmaz (sınıftan gelir)."""
        if "gradeLevel" not in payload or await self.own_level():
            # Birleştirmeden sonra seviye ÖĞRENCİDEDİR: kartta doğrudan yazılır.
            return
        name = columns.get("class_name", (existing or {}).get("class_name") or "")
        if name and (await self.class_plans(name)).get(name) is not None:
            raise curriculum.CurriculumError(
                f"Seviye sınıftan gelir ({name}). Sınıfın seviyesini Sınıflar ekranında değiştirin; "
                "öğrenciye özel yalnız alan ve hedef sınav istisnası girilir.")

    async def _effective_level_events(self, existing: dict[str, Any] | None,
                                      columns: dict[str, Any], events: list[Event]) -> list[Event]:
        """`student.updated` alanlarında seviye/alan/hedef sınav GEÇERLİ değerin değişimine göre."""
        before_name = (existing or {}).get("class_name") or ""
        after_name = columns.get("class_name", before_name) or ""
        level_columns = {"grade_level", "track", "exam_target", "level_override"}
        if before_name == after_name and not level_columns & set(columns):
            return events
        plans: dict[str, dict[str, Any] | None] = {}
        if not await self.own_level():
            for name in {before_name, after_name} - {""}:
                plans.update(await self.class_plans(name))
        before = curriculum.effective(existing, plans.get(before_name) if before_name else None)
        after = curriculum.effective({**(existing or {}), **columns},
                                     plans.get(after_name) if after_name else None)
        changed = [key for key in ("gradeLevel", "track", "examTarget") if before[key] != after[key]]
        out: list[Event] = []
        placed = False
        for event in events:
            if event.type == "student.updated":
                fields = [f for f in event.fields if f not in ("gradeLevel", "track", "examTarget")]
                fields += changed
                placed = True
                if fields:
                    out.append(Event("student.updated", fields, event.data))
            else:
                out.append(event)
        if not placed and changed:
            out.append(Event("student.updated", changed))
        return out

    async def list_classes(self) -> dict[str, Any]:
        """Sınıflar ekranı: tanımlı sınıflar ∪ öğrencilerde kullanılan sınıf adları.

        Birleştirmeden (göç 009) sonra kaynak CANLI sınıf kayıtlarıdır (eski
        gruplar dahil, tek liste); öğrenci sayısı bugünkü üyelerdir.
        """
        if self._group_service is not None and await self.own_level():
            overview = await self._group_service.overview()
            merged_items = []
            for group in overview["groups"]:
                plan = group.get("classLevel")
                merged_items.append({
                    "id": group["id"], "name": group["name"],
                    "gradeLevel": plan["gradeLevel"] if plan else None,
                    "track": plan["track"] if plan else None,
                    "examTarget": plan["examTarget"] if plan else None,
                    "defined": plan is not None, "studentCount": group["memberCount"],
                    "label": curriculum.class_label(group["name"], plan),
                    "suggestion": None if plan else curriculum.suggest(group["name"]),
                    "updatedAt": group["updatedAt"],
                })
            merged_items.sort(key=lambda item: _class_sort_key(item["name"]))
            return {"items": merged_items,
                    "undefinedCount": sum(1 for item in merged_items
                                          if not item["defined"] and item["studentCount"])}
        rows, counts = await asyncio.gather(
            self._store.fetch_all(f"SELECT * FROM {self._classes}"),
            self._store.fetch_all(
                f"SELECT class_name, COUNT(*) AS n FROM {self._table} "
                f"WHERE class_name != '' AND status != 'inactive' GROUP BY class_name"),
        )
        defined = {str(row["name"]): row for row in rows}
        count = {str(row["class_name"]): int(row["n"] or 0) for row in counts}
        names = sorted(set(defined) | set(count), key=_class_sort_key)
        items = []
        for name in names:
            plan = curriculum.class_from_row(defined.get(name))
            items.append({
                "name": name,
                "gradeLevel": plan["gradeLevel"] if plan else None,
                "track": plan["track"] if plan else None,
                "examTarget": plan["examTarget"] if plan else None,
                "defined": plan is not None,
                "studentCount": count.get(name, 0),
                "label": curriculum.class_label(name, plan),
                "suggestion": None if plan else curriculum.suggest(name),
                "updatedAt": str((defined.get(name) or {}).get("updated_at") or "") or None,
            })
        return {"items": items, "undefinedCount": sum(1 for item in items if not item["defined"]
                                                      and item["studentCount"])}

    async def set_class_level(self, class_name: str, patch: dict[str, Any], *,
                              actor: str = "") -> dict[str, Any]:
        """Sınıfın seviyesi / alanı / hedef sınavı. Sınıftaki her öğrencinin GEÇERLİ değeri
        değiştiyse öğrenci başına `student.updated` (Akademi müfredatı yeniden çözer).

        Birleştirmeden sonra: adı verilen canlı sınıfın seviyesi yazılır ve
        bugünkü üyelerin KENDİ seviyesine uygulanır (`GroupService.update`).
        """
        if self._group_service is not None and await self.own_level():
            group_id = await self._live_class_id(class_name)
            current = await self._group_service.detail(group_id)
            level = dict(current.get("classLevel") or {})
            level.update({key: patch[key] for key in ("gradeLevel", "track", "examTarget")
                          if key in patch})
            before = len(await self._group_service.current_students(group_id))
            record = await self._group_service.update(group_id, {"level": level},
                                                      actor=actor or "Öğrenci Yönetimi")
            plan = record.get("classLevel")
            return {"name": record["name"], **(plan or {}),
                    "label": curriculum.class_label(record["name"], plan),
                    "updatedStudents": before}
        name = await self._class_key(class_name)
        if not name:
            raise ValueError("Sınıf adı boş olamaz.")
        rows = await self._store.fetch_all(f"SELECT * FROM {self._classes} WHERE name = ?", (name,))
        current = rows[0] if rows else None
        row = curriculum.resolve_class(current, patch)
        stamp = iso(datetime.now(UTC))
        if current is None:
            await self._store.execute(
                f"INSERT INTO {self._classes} (name, grade_level, track, exam_target, created_at, "
                f"updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, row["grade_level"], row["track"], row["exam_target"], stamp, stamp, actor[:160]))
        else:
            await self._store.execute(
                f"UPDATE {self._classes} SET grade_level = ?, track = ?, exam_target = ?, "
                f"updated_at = ?, updated_by = ? WHERE name = ?",
                (row["grade_level"], row["track"], row["exam_target"], stamp, actor[:160], name))
        updated = await self._announce_class(name, curriculum.class_from_row(current),
                                             curriculum.class_from_row(row))
        self._changed(canteen=False)
        self._log.info("sınıf seviyesi", sinif=name, ogrenci=updated)
        plan = curriculum.class_from_row(row)
        return {"name": name, **(plan or {}), "label": curriculum.class_label(name, plan),
                "updatedStudents": updated}

    async def clear_class_level(self, class_name: str) -> dict[str, Any]:
        """Sınıf tanımını kaldırır: öğrenciler kendi (eski) değerlerine döner.

        Birleştirmeden sonra yalnız sınıfın seviyesi (şablon) kalkar; öğrencinin
        kendi seviyesi değişmez.
        """
        if self._group_service is not None and await self.own_level():
            group_id = await self._live_class_id(class_name)
            record = await self._group_service.update(group_id, {"level": None},
                                                      actor="Öğrenci Yönetimi")
            return {"name": record["name"], "updatedStudents": 0}
        name = await self._class_key(class_name)
        rows = await self._store.fetch_all(f"SELECT * FROM {self._classes} WHERE name = ?", (name,))
        if not rows:
            raise LookupError("Sınıf tanımı yok.")
        await self._store.execute(f"DELETE FROM {self._classes} WHERE name = ?", (name,))
        updated = await self._announce_class(name, curriculum.class_from_row(rows[0]), None)
        self._changed(canteen=False)
        return {"name": name, "updatedStudents": updated}

    async def _live_class_id(self, class_name: str) -> str:
        trimmed = " ".join(str(class_name or "").split())
        row = await self._store.fetch_one(
            f"SELECT id FROM {self._groups} WHERE (name_fold = ? OR legacy_class_name = ?) "
            f"AND archived_at = '' ORDER BY created_at LIMIT 1", (fold(trimmed), trimmed))
        if row is None:
            raise LookupError("Sınıf bulunamadı.")
        return str(row["id"])

    async def _class_key(self, class_name: str) -> str:
        """Var olan sınıf adı olduğu gibi (canlıdaki "Mezun" gibi elle yazılmış adlar), yenisi
        `normalize_class` ile ("11a" → "11-A")."""
        trimmed = " ".join(str(class_name or "").split())
        if not trimmed:
            return ""
        if await self._store.fetch_all(f"SELECT name FROM {self._classes} WHERE name = ?",
                                       (trimmed,)):
            return trimmed
        used = await self._store.fetch_all(
            f"SELECT kantin_id FROM {self._table} WHERE class_name = ? LIMIT 1", (trimmed,))
        return trimmed if used else normalize_class(trimmed)

    async def _announce_class(self, name: str, before: dict[str, Any] | None,
                              after: dict[str, Any] | None) -> int:
        """Sınıftaki öğrencilerin geçerli değeri değiştiyse öğrenci başına `student.updated`."""
        members = await self._store.fetch_all(
            f"SELECT * FROM {self._table} WHERE class_name = ?", (name,))
        updated = 0
        for profile in members:
            kantin_id = str(profile["kantin_id"])
            if await self._sync.tombstone(kantin_id) is not None:
                continue
            old = curriculum.effective(profile, before)
            new = curriculum.effective(profile, after)
            fields = [key for key in ("gradeLevel", "track", "examTarget") if old[key] != new[key]]
            if fields:
                await self._sync.write(kantin_id, columns={},
                                       events=[Event("student.updated", fields)])
                updated += 1
        return updated

    # ------------------------------------------------------------ kartlar

    async def build_cards(self, kantin_ids: list[str] | None = None) -> dict[str, Any]:
        """Seçili (ya da tüm) öğrenciler için A4 kart PDF'i üretir.

        Kart QR'ı kasadakiyle BİT-UYUMLUDUR: aynı anahtar, aynı kodlayıcı.
        Anahtar kantinden gelir ve arayüze hiç inmez.
        """
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
        name = f"kantin-kartlari-{len(cards)}-{datetime.now(UTC).astimezone().strftime('%Y%m%d-%H%M')}.pdf"
        # Kart PDF'i öğrenci adı ve QR kimliği taşır — yalnız kullanıcıya okunur.
        path = write_private(self._export_dir / name, content)

        self._log.info("kart pdf üretildi", count=len(cards), path=str(path))
        return {"ok": True, "count": len(cards), "name": name,
                "path": str(path), "bytes": len(content)}

    # ------------------------------------------------------ önizleme/baskı

    async def export(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        """PDF üretir, `data/exports/` altına yazar ve yolunu döner.

        `kind` ∈ {"cards", "access-code-list", "access-code-reset"}. Tek
        dispatcher — `preview()` ve doğrudan `/export` çağrısı aynı yoldan
        geçer, ikisi ayrışmasın diye.
        """
        students = [str(item) for item in (params.get("students") or [])]

        if kind == "cards":
            return await self.build_cards(students)
        if kind == "access-code-list":
            return await self._export_code_list(students, reset=False)
        if kind == "access-code-reset":
            return await self._export_code_list(students, reset=True)
        return {"ok": False, "error": f"Bilinmeyen çıktı türü: {kind}"}

    async def _export_code_list(self, kantin_ids: list[str], *, reset: bool) -> dict[str, Any]:
        if not kantin_ids:
            return {"ok": False, "error": "Öğrenci seçilmedi."}

        if reset:
            try:
                result = await self.reset_access_codes(kantin_ids)
            except ValueError as failure:
                return {"ok": False, "error": str(failure)}
            rows = result["issued"]
            failed = result["failed"]
            missing: list[dict[str, Any]] = []
        else:
            result = await self.access_code_list(kantin_ids)
            rows = result["codes"]
            missing = result["missing"]
            failed = []

        if not rows:
            reason = "Sıfırlanacak öğrenci kalmadı." if reset else "Seçilenlerin hiçbirinin kodu yok."
            return {"ok": False, "error": reason}

        title = "Kantin Giriş Kodları — Yeni Üretildi" if reset else "Kantin Giriş Kodları"
        note = (
            "Bu kodlar ÜRETİLDİĞİ an geçerlidir; eski kodlar geçersizdir."
            if reset else
            "Bu liste kantinde hâlâ geçerli olan kodları gösterir; hiçbir kod değiştirilmedi."
        )

        from .code_list import CodeListError, build_code_list_pdf
        try:
            content = build_code_list_pdf(rows, title=title, note=note)
        except CodeListError as failure:
            return {"ok": False, "error": str(failure)}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        tag = "sifirlama" if reset else "liste"
        name = f"kantin-sifre-{tag}-{len(rows)}-{stamp}.pdf"
        # Şifre listesi öğrenci adı ve giriş kodu taşır — yalnız kullanıcıya okunur.
        path = write_private(self._export_dir / name, content)

        self._log.info("şifre listesi pdf üretildi", reset=reset, count=len(rows), path=str(path))
        return {
            "ok": True, "count": len(rows), "name": name, "path": str(path), "bytes": len(content),
            "missing": missing, "failed": failed,
        }

    async def preview(self, kind: str, params: dict[str, Any], *,
                      max_pages: int = 12, dpi: int = 110) -> dict[str, Any]:
        """Çıktıyı üretir ve sayfalarını GÖRÜNTÜ olarak döner.

        Önizleme, basılacak PDF'in BİREBİR kendisidir: dosya `pdftoppm` ile
        sayfa sayfa PNG'ye çevrilir (bbd_canteen_reports/backend/service.py
        ile aynı desen — K3 gereği import edilemez, buraya kopyalanmıştır).
        """
        produced = await self.export(kind, params)
        if not produced.get("ok"):
            return produced

        path = Path(produced["path"])
        if path.suffix.lower() != ".pdf":
            return {**produced, "pages": [], "pageCount": 0,
                    "previewError": "Bu çıktı PDF değil; önizlenemez."}

        try:
            pages, total = await self._render_pages(path, max_pages=max_pages, dpi=dpi)
        except PreviewError as failure:
            return {**produced, "pages": [], "pageCount": 0, "previewError": str(failure)}

        return {**produced, "pages": pages, "pageCount": total,
                "shown": len(pages), "previewError": ""}

    async def _render_pages(self, path: Path, *, max_pages: int,
                            dpi: int) -> tuple[list[str], int]:
        """PDF sayfalarını PNG data-URI listesine çevirir."""
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` bulunamadı (poppler-utils kurulu olmalı). "
                "Dosya yine de kaydedildi ve yazdırılabilir."
            )

        with tempfile.TemporaryDirectory(prefix="km-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            args = [binary, "-png", "-r", str(int(dpi)),
                    "-f", "1", "-l", str(int(max_pages)), str(path), str(target)]
            try:
                process = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise PreviewError("Önizleme üretilemedi: süre aşıldı.") from None
            except OSError as failure:
                raise PreviewError(f"Önizleme üretilemedi: {failure}") from failure

            if process.returncode != 0:
                raise PreviewError(
                    f"Önizleme üretilemedi: {err.decode(errors='replace').strip() or 'bilinmeyen hata'}"
                )

            files = sorted(Path(folder).glob("sayfa*.png"))
            pages = [
                "data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                for item in files
            ]

        return pages, await self._page_count(path) or len(pages)

    @staticmethod
    async def _page_count(path: Path) -> int:
        """PDF'in gerçek sayfa sayısı — önizlemede kaç sayfanın gizlendiğini söylemek için."""
        binary = shutil.which("pdfinfo")
        if not binary:
            return 0
        try:
            process = await asyncio.create_subprocess_exec(
                binary, str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except (TimeoutError, OSError):
            return 0
        match = re.search(r"^Pages:\s+(\d+)", out.decode(errors="replace"), re.MULTILINE)
        return int(match.group(1)) if match else 0

    # Yazdırma adımı BURADA BİTMEZ: baskı artık kullanıcının cihazında
    # yapılıyor (ADR 0026), sunucuda CUPS yok. Kabuk üretilen PDF'i
    # çekirdeğin genel `POST /api/outputs/document` ucundan indirip yerel
    # yazıcıya veriyor (`apps/desktop/shell/ui-kit/printing.js` →
    # `printDocument`). Modüle özel bir `/print` ucu KASITLI OLARAK yok —
    # bbd_canteen_reports'ta bu uç vardı ve sunucuda CUPS bulunamadığı için
    # her tıklamada hata veriyordu (bkz. o modülün ui/panel/index.js'indeki
    # "Eskiden .../print çağrılıyordu" notu).


# ------------------------------------------------------------------ yardım


def _split_name(display_name: str) -> tuple[str, str]:
    """Kantin tek alan tutuyor; profil yoksa son kelimeyi soyad sayarız."""
    return split_name(display_name)


#: NOT NULL '' tutan metin sütunları (telafide boş metne döner).
_TEXT_COLUMNS = frozenset(PROFILE_COLUMNS.values()) | {"identity_updated_at"}


_CLASS = re.compile(r"^(\d{1,2})\s*[-/.]?\s*([A-ZÇĞİÖŞÜ]?)$")


def _class_sort_key(name: str) -> tuple[int, str]:
    """"5-A" < "9-B" < "11-SAY" < "Mezun" (rakamla başlamayan sonda)."""
    digits = ""
    for char in name:
        if not char.isdigit():
            break
        digits += char
    return (int(digits) if digits else 999, name.casefold())


def normalize_class(value: Any) -> str:
    """"11a" → "11-A", "  9 / b " → "9-B"; tanınmayan ad büyük harfle, kırpılarak
    kalır (ekrandaki `normalizeClass` ile aynı kural)."""
    raw = " ".join(str(value or "").split())
    if not raw:
        return ""
    upper = raw.replace("i", "İ").replace("ı", "I").upper()
    match = _CLASS.match(upper)
    if not match:
        return upper[:CLASS_NAME_MAX]
    return f"{match.group(1)}-{match.group(2)}" if match.group(2) else match.group(1)


def _extra_phones(profile: dict[str, Any] | None) -> list[str]:
    """Yuva 3.. telefonları (normalize; boş yuva ''), `MAX_GUARDIANS - 2` uzunlukta."""
    phones = [normalize_phone(item["phone"]) or item["phone"].strip()
              for item in guardian_rules.extra_items(profile)]
    return (phones + [""] * guardian_rules.MAX_GUARDIANS)[: guardian_rules.MAX_GUARDIANS - 2]


def _extra_people(profile: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Yuva 3.. ad + yakınlık (telefonsuz karşılaştırma: `student.updated` `guardians` için)."""
    return [(item["name"], item["relation"]) for item in guardian_rules.extra_items(profile)]


def _guardian_phone(record: dict[str, Any] | None, slot: int) -> str:
    for guardian in (record or {}).get("guardians") or []:
        if int(guardian.get("slot") or 0) == slot:
            return str(guardian.get("phone") or "")
    return ""


def _controls_view(student: dict[str, Any]) -> dict[str, Any]:
    """Kantin öğrencisinden yalnız kontrol ekranının alanları (`accessCode` yok)."""
    restricted = student.get("restrictedProductIds")
    return {
        "kantinId": student.get("id"),
        "balance": int(student.get("balance") or 0),
        "spendingLimit": student.get("spendingLimit"),
        "dailyLimit": student.get("dailyLimit"),
        "isBlocked": bool(student.get("isBlocked")),
        "guardianBlocked": bool(student.get("guardianBlocked")),
        "restrictedProductIds": sorted({int(item) for item in restricted})
        if isinstance(restricted, list) else [],
        "spentToday": student.get("spentToday"),
        "controlsUpdatedAt": student.get("controlsUpdatedAt"),
    }


def _curriculum_patch(payload: dict[str, Any]) -> dict[str, Any]:
    """Gövdeden yalnız seviye/alan/hedef sınav anahtarları (gönderilmişse)."""
    return {key: payload[key] for key in curriculum.CURRICULUM_COLUMNS if key in payload}


def _display_name(payload: dict[str, Any]) -> str:
    if payload.get("displayName"):
        return str(payload["displayName"]).strip()[:120]
    name = f"{payload.get('firstName') or ''} {payload.get('lastName') or ''}".strip()
    return name[:120]


def _new_opaque_id() -> str:
    """Kantin'in kendi biçimiyle aynı: STU- + 10 karakter (bkz. StudentFactory)."""
    return "STU-" + "".join(secrets.choice(OPAQUE_ALPHABET) for _ in range(10))
