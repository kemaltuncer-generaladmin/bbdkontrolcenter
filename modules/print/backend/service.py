"""Çıktı Merkezi servisi — kaydı okur, önizler, yeniden bastırır, budar.

BU MODÜL KAYIT YAZMAZ. Kayıt, dosyayı yazan çekirdek fonksiyonunda doğar
(ADR 0019 §1); burası yalnız okur, sayaç günceller ve ekranı besler. Yirmi
modülün ortak kaydı hiçbir modülün tablosu olamayacağı için `outputs` tablosu
`km_core/store` içindedir (§2) — modülün kendi `mod_print_*` tablosu yoktur ve
göçü de yoktur.

TABLOYA ERİŞİM TEK KAPIDAN GEÇER (K4): ham sqlite sürücüsü çağrılmaz,
`ctx.store` üzerinden gidilir. `users` tablosuna yalnız üreten kişinin ADINI
göstermek için LEFT JOIN ile bakılır; kimlik çekirdeğindir, burada
değiştirilmez.

DOSYA KAYBOLMUŞSA SATIR SİLİNMEZ (§4). "Ben bu raporu almıştım" diyen
kullanıcının hiçbir iz bulamaması en kötü sonuçtur: satır kalır, durumu
"dosya bulunamadı" olur, yeniden bas düğmesi kapanır ve nedeni yazılır.

SAKLAMA SÜRESİ KAYIT İÇİNDİR (§5): budama yalnız satırları siler. Kullanıcının
masaüstündeki dosyalarına dokunulmaz.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

#: Çekirdeğin ortak çıktı kaydı. Modülün kendi tablosu değildir (ADR 0019 §2).
OUTPUTS = "outputs"

#: Önizlemesi görüntüye çevrilebilen tür.
PAGE_KIND = "pdf"

#: Önizlemesi metin olarak gösterilebilen türler. Kalanı "önizlenemez" der;
#: uydurma bir gösterim yapılmaz.
TEXT_KINDS = ("csv", "txt", "json", "log", "md", "sql")

#: Yeniden basılabilen tür. `lp` başka türleri de kabul eder ama sonucu
#: öngörülemez; kullanıcıya "denendi ama çıkmadı" yaşatmamak için dar tutuldu.
PRINTABLE_KINDS = ("pdf",)

#: Klasörü açan işletim sistemi aracı. Bunun bir platform yeteneği karşılığı
#: yok; `printer` gibi bir kapıya bağlanacak ikinci bir tüketici çıkarsa
#: km_platform'a yükseltilir (ADR 0009'daki aynı ölçü).
_OPENERS = {"linux": "xdg-open", "darwin": "open", "win32": "explorer"}

_OPEN_TIMEOUT = 8.0
_PREVIEW_TIMEOUT = 60.0

MISSING_TEXT = "Dosya bulunamadı — taşınmış, yeniden adlandırılmış ya da silinmiş olabilir."
NOT_A_FILE_TEXT = "Yoldaki öğe bir dosya değil."
UNREADABLE_TEXT = "Dosya okunamıyor (izin)."
NO_PRINTER_TEXT = "Yazıcı yeteneği bu kurulumda yok."


def _now() -> str:
    """Yerel saat dilimi farkını KORUYAN damga (bkz. tests/integration)."""
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class OutputsService:
    def __init__(self, *, store: Any, log: Any, printer: Any = None,
                 config: dict[str, Any] | None = None) -> None:
        self._store = store
        self._log = log
        self._printer = printer
        self._config = config or {}

    # ------------------------------------------------------------- ayarlar

    @property
    def keep_days(self) -> int:
        return max(0, _int(self._config.get("keep_job_history_days"), 30))

    @property
    def max_copies(self) -> int:
        return max(1, _int(self._config.get("max_copies"), 20))

    # -------------------------------------------------------------- liste

    async def outputs(self, *, search: str = "", start: str = "", end: str = "",
                      kind: str = "", source: str = "", user: str = "",
                      page: int = 1, size: int = 50,
                      can_reprint: bool = False) -> dict[str, Any]:
        """Süzülmüş çıktı listesi + süzgeç seçenekleri.

        Seçenekler TÜM tablodan çıkarılır, süzülmüş kümeden değil: bir süzgeç
        kendi seçeneklerini eleseydi kullanıcı seçtiği değeri geri alamazdı.
        """
        where, params = _filter_sql(search=search, start=start, end=end,
                                    kind=kind, source=source, user=user)
        total_row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS count FROM {OUTPUTS} o {where}", params)
        total = int((total_row or {}).get("count") or 0)

        size = max(1, min(200, _int(size, 50)))
        page = max(1, _int(page, 1))
        rows = await self._store.fetch_all(
            # Üreten kişinin ADI kimlik çekirdeğinden okunur; kayıtta yalnız
            # kimliği durur. Yazma yok, yalnız gösterim.
            f"SELECT o.*, u.first_name, u.last_name FROM {OUTPUTS} o "
            f"LEFT JOIN users u ON u.id = o.user_id {where} "
            "ORDER BY o.created_at DESC, o.rowid DESC LIMIT ? OFFSET ?",
            (*params, size, (page - 1) * size),
        )

        items = [self._row_view(row) for row in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "size": size,
            "facets": await self._facets(),
            "canReprint": bool(can_reprint),
            "printerAvailable": self._printer is not None,
            "keepDays": self.keep_days,
            "maxCopies": self.max_copies,
            "missing": sum(1 for item in items if not item["exists"]),
        }

    async def _facets(self) -> dict[str, list[dict[str, Any]]]:
        sources = await self._store.fetch_all(
            f"SELECT source AS value, COUNT(*) AS count FROM {OUTPUTS} "
            "GROUP BY source ORDER BY source")
        kinds = await self._store.fetch_all(
            f"SELECT kind AS value, COUNT(*) AS count FROM {OUTPUTS} "
            "GROUP BY kind ORDER BY kind")
        users = await self._store.fetch_all(
            f"SELECT o.user_id AS value, COUNT(*) AS count, u.first_name, u.last_name "
            f"FROM {OUTPUTS} o LEFT JOIN users u ON u.id = o.user_id "
            "GROUP BY o.user_id ORDER BY u.first_name, u.last_name")
        return {
            "sources": [{"value": row["value"] or "", "label": row["value"] or "—",
                         "count": row["count"]} for row in sources],
            "kinds": [{"value": row["value"] or "", "label": (row["value"] or "—").upper(),
                       "count": row["count"]} for row in kinds],
            "users": [{"value": row["value"] or "", "label": _person(row), "count": row["count"]}
                      for row in users],
        }

    def _row_view(self, row: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(row["path"]))
        exists, reason = _file_state(path)
        kind = str(row["kind"] or "")
        return {
            "id": row["id"],
            "createdAt": row["created_at"],
            "title": row["title"] or path.name,
            "kind": kind,
            "source": row["source"],
            "path": str(path),
            "folder": str(path.parent),
            "bytes": row["bytes"],
            "pages": row["pages"],
            "digest": row["params_digest"],
            "printedCount": row["printed_count"] or 0,
            "lastPrintedAt": row["last_printed_at"],
            "userId": row["user_id"] or "",
            "user": _person(row),
            "exists": exists,
            "missingReason": reason,
            # Kapalı düğmenin nedeni olmak zorunda: "neden basamıyorum" sorusu
            # ekranda cevaplanır, kullanıcı denemeye zorlanmaz.
            "printable": kind in PRINTABLE_KINDS,
            "previewable": kind == PAGE_KIND or kind in TEXT_KINDS,
        }

    async def _row(self, output_id: str) -> dict[str, Any] | None:
        return await self._store.fetch_one(
            f"SELECT * FROM {OUTPUTS} WHERE id = ?", (output_id,))

    # ---------------------------------------------------------- önizleme

    async def preview(self, output_id: str) -> dict[str, Any]:
        """PDF ise sayfa görüntüleri, metin ise dosyanın başı.

        ÖNİZLEME DOSYANIN KENDİSİDİR: yeniden üretilmez. ADR 0019'un çıkış
        noktası tam buydu — kaynak veri değiştiyse yeniden üretilen rapor
        eskisiyle aynı değildir.
        """
        row = await self._row(output_id)
        if row is None:
            return {"ok": False, "error": "Kayıt bulunamadı."}

        path = Path(str(row["path"]))
        exists, reason = _file_state(path)
        if not exists:
            return {"ok": False, "error": reason}

        kind = str(row["kind"] or "")
        name = str(row["title"] or path.name)
        if kind == PAGE_KIND:
            try:
                pages = await self._render(path)
            except OSError as failure:
                return {"ok": False, "error": f"Önizleme üretilemedi: {failure}"}
            if not pages:
                return {"ok": False, "name": name,
                        "error": "Önizleme üretilemedi (poppler-utils kurulu olmayabilir). "
                                 "Dosya yerinde duruyor ve yazdırılabilir."}
            return {"ok": True, "mode": "pages", "name": name, "pages": pages}

        if kind in TEXT_KINDS:
            limit = max(1024, _int(self._config.get("preview_text_bytes"), 65536))
            try:
                raw = path.read_bytes()[:limit]
            except OSError:
                return {"ok": False, "error": UNREADABLE_TEXT}
            text = raw.decode("utf-8-sig", errors="replace")
            return {"ok": True, "mode": "text", "name": name, "text": text,
                    "truncated": (row["bytes"] or 0) > limit}

        return {"ok": False, "name": name,
                "error": f"“{kind or 'bu'}” türü önizlenemez; dosya klasöründen açılabilir."}

    async def _render(self, path: Path) -> list[str]:
        """PDF sayfalarını PNG data URL'lerine çevirir (`pdftoppm`).

        `pdftoppm` bu depoda birkaç modülde ayrı ayrı çağrılıyor; ADR 0019 bunu
        açıkça "bir kez yapılmış hata" diye anıyor. Çıktı Merkezi tüm çıktıların
        TEK önizleme yeri olduğu için doğru sadeleşme, o kopyaların zamanla
        buraya bırakılmasıdır — kopyaların temizliği ayrı iştir, burada
        kimsenin koduna dokunulmadı.
        """
        binary = "pdftoppm"
        pages = max(1, min(50, _int(self._config.get("preview_pages"), 8)))
        dpi = max(40, min(300, _int(self._config.get("preview_dpi"), 110)))

        with tempfile.TemporaryDirectory(prefix="km-cikti-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(dpi), "-f", "1", "-l", str(pages),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(),
                                                timeout=_PREVIEW_TIMEOUT)
            except TimeoutError:
                raise OSError("süre aşıldı") from None
            except FileNotFoundError:
                return []
            if process.returncode != 0:
                raise OSError(err.decode(errors="replace").strip() or "bilinmeyen hata")
            return [
                "data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                for item in sorted(Path(folder).glob("sayfa*.png"))
            ]

    # -------------------------------------------------------- yeniden bas

    async def reprint(self, output_id: str, *, copies: int = 1,
                      actor: str = "") -> dict[str, Any]:
        """Kayıtlı dosyayı yazıcıya gönderir ve sayacı işler.

        SAYAÇ "DENENDİ" SAYAR (ADR 0014/0019). Linux'ta sessizce basılır;
        Windows/macOS'ta yetenek `{mode: "system"}` döner, yani kâğıt değil
        SİSTEM YAZDIRMA PENCERESİ açılır ve kullanıcı iptal edebilir. Uygulama
        bunu bilemez; sayaç bu yüzden "basıldı" değil "denendi" demektir ve
        ekran da öyle yazar.

        Basılacak yol İSTEKTEN GELMEZ, kayıttan okunur: serbest yol kabul etmek
        makinedeki herhangi bir dosyayı kâğıda döktürmeye açık kapı bırakırdı.
        """
        row = await self._row(output_id)
        if row is None:
            return {"ok": False, "error": "Kayıt bulunamadı."}

        path = Path(str(row["path"]))
        exists, reason = _file_state(path)
        if not exists:
            return {"ok": False, "error": reason}
        if self._printer is None:
            return {"ok": False, "error": NO_PRINTER_TEXT}

        count = max(1, min(self.max_copies, _int(copies, 1)))
        try:
            result = await self._printer.print_file(path, title=path.name, copies=count)
        except Exception as failure:  # noqa: BLE001 — yazıcı dış dünyadır (K7)
            self._log.warning("yeniden baskı başarısız", output=output_id,
                              error=str(failure))
            return {"ok": False, "error": str(failure) or "Baskı gönderilemedi."}

        payload = dict(result or {})
        if payload.get("ok") is False:
            return {"ok": False, "error": str(payload.get("error") or "Baskı gönderilemedi.")}

        stamp = _now()
        await self._store.execute(
            f"UPDATE {OUTPUTS} SET printed_count = printed_count + 1, last_printed_at = ? "
            "WHERE id = ?",
            (stamp, output_id),
        )
        mode = str(payload.get("mode") or "queue")
        self._log.info("çıktı yeniden basıldı", output=output_id, mode=mode,
                       copies=count, actor=actor)
        return {
            "ok": True,
            "mode": mode,
            "path": str(path),
            "copies": count,
            "printer": payload.get("printer") or payload.get("label") or "",
            "lastPrintedAt": stamp,
            "printedCount": int(row["printed_count"] or 0) + 1,
            # Kabuğun ADR 0014 yolunu izleyebilmesi için: `system` kipinde
            # kâğıt çıkmadı, pencere açıldı.
            "attempted": True,
        }

    # ------------------------------------------------------- klasörü aç

    async def open_folder(self, output_id: str) -> dict[str, Any]:
        """Çıktının klasörünü işletim sisteminin dosya yöneticisinde açar.

        Dosya kaybolmuş olsa bile KLASÖR açılır: kullanıcı dosyayı oraya bakıp
        aramak ister. Yol yine kayıttan gelir, istekten değil.
        """
        row = await self._row(output_id)
        if row is None:
            return {"ok": False, "error": "Kayıt bulunamadı."}

        folder = Path(str(row["path"])).parent
        if not folder.is_dir():
            return {"ok": False,
                    "error": f"Klasör de yok: {folder}. Çıktı klasörü silinmiş olabilir."}

        opener = _OPENERS.get(sys.platform)
        if opener is None:
            return {"ok": False, "error": f"Bu platformda klasör açılamıyor: {sys.platform}"}

        try:
            process = await asyncio.create_subprocess_exec(
                opener, str(folder),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(process.communicate(), timeout=_OPEN_TIMEOUT)
        except (TimeoutError, OSError) as failure:
            return {"ok": False, "error": f"Klasör açılamadı: {failure}"}
        if process.returncode != 0:
            detail = err.decode(errors="replace").strip()
            return {"ok": False, "error": f"Klasör açılamadı: {detail or process.returncode}"}
        return {"ok": True, "folder": str(folder)}

    # ----------------------------------------------------------- yazıcı

    async def printer_status(self) -> dict[str, Any]:
        if self._printer is None:
            return {"ready": False, "error": NO_PRINTER_TEXT}
        try:
            return await self._printer.status()
        except Exception as failure:  # noqa: BLE001 — durum sorusu ekranı düşürmez (K7)
            return {"ready": False, "error": str(failure)}

    # ----------------------------------------------------------- budama

    async def prune(self) -> dict[str, Any]:
        """Süresi dolan KAYIT satırlarını siler — dosyalara dokunmaz (§5).

        Kullanıcının masaüstündeki klasörü uygulamanın alanı değildir. Budama
        yalnız listeyi kısaltır; silinen satırın dosyası yerinde durur.
        """
        days = self.keep_days
        if days <= 0:
            return {"ok": True, "removed": 0, "keepDays": 0}

        cutoff = (datetime.now(UTC).astimezone() - timedelta(days=days)).isoformat(
            timespec="seconds")
        row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS count FROM {OUTPUTS} WHERE created_at < ?", (cutoff,))
        removed = int((row or {}).get("count") or 0)
        if removed:
            await self._store.execute(
                f"DELETE FROM {OUTPUTS} WHERE created_at < ?", (cutoff,))
            self._log.info("çıktı kaydı budandı", removed=removed, keep_days=days)
        return {"ok": True, "removed": removed, "keepDays": days}


# ------------------------------------------------------------------ yardım


def _person(row: dict[str, Any]) -> str:
    """Üreten kişinin adı; kayıtta kimlik yoksa boş dizge.

    "Bilinmiyor" yazısı EKRANA aittir; servis uydurmaz.
    """
    name = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
    if name:
        return name
    return str(row.get("user_id") or "")


def _file_state(path: Path) -> tuple[bool, str]:
    """Dosya hâlâ orada mı — değilse NEDENİ ile birlikte."""
    try:
        if not path.exists():
            return False, MISSING_TEXT
        if not path.is_file():
            return False, NOT_A_FILE_TEXT
        if not os.access(path, os.R_OK):
            return False, UNREADABLE_TEXT
    except OSError as failure:
        return False, f"Dosyaya bakılamadı: {failure}"
    return True, ""


def _filter_sql(*, search: str, start: str, end: str, kind: str, source: str,
                user: str) -> tuple[str, tuple[Any, ...]]:
    """Süzgeçleri WHERE cümlesine çevirir. Boş süzgeç hiç sorulmaz."""
    clauses: list[str] = []
    params: list[Any] = []

    if search.strip():
        clauses.append("(o.title LIKE ? OR o.path LIKE ?)")
        needle = f"%{search.strip()}%"
        params += [needle, needle]
    if start.strip():
        clauses.append("substr(o.created_at, 1, 10) >= ?")
        params.append(start.strip())
    if end.strip():
        clauses.append("substr(o.created_at, 1, 10) <= ?")
        params.append(end.strip())
    if kind.strip():
        clauses.append("o.kind = ?")
        params.append(kind.strip())
    if source.strip():
        clauses.append("o.source = ?")
        params.append(source.strip())
    if user.strip():
        clauses.append("o.user_id = ?")
        params.append(user.strip())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, tuple(params)
