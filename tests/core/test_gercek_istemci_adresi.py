"""Gerçek istemci adresi — güvenilir vekil zinciri ve Cloudflare (ADR 0030).

Çözücü (`km_core.http.client_address.resolve`) saf işlevdir; burada bütün
sahteleme yolları tek tek sınanır. HTTP kapısındaki karşılığı (servis anahtarı
ağ kısıtı, başarısız deneme kovası) `test_servis_anahtari.py`, giriş hız
sınırı aşağıdadır.
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
from km_core.http.client_address import (
    CLOUDFLARE_RANGES,
    DEFAULT_TRUSTED_PROXIES,
    ProxyTrust,
    parse_address,
    resolve,
)
from km_core.security.rate_limit import RateLimiter

TRAEFIK = "10.0.1.5"
CF_EDGE = "162.158.12.34"
CF_EDGE_V6 = "2606:4700:10::ac43:1"
AKADEMI = "89.252.177.76"
SALDIRGAN = "198.51.100.66"

TRUST = ProxyTrust.build()


# ------------------------------------------------------------------ çözücü


@pytest.mark.parametrize(("peer", "xff", "cf", "beklenen"), [
    # Cloudflare → Traefik → uygulama (Traefik gelen XFF'yi siler, kenarı yazar).
    (TRAEFIK, CF_EDGE, AKADEMI, AKADEMI),
    (TRAEFIK, CF_EDGE_V6, AKADEMI, AKADEMI),
    # Traefik Cloudflare'e güveniyorsa liste uzar; sonuç aynı.
    (TRAEFIK, f"{AKADEMI}, {CF_EDGE}", AKADEMI, AKADEMI),
    # Cloudflare yok, doğrudan Traefik: en sağ durak istemci.
    (TRAEFIK, AKADEMI, None, AKADEMI),
    # Uygulama doğrudan Cloudflare'den bağlantı alıyorsa (Traefik yok).
    (CF_EDGE, None, AKADEMI, AKADEMI),
    # Yerel istek (sidecar, sağlık denetimi): başlıksız, bağlantının adresi.
    ("127.0.0.1", None, None, "127.0.0.1"),
    (TRAEFIK, None, None, TRAEFIK),
    # İki yerel vekil art arda.
    (TRAEFIK, f"{AKADEMI}, 172.18.0.3", None, AKADEMI),
    # Port ve köşeli parantez biçimleri.
    (TRAEFIK, f"{CF_EDGE}:443", f"{AKADEMI}:5555", AKADEMI),
    (TRAEFIK, "[2001:db8::7]:443", None, "2001:db8::7"),
    # IPv4 eşlemeli IPv6 bağlantı adresi.
    ("::ffff:10.0.1.5", AKADEMI, None, AKADEMI),
])
def test_dogru_yol(peer: str, xff: str | None, cf: str | None, beklenen: str) -> None:
    assert resolve(peer, xff, cf, TRUST) == beklenen


@pytest.mark.parametrize(("peer", "xff", "cf", "beklenen"), [
    # Cloudflare atlanarak doğrudan sunucuya: Traefik saldırganın GERÇEK
    # adresini yazar; uydurulan CF-Connecting-IP okunmaz.
    (TRAEFIK, SALDIRGAN, AKADEMI, SALDIRGAN),
    # Saldırgan XFF'nin başına izinli adresi yazar (Traefik ekleyerek iletir).
    (TRAEFIK, f"{AKADEMI}, {SALDIRGAN}", AKADEMI, SALDIRGAN),
    # Saldırgan bir Cloudflare adresini XFF'ye yazar: en sağdaki gerçek durak
    # saldırgandır, yürüyüş orada durur.
    (TRAEFIK, f"{CF_EDGE}, {SALDIRGAN}", AKADEMI, SALDIRGAN),
    # Güvenilmeyen bağlantı: hiçbir başlık okunmaz.
    (SALDIRGAN, CF_EDGE, AKADEMI, SALDIRGAN),
    (SALDIRGAN, AKADEMI, None, SALDIRGAN),
    # Cloudflare üzerinden gelen saldırgan CF-Connecting-IP uyduramaz
    # (Cloudflare ezer); XFF'ye yazdığı da okunmaz.
    (TRAEFIK, f"{AKADEMI}, {SALDIRGAN}, {CF_EDGE}", SALDIRGAN, SALDIRGAN),
    # Bozuk durak: bilinmeyen (ağ kısıtı reddeder).
    (TRAEFIK, "bozuk", AKADEMI, ""),
    (TRAEFIK, f"{AKADEMI}, x" * 3, None, ""),
    # Cloudflare durağında bozuk CF-Connecting-IP: XFF'de sola yürünür.
    (TRAEFIK, f"{SALDIRGAN}, {CF_EDGE}", "uydurma", SALDIRGAN),
])
def test_sahteleme(peer: str, xff: str | None, cf: str | None, beklenen: str) -> None:
    assert resolve(peer, xff, cf, TRUST) == beklenen


def test_uzun_liste_sinirli_yurunur() -> None:
    zincir = ", ".join(["10.0.0.9"] * 40)
    # Bütün duraklar yerel: en çok MAX_HOPS yürünür, sonuncusu döner.
    assert resolve(TRAEFIK, zincir, None, TRUST) == "10.0.0.9"


def test_ip_olmayan_baglanti_basliklari_okumaz() -> None:
    assert resolve("testclient", AKADEMI, AKADEMI, TRUST) == "testclient"
    assert resolve(None, AKADEMI, AKADEMI, TRUST) == ""


def test_cloudflare_kapatilabilir() -> None:
    trust = ProxyTrust.build(cloudflare_trusted=False)
    assert trust.cloudflare == ()
    # Cloudflare tanınmıyorsa kenar sunucusu istemci sayılır.
    assert resolve(TRAEFIK, CF_EDGE, AKADEMI, trust) == CF_EDGE


def test_yerel_vekil_listesi_daraltilabilir() -> None:
    trust = ProxyTrust.build(trusted_proxies=["10.0.1.0/24"])
    assert resolve(TRAEFIK, CF_EDGE, AKADEMI, trust) == AKADEMI
    assert resolve("10.9.9.9", CF_EDGE, AKADEMI, trust) == "10.9.9.9"
    hic = ProxyTrust.build(trusted_proxies=[])
    assert resolve(TRAEFIK, CF_EDGE, AKADEMI, hic) == TRAEFIK


def test_cloudflare_listesi_ezilebilir() -> None:
    trust = ProxyTrust.build(cloudflare_ranges=["192.0.2.0/24"])
    assert resolve(TRAEFIK, "192.0.2.10", AKADEMI, trust) == AKADEMI
    assert resolve(TRAEFIK, CF_EDGE, AKADEMI, trust) == CF_EDGE


def test_gecersiz_ag_acilista_hata() -> None:
    with pytest.raises(ValueError, match="geçersiz ağ"):
        ProxyTrust.build(trusted_proxies=["10.0.0.0/33"])


def test_yerlesik_listeler_gecerli() -> None:
    trust = ProxyTrust.build()
    assert len(trust.cloudflare) == len(CLOUDFLARE_RANGES)
    assert len(trust.local) == len(DEFAULT_TRUSTED_PROXIES)
    # Akademi'nin çıkış adresi hiçbir güvenilir listede değildir.
    akademi = parse_address(AKADEMI)
    assert akademi is not None and not trust.is_trusted(akademi)


def test_ayardan_okunur() -> None:
    class Ayar:
        def __init__(self, data: dict[str, Any]) -> None:
            self.data = data

        def get(self, path: str, default: Any = None) -> Any:
            return self.data.get(path, default)

    # Ortam değişkeninden gelen virgüllü metin de okunur.
    trust = ProxyTrust.from_config(Ayar({"server.trusted_proxies": "10.0.1.0/24, 127.0.0.1/32",
                                         "server.cloudflare.trusted": "false"}))
    assert [str(n) for n in trust.local] == ["10.0.1.0/24", "127.0.0.1/32"]
    assert trust.cloudflare == ()
    varsayilan = ProxyTrust.from_config(Ayar({}))
    assert len(varsayilan.cloudflare) == len(CLOUDFLARE_RANGES)
    assert ProxyTrust.from_config(None) == ProxyTrust.build()


def test_paket_ayari_varsayilanla_ayni() -> None:
    config = load_config()
    trust = ProxyTrust.from_config(config)
    assert trust == ProxyTrust.build()


# ------------------------------------------------------------ giriş hız sınırı


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
        "log_path": str(tmp_path / "gunluk.log"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": "593174"}
    data["files"] = {"output_path": str(tmp_path / "cikti")}
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False,
                    client=(TRAEFIK, 50000)) as c:
        yield c


def test_giris_hiz_siniri_gercek_adrese_gore(client: TestClient) -> None:
    """Cloudflare arkasında herkes aynı kenardan gelir; kova gerçek adresindir."""
    client.app.state.login_limiter = RateLimiter(  # type: ignore[attr-defined]
        source_limit=2, source_window=60.0, global_limit=100, global_window=60.0)
    via_cf = {"X-Forwarded-For": CF_EDGE, "CF-Connecting-IP": SALDIRGAN}
    for pin in ("000001", "000002"):
        assert client.post("/api/auth/login", json={"password": pin},
                           headers=via_cf).status_code == 401
    assert client.post("/api/auth/login", json={"password": "000003"},
                       headers=via_cf).status_code == 429
    # Saldırgan her denemede başka bir "ilk değer" yazarak kovadan kaçamaz.
    kacak = {"X-Forwarded-For": f"203.0.113.7, {CF_EDGE}", "CF-Connecting-IP": SALDIRGAN}
    assert client.post("/api/auth/login", json={"password": "000004"},
                       headers=kacak).status_code == 429
    # Aynı Cloudflare kenarından gelen başka kişi etkilenmez.
    baska = {"X-Forwarded-For": CF_EDGE, "CF-Connecting-IP": "203.0.113.20"}
    assert client.post("/api/auth/login", json={"password": "593174"},
                       headers=baska).status_code == 200
