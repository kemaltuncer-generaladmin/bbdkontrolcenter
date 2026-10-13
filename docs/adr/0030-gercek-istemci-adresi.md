# ADR 0030 — Gerçek istemci adresi: güvenilir vekil zinciri ve Cloudflare

**Durum:** Kabul edildi · 13.10.2026
**Etkilediği:** `km_core/http/client_address.py` (yeni), `km_core/http/deps.py`
(`client_ip`), `km_core/http/users.py` (giriş hız sınırı kaynağı),
`config/default.yaml` → `server.trusted_proxies`, `server.cloudflare.*`
**Değiştirdiği:** [ADR 0028](0028-servis-anahtarlari.md) §4 (ağ kısıtında
istemci adresi). 0028'in geri kalanı aynen geçerlidir.

## Bağlam

KM sunucusu Cloudflare → Traefik (Coolify `coolify-proxy`) → uvicorn zinciriyle
yayındadır. ADR 0028 §4 istemci adresini "en sağdaki `X-Forwarded-For`" diye
tanımladı. Bu, önünde yalnız Traefik olan bir kurulumda doğrudur; Cloudflare
araya girince yanlıştır:

- Traefik Cloudflare'e güvenmediği için gelen `X-Forwarded-For`u siler ve
  kendi gördüğü adresi yazar: bir **Cloudflare kenar sunucusu**. En sağdaki
  değer her istekte Cloudflare'dir.
- Servis anahtarının `allowed_cidrs` kısıtı (ör. Akademi sunucusu
  `89.252.177.76/32`) bu yüzden **her isteği reddeder**; kısıtsız anahtar
  vermek tek çıkış olur.
- Giriş hız sınırı ise `X-Forwarded-For`un **ilk** değerine bakıyordu — Cloudflare
  arkasında bu da kenar sunucusudur (bütün kullanıcılar birkaç kovada, 8
  deneme/dk'yı başkaları tüketir) ve istemci tarafından uydurulabilir (her
  denemede başka değer yazan kaynak sınırını atlatır). Servis anahtarının
  başarısız deneme kovası da aynı kenar adresine düşüyordu.

Cloudflare gerçek adresi `CF-Connecting-IP` başlığında taşır; ama başlığı
herkes yazabilir. Cloudflare'i atlayıp sunucuya doğrudan gelen biri
`CF-Connecting-IP: 89.252.177.76` gönderirse kısıt delinir.

## Karar

**1. Tek çözücü.** İstemci adresi tek yerde çözülür:
`km_core/http/client_address.py` → `client_address(request)`. Servis anahtarının
ağ kısıtı, başarısız anahtar kovası ve giriş hız sınırı bunu kullanır. İstek
başına bir kez hesaplanır.

**2. Başlıklar yalnız güvenilir vekilden.** Bağlantının adresi
(`request.client.host`) güvenilir değilse adres odur; hiçbir başlık okunmaz.
Güvenilir = yerel vekil (`server.trusted_proxies`; varsayılan geri döngü +
özel ağlar `10/8`, `172.16/12`, `192.168/16`, `fc00::/7` — Traefik'in konteyner
ağı) ya da Cloudflare aralığı.

**3. Sağdan sola yürüyüş.** Güvenilir bağlantıda `X-Forwarded-For` sağdan sola
yürünür; güvenilir duraklar atlanır, güvenilmeyen ilk durak istemcidir. Bir
**Cloudflare durağına** gelindiğinde `CF-Connecting-IP` geçerli bir adresse
istemci odur (Cloudflare bu başlığı her istekte kendisi yazar, istemcininkini
ezer). En çok 16 durak yürünür; bozuk bir durak "bilinmeyen" (`""`) verir ve
ağ kısıtı bilinmeyeni her zaman reddeder.

**4. Cloudflare aralıkları yerleşik, ayarla ezilebilir.** Liste
`CLOUDFLARE_RANGES` (cloudflare.com/ips, 2026-10) koddadır;
`server.cloudflare.ranges` doluysa onun yerine geçer,
`server.cloudflare.trusted: false` Cloudflare'i hiç tanımaz. Geçersiz CIDR
açılışta hata verir — sessizce "herkese güven"e düşülmez.

**5. uvicorn başlıkları işlemez.** Sunucu imajı uvicorn'u `--no-proxy-headers`
ile başlatır (`deploy/server/Dockerfile`; yerel kip `km_core/main.py` →
`proxy_headers=False`). `request.client.host` her zaman bağlantının gerçek
adresidir (sunucuda Traefik) ve karar yalnız bu çözücüdedir. Gerekçe: uvicorn
başlıkları işlerse ve `FORWARDED_ALLOW_IPS='*'` verilmişse adresi
`X-Forwarded-For`un uydurulabilir İLK değeriyle değiştirir — çözücü o adresi
"vekilsiz bağlantı" sanar ve ağ kısıtı delinirdi. Bugün de uvicorn'un
varsayılanı (yalnız `127.0.0.1`'e güven) Traefik'e güvenmediği için davranış
değişmez; bayrak ortam değişkeniyle açılan bir deliği kapatır.

## Sahteleme — neden güvenli

| Saldırı | Sonuç |
|---|---|
| Cloudflare'i atlayıp doğrudan sunucuya, `CF-Connecting-IP` ve `X-Forwarded-For` uydurur | Traefik'in yazdığı en sağ durak saldırganın gerçek adresidir, Cloudflare aralığında değildir; yürüyüş orada durur, uydurulan başlıklar okunmaz |
| `X-Forwarded-For`un başına izinli adresi yazar | Sağdan yürünür; baştaki değere hiç gelinmez |
| `X-Forwarded-For`a bir Cloudflare adresi yazar | Ondan sağdaki gerçek durak (saldırgan) güvenilmez; orada durulur |
| Cloudflare üzerinden gelip `CF-Connecting-IP` uydurur | Cloudflare başlığı ezer |
| Uygulamaya vekilsiz, doğrudan bağlanır | Bağlantı adresi güvenilmez; başlık okunmaz |

Yerel vekil listesindeki özel ağlardan bağlantı ancak sunucunun içinden
(konteyner ağı) kurulabilir; orada olan zaten sunucuya erişmiştir.

## Sonuçlar

- Akademi'nin servis anahtarı ağ kısıtıyla üretilebilir:
  `scripts/service-key.py create … --cidr 89.252.177.76/32`.
- Giriş hız sınırı ve servis anahtarı başarısız deneme kovası gerçek istemciye
  göre sayılır: Cloudflare kenarını paylaşan kişiler birbirini kilitlemez,
  uydurulan ilk değerle kovadan kaçılamaz.
- Yeni bir ters vekil eklenirse (ör. Traefik önünde başka bir yük dengeleyici)
  adresi `server.trusted_proxies`'e yazılır; yazılmazsa o vekil istemci sanılır
  (kısıtlı anahtar reddedilir — açık değil, kapalı başarısızlık).
- Cloudflare yeni aralık eklerse ve liste güncellenmezse o aralıktan gelen
  istekler kenar sunucusunun adresine düşer (yine kapalı başarısızlık); ayar
  `server.cloudflare.ranges` ile kod değişmeden düzeltilir.
