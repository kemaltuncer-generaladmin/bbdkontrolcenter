"""İsteğin GERÇEK istemci adresi — güvenilir vekil zinciri ve Cloudflare (ADR 0030).

KM sunucuda Cloudflare → Traefik (Coolify) → uvicorn zinciriyle koşar. Uygulama
bağlantıyı Traefik'ten alır (`request.client.host` konteyner ağının adresidir);
Traefik `X-Forwarded-For`a kendi gördüğü adresi, yani bir **Cloudflare kenar
sunucusunu** yazar. ADR 0028'in "en sağdaki X-Forwarded-For" kuralı bu yüzden
her isteği Cloudflare'in adresine bağlıyordu: servis anahtarının `allowed_cidrs`
kısıtı her isteği reddediyor, giriş hız sınırı herkesi birkaç Cloudflare
adresinin kovasına topluyordu.

## Kural

Başlıklara YALNIZ güvenilir bir vekilden gelindiğinde bakılır:

1. Bağlantının adresi (`request.client.host`) güvenilir değilse — yerel vekil
   (`server.trusted_proxies`, varsayılan geri döngü + özel ağlar: Traefik'in
   konteyner ağı) ya da Cloudflare aralığı değilse — **adres odur**, hiçbir
   başlık okunmaz.
2. Güvenilirse `X-Forwarded-For` SAĞDAN SOLA yürünür: güvenilir her durak
   atlanır, güvenilmeyen ilk durak istemcidir.
3. Yürürken bir Cloudflare durağına gelinirse ve `CF-Connecting-IP` geçerli bir
   adresse **o** istemcidir: Cloudflare bu başlığı her istekte kendisi yazar
   (istemcinin gönderdiğini ezer).

Sahteleme: Cloudflare'i atlayıp doğrudan sunucuya gelen biri `CF-Connecting-IP`
ve `X-Forwarded-For` uydurabilir; ama Traefik'in yazdığı en sağdaki durak onun
GERÇEK adresidir, Cloudflare aralığında değildir ve yürüyüş orada durur —
uydurulan başlıklar hiç okunmaz. Geçersiz bir durak (bozuk metin) yürüyüşü
"bilinmeyen" (`""`) ile bitirir: ağ kısıtı bilinmeyen adresi her zaman reddeder.

Cloudflare aralıkları yerleşiktir (`CLOUDFLARE_RANGES`, kaynak
https://www.cloudflare.com/ips/ — 2026-10). Cloudflare listeyi değiştirirse
`server.cloudflare.ranges` ile kod değişmeden ezilir; `server.cloudflare.trusted:
false` Cloudflare'i hiç tanımaz (KM doğrudan yayındaysa).

Çekirdek modül adı bilmez (K1); bu dosya yalnız ağ bilgisi taşır.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: Yerel vekil varsayılanı: geri döngü ve özel ağlar. Coolify'ın Traefik'i
#: (`coolify-proxy`) uygulamaya Docker ağından (10.0.0.0/8 ya da
#: 172.16.0.0/12) bağlanır. Bu adreslerden bağlantı ancak sunucunun içinden
#: kurulabilir.
DEFAULT_TRUSTED_PROXIES: tuple[str, ...] = (
    "127.0.0.0/8", "::1/128",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "fc00::/7",
)

#: Cloudflare kenar sunucuları — https://www.cloudflare.com/ips-v4 ve ips-v6
#: (2026-10). Değişirse `server.cloudflare.ranges` ezer.
CLOUDFLARE_RANGES: tuple[str, ...] = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)

#: `X-Forwarded-For` içinde en çok bu kadar durak yürünür (uzun başlık yağmuru).
MAX_HOPS = 16


def _networks(values: Iterable[Any]) -> tuple[IPNetwork, ...]:
    """CIDR listesi → ağlar. Geçersiz satır AÇILIŞTA hata verir (sessiz güven yok)."""
    out: list[IPNetwork] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        try:
            out.append(ipaddress.ip_network(text, strict=False))
        except ValueError as exc:
            raise ValueError(f"geçersiz ağ (güvenilir vekil listesi): {text!r}") from exc
    return tuple(out)


def parse_address(raw: str | None) -> IPAddress | None:
    """Başlıktaki tek adres: `1.2.3.4`, `1.2.3.4:5678`, `[2001:db8::1]:443`.

    IPv4 eşlemeli IPv6 (`::ffff:10.0.0.2`) IPv4'e indirgenir. Geçersizse None.
    """
    text = str(raw or "").strip().strip('"')
    if not text or len(text) > 64:
        return None
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            return None
        text = text[1:end]
    elif text.count(":") == 1:          # IPv4:port
        text = text.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


@dataclass(frozen=True)
class ProxyTrust:
    """Hangi bağlantılara başlık okutulur."""

    local: tuple[IPNetwork, ...]
    cloudflare: tuple[IPNetwork, ...]

    @classmethod
    def build(cls, *, trusted_proxies: Iterable[Any] | None = None,
              cloudflare_trusted: bool = True,
              cloudflare_ranges: Iterable[Any] | None = None) -> ProxyTrust:
        local = _networks(DEFAULT_TRUSTED_PROXIES if trusted_proxies is None else trusted_proxies)
        ranges = list(cloudflare_ranges or []) or list(CLOUDFLARE_RANGES)
        return cls(local=local, cloudflare=_networks(ranges) if cloudflare_trusted else ())

    @classmethod
    def from_config(cls, config: Any) -> ProxyTrust:
        if config is None:
            return cls.build()

        def get(path: str, default: Any) -> Any:
            try:
                value = config.get(path, default)
            except Exception:  # noqa: BLE001 — bozuk ayar varsayılana düşer
                return default
            return default if value is None else value

        proxies = get("server.trusted_proxies", None)
        if isinstance(proxies, str):
            proxies = proxies.split(",")
        ranges = get("server.cloudflare.ranges", [])
        if isinstance(ranges, str):
            ranges = ranges.split(",")
        trusted = get("server.cloudflare.trusted", True)
        if isinstance(trusted, str):
            trusted = trusted.strip().lower() not in ("0", "false", "no", "hayir", "hayır", "off")
        return cls.build(trusted_proxies=proxies, cloudflare_trusted=bool(trusted),
                         cloudflare_ranges=ranges)

    def is_local(self, address: IPAddress) -> bool:
        return any(address in network for network in self.local)

    def is_cloudflare(self, address: IPAddress) -> bool:
        return any(address in network for network in self.cloudflare)

    def is_trusted(self, address: IPAddress) -> bool:
        return self.is_local(address) or self.is_cloudflare(address)


def resolve(peer: str | None, forwarded_for: str | None, cf_connecting_ip: str | None,
            trust: ProxyTrust) -> str:
    """Gerçek istemci adresi (metin). Belirlenemezse `""`.

    `peer` bağlantının adresi; ötekiler başlıkların ham değeri.
    """
    current = parse_address(peer)
    if current is None:
        # Bağlantının adresi IP değil (test istemcisi, unix soketi): başlıklar
        # güvenilmez; ne geldiyse o.
        return str(peer or "")
    hops = [hop for hop in str(forwarded_for or "").split(",") if hop.strip()][-MAX_HOPS:]
    cf_client = parse_address(cf_connecting_ip) if cf_connecting_ip else None
    while trust.is_trusted(current):
        if cf_client is not None and trust.is_cloudflare(current):
            return str(cf_client)
        if not hops:
            return str(current)
        nxt = parse_address(hops.pop())
        if nxt is None:
            return ""
        current = nxt
    return str(current)


def _trust(request: Request) -> ProxyTrust:
    """Uygulama başına bir kez kurulur (`app.state`); ayar değişince yeniden başlatma."""
    state = request.app.state
    trust: ProxyTrust | None = getattr(state, "proxy_trust", None)
    if trust is None:
        trust = ProxyTrust.from_config(getattr(state, "config", None))
        state.proxy_trust = trust
    return trust


def client_address(request: Request) -> str:
    """İsteğin gerçek istemci adresi — servis anahtarı ağ kısıtı ve bütün hız sınırları.

    İstek başına bir kez hesaplanır (`request.state`). Belirlenemezse `""`.
    """
    cached = getattr(request.state, "km_client_address", None)
    if cached is not None:
        return str(cached)
    client = request.client
    address = resolve(
        client.host if client else None,
        request.headers.get("x-forwarded-for"),
        request.headers.get("cf-connecting-ip"),
        _trust(request),
    )
    request.state.km_client_address = address
    return address
