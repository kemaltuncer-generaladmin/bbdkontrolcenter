"""Kimlik senkronu yeteneği (ADR 0021 — Kontrol Merkezi tarafı).

Bağlayıcı davranışlar:

  · SERVİS YOKSA HİÇBİR YETENEK GERİLEMEZ — yetenek kapalıyken giriş yereldir,
    eşleme ekranı hiç açılmaz.
  · GİRİŞ ÇEVRİMDIŞI ÇALIŞIR; tek istisna önbelleğin yaş sınırını aşmasıdır.
  · YAZMA YALNIZ ÇEVRİMİÇİDİR ve bağlantı yokken **denenmez** — istek hiç
    gönderilmez.
  · `revision` değişmemişse önbellek yeniden yazılmaz.
  · Önbellek 0600'dür: içinde PIN hash'i vardır.
  · DENETİM KAYDI ASLA DÜŞMEZ: gönderilemeyen kayıt kuyrukta birikir ve geri
    çekilmeli olarak yeniden denenir (ADR 0021 §5).

Ağa çıkılmaz; istemci taklit edilir.
"""

from __future__ import annotations

import json
import stat
from collections.abc import AsyncIterator, Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security.migrations import apply_core_migrations
from km_core.store.db import Store
from km_platform.identity_sync.client import IdentityResponseError
from km_platform.identity_sync.errors import (
    IdentitySyncError,
    ManagementKeyMissing,
    NotPaired,
    WriteRequiresConnection,
)
from km_platform.identity_sync.queue import backoff_seconds
from km_platform.identity_sync.service import TOKEN_KEY, IdentitySync

KADRO: dict[str, Any] = {
    "revision": 7,
    "changed": True,
    "users": [{"id": "u-1", "first_name": "Ayşe", "last_name": "Yılmaz",
               "password_hash": "$argon2id$sahte", "secret_lookup": "abc",
               "status": "active", "roles": ["admin"]}],
    "roles": [{"id": "admin", "name": "Admin", "description": "", "builtin": True}],
    "grants": {"role_permissions": [], "user_roles": []},
}


class SahteKasa:
    """`Vault`ın yeteneğe bakan yüzü. Sır bellekte durur, diske yazılmaz."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class SahteIstemci:
    def __init__(self, *, online: bool = True, roster: dict[str, Any] | None = None,
                 provisioning: dict[str, Any] | None = None) -> None:
        self.online = online
        self.roster_payload = roster or deepcopy(KADRO)
        # ADR 0025 — `sync()` kadronun ardından kurulum paketini de sorar.
        # Varsayılan BOŞ PAKETTİR: bu dosyanın testleri kadroyu ölçüyor ve
        # paketin içeriği onları ilgilendirmiyor. Paketin kendi davranışı
        # `test_kimlik_kurulum_paketi.py` içinde sınanır.
        self.provisioning_payload = provisioning or {
            "revision": 1, "changed": True, "secrets": {}, "settings": {},
        }
        self.calls: list[str] = []

    def _guard(self) -> None:
        if not self.online:
            raise IdentitySyncError("Kimlik servisine ulaşılamadı: ağ yok")

    async def health(self) -> dict[str, Any]:
        self.calls.append("health")
        self._guard()
        return {"status": "ok"}

    async def roster(self, token: str, *, known_revision: int | None = None) -> dict[str, Any]:
        self.calls.append("roster")
        self._guard()
        if known_revision is not None and known_revision == self.roster_payload["revision"]:
            return {"revision": known_revision, "changed": False}
        return deepcopy(self.roster_payload)

    async def provisioning(self, token: str, *,
                           known_revision: int | None = None) -> dict[str, Any]:
        self.calls.append("provisioning")
        self._guard()
        if known_revision is not None and known_revision == self.provisioning_payload["revision"]:
            return {"revision": known_revision, "changed": False}
        return deepcopy(self.provisioning_payload)

    async def pair(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("pair")
        self._guard()
        self.pair_args = kwargs
        return {"installationId": "i-1", "token": "kurulum-tokeni"}

    async def create_user(self, token: str, actor_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("create_user")
        self._guard()
        return {"revision": 8, "user": body}

    async def update_user(self, token: str, actor_id: str, user_id: str,
                          body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("update_user")
        self._guard()
        return {"revision": 8, "user": body}

    async def set_status(self, token: str, actor_id: str, user_id: str,
                         status: str) -> dict[str, Any]:
        self.calls.append("set_status")
        self._guard()
        return {"revision": 8, "user": {"status": status}}

    async def push_audit(self, token: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append("push_audit")
        self._guard()
        return {"accepted": len(entries)}

    # -------------------------------------------------------- kurulum yönetimi

    async def create_pair_code(self, admin_token: str, *,
                               note: str | None = None) -> dict[str, Any]:
        self.calls.append("create_pair_code")
        self._guard()
        self.admin_token = admin_token
        return {"code": "12345678", "expiresAt": "2026-08-17T09:10:00+00:00"}

    async def installations(self, admin_token: str) -> list[dict[str, Any]]:
        self.calls.append("installations")
        self._guard()
        self.admin_token = admin_token
        return [{"id": "i-1", "machineName": "MSI", "platform": "Linux",
                 "version": "0.1.0", "status": "active", "pairedAt": "2026-08-17T08:00:00+00:00",
                 "lastSeenAt": "2026-08-17T09:00:00+00:00", "revokedAt": None}]

    async def revoke_installation(self, admin_token: str,
                                  installation_id: str) -> dict[str, Any]:
        self.calls.append("revoke_installation")
        self._guard()
        self.admin_token = admin_token
        return {"id": installation_id, "status": "revoked",
                "revokedAt": "2026-08-17T09:30:00+00:00"}


def yapilandir(tmp_path: Path, **overrides: Any) -> Config:
    blok = {
        "enabled": True,
        "base_url": "https://kontrolmerkezi.example",
        "cache_path": str(tmp_path / "identity-roster.json"),
        "max_cache_age_hours": 72,
        "require_pairing": True,
        **overrides,
    }
    return Config({"platform": {"identity_sync": blok}}, root=tmp_path)


def kur(tmp_path: Path, *, kasa: SahteKasa | None = None,
        istemci: SahteIstemci | None = None, depo: Store | None = None,
        **overrides: Any) -> tuple[IdentitySync, SahteIstemci]:
    kasa = kasa or SahteKasa()
    istemci = istemci or SahteIstemci()
    sync = IdentitySync(
        kasa,  # type: ignore[arg-type]
        yapilandir(tmp_path, **overrides),
        client=istemci,  # type: ignore[arg-type]
        store=depo,
    )
    return sync, istemci


@pytest.fixture
async def depo(tmp_path: Path) -> AsyncIterator[Store]:
    """Denetim kuyruğunun tablosunu taşıyan çekirdek deposu."""
    store = Store(tmp_path / "kuyruk.sqlite")
    await store.open()
    await apply_core_migrations(store)
    yield store
    await store.close()


# ------------------------------------------------------- servis yokken


async def test_kapali_yetenek_hicbir_seyi_degistirmez(tmp_path: Path) -> None:
    """Servis kurulmadan uygulama BUGÜNKÜ GİBİ çalışır."""
    sync, istemci = kur(tmp_path, enabled=False)

    assert sync.configured is False
    assert sync.login_policy() == "local"
    assert await sync.pairing_required() is False
    assert await sync.installation_token() is None
    assert istemci.calls == []


async def test_adres_verilmemis_ayar_acik_sayilmaz(tmp_path: Path) -> None:
    sync, _ = kur(tmp_path, base_url="")
    assert sync.configured is False
    assert sync.login_policy() == "local"


# --------------------------------------------------------- eşleme kipi


async def test_eslenmemis_kurulum_eslesme_ekrani_ister(tmp_path: Path) -> None:
    sync, _ = kur(tmp_path)
    assert await sync.pairing_required() is True

    durum = await sync.state()
    assert durum["pairingRequired"] is True
    assert durum["paired"] is False
    # DURUM SIR DÖNDÜRMEZ.
    assert "token" not in json.dumps(durum)


async def test_eslesme_tokeni_kasaya_yazilir(tmp_path: Path) -> None:
    kasa = SahteKasa()
    sync, istemci = kur(tmp_path, kasa=kasa)

    sonuc = await sync.pair("12345678")

    assert sonuc["paired"] is True
    assert kasa.data[TOKEN_KEY] == "kurulum-tokeni"
    # Anahtar çifti üretildi ve AÇIK anahtar gönderildi; özel anahtar kasada.
    assert "identity_sync.private_key" in kasa.data
    assert istemci.pair_args["public_key"].startswith("-----BEGIN PUBLIC KEY-----")
    assert "PRIVATE KEY" in kasa.data["identity_sync.private_key"]
    # Eşlemenin ardından kadro hemen çekilir.
    assert "roster" in istemci.calls
    assert await sync.pairing_required() is False


async def test_esleme_ayarlanmamis_serviste_yapilmaz(tmp_path: Path) -> None:
    sync, istemci = kur(tmp_path, enabled=False)
    with pytest.raises(IdentitySyncError):
        await sync.pair("12345678")
    assert istemci.calls == []


# --------------------------------------------------------------- kadro


async def test_revizyon_degismemisse_onbellek_yeniden_yazilmaz(tmp_path: Path) -> None:
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    sync, _ = kur(tmp_path, kasa=kasa)

    ilk = await sync.sync()
    # `provisioning` alanı ADR 0025 ile eklendi: senkron artık kadronun
    # ardından kurulum paketini de soruyor. Kadro sözleşmesi değişmedi.
    assert {k: v for k, v in ilk.items() if k != "provisioning"} == {
        "synced": True, "changed": True, "revision": 7,
    }
    once = sync.cache.path.stat().st_mtime_ns

    ikinci = await sync.sync()
    assert ikinci["changed"] is False
    assert sync.cache.path.stat().st_mtime_ns == once


async def test_onbellek_yalniz_sahibine_okunur(tmp_path: Path) -> None:
    """Dosyada `password_hash` var; varsayılan umask altında 0644 doğardı."""
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    sync, _ = kur(tmp_path, kasa=kasa)
    await sync.sync()

    izin = stat.S_IMODE(sync.cache.path.stat().st_mode)
    assert izin == 0o600
    icerik = json.loads(sync.cache.path.read_text(encoding="utf-8"))
    assert icerik["revision"] == 7
    assert icerik["fetchedAt"]


async def test_merkez_dustugunde_senkron_hata_yukseltmez(tmp_path: Path) -> None:
    """Senkron başarısız olduğunda kurulum eldeki önbellekle çalışmaya devam
    eder; giriş bozulmaz (K7)."""
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    sync, _ = kur(tmp_path, kasa=kasa, istemci=SahteIstemci(online=False))

    sonuc = await sync.sync()
    assert sonuc["synced"] is False
    assert sync.cache.read() is None


async def test_eslenmemis_kurulum_kadro_cekmez(tmp_path: Path) -> None:
    sync, istemci = kur(tmp_path)
    sonuc = await sync.sync()
    assert sonuc == {"synced": False, "reason": "eşlenmemiş"}
    assert istemci.calls == []


# ------------------------------------------------------- önbellek yaşı


async def test_taze_onbellekle_cevrimdisi_giris_kabul_edilir(tmp_path: Path) -> None:
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    sync, _ = kur(tmp_path, kasa=kasa)
    await sync.sync()

    assert sync.login_policy() == "local"


async def test_sinir_asilinca_yalniz_cevrimici_giris(tmp_path: Path) -> None:
    """Pasifleştirilen bir kullanıcının çevrimdışı bir makinede sonsuza dek
    giriş yapabilmesi kabul edilemez (ADR 0021 — Sonuçlar)."""
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    sync, _ = kur(tmp_path, kasa=kasa, max_cache_age_hours=24)
    await sync.sync()

    eski = datetime.now(UTC) - timedelta(hours=30)
    kayit = sync.cache.read() or {}
    kayit["fetchedAt"] = eski.isoformat(timespec="seconds")
    sync.cache.path.write_text(json.dumps(kayit), encoding="utf-8")

    assert sync.login_policy() == "online_only"


async def test_sinir_sifirsa_onbellek_eskimez(tmp_path: Path) -> None:
    sync, _ = kur(tmp_path, max_cache_age_hours=0)
    assert sync.login_policy() == "local"


# --------------------------------------------------------------- yazma


async def test_eslenmemis_kurulum_merkeze_yazamaz(tmp_path: Path) -> None:
    sync, istemci = kur(tmp_path)
    with pytest.raises(NotPaired):
        await sync.create_user("u-1", {"firstName": "Veli"})
    assert istemci.calls == []


async def test_baglanti_yokken_yazma_DENENMEZ(tmp_path: Path) -> None:
    """Ekran "bu işlem için bağlantı gerekiyor" der ve istek HİÇ GÖNDERİLMEZ.

    Yarım yazılmış bir kadro, hiç yazılmamıştan kötüdür (ADR 0021 §3).
    """
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    istemci = SahteIstemci(online=False)
    sync, _ = kur(tmp_path, kasa=kasa, istemci=istemci)

    with pytest.raises(WriteRequiresConnection) as hata:
        await sync.create_user("u-1", {"firstName": "Veli"})
    assert "bağlantı gerekiyor" in str(hata.value)

    with pytest.raises(WriteRequiresConnection):
        await sync.set_status("u-1", "u-2", "disabled")

    # YALNIZ SAĞLIK SORULDU; hiçbir yazma denenmedi.
    assert set(istemci.calls) == {"health"}


async def test_cevrimiciyken_yazma_merkeze_gider(tmp_path: Path) -> None:
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    sync, istemci = kur(tmp_path, kasa=kasa)

    await sync.create_user("u-1", {"firstName": "Veli"})
    await sync.update_user("u-1", "u-2", {"title": "Şef"})
    await sync.set_status("u-1", "u-2", "disabled")

    assert istemci.calls.count("create_user") == 1
    assert istemci.calls.count("update_user") == 1
    assert istemci.calls.count("set_status") == 1


# ------------------------------------------------- kurulum yönetimi (§4)
#
# "KM Cihaz Eşle" ekranının dayandığı üçlü. Hepsi YAZMA YOLUNDADIR: ağ yoksa
# denenmez; yönetim anahtarı yoksa hiç başlamaz.

ADMIN_KEY = "identity_sync.admin_token"


async def test_yonetim_anahtari_yoksa_KOD_URETILMEZ(tmp_path: Path) -> None:
    """Merkezdeki `/installations*` uçları yönetim token'ıyla korunuyor; o
    anahtar kasada yoksa istek HİÇ GÖNDERİLMEZ ve ekran nedenini yazar."""
    sync, istemci = kur(tmp_path)

    with pytest.raises(ManagementKeyMissing) as hata:
        await sync.create_pair_code()
    assert "identity_sync.admin_token" in str(hata.value)

    with pytest.raises(ManagementKeyMissing):
        await sync.installations()
    with pytest.raises(ManagementKeyMissing):
        await sync.revoke_installation("i-1")

    assert istemci.calls == []


async def test_baglanti_yokken_kurulum_listesi_DENENMEZ(tmp_path: Path) -> None:
    """Listeleme bir okuma gibi görünse de merkezin kaydını okur; önbelleği
    yoktur. Ağ yokken denemek, bayat bir listeye bakıp "iptal ettim" demeye
    davet olurdu."""
    kasa = SahteKasa()
    kasa.data[ADMIN_KEY] = "yonetim-anahtari"
    sync, istemci = kur(tmp_path, kasa=kasa, istemci=SahteIstemci(online=False))

    with pytest.raises(WriteRequiresConnection):
        await sync.installations()
    with pytest.raises(WriteRequiresConnection):
        await sync.create_pair_code()

    # YALNIZ SAĞLIK SORULDU.
    assert set(istemci.calls) == {"health"}


async def test_kurulum_yonetimi_YONETIM_ANAHTARIYLA_konusur(tmp_path: Path) -> None:
    """Kurulum token'ı "bu makine bizim" der, "yeni makine kaydedebilirim"
    demez. İkisi ayrı anahtardır ve karıştırılmaz."""
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "kurulum-tokeni"
    kasa.data[ADMIN_KEY] = "yonetim-anahtari"
    sync, istemci = kur(tmp_path, kasa=kasa)

    kod = await sync.create_pair_code("Muhasebe dizüstü")
    assert kod["code"] == "12345678"
    # SÜREYİ SUNUCU SÖYLER — arayüz yalnız geri sayar.
    assert kod["expiresAt"] == "2026-08-17T09:10:00+00:00"
    assert istemci.admin_token == "yonetim-anahtari"

    liste = await sync.installations()
    assert liste[0]["machineName"] == "MSI"

    iptal = await sync.revoke_installation("i-1")
    assert iptal["status"] == "revoked"
    assert iptal["revokedAt"]
    assert istemci.admin_token == "yonetim-anahtari"


# ----------------------------------------------------------- eşlemeyi çöz


async def test_esleme_cozulunce_TOKEN_SILINIR_OZEL_ANAHTAR_KALIR(
    tmp_path: Path, depo: Store,
) -> None:
    """Aynı makine yeniden eşlendiğinde merkez onu AYNI kimlikle tanımalı."""
    kasa = SahteKasa()
    sync, _ = kur(tmp_path, kasa=kasa, depo=depo)
    await sync.pair("12345678")
    assert kasa.data[TOKEN_KEY]

    sonuc = await sync.unpair()

    assert sonuc["paired"] is False
    assert TOKEN_KEY not in kasa.data
    assert "identity_sync.installation_id" not in kasa.data
    # ÖZEL ANAHTAR KALIR.
    assert "identity_sync.private_key" in kasa.data
    assert await sync.is_paired() is False
    # Önbellek de gider: içinde merkezin PIN hash'leri vardı.
    assert sync.cache.read() is None


async def test_esleme_cozulunce_MERKEZ_KULLANICILARI_PASIFLESIR(
    tmp_path: Path, depo: Store,
) -> None:
    """Merkezden gelen kadro artık tazelenemez; o satırların girişte kabul
    edilmeye devam etmesi, merkezden çıkarılmış birinin bu makinede süresiz
    girebilmesi demekti. SİLİNMEZ — denetim izindeki bağ kopmasın."""
    await depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, origin, "
        "pin_hash, pin_lookup, pin_set_at, created_at, updated_at) "
        "VALUES ('u-merkez', 'Merkez', 'Kullanıcı', 'org', 'active', 'central', "
        "'', 'pin-yok:u-merkez', 'dun', 'dun', 'dun')"
    )
    await depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, origin, "
        "pin_hash, pin_lookup, pin_set_at, created_at, updated_at) "
        "VALUES ('u-yerel', 'Yerel', 'Kullanıcı', 'org', 'active', 'local', "
        "'', 'pin-yok:u-yerel', 'dun', 'dun', 'dun')"
    )
    await depo.execute(
        "INSERT INTO sessions (token, user_id, created_at, last_seen, expires_at) "
        "VALUES ('t-merkez', 'u-merkez', 'dun', 'dun', '2999-01-01T00:00:00+00:00')"
    )

    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "kurulum-tokeni"
    sync, _ = kur(tmp_path, kasa=kasa, depo=depo)

    sonuc = await sync.unpair()
    assert sonuc["disabledUsers"] == 1

    satirlar = {
        str(row["id"]): str(row["status"])
        for row in await depo.fetch_all("SELECT id, status FROM users")
    }
    # İKİ SATIR DA DURUYOR: silme yok.
    assert satirlar == {"u-merkez": "disabled", "u-yerel": "active"}
    # Pasifleşenin açık oturumu da kapanır.
    assert await depo.fetch_all("SELECT token FROM sessions") == []


async def test_denetim_itisi_esleme_ister(tmp_path: Path) -> None:
    """Kuyruk BAĞLANMAMIŞKEN eski sözleşme geçerlidir: hata yükselir ki çağıran
    kayıtları elinde tuttuğunu bilsin."""
    sync, istemci = kur(tmp_path)
    with pytest.raises(NotPaired):
        await sync.push_audit([{"at": "2026-08-17T09:00:00+00:00",
                                "action": "auth.login", "result": "ok"}])
    assert istemci.calls == []


# ------------------------------------------------- denetim kuyruğu (§5)


KAYITLAR = [
    {"at": "2026-08-17T09:00:00+00:00", "action": "auth.login", "result": "ok",
     "userId": "u-1"},
    {"at": "2026-08-17T09:05:00+00:00", "action": "users.update", "result": "ok"},
]


async def test_gonderilemeyen_denetim_kaydi_DUSMEZ(tmp_path: Path, depo: Store) -> None:
    """ADR 0021 §5 — "yerelde birikir ve yeniden denenir; asla düşürülmez"."""
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    istemci = SahteIstemci(online=False)
    sync, _ = kur(tmp_path, kasa=kasa, istemci=istemci, depo=depo)

    sonuc = await sync.push_audit(KAYITLAR)
    assert sonuc["accepted"] == 2
    assert sonuc["sent"] == 0
    assert await sync.audit_pending() == 2

    # GERİ ÇEKİLME: ağ döndü diye hemen yeniden denenmez.
    istemci.online = True
    assert (await sync.flush_audit())["sent"] == 0
    assert await sync.audit_pending() == 2

    # Bekleme dolunca kuyruk boşalır ve kayıtlar merkeze gider.
    await depo.execute(
        "UPDATE identity_audit_queue SET next_attempt_at = '2000-01-01T00:00:00+00:00'"
    )
    bosaldi = await sync.flush_audit()
    assert bosaldi["sent"] == 2
    assert bosaldi["pending"] == 0
    assert await sync.audit_pending() == 0


async def test_denetim_kaydi_gonderilmeden_once_yazilir(tmp_path: Path, depo: Store) -> None:
    """Kayıt kuyruğa ÖNCE girer: gönderim sırasında süreç ölse bile kaybolmaz."""
    kasa = SahteKasa()
    kasa.data[TOKEN_KEY] = "t"
    sync, istemci = kur(tmp_path, kasa=kasa, depo=depo)

    sonuc = await sync.push_audit(KAYITLAR)
    assert sonuc["sent"] == 2
    assert istemci.calls.count("push_audit") == 1
    # Teslim edilen kayıt kuyrukta ikinci kopya olarak durmaz.
    assert await sync.audit_pending() == 0


async def test_eslenmemis_kurulumda_denetim_kaydi_BIRIKIR(tmp_path: Path, depo: Store) -> None:
    """Eşleme henüz yokken de kayıt düşmez; eşlendiğinde gider."""
    kasa = SahteKasa()
    sync, istemci = kur(tmp_path, kasa=kasa, depo=depo)

    sonuc = await sync.push_audit(KAYITLAR)
    assert sonuc["accepted"] == 2
    assert sonuc["reason"] == "eşlenmemiş"
    assert await sync.audit_pending() == 2
    assert istemci.calls == []

    kasa.data[TOKEN_KEY] = "t"
    assert (await sync.flush_audit())["sent"] == 2
    assert await sync.audit_pending() == 0


async def test_geri_cekilme_ikiye_katlanir_ve_bir_saatte_durur() -> None:
    assert backoff_seconds(1) == 30
    assert backoff_seconds(2) == 60
    assert backoff_seconds(3) == 120
    # ÜST SINIR: uzun bir kesintiden sonra kuyruk günlerce beklemez.
    assert backoff_seconds(50) == 3600


# ---------------------------------------------------- kabuğun gördüğü uç


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Aşağıdaki testler "merkez KAPALI" varsayımına dayanır — ayar BURADA
    sabitlenir.

    `load_config()` makineye özel `config/local.yaml` dosyasını da okur ve o
    dosyada merkez açıldığı gün (17.08.2026) bu fixture sessizce "açık" hâle
    geldi: `enabled` alanı `True` döndü, 503 bekleyen uçlar 401 vermeye başladı.
    Git dışı bir dosyanın kapıyı belirlemesi, geliştiricinin makinesine göre
    renk değiştiren bir kapıdır; beklenen ayar testin kendisinde durur.
    """
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": "482913"}
    data["platform"] = {
        **data.get("platform", {}),
        "identity_sync": {
            **(data.get("platform", {}).get("identity_sync") or {}),
            "enabled": False,
            "base_url": "",
        },
    }
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as c:
        yield c


def test_esleme_durumu_oturum_istemez(client: TestClient) -> None:
    """Eşleme girişten ÖNCE gelir: oturum istemek, kurulumu hiç açılamaz
    hâle getirirdi."""
    cevap = client.get("/api/pairing/state")
    assert cevap.status_code == 200
    durum = cevap.json()
    # Varsayılan ayarda yetenek kapalıdır; ekran bugünkü gibi giriş açar.
    assert durum["enabled"] is False
    assert durum["pairingRequired"] is False
    assert durum["loginPolicy"] == "local"


def test_ayarlanmamis_serviste_esleme_503_doner(client: TestClient) -> None:
    cevap = client.post("/api/pairing/pair", json={"code": "12345678"})
    assert cevap.status_code == 503
    assert "ayarlanmamış" in cevap.json()["error"]["message"]


# ---------------------------------------------- çift kapının backend yarısı
#
# K9: "KM Cihaz Eşle" ekranını menüden gizlemek yetkilendirme DEĞİLDİR. Aşağıda
# sınanan şey, izni olmayan bir oturumun uçlara ULAŞAMADIĞIDIR — gerçek oturum
# belirteciyle, taklit bir izin nesnesiyle değil.

YONETICI_PINI = "482913"
PERSONEL_PINI = "735204"

#: Ekranın kullandığı dört uç: (metot, yol, gövde).
YONETIM_UCLARI = [
    ("GET", "/api/pairing/installations", None),
    ("POST", "/api/pairing/pair-code", {"note": None}),
    ("POST", "/api/pairing/installations/i-1/revoke", None),
    ("POST", "/api/pairing/unpair", {"password": PERSONEL_PINI}),
]


def _istek(client: TestClient, metot: str, yol: str, govde: Any,
           token: str | None) -> Any:
    basliklar = {"Authorization": f"Bearer {token}"} if token else {}
    return client.request(metot, yol, headers=basliklar, json=govde)


def _yonetici_token(client: TestClient) -> str:
    cevap = client.post("/api/auth/login", json={"password": YONETICI_PINI})
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def _personel_token(client: TestClient) -> str:
    """`installations.*` taşımayan gerçek bir oturum."""
    yonetici = _yonetici_token(client)
    acildi = client.post("/api/users", headers={"Authorization": f"Bearer {yonetici}"}, json={
        "firstName": "Veli", "lastName": "Demir", "orgScope": "org",
        "roles": ["org_staff"], "password": PERSONEL_PINI,
    })
    assert acildi.status_code == 201, acildi.text
    cevap = client.post("/api/auth/login", json={"password": PERSONEL_PINI})
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


@pytest.mark.parametrize(("metot", "yol", "govde"), YONETIM_UCLARI)
def test_oturumsuz_istek_401(client: TestClient, metot: str, yol: str, govde: Any) -> None:
    assert _istek(client, metot, yol, govde, None).status_code == 401


@pytest.mark.parametrize(("metot", "yol", "govde"), YONETIM_UCLARI)
def test_izinsiz_oturum_403(client: TestClient, metot: str, yol: str, govde: Any) -> None:
    """Kurum personelinin oturumu geçerlidir; işlem yine reddedilir."""
    token = _personel_token(client)
    cevap = _istek(client, metot, yol, govde, token)
    assert cevap.status_code == 403, cevap.text
    assert cevap.json()["error"]["message"] == "Bu işlem için yetkiniz yok."


def test_yetkili_oturum_403_ALMAZ(client: TestClient) -> None:
    """Kapı izne bakıyor, role değil (K10).

    Varsayılan ayarda merkez KAPALI olduğu için uçlar 503 döner — burada
    sınanan şey reddin 403 OLMAMASIDIR: izin kapısı geçilmiş, iş merkezin
    kapalı olmasına takılmıştır.
    """
    token = _yonetici_token(client)
    for metot, yol, govde in YONETIM_UCLARI[:3]:
        cevap = _istek(client, metot, yol, govde, token)
        assert cevap.status_code == 503, f"{yol}: {cevap.text}"
        assert "ayarlanmamış" in cevap.json()["error"]["message"]


def test_esleme_cozme_YANLIS_PIN_ile_yapilamaz(client: TestClient) -> None:
    """YIKICI İŞLEM PIN TEYİDİ İSTER: izin yeterli değildir (permissions.md 3)."""
    token = _yonetici_token(client)

    yanlis = _istek(client, "POST", "/api/pairing/unpair", {"password": "111111"}, token)
    assert yanlis.status_code == 403
    assert "PIN doğrulanamadı" in yanlis.json()["error"]["message"]

    dogru = _istek(client, "POST", "/api/pairing/unpair",
                   {"password": YONETICI_PINI}, token)
    assert dogru.status_code == 200, dogru.text
    assert dogru.json()["paired"] is False


@pytest.fixture
def acik_client(tmp_path: Path) -> Iterator[TestClient]:
    """Kimlik senkronu AÇIK bir kurulum; merkez istemcisi taklit edilir."""
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    data["platform"] = {
        **data.get("platform", {}),
        "identity_sync": {
            **(data.get("platform", {}).get("identity_sync") or {}),
            "enabled": True,
            "base_url": "https://kontrolmerkezi.example",
            "cache_path": str(tmp_path / "identity-roster.json"),
        },
    }
    # Yönetim anahtarı KASADAN gelir; `Vault.get` önce ayardaki `secrets`
    # bloğuna bakar (K8 — depoda sır yoktur, testte taklidi vardır). Kasaya
    # doğrudan yazmak, TestClient'ın kendi olay döngüsündeki bağlantıyı başka
    # bir döngüden kullanmak olurdu.
    data["secrets"] = {**data.get("secrets", {}), ADMIN_KEY: "yonetim-anahtari"}
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as c:
        yield c


def test_kurulum_listesi_yetkiliye_doner(acik_client: TestClient) -> None:
    token = _yonetici_token(acik_client)
    acik_client.app.state.identity_sync._client = SahteIstemci()

    cevap = _istek(acik_client, "GET", "/api/pairing/installations", None, token)
    assert cevap.status_code == 200, cevap.text
    assert cevap.json()["installations"][0]["machineName"] == "MSI"

    # Kod üretimi de geçer ve SÜREYİ SUNUCU söyler.
    kod = _istek(acik_client, "POST", "/api/pairing/pair-code", {"note": None}, token)
    assert kod.status_code == 200, kod.text
    assert kod.json()["expiresAt"]


def test_merkezin_DURUM_KODU_korunur(acik_client: TestClient) -> None:
    """Merkez "kurulum bulunamadı" diyorsa kabuk 404 görür, 503 değil: yeniden
    denemek hiçbir şeyi değiştirmeyecek bir işi "sonra dene" diye göstermek,
    kullanıcıyı olmayan bir arıza aramaya gönderirdi."""
    token = _yonetici_token(acik_client)

    class Yok(SahteIstemci):
        async def revoke_installation(self, admin_token: str,
                                      installation_id: str) -> dict[str, Any]:
            raise IdentityResponseError(404, "Kurulum bulunamadı.")

    acik_client.app.state.identity_sync._client = Yok()

    cevap = _istek(acik_client, "POST", "/api/pairing/installations/yok/revoke", None, token)
    assert cevap.status_code == 404, cevap.text
    assert cevap.json()["error"]["message"] == "Kurulum bulunamadı."


def test_esleme_ekrani_cekirdek_ekranlar_listesinde(client: TestClient) -> None:
    """Ekran menüye `installations.view` ile girer ve `source: core` taşır —
    modül değildir (ADR 0017)."""
    token = _yonetici_token(client)
    cevap = client.get("/modules", headers={"Authorization": f"Bearer {token}"})
    assert cevap.status_code == 200
    kayit = next(m for m in cevap.json()["modules"] if m["id"] == "core_pairing")
    assert kayit["source"] == "core"
    assert kayit["visible"] is True
    assert kayit["ui"]["nav"]["requires"] == ["installations.view"]


def test_esleme_ekrani_yetkisiz_kullaniciya_GORUNMEZ(client: TestClient) -> None:
    """Menü süzmesini çekirdek yapar; kabuk yalnız çizer (K1, K9)."""
    token = _personel_token(client)
    cevap = client.get("/modules", headers={"Authorization": f"Bearer {token}"})
    kayit = next(m for m in cevap.json()["modules"] if m["id"] == "core_pairing")
    assert kayit["visible"] is False
