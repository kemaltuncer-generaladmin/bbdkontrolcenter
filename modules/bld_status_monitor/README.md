# Durum Monitörü

Mobil uygulama, web sitesi, mutfak kasaları ve sunucu sağlığının izlenmesi;
hata kaydı ve düzeltme defteri. "Bir şey çalışmıyor" şikâyeti geldiğinde
bakılacak ilk ekran.

Grup: **BLD** · İzinler: `bld_status_monitor.view`, `bld_status_monitor.manage`

Sözleşme: [`BLD/docs/control/monitor.md`](../../../BLD/docs/control/monitor.md)
(beş uç) + `00-genel.md`. Geçit metotları
[`bld_api/README.md`](../bld_api/README.md)'deki donmuş tablodan alınır.

---

## Ne yapar

| Sekme | İçerik |
|---|---|
| Durum | Dört sağlık kutusu (`kpiRow`), sunucunun hükmü, her araştırma için bir `statusLine`, açık olay ve kasa sayaçları |
| Hatalar | Sunucudaki hata olaylarının süzülen, sayfalanan listesi (`filterBar` + `dataTable`); satıra tıklayınca bağlam çekmecesi ve gerekçeli çözüm |
| Kasalar | Kasa sağlık tablosu — üç durumlu alanlar korunur |
| Geçmiş | Olay geçmişi (`timeline`) + bu ekranın **yerel** araştırma defteri |
| Düzeltme defteri | Yazılı düzeltme adımları ve defterden komut gönderme |
| Ayar ve iz | Yoklama tercihi ve yerel yazma izi |

## Bu modül uzak veriden türemiş veri saklar — ve haklı

On iki kardeş BLD modülünün hiçbiri uzak veriyi kopyalamıyor. Bu modül
kopyalıyor. Gerekçe tek cümlelik: gereksinim **"en ufak hata bile loglanıp
orada kalacak"** diyor ve **uzak taraf böyle bir geçmiş tutmuyor.**

`veykemtu_monitor_events` yalnız **bileşenlerin yazabildiği** hatayı bilir.
Geçidin kopması, imzanın reddedilmesi, ucun henüz dağıtılmamış olması ve
sunucunun hiç cevap vermemesi **sunucuya hiç ulaşmaz** — yani tam olarak
izlemek istediğimiz arıza, uzak defterde görünmez olandır.

Saklananın sınırı keskin: **yalnız araştırma sonucu ve hata.** Sipariş,
müşteri, stok, abonelik ve fatura buraya yazılmaz; bu modülün tablolarında tek
bir kuruş, tek bir telefon numarası bulunmaz.

## Ne yapmaz

- **Sağlık hükmünü yeniden hesaplamaz.** `ok` / `degraded` / `down` sunucunun
  tek cümlelik hükmüdür. Üç ayrı ekranın (izleme · gösterge paneli · KDS
  yönetimi) aynı duruma bakıp farklı renk göstermesi, hangisine inanılacağını
  belirsiz kılardı. Ekran hükmü okur, `reasons` etiketlerini Türkçeleştirir ve
  **tanımadığı etiketi gizlemez.**
- **Kasa yönetmez.** Eşleme, ayar, cihaz düzenleme `bld_kds` ekranındadır.
  Buradan yalnız defterde tanımlı **düzeltme** komutları gider.
- **Kabuk komutu çalıştırmaz.** Her komut `bld.api` geçidinden geçer (K4).
- **Kayıt silmez.** Çözülen olay işaretlenir, defter kaydı pasifleştirilir.
- **Uzak olay listesini kopyalamaz.** Yerel defter uzak defterin aynası değil,
  **onun göremediği şeyin** kaydıdır.

## Uçlar

| Metot | Yol | İzin |
|---|---|---|
| GET | `/overview` | `bld_status_monitor.view` |
| GET | `/summary` | `bld_status_monitor.view` |
| GET | `/devices` | `bld_status_monitor.view` |
| GET | `/events` | `bld_status_monitor.view` |
| GET | `/events/{id}` | `bld_status_monitor.view` |
| GET | `/log` | `bld_status_monitor.view` |
| GET | `/history` | `bld_status_monitor.view` |
| GET | `/audit` | `bld_status_monitor.view` |
| GET | `/runbook` | `bld_status_monitor.view` |
| POST | `/events/{id}/resolve` | `bld_status_monitor.manage` |
| PUT | `/runbook/{key}` | `bld_status_monitor.manage` |
| POST | `/runbook/{key}/run` | `bld_status_monitor.manage` |
| PUT | `/prefs` | `bld_status_monitor.view` |

`/overview` **ağa çıkmaz**: kutu tanımları, seviye sözlüğü, kanal listesi ve
gerekçe sınırları yereldir — böylece geçit düşükken de ekran kurulabilir (K7).
İzleme ekranının, izlediği sistem düştüğü için açılmaması sorunun kendisini
görünmez yapardı.

**Panel tek uç yoklar.** `GET /summary` gövdesi `devices` bloğunu zaten
taşıyor; kutuları çizmek için ayrıca `/devices` çağırmak yoklama başına ikinci
bir istek demekti ve `00-genel.md` §2'deki bütçe bu ekran için 60 saniyede bir
tek istek varsayıyor.

## Bilinmesi gerekenler

- **"Bilinmiyor" ile "durdu" ayrı şeydir.** İlki "soramadım", ikincisi
  "sordum, kötü". `unknown` sözleşmede yoktur ve sunucudan hiç gelmez; Kontrol
  Merkezi soruyu soramadığında kendi koyar.
- **Uç henüz yayında olmayabilir.** Sunucu tarafı paralel yazılıyor;
  `control_endpoint_missing` **beklenen** bir durumdur ve sarı gösterilir,
  kırmızı değil. Ekran zarifçe bozulur: yerel defter ve düzeltme defteri
  çalışmaya devam eder.
- **`info` varsayılan süzgeçte gizlidir** ve gizlendiği ekranda yazar.
- **Üç durumlu alanlar korunur.** `printer_ok` / `sound_ok` / `alarm_muted`
  `null` olabilir; sağlık bildirmemiş bir kasa **arızalı sayılmaz**.
- **Yerel defter parmak izine göre birleşir.** Kural sözleşmedekinin
  aynısıdır (`sha256(source|code|device|normalize(message))`, sayılar `<n>`,
  UUID'ler `<id>`): `occurrence_count` artar, `first_seen_at` **hiç
  değişmez**. Aynı kuralın seçilmesi bilinçli — iki geçmişi yan yana koyan
  kişi, aynı hatanın iki defterde farklı bölünmüş olmasıyla uğraşmasın.
- **Düzeltme eylemi kapalı listededir.** Defter satırı bir veritabanı kaydıdır;
  oradan okunan bir adı `getattr(api, name)` ile çağırmak, deftere yazabilen
  birine geçidin bütün metotlarını açardı (`cancel_order`, `void_invoice`,
  `run_sms_announcement` dâhil). Liste `backend/monitor.py::RUNBOOK_ACTIONS`.
- **`reprint` ve `unpair` defterde yoktur.** İlki olaya özel bir sipariş
  kimliği ister (bir tanıma yazılamaz), ikincisi bir düzeltme değil kasayı
  sahada yeni kod girilene kadar sipariş göremez hâle getirmektir.
- **Her yazma açık `dry_run=` geçirir.** Geçidin varsayılanına güvenilmez:
  `config/local.yaml` git dışıdır ve orada `dry_run_default: true` yazıyor
  olabilir.

## Tablolar

| Tablo | İçerik |
|---|---|
| `mod_bld_status_monitor_events` | Araştırma sonuçları ve **sunucuya ulaşmayan** hatalar. Parmak izine göre birleşir, satır silinmez. |
| `mod_bld_status_monitor_runbook` | Düzeltme adımlarının tanımları. Silinmez, pasifleştirilir. |
| `mod_bld_status_monitor_audit` | Bu ekrandan yapılan yazma **denemeleri** — "ne denendi" ve "ne oldu" iki ayrı satır (ADR 0012). |
| `mod_bld_status_monitor_prefs` | Ekran tercihi. BLD'yi etkilemez. |

## Sözleşmede eksik görülenler

Bunlar uydurulmadı; olduğu gibi bildirilir:

1. **`bld_monitor.view` / `bld_monitor.manage` anahtarları kullanılmadı.**
   `00-genel.md` §10 izleme alanını bu adlarla listeliyor ama bir modül kendi
   kimliği dışında izin **tanımlayamaz** (`module.schema.json`). Karşılıkları
   `bld_status_monitor.view` / `.manage`.
2. **`meta.open_counts` yalnız liste ucundan geliyor**, `summary` ucundaki
   `events.open` ile aynı bilgiyi taşıyor. Panel özet ucunu kullanıyor; ikisini
   birden okumak ikinci bir istek ederdi.
3. **Kritik olayın hangi bileşende olduğu bilinmiyor.** `critical_open` bütün
   için tek sayı, `by_source` ise seviye ayırmıyor. Ekran yalnız **tek**
   kaynağın açık olayı varsa kritiği ona atfeder; birden çok kaynakta olay
   varken tahmin etmez — yanlış ekibi sahaya göndermek pahalıdır.
4. **`ssh` platform yeteneği yok.** `km_platform/ssh/` bugün boş bir iskelet.
   Kabuk erişimi gerektiren düzeltme adımları deftere `channel: manual` ile
   yazılabilir ama **çalıştırılamaz**; düğme kapalı çizilir ve nedeni üstünde
   yazar. Yetenek yazıldığında yeni bir kanal eklenerek açılır.

## Testler

```bash
.venv/bin/python -m pytest modules/bld_status_monitor -q
.venv/bin/ruff check modules/bld_status_monitor
```

Testler ağa çıkmaz: sahte geçit, sahte depo ve sahte olay yolu kullanılır. Üç
davranış sabitlenmiştir ve değiştirilmesi bilinçli bir karar gerektirir:

- **aynı gözlem ikinci satır açmaz** ve `first_seen_at` hiç değişmez,
- **sunucuya ulaşmayan hata yerel deftere düşer** (`kind="fault"`,
  `source="kontrol_merkezi"`),
- **her uç izin ilan eder** ve rota dosyasındaki servis metotları serviste
  gerçekten vardır (rota ↔ denetleyici ayrışması yalnız uç çağrılınca patlar).
