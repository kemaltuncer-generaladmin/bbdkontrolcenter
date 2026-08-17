"""Merkezden gelen kadronun yerel kayda yansıtılması (ADR 0021 §2).

Sınanan söz — ADR'nin asıl amacı budur:

  · **MERKEZDE AÇILAN KULLANICI İKİNCİ KURULUMDA GİRİŞ YAPAR.** Önbellek tek
    başına yetmez; kayıt `users`/`roles`/`user_roles` tablolarına düşmeden
    giriş yolu onu göremez.
  · **YERELDE ELLE AÇILMIŞ KULLANICI SİLİNMEZ.** Merkezin kopyası
    (`origin='central'`) ile kurulumun kendi kaydı (`origin='local'`) ayrı
    durur; yansıtma yerel satıra dokunmaz.
  · **MERKEZDEN GELEN PASİFLEŞTİRME UYGULANIR** ve satır silinmez.
  · **ÖNBELLEK ÇOK ESKİYSE YALNIZ ÇEVRİMİÇİ GİRİŞ** kabul edilir.
  · **YETENEK KAPALIYKEN HİÇBİR KAPI ÇALIŞMAZ** — bugünkü davranış birebir.

Ağa çıkılmaz; merkez istemcisi taklit edilir.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security.migrations import apply_core_migrations
from km_core.security.roster_projection import (
    ORIGIN_CENTRAL,
    ORIGIN_LOCAL,
    project_roster,
    projected_revision,
)
from km_core.store.db import Store
from km_platform.identity_sync.errors import IdentitySyncError

YEREL_PIN = "482913"      # kurulumun kendi ilk yöneticisi (bootstrap)
MERKEZ_PIN = "735204"     # merkezde açılan kullanıcı
MERKEZ_ID = "u-merkez-1"

_hasher = PasswordHasher()


class SahteIstemci:
    """Merkezin taklidi. `test_kimlik_senkronu.py` içindekinin bu dosyaya gereken
    kadarı; `tests/core` bir paket olmadığı için ortak fake import edilemiyor."""

    def __init__(self, roster: dict[str, Any], *, online: bool = True) -> None:
        self.roster_payload = roster
        self.online = online
        self.calls: list[str] = []

    def _guard(self) -> None:
        if not self.online:
            raise IdentitySyncError("Kimlik servisine ulaşılamadı: ağ yok")

    async def health(self) -> dict[str, Any]:
        self.calls.append("health")
        self._guard()
        return {"status": "ok"}

    async def pair(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("pair")
        self._guard()
        return {"installationId": "i-1", "token": "kurulum-tokeni"}

    async def roster(self, token: str, *, known_revision: int | None = None) -> dict[str, Any]:
        self.calls.append("roster")
        self._guard()
        if known_revision is not None and known_revision == self.roster_payload["revision"]:
            return {"revision": known_revision, "changed": False}
        return deepcopy(self.roster_payload)

    async def push_audit(self, token: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append("push_audit")
        self._guard()
        return {"accepted": len(entries)}

    async def provisioning(self, token: str, *,
                           known_revision: int | None = None) -> dict[str, Any]:
        """ADR 0025 — `sync()` kadronun ardından kurulum paketini de sorar.

        BOŞ PAKET döner: bu dosya yansıtmayı ölçüyor, paketin içeriği onu
        ilgilendirmiyor. Paketin kendi davranışı `test_kimlik_kurulum_paketi.py`
        içinde sınanır.
        """
        self.calls.append("provisioning")
        self._guard()
        return {"revision": 1, "changed": True, "secrets": {}, "settings": {}}


def kadro(*, lookup: str, revision: int = 7, status: str = "active",
          users: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Merkezin döndürdüğü kadro. `lookup` KURULUMUN pepper'ıyla üretilir —
    yansıtmanın çalışması buna bağlıdır (bkz. roster_projection modül başlığı)."""
    merkez = {
        "id": MERKEZ_ID,
        "first_name": "Ayşe", "last_name": "Yılmaz",
        "org_scope": "org", "status": status,
        "password_hash": _hasher.hash(MERKEZ_PIN),
        "secret_lookup": lookup,
        "password_set_at": "2026-08-16T10:00:00+00:00",
        "created_at": "2026-08-16T10:00:00+00:00",
        "updated_at": "2026-08-16T10:00:00+00:00",
        "revision": 1,
        "directory_visible": True,
        "roles": ["admin"],
    }
    return {
        "revision": revision,
        "changed": True,
        "users": [merkez] if users is None else users,
        "roles": [
            {"id": "admin", "name": "Admin", "description": "Tam yetki.", "builtin": True},
            {"id": "org_staff", "name": "Kurum Personeli", "description": "", "builtin": True},
        ],
        "grants": {
            "role_permissions": [{"role_id": "admin", "permission": "users.view"}],
            "user_roles": [{"user_id": MERKEZ_ID, "role_id": "admin"}],
        },
    }


# ------------------------------------------------- uçtan uca: giriş yolu


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Kimlik senkronu AÇIK bir kurulum. Merkez istemcisi taklit edilir."""
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YEREL_PIN}
    data["platform"] = {
        **data.get("platform", {}),
        "identity_sync": {
            **(data.get("platform", {}).get("identity_sync") or {}),
            "enabled": True,
            "base_url": "https://kontrolmerkezi.example",
            "cache_path": str(tmp_path / "identity-roster.json"),
            "max_cache_age_hours": 24,
            "require_pairing": True,
        },
    }
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as test:
        yield test


def esle(client: TestClient, **kwargs: Any) -> SahteIstemci:
    """Kurulumu merkezle eşler; kadro eşlemenin ardından çekilir.

    Kadro `secret_lookup` değerini KURULUMUN pepper'ıyla üretir: gerçek
    dağıtımda merkez ile kurulum aynı `KM_IDENTITY_PEPPER`i paylaşır.
    """
    kimlik = client.app.state.identity
    istemci = SahteIstemci(roster=kadro(lookup=kimlik.secret_lookup(MERKEZ_PIN), **kwargs))
    client.app.state.identity_sync._client = istemci
    cevap = client.post("/api/pairing/pair", json={"code": "12345678"})
    assert cevap.status_code == 200, cevap.text
    return istemci


def giris(client: TestClient, pin: str) -> Any:
    return client.post("/api/auth/login", json={"password": pin})


def eskit(client: TestClient, saat: int = 30) -> None:
    """Önbelleğin `fetchedAt` damgasını geriye alır."""
    cache = client.app.state.identity_sync.cache
    kayit = cache.read() or {}
    kayit["fetchedAt"] = (datetime.now(UTC) - timedelta(hours=saat)).isoformat(timespec="seconds")
    cache.path.write_text(json.dumps(kayit, ensure_ascii=False), encoding="utf-8")


def test_MERKEZDE_ACILAN_KULLANICI_GIRIS_YAPAR(client: TestClient) -> None:
    """ADR 0021'in asıl amacı. Önbellek tek başına yetmez; yansıtma şart."""
    esle(client)

    cevap = giris(client, MERKEZ_PIN)
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert govde["token"]
    assert govde["user"]["id"] == MERKEZ_ID
    assert govde["user"]["roles"] == ["admin"]
    # Rol → izin bağı da geldi: yetki yeniden kurulabildi.
    assert "users.view" in govde["user"]["permissions"]


def test_yerel_yonetici_yansitmadan_sonra_da_girer(client: TestClient) -> None:
    """YEREL KAYIT SİLİNMEZ. Merkezin kadrosu yerel kullanıcıyı götürmez."""
    esle(client)
    assert giris(client, MERKEZ_PIN).status_code == 200

    cevap = giris(client, YEREL_PIN)
    assert cevap.status_code == 200, cevap.text

    kullanicilar = client.get(
        "/api/users", headers={"Authorization": f"Bearer {cevap.json()['token']}"}
    ).json()["users"]
    kaynaklar = {k["id"]: k["origin"] for k in kullanicilar}
    assert kaynaklar[MERKEZ_ID] == ORIGIN_CENTRAL
    assert len([k for k, v in kaynaklar.items() if v == ORIGIN_LOCAL]) == 1


def test_merkezden_gelen_pasiflestirme_uygulanir(client: TestClient) -> None:
    istemci = esle(client)
    assert giris(client, MERKEZ_PIN).status_code == 200

    # Merkez kullanıcıyı pasifleştirdi ve revizyonu artırdı.
    istemci.roster_payload = kadro(
        lookup=client.app.state.identity.secret_lookup(MERKEZ_PIN),
        revision=8, status="disabled",
    )
    eskit(client)  # bir sonraki girişte yeniden senkron olsun

    assert giris(client, MERKEZ_PIN).status_code == 401
    # Yerel yönetici etkilenmez.
    assert giris(client, YEREL_PIN).status_code == 200


def test_kadrodan_dusen_kullanici_pasiflesir_SILINMEZ(client: TestClient) -> None:
    istemci = esle(client)
    assert giris(client, MERKEZ_PIN).status_code == 200

    # Kadro artık o kullanıcıyı hiç taşımıyor.
    istemci.roster_payload = kadro(
        lookup=client.app.state.identity.secret_lookup(MERKEZ_PIN), revision=9, users=[]
    )
    eskit(client)

    cevap = giris(client, YEREL_PIN)
    assert cevap.status_code == 200
    kullanicilar = client.get(
        "/api/users", headers={"Authorization": f"Bearer {cevap.json()['token']}"}
    ).json()["users"]
    dusen = next(k for k in kullanicilar if k["id"] == MERKEZ_ID)
    # KAYIT DURUYOR, yalnız pasifleşti.
    assert dusen["status"] == "disabled"
    assert giris(client, MERKEZ_PIN).status_code == 401


def test_sinir_asilinca_baglanti_yoksa_giris_REDDEDILIR(client: TestClient) -> None:
    """Pasifleştirilen bir kullanıcının çevrimdışı bir makinede sonsuza dek
    giriş yapabilmesi kabul edilemez (ADR 0021 — Sonuçlar)."""
    istemci = esle(client)
    eskit(client)
    istemci.online = False

    cevap = giris(client, MERKEZ_PIN)
    assert cevap.status_code == 503
    mesaj = cevap.json()["error"]["message"]
    assert "bağlantı gerekiyor" in mesaj
    assert "eskidi" in mesaj

    # Yerel yönetici de aynı kapıya takılır: kural kadronun tamamı içindir.
    assert giris(client, YEREL_PIN).status_code == 503

    # Merkez dönünce kadro tazelenir ve giriş açılır.
    istemci.online = True
    assert giris(client, MERKEZ_PIN).status_code == 200


def test_esleme_yokken_giris_bugunku_gibi_calisir(client: TestClient) -> None:
    """Yetenek açık ama kurulum HENÜZ EŞLENMEMİŞ: önbellek yok, kapı çalışmaz.

    Aksi hâlde eşleme ekranındaki bir makinenin ilk yöneticisi kilitlenirdi.
    """
    assert client.app.state.identity_sync.cache.read() is None
    assert giris(client, YEREL_PIN).status_code == 200


# -------------------------------------------------- gerileme yasağı (kapalı)


@pytest.fixture
def kapali_client(tmp_path: Path) -> Iterator[TestClient]:
    """Varsayılan ayar: `platform.identity_sync.enabled: false`.

    KAPALI DURUM AYARDAN DEĞİL, BURADAN GELİR. `load_config()` makineye özel
    `config/local.yaml` dosyasını da okur; o dosyada merkez AÇILDIĞI gün
    (17.08.2026 — uygulama birden çok cihaza kurulacak) bu fixture sessizce
    "açık" hâle geldi ve testin sınadığı gerileme yasağı sınanmaz oldu.
    Git dışı bir dosyanın kapıyı belirlemesi, açık kapıyı yeşil gösteren bir
    kapıdır; beklenen ayar testin kendisinde durur.
    """
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YEREL_PIN}
    data["platform"] = {
        **data.get("platform", {}),
        "identity_sync": {
            **(data.get("platform", {}).get("identity_sync") or {}),
            "enabled": False,
            "base_url": "",
        },
    }
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as test:
        yield test


def test_yetenek_kapaliyken_hicbir_ek_kapi_calismaz(kapali_client: TestClient) -> None:
    """GERİLEME YASAK: kapalı ayarda giriş bugünkü gibi yereldir ve yansıtma
    hiç koşmaz."""
    sync = kapali_client.app.state.identity_sync
    assert sync.configured is False

    cevap = giris(kapali_client, YEREL_PIN)
    assert cevap.status_code == 200

    # Yansıtma kaydı hiç doğmadı: kapı gövdesine girilmedi.
    store = kapali_client.app.state.store
    portal = kapali_client.portal
    assert portal is not None
    assert portal.call(projected_revision, store) is None


# ------------------------------------------------ yansıtmanın kendi kuralları


@pytest.fixture
async def depo(tmp_path: Path) -> Any:
    store = Store(tmp_path / "km.sqlite")
    await store.open()
    await apply_core_migrations(store)
    yield store
    await store.close()


async def test_ayni_revizyon_yeniden_yazilmaz(depo: Store) -> None:
    payload = kadro(lookup="arama-degeri")
    ilk = await project_roster(depo, payload)
    assert ilk["projected"] is True
    assert await projected_revision(depo) == 7

    ikinci = await project_roster(depo, payload)
    assert ikinci == {"projected": False, "reason": "değişmedi", "revision": 7}


async def test_ayni_kimlikli_yerel_kayit_EZILMEZ(depo: Store) -> None:
    """Kimlikler uuid olduğu için beklenmez; olursa yerel kayıt kazanır."""
    await depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
        "pin_lookup, pin_set_at, created_at, updated_at, origin) "
        "VALUES (?, 'Yerel', 'Kayit', 'org', 'active', '', 'pin-yok:yerel', "
        "'dun', 'dun', 'dun', ?)",
        (MERKEZ_ID, ORIGIN_LOCAL),
    )

    sonuc = await project_roster(depo, kadro(lookup="arama-degeri"))
    assert sonuc["localKept"] == [MERKEZ_ID]

    row = await depo.fetch_one("SELECT first_name, origin FROM users WHERE id = ?", (MERKEZ_ID,))
    assert row is not None
    assert row["first_name"] == "Yerel"
    assert row["origin"] == ORIGIN_LOCAL


async def test_ayni_PIN_catismasinda_merkez_kaydi_SIRSIZ_yansitilir(depo: Store) -> None:
    """`secret_lookup` UNIQUE'tir. Yerelde aynı PIN kullanılıyorsa merkez kaydı
    yine açılır ama sırsız: aynı sır iki kişiye ait olamaz."""
    await depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
        "pin_lookup, pin_set_at, password_hash, secret_lookup, created_at, updated_at) "
        "VALUES ('yerel-1', 'Yerel', 'Kayit', 'org', 'active', '', 'pin-yok:yerel-1', "
        "'dun', 'argon2', 'ayni-arama', 'dun', 'dun')"
    )

    sonuc = await project_roster(depo, kadro(lookup="ayni-arama"))
    assert sonuc["projected"] is True

    merkez = await depo.fetch_one(
        "SELECT secret_lookup, origin, status FROM users WHERE id = ?", (MERKEZ_ID,)
    )
    assert merkez is not None
    assert merkez["origin"] == ORIGIN_CENTRAL
    assert merkez["secret_lookup"] is None
    # Yerel kaydın sırrı yerinde durur.
    yerel = await depo.fetch_one("SELECT secret_lookup FROM users WHERE id = 'yerel-1'")
    assert yerel is not None
    assert yerel["secret_lookup"] == "ayni-arama"


async def test_yerel_izin_atamalari_silinmez(depo: Store) -> None:
    """`role_permissions` YALNIZ EKLENİR: bu kurulumdaki modül izinleri
    merkezde yok diye düşmez."""
    await depo.execute(
        "INSERT INTO role_permissions (role_id, permission) VALUES ('admin', 'bell.manage')"
    )
    await project_roster(depo, kadro(lookup="arama-degeri"))

    izinler = {
        (row["role_id"], row["permission"])
        for row in await depo.fetch_all("SELECT role_id, permission FROM role_permissions")
    }
    assert ("admin", "bell.manage") in izinler
    assert ("admin", "users.view") in izinler


async def test_kadro_yoksa_yansitma_yapilmaz(depo: Store) -> None:
    """Merkez `changed: false` dediğinde önbellekte kullanıcı listesi yoktur."""
    sonuc = await project_roster(depo, {"revision": 7, "changed": False})
    assert sonuc == {"projected": False, "reason": "kadro yok"}
    assert await projected_revision(depo) is None
