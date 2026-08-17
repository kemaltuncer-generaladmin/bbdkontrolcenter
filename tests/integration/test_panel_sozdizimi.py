"""Panel ve kit dosyalarının sözdizimi — ESM olarak denetlenir.

BULUNAN HATA (2026-08-15). Bu depoda panel sözdizimi `node --check dosya.js`
ile denetleniyordu. O komut ESM içeriği gördüğünde **sessizce geçer**:

    $ printf 'import a from "b";\\nconst x = ;\\n' > bozuk.js
    $ node --check bozuk.js        # çıkış kodu 0 — YALANCI YEŞİL
    $ cp bozuk.js bozuk.mjs
    $ node --check bozuk.mjs       # SyntaxError — doğru cevap

Node uzantıya bakarak kip seçiyor: `.js` betik sayılıyor, `import` satırı
görülünce denetim erken bırakılıyor. Paneller ESM (`import ... from`), yani
bugüne kadar koşturduğumuz her denetim hiçbir şey kanıtlamamış — bozuk bir
panel testten geçer, yalnız kullanıcı ekranı açtığında beyaz ekran görürdü.

Bu test dosyayı `.mjs` olarak kopyalayıp denetler. Kopya şart: uzantı
değişmeden Node kipi değiştirmenin dosya başına yolu yok.

`node` yoksa test ATLANIR — çekirdek Python'dur, Node yalnız kabuk aracıdır ve
onun yokluğu Python süitini kırmamalı.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Denetlenecek yerler: panel girişleri, ortak kit ve kabuğun kendi dosyaları.
#:
#: `shell/core-panels/**` AYRICA YAZILIR. Çekirdek ekranları modül DEĞİLDİR
#: (ADR 0017): manifestleri yoktur, `modules/*/ui/panel/` altından gelmezler ve
#: `shell/*.js` deseni de alt klasöre inmez. Desen genişletilmeden önce bu
#: dosyalar HİÇBİR desene girmiyordu — yeni bir çekirdek panelindeki sözdizimi
#: hatası tüm süiti yeşil bırakırdı.
DESENLER = (
    "modules/*/ui/panel/**/*.js",
    "apps/desktop/shell/ui-kit/*.js",
    "apps/desktop/shell/core-panels/**/*.js",
    "apps/desktop/shell/*.js",
)


def _kaynaklar() -> list[Path]:
    bulunan: list[Path] = []
    for desen in DESENLER:
        bulunan.extend(
            path for path in ROOT.glob(desen)
            if path.is_file() and "node_modules" not in path.parts
            # `shell/panels/` ÜRETİLEN kopyadır (git dışı); kaynağı zaten
            # `modules/*/ui/panel/` altında denetleniyor. İkisini birden
            # denetlemek aynı hatayı iki kez sayardı.
            and "panels" not in path.relative_to(ROOT).parts[:3]
        )
    return sorted(set(bulunan))


@pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")
def test_tum_panel_ve_kit_dosyalari_gecerli_esm() -> None:
    kaynaklar = _kaynaklar()
    assert kaynaklar, "Denetlenecek JS bulunamadı — desenler kaymış olabilir."

    hatalar: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        hedef = Path(tmp) / "denetim.mjs"
        for path in kaynaklar:
            hedef.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            sonuc = subprocess.run(
                ["node", "--check", str(hedef)],
                capture_output=True, text=True, check=False,
            )
            if sonuc.returncode != 0:
                ilk = (sonuc.stderr.strip().splitlines() or ["(çıktı yok)"])[:3]
                hatalar.append(f"{path.relative_to(ROOT)}: {' | '.join(ilk)}")

    assert hatalar == [], "Sözdizimi hatası taşıyan dosyalar:\n" + "\n".join(hatalar)


@pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")
def test_denetim_bozuk_dosyayi_GERCEKTEN_yakalar() -> None:
    """Testin kendisi anlamlı mı — yalancı yeşili yeniden üretip yakalıyoruz.

    Bu olmadan yukarıdaki test, `node --check`'in bir gün yine sessizleşmesi
    hâlinde fark edilmeden yeşil kalırdı.
    """
    bozuk = "import a from 'b';\nconst x = ;\n"
    with tempfile.TemporaryDirectory() as tmp:
        js = Path(tmp) / "bozuk.js"
        mjs = Path(tmp) / "bozuk.mjs"
        js.write_text(bozuk, encoding="utf-8")
        mjs.write_text(bozuk, encoding="utf-8")

        as_js = subprocess.run(["node", "--check", str(js)],
                               capture_output=True, check=False)
        as_mjs = subprocess.run(["node", "--check", str(mjs)],
                                capture_output=True, check=False)

    # `.js` yolunun yalancı yeşil verdiğini SABİTLEMİYORUZ (Node bunu bir gün
    # düzeltebilir); sabitlediğimiz şey `.mjs` yolunun DOĞRU cevabı vermesi.
    assert as_mjs.returncode != 0, (
        "`.mjs` denetimi bozuk dosyayı yakalamadı; bu testin dayandığı yöntem "
        f"artık geçerli değil (node --check .js çıkışı: {as_js.returncode})."
    )
