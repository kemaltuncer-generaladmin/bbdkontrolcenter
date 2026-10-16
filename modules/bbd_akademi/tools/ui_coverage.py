"""Panel kapsamı: izin listesindeki her işlem ekranda nerede?

KULLANICI KARARI: Akademi'nin her yönetim işi KM'den yapılabilir. Bu betik
bunu ölçer — `backend/allowlist.py`deki her işlem için panel dosyalarında
(`ui/panel/*.js`) bir karşılık arar:

  · YAZMA işlemi (GET dışı): işlem kimliği (`'admin_createExam'`) dosyada
    geçmelidir. Panelde her yazma düğmesi `actionButton(app, '<id>', …)` ya da
    `app.can('<id>')` ile çizildiği için kimlik orada zaten yazılıdır; yalnız
    yol eşleşmesi yetmez (aynı yolun GET'i ile karışır).
  · OKUMA işlemi (GET): kimlik ya da yol şablonu (`/v1/admin/exams/${id}` →
    `/v1/admin/exams/{}`) geçmelidir.

Yorumlar sayılmaz (başlık yorumunda adı geçen işlem ekranda yok demektir).

Ekranda BİLEREK yer almayan işlemler `NOT_SURFACED` tablosundadır ve her
birinin gerekçesi yazılıdır; tablo boş gerekçe ya da artık ekranda olan bir
işlem taşırsa test kırılır (`tests/test_akademi_panel_coverage.py`).

`PENDING` ayrı tablodur: sözleşmeye yeni girmiş, ekranı HENÜZ yazılmamış
işlemler (bilerek değil, sırada). Aynı kurallar geçerlidir — gerekçe yazılı,
ekrana gelen işlem tablodan çıkarılır; sayı raporda ayrıca görünür.

Kullanım (depo kökünden):

    .venv/bin/python modules/bbd_akademi/tools/ui_coverage.py [--all] [--prefix /v1/admin/exams]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]
PANEL = MODULE / "ui" / "panel"
REPO = MODULE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.operations import Operation

#: Ekranda bilerek yer almayan işlemler → gerekçe.
NOT_SURFACED: dict[str, str] = {
    # --- hata defteri gelen kutusu (Akademi §14 104)
    "admin_markMistakeInboxSeen":
        "Okundu işareti yalnız sorumlu öğretmenin kendi gelen kutusundadır (mobil / web 'Tümünü okundu say'); KM'nin bakması kaydı okundu yapmaz (Akademi §14 104).",
    # --- uzaktan tablet sınırları: KM'nin kendi Tablet modülü ekranlarında.
    "admin_listTabletProfiles":
        "KM'nin kendi Tablet modülü (bbd_tablet) profil ve öğrenciye özel süre ekranlarındadır; asıl kayıt orada (A5), Akademi bu uçlarla yalnız mobil/web yöneticiye vekillik eder (Akademi §14 94).",
    "admin_updateTabletProfile":
        "KM'nin kendi Tablet modülü (bbd_tablet) profil ve öğrenciye özel süre ekranlarındadır; asıl kayıt orada (A5), Akademi bu uçlarla yalnız mobil/web yöneticiye vekillik eder (Akademi §14 94).",
    "admin_setStudentTabletLimit":
        "KM'nin kendi Tablet modülü (bbd_tablet) profil ve öğrenciye özel süre ekranlarındadır; asıl kayıt orada (A5), Akademi bu uçlarla yalnız mobil/web yöneticiye vekillik eder (Akademi §14 94).",
    "admin_revokeTabletGrant":
        "KM'nin kendi Tablet modülü (bbd_tablet) profil ve öğrenciye özel süre ekranlarındadır; asıl kayıt orada (A5), Akademi bu uçlarla yalnız mobil/web yöneticiye vekillik eder (Akademi §14 94).",
    "admin_getTabletLimits":
        "KM'nin kendi Tablet modülü (bbd_tablet) profil ve öğrenciye özel süre ekranlarındadır; asıl kayıt orada (A5), Akademi bu uçlarla yalnız mobil/web yöneticiye vekillik eder (Akademi §14 94).",
    "students_getTabletLimits":
        "Öğrenci/veli ucu; KM panelinde öğrenci ekranı yok (Akademi §14 94).",
    # --- kantin: Akademi verisi değil (sözleşme §2, A12). Kantin veli
    # kontrolleri ve kantin nakit tahsilatı KM'de Öğrenciler ekranındadır
    # (`canteen.api`); burada ikinci bir kantin tahsilat ekranı açmak iki
    # kasa demektir.
    "admin_collectCanteenCash":
        "Kantin nakit tahsilatı KM Öğrenciler ekranındadır (canteen.api); kurum "
        "finansıyla karışmasın diye bu panelde yok (A12).",
    "admin_patchStudentCanteenControls":
        "Kantin kontrolleri / kurum engeli KM Öğrenciler ekranından yönetilir "
        "(sözleşme §2, kantin-veli-kontrolleri.md).",
    # --- personel itmesi panelden değil, modülün kendi servisinden gider:
    # KM listesi (mod_bbd_akademi_staff) değişince ve zamanlanmış görevle.
    "admin_syncStaff":
        "Tam liste itmesini modülün personel servisi yapar (backend/staff.py: "
        "değişiklikte, zamanlanmış görevde ve panelin 'Şimdi eşitle' düğmesiyle "
        "km/staff/sync üzerinden); panel bu uca doğrudan gitmez.",
    # Çalışma grubu yazma uçları (create/update/delete StudentGroup) sözleşmeden
    # 2d91b96 ile kalktı: grup KM öğrenci yönetiminde açılır ve Akademi'ye
    # eşitlenir (ürün kararı 12.10.2026); izin listesinde artık yoklar.
    # --- öğrenci dışa aktarımı: işi BAŞLATAN uç (`students_exportMistakes`)
    # entegrasyon anahtarına açık değil; yoklanacak iş olmadan durum okuması
    # anlamsız. Kurum çıktıları ortak `/v1/admin/exports/{id}` üzerinden iner.
    # --- tek ödev: "Ödev ver" toplu ödev sihirbazıdır (ne → kime → ne zaman);
    # tek hedef + tek ödev satırı aynı sonucu verir. İkinci bir "tek ödev"
    # formu kullanıcıyı ikiye bölerdi (kullanıcı: "karışık olmasın").
    "admin_createHomework":
        "Ödev verme panelde tek yoldan: toplu ödev sihirbazı (admin_createHomeworkBulk; tek "
        "hedef ve tek satır tek ödev demektir). Mevcut ödevin düzenlenmesi admin_updateHomework.",
    "students_getExport":
        "Hata defteri dışa aktarımını başlatan uç (students_exportMistakes) "
        "entegrasyon anahtarına açık değil; KM'nin başlatabileceği bir iş yok.",
}


#: Sözleşmeye yeni girmiş, ekranı henüz yazılmamış işlemler → gerekçe.
#: (km-0513: yoklama/program ve koçluk/mesaj paketleri yazıldı; boş.)
PENDING: dict[str, str] = {}


@dataclass(frozen=True)
class Hit:
    operation: Operation
    files: tuple[str, ...]
    by_id: bool

    @property
    def covered(self) -> bool:
        if self.operation.method != "GET":
            return self.by_id
        return bool(self.files)


def strip_comments(source: str) -> str:
    """Yorumları boşlukla değiştirir; dizgeler ve şablon dizgeleri korunur."""
    out: list[str] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if source[i] == "\\":
                    out.append(source[i:i + 2])
                    i += 2
                    continue
                out.append(source[i])
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            out.append("  ")
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_TEMPLATE = re.compile(r"\$\{[^}]*\}")
_PARAM = re.compile(r"\{[A-Za-z0-9]+\}")


def panel_sources(panel: Path = PANEL) -> dict[str, str]:
    return {path.name: strip_comments(path.read_text(encoding="utf-8"))
            for path in sorted(panel.rglob("*.js"))}


def scan(sources: dict[str, str] | None = None,
         operations: tuple[Operation, ...] = OPERATIONS) -> list[Hit]:
    sources = panel_sources() if sources is None else sources
    normalized = {name: _TEMPLATE.sub("{}", text) for name, text in sources.items()}
    hits: list[Hit] = []
    for operation in operations:
        literal = re.compile(rf"['\"`]{re.escape(operation.operation_id)}['\"`]")
        template = _PARAM.sub("{}", operation.path)
        path_pattern = re.compile(re.escape(template) + r"(?![A-Za-z0-9/{-])")
        id_files = [name for name, text in sources.items() if literal.search(text)]
        files = set(id_files)
        if operation.method == "GET":
            files.update(name for name, text in normalized.items() if path_pattern.search(text))
        hits.append(Hit(operation, tuple(sorted(files)), bool(id_files)))
    return hits


def uncovered(hits: list[Hit]) -> list[Hit]:
    return [hit for hit in hits
            if not hit.covered and hit.operation.operation_id not in NOT_SURFACED
            and hit.operation.operation_id not in PENDING]


def by_section(hits: list[Hit]) -> dict[str, int]:
    """Dosya → o dosyada karşılığı olan işlem sayısı (bir işlem birden çok yerde olabilir)."""
    counts: dict[str, int] = {}
    for hit in hits:
        if not hit.covered:
            continue
        for name in hit.files:
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--all", action="store_true", help="karşılanan işlemleri de yaz")
    parser.add_argument("--prefix", default="", help="yalnız bu yol önekini göster")
    args = parser.parse_args(argv)

    hits = scan()
    shown = [hit for hit in hits if hit.operation.path.startswith(args.prefix)]
    for hit in shown:
        op = hit.operation
        if op.operation_id in NOT_SURFACED:
            mark = "YOK*"
        elif op.operation_id in PENDING:
            mark = "SIRA"
        elif hit.covered:
            mark = "ok  "
        else:
            mark = "EKSİK"
        if mark == "ok  " and not args.all:
            continue
        print(f"{mark} {op.method:6} {op.path:66} {op.operation_id:40} {','.join(hit.files)}")
    missing = uncovered(hits)
    admin = [hit for hit in hits if hit.operation.path.startswith("/v1/admin/")]
    print()
    print(f"toplam {len(hits)} işlem ({len(admin)} admin) · ekranda "
          f"{sum(hit.covered for hit in hits)} · bilerek yok {len(NOT_SURFACED)} · "
          f"sırada {len(PENDING)} · EKSİK {len(missing)}")
    for name, count in by_section(hits).items():
        print(f"  {name:24} {count}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
