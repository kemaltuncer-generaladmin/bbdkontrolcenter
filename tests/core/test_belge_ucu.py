"""Üretilmiş belgenin baytlarını veren uç — yol kapısı.

NEDEN VAR. Baskı artık kullanıcının makinesinde yapılıyor: çekirdek sunucuda
koşuyor ve sunucu imajında CUPS yok, yazıcılar ise kullanıcının masasında.
Kabuğun PDF'i basabilmesi için önce eline alması gerekiyor.

Bu uç bir DOSYA OKUMA ucudur ve tam da bu yüzden dar tutulmuştur: yalnız rapor
kökü altındaki PDF'ler verilir. Serbest yol kabul etseydi, oturumu olan herkes
sunucudaki herhangi bir dosyayı okuyabilirdi.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import Config, load_config
from km_core.http.app import create_app

ROOT = Path(__file__).resolve().parents[2]
YONETICI_PINI = "471902"

#: Gerçek bir PDF olması gerekmiyor; uç içeriği yorumlamıyor, uzantıya ve
#: konuma bakıyor. Ayrıştırma denemek burada sınanan şeyi değiştirmezdi.
SAHTE_PDF = b"%PDF-1.4\ntest\n%%EOF\n"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
        "log_path": str(tmp_path / "gunluk.log"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    data["files"] = {"output_path": str(tmp_path / "cikti")}
    app = create_app(Config(data, root=ROOT))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def token(client: TestClient) -> str:
    cevap = client.post("/api/auth/login", json={"password": YONETICI_PINI})
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def rapor_koku(client: TestClient) -> Path:
    """Ucun kabul ettiği kök — testin de aynı yeri kullanması şart."""
    from km_core.config.paths import data_dir
    from km_core.files.outputs import reports_root

    config = client.app.state.config
    return reports_root(data_dir(config.root) / "exports")


def iste(client: TestClient, path: str) -> object:
    return client.post(
        "/api/outputs/document",
        headers={"Authorization": f"Bearer {token(client)}"},
        json={"path": path},
    )


# ------------------------------------------------------------------ kapı


def test_rapor_kokundeki_pdf_verilir(client: TestClient) -> None:
    kok = rapor_koku(client)
    kok.mkdir(parents=True, exist_ok=True)
    belge = kok / "ornek.pdf"
    belge.write_bytes(SAHTE_PDF)

    cevap = iste(client, str(belge))
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert govde["name"] == "ornek.pdf"
    # Baytlar OLDUĞU GİBİ dönmeli: kabuk bunu yazıcıya veriyor.
    assert base64.b64decode(govde["data"]) == SAHTE_PDF


def test_modulun_yazdigi_klasordeki_pdf_verilir(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modüllerin çıktı klasörü (`<kök>/data/exports`) kapıdan geçer.

    KURULU SİSTEMİ TAKLİT EDER. Geliştirmede `data_dir(root)` zaten
    `<kök>/data` olduğu için iki adres çakışır ve arıza GÖRÜNMEZ; sunucuda
    `data_dir` sistem veri dizinine gider ve ayrışırlar. `data_dir` burada
    bilerek başka bir yere çevrilir — arızanın koştuğu koşul budur.

    Belirti: uygulamanın kendi ürettiği raporda "bu dosya rapor klasöründe
    değil; güvenlik gereği verilmez" ve hiçbir ekrandan çıktı alınamaması.
    """
    from km_core.files import outputs as outputs_module
    from km_core.http import documents as documents_module

    # Masaüstü yok (sunucuda da yok): iki taraf da fallback'e düşer.
    monkeypatch.setattr(outputs_module, "desktop_dir", lambda: None)
    monkeypatch.setattr(documents_module, "data_dir", lambda _root: tmp_path / "sistem")

    kok = client.app.state.config.root / "data" / "exports"
    kok.mkdir(parents=True, exist_ok=True)
    belge = kok / "kapi-denemesi.pdf"
    belge.write_bytes(SAHTE_PDF)
    try:
        cevap = iste(client, str(belge))
        assert cevap.status_code == 200, cevap.text
        assert base64.b64decode(cevap.json()["data"]) == SAHTE_PDF
    finally:
        belge.unlink(missing_ok=True)


def test_rapor_kokunun_disindaki_dosya_verilmez(client: TestClient, tmp_path: Path) -> None:
    # Serbest yol kabul etmek, oturumu olan herkese sunucudaki her dosyayı
    # okutmak olurdu.
    disarida = tmp_path / "gizli.pdf"
    disarida.write_bytes(SAHTE_PDF)
    assert iste(client, str(disarida)).status_code == 403


def test_ust_dizine_cikilamaz(client: TestClient, tmp_path: Path) -> None:
    kok = rapor_koku(client)
    kok.mkdir(parents=True, exist_ok=True)
    hedef = tmp_path / "kacak.pdf"
    hedef.write_bytes(SAHTE_PDF)
    assert iste(client, f"{kok}/../../{hedef.name}").status_code in (403, 404)


def test_sembolik_bag_ile_disari_cikilamaz(client: TestClient, tmp_path: Path) -> None:
    """`resolve()` bağı çözdükten SONRA denetlenir; bağ koymak kapıyı açmaz."""
    kok = rapor_koku(client)
    kok.mkdir(parents=True, exist_ok=True)
    disarida = tmp_path / "gizli.pdf"
    disarida.write_bytes(SAHTE_PDF)
    bag = kok / "bag.pdf"
    try:
        bag.symlink_to(disarida)
    except OSError:  # pragma: no cover - sembolik bağ kurulamayan sistem
        pytest.skip("sembolik bağ kurulamıyor")
    assert iste(client, str(bag)).status_code == 403


def test_pdf_disi_uzanti_verilmez(client: TestClient) -> None:
    # Uç "dosya oku" ucuna dönüşmesin: yalnız basılabilir belge çıkar.
    kok = rapor_koku(client)
    kok.mkdir(parents=True, exist_ok=True)
    baska = kok / "kayit.csv"
    baska.write_text("a;b\n", encoding="utf-8")
    assert iste(client, str(baska)).status_code == 415


def test_oturumsuz_istek_reddedilir(client: TestClient) -> None:
    cevap = client.post("/api/outputs/document", json={"path": "/tmp/x.pdf"})
    assert cevap.status_code == 401


def test_olmayan_dosya_404(client: TestClient) -> None:
    kok = rapor_koku(client)
    kok.mkdir(parents=True, exist_ok=True)
    assert iste(client, str(kok / "yok.pdf")).status_code == 404


# ------------------------------------------------------------------ izin
#
# BU UÇ BASKININ TEK GEÇİDİ: kabuk her "Yazdır" düğmesinde buradan geçiyor.
# Kapı bir rolü dışarıda bırakırsa o rol uygulamanın HİÇBİR yerinde çıktı
# alamaz ve belirti "yazıcı hatası" gibi görünür.


def test_outputs_print_butun_yerlesik_rollere_verilir() -> None:
    """Rapor üretebilen her rol onu bastırabilmeli.

    Kapı başlangıçta `print.view`/`settings.view` istiyordu. `print.view` bir
    MODÜL izni — Çıktı Merkezi'nde herkesin çıktısını görme yetkisi — ve Mali
    Müşavir'de yok. O rol `store_reports.view` ve `bbd_canteen_reports.export`
    taşıdığı için ekranda "Yazdır" düğmesi görüyor, tıkladığında 403 alıyordu.
    """
    from km_core.security.identity import BUILTIN_ROLES
    from km_core.security.permissions import CORE_PERMISSIONS

    kayit = next(p for p in CORE_PERMISSIONS if p["key"] == "outputs.print")
    assert set(kayit["default_roles"]) == {rol for rol, *_ in BUILTIN_ROLES}


def test_mali_musavir_belgeyi_alabilir(client: TestClient) -> None:
    """Yalnız `accountant` rolü taşıyan oturum belgeyi indirebilir.

    Kapının kabul ettiği anahtarları değil, GERÇEK bir oturumu sınar: izin
    listesi doğru görünürken rol dağıtımı eksik kalırsa bu test düşer.
    """
    kok = rapor_koku(client)
    kok.mkdir(parents=True, exist_ok=True)
    belge = kok / "mali-rapor.pdf"
    belge.write_bytes(SAHTE_PDF)

    # Kullanıcı UYGULAMANIN KENDİ UCUNDAN açılır: `identity`yi doğrudan çağırmak
    # ayrı bir olay döngüsü kurar ve testin gördüğü depo ile uygulamanınki
    # ayrışır. Buradan açılan kullanıcı gerçek kullanıcıyla aynı yoldan geçer.
    pin = "830514"
    kurulum = client.post(
        "/api/users",
        headers={"Authorization": f"Bearer {token(client)}"},
        json={"firstName": "Mali", "lastName": "Müşavir", "orgScope": "org",
              "roles": ["accountant"], "password": pin},
    )
    assert kurulum.status_code == 201, kurulum.text

    giris = client.post("/api/auth/login", json={"password": pin})
    assert giris.status_code == 200, giris.text
    cevap = client.post(
        "/api/outputs/document",
        headers={"Authorization": f"Bearer {giris.json()['token']}"},
        json={"path": str(belge)},
    )
    assert cevap.status_code == 200, cevap.text
    assert base64.b64decode(cevap.json()["data"]) == SAHTE_PDF
