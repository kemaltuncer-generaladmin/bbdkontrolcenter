"""Sistem Ayarları API'si — ADR 0017 (çekirdek ekran) ve ADR 0018 (sözleşme).

BURADA SINANAN ŞEY EKRANIN GÖRÜNTÜSÜ DEĞİL, KAPILAR VE KATMANLAR:

* **K9 — çift kapı.** İzni olmayan oturum uçlara ULAŞAMAZ. Modül sekmesi
  ayrıca kendi `requires` iznini sorar: `settings.manage` sahibi olmak bir
  modülün ayarını değiştirme yetkisi vermez (ADR 0018 §3).
* **Katman önceliği.** dosya → çekirdek deposu → ortam değişkeni. Depo
  `local.yaml`'ı ezer ama ekran ezilen değeri de bildirir; ortam değişkeni
  hiçbir şeyin ezemeyeceği en üst katmandır ve oraya yazma denemesi reddedilir.
* **Yalnız ilan edilmiş alan.** İstek gövdesi ayar YOLU taşımaz, alan adı
  taşır; katalogda olmayan bir ad reddedilir. Aksi hâlde uca doğrudan istek
  atan biri istediği ayar yoluna yazabilirdi.
* **Sahte düğme yok.** Güncelleme ucu olmadığını ekranın uydurması değil,
  sunucunun söylemesi gerekir.

Testler gerçek uygulamayı ayağa kaldırır ve gerçek oturum belirteciyle konuşur;
taklit bir izin nesnesi kullanılmaz. Depo, kasa anahtarı, günlük ve çıktı
klasörü geçici dizine alınır — test kullanıcının masaüstüne dosya yazmaz.
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.contracts.manifest import Manifest
from km_core.http.app import create_app
from km_core.kernel.kernel import ModuleRecord

YONETICI_PINI = "482913"
PERSONEL_PINI = "735204"

#: Yetkisiz kullanıcı için ayar sekmesi taşıyan sahte bir modül kaydı. Gerçek
#: bir modüle bağlanmıyoruz: bu testin konusu KAPI, modülün kendisi değil.
#: Ayrıca gerçek bir modül adı yazmak, çekirdek testine modül adı sokardı (K1).
YETKISIZ_MODUL = "deneme_kapali"
YETKILI_MODUL = "deneme_acik"


def _blok(requires: list[str]) -> dict[str, Any]:
    return {
        "tab": f"Deneme ({requires[0]})",
        "requires": requires,
        "groups": [
            {
                "id": "tarama",
                "title": "Tarama",
                "fields": [
                    {"key": "schedule", "type": "cron", "title": "Zaman",
                     "default": "0 3 * * *"},
                    {"key": "sure", "type": "int", "title": "Süre", "min": 1, "max": 10},
                ],
            }
        ],
    }


def _sahte_kayit(module_id: str, requires: list[str], path: Path) -> ModuleRecord:
    manifest = Manifest(
        id=module_id,
        name=module_id,
        version="0.1.0",
        sdk=">=0.1,<1.0",
        entrypoint="backend.module:register",
        path=path,
    )
    record = ModuleRecord(manifest)
    record.state = "loaded"
    record.settings = _blok(requires)
    return record


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
    # Destek paketi ve test sayfası buraya yazılır; masaüstüne dokunulmaz.
    data["files"] = {"output_path": str(tmp_path / "cikti")}

    app = create_app(Config(data, root=ROOT))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        kernel = test_client.app.state.kernel
        kernel.records[YETKISIZ_MODUL] = _sahte_kayit(
            YETKISIZ_MODUL, [f"{YETKISIZ_MODUL}.manage"], tmp_path / YETKISIZ_MODUL,
        )
        # `users.view` yöneticide var ve ayar izinlerinden AYRI bir anahtar:
        # sekmenin kendi izninin gerçekten sorulduğunu böyle görürüz.
        kernel.records[YETKILI_MODUL] = _sahte_kayit(
            YETKILI_MODUL, ["users.view"], tmp_path / YETKILI_MODUL,
        )
        yield test_client


def basliklar(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def yonetici_token(client: TestClient) -> str:
    cevap = client.post("/api/auth/login", json={"password": YONETICI_PINI})
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def personel_token(client: TestClient, token: str) -> str:
    """Ayar izni OLMAYAN bir oturum."""
    cevap = client.post(
        "/api/users",
        headers=basliklar(token),
        json={
            "firstName": "Veli", "lastName": "Demir", "orgScope": "org",
            "roles": ["org_staff"], "password": PERSONEL_PINI,
        },
    )
    assert cevap.status_code == 201, cevap.text
    giris = client.post("/api/auth/login", json={"password": PERSONEL_PINI})
    assert giris.status_code == 200, giris.text
    return str(giris.json()["token"])


def sekme(govde: dict[str, Any], tab_id: str) -> dict[str, Any] | None:
    return next((tab for tab in govde["tabs"] if tab["id"] == tab_id), None)


def alan(tab: dict[str, Any], key: str) -> dict[str, Any]:
    for group in tab["groups"]:
        for field in group["fields"]:
            if field["key"] == key:
                return dict(field)
    raise AssertionError(f"{key} alanı sekmede yok")


# ------------------------------------------------------------------- kapılar


def test_oturumsuz_istek_reddedilir(client: TestClient) -> None:
    assert client.get("/api/settings").status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/settings"),
        ("GET", "/api/settings/printer"),
        ("GET", "/api/settings/update"),
        ("GET", "/api/settings/diagnostics"),
        ("POST", "/api/settings/support-bundle"),
    ],
)
def test_izinsiz_kullanici_uclara_ulasamaz(
    client: TestClient, method: str, path: str,
) -> None:
    """K9: ekranda gizlemek yetkilendirme değildir; kapı backend'dedir."""
    token = personel_token(client, yonetici_token(client))
    cevap = client.request(method, path, headers=basliklar(token))
    assert cevap.status_code == 403, cevap.text


def test_izinsiz_kullanici_test_sayfasi_bastiramaz(client: TestClient) -> None:
    """Kapı gövdeden ÖNCE kapanır: reddedilen istek yazıcıya hiç ulaşmaz.

    Bu uç bilerek yalnız RET yönünden sınanır; başarı yolu gerçek kâğıt
    harcardı ve testler dış dünyaya çıkmaz.
    """
    token = personel_token(client, yonetici_token(client))
    cevap = client.post("/api/settings/printer/test-page", headers=basliklar(token))
    assert cevap.status_code == 403, cevap.text


def test_izinsiz_kullanici_yazamaz(client: TestClient) -> None:
    token = personel_token(client, yonetici_token(client))
    cevap = client.put(
        "/api/settings",
        headers=basliklar(token),
        json={"tab": "core.general", "values": {"app.name": "Sızıntı"}},
    )
    assert cevap.status_code == 403, cevap.text


# ------------------------------------------------------------------ katalog


def test_cekirdek_sekmeleri_her_zaman_vardir(client: TestClient) -> None:
    """Dört sabit sekme çekirdeğindir; modülden gelmez (ADR 0018 §2)."""
    govde = client.get("/api/settings", headers=basliklar(yonetici_token(client))).json()
    kimlikler = [tab["id"] for tab in govde["tabs"]]

    for beklenen in ("core.general", "core.printer", "core.update", "core.diagnostics"):
        assert beklenen in kimlikler
    assert govde["canManage"] is True


def test_modul_sekmesi_kendi_iznini_sorar(client: TestClient) -> None:
    """`settings.manage` yeter değildir: sekmenin ilan ettiği izin de gerekir."""
    govde = client.get("/api/settings", headers=basliklar(yonetici_token(client))).json()

    assert sekme(govde, YETKILI_MODUL) is not None, "izni olan sekme gizlenmiş"
    assert sekme(govde, YETKISIZ_MODUL) is None, "izni olmayan sekme listelendi"


def test_izni_olmayan_modul_sekmesine_yazilamaz(client: TestClient) -> None:
    cevap = client.put(
        "/api/settings",
        headers=basliklar(yonetici_token(client)),
        json={"tab": YETKISIZ_MODUL, "values": {"sure": 5}},
    )
    assert cevap.status_code == 403, cevap.text


def test_modul_kaydi_yoksa_sekmesi_de_yoktur(client: TestClient) -> None:
    """Modül silinince sekmesi de gider — çekirdekte tek satır değişmeden."""
    token = yonetici_token(client)
    del client.app.state.kernel.records[YETKILI_MODUL]

    govde = client.get("/api/settings", headers=basliklar(token)).json()
    assert sekme(govde, YETKILI_MODUL) is None


# ------------------------------------------------------------------ katmanlar


def test_dosyadan_gelen_deger_kaynagini_bildirir(client: TestClient) -> None:
    govde = client.get("/api/settings", headers=basliklar(yonetici_token(client))).json()
    kayit = alan(sekme(govde, "core.general") or {}, "app.name")

    assert kayit["source"] == "file"
    assert kayit["hasStore"] is False
    assert kayit["editable"] is True


def test_depo_dosyayi_ezer_ama_dosyadaki_deger_gorunur_kalir(client: TestClient) -> None:
    """ADR 0018 §4: ekran “bu değer dosyadan geliyor, arayüzden ezildi” diyebilmeli."""
    token = yonetici_token(client)
    onceki = alan(
        sekme(client.get("/api/settings", headers=basliklar(token)).json(), "core.general") or {},
        "app.name",
    )["value"]

    cevap = client.put(
        "/api/settings",
        headers=basliklar(token),
        json={"tab": "core.general", "values": {"app.name": "Deneme Kurulumu"}},
    )
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["restartRequired"] is True

    kayit = alan(
        sekme(client.get("/api/settings", headers=basliklar(token)).json(), "core.general") or {},
        "app.name",
    )
    assert kayit["value"] == "Deneme Kurulumu"
    assert kayit["source"] == "store"
    assert kayit["hasFile"] is True
    assert kayit["layers"]["file"] == onceki, "ezilen dosya değeri kayboldu"


def test_bos_deger_ezmeyi_kaldirir(client: TestClient) -> None:
    token = yonetici_token(client)
    client.put("/api/settings", headers=basliklar(token),
               json={"tab": "core.general", "values": {"app.name": "Geçici"}})

    cevap = client.put("/api/settings", headers=basliklar(token),
                       json={"tab": "core.general", "values": {"app.name": None}})
    assert cevap.status_code == 200, cevap.text

    kayit = alan(
        sekme(client.get("/api/settings", headers=basliklar(token)).json(), "core.general") or {},
        "app.name",
    )
    assert kayit["source"] == "file"
    assert kayit["hasStore"] is False


def test_ortam_degiskeni_en_ustte_kalir(client: TestClient) -> None:
    """Acil müdahale yolu kapanmaz: ortamdan gelen alan yazılamaz ve nedeni yazılı."""
    token = yonetici_token(client)
    os.environ["KM__app__name"] = "Ortamdan Gelen"
    try:
        kayit = alan(
            sekme(client.get("/api/settings", headers=basliklar(token)).json(),
                  "core.general") or {},
            "app.name",
        )
        assert kayit["source"] == "env"
        assert kayit["value"] == "Ortamdan Gelen"
        assert kayit["editable"] is False
        assert "KM__app__name" in kayit["lockedReason"]

        cevap = client.put("/api/settings", headers=basliklar(token),
                           json={"tab": "core.general", "values": {"app.name": "Boşuna"}})
        assert cevap.status_code == 409, cevap.text
    finally:
        del os.environ["KM__app__name"]


# ----------------------------------------------------------------- doğrulama


def test_ilan_edilmemis_alana_yazilamaz(client: TestClient) -> None:
    """Gövde ayar YOLU değil ALAN ADI taşır; katalogda olmayan ad reddedilir."""
    cevap = client.put(
        "/api/settings",
        headers=basliklar(yonetici_token(client)),
        json={"tab": "core.general", "values": {"auth.bootstrap_pin": "111111"}},
    )
    assert cevap.status_code == 400, cevap.text
    assert "tanımlı olmayan" in cevap.json()["error"]["message"]


def test_bilinmeyen_sekme_reddedilir(client: TestClient) -> None:
    cevap = client.put(
        "/api/settings",
        headers=basliklar(yonetici_token(client)),
        json={"tab": "core.yok", "values": {}},
    )
    assert cevap.status_code == 404, cevap.text


@pytest.mark.parametrize(
    ("tab", "values"),
    [
        ("core.general", {"app.name": 5}),                       # metin değil
        ("core.printer", {"platform.printer.media": "A3"}),      # seçenek dışı
        (YETKILI_MODUL, {"sure": 99}),                           # üst sınırın üstü
        (YETKILI_MODUL, {"sure": "üç"}),                         # sayı değil
        (YETKILI_MODUL, {"schedule": "her gece"}),               # cron değil
    ],
)
def test_gecersiz_deger_reddedilir(
    client: TestClient, tab: str, values: dict[str, Any],
) -> None:
    cevap = client.put(
        "/api/settings",
        headers=basliklar(yonetici_token(client)),
        json={"tab": tab, "values": values},
    )
    assert cevap.status_code == 400, cevap.text


def test_modul_ayari_kendi_ad_alanina_yazilir(client: TestClient) -> None:
    token = yonetici_token(client)
    cevap = client.put("/api/settings", headers=basliklar(token),
                       json={"tab": YETKILI_MODUL, "values": {"sure": 7}})
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["changed"] == [f"modules.{YETKILI_MODUL}.sure"]


# ------------------------------------------------------------- diğer uçlar


def test_guncellemeyi_kabugun_yuruttugunu_soyler(client: TestClient) -> None:
    """Güncelleyici kabukta; çekirdek bunu saklamaz, açıkça söyler.

    `canCheck`/`canInstall` BİLEREK yanlıştır: bu süreçte denetleme ya da
    kurulum ucu yok ve olmayacak — sidecar kendi altındaki dosyaları
    değiştiremez. Ekran düğmeleri bu yüzden buradaki yollara değil, kabuğun
    komutlarına bağlanır.
    """
    govde = client.get("/api/settings/update",
                       headers=basliklar(yonetici_token(client))).json()

    assert govde["canCheck"] is False
    assert govde["canInstall"] is False
    assert govde["checkPath"] is None
    assert govde["installPath"] is None
    assert govde["handledBy"] == "shell"
    assert govde["reason"], "nedeni boş bırakılmaz"
    assert govde["version"]


def test_tanilama_gunlugu_maskeler(client: TestClient, tmp_path: Path) -> None:
    """Ekranda ham, dosyada maskeli olsaydı maskeleme kopyala-yapıştırla delinirdi."""
    (tmp_path / "gunluk.log").write_text(
        "GET https://bbdstore.com.tr/api/bell/status\n"
        "token=49fe6dcc5648c8929fc6706655be965caf2f5638d2b1f3da\n"
        "veli: 0532 123 45 67\n",
        encoding="utf-8",
    )
    govde = client.get("/api/settings/diagnostics",
                       headers=basliklar(yonetici_token(client))).json()
    metin = "\n".join(govde["lines"])

    assert govde["masked"] is True
    assert "bbdstore.com.tr" not in metin
    assert "49fe6dcc5648c8929fc6706655be965caf2f5638d2b1f3da" not in metin
    assert "5321234567" not in metin.replace(" ", "")


def test_destek_paketi_maskelenmeden_uretilmez(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "gunluk.log").write_text(
        "POST https://bbdstore.com.tr/api/bell/sound\n"
        "Authorization: Bearer 1|3EjaOnr1b8ioTubRL6LzxzyJZaHfnYRSkIPPEr6X\n"
        "cep: 05321234567\n",
        encoding="utf-8",
    )
    cevap = client.post("/api/settings/support-bundle",
                        headers=basliklar(yonetici_token(client)))
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert govde["masked"] is True

    paket = Path(govde["path"])
    assert paket.is_file()
    # Kişisel veri taşıyan çıktı 0600 açılır (km_core/files/private.py).
    assert paket.stat().st_mode & 0o077 == 0

    with zipfile.ZipFile(paket) as arsiv:
        assert set(arsiv.namelist()) == {
            "OKUBENI.txt", "ozet.json", "ayarlar.json", "gunluk.log",
        }
        gunluk = arsiv.read("gunluk.log").decode("utf-8")

    assert "bbdstore.com.tr" not in gunluk
    assert "3EjaOnr1b8ioTubRL6LzxzyJZaHfnYRSkIPPEr6X" not in gunluk
    assert "05321234567" not in gunluk


def test_yazici_ucu_hata_firlatmaz(client: TestClient) -> None:
    """Yazıcı yoksa da ekran açılır: durum anlatılır, istisna atılmaz (K7)."""
    cevap = client.get("/api/settings/printer", headers=basliklar(yonetici_token(client)))

    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert "available" in govde
    assert "status" in govde
