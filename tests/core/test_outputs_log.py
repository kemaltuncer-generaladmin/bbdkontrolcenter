"""Çıktı kaydı — dosyayı yazan fonksiyonda doğar (ADR 0019 §1).

BU TESTİN KORUDUĞU ŞEY, MODÜLLERİN DEĞİŞMEMESİDİR. Kayıt `write_private`
içinde düştüğü için hiçbir modül "çıktı ürettim" diye bildirmek zorunda
değildir; bir gün bu bağ koparsa yirmi ekranın hiçbirinde hata görünmez, çıktı
yalnızca listede eksilir. Sessiz kayıp buradan yakalanır.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from km_core.files import outputs_log
from km_core.files.private import write_private
from km_core.store.db import Store


@pytest.fixture
async def database(tmp_path: Path) -> Iterator[Path]:
    """Şeması kurulmuş gerçek depo; kayıt oraya düşer."""
    path = tmp_path / "kayit.sqlite"
    store = Store(path)
    await store.open()
    outputs_log.use_database(path)
    yield path
    outputs_log.use_database(None)
    await store.close()


def rows(database: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in
                connection.execute("SELECT * FROM outputs ORDER BY rowid")]
    finally:
        connection.close()


# ------------------------------------------------------------------- kayıt


async def test_write_private_kayit_dusurur(database: Path, tmp_path: Path) -> None:
    hedef = tmp_path / "Raporlar" / "kantin-ozet-2026-08-01_2026-08-13.csv"
    write_private(hedef, b"a;b;c\n")

    (kayit,) = rows(database)
    assert kayit["path"] == str(hedef)
    assert kayit["title"] == hedef.name
    assert kayit["kind"] == "csv"
    assert kayit["bytes"] == 6
    assert kayit["printed_count"] == 0
    assert kayit["last_printed_at"] is None
    # Damga yerel farkı taşır: gün öneki "bugün" sorgularıyla uyuşsun.
    assert kayit["created_at"][:2] == "20"


async def test_kayit_dusmezse_dosya_YINE_yazilir(tmp_path: Path) -> None:
    """K7 — kaydın kaybı can sıkıcıdır, raporun kaybı işi durdurur."""
    outputs_log.use_database(tmp_path / "olmayan.sqlite")
    try:
        hedef = tmp_path / "rapor.pdf"
        write_private(hedef, b"%PDF-1.4\n")
        assert hedef.read_bytes() == b"%PDF-1.4\n"
        assert hedef.stat().st_mode & 0o777 == 0o600
    finally:
        outputs_log.use_database(None)


async def test_bozuk_depo_dosya_uretimini_DURDURMAZ(tmp_path: Path) -> None:
    bozuk = tmp_path / "bozuk.sqlite"
    bozuk.write_bytes(b"bu bir sqlite dosyasi degil")
    outputs_log.use_database(bozuk)
    try:
        hedef = tmp_path / "rapor.csv"
        write_private(hedef, b"x")
        assert hedef.is_file()
    finally:
        outputs_log.use_database(None)


async def test_sayfa_sayisi_pdf_icin_okunur(database: Path, tmp_path: Path) -> None:
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf = tmp_path / "iki-sayfa.pdf"
    surface = reportlab.Canvas(str(pdf))
    surface.drawString(40, 40, "bir")
    surface.showPage()
    surface.drawString(40, 40, "iki")
    surface.showPage()
    surface.save()

    write_private(tmp_path / "kopya.pdf", pdf.read_bytes())
    (kayit,) = rows(database)
    assert kayit["pages"] == 2


async def test_csv_sayfa_sayisi_UYDURULMAZ(database: Path, tmp_path: Path) -> None:
    write_private(tmp_path / "liste.csv", b"a;b\n")
    (kayit,) = rows(database)
    assert kayit["pages"] is None


async def test_ayni_icerik_ayni_parmak_izini_verir(database: Path, tmp_path: Path) -> None:
    write_private(tmp_path / "bir.csv", b"ayni")
    write_private(tmp_path / "iki.csv", b"ayni")
    write_private(tmp_path / "uc.csv", b"baska")

    izler = [row["params_digest"] for row in rows(database)]
    assert izler[0] == izler[1] != izler[2]


async def test_ureten_kisi_baglamdan_okunur(database: Path, tmp_path: Path) -> None:
    with outputs_log.use_actor("kullanici-42"):
        write_private(tmp_path / "a.csv", b"x")
    write_private(tmp_path / "b.csv", b"y")

    ilk, ikinci = rows(database)
    assert ilk["user_id"] == "kullanici-42"
    # Kimlik bilinmiyorsa satır YİNE yazılır; yalnız alan boş kalır.
    assert ikinci["user_id"] is None


# ------------------------------------------------------------ kaynak (K1)


def _sahte_modul(tmp_path: Path, module_id: str, package: str) -> Any:
    """`modules/<id>/backend/service.py` taşıyan gerçek bir dosya ağacı kurar."""
    root = tmp_path / "modules" / module_id
    (root / "backend").mkdir(parents=True)
    (root / "module.yaml").write_text(f"id: {module_id}\n", encoding="utf-8")
    source = root / "backend" / "service.py"
    source.write_text(
        "from km_core.files.private import write_private\n"
        "\n"
        "def uret(path, content):\n"
        "    return write_private(path, content)\n",
        encoding="utf-8",
    )

    spec = importlib.util.spec_from_file_location(package, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[package] = module
    spec.loader.exec_module(module)
    return module


async def test_kaynak_modul_yolundan_okunur(database: Path, tmp_path: Path) -> None:
    """Çekirdek modül adını BİLMEZ; `modules/<id>/` yolundan okur (K1)."""
    modul = _sahte_modul(tmp_path, "sahte_rapor", "sahte_paket_service")
    try:
        modul.uret(tmp_path / "cikti.pdf", b"%PDF-1.4\n")
    finally:
        del sys.modules["sahte_paket_service"]

    (kayit,) = rows(database)
    assert kayit["source"] == "sahte_rapor"


async def test_kaynak_kernel_paket_adindan_da_okunur(database: Path, tmp_path: Path) -> None:
    """Kernel modülleri `km_mod_<id>` paketiyle yüklüyor; o ad da yeter."""
    paket = "km_mod_sahte_ikinci.backend.service"
    modul = _sahte_modul(tmp_path, "baska_klasor", paket)
    try:
        modul.uret(tmp_path / "cikti.csv", b"x")
    finally:
        del sys.modules[paket]

    (kayit,) = rows(database)
    assert kayit["source"] == "sahte_ikinci"


async def test_modulsuz_yazma_core_kaynagiyla_kaydedilir(database: Path,
                                                         tmp_path: Path) -> None:
    """Modül yoksa satır GİZLENMEZ: kaynağı 'core' olarak durur."""
    write_private(tmp_path / "cekirdek.csv", b"x")
    (kayit,) = rows(database)
    assert kayit["source"] == outputs_log.CORE_SOURCE


# --------------------------------------------------- bildirilmemiş depo


def test_bildirilmemis_depoya_YAZILMAZ(tmp_path: Path) -> None:
    """Depo gösterilmediyse kayıt hiç düşmez — ayardan tahmin edilmez.

    Bu bir kez oldu: takım koşusu geliştiricinin GERÇEK
    `data/kontrol-merkezi.sqlite` dosyasına 390 çöp satır yazdı, çoğunun yolu
    `/tmp/…` altındaydı. Ölçüt "pytest koşuyor mu" DEĞİLDİR — o yalnız bir
    belirtiyi kapatır, betikleri ve gömülü kullanımı açıkta bırakırdı. Ölçüt
    şudur: yazan taraf bir depo bildirdi mi?
    """
    assert outputs_log.database_path() is None

    hedef = tmp_path / "sessiz.csv"
    write_private(hedef, b"x")
    assert hedef.is_file(), "kayıt atlanınca dosya da yazılmamış"


def test_ayar_okunmaz_gercek_depo_kendiliginden_BULUNMAZ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pytest damgası silinse bile ayardaki depo kendiliğinden bulunmaz.

    Eski koruma `PYTEST_CURRENT_TEST` ortam değişkenine bakıyordu; o değişken
    yokken (alt süreç, elle çağrılan betik, gömülü kullanım) kayıt yine ayardan
    okunan gerçek depoya düşerdi. Kapıyı kapatan şey artık ortam değil, bağın
    kendisi.
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert outputs_log.database_path() is None

    write_private(tmp_path / "betik-ciktisi.csv", b"x")
    assert outputs_log.database_path() is None


def test_bag_cozulunce_kayit_DURUR(tmp_path: Path) -> None:
    """`use_database(None)` bağı gerçekten çözer; sonraki yazma kayıtsızdır."""
    depo = tmp_path / "kayit.sqlite"
    outputs_log.use_database(depo)
    assert outputs_log.database_path() == depo

    outputs_log.use_database(None)
    assert outputs_log.database_path() is None
    write_private(tmp_path / "sonra.csv", b"x")
