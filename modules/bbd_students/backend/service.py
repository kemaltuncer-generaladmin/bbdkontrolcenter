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

#: Toplu şifre sıfırlamada iki çağrı arasındaki bekleme (saniye). Kantin
#: tarafındaki `auth:sanctum` grubu `throttle:120,1` sınırı taşıyor
#: (bbdkantin backend/routes/api.php); büyük seçimlerde (100+ öğrenci) bu
#: sınıra değmemek için sıralı çağrılar arasına küçük bir boşluk konur.
RESET_DELAY_SECONDS = 0.6


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Dosya yine de kaydedilmiştir."""


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

    @property
    def _export_dir(self) -> Path:
        """Çıktı klasörü — her çağrıda YENİDEN çözülür (ay değişince değişsin)."""
        return report_dir(
            self._category, fallback=self._fallback_dir,
            configured=str(self._config.get("export_path") or ""),
        )

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
            # "tek şifre" kipinin 6 haneli giriş kodu — kantin zaten listede
            # veriyor (bkz. bbd_canteen_api/backend/client.py:students()
            # yorumu); burada yalnızca geçiriliyor, üretilmiyor.
            "accessCode": canteen.get("accessCode") or "",
            "accessCodeIssuedAt": canteen.get("accessCodeIssuedAt"),
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
