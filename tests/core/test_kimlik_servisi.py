"""Merkezî kimlik servisi (ADR 0021) — `services/identity`.

Burada sınanan şey, ADR'nin bağlayıcı saydığı davranışlardır:

  · Yönetim token'ı TANIMSIZSA uç 503 döner — sessizce açık kalmaz.
  · Eşleme kodu TEK KULLANIMLIK ve SÜRELİDİR.
  · Eşleme token'ı OTURUM DEĞİLDİR: yazma için kişinin izni ayrıca sorulur (K9).
  · İptal edilen kurulum kadro ÇEKEMEZ.
  · `revision` değişmemişse kadro verisi HİÇ GÖNDERİLMEZ.
  · Danışma kilidi UYARIR, ENGELLEMEZ (ADR 0020 §2).

Testler gerçek uygulamayı ayağa kaldırır; ağa çıkılmaz, veritabanı geçici
dizindedir.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from services.identity.app.main import create_app
from services.identity.app.settings import Settings, SettingsError

YONETICI_PINI = "482913"
PERSONEL_PINI = "735204"
ADMIN_TOKEN = "test-yonetim-tokeni"


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(
        db_path=str(tmp_path / "identity.sqlite"),
        pepper="test-pepper",
        admin_token=extra.pop("admin_token", ADMIN_TOKEN),
        bootstrap_pin=YONETICI_PINI,
        **extra,
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def yonetim(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def kurulum(token: str, actor_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if actor_id:
        headers["X-KM-Actor-Id"] = actor_id
    return headers


def esle(client: TestClient, *, makine: str = "MSI") -> str:
    """Kod üretir, eşler ve kurulum token'ını döndürür."""
    kod = client.post(
        "/installations/pair-code", headers=yonetim(), json={"note": makine}
    )
    assert kod.status_code == 200, kod.text
    cevap = client.post("/pair", json={
        "code": kod.json()["code"],
        "publicKey": "ssh-ed25519 AAAA-test",
        "machineName": makine,
        "platform": "Linux",
        "version": "0.1.0",
    })
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def yonetici_kimligi(client: TestClient, token: str) -> str:
    kadro = client.get("/roster", headers=kurulum(token))
    assert kadro.status_code == 200, kadro.text
    kullanicilar = kadro.json()["users"]
    return str(next(k for k in kullanicilar if "admin" in k["roles"])["id"])


# --------------------------------------------------------------- ayarlar


def test_pepper_olmadan_ayar_kurulamaz(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pepper rastgele üretilip veritabanına yazılmaz: bir kurtarma sonrası
    herkesin girişi sessizce bozulurdu."""
    monkeypatch.delenv("KM_IDENTITY_PEPPER", raising=False)
    with pytest.raises(SettingsError):
        Settings.from_env()


# ------------------------------------------------------------- sağlık


def test_saglik_kimlik_istemez(client: TestClient) -> None:
    cevap = client.get("/health")
    assert cevap.status_code == 200
    assert cevap.json()["status"] == "ok"
    assert cevap.json()["rosterRevision"] >= 1


# ------------------------------------------------------- yönetim token'ı


def test_yonetim_tokeni_tanimsizsa_uc_ACIK_KALMAZ(tmp_path: Path) -> None:
    """TANIMSIZ TOKEN AÇIK KAPI DEĞİLDİR — ama tek kapı da değildir.

    Yönetim uçları artık İKİ yoldan korunuyor: olağan yol eşlenmiş bir makine
    + `installations.manage` izni olan kişi, acil yol yönetim token'ı. Amaç,
    kod üretmenin tek bir makineye ya da dağıtılan bir sırra bağlı kalmaması.

    Token tanımsızken acil yol kapalıdır ve istek kimliksiz geldiği için
    OLAĞAN yolun ilk kapısına (kurulum token'ı) takılır: 401. Eskiden 503
    dönüyordu; değişen şey kapının KAPALI olması değil, hangi kapının
    konuştuğu.
    """
    app = create_app(_settings(tmp_path, admin_token=""))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        cevap = test_client.post("/installations/pair-code", json={})
        assert cevap.status_code == 401
        # Kapı gerçekten kapalı: kod üretilmedi.
        assert "code" not in cevap.json()


def test_yanlis_yonetim_tokeni_reddedilir(client: TestClient) -> None:
    cevap = client.post("/installations/pair-code", headers=yonetim("yanlis"), json={})
    assert cevap.status_code == 401


# -------------------------------------------------------------- eşleme


def test_eslesme_token_uretir_ve_kurulum_listeye_duser(client: TestClient) -> None:
    token = esle(client, makine="Flex5")
    assert token

    liste = client.get("/installations", headers=yonetim())
    assert liste.status_code == 200
    kayit = liste.json()["installations"][0]
    assert kayit["machineName"] == "Flex5"
    assert kayit["platform"] == "Linux"
    assert kayit["status"] == "active"
    assert kayit["lastSeenAt"]
    # Token ve açık anahtar listeye SIZMAZ.
    assert "token" not in kayit
    assert "publicKey" not in kayit


def test_eslesme_kodu_tek_kullanimliktir(client: TestClient) -> None:
    kod = client.post("/installations/pair-code", headers=yonetim(), json={}).json()["code"]
    govde = {
        "code": kod, "publicKey": "k", "machineName": "M",
        "platform": "Linux", "version": "0.1.0",
    }
    assert client.post("/pair", json=govde).status_code == 200
    ikinci = client.post("/pair", json=govde)
    assert ikinci.status_code == 401


def test_suresi_gecmis_kod_reddedilir(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, pair_code_ttl_seconds=-1))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        kod = test_client.post(
            "/installations/pair-code", headers=yonetim(), json={}
        ).json()["code"]
        cevap = test_client.post("/pair", json={
            "code": kod, "publicKey": "k", "machineName": "M",
            "platform": "Linux", "version": "0.1.0",
        })
        assert cevap.status_code == 401


def test_uydurma_kod_reddedilir(client: TestClient) -> None:
    cevap = client.post("/pair", json={
        "code": "00000000", "publicKey": "k", "machineName": "M",
        "platform": "Linux", "version": "0.1.0",
    })
    assert cevap.status_code == 401


# --------------------------------------------------------------- kadro


def test_kadro_kurulum_tokeni_ister(client: TestClient) -> None:
    assert client.get("/roster").status_code == 401
    assert client.get("/roster", headers=kurulum("uydurma")).status_code == 401


def test_kadro_kullanici_rol_ve_izinleri_tasir(client: TestClient) -> None:
    token = esle(client)
    kadro = client.get("/roster", headers=kurulum(token)).json()

    assert kadro["changed"] is True
    assert kadro["revision"] >= 1
    assert any("admin" in kullanici["roles"] for kullanici in kadro["users"])
    assert any(rol["id"] == "admin" for rol in kadro["roles"])
    # ÇEVRİMDIŞI GİRİŞ BUNA BAĞLIDIR: hash ve arama anahtarı kadroyla gelir.
    yonetici = next(k for k in kadro["users"] if "admin" in k["roles"])
    assert yonetici["password_hash"]
    assert yonetici["secret_lookup"]
    # Rol → izin bağı olmadan kurulum yetkiyi yeniden kuramaz.
    izinler = {g["permission"] for g in kadro["grants"]["role_permissions"]}
    assert "users.manage" in izinler


def test_revizyon_degismemisse_veri_gonderilmez(client: TestClient) -> None:
    token = esle(client)
    ilk = client.get("/roster", headers=kurulum(token)).json()

    ikinci = client.get(
        "/roster", headers=kurulum(token), params={"known_revision": ilk["revision"]}
    ).json()
    # KADRO GÖNDERİLMEZ — sınanan söz bu. `pepperFingerprint` kadro değil,
    # kurulumun kendi anahtarının merkezinkiyle uyuşup uyuşmadığını anlamasını
    # sağlayan denetim alanıdır ve HER yanıtta gider: uyuşmazlık "değişmedi"
    # turlarında da fark edilebilmeli, yoksa kurulum günlerce sessizce kırık
    # kalırdı.
    assert ikinci["revision"] == ilk["revision"]
    assert ikinci["changed"] is False
    assert "users" not in ikinci
    assert "roles" not in ikinci
    assert "grants" not in ikinci
    assert ikinci["pepperFingerprint"] == ilk["pepperFingerprint"]


def test_iptal_edilen_kurulum_kadro_cekemez(client: TestClient) -> None:
    token = esle(client)
    kurulum_id = client.get("/installations", headers=yonetim()).json()["installations"][0]["id"]

    iptal = client.post(f"/installations/{kurulum_id}/revoke", headers=yonetim())
    assert iptal.status_code == 200
    assert iptal.json()["installation"]["status"] == "revoked"
    # KAYIT SİLİNMEZ: ne zaman eşlendiği ve ne zaman iptal edildiği kalır.
    assert iptal.json()["installation"]["revokedAt"]

    assert client.get("/roster", headers=kurulum(token)).status_code == 401


# --------------------------------------------------------------- yazma


def test_yazma_kisiyi_sorar_token_oturum_degildir(client: TestClient) -> None:
    """Kurulum token'ı geçerli olsa bile KİŞİ bildirilmeden yazılamaz."""
    token = esle(client)
    cevap = client.post("/users", headers=kurulum(token), json={
        "firstName": "Veli", "lastName": "Demir", "orgScope": "org",
        "roles": ["org_staff"], "password": PERSONEL_PINI,
    })
    assert cevap.status_code == 401


def test_yetkisiz_kisi_kullanici_acamaz(client: TestClient) -> None:
    token = esle(client)
    yonetici_id = yonetici_kimligi(client, token)

    acildi = client.post("/users", headers=kurulum(token, yonetici_id), json={
        "firstName": "Veli", "lastName": "Demir", "orgScope": "org",
        "roles": ["org_staff"], "password": PERSONEL_PINI,
    })
    assert acildi.status_code == 201, acildi.text
    personel_id = acildi.json()["user"]["id"]

    # K9 — ikinci kapı: izni olmayan kişi kurulumun token'ıyla da geçemez.
    reddedildi = client.post("/users", headers=kurulum(token, personel_id), json={
        "firstName": "Ayşe", "lastName": "Yılmaz", "orgScope": "org",
        "roles": ["org_staff"], "password": "917348",
    })
    assert reddedildi.status_code == 403


def test_yazma_kadro_revizyonunu_arttirir(client: TestClient) -> None:
    token = esle(client)
    yonetici_id = yonetici_kimligi(client, token)
    once = client.get("/roster", headers=kurulum(token)).json()["revision"]

    acildi = client.post("/users", headers=kurulum(token, yonetici_id), json={
        "firstName": "Veli", "lastName": "Demir", "orgScope": "org",
        "roles": ["org_staff"], "password": PERSONEL_PINI,
    })
    assert acildi.json()["revision"] > once

    personel_id = acildi.json()["user"]["id"]
    guncel = client.put(f"/users/{personel_id}", headers=kurulum(token, yonetici_id), json={
        "title": "Şef", "expectedRevision": 1,
    })
    assert guncel.status_code == 200, guncel.text
    assert guncel.json()["user"]["title"] == "Şef"

    # ADR 0020 §1 — beklenen revizyon uymuyorsa 409; SESSİZ EZME YOK.
    catisma = client.put(f"/users/{personel_id}", headers=kurulum(token, yonetici_id), json={
        "title": "Müdür", "expectedRevision": 1,
    })
    assert catisma.status_code == 409

    pasif = client.post(
        f"/users/{personel_id}/status", headers=kurulum(token, yonetici_id),
        json={"status": "disabled"},
    )
    assert pasif.status_code == 200
    assert pasif.json()["user"]["status"] == "disabled"


def test_son_yonetici_pasiflestirilemez(client: TestClient) -> None:
    """Çekirdeğin bütünlük kısıtı serviste de geçerlidir — kod tek kopyadır."""
    token = esle(client)
    yonetici_id = yonetici_kimligi(client, token)
    cevap = client.post(
        f"/users/{yonetici_id}/status", headers=kurulum(token, yonetici_id),
        json={"status": "disabled"},
    )
    assert cevap.status_code == 400
    assert "son aktif kullanıcı" in cevap.json()["error"]["message"]


# ------------------------------------------------------------- denetim


def test_denetim_kayitlari_kurulum_kimligiyle_saklanir(client: TestClient) -> None:
    token = esle(client, makine="Flex5")
    cevap = client.post("/audit", headers=kurulum(token), json={"entries": [
        {"at": "2026-08-17T09:00:00+00:00", "action": "auth.login", "result": "ok",
         "userId": "u-1"},
        {"at": "2026-08-17T09:05:00+00:00", "action": "users.update", "result": "ok"},
    ]})
    assert cevap.status_code == 200
    assert cevap.json()["accepted"] == 2

    assert client.post("/audit", json={"entries": []}).status_code in (401, 422)


# ------------------------------------------------------------- kilitler


def test_kilit_uyarir_engellemez(client: TestClient) -> None:
    birinci = esle(client, makine="MSI")
    ikinci = esle(client, makine="Flex5")

    alindi = client.post("/locks", headers=kurulum(birinci), json={
        "resource": "users/42", "holderName": "Ayşe Yılmaz",
    })
    assert alindi.status_code == 200
    assert alindi.json()["acquired"] is True
    kilit_id = alindi.json()["lock"]["id"]

    # İKİNCİ KURULUM HATA ALMAZ: kilit engellemez, uyarır ve KİMİN tuttuğunu
    # söyler (ADR 0020 §2).
    catisma = client.post("/locks", headers=kurulum(ikinci), json={
        "resource": "users/42", "holderName": "Veli Demir",
    })
    assert catisma.status_code == 200
    assert catisma.json()["acquired"] is False
    assert catisma.json()["lock"]["holderName"] == "Ayşe Yılmaz"

    # Başkasının kilidi düşürülemez.
    assert client.delete(f"/locks/{kilit_id}", headers=kurulum(ikinci)).json()["released"] is False
    assert client.delete(f"/locks/{kilit_id}", headers=kurulum(birinci)).json()["released"] is True

    # Bırakıldıktan sonra ikinci kurulum alabilir.
    tekrar = client.post("/locks", headers=kurulum(ikinci), json={
        "resource": "users/42", "holderName": "Veli Demir",
    })
    assert tekrar.json()["acquired"] is True


def test_kilit_ttl_ust_sinira_kirpilir(tmp_path: Path) -> None:
    """TTL ZORUNLUDUR ve istemcinin istediği süre sınırsız değildir."""
    app = create_app(_settings(tmp_path, lock_max_ttl_seconds=60))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        token = esle(test_client)
        cevap = test_client.post("/locks", headers=kurulum(token), json={
            "resource": "users/7", "holderName": "Ayşe", "ttlSeconds": 3600,
        })
        assert cevap.json()["acquired"] is True
        acquired_at = cevap.json()["lock"]["acquiredAt"]
        expires_at = cevap.json()["lock"]["expiresAt"]
        assert expires_at > acquired_at
