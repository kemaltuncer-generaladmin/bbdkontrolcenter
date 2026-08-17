"""Kullanıcı yönetimi API'si — K9'un backend tarafı (ADR 0007).

Ekranda gizlemek yetkilendirme değildir: burada sınanan şey, izni olmayan bir
oturumun uçlara ULAŞAMADIĞIDIR. Testler gerçek uygulamayı ayağa kaldırır ve
gerçek oturum belirteciyle konuşur; taklit bir izin nesnesi kullanılmaz —
öyle olsaydı asıl kapıyı (bağımlılık zinciri) hiç sınamazdık.

ADI ŞİFRE, GÖNDERİLEN PIN. ADR 0016 reddedildi ama göçü koştu: gövde alanı
`password`, uç `set-password` ve izin `users.set_password` adlarını korudu.
Aşağıda o adlara verilen her değer 6 haneli bir PIN'dir.

Depo ve kasa anahtarı geçici dizine alınır; ağa çıkılmaz.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security.identity import PASSWORD_UNAVAILABLE

YONETICI_PINI = "482913"
PERSONEL_PINI = "735204"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}

    app = create_app(Config(data, root=ROOT))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def giris(client: TestClient, pin: str) -> Any:
    return client.post("/api/auth/login", json={"password": pin})


def basliklar(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def yonetici_token(client: TestClient) -> str:
    cevap = giris(client, YONETICI_PINI)
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def personel_ac(client: TestClient, token: str, *, pin: str = PERSONEL_PINI,
                roles: list[str] | None = None) -> dict[str, Any]:
    cevap = client.post(
        "/api/users",
        headers=basliklar(token),
        json={
            "firstName": "Veli",
            "lastName": "Demir",
            "orgScope": "org",
            "phoneMobile": "0532 000 11 22",
            "roles": roles or ["org_staff"],
            "password": pin,
        },
    )
    assert cevap.status_code == 201, cevap.text
    return dict(cevap.json()["user"])


# ---------------------------------------------------------------- giriş


def test_ilk_yonetici_piniyle_girer(client: TestClient) -> None:
    cevap = giris(client, YONETICI_PINI)
    assert cevap.status_code == 200
    govde = cevap.json()
    assert govde["token"]
    assert "users.view" in govde["user"]["permissions"]


def test_yanlis_pin_TEK_TIP_401_doner(client: TestClient) -> None:
    cevap = giris(client, "907142")
    assert cevap.status_code == 401
    assert cevap.json()["error"]["message"] == "Giriş yapılamadı."


# ------------------------------------------------------------ izin kapısı


def test_oturumsuz_istek_reddedilir(client: TestClient) -> None:
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/roles").status_code == 401


def test_izinsiz_oturum_kullanici_listesini_goremez(client: TestClient) -> None:
    yonetici = yonetici_token(client)
    personel_ac(client, yonetici)

    personel = str(giris(client, PERSONEL_PINI).json()["token"])
    assert client.get("/api/users", headers=basliklar(personel)).status_code == 403
    assert client.get("/api/roles", headers=basliklar(personel)).status_code == 403


def test_izinsiz_oturum_kullanici_ACAMAZ_ve_PIN_ATAYAMAZ(client: TestClient) -> None:
    yonetici = yonetici_token(client)
    hedef = personel_ac(client, yonetici)

    personel = str(giris(client, PERSONEL_PINI).json()["token"])
    olustur = client.post(
        "/api/users",
        headers=basliklar(personel),
        json={"firstName": "X", "lastName": "Y", "orgScope": "org",
              "roles": ["org_staff"], "password": "628374"},
    )
    assert olustur.status_code == 403

    pin = client.post(
        f"/api/users/{hedef['id']}/password",
        headers=basliklar(personel),
        json={"password": "628374"},
    )
    assert pin.status_code == 403

    durum = client.post(
        f"/api/users/{hedef['id']}/status",
        headers=basliklar(personel),
        json={"status": "disabled"},
    )
    assert durum.status_code == 403


def test_yetkili_oturum_listeyi_gorur(client: TestClient) -> None:
    token = yonetici_token(client)
    cevap = client.get("/api/users", headers=basliklar(token))
    assert cevap.status_code == 200
    kullanicilar = cevap.json()["users"]
    assert len(kullanicilar) == 1
    assert kullanicilar[0]["fullName"] == "Sistem Yöneticisi"


def test_yanit_SIR_ALANI_TASIMAZ(client: TestClient) -> None:
    token = yonetici_token(client)
    kayit = client.get("/api/users", headers=basliklar(token)).json()["users"][0]
    for yasak in ("password_hash", "passwordHash", "secret_lookup", "secretLookup",
                  "pin_hash", "pinHash", "pin_lookup", "pinLookup"):
        assert yasak not in kayit


def test_roller_izinleriyle_dondurulur(client: TestClient) -> None:
    token = yonetici_token(client)
    roller = {rol["id"]: rol for rol in client.get("/api/roles", headers=basliklar(token)).json()["roles"]}

    assert "admin" in roller
    assert "users.set_password" in roller["admin"]["permissions"]
    # Eski anahtar hiçbir rolde kalmamalı (göç 0003).
    assert all("users.set_pin" not in rol["permissions"] for rol in roller.values())


# ----------------------------------------------------------------- PIN


def test_ayni_pin_ikinci_kullaniciya_atanamaz_ve_KIMI_SOYLEMEZ(client: TestClient) -> None:
    token = yonetici_token(client)
    personel_ac(client, token)

    cevap = client.post(
        "/api/users",
        headers=basliklar(token),
        json={"firstName": "Ayse", "lastName": "Yilmaz", "orgScope": "org",
              "roles": ["org_staff"], "password": PERSONEL_PINI},
    )
    assert cevap.status_code == 400
    mesaj = cevap.json()["error"]["message"]
    assert mesaj == PASSWORD_UNAVAILABLE
    assert "Veli" not in mesaj and "Demir" not in mesaj


def test_kisa_pin_reddedilir(client: TestClient) -> None:
    token = yonetici_token(client)
    cevap = client.post(
        "/api/users",
        headers=basliklar(token),
        json={"firstName": "Ayse", "lastName": "Yilmaz", "orgScope": "org",
              "roles": ["org_staff"], "password": "4829"},
    )
    assert cevap.status_code == 400
    assert "6" in cevap.json()["error"]["message"]


def test_rakam_disi_pin_reddedilir(client: TestClient) -> None:
    token = yonetici_token(client)
    cevap = client.post(
        "/api/users",
        headers=basliklar(token),
        json={"firstName": "Ayse", "lastName": "Yilmaz", "orgScope": "org",
              "roles": ["org_staff"], "password": "cinaraltindaki-kuyu"},
    )
    assert cevap.status_code == 400
    assert "rakam" in cevap.json()["error"]["message"].casefold()


def test_yonetici_pin_sifirlayabilir(client: TestClient) -> None:
    token = yonetici_token(client)
    hedef = personel_ac(client, token)

    cevap = client.post(
        f"/api/users/{hedef['id']}/password",
        headers=basliklar(token),
        json={"password": "519286"},
    )
    assert cevap.status_code == 200
    assert giris(client, "519286").status_code == 200
    assert giris(client, PERSONEL_PINI).status_code == 401


def test_kullanici_kendi_pinini_degistirir(client: TestClient) -> None:
    yonetici = yonetici_token(client)
    personel_ac(client, yonetici)

    cevap = client.post(
        "/api/auth/set-password",
        json={"currentSecret": PERSONEL_PINI, "password": "471039"},
    )
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["token"]
    assert giris(client, "471039").status_code == 200


def test_yanlis_mevcut_sirla_pin_degismez(client: TestClient) -> None:
    cevap = client.post(
        "/api/auth/set-password",
        json={"currentSecret": "805213", "password": "471039"},
    )
    assert cevap.status_code == 401


# ------------------------------------------------------- düzenleme / durum


def test_kullanici_duzenlenir(client: TestClient) -> None:
    token = yonetici_token(client)
    hedef = personel_ac(client, token)

    cevap = client.put(
        f"/api/users/{hedef['id']}",
        headers=basliklar(token),
        json={"title": "Depo Sorumlusu", "expectedRevision": hedef["revision"]},
    )
    assert cevap.status_code == 200
    kayit = cevap.json()["user"]
    assert kayit["title"] == "Depo Sorumlusu"
    assert kayit["revision"] == hedef["revision"] + 1
    # Gönderilmeyen alan DOKUNULMAZ.
    assert kayit["lastName"] == "Demir"


def test_eski_revizyonla_yazma_409_doner(client: TestClient) -> None:
    token = yonetici_token(client)
    hedef = personel_ac(client, token)

    client.put(f"/api/users/{hedef['id']}", headers=basliklar(token),
               json={"title": "Once"})
    cevap = client.put(
        f"/api/users/{hedef['id']}",
        headers=basliklar(token),
        json={"title": "Sonra", "expectedRevision": hedef["revision"]},
    )
    assert cevap.status_code == 409


def test_son_yonetici_pasiflestirilemez(client: TestClient) -> None:
    token = yonetici_token(client)
    yonetici_id = client.get("/auth/me", headers=basliklar(token)).json()["id"]

    cevap = client.post(
        f"/api/users/{yonetici_id}/status",
        headers=basliklar(token),
        json={"status": "disabled"},
    )
    assert cevap.status_code == 400


def test_pasiflestirilen_kullanici_giremez(client: TestClient) -> None:
    token = yonetici_token(client)
    hedef = personel_ac(client, token)

    cevap = client.post(
        f"/api/users/{hedef['id']}/status",
        headers=basliklar(token),
        json={"status": "disabled"},
    )
    assert cevap.status_code == 200
    assert giris(client, PERSONEL_PINI).status_code == 401


def test_olmayan_kullanici_404(client: TestClient) -> None:
    token = yonetici_token(client)
    cevap = client.put("/api/users/yok-boyle-biri", headers=basliklar(token),
                       json={"title": "X"})
    assert cevap.status_code == 404
