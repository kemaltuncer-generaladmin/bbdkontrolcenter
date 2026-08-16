# SMS Paneli (`bld_sms`)

BLD'nin müşteriye giden SMS metinlerini, tetikleyicilerini, toplu duyurusunu ve
gönderim kaydını yöneten ekran.

Grup: **BLD** · Uç öneki: `/api/bld_sms` ·
İzinler: `bld_sms.view` · `bld_sms.manage` · `bld_sms.announce`

## Bu ekran SMS göndermez

Gönderimi **BLD sunucusu** yapar (`Services\Sms\SmsSender`; üretimde
`NetgsmSmsSender`, sır tanımsızsa `LogSmsSender`). Bu modül yalnız
`/api/control/sms/*` uçlarına, o da `bld.api` geçidinden gider (K4). Netgsm
istemcisi burada **yoktur ve yazılmayacaktır**.

Kontrol Merkezi'nin kendi Netgsm şeridi (`notify` platform yeteneği) ayrı bir
şeydir ve bu ekrandaki gönderimlerde **kullanılmaz**. Panel ikisini ayrı
satırda yazar; birleştirilseydi, BLD'nin sırrı eksikken yönetici Kontrol
Merkezi'nin ayarını düzeltmeye çalışır ve hiçbir şey değişmezdi. Yetenek
`optional`: yoksa ekran tümüyle çalışır (K7).

## Sözleşme

Normatif kaynak `BLD/docs/control/sms.md` + `BLD/docs/control/00-genel.md`.
Şablon anahtarları, değişken adları, kitle değerleri ve segment kuralı **orada
sabittir**; buradan hiçbir alan uydurulmaz.

Geçit metotları `modules/bld_api/README.md` §10'daki donmuş tablodan alınır:
`sms_templates` · `update_sms_template` · `preview_sms_template` ·
`send_test_sms` · `sms_log` · `sms_announcement` · `set_sms_announcement` ·
`run_sms_announcement`.

## Uç noktalar

| Metot | Yol | İzin | Ne yapar |
|---|---|---|---|
| GET | `/templates` | `view` | Şablonlar + öbekler + yerel politika + sağlayıcı durumu |
| GET | `/log` | `view` | Gönderim kaydı (sayfalı, süzgeçli) |
| GET | `/announcement` | `view` | Duyuru taslağı, alıcı tahmini, bekleyen kuru prova |
| GET | `/history` | `view` | Bu ekrandan yapılan yazma **denemeleri** (yerel iz) |
| POST | `/measure` | `view` | **Yerel** karakter/segment sayacı ve önizleme — ağa çıkmaz |
| POST | `/templates/{key}/preview` | `view` | **Sunucu** önizlemesi; SMS göndermez, gerekçe ister |
| PATCH | `/templates/{key}` | `manage` | Metin ve/veya durum (kısmi) |
| POST | `/send-test` | `manage` | Tek numaraya `[DENEME]` SMS'i |
| PUT | `/announcement` | `manage` | Duyuru **taslağı** — göndermez |
| POST | `/announcement/run` | `manage` **+** `announce` | Kuru prova / **gerçek** toplu gönderim |

Son uç iki izni birden kabul eder (`requires` "en az biri"): kuru prova `manage`
ile yapılabilir, **gerçek gönderim `bld_sms.announce` ister** ve servis bunu
ayrıca denetler (çift kapı, K9).

## Toplu gönderimin önündeki beş kapı

Toplu duyuru geri alınamaz, gerçek para harcar ve yanlış metin spam şikâyeti ile
numara kaybı demektir. Kapılar üst üstedir ve her biri ayrı bir teste bağlıdır:

1. **Ayrı izin** — `bld_sms.announce`, varsayılan olarak yalnız `admin`.
2. **Gerekçe** — en az 10 karakter, panelde ve backend'de (ADR 0012; PIN yok).
3. **Zorunlu kuru prova** — istek gerçekten sunucuya gider (`dry_run=true`),
   sunucu ön denetimleri koşar ve "kaç alıcı, hangi işlenmiş gövde" cevabını
   döner. Denetleyicide kısa devre yaptırılmaz.
4. **Tek kullanımlık jeton** — prova bir jeton üretir; jeton bir kez kullanılır,
   ömrü sınırlıdır (`announcement_dry_run_ttl_minutes`) ve **taslak değişince
   düşer**.
5. **Alıcı sayısı eşleşmesi** — jetondaki sayı ile `confirm_recipients` birebir
   aynı olmalı; sunucu ayrıca kendi hesabıyla karşılaştırır (409) ve 10
   dakikalık soğuma penceresi uygular.

Panel bunlara ekranın kendi eşiklerini de koyar: `all_customers` kitlesi ve
büyük gönderimler ayrıca `confirmSimple` ile sorulur.

## Şablonu açmak bilinçli bir harekettir

Açık bir şablon, olay her gerçekleştiğinde müşteriye SMS gönderir. Bu yüzden
açma/kapama `confirmWithReason`'dan geçer ve kararın kendisi yerele kaydedilir
(`mod_bld_sms_triggers`). Sunucudaki `enabled` sütunu "bugün açık" der,
"bilinçli açıldı" demez; ikisi ayrı sorudur ve ikincisinin cevabı yalnız
buradadır. Sunucuda açık ama bu ekrandan hiç açılmamış şablonlar panelde
`onaylanmadı` rozetiyle işaretlenir.

## İki segment ölçüsü — ikisi de doğru

| Ölçü | Kural | Nerede |
|---|---|---|
| **Faturalanan** | GSM-7'ye sığmayan tek karakter → UCS-2, segment **70/67** | Sözleşme, `text._worst_case` |
| Sağlayıcı tahmini | `ğ Ğ ı İ ş Ş` Netgsm Türkçe tablosuyla, 2 septet, **160/153** | `km_sdk.plan_text` |

Aradaki fark **paradır**. Panel ikisini `measureBar` ile yan yana çizer ve
"Faturalanan ölçü bu" işaretini sözleşmenin ölçüsüne koyar — faturayı gönderen
taraf o. Sadeleştirme (`ş` → `s`) önerisi yalnız **kazanç varsa** görünür ve
metni kendiliğinden değiştirmez.

Canlı sayaç `POST /measure` ile **yerelde** hesaplanır: ağa çıkmaz, denetim
satırı yazmaz ve geçidin dakikada 18 istekle sınırlı tek kovasını yakmaz.
Sunucu önizlemesi ayrı bir düğmedir ve gerçeği o söyler.

## Yerel tablolar

Uzak veri **kopyalanmaz**. Dört tablo yalnız BLD'de karşılığı olmayanı tutar:

| Tablo | Ne için |
|---|---|
| `mod_bld_sms_audit` | Yazma **denemesi** izi. `denendi` satırı geçit çağrısından ÖNCE düşer; ağ koparsa geriye yalnız o kalır. |
| `mod_bld_sms_templates` | Temel çizgi: yazdığımız anın **özeti** (sha256), uzunluğu, segmenti. Metnin kendisi yazılmaz; amaç sapma yakalamak. |
| `mod_bld_sms_triggers` | Tetikleyici politikası: kim, ne zaman, hangi gerekçeyle açtı/kapattı. |
| `mod_bld_sms_announcement_dry` | Toplu duyurunun kuru prova jetonu. |

Denetim satırına **metnin tamamı ve açık telefon numarası yazılmaz** (sözleşme
"Denetim eylemleri"); numara maskeli (`532****567`), metin yalnız ölçüsüyle
geçer.

## Doğrulama

```bash
cd "Kontrol Merkezi"
.venv/bin/python -m pytest modules/bld_sms
.venv/bin/ruff check .
node --check modules/bld_sms/ui/panel/index.js
```

Hiçbir test ağa çıkmaz ve **hiçbir test gerçek SMS göndermez**.

## Sözleşmeyle çelişen üç nokta (uydurulmadı, raporlandı)

- **Sipariş şablonu 6 tanedir, 7 değil.** Sözleşmede `order_created`,
  `order_confirmed`, `order_on_the_way`, `order_delivered`, `order_cancelled`,
  `order_revised` var; `hazirlaniyor`/`hazir` durumlarının SMS'i yok.
- **Değişken sözdizimi tek süslü parantezdir** (`{ad}`), çift değil — sözleşme
  "Değişken sözdizimi" başlığı.
- **Zamanlanmış toplu duyuru yoktur.** Sözleşmede duyuru için bir zamanlayıcı
  ucu bulunmuyor; duyuru elle çalıştırılır ve panel bunu açıkça yazar.
