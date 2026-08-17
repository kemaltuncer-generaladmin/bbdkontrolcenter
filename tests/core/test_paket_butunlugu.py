"""Paketin taşıması gereken dosyalar ve eksiklerine karşı dayanıklılık.

NEDEN VAR. v0.1.1 kurulumu açılmıyordu: kabuk "Çekirdeğe ulaşılamadı —
bağlantı reddedildi" diyordu, yani 127.0.0.1:8787'de dinleyen yoktu. Sebep tek
bir eksik dosyaydı — `docs/schemas/module.schema.json` `tauri.release.json`
kaynak listesine konmamıştı. Çekirdek açılırken `read_manifest` onu okumaya
çalışıyor, `FileNotFoundError` atıyor, `discover` yalnız `ManifestError`
yakaladığı için hata `lifespan`den dışarı kaçıyor ve sidecar hiç ayağa
kalkmıyordu.

İki ayrı şey sınanır, ikisi de ayrı ayrı kırılabilir:

  · **Kaynak listesi.** Çekirdeğin ÇALIŞMA ANINDA kök dizinden okuduğu her yol
    pakete girmeli. Liste elle yazıldı: `tauri.release.json`den türetilseydi
    test yalnız "kendisiyle tutarlı" olur, unutulan dosyayı yakalamazdı — ki
    v0.1.1'de tam olarak unutulan buydu.
  · **Dayanıklılık.** Dosya yine de eksik kalırsa uygulama AYAĞA KALKMALI
    (ARCHITECTURE §5: "geçersiz manifest → modül yüklenmez, hata loglanır,
    uygulama yine ayağa kalkar"). Tek bir eksik dosyanın bütün uygulamayı
    kapatması o sözleşmeye aykırıdır.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from km_core.kernel.kernel import Kernel

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "apps" / "desktop" / "src-tauri" / "tauri.release.json"

#: Çekirdeğin kurulu uygulamada kök dizinden okuduğu yollar. ELLE YAZILDI.
PAKETE_GIRMELI = (
    "backend/src",
    "modules",
    "config/default.yaml",
    "docs/schemas/module.schema.json",
)


def _hedefler() -> set[str]:
    belge = json.loads(RELEASE.read_text(encoding="utf-8"))
    return set((belge.get("bundle") or {}).get("resources", {}).values())


def test_calisma_aninda_okunan_her_yol_pakete_giriyor() -> None:
    eksik = [yol for yol in PAKETE_GIRMELI if yol not in _hedefler()]
    assert not eksik, (
        f"tauri.release.json kaynak listesinde eksik: {eksik}. "
        "Kurulu uygulama bu dosyaları kök dizinde arar; biri yoksa çekirdek "
        "açılışta patlar ve kabuk 'Çekirdeğe ulaşılamadı' der."
    )


def test_gomulu_calisma_zamani_pakete_giriyor() -> None:
    """Kullanıcının makinesinde Python OLMAYABİLİR; paket kendi yorumlayıcısını
    taşır (ADR 0023). Bu satır düşerse kurulum sistem Python'una düşer."""
    assert "runtime" in _hedefler()


def test_paketlenen_yollar_depoda_gercekten_var() -> None:
    """Kaynak listesi var olmayan bir yolu gösteriyorsa derleme sessizce
    eksik paket üretir."""
    for yol in PAKETE_GIRMELI:
        assert (ROOT / yol).exists(), f"depoda yok: {yol}"


def test_sema_eksikse_uygulama_yine_ayaga_kalkar() -> None:
    with tempfile.TemporaryDirectory() as gecici:
        kok = Path(gecici)
        (kok / "modules").mkdir()

        kernel = Kernel.__new__(Kernel)
        kernel.records, kernel.problems, kernel.skipped = {}, [], []

        # Patlarsa test burada düşer — v0.1.1'in davranışı buydu.
        kernel.discover(kok / "modules", kok / "docs" / "schemas" / "yok.json")

        assert kernel.problems, "eksik şema sessizce geçilmemeli"
        assert "şema" in kernel.problems[0].lower()
        assert kernel.records == {}
