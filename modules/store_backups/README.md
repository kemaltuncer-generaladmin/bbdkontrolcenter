# Manuel Yedekleme

Mağaza veritabanı, yüklenen dosyaları ve ayarlarının elle yedeklenmesi,
doğrulanması, indirilmesi ve geri yüklenmesi.

Grup: **BBD Store** · CSS öneki: `bu` · Rapor rafı:
`Raporlar/Mağaza/Denetim/<yıl>/<ay>`
İzinler: `store_backups.view`, `store_backups.manage`, `store_backups.restore`,
`store_backups.delete`

## Depodaki en yıkıcı ekran

Yedek **almak** zararsızdır. Yedek **geri yüklemek** mevcut mağaza verisini
değiştirir ve bu depodaki geri dönülmesi en zor işlemdir. Koruma dört
katmanlıdır ve dördü de backend'de uygulanır — arayüzde gizlemek yetkilendirme
değildir (K9):

1. **Ayrı izin anahtarı** — `store_backups.restore`, yalnız `admin`. Yedek
   almaya yeten `store_backups.manage` geri yüklemeye yetmez.
2. **Gerekçe (≥10 karakter)** — şemada (`Field(min_length=10)`) *ve* serviste
   (`inventory.reason_error`) doğrulanır; istemci şemayı atlatabilir.
3. **`dryRun` varsayılanı** — hem uç gövdesinde hem geçitte varsayılan kuru
   prova.
4. **Geri yüklemeden önce zorunlu güvenlik yedeği** — `restore` önce yeni bir
   yedek aldırır. **Alınamazsa geri yükleme hiç başlamaz.** Bu ekranın
   üretmemesi gereken tek sonuç "geri yükledik ama eski hâle dönemiyoruz"dur.

`destructive: true` **kullanılmaz** (ADR 0012): o bayrak çekirdekte PIN
kapısına bağlanacak, mağaza ekranları PIN istemiyor.

## Ekran

| Bölüm | İçerik |
|---|---|
| Kalıcı uyarı bandı | Kapatılamaz, kaydırmayla kaybolmaz: "Canlı mağaza verisi. Geri yükleme mevcut veriyi değiştirir." |
| Durum bandı | Son yedek ne zaman · yaşı · boyutu · kapsamı · doğrulandı mı — **renk + yazı**, üç hâl: var · yok · **bilinmiyor** |
| Yeni yedek kartı | Kapsam kutuları (veritabanı · yüklenen dosyalar · ayarlar) + not + adımlı ilerleme |
| Disk kartı | Doluluk çubuğu ve "kalan alanla yaklaşık kaç yedek daha alınır" cümlesi |
| Filtre şeridi | Arama · kapsam · tür · doğrulama · tarih aralığı · boyut (MB) aralığı |
| Tablo | Tarih · Tür · Kapsam · Boyut · Süre · Alan kişi · Not · Doğrulama · Yol (kopyala) · Eylemler |
| Satır eylemleri | Doğrula · İndir · **Geri yükle** · Sil *(uç yayında olmadığı için kapalı)*. Dosya adı beyaz listeden geçmeyen satırda **dördü de kapalı** ve nedeni ipucunda |
| İşlem geçmişi sekmesi | Bu ekrandan yapılan her işlem, **gerekçesiyle** (yerel iz) |

## Mağazanın yanıt biçimi

**Yedek uçları çalışmıyor — ama 404 diye değil.** `GET /api/admin/bbd/backups`
bugün (2026-08-13) canlıda **503** dönüyor:

```json
{"error": {"code": "CONTROL_API_DISABLED",
           "message": "Kontrol Merkezi API'si kapalı. Açmak için
                       BBD_CONTROL_API_ENABLED=true yapıp yapılandırma
                       önbelleğini yenileyin."}}
```

Aynı anda `/api/admin/settings/channels` ve `/api/admin/orders` **200** dönüyor:
**mağaza ayakta.** Uç da yayında; kapalı olan bir **anahtar**.

Bu, ekranın gördüğü hâllerin **üçe** çıktığı anlamına gelir
(`endpointState`) — ve üçü de ayrı cümleyle anlatılır, çünkü **çareleri
farklı**:

| Hâl | Nereden anlaşılır | Ne denir | Ne yapılır |
|---|---|---|---|
| `live` | — | normal ekran | — |
| `missing` | geçidin `bbd_endpoint_missing` kodu (404) | "Yedek ucu henüz yayında değil" | beklenir |
| `disabled` | mağazanın `CONTROL_API_DISABLED` jetonu (503) | "Kontrol Merkezi API'si kapalı" | sunucuda `BBD_CONTROL_API_ENABLED=true` |

Geçit 503'e `code="server"` verir, `bbd_endpoint_missing` **vermez**. Yalnız o
koda bakan sürüm bu hâli "gerçek arıza" sayıyordu; sonucu iki ayrı zarardı:
ekran ayakta olan bir mağaza için **"Mağazaya ulaşılamadı"** diyordu ve
**"Yedeği başlat" düğmesi açık kalıyordu** — kullanıcı kapsam seçip gerekçe
yazıp onaylıyor, sonunda 503 görüyordu. İkisi de düzeltildi.

Jeton **metinde** aranır (`inventory.endpoint_state`) ve bu, "metne bakma"
kuralının ihlali değildir: `CONTROL_API_DISABLED` çevrilen bir cümle değil,
mağazanın **makine jetonudur**. Geçit gövdedeki `error` alanını hata metnine
olduğu gibi koyuyor ve `mask_text` yalnız sır *adlarını* yıldızlıyor.

Değişmeyen kurallar:

- Durum satırı **"Mağazaya ulaşılamadı" demez** — mağaza ayakta; çalışmayan bu
  ekranın konuştuğu paket. İkisini aynı cümleyle anlatmak personeli boşuna
  sunucu odasına gönderir.
- "Yedeği başlat" düğmesi **kapatılır** ve nedeni kartın içinde, gözle görünür
  bir kutuda yazar (`title` dokunmatikte ve klavyeyle hiç görünmez).
- Durum bandı **"Hiç yedek alınmamış" demez.** Boş liste ile okunamamış liste
  aynı şey değildir; ikincisinde bilinen tek şey listeyi alamadığımızdır.
- Gerçek 5xx **gizlenmez**: jetonu taşımayan bir 500 "uç kapalı" sayılmaz,
  yoksa çöken bir mağaza "anahtar kapalı, sunucuya gitmeyin" diye anlatılırdı.

Aşağıdakiler `bbdstore.com.tr` üzerinde salt okunarak sınandı (varsayım
değildir) ve yedek ucu yayınlandığında da geçerli olacak:

- **Alan adları camelCase'tir** (`createdAt`, `grandTotal`, `qtyOrdered`).
  `created_at` diye bakan kod hiçbir şey bulamaz ve **istisna da atmaz** —
  ekran sessizce "—" dolu görünür. Bu modülde her okuma `inventory.pick()`
  üzerinden geçer ve **iki yazımı da** çözer.
- **Zaman damgası saat dilimsizdir ve YEREL saattir** (`"2026-08-13 19:54:51"`).
  `inventory.parse_time` saat dilimsiz damgayı **yerel** sayar. UTC saymak
  yedeği üç saat gençleştirir: 50 saatlik bir yedek 47 saatlik görünür ve 48
  saatlik bayatlama eşiği hiç tetiklenmez — durum bandı yeşil kalırken elde
  kalan en yeni yedek çoktan eskimiştir.
- `per_page` sunucuda **50'ye kırpılır**, `meta` camelCase, `links` boş döner.
  Bu ekranın listesi onlarca satır olduğu için **sayfalama yoktur**; süzme
  istemcide yapılır.
- Laravel tanımadığı sorgu parametresini **sessizce yok sayar**; bu yüzden bu
  ekran envanter ucuna hiç süzgeç göndermez ve süzmeyi kendisi yapar.

## Bilinmeyen değer uydurulmaz

Bu ekranın bütün riski hesaplarda, ve her hesabın yanlış tarafa düşme biçimi
farklı:

| Bilinmeyen | Ne yapılır | Neden |
|---|---|---|
| Disk bilgisi gelmedi | "bilinmiyor" yazar, 0 yazmaz | 0 göstermek "disk dolu" demekle aynı |
| Toplam var, boş alan yok | Doluluk çubuğu **çizilmez** | %0'da duran çubuk diski bomboş gösterir |
| Boş alan var, yedek boyutu yok | Eksik olanın **adı** söylenir | Kullanıcı yanlış tarafa bakmasın |
| Doğrulama sonucu yok | `unknown` — "Doğrulandı" **değil** | Hiç doğrulanmamış yedek yeşil görünürse, açılmadığı felaket gününde anlaşılır |
| Tarih okunamadı | Yaş `null`, satır listede **kalır** | Kaybolan satır, olmayan satır gibi görünür |
| Tanımadığımız yedek türü | Ham hâliyle yazılır | "Manuel" demek, rotasyonun bıraktığı yedeği "ben aldım, silebilirim" dedirtir |
| Kaç yedek daha sığar | Son 5 yedeğin **ortalaması** | Tek yedeğe bakmak: kapsamı dar bir yedek "daha 900 sığar" dedirtir |

## Bilerek yapılmayanlar

- **Yedek dosyasını tarayıcıya akıtma.** Panelin `api()` katmanı JSON taşıyor;
  yüz megabaytlık gövdeyi belleğe alıp base64'e çevirmek sidecar'ı da kabuğu da
  düşürürdü. Dosya diske **0600** ile yazılır ve ekran **yolu** söyler.
  `download_limit_mb` (varsayılan 200) üstündeki dosyada indirme hiç başlamaz;
  sunucudaki yol gösterilir. Envanterdeki boyut yanlış olabileceği için
  **gerçek boyut da** indirmeden sonra denetlenir.
- **Yedek silme.** Mağaza tarafında silme ucu yok. Uydurma bir çağrı yazmak
  yerine düğme **kapalı** durur ve nedenini yazar; yaş kapısı
  (`delete_min_age_days`, varsayılan 30) yine de bugünden yazılmış ve test
  edilmiştir — uç yayınlandığı gün kural "sonra ekleriz" borcuna kalmaz.
- **Sahte ilerleme.** Adımlardan "Dışa aktarılıyor" ve "Sıkıştırılıyor" mağaza
  tarafında **tek istek içinde** geçer; aradaki geçişi gözleyemeyiz. Sahte bir
  sayaçla ilerletmek yerine adım **atlanır** — uydurma ilerleme, ilerleme
  değildir. "Doğrulanıyor" gerçekten ayrı bir çağrıdır. Aynı sebeple ilerleme
  çubuğu **işin başladığı yerde** açılır: meşgulken basılan düğme, hiç
  başlamamış bir işi "Dışa aktarılıyor"da asılı bırakıyordu.
- **Zamanlanmış yedek rotasyonuna karışma.** Buradan alınan yedek manueldir.

## Yerel tablo

`mod_store_backups_audit` — yedeklerin **kendisi** mağazadadır ve buraya
kopyalanmaz; kopya envanter, dosya taşındığında sessizce yalan söyler. Burada
yalnız mağazada karşılığı olmayan şey durur: **niyet ve gerekçe.** Bagisto'nun
denetim kaydında gerekçe alanı yok; ayrıca ağ koparsa "ne yapmaya çalıştık"
bilgisi yalnız burada kalır. Geri yükleme yarıda kesildiyse elimizdeki tek
kayıt bu satırdır: önce `denendi`, sonra `ok`/`hata` yazılır — ikincisi hiç
gelmediyse işlem ortada kesilmiştir.

## Uçlar

`GET /backups` · `GET /audit` · `POST /export` · `POST /backups` ·
`POST /backups/verify` · `POST /backups/download` · `POST /backups/restore` ·
`POST /backups/delete`

Yedek adı **gövdede** taşınır, yolda değil: ad bir dosya adıdır ve yolda
taşımak `%2f` / `..` kaçışlarıyla uğraşmak demektir. Gövdedeki değer serviste
beyaz listeyle denetlenir (`inventory.safe_name`).

## Yayımladığı olaylar

`store.backup.created` → `{name, scope, bytes, actor, dryRun}`
`store.backup.restored` → `{name, actor, safetyBackup}`

Kuru provada **yayımlanmaz**. Dinleyen yoksa ya da dinleyici patlarsa iş durmaz
(K7); doğrudan modül çağrısı yoktur (K3).

## Testler

```bash
.venv/bin/python -m pytest modules/store_backups/tests -q
.venv/bin/ruff check modules/store_backups
```

- `test_store_backups_inventory.py` — saf hesaplar (ağ yok, dosya yok, durum
  yok): alan adı iki yazım, saat dilimi, doğrulamanın üç hâli, disk tahmini,
  yaş kapısı, dosya adı beyaz listesi, **ucun üç hâli**.
- `test_store_backups_service.py` — iş kuralları, K7, yıkıcı işlem kapıları,
  "uç yok" · "uç kapalı" · "mağaza çöktü" **üçlü** ayrımı.

Sahtedeki 503 gövdesi (`store_backups_fakes.DISABLED_MESSAGE`) canlıdan
alınmıştır ve geçidin o gövdeden ürettiği metnin aynısıdır; uydurulmuş bir
hata metnine karşı geçen test hiçbir şey kanıtlamazdı.

Ağa çıkılmaz; `store.api` taklit edilir. Sahtede **uydurulmuş metot yoktur** —
gerçek geçitte olmayan bir çağrıyı sahtede yazmak, olmayan bir uca güvenen bir
testi "geçiyor" gösterirdi.
