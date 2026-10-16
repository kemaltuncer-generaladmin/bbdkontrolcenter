"""Uç envanteri denetimi (iş 63) — KM'nin HER HTTP ucu için kalıcı güvenlik kuralları.

Uygulama gerçekten ayağa kaldırılır (bütün etkin modüller, sunucu görünümü: Linux) ve rota
tablosu `tools/security/endpoint_inventory.py` ile çıkarılır. Kurallar:

1. **Kimliksiz uç yok** — `PUBLIC_ENDPOINTS` dışındaki her uç oturum ya da servis anahtarı
   ister. Listedeki her satırın gerekçesi yazılıdır ve uç gerçekten var olmalıdır.
2. **İzinsiz yazma ucu yok** — oturum isteyen her yazma ucu en az bir izin kapısından geçer
   (K9). Yalnız oturum isteyen okumalar `SESSION_ONLY` listesindedir.
3. **Her izin katalogda** — uçların sorduğu her izin çekirdekte ya da bir manifestte ilan
   edilmiştir ve `docs/permissions.md`'de yazılıdır.
4. **Yönetimsel/yıkıcı uç step-up ister** — yıkıcı (`destructive: true`) ya da yetki veren
   çekirdek izinle korunan yazma ucu PIN teyidi ya da gerekçeli onay (ADR 0012) taşır;
   istisnalar `STEP_UP_EXCEPTIONS`'ta gerekçe + bulgu kimliğiyle durur.
5. **Belgeler güncel** — `docs/security/endpoint-envanteri.md` ve `docs/permissions.md`
   izin kataloğu üreticinin çıktısıyla aynı.

Yeni bir uç bu kurallardan birine takılırsa doğru düzeltme ucun kapısıdır; listeye satır
eklemek ancak gerekçesiyle ve gözden geçirmeyle olur.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from km_core.security.permissions import CORE_PERMISSION_KEYS, CORE_PERMISSIONS

ROOT = Path(__file__).resolve().parents[2]

#: (yöntem, yol) → gerekçe. Oturum İSTEMEYEN uçlar.
PUBLIC_ENDPOINTS: dict[tuple[str, str], str] = {
    ("GET", "/health"): "Sağlık yoklaması; sır taşımaz (yalnız commit kısaltması, modül sayıları).",
    ("POST", "/api/auth/login"): "Girişin kendisi; PIN ile korunur, IP + genel hız sınırı (KM-01).",
    ("POST", "/api/auth/set-password"): "Mevcut PIN ile korunur; giriş kovasını paylaşır (KM-01).",
    ("POST", "/api/auth/logout"): "Yalnız taşınan belirteci siler; yanıt tek tip.",
    ("POST", "/auth/logout"): "Eski yol; yalnız taşınan belirteci siler.",
    ("GET", "/api/pairing/state"): "Eşleme ekranı girişten önce açılır; sır dönmez (ADR 0021 §4).",
    ("POST", "/api/pairing/pair"): "Tek kullanımlık merkez koduyla korunur; deneme kısıtı var.",
    ("POST", "/api/pairing/reset"): "Yalnız anahtar uyuşmazlığında / yerelden / installations.manage "
                                    "ile çalışır (KM-05); sunucuda yetenek kapalı → 409.",
    # Kurum tabletleri: KM oturumu yoktur, uç KENDİ cihaz/öğrenci belirtecini doğrular
    # (`modules/bbd_tablet/backend/api/tablet_routes.py` → device_auth / session auth).
    ("POST", "/api/tablet/v1/devices/enroll"): "Tablet: 60 dk geçerli 8 haneli (40 bit) eşleme kodu.",
    ("GET", "/api/tablet/v1/profiles"): "Tablet cihaz belirteci.",
    ("GET", "/api/tablet/v1/profiles/{profile_id}"): "Tablet cihaz belirteci.",
    ("POST", "/api/tablet/v1/devices/{device_id}/initial-profile"): "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("POST", "/api/tablet/v1/devices/{device_id}/sync"): "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("GET", "/api/tablet/v1/devices/{device_id}/releases/{release_id}/files/{index}"):
        "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("GET", "/api/tablet/v1/devices/{device_id}/self-update/{release_id}"):
        "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("POST", "/api/tablet/v1/devices/{device_id}/diagnostics"): "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("GET", "/api/tablet/v1/devices/{device_id}/policy"): "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("POST", "/api/tablet/v1/devices/{device_id}/heartbeat"): "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("POST", "/api/tablet/v1/devices/{device_id}/unpair"): "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("PUT", "/api/tablet/v1/devices/{device_id}/apps"): "Tablet cihaz belirteci + cihaz eşleşmesi.",
    ("POST", "/api/tablet/v1/auth/login"): "Tablet cihaz belirteci; öğrenci kodu denemesi kısıtlı.",
    ("POST", "/api/tablet/v1/auth/logout"): "Öğrenci oturumu çıkış belirteci.",
    ("POST", "/api/tablet/v1/usage/batch"): "Öğrenci oturumu kullanım belirteci.",
}

#: Oturum isteyen ama izin sormayan OKUMALAR (yazma asla).
SESSION_ONLY: dict[tuple[str, str], str] = {
    ("GET", "/auth/me"): "Oturum sahibinin kendi özeti.",
    ("GET", "/modules"): "Menü kaydı; ekranlar kullanıcının izinlerine göre süzülür.",
    ("GET", "/api/events/wait"): "Uzun yoklama; olaylar kişisel veri taşımaz (ADR 0029).",
}

#: Yetki veren çekirdek izinler — yıkıcı bayrağı olmasa da step-up ister (permissions.md kural 3).
PRIVILEGE_PERMISSIONS = frozenset({
    "users.set_password", "roles.manage", "secrets.manage", "database.restore",
    "installations.manage",
})

#: (yöntem, yol) → gerekçe (+ bulgu kimliği). Step-up taşımayan yıkıcı/yönetimsel uçlar.
STEP_UP_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/pairing/pair-code"): "ADR 0026: sunucuda merkezî kimlik yeteneği kapalı; kod üretmek "
                                        "makineye erişim vermez, merkez ayrıca onaylar (KM-11, düşük).",
    ("POST", "/api/pairing/installations/{installation_id}/revoke"):
        "Savunma işlemi (kurulum iptali) — hızlı olmalı; denetim izine yazılır (KM-11).",
    # Günlük kantin/ödeme işleri: yıkıcı bayrağı "geri alınamaz/para/ileti" demek; her seferinde
    # PIN sormak ADR 0012'nin reddettiği sürtünme. Ayrı izin anahtarı + denetim kaydı + ekranda
    # onay penceresi var (KM-12, kabul edilmiş tasarım kararı).
    ("PUT", "/api/bbd_canteen_products/products/{product_id}/active"): "Günlük iş; ayrı izin (KM-12).",
    ("POST", "/api/bbd_payment_request/collections"): "Kasa tahsilatı; günlük iş, ayrı izin (KM-12).",
    ("POST", "/api/bbd_payment_request/requests/{ref}/cancel"): "Günlük iş; ayrı izin (KM-12).",
    ("POST", "/api/bbd_payment_request/requests/{ref}/reopen"): "Günlük iş; ayrı izin (KM-12).",
    ("POST", "/api/bbd_payment_request/requests/{ref}/resend"): "Günlük iş; ayrı izin (KM-12).",
    ("POST", "/api/bbd_payment_request/send"): "Günlük iş; ayrı izin (KM-12).",
    ("POST", "/api/bbd_payment_request/students/{kantin_id}/request"): "Günlük iş; ayrı izin (KM-12).",
    ("POST", "/api/bbd_sms/send"): "Günlük iş; ayrı izin, SMS kotası sağlayıcıda (KM-12).",
}


def _load_tool() -> ModuleType:
    path = ROOT / "tools" / "security" / "endpoint_inventory.py"
    spec = importlib.util.spec_from_file_location("km_endpoint_inventory", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    return _load_tool()


@pytest.fixture(scope="module")
def envanter(tool: ModuleType) -> tuple[list[Any], list[dict[str, Any]]]:
    endpoints, modules = tool.inventory()
    return endpoints, modules


def test_envanter_gercekten_tarar(envanter: tuple[list[Any], list[dict[str, Any]]]) -> None:
    endpoints, modules = envanter
    assert len(endpoints) > 400
    assert {"core", "platform", "bbd_akademi", "bbd_students"} <= {e.module for e in endpoints}
    assert not [m for m in modules if m.get("state") == "failed"], "yüklenemeyen modül var"


def test_kimliksiz_uc_yalniz_gerekceli_listede(envanter: tuple[list[Any], list[dict[str, Any]]]) -> None:
    endpoints, _ = envanter
    acik = {e.key for e in endpoints if not e.authenticated}
    assert acik - set(PUBLIC_ENDPOINTS) == set(), "gerekçesiz kimliksiz uç"
    assert set(PUBLIC_ENDPOINTS) - acik == set(), "listede olup artık var olmayan / kimlik isteyen uç"


def test_izinsiz_yazma_ucu_yok(envanter: tuple[list[Any], list[dict[str, Any]]]) -> None:
    endpoints, _ = envanter
    yazma = [e.key for e in endpoints if e.authenticated and e.write and not e.groups]
    assert yazma == [], "oturum isteyip izin sormayan yazma ucu (K9)"
    okuma = {e.key for e in endpoints if e.authenticated and not e.write and not e.groups}
    assert okuma == set(SESSION_ONLY)


def test_her_izin_ilan_edilmis_ve_belgelenmis(envanter: tuple[list[Any], list[dict[str, Any]]]) -> None:
    endpoints, modules = envanter
    declared = set(CORE_PERMISSION_KEYS) | {p["key"] for m in modules for p in m["permissions"]}
    used = {perm for e in endpoints for perm in e.permissions}
    assert used - declared == set(), "ilan edilmemiş izin soran uç"
    doc = (ROOT / "docs" / "permissions.md").read_text(encoding="utf-8")
    assert sorted(k for k in used if f"`{k}`" not in doc) == []


def _sensitive(modules: list[dict[str, Any]]) -> set[str]:
    out = set(PRIVILEGE_PERMISSIONS)
    out |= {p["key"] for p in CORE_PERMISSIONS if p.get("destructive")}
    out |= {p["key"] for m in modules for p in m["permissions"] if p.get("destructive")}
    return out


def test_yonetimsel_ve_yikici_uclar_step_up_ister(envanter: tuple[list[Any], list[dict[str, Any]]]) -> None:
    endpoints, modules = envanter
    hassas = _sensitive(modules)
    eksik = sorted(
        e.key for e in endpoints
        if e.write and not e.step_up
        and any({entry.partition(":")[0] for entry in group} <= hassas for group in e.groups)
    )
    assert [key for key in eksik if key not in STEP_UP_EXCEPTIONS] == [], "step-up'sız yıkıcı uç"
    assert sorted(set(STEP_UP_EXCEPTIONS) - set(eksik)) == [], "istisna artık gereksiz — listeden çıkarın"


def test_kullanici_yonetimi_pin_teyidi_ister(envanter: tuple[list[Any], list[dict[str, Any]]]) -> None:
    """KM-06: yetki veren üç uç PIN teyidi taşır (kural 3)."""
    endpoints, _ = envanter
    by_key = {e.key: e for e in endpoints}
    for key in (("POST", "/api/users"), ("PUT", "/api/users/{user_id}"),
                ("POST", "/api/users/{user_id}/password"), ("POST", "/api/pairing/unpair")):
        assert by_key[key].step_up == "pin", key


def test_belgeler_guncel(tool: ModuleType, envanter: tuple[list[Any], list[dict[str, Any]]]) -> None:
    endpoints, modules = envanter
    hint = "eski — `.venv/bin/python tools/security/endpoint_inventory.py` koşturun"
    assert tool.OUTPUT.read_text(encoding="utf-8") == tool.render(endpoints, modules), \
        f"docs/security/endpoint-envanteri.md {hint}"
    doc = tool.PERMISSIONS_DOC.read_text(encoding="utf-8")
    assert doc == tool.with_catalog(doc, tool.render_catalog(modules)), f"docs/permissions.md {hint}"
