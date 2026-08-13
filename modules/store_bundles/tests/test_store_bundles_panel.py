"""Panel ve künye sözleşmesi — kaynak dosya üzerinden.

NEDEN PYTHON'DAN. Depoda JS koşucusu yok; bu ekranın panelde bozulduğunda
SESSİZ kalan birkaç kuralı var (kök sınıfı, `loadStyles` yeri, overlay'in
nereye eklendiği, cleanup'ın gerçek kaynak bırakması). Hiçbiri çalışma
zamanında hata vermez — panel açılır, sadece yanlış çalışır. Kaynağı okuyup
bu kuralları sınamak, hiç sınamamaktan iyidir.

Sınananlar sözleşmedir, biçim değil: burada kırılan bir test "kit kuralı
çiğnendi" demektir (apps/desktop/shell/ui-kit/README.md · Kesin kurallar).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

MODULE = Path(__file__).resolve().parents[1]
PANEL = (MODULE / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
CSS = (MODULE / "ui" / "panel" / "panel.css").read_text(encoding="utf-8")
MANIFEST = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))
SERVICE = (MODULE / "backend" / "service.py").read_text(encoding="utf-8")
MIGRATION = (MODULE / "backend" / "migrations" / "001_bundles.sql").read_text(encoding="utf-8")
ROUTES = (MODULE / "backend" / "api" / "routes.py").read_text(encoding="utf-8")


def _mount_body() -> str:
    return PANEL[PANEL.index("export function mount("):]


# ======================================================== panel sözleşmesi

def test_panel_kokunun_kit_panel_sinifi_var() -> None:
    # Yoksa toast ve overlay panelin değil TÜM PENCERENİN üstüne taşar.
    assert "'kit-panel sb'" in PANEL


def test_panel_css_yalnizca_mount_icinde_yuklenir() -> None:
    """Kabuk, yetenek çözümü sırasında hiç açılmayan panelleri de import
    ediyor; dosya tepesindeki `loadStyles()` kullanılmayan stilleri
    `document.head`'e sızdırır ve orada HİÇ KALDIRILMAZ."""
    assert PANEL.count("loadStyles(") == 1
    assert "loadStyles(import.meta.url)" in _mount_body()


def test_overlay_panel_kokune_eklenir_body_ye_degil() -> None:
    # Panel değişince kabuk `root.replaceChildren()` yapıyor; body'deki overlay
    # orada asılı kalır ve ekranı kilitler.
    assert "nodes.root.append(overlay)" in PANEL
    assert "document.body.append" not in PANEL


def test_cleanup_gercek_kaynak_birakir() -> None:
    kapanis = PANEL[PANEL.index("  return () => {"):]
    assert "nodes.filters?.destroy()" in kapanis      # arama debounce'u
    assert "dropForm()" in kapanis                    # formGrid → dateField
    assert "closers.forEach" in kapanis               # picker/hesap dinleyicileri
    assert "recalcSoon.cancel()" in PANEL


def test_tarih_alani_native_input_ile_yazilmaz() -> None:
    # WebKitGTK'da `<input type="date">` açılır takvimi kapanmıyor; kitin
    # `dateField()`'ı (formGrid `type: 'date'`) kullanılır.
    assert "type = 'date'" not in PANEL
    assert 'type="date"' not in PANEL
    assert "type: 'date'" in PANEL


def test_css_oneki_yalniz_kendi_ad_alanindadir() -> None:
    # Panel CSS'i `document.head`'e eklenir ve HİÇ kaldırılmaz; `sb-` dışına
    # çıkan bir seçici, bu panel bir kez açıldıktan sonra diğerlerini boyar.
    for line in CSS.splitlines():
        stripped = line.strip()
        if not stripped.startswith("."):
            continue
        assert stripped.startswith(".sb"), stripped


# ================================================== kite geçmiş davranışlar

def test_liste_tablosu_her_cizimde_yeniden_kurulmaz() -> None:
    """Arama kutusu her harfte `renderList` çağırıyor. Tabloyu her seferinde
    yeniden kurmak, kullanıcının seçtiği sütun sıralamasını her tuş vuruşunda
    sıfırlıyordu; kit `dataTable` tam bu iş için `update({rows, empty})`
    veriyor."""
    assert "nodes.table.update({" in PANEL
    assert PANEL.count("dataTable({") == 2      # liste + işlem geçmişi


def test_para_ayristirmasi_kitin_kuralindan_gecer() -> None:
    """Kuruşa çevirme tek bir yerde olmalı (kit kuralı 5). Elde
    `Number(x) * 100` yazmak ikinci bir para kuralı doğurur ve `1234.35` gibi
    değerlerde bir kuruş aşağı kayar."""
    assert "parseMoney(item.price)" in PANEL
    assert "Number(item.price)" not in PANEL


# =================================================== görünürlük ve dürüstlük

def test_kar_bilinmiyorken_sebep_yalniz_ipucunda_kalmaz() -> None:
    """Yalnız `title` ipucu dokunmatikte ve klavyeyle HİÇ açılmaz: kâr sütunu
    boş “—” diye okunur ve kullanıcı sayının neden yokluğunu öğrenemez."""
    hucre = PANEL[PANEL.index("function profitCell("):PANEL.index("const LIST_COLUMNS")]
    assert "maliyet girilmemiş" in hucre
    assert "set fiyatı yok" in hucre


def test_eksik_liste_bilgi_kutusu_olarak_gosterilmez() -> None:
    # `missed` = kategoride görünüp listeye GİRMEYEN set sayısı; liste eksik
    # demektir ve sıradan bir açıklama gibi çizilemez.
    uyari = PANEL[PANEL.index("function renderNotice("):PANEL.index("function visibleRows(")]
    assert "state.missed" in uyari
    assert "'warn'" in uyari
    assert "missed: payload.missed" in PANEL


def test_liste_dusunce_duzenleyici_de_dusurulur() -> None:
    """Yalnız listeyi yeniden çizmek, sağda artık elimizde olmayan bir setin
    künyesini — Kaydet ve Vitrinden kaldır düğmeleriyle — ekranda bırakıyordu."""
    kurtarma = PANEL[PANEL.index("payload = await api(`${BASE}/bundles`);"):
                     PANEL.index("  state = {\n    ...state,")]
    assert "renderEditor()" in kurtarma
    assert "renderNotice()" in kurtarma
    assert "selected: 0" in kurtarma


# ================================================ yıkıcı işlem kapısı (ADR 0012)

def test_yikici_islem_pin_degil_gerekce_ister() -> None:
    # `destructive: true` çekirdekte PIN kapısına bağlanacak; mağaza ekranları
    # PIN istemiyor. Koruma: ayrı izin + gerekçe + dryRun.
    for item in MANIFEST["permissions"]:
        assert "destructive" not in item, item
    assert "confirmWithReason(" in PANEL
    assert "minLength: 10" in PANEL


def test_her_uc_izin_ilan_eder_ve_gerekce_semada_dogrulanir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir.
    yollar = ROUTES.count("@router.")
    assert yollar == ROUTES.count('requires("')
    assert ROUTES.count("reason: str = Field(min_length=10") == 2   # save + status
    assert ROUTES.count("dryRun: bool = True") == 2


def test_pasiflestirme_ayri_izin_ister() -> None:
    anahtarlar = {item["key"] for item in MANIFEST["permissions"]}
    assert anahtarlar == {"store_bundles.view", "store_bundles.manage",
                          "store_bundles.deactivate"}
    assert 'requires("store_bundles.deactivate")' in ROUTES
    assert "delete" not in ROUTES.lower().replace("delete_", "")


# ======================================================== künye ve K5 · K11

def test_tum_tablolar_ve_indeksler_modul_onekiyle_baslar() -> None:
    """K5: modül yalnız kendi göçleriyle açtığı tablolara yazar. Önek
    kaymışsa, aynı adı kullanan başka bir modülün tablosuna yazılır."""
    isimler = re.findall(
        r"CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][\w]*)",
        MIGRATION, flags=re.IGNORECASE)
    assert len(isimler) == 4
    for name in isimler:
        assert name.startswith("mod_store_bundles_"), name


def test_pdftoppm_bagimliligi_ilan_edilir() -> None:
    """K11: bağımlılık ilan edilir, kopyalanmaz. Önizleme `pdftoppm`
    çalıştırıyor; ilan edilmezse kurulum betiği onu toplamaz ve önizleme
    kurulumdan kuruluma sessizce hiç gelmez."""
    assert "pdftoppm" in SERVICE
    assert "poppler-utils" in (MANIFEST.get("dependencies") or {}).get("system", [])


def test_modul_yalnizca_km_sdk_import_eder() -> None:
    # K2: modül `km_core` / `km_platform` görmez.
    for path in (MODULE / "backend").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "km_core" not in text, path
        assert "km_platform" not in text, path


def test_magaza_verisine_yalniz_gecitten_bakilir() -> None:
    # K4: modülde ham httpx/DB sürücüsü yok; erişim `store.api` yeteneğinden.
    for path in (MODULE / "backend").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for yasak in ("import httpx", "import requests", "import asyncssh", "import paramiko"):
            assert yasak not in text, f"{path}: {yasak}"
