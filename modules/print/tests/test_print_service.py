"""Çıktı Merkezi servisi — liste, süzgeç, kayıp dosya, yeniden basma, budama."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from print_backend.service import OutputsService


class FakeLog:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def _write(self, level: str, message: str, **fields: Any) -> None:
        self.lines.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._write("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._write("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._write("error", message, **fields)


class FakePrinter:
    """Yazıcı yeteneğinin taklidi — gerçek `lp` çağrılmaz."""

    def __init__(self, result: dict[str, Any] | None = None,
                 failure: Exception | None = None) -> None:
        self.result = result or {"ok": True, "printer": "HP_LaserJet", "job": "HP-42"}
        self.failure = failure
        self.calls: list[tuple[Path, int]] = []

    async def print_file(self, path: Path, *, title: str = "",
                         copies: int = 1) -> dict[str, Any]:
        self.calls.append((path, copies))
        if self.failure is not None:
            raise self.failure
        return self.result

    async def status(self) -> dict[str, Any]:
        return {"ready": True, "target": {"name": "HP_LaserJet"}}


def build(store: Any, printer: Any = None, **config: Any) -> OutputsService:
    return OutputsService(store=store, log=FakeLog(), printer=printer, config=config)


async def add_output(store: Any, *, output_id: str, path: Path, source: str = "kantin",
                     kind: str = "pdf", when: str | None = None,
                     user_id: str | None = None, size: int = 1024) -> None:
    """Çekirdeğin düşürdüğü kaydın aynısı. Servis kayıt YAZMAZ, yalnız okur."""
    await store.execute(
        "INSERT INTO outputs (id, created_at, user_id, source, kind, title, path, "
        "bytes, pages, params_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (output_id,
         when or datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
         user_id, source, kind, path.name, str(path), size, 2, "abc123"),
    )


@pytest.fixture
def rapor(tmp_path: Path) -> Path:
    path = tmp_path / "kantin-ozet-2026-08-01_2026-08-13.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    return path


# -------------------------------------------------------------------- liste


async def test_liste_kaydi_ve_dosya_durumunu_birlikte_doner(store: Any, rapor: Path) -> None:
    await add_output(store, output_id="a1", path=rapor)
    sonuc = await build(store).outputs()

    (satir,) = sonuc["items"]
    assert sonuc["total"] == 1
    assert satir["title"] == rapor.name
    assert satir["exists"] is True
    assert satir["missingReason"] == ""
    assert satir["printable"] is True
    assert satir["folder"] == str(rapor.parent)


async def test_dosya_yoksa_kayit_SILINMEZ_durumu_degisir(store: Any, tmp_path: Path) -> None:
    """ADR 0019 §4 — kaydı gizlemek en kötü seçenektir."""
    kayip = tmp_path / "silinmis.pdf"
    await add_output(store, output_id="a1", path=kayip)

    sonuc = await build(store).outputs()
    (satir,) = sonuc["items"]

    assert sonuc["total"] == 1, "kayıt listeden düştü"
    assert satir["exists"] is False
    assert "bulunamadı" in satir["missingReason"]
    assert sonuc["missing"] == 1


async def test_suzgecler_tur_modul_ureten_ve_tarih(store: Any, tmp_path: Path) -> None:
    eski = (datetime.now(UTC).astimezone() - timedelta(days=40)).isoformat(timespec="seconds")
    await add_output(store, output_id="a1", path=tmp_path / "bir.pdf", source="kantin")
    await add_output(store, output_id="a2", path=tmp_path / "iki.csv", source="magaza",
                     kind="csv", user_id="u1")
    await add_output(store, output_id="a3", path=tmp_path / "uc.pdf", source="magaza",
                     when=eski)
    service = build(store)

    assert [row["id"] for row in (await service.outputs(kind="csv"))["items"]] == ["a2"]
    assert {row["id"] for row in (await service.outputs(source="magaza"))["items"]} == {"a2", "a3"}
    assert [row["id"] for row in (await service.outputs(user="u1"))["items"]] == ["a2"]

    bugun = datetime.now(UTC).astimezone().date().isoformat()
    taze = await service.outputs(start=bugun)
    assert {row["id"] for row in taze["items"]} == {"a1", "a2"}

    arama = await service.outputs(search="iki")
    assert [row["id"] for row in arama["items"]] == ["a2"]


async def test_suzgec_secenekleri_TUM_tablodan_cikar(store: Any, tmp_path: Path) -> None:
    """Süzgeç kendi seçeneğini elerse kullanıcı seçtiğini geri alamaz."""
    await add_output(store, output_id="a1", path=tmp_path / "bir.pdf", source="kantin")
    await add_output(store, output_id="a2", path=tmp_path / "iki.csv", source="magaza",
                     kind="csv")

    sonuc = await build(store).outputs(kind="csv")
    assert [item["value"] for item in sonuc["facets"]["kinds"]] == ["csv", "pdf"]
    assert [item["value"] for item in sonuc["facets"]["sources"]] == ["kantin", "magaza"]


async def test_ureten_adi_kimlikten_okunur(store: Any, tmp_path: Path) -> None:
    await store.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, pin_hash, pin_lookup, "
        "pin_set_at, created_at, updated_at) "
        "VALUES ('u1', 'Ayşe', 'Yılmaz', 'org', 'x', 'y', 'z', 'now', 'now')")
    await add_output(store, output_id="a1", path=tmp_path / "bir.pdf", user_id="u1")

    (satir,) = (await build(store).outputs())["items"]
    assert satir["user"] == "Ayşe Yılmaz"


# ------------------------------------------------------------- yeniden bas


async def test_yeniden_basma_sayaci_isler(store: Any, rapor: Path) -> None:
    printer = FakePrinter()
    await add_output(store, output_id="a1", path=rapor)
    service = build(store, printer)

    sonuc = await service.reprint("a1", copies=2)

    assert sonuc["ok"] is True
    assert printer.calls == [(rapor, 2)]
    (satir,) = (await service.outputs())["items"]
    assert satir["printedCount"] == 1
    assert satir["lastPrintedAt"]


async def test_sistem_penceresi_de_DENEME_sayar(store: Any, rapor: Path) -> None:
    """ADR 0014 — Windows/macOS'ta kâğıt çıkmaz, pencere açılır."""
    printer = FakePrinter({"mode": "system", "path": str(rapor)})
    await add_output(store, output_id="a1", path=rapor)
    service = build(store, printer)

    sonuc = await service.reprint("a1")

    assert sonuc["mode"] == "system"
    assert sonuc["attempted"] is True
    (satir,) = (await service.outputs())["items"]
    assert satir["printedCount"] == 1


async def test_kayip_dosya_basilmaz_ve_sayac_ARTMAZ(store: Any, tmp_path: Path) -> None:
    printer = FakePrinter()
    await add_output(store, output_id="a1", path=tmp_path / "yok.pdf")
    service = build(store, printer)

    sonuc = await service.reprint("a1")

    assert sonuc["ok"] is False
    assert "bulunamadı" in sonuc["error"]
    assert printer.calls == []
    (satir,) = (await service.outputs())["items"]
    assert satir["printedCount"] == 0


async def test_yazici_hatasi_sayaci_ARTIRMAZ(store: Any, rapor: Path) -> None:
    printer = FakePrinter(failure=RuntimeError("Yazıcı bağlantısı hatası"))
    await add_output(store, output_id="a1", path=rapor)
    service = build(store, printer)

    sonuc = await service.reprint("a1")

    assert sonuc["ok"] is False
    assert "Yazıcı bağlantısı" in sonuc["error"]
    (satir,) = (await service.outputs())["items"]
    assert satir["printedCount"] == 0


async def test_yazici_yoksa_liste_CALISIR_baski_nedenini_soyler(store: Any,
                                                                rapor: Path) -> None:
    await add_output(store, output_id="a1", path=rapor)
    service = build(store)

    liste = await service.outputs()
    assert liste["printerAvailable"] is False
    assert liste["items"]

    sonuc = await service.reprint("a1")
    assert sonuc["ok"] is False
    assert "Yazıcı yeteneği" in sonuc["error"]


async def test_kopya_sayisi_ust_sinira_kirpilir(store: Any, rapor: Path) -> None:
    printer = FakePrinter()
    await add_output(store, output_id="a1", path=rapor)
    service = build(store, printer, max_copies=3)

    await service.reprint("a1", copies=99)
    assert printer.calls == [(rapor, 3)]


# ---------------------------------------------------------------- önizleme


async def test_metin_onizlemesi_dosyanin_kendisini_gosterir(store: Any,
                                                            tmp_path: Path) -> None:
    csv = tmp_path / "liste.csv"
    csv.write_bytes("﻿ad;tutar\r\nAyşe;12,50\r\n".encode())
    await add_output(store, output_id="a1", path=csv, kind="csv")

    sonuc = await build(store).preview("a1")

    assert sonuc["mode"] == "text"
    assert "Ayşe;12,50" in sonuc["text"]
    assert not sonuc["text"].startswith("﻿"), "BOM ekrana sızdı"


async def test_kayip_dosya_onizlenmez_nedeni_yazilir(store: Any, tmp_path: Path) -> None:
    await add_output(store, output_id="a1", path=tmp_path / "yok.csv", kind="csv")
    sonuc = await build(store).preview("a1")
    assert sonuc["ok"] is False
    assert "bulunamadı" in sonuc["error"]


async def test_pdf_onizlemesi_sayfa_goruntusu_doner(store: Any, tmp_path: Path) -> None:
    """Önizleme, basılacak PDF'in BİREBİR kendisidir."""
    pytest.importorskip("reportlab.pdfgen.canvas")
    if shutil.which("pdftoppm") is None:
        pytest.skip("poppler-utils kurulu değil")

    from reportlab.pdfgen.canvas import Canvas

    pdf = tmp_path / "kartlar.pdf"
    surface = Canvas(str(pdf))
    surface.drawString(40, 40, "3A")
    surface.save()
    await add_output(store, output_id="a1", path=pdf)

    sonuc = await build(store).preview("a1")

    assert sonuc["mode"] == "pages"
    assert sonuc["pages"] and sonuc["pages"][0].startswith("data:image/png;base64,")


async def test_bilinmeyen_tur_onizlenemez_der(store: Any, tmp_path: Path) -> None:
    yedek = tmp_path / "yedek.sqlite"
    yedek.write_bytes(b"x")
    await add_output(store, output_id="a1", path=yedek, kind="sqlite")

    sonuc = await build(store).preview("a1")
    assert sonuc["ok"] is False
    assert "önizlenemez" in sonuc["error"]


# --------------------------------------------------------------- klasör aç


async def test_bilinmeyen_kayit_icin_klasor_acilmaz(store: Any) -> None:
    sonuc = await build(store).open_folder("yok")
    assert sonuc == {"ok": False, "error": "Kayıt bulunamadı."}


async def test_klasor_de_yoksa_neden_yazilir(store: Any, tmp_path: Path) -> None:
    """Dosya yönetici penceresi AÇILMAZ; kullanıcıya ne olduğu söylenir."""
    await add_output(store, output_id="a1", path=tmp_path / "silinmis-klasor" / "a.pdf")
    sonuc = await build(store).open_folder("a1")
    assert sonuc["ok"] is False
    assert "Klasör de yok" in sonuc["error"]


# ------------------------------------------------------------------ budama


async def test_budama_KAYDI_siler_DOSYAYA_dokunmaz(store: Any, tmp_path: Path) -> None:
    """ADR 0019 §5 — masaüstündeki klasör kullanıcının alanıdır."""
    eski_dosya = tmp_path / "eski.pdf"
    eski_dosya.write_bytes(b"%PDF-1.4\n")
    eski = (datetime.now(UTC).astimezone() - timedelta(days=45)).isoformat(timespec="seconds")
    await add_output(store, output_id="a1", path=eski_dosya, when=eski)
    await add_output(store, output_id="a2", path=tmp_path / "yeni.pdf")
    service = build(store, keep_job_history_days=30)

    sonuc = await service.prune()

    assert sonuc["removed"] == 1
    assert [row["id"] for row in (await service.outputs())["items"]] == ["a2"]
    assert eski_dosya.is_file(), "kullanıcının dosyası silinmiş"


async def test_sifir_gun_budama_YAPMAZ(store: Any, tmp_path: Path) -> None:
    eski = (datetime.now(UTC).astimezone() - timedelta(days=900)).isoformat(timespec="seconds")
    await add_output(store, output_id="a1", path=tmp_path / "eski.pdf", when=eski)

    sonuc = await build(store, keep_job_history_days=0).prune()
    assert sonuc["removed"] == 0
    assert (await build(store).outputs())["total"] == 1


# ------------------------------------------------------- çekirdekle zincir


async def test_cekirdegin_dusurdugu_kayit_ekranda_gorunur(store: Any, tmp_path: Path) -> None:
    """Zincirin tamamı: `write_private` → `outputs` → Çıktı Merkezi listesi.

    Modül tarafında "çıktı ürettim" diye bildiren TEK SATIR YOKTUR; kayıt
    dosyayı yazan çekirdek fonksiyonunda doğar (ADR 0019 §1).
    """
    from km_core.files import outputs_log
    from km_core.files.private import write_private

    outputs_log.use_database(store._store.path)
    try:
        with outputs_log.use_actor("u9"):
            yazilan = write_private(tmp_path / "ogrenci-kartlari-3A.pdf", b"%PDF-1.4\n")
    finally:
        outputs_log.use_database(None)

    sonuc = await build(store).outputs()
    (satir,) = sonuc["items"]
    assert satir["path"] == str(yazilan)
    assert satir["kind"] == "pdf"
    assert satir["userId"] == "u9"
    assert satir["exists"] is True
