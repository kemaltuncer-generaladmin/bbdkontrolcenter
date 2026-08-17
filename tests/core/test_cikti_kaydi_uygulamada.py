"""Çıktı kaydının uygulamaya BAĞLANDIĞI yer (ADR 0019 §1–§2).

Kayıt katmanının kendi davranışı `test_outputs_log.py` içinde sınanır. Burada
sınanan şey, o katmanın gerçek uygulamaya bağlı olup olmadığıdır — ikisi ayrı
sorulardır ve ikincisi uzun süre cevapsız kaldı:

- Depo BİLDİRİLİYOR MU? `use_database()` yazılmıştı ama kimse çağırmıyordu;
  kayıt hangi veritabanına yazacağını ayardan tahmin ediyordu.
- ÜRETEN KİŞİ yazılıyor mu? `use_actor()` yazılmıştı ama oturumun çözüldüğü
  yere bağlanmamıştı; `outputs.user_id` her satırda boştu ve "geçen ayın
  raporunu kim aldı" sorusu — ADR'nin çıkış noktası — cevapsızdı.

Gerçek uygulama ayağa kaldırılır ve gerçek oturum belirteciyle konuşulur:
taklit bir bağlam kurulsaydı asıl kapıyı (bağımlılık zinciri) hiç sınamazdık.

Depo, kasa anahtarı ve ÇIKTI KLASÖRÜ geçici dizine alınır — takım kullanıcının
masaüstüne dosya bırakmaz.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.files import outputs_log
from km_core.http.app import create_app

YONETICI_PINI = "482913"
IKINCI_PINI = "735204"


@pytest.fixture
def depo(tmp_path: Path) -> Path:
    return tmp_path / "km.sqlite"


@pytest.fixture
def client(tmp_path: Path, depo: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(depo),
        "secret_key_path": str(tmp_path / "secret.key"),
        "log_path": str(tmp_path / "gunluk.log"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    # Destek paketi buraya yazılır; masaüstüne dokunulmaz.
    data["files"] = {"output_path": str(tmp_path / "cikti")}

    app = create_app(Config(data, root=ROOT))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def satirlar(depo: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(depo)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in
                connection.execute("SELECT * FROM outputs ORDER BY rowid")]
    finally:
        connection.close()


def token_al(client: TestClient, pin: str) -> str:
    cevap = client.post("/api/auth/login", json={"password": pin})
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def basliklar(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def kimlik(client: TestClient, token: str) -> str:
    cevap = client.get("/auth/me", headers=basliklar(token))
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["id"])


def destek_paketi(client: TestClient, token: str) -> Any:
    return client.post("/api/settings/support-bundle", headers=basliklar(token))


# ------------------------------------------------------------ depo bağlama


def test_acilis_depoyu_BILDIRIR(client: TestClient, depo: Path) -> None:
    """Uygulama ayaktayken kayıt, uygulamanın KENDİ deposuna bağlıdır."""
    assert outputs_log.database_path() == depo


def test_kapanista_bag_COZULUR(tmp_path: Path) -> None:
    """Kapanan uygulama bağı bırakır; ardından yazılan dosya kayıtsızdır.

    Bırakılmasaydı, kapanmış bir uygulamanın deposu süreçte asılı kalır ve
    sonraki her yazma oraya düşerdi — geliştirme veritabanının kirlenme yolu
    tam olarak buydu.
    """
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
        "log_path": str(tmp_path / "gunluk.log"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    data["files"] = {"output_path": str(tmp_path / "cikti")}

    with TestClient(create_app(Config(data, root=ROOT))):
        assert outputs_log.database_path() is not None
    assert outputs_log.database_path() is None


def test_cikti_uygulamanin_deposuna_DUSER(client: TestClient, depo: Path) -> None:
    """Destek paketi çekirdeğin kendi çıktısıdır ve listede görünür."""
    token = token_al(client, YONETICI_PINI)
    cevap = destek_paketi(client, token)
    assert cevap.status_code == 200, cevap.text

    (kayit,) = satirlar(depo)
    assert kayit["path"] == cevap.json()["path"]
    assert kayit["kind"] == "zip"
    assert kayit["source"] == outputs_log.CORE_SOURCE


# ---------------------------------------------------------- üreten kullanıcı


def test_ureten_kullanici_KAYDEDILIR(client: TestClient, depo: Path) -> None:
    """ADR'nin çıkış sorusu: "bu raporu kim aldı" — cevabı satırda durur."""
    token = token_al(client, YONETICI_PINI)
    yonetici = kimlik(client, token)

    assert destek_paketi(client, token).status_code == 200

    (kayit,) = satirlar(depo)
    assert kayit["user_id"] == yonetici


def test_kimlik_SONRAKI_ISTEGE_SIZMAZ(client: TestClient, depo: Path) -> None:
    """Bağlam istekle biter: ikinci çıktı ikinci kişinin adına yazılır.

    Bağlam değişkeni istek sonunda geri alınmasaydı, aynı olay döngüsünde
    sırayı devralan istek başkasının kimliğiyle çıktı üretirdi. Kaydın yanlış
    olması, hiç olmamasından kötüdür: "ben almadım" diyen kişinin adı listede
    durur.
    """
    yonetici_token = token_al(client, YONETICI_PINI)
    yonetici = kimlik(client, yonetici_token)

    acilis = client.post(
        "/api/users",
        headers=basliklar(yonetici_token),
        json={
            "firstName": "İkinci",
            "lastName": "Yönetici",
            "orgScope": "org",
            "roles": ["admin"],
            "password": IKINCI_PINI,
        },
    )
    assert acilis.status_code == 201, acilis.text

    ikinci_token = token_al(client, IKINCI_PINI)
    ikinci = kimlik(client, ikinci_token)
    assert ikinci != yonetici

    assert destek_paketi(client, yonetici_token).status_code == 200
    assert destek_paketi(client, ikinci_token).status_code == 200

    ilk_kayit, ikinci_kayit = satirlar(depo)
    assert ilk_kayit["user_id"] == yonetici
    assert ikinci_kayit["user_id"] == ikinci
