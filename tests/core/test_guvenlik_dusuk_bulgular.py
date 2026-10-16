"""Güvenlik taramasının (2026-10-16) açık kalan DÜŞÜK bulguları — 63-duzeltici.

Ayrıntı: `docs/security/bulgular-2026-10-16.md`.

* KM-14 — kabuğun `core_request` köprüsü yolu `/` ile başlamayan (ya da `//`,
  şema, `@` taşıyan) isteği reddeder; Bearer başka konağa gidemez. Birim testleri
  Rust'ta (`src-tauri/src/core_path.rs`, bağımlılıksız: `rustc --test`); burada
  köprünün o denetimden geçtiği sabitlenir.
* KM-15 — `files.output_path` / `core.log_path` yalnız izinli kök altında:
  kaydederken 400, depoda kalmış eski kök dışı değer çalışan ayara karışmaz.
* KM-16 — üretilen ilk yönetici PIN'i günlüğe YAZILMAZ; veri dizinindeki 0600
  dosyaya bir kez yazılır.
* KM-17 — mağaza modüllerinin işlem jetonu günlükte maskelenir (ilk 6 karakter).
"""

from __future__ import annotations

import re
import stat
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest
import structlog
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.files.confine import check_setting, confine_stored
from km_core.http.app import BOOTSTRAP_PIN_FILE, create_app
from km_sdk import masked_token

YONETICI_PINI = "482913"
TAURI = ROOT / "apps" / "desktop" / "src-tauri" / "src"


# ------------------------------------------------------------------ KM-14


def test_core_request_yolu_denetimden_gecer() -> None:
    main = (TAURI / "main.rs").read_text(encoding="utf-8")
    body = main[main.index("async fn core_request("):]
    body = body[: body.index("\n}\n")]
    assert "core_path::core_url(&base, &path)?" in body
    assert 'format!("{base}{path}")' not in body
    tests = (TAURI / "core_path.rs").read_text(encoding="utf-8")
    for kotu in ('"@evil.com/api/x"', '"//evil.com/api"', '"https://evil.com/api"', '"/api/x@evil.com"'):
        assert kotu in tests


# ------------------------------------------------------------------ KM-15


@pytest.fixture
def veri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "veri"
    monkeypatch.setenv("KM_DATA_DIR", str(folder))
    return folder


def test_yol_ayari_izinli_kok_disina_cikamaz(veri: Path) -> None:
    assert check_setting("core.log_path", "data/launcher.log", ROOT) is None
    assert check_setting("core.log_path", str(veri / "gunluk" / "km.log"), ROOT) is None
    assert check_setting("files.output_path", str(veri / "cikti"), ROOT) is None
    assert check_setting("files.output_path", "", ROOT) is None          # boş = varsayılan
    for kotu in ("/etc/passwd", "data/../../etc/passwd", str(veri / ".." / "baska.log"), "~/.ssh/id_rsa"):
        assert check_setting("core.log_path", kotu, ROOT), kotu
    for kotu in ("/etc", "/tmp", str(veri / ".." / "disari"), "/var/lib/postgresql"):
        assert check_setting("files.output_path", kotu, ROOT), kotu
    # Başka ayarlara dokunulmaz.
    assert check_setting("app.name", "/etc", ROOT) is None


def test_depodaki_kok_disi_deger_uygulanmaz(veri: Path) -> None:
    kept = confine_stored({"core.log_path": "/etc/passwd", "files.output_path": "/etc",
                           "app.name": "Kurum"}, ROOT)
    assert kept == {"app.name": "Kurum"}


@pytest.fixture
def client(tmp_path: Path, veri: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {**data.get("core", {}), "store_path": str(tmp_path / "km.sqlite"),
                    "secret_key_path": str(tmp_path / "secret.key"),
                    "log_path": str(veri / "gunluk.log")}
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    data["files"] = {"output_path": str(veri / "cikti")}
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as test_client:
        yield test_client


def _token(client: TestClient) -> dict[str, str]:
    cevap = client.post("/api/auth/login", json={"password": YONETICI_PINI})
    assert cevap.status_code == 200, cevap.text
    return {"Authorization": f"Bearer {cevap.json()['token']}"}


def test_ayarlar_ekranindan_kok_disi_yol_reddedilir(client: TestClient, veri: Path) -> None:
    headers = _token(client)
    for tab, key, value in (("core.general", "files.output_path", "/etc"),
                            ("core.diagnostics", "core.log_path", "/etc/passwd"),
                            ("core.diagnostics", "core.log_path", "data/../../../etc/hosts")):
        cevap = client.put("/api/settings", headers=headers, json={"tab": tab, "values": {key: value}})
        assert cevap.status_code == 400, cevap.text
        assert "yalnız" in cevap.json()["error"]["message"]
    tamam = client.put("/api/settings", headers=headers,
                       json={"tab": "core.general", "values": {"files.output_path": str(veri / "raporlar")}})
    assert tamam.status_code == 200, tamam.text
    tani = client.get("/api/settings/diagnostics", headers=headers).json()
    assert tani["logPath"] == str(veri / "gunluk.log")


# ------------------------------------------------------------------ KM-16


def test_ilk_yonetici_pini_gunluge_yazilmaz(tmp_path: Path, veri: Path) -> None:
    data = deepcopy(load_config().as_dict())
    data["core"] = {**data.get("core", {}), "store_path": str(tmp_path / "km.sqlite"),
                    "secret_key_path": str(tmp_path / "secret.key"),
                    "log_path": str(veri / "gunluk.log")}
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": ""}
    data["files"] = {"output_path": str(veri / "cikti")}
    with structlog.testing.capture_logs() as kayitlar, \
            TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as test_client:
        dosya = veri / BOOTSTRAP_PIN_FILE
        assert dosya.is_file()
        assert stat.S_IMODE(dosya.stat().st_mode) == 0o600
        pin = re.search(r"PIN: (\d{6})", dosya.read_text(encoding="utf-8"))
        assert pin is not None
        cevap = test_client.post("/api/auth/login", json={"password": pin.group(1)})
        assert cevap.status_code == 200, cevap.text
    olay = [kayit for kayit in kayitlar if "İLK YÖNETİCİ" in str(kayit.get("event"))]
    assert olay and olay[0]["path"] == str(dosya)
    assert all(pin.group(1) not in repr(kayit) for kayit in kayitlar), "PIN günlüğe düştü"


# ------------------------------------------------------------------ KM-17


def test_jeton_maskesi() -> None:
    assert masked_token("abcdef0123456789") == "abcdef…"
    assert masked_token("abc") == "…"
    assert masked_token("") == "" and masked_token(None) == ""


LOG_CALL = re.compile(r"_?log\.(?:debug|info|warning|error|exception)\((.*?)\)\n", re.DOTALL)


@pytest.mark.parametrize("module", ["store_ai", "store_notifications", "store_trial_club"])
def test_magaza_gunlugune_tam_jeton_yazilmaz(module: str) -> None:
    for path in (ROOT / "modules" / module / "backend").rglob("*.py"):
        for call in LOG_CALL.findall(path.read_text(encoding="utf-8")):
            for argument in re.findall(r"\btoken=([^,)]+)", call):
                assert argument.strip().startswith("masked_token("), f"{path.name}: token={argument}"
