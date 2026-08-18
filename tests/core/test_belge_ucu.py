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
