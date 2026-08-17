"""Kurulum paketi: sırların ve geçit ayarlarının dağıtımı (ADR 0025).

Burada sınanan şey, ADR'nin bağlayıcı saydığı davranışlardır:

  · KASA ANAHTARI YOKSA UÇ KAPALIDIR (503) — sessizce düz metin YAZMAZ.
  · İPTAL EDİLEN KURULUM PAKET ALAMAZ.
  · SIRLAR VERİTABANINDA DÜZ DURMAZ — dosyayı okuyan değeri göremez.
  · MAKİNEYE ÖZEL ANAHTAR DAĞITILAMAZ (`identity_sync.*`, `core.pin_pepper`) —
    ne merkez kabul eder, ne kurulum yazar.
  · HER DAĞITIM DENETİM İZİNE DÜŞER: hangi kurulum, ne zaman. Değer yazılmaz.
  · `revision` değişmemişse SIR HİÇ TAŞINMAZ.
  · KURULUM PAKETİ SENKRONDA UYGULANIR: sır kasaya, ayar ayar deposuna.
  · `push-secrets.py` KURU PROVADA TEK BİR AĞ İSTEĞİ YAPMAZ.

Testler gerçek uygulamayı ayağa kaldırır; ağa çıkılmaz, veritabanı geçici
dizindedir.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from services.identity.app.main import create_app
from services.identity.app.settings import Settings

from km_core.config.loader import ROOT, Config
from km_core.config.settings_store import SettingsStore, apply_settings_migrations
from km_core.security.migrations import apply_core_migrations
from km_core.store.db import Store
from km_platform.identity_sync.client import IdentityResponseError
from km_platform.identity_sync.errors import IdentitySyncError
from km_platform.identity_sync.service import (
    PROVISIONING_REVISION_KEY,
    TOKEN_KEY,
    IdentitySync,
)

YONETICI_PINI = "482913"
ADMIN_TOKEN = "test-yonetim-tokeni"
KASA_ANAHTARI = Fernet.generate_key().decode("ascii")

# Gerçek bir sır gibi görünen ama hiçbir yerde geçerli olmayan değer. Testin
# aradığı şey "bu metin veritabanı dosyasında düz geçiyor mu".
SIR = "kagit-uzerinde-gorunmesi-yasak-deger-8f2a"


def _settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(
        db_path=str(tmp_path / "identity.sqlite"),
        pepper="test-pepper",
        admin_token=extra.pop("admin_token", ADMIN_TOKEN),
        vault_key=extra.pop("vault_key", KASA_ANAHTARI),
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


def kurulum(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def esle(client: TestClient, *, makine: str = "MacBook") -> tuple[str, str]:
    """Kod üretir, eşler; (token, kurulum kimliği) döndürür."""
    kod = client.post("/installations/pair-code", headers=yonetim(), json={"note": makine})
    assert kod.status_code == 200, kod.text
    cevap = client.post("/pair", json={
        "code": kod.json()["code"],
        "publicKey": "ssh-ed25519 AAAA-test",
        "machineName": makine,
        "platform": "Darwin",
        "version": "0.1.0",
    })
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"]), str(cevap.json()["installationId"])


def yukle(client: TestClient, **govde: Any) -> Any:
    return client.put("/provisioning", headers=yonetim(), json=govde)


# ------------------------------------------------------------ kasa anahtarı


def test_kasa_anahtari_yoksa_uc_KAPALI(tmp_path: Path) -> None:
    """ANAHTAR YOKSA SESSİZCE DÜZ METİN YAZILMAZ.

    "Şifreleme kurulmamış, o hâlde açık yaz" davranışı bir gün bütün sunucu
    parolalarını okunur bırakırdı. Kapalı olduğunu SÖYLEYEN bir kapı (503),
    sessizce açık bir kapıdan iyidir.
    """
    app = create_app(_settings(tmp_path, vault_key=""))
    with TestClient(app, raise_server_exceptions=False) as client:
        token, _ = esle(client)

        cekme = client.get("/provisioning", headers=kurulum(token))
        assert cekme.status_code == 503, cekme.text
        assert "KM_IDENTITY_VAULT_KEY" in cekme.json()["error"]["message"]

        yazma = yukle(client, secrets={"server.store.app_key": SIR})
        assert yazma.status_code == 503, yazma.text

        # Ve gerçekten HİÇBİR ŞEY yazılmamış olmalı: 503 dönüp arka planda
        # düz metin bırakan bir uç, en kötü ihtimaldir.
        satirlar = client.get("/provisioning/summary", headers=yonetim())
        assert satirlar.status_code == 200, satirlar.text
        assert satirlar.json()["items"] == []


def test_bozuk_kasa_anahtari_da_KAPALI(tmp_path: Path) -> None:
    """Fernet anahtarı olmayan bir metin 500 değil 503 üretir.

    500 "kod patladı" der ve kimse ne yapacağını bilmez; 503 "bu uç şu an
    kapalı" der ve nedeni cümlenin içindedir.
    """
    app = create_app(_settings(tmp_path, vault_key="bu-bir-fernet-anahtari-degil"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token, _ = esle(client)
        cevap = client.get("/provisioning", headers=kurulum(token))
        assert cevap.status_code == 503, cevap.text


# --------------------------------------------------------------- dağıtım


def test_paket_eslenmis_kuruluma_gider(client: TestClient) -> None:
    token, _ = esle(client)
    assert yukle(
        client,
        secrets={"server.store.app_key": SIR},
        settings={"modules.bld_api.base_url": "https://ornek.test", "modules.store_api.read_only": False},
    ).status_code == 200

    cevap = client.get("/provisioning", headers=kurulum(token))
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert govde["changed"] is True
    assert govde["secrets"] == {"server.store.app_key": SIR}
    # AYAR TİPİ KORUNUR: `False` metne çevrilip `"False"` olarak dönmez.
    assert govde["settings"]["modules.store_api.read_only"] is False
    assert govde["settings"]["modules.bld_api.base_url"] == "https://ornek.test"


def test_eslenmemis_makine_paket_alamaz(client: TestClient) -> None:
    esle(client)  # merkezde paket olsun
    yukle(client, secrets={"server.store.app_key": SIR})
    assert client.get("/provisioning").status_code == 401
    assert client.get("/provisioning", headers=kurulum("uydurma-token")).status_code == 401


def test_IPTAL_EDILEN_KURULUM_PAKET_ALAMAZ(client: TestClient) -> None:
    """ADR 0025'in en sert cümlesi.

    İptal, kadroyu kesmekle bitmez: iptal edilmiş bir makinenin sunucu
    parolalarını çekmeye devam etmesi, iptalin hiçbir anlamı kalmaması demekti.
    Kapı `require_installation`dır ve o yalnız `status = 'active'` satırları
    eşleştirir — bu test o davranışın kurulum paketinde de geçerli olduğunu
    doğrular.
    """
    token, installation_id = esle(client)
    yukle(client, secrets={"server.store.app_key": SIR})

    # İptalden ÖNCE çalışıyor.
    assert client.get("/provisioning", headers=kurulum(token)).status_code == 200

    iptal = client.post(f"/installations/{installation_id}/revoke", headers=yonetim())
    assert iptal.status_code == 200, iptal.text

    cevap = client.get("/provisioning", headers=kurulum(token))
    assert cevap.status_code == 401, cevap.text
    # Yanıt "iptal edildin" DEMEZ: geçerli bir token'ın varlığını doğrulamak olurdu.
    assert SIR not in cevap.text


def test_revizyon_degismemisse_SIR_HIC_TASINMAZ(client: TestClient) -> None:
    token, _ = esle(client)
    yukle(client, secrets={"server.store.app_key": SIR})

    ilk = client.get("/provisioning", headers=kurulum(token)).json()
    revizyon = ilk["revision"]

    ikinci = client.get("/provisioning", headers=kurulum(token),
                        params={"known_revision": revizyon})
    assert ikinci.status_code == 200
    assert ikinci.json() == {"revision": revizyon, "changed": False}
    assert SIR not in ikinci.text


def test_degismeyen_deger_revizyonu_ARTIRMAZ(client: TestClient) -> None:
    """Fernet her şifrelemede farklı çıktı üretir; satırı körlemesine yazmak
    her koşuda revizyonu artırır ve sahadaki her kurulum bütün sırları yeniden
    çekerdi."""
    esle(client)
    ilk = yukle(client, secrets={"server.store.app_key": SIR}).json()
    ikinci = yukle(client, secrets={"server.store.app_key": SIR}).json()

    assert ilk["written"] == 1
    assert ikinci["written"] == 0
    assert ikinci["revision"] == ilk["revision"]

    ucuncu = yukle(client, secrets={"server.store.app_key": SIR + "-yeni"}).json()
    assert ucuncu["written"] == 1
    assert ucuncu["revision"] == ilk["revision"] + 1


# -------------------------------------------------------------- şifreleme


def test_sir_veritabaninda_DUZ_DURMAZ(tmp_path: Path) -> None:
    """Dosya sızarsa sırlar açılmasın: `/data/identity.sqlite` düz metin taşımaz.

    Test dosyanın BAYTLARINA bakar. Bir gün şifreleme yanlışlıkla kaldırılırsa
    ya da yeni bir alan şifrelenmeden eklenirse, uçlar hâlâ doğru cevap
    verdiği için hiçbir davranış testi bunu yakalayamazdı.
    """
    db_path = tmp_path / "identity.sqlite"
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        esle(client)
        assert yukle(
            client,
            secrets={"server.store.app_key": SIR},
            settings={"modules.bld_api.base_url": "https://gizli-olmayan-ama-sifreli.test"},
        ).status_code == 200

    ham = db_path.read_bytes()
    assert SIR.encode("utf-8") not in ham, "sır düz metin olarak diskte duruyor"
    # Ayar da şifrelenir: tek yol vardır ve o yol şifreler. Yanlış etiketlenen
    # bir sırrın düz metne düşmesi böyle imkânsızlaşır.
    assert b"https://gizli-olmayan-ama-sifreli.test" not in ham
    # Anahtar ADI ise düz durur — özet ekranı kasa anahtarı olmadan da okunmalı.
    assert b"server.store.app_key" in ham


def test_ozet_ANAHTAR_ADI_verir_DEGER_vermez(client: TestClient) -> None:
    esle(client)
    yukle(client, secrets={"server.store.app_key": SIR})

    cevap = client.get("/provisioning/summary", headers=yonetim())
    assert cevap.status_code == 200, cevap.text
    anahtarlar = [item["key"] for item in cevap.json()["items"]]
    assert anahtarlar == ["server.store.app_key"]
    assert SIR not in cevap.text


# --------------------------------------------------- makineye özel anahtarlar


@pytest.mark.parametrize("key", [
    "identity_sync.installation_token",
    "identity_sync.private_key",
    "identity_sync.admin_token",
    "core.pin_pepper",
])
def test_makineye_ozel_anahtar_MERKEZE_YAZILAMAZ(client: TestClient, key: str) -> None:
    """Betikteki yasak tek kapı değildir.

    `scripts/push-secrets.py` bu anahtarları göndermiyor; ama betiği atlayıp
    doğrudan uca yazan bir istek aynı zararı verirdi — bütün kurulumlar tek
    kurulum token'ını paylaşır ya da herkesin PIN'i bir anda kırılırdı.
    """
    esle(client)
    cevap = yukle(client, secrets={key: SIR})
    assert cevap.status_code == 400, cevap.text
    assert client.get("/provisioning/summary", headers=yonetim()).json()["items"] == []


def test_yasakli_anahtar_TEK_BASINA_TUM_ISTEGI_dusurur(client: TestClient) -> None:
    """Yarısı yazılmış bir paket, hiç yazılmamıştan kötüdür: gönderen taraf
    "gitti" sanır ve eksik olanı bir daha hiç aramaz."""
    esle(client)
    cevap = yukle(client, secrets={"server.store.app_key": SIR, "core.pin_pepper": SIR})
    assert cevap.status_code == 400, cevap.text
    assert client.get("/provisioning/summary", headers=yonetim()).json()["items"] == []


def test_bos_govde_reddedilir(client: TestClient) -> None:
    esle(client)
    assert yukle(client, secrets={}, settings={}).status_code == 400


# ---------------------------------------------------------------- denetim


def _iz(db_path: Path, action: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
        )]
    finally:
        connection.close()


def test_her_dagitim_DENETIM_IZINE_duser(tmp_path: Path) -> None:
    """ADR 0025 §5 — kim, ne zaman, hangi kurulum. DEĞER YAZILMAZ.

    "Değişmedi" yanıtı dağıtım değildir ve iz bırakmaz: bıraksaydı her senkron
    turu izi doldurur ve gerçek dağıtımlar arasında kaybolurdu.
    """
    db_path = tmp_path / "identity.sqlite"
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        token, installation_id = esle(client)
        yukle(client, secrets={"server.store.app_key": SIR})

        cekme = client.get("/provisioning", headers=kurulum(token))
        revizyon = cekme.json()["revision"]
        # Değişmemiş tur: iz bırakmamalı.
        client.get("/provisioning", headers=kurulum(token),
                   params={"known_revision": revizyon})

    satirlar = _iz(db_path, "provisioning.pull")
    assert len(satirlar) == 1, "yalnız gerçek dağıtım ize düşmeli"
    assert satirlar[0]["installation_id"] == installation_id
    assert satirlar[0]["result"] == "ok"
    assert SIR not in str(satirlar[0]["detail"])


def test_yukleme_de_ize_duser_ve_ANAHTAR_ADI_tasir(tmp_path: Path) -> None:
    db_path = tmp_path / "identity.sqlite"
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        esle(client)
        yukle(client, secrets={"server.store.app_key": SIR})
        yukle(client, secrets={"core.pin_pepper": SIR})  # reddedilecek

    satirlar = _iz(db_path, "provisioning.push")
    assert [row["result"] for row in satirlar] == ["ok", "denied"]
    assert "server.store.app_key" in str(satirlar[0]["detail"])
    assert SIR not in str(satirlar[0]["detail"])
    # Acil yoldan (yönetim token'ı) gelindiğinde arkada KİŞİ yoktur; iz bunu
    # boş `user_id` ile söyler ve uydurmaz.
    assert satirlar[0]["user_id"] is None


# ======================================================== KURULUM TARAFI ====


class SahteKasa:
    """`Vault`ın yeteneğe bakan yüzü. Sır bellekte durur, diske yazılmaz."""

    AUTO_FLAG = "core.pin_pepper_auto"

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class SahteIstemci:
    """Yalnız bu testin ihtiyaç duyduğu iki ucu taklit eder."""

    def __init__(self, paket: dict[str, Any] | None = None,
                 hata: Exception | None = None) -> None:
        self.paket = paket if paket is not None else {
            "revision": 4,
            "changed": True,
            "secrets": {"server.store.app_key": SIR},
            "settings": {"modules.bld_api.base_url": "https://ornek.test",
                         "modules.store_api.read_only": False},
        }
        self.hata = hata
        self.calls: list[tuple[str, Any]] = []

    async def provisioning(self, token: str, *,
                           known_revision: int | None = None) -> dict[str, Any]:
        self.calls.append(("provisioning", known_revision))
        if self.hata is not None:
            raise self.hata
        if known_revision is not None and known_revision == self.paket.get("revision"):
            return {"revision": known_revision, "changed": False}
        return dict(self.paket)


def yapilandir(tmp_path: Path) -> Config:
    return Config({"platform": {"identity_sync": {
        "enabled": True,
        "base_url": "https://kontrolmerkezi.example",
        "cache_path": str(tmp_path / "identity-roster.json"),
    }}}, root=tmp_path)


@pytest.fixture
async def depo(tmp_path: Path) -> AsyncIterator[Store]:
    store = Store(tmp_path / "kurulum.sqlite")
    await store.open()
    await apply_core_migrations(store)
    await apply_settings_migrations(store)
    yield store
    await store.close()


def kur(tmp_path: Path, depo: Store | None = None,
        istemci: SahteIstemci | None = None) -> tuple[IdentitySync, SahteKasa, SahteIstemci]:
    kasa = SahteKasa()
    istemci = istemci or SahteIstemci()
    sync = IdentitySync(
        kasa,  # type: ignore[arg-type]
        yapilandir(tmp_path),
        client=istemci,  # type: ignore[arg-type]
        store=depo,
    )
    return sync, kasa, istemci


async def test_GERCEK_ISTEMCI_GERCEK_MERKEZLE_konusur(tmp_path: Path, depo: Store) -> None:
    """UÇTAN UCA: sahte yok, iki taraf da gerçek.

    NEDEN AYRI BİR TEST. Yukarıdaki testler iki tarafı AYRI AYRI ölçüyor —
    merkez `TestClient` ile, kurulum sahte istemciyle. İkisi de geçerken alan
    adlarının (`secrets`, `settings`, `changed`, `revision`) ve sorgu
    parametresinin (`known_revision`) ayrışması mümkündür ve o ayrışma sahada
    tam olarak şöyle görünür: her şey yolunda görünür, hiçbir sır yazılmaz.
    17.08.2026'daki pepper arızası da böyle bir sessiz uyumsuzluktu.

    `IdentityClient` ağa çıkmaz: `httpx` ASGI taşıyıcısıyla merkezin
    uygulamasına doğrudan bağlanır.
    """
    import httpx

    from km_platform.identity_sync import client as client_module
    from km_platform.identity_sync.client import IdentityClient

    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as merkez:
        token, _ = esle(merkez)
        assert yukle(
            merkez,
            secrets={"server.store.app_key": SIR},
            settings={"modules.store_api.read_only": False},
        ).status_code == 200

        class _AsgiHttpx:
            """`httpx` modülünün taklidi — yalnız `AsyncClient` yerini tutar."""

            @staticmethod
            def AsyncClient(**kwargs: Any) -> Any:
                kwargs.pop("timeout", None)
                return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

        gercek_istemci = IdentityClient("http://merkez.test")
        eski = client_module._httpx
        client_module._httpx = lambda: _AsgiHttpx  # type: ignore[assignment]
        try:
            kasa = SahteKasa()
            sync = IdentitySync(
                kasa,  # type: ignore[arg-type]
                yapilandir(tmp_path),
                client=gercek_istemci,
                store=depo,
            )
            await kasa.set(TOKEN_KEY, token)

            sonuc = await sync.fetch_provisioning()
            assert sonuc["applied"] is True, sonuc
            assert kasa.data["server.store.app_key"] == SIR
            assert (await SettingsStore(depo).values())["modules.store_api.read_only"] is False

            # İkinci tur: revizyon aynı, sır ağa HİÇ ÇIKMAZ.
            ikinci = await sync.fetch_provisioning()
            assert ikinci["changed"] is False
        finally:
            client_module._httpx = eski  # type: ignore[assignment]


async def test_kurulum_paketi_KASAYA_ve_AYAR_DEPOSUNA_yazilir(
    tmp_path: Path, depo: Store
) -> None:
    """Sır kasaya, ayar ayar deposuna. İkisi ayrı yere gider çünkü ayrı
    şeylerdir: kasa şifreler ve değeri ekranda göstermez (K8)."""
    sync, kasa, _ = kur(tmp_path, depo)
    await kasa.set(TOKEN_KEY, "kurulum-tokeni")

    sonuc = await sync.fetch_provisioning()

    assert sonuc["applied"] is True
    assert sonuc["secrets"] == 1
    assert sonuc["settings"] == 2
    assert kasa.data["server.store.app_key"] == SIR
    assert await sync.provisioning_revision() == 4

    ayarlar = await SettingsStore(depo).values()
    assert ayarlar["modules.bld_api.base_url"] == "https://ornek.test"
    # TİP KORUNUR: `False` ayar deposunda `"False"` metnine dönmez.
    assert ayarlar["modules.store_api.read_only"] is False

    # SIR AYAR DEPOSUNA SIZMAZ (K8).
    assert "server.store.app_key" not in ayarlar


async def test_eslenmemis_kurulum_paket_istemez(tmp_path: Path, depo: Store) -> None:
    sync, _, istemci = kur(tmp_path, depo)
    sonuc = await sync.fetch_provisioning()
    assert sonuc == {"applied": False, "reason": "eşlenmemiş"}
    assert istemci.calls == [], "eşlenmemişken ağa çıkılmamalı"


async def test_revizyon_ayniysa_kasaya_dokunulmaz(tmp_path: Path, depo: Store) -> None:
    sync, kasa, _ = kur(tmp_path, depo)
    await kasa.set(TOKEN_KEY, "kurulum-tokeni")
    await sync.fetch_provisioning()

    kasa.data["server.store.app_key"] = "elle-degistirilmis"
    sonuc = await sync.fetch_provisioning()

    assert sonuc["changed"] is False
    assert kasa.data["server.store.app_key"] == "elle-degistirilmis", (
        "değişmemiş paket kasayı yeniden yazmamalı"
    )


async def test_MERKEZ_YASAKLI_ANAHTAR_gonderse_bile_YAZILMAZ(
    tmp_path: Path, depo: Store
) -> None:
    """İKİNCİ KAPI. Merkez tarafı bunları zaten reddediyor; yanlış
    yapılandırılmış ya da ele geçirilmiş bir merkez yine de gönderirse
    kurulum kendi kimliğini teslim etmez."""
    istemci = SahteIstemci({
        "revision": 9, "changed": True,
        "secrets": {
            "identity_sync.installation_token": "baskasinin-tokeni",
            "core.pin_pepper": "herkesin-girisini-kiracak-anahtar",
            "server.store.app_key": SIR,
        },
        "settings": {},
    })
    sync, kasa, _ = kur(tmp_path, depo, istemci)
    await kasa.set(TOKEN_KEY, "kurulum-tokeni")
    await kasa.set("core.pin_pepper", "bu-makinenin-anahtari")

    sonuc = await sync.fetch_provisioning()

    assert sonuc["secrets"] == 1, "yalnız iş sırrı yazılmalı"
    assert kasa.data[TOKEN_KEY] == "kurulum-tokeni"
    assert kasa.data["core.pin_pepper"] == "bu-makinenin-anahtari"
    assert kasa.data["server.store.app_key"] == SIR


async def test_merkez_eski_surumse_SESSIZ_gecilir(tmp_path: Path, depo: Store) -> None:
    """0025 öncesi bir merkezde uç yoktur (404). Bu bir arıza değildir:
    kurulum bugünkü gibi kendi kasasıyla çalışır (K7)."""
    sync, kasa, _ = kur(tmp_path, depo, SahteIstemci(hata=IdentityResponseError(404, "yok")))
    await kasa.set(TOKEN_KEY, "kurulum-tokeni")

    sonuc = await sync.fetch_provisioning()
    assert sonuc["applied"] is False
    assert "kurulum paketi ucu yok" in sonuc["reason"]


async def test_ag_yokken_HATA_YUKSELMEZ(tmp_path: Path, depo: Store) -> None:
    sync, kasa, _ = kur(tmp_path, depo, SahteIstemci(hata=IdentitySyncError("ağ yok")))
    await kasa.set(TOKEN_KEY, "kurulum-tokeni")
    sonuc = await sync.fetch_provisioning()
    assert sonuc["applied"] is False


async def test_ayar_deposu_baglanmamissa_SIR_YINE_yazilir(tmp_path: Path) -> None:
    """Depo `attach_store` ile sonradan bağlanıyor. Bağlanmamışken patlamak,
    eşleme akışının tamamını düşürürdü (K7)."""
    sync, kasa, _ = kur(tmp_path, depo=None)
    await kasa.set(TOKEN_KEY, "kurulum-tokeni")

    sonuc = await sync.fetch_provisioning()

    assert sonuc["applied"] is True
    assert sonuc["secrets"] == 1
    assert sonuc["settings"] == 0
    assert kasa.data["server.store.app_key"] == SIR


async def test_eslemeyi_cozmek_REVIZYONU_dusurur_SIRLARI_dusurmez(
    tmp_path: Path, depo: Store
) -> None:
    """Dağıtılmış sırları silmek, yeniden eşlemek isteyen yöneticinin elindeki
    makineyi çalışmaz hâle getirirdi. Revizyon işaretinin düşmesi yeter."""
    sync, kasa, _ = kur(tmp_path, depo)
    await kasa.set(TOKEN_KEY, "kurulum-tokeni")
    await sync.fetch_provisioning()

    await sync.reset_pairing()

    assert PROVISIONING_REVISION_KEY not in kasa.data
    assert kasa.data["server.store.app_key"] == SIR


# ============================================================ BETİK =========


def _betik() -> Any:
    """`scripts/push-secrets.py` — adında tire olduğu için yoldan yüklenir
    (`test_kadro_ice_aktarma.py` ile aynı desen)."""
    source = ROOT / "scripts" / "push-secrets.py"
    spec = importlib.util.spec_from_file_location("push_secrets", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["push_secrets"] = module
    spec.loader.exec_module(module)
    return module


def _yalitilmis_betik(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Betiği DEPONUN GERÇEK `config/local.yaml` DOSYASINDAN KOPARIR.

    Yalıtım şart: betik ayarı `load_config()` ile okuyor ve bu makinede o
    dosyada gerçek sunucu sırları var. Yalıtmayan bir test onları okur, parmak
    izlerini terminale basar ve makineden makineye farklı sonuç verir.
    """
    betik = _betik()
    kasa_anahtari = tmp_path / "secret.key"
    kasa_anahtari.write_bytes(Fernet.generate_key())

    def sahte_config(env: str | None = None) -> Config:
        return Config(
            {"core": {"secret_key_path": str(kasa_anahtari)},
             "platform": {"identity_sync": {"base_url": "https://kontrolmerkezi.example"}}},
            root=tmp_path,
        )

    monkeypatch.setattr(betik, "load_config", sahte_config)
    monkeypatch.setattr(betik, "_gonder", _patlat)
    return betik, kasa_anahtari


def test_KURU_PROVA_HICBIR_SEY_GONDERMEZ(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    """Varsayılan kip kuru provadır ve TEK BİR AĞ İSTEĞİ YAPMAZ.

    Gönderim fonksiyonu patlayacak biçimde değiştirilir: çağrılırsa test düşer.
    Token ve adres BİLEREK tanımlıdır — "gönderemedi çünkü token yoktu"
    sonucuyla "göndermedi çünkü kuru provaydı" karışmasın.
    """
    betik, _ = _yalitilmis_betik(tmp_path, monkeypatch)
    monkeypatch.setenv("KM_IDENTITY_ADMIN_TOKEN", "sahte-token")
    monkeypatch.setenv("KM_IDENTITY_URL", "https://kontrolmerkezi.example")

    kod = betik.main(["--db", str(_bos_veritabani(tmp_path))])

    cikti = capsys.readouterr().out
    assert "KURU PROVA" in cikti
    assert "hiçbir şey gönderilmez" in cikti
    # Anahtar bulunamadı: çıkış kodu insan kararı beklendiğini söyler.
    assert kod == 1


def test_yasakli_anahtar_betikte_de_GONDERILMEZ() -> None:
    betik = _betik()
    for key in ("identity_sync.private_key", "core.pin_pepper", "core.pin_pepper_previous"):
        assert betik.yasakli(key) is True
    assert betik.yasakli("server.store.app_key") is False
    # Açık liste yasaklıların hiçbirini içermemeli.
    assert not [k for k in betik.SECRET_KEYS if betik.yasakli(k)]


def test_kuru_prova_DEGER_YAZMAZ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    """Terminale düşen bir sır, oradan kayıtlara ve ekran görüntülerine düşer."""
    betik, kasa_anahtari = _yalitilmis_betik(tmp_path, monkeypatch)
    sifreli = Fernet(kasa_anahtari.read_bytes()).encrypt(SIR.encode("utf-8")).decode("ascii")
    db_path = _bos_veritabani(tmp_path, sir=sifreli)

    kod = betik.main(["--db", str(db_path)])

    cikti = capsys.readouterr().out
    # Sır GERÇEKTEN okunmuş olmalı, yoksa test hiçbir şey kanıtlamaz.
    assert "server.store.app_key" in cikti
    assert SIR not in cikti
    assert kod == 1  # kalan anahtarlar eksik


def _patlat(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("kuru provada ağa çıkıldı")


def _bos_veritabani(tmp_path: Path, *, sir: str | None = None) -> Path:
    """Betiğin okuyabileceği en küçük veritabanı: `secrets` + `settings`."""
    db_path = tmp_path / "kontrol-merkezi.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        "CREATE TABLE secrets (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);"
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, "
        "updated_by TEXT, updated_at TEXT);"
    )
    if sir is not None:
        connection.execute(
            "INSERT INTO secrets (key, value, updated_at) VALUES (?, ?, '')",
            ("server.store.app_key", sir),
        )
    connection.commit()
    connection.close()
    return db_path
