"""Kadro göçü: `POST /roster/import` ve `scripts/push-roster.py`.

Merkez (ADR 0021) kadro biriktikten SONRA kuruldu; kadrosunda yalnız dağıtımda
doğan bootstrap yöneticisi var. Bu takım, var olan bir kurulumun kullanıcılarını
PIN'LERİ DEĞİŞMEDEN merkeze taşıyan yolu sınar.

Bağlayıcı davranışlar:

  · Uç YALNIZ yönetim token'ıyla açılır — kurulum token'ı yetmez.
  · İki kez koşmak kadroyu İKİZLEMEZ ve var olan satırı EZMEZ.
  · `secret_lookup` çakışması ve tanımsız rol satırı atlatır; sessizce yutulmaz.
  · Betik varsayılan olarak KURU PROVADIR: hiçbir şey göndermez.

Testler ağa çıkmaz; gönderim işlevi taklit edilir.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from services.identity.app.main import create_app
from services.identity.app.settings import Settings

from km_core.security.identity import Identity
from km_core.security.migrations import apply_core_migrations
from km_core.store.db import Store

ROOT = Path(__file__).resolve().parents[2]

YONETICI_PINI = "482913"
PERSONEL_PINI = "735204"
IKINCI_PIN = "917348"
ADMIN_TOKEN = "test-yonetim-tokeni"

# Göçte taşınan gerçek alanların temsilcileri. Argon2 hash'i ve HMAC lookup'ı
# OLDUĞU GİBİ taşınır; testte biçimleri yeterlidir, çünkü merkez onları
# doğrulamaz — yalnız saklar ve kadroyla geri verir.
SAHTE_HASH = "$argon2id$v=19$m=65536,t=3,p=4$" + "A" * 22 + "$" + "B" * 43


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


def esle(client: TestClient, *, makine: str = "Flex5") -> str:
    kod = client.post("/installations/pair-code", headers=yonetim(), json={"note": makine})
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


def kadro(client: TestClient, token: str) -> dict[str, Any]:
    cevap = client.get("/roster", headers={"Authorization": f"Bearer {token}"})
    assert cevap.status_code == 200, cevap.text
    return dict(cevap.json())


def gocmen(**over: Any) -> dict[str, Any]:
    """Taşınacak tek kullanıcı. Kimlik (uuid) KORUNUR."""
    govde: dict[str, Any] = {
        "id": "c1b24707-b7dc-41b1-a0a5-88dff0e56655",
        "firstName": "Hasan Hüseyin",
        "lastName": "Bardakcı",
        "orgScope": "org",
        "roles": ["admin", "accountant"],
        "passwordHash": SAHTE_HASH,
        "secretLookup": "a" * 64,
        "passwordSetAt": "2026-08-17T15:45:22+00:00",
        "status": "active",
    }
    govde.update(over)
    return govde


def ikinci_gocmen(**over: Any) -> dict[str, Any]:
    return gocmen(**{
        "id": "b1e65fde-1f85-4edf-a77b-9ce413c3c7fe",
        "firstName": "Mücahit Ziya",
        "lastName": "Bardakcı",
        "roles": ["bld_staff"],
        "secretLookup": "b" * 64,
        **over,
    })


# ------------------------------------------------------------------ kapı


def test_ice_aktarma_yonetim_tokeni_ister(client: TestClient) -> None:
    assert client.post("/roster/import", json={"users": [gocmen()]}).status_code == 401
    yanlis = client.post("/roster/import", headers=yonetim("yanlis"),
                         json={"users": [gocmen()]})
    assert yanlis.status_code == 401


def test_kurulum_tokeni_ice_aktarmaya_yetmez(client: TestClient) -> None:
    """Kurulum token'ı 'bu makine bizim' der, 'kadroyu değiştirebilirim' demez.

    Eşlenmiş her makinenin kadroyu toptan yazabilmesi, çalınan bir makineyi
    merkezin sahibi yapardı.
    """
    kurulum_tokeni = esle(client)
    cevap = client.post("/roster/import",
                        headers={"Authorization": f"Bearer {kurulum_tokeni}"},
                        json={"users": [gocmen()]})
    assert cevap.status_code == 401


def test_yonetim_tokeni_tanimsizsa_uc_kapali(tmp_path: Path) -> None:
    """TANIMSIZ TOKEN AÇIK KAPI DEĞİLDİR (auth.py ile aynı sözleşme)."""
    app = create_app(_settings(tmp_path, admin_token=""))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        cevap = test_client.post("/roster/import", json={"users": [gocmen()]})
        assert cevap.status_code == 503


# ----------------------------------------------------------------- taşıma


def test_kullanicilar_kimlikleri_ve_pinleriyle_tasinir(client: TestClient) -> None:
    """ÇEVRİMDIŞI GİRİŞ BUNA BAĞLIDIR: hash ve arama anahtarı olduğu gibi gider."""
    token = esle(client)
    once = client.get("/health").json()["rosterRevision"]

    cevap = client.post("/roster/import", headers=yonetim(),
                        json={"users": [gocmen(), ikinci_gocmen()]})
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["added"] == 2
    assert cevap.json()["skipped"] == 0
    # Kurulumlar tazelensin diye revizyon artar (ADR 0021 §2).
    assert cevap.json()["revision"] > once

    liste = kadro(client, token)["users"]
    tasinan = next(k for k in liste if k["id"] == gocmen()["id"])
    assert tasinan["first_name"] == "Hasan Hüseyin"
    assert tasinan["password_hash"] == SAHTE_HASH
    assert tasinan["secret_lookup"] == "a" * 64
    assert tasinan["password_set_at"] == "2026-08-17T15:45:22+00:00"
    assert sorted(tasinan["roles"]) == ["accountant", "admin"]

    # VAR OLAN KAYIT SİLİNMEZ: merkezin kendi yöneticisi yerinde durur.
    assert any("admin" in k["roles"] and k["id"] != gocmen()["id"] for k in liste)


def test_iki_kez_kosmak_kadroyu_ikizlemez(client: TestClient) -> None:
    """İdempotenslik: aynı kimlik ikinci kez gelirse ATLANIR, ikinci satır açılmaz."""
    token = esle(client)
    ilk = client.post("/roster/import", headers=yonetim(),
                      json={"users": [gocmen(), ikinci_gocmen()]})
    assert ilk.json()["added"] == 2
    sayi = len(kadro(client, token)["users"])
    revizyon = client.get("/health").json()["rosterRevision"]

    ikinci = client.post("/roster/import", headers=yonetim(),
                         json={"users": [gocmen(), ikinci_gocmen()]})
    assert ikinci.status_code == 200
    assert ikinci.json()["added"] == 0
    assert ikinci.json()["skipped"] == 2
    assert {skip["code"] for skip in ikinci.json()["skips"]} == {"zaten_var"}
    assert len(kadro(client, token)["users"]) == sayi
    # HİÇBİR ŞEY EKLENMEDİYSE REVİZYON ARTMAZ: eşli her kurulumu boşuna tam
    # kadro indirmeye zorlamak, "değişmemişse veri çekilmez" kuralını boşa çıkarır.
    assert client.get("/health").json()["rosterRevision"] == revizyon


def test_var_olan_kayit_ezilmez(client: TestClient) -> None:
    """İkinci koşuda gelen FARKLI veri var olan satırı DEĞİŞTİRMEZ.

    Uç yalnız yönetim token'ıyla korunur; arkasında bir kişi ve izin denetimi
    yoktur (K9). Var olanı ezebilseydi `PUT /users/{id}` yolunun denetimini
    atlayan ikinci bir yazma kanalı açılırdı.
    """
    token = esle(client)
    client.post("/roster/import", headers=yonetim(), json={"users": [gocmen()]})

    client.post("/roster/import", headers=yonetim(),
                json={"users": [gocmen(firstName="Başkası", roles=["org_staff"])]})

    satir = next(k for k in kadro(client, token)["users"] if k["id"] == gocmen()["id"])
    assert satir["first_name"] == "Hasan Hüseyin"
    assert sorted(satir["roles"]) == ["accountant", "admin"]


# ---------------------------------------------------------------- atlama


def test_sir_catismasi_satiri_atlar_ve_nedenini_soyler(client: TestClient) -> None:
    """Aynı PIN iki kişiye verilemez; çakışan satır SESSİZCE YUTULMAZ."""
    token = esle(client)
    yonetici = next(k for k in kadro(client, token)["users"] if "admin" in k["roles"])

    cevap = client.post("/roster/import", headers=yonetim(), json={
        "users": [gocmen(secretLookup=yonetici["secret_lookup"])],
    })
    assert cevap.status_code == 200
    assert cevap.json()["added"] == 0
    skip = cevap.json()["skips"][0]
    assert skip["code"] == "sir_catismasi"
    assert "PIN" in skip["reason"]
    # KİMİNLE çakıştığı söylenmez: deneme yoluyla başkasının PIN'ini öğrenmeye
    # kapı açardı (Identity._assert_password_free ile aynı kural).
    assert yonetici["id"] not in skip["reason"]
    assert all(k["id"] != gocmen()["id"] for k in kadro(client, token)["users"])


def test_bilinmeyen_rol_satiri_reddeder(client: TestClient) -> None:
    """Rol merkezde yoksa satır reddedilir; rol KENDİLİĞİNDEN AÇILMAZ."""
    token = esle(client)
    cevap = client.post("/roster/import", headers=yonetim(),
                        json={"users": [gocmen(roles=["mudur"])]})
    assert cevap.json()["added"] == 0
    skip = cevap.json()["skips"][0]
    assert skip["code"] == "tanimsiz_rol"
    assert "mudur" in skip["reason"]

    liste = kadro(client, token)
    assert all(k["id"] != gocmen()["id"] for k in liste["users"])
    assert all(rol["id"] != "mudur" for rol in liste["roles"])


def test_atlanan_satir_digerlerini_durdurmaz(client: TestClient) -> None:
    token = esle(client)
    cevap = client.post("/roster/import", headers=yonetim(), json={
        "users": [gocmen(roles=["mudur"]), ikinci_gocmen()],
    })
    assert cevap.json()["added"] == 1
    assert cevap.json()["skipped"] == 1
    assert any(k["id"] == ikinci_gocmen()["id"] for k in kadro(client, token)["users"])


# ----------------------------------------------------------------- betik


def _betik() -> Any:
    """`scripts/push-roster.py` — adında tire olduğu için yoldan yüklenir."""
    source = ROOT / "scripts" / "push-roster.py"
    spec = importlib.util.spec_from_file_location("push_roster", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["push_roster"] = module
    spec.loader.exec_module(module)
    return module


async def _yerel_veritabani(tmp_path: Path) -> Path:
    """Gerçek çekirdek şemasıyla küçük bir yerel kurulum.

    Sahte tablo kurulmaz: betiğin okuduğu sütunlar (`origin`, `created_by`,
    `secret_lookup`) göçlerle gelir; taklit bir şema, göç değiştiğinde testi
    sessizce yanıltırdı.
    """
    path = tmp_path / "kontrol-merkezi.sqlite"
    store = Store(path)
    await store.open()
    await apply_core_migrations(store)
    identity = Identity(store, pepper="test-pepper")
    await identity.ensure_builtin_roles()
    # Bootstrap yöneticisi: kimse adına açılmadı, `created_by` boş.
    yonetici_id = await identity.create_user(
        first_name="Sistem", last_name="Yöneticisi", org_scope="org",
        password=YONETICI_PINI, roles=["admin"],
    )
    await identity.create_user(
        first_name="Zahide", last_name="BLD", org_scope="org",
        password=PERSONEL_PINI, roles=["bld_staff"], created_by=yonetici_id,
    )
    await identity.create_user(
        first_name="Merve", last_name="BLD", org_scope="org",
        password=IKINCI_PIN, roles=["bld_staff"], created_by=yonetici_id,
    )
    await store.close()
    return path


async def test_kuru_prova_hicbir_sey_gondermez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """VARSAYILAN KURU PROVADIR. Ne gönderileceğini yazar, göndermez."""
    path = await _yerel_veritabani(tmp_path)
    betik = _betik()
    cagrilar: list[Any] = []
    monkeypatch.setattr(betik, "_gonder", lambda *a, **k: cagrilar.append((a, k)))
    monkeypatch.setenv(betik.TOKEN_ENV, "test-yonetim-tokeni")

    kod = betik.main(["--db", str(path), "--merkez", "https://ornek.gecersiz"])
    assert kod == 0
    assert cagrilar == []

    cikti = capsys.readouterr().out
    assert "KURU PROVA" in cikti
    assert "Zahide BLD" in cikti and "Merve BLD" in cikti
    # Bootstrap yöneticisi gönderilmez ve NEDENİ yazılır.
    assert "Sistem Yöneticisi" in cikti
    assert "--bootstrap-dahil" in cikti
    assert "SALT OKUNUR" in cikti


async def test_kuru_prova_kaynak_veritabanina_yazmaz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Betik göç sırasında yerel kadroyu değiştirmez — dosya bit bit aynı kalır."""
    path = await _yerel_veritabani(tmp_path)
    once = path.read_bytes()
    betik = _betik()
    monkeypatch.setattr(betik, "_gonder", lambda *a, **k: None)
    betik.main(["--db", str(path), "--merkez", "https://ornek.gecersiz"])
    assert path.read_bytes() == once


async def test_uygula_token_olmadan_baslamaz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token ortam değişkeninden gelir (K8); yoksa gönderim HİÇ başlamaz."""
    path = await _yerel_veritabani(tmp_path)
    betik = _betik()
    cagrilar: list[Any] = []
    monkeypatch.setattr(betik, "_gonder", lambda *a, **k: cagrilar.append((a, k)))
    monkeypatch.delenv(betik.TOKEN_ENV, raising=False)

    kod = betik.main(["--db", str(path), "--merkez", "https://ornek.gecersiz", "--uygula"])
    assert kod == 2
    assert cagrilar == []


async def test_uygula_yalniz_yerel_kullanicilari_kimlikleriyle_gonderir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = await _yerel_veritabani(tmp_path)
    betik = _betik()
    gonderilen: list[dict[str, Any]] = []

    def sahte(merkez: str, token: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        gonderilen.append(payload)
        return {"revision": 2, "added": len(payload["users"]), "skipped": 0, "skips": []}

    monkeypatch.setattr(betik, "_gonder", sahte)
    monkeypatch.setenv(betik.TOKEN_ENV, "test-yonetim-tokeni")

    kod = betik.main(["--db", str(path), "--merkez", "https://ornek.gecersiz", "--uygula"])
    assert kod == 0

    kullanicilar = gonderilen[0]["users"]
    assert [k["firstName"] for k in kullanicilar] == ["Merve", "Zahide"]
    for kullanici in kullanicilar:
        # PIN'LER KORUNUR: hash ve arama anahtarı olduğu gibi gider, düz PIN asla.
        assert kullanici["passwordHash"].startswith("$argon2id$")
        assert len(kullanici["secretLookup"]) == 64
        assert kullanici["id"]
        assert "password" not in kullanici


async def test_bootstrap_dahil_bayragi_yoneticiyi_de_gonderir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Karar geri alınabilir olmalı: atlama varsayılandır, yasak değil."""
    path = await _yerel_veritabani(tmp_path)
    betik = _betik()
    gonderilen: list[dict[str, Any]] = []
    monkeypatch.setattr(betik, "_gonder",
                        lambda merkez, token, payload, **k: gonderilen.append(payload)
                        or {"revision": 2, "added": 3, "skipped": 0, "skips": []})
    monkeypatch.setenv(betik.TOKEN_ENV, "test-yonetim-tokeni")

    betik.main(["--db", str(path), "--merkez", "https://ornek.gecersiz",
                "--uygula", "--bootstrap-dahil"])
    adlar = {k["firstName"] for k in gonderilen[0]["users"]}
    assert adlar == {"Sistem", "Zahide", "Merve"}


def test_merkez_adresi_bayrak_sonra_ortam_degiskeni(monkeypatch: pytest.MonkeyPatch) -> None:
    betik = _betik()
    monkeypatch.setenv(betik.URL_ENV, "https://ortamdan.ornek/")
    assert betik.resolve_merkez("https://bayraktan.ornek/") == "https://bayraktan.ornek"
    assert betik.resolve_merkez(None) == "https://ortamdan.ornek"
