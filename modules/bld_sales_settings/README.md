# Satış Ayarları

Satışı açan, kapatan ve kurallarını belirleyen tek ekran. Sipariş şalteri,
sabah kesim saati, ileri gün sınırı, minimum sepet tutarı, teslimat ücreti,
ödeme yöntemleri, yoğunluk anahtarı, süre alanları, kapalı günler ve günün
hızlı stok tavanları.

**Siparişin mutfağa düştüğü an bir ayar değildir**, kesim saatinden türetilir
(`docs/control/settings.md` → "Sipariş mutfağa ne zaman düşer"). Ayrı bir
`subscription_release_time` anahtarı vardı ve 17.08.2026'da kaldırıldı; ekran
cevabı kesim saati alanının yanındaki ipucu kutusunda YAZAR.

Grup: **BLD** · Sözleşme: `BLD/docs/control/settings.md` (+ stok şeridi için
`menu.md` → `GET|PUT /days/{date}/stock`) · Geçit: `bld_api` (K4).

## İzinler — üç anahtar

| Anahtar | Ne yapar |
|---|---|
| `bld_sales_settings.view` | Okuma: ayarlar, kapalı günler, stok, yerel denetim izi |
| `bld_sales_settings.manage` | Satış **kuralları** + hızlı stok tavanları + ekran tercihi |
| `bld_sales_settings.ordering` | Satış kanalının **açık/kapalı** olması: durdur/aç, kapalı gün ekle/kaldır |

`manage` satış açıkken geçerli kuralları değiştirir; yanlış girilen bir kesim
saati bir dakikada düzeltilir. `ordering` yetkisinin hatası geri alınamaz:
unutulan bir durdurma o günün siparişlerini kaybettirir, yanlış eklenen bir
kapalı gün abonelik üretimini atlatır, kaldırılan bir tatil mutfağı resmî
tatilde üretime sokar.

`busy` (yoğunluk) anahtarı bilerek `manage` altındadır — satışı **kesmez**,
yalnız müşteriye uyarı gösterir.

## Uçlar

| Metot | Yol | İzin |
|---|---|---|
| GET | `/sales` | `view` |
| GET | `/closed-days` | `view` |
| GET | `/stock` | `view` |
| GET | `/audit` | `view` |
| GET | `/prefs` | `view` |
| PUT | `/sales` | `manage` |
| PUT | `/stock/{date}` | `manage` |
| POST | `/prefs` | `manage` |
| POST | `/ordering/pause` | `ordering` |
| POST | `/ordering/resume` | `ordering` |
| POST | `/closed-days` | `ordering` |
| DELETE | `/closed-days/{date}` | `ordering` |

## Kuru prova bu ekranın en tehlikeli hatasıdır

Sessizce no-op olan bir ayar yazması **başarıdan ayırt edilemez**: ekran
"kaydedildi" der, denetim izine satır düşer, `location_options` tablosunda
hiçbir şey değişmez ve yönetici bunu ancak ertesi sabah kesim saatinin eski
değerde olduğunu görünce anlar. Üç kapı var:

1. **Bayrak iki değerli.** Gövdede `preview: bool = False`. `bld_kds`'teki
   `dryRun: bool|None` + modül ayarı kalıbı **kullanılmadı**: `None` dalı,
   ayarın (ya da git dışı `config/local.yaml`'ın) yanlış olduğu bir kurulumda
   her yazmayı sessizce provaya çevirirdi. Modül ayarında `dry_run_default`
   **yoktur ve olmayacaktır**.
2. **Geçide her çağrıda açık `dry_run=` geçilir.** Geçidin varsayılanına asla
   güvenilmez (`bld_api/README.md`).
3. **Yanıt doğrulanır.** `_verify()` yanıttaki `dry_run` bayrağını istenenle
   karşılaştırır; uyuşmuyorsa işlem başarısız sayılır ve neden yazılır. Ayrıca
   gerçek yazmada sunucu "hiçbir alan değişmedi" derken biz gerçek bir fark
   göndermişsek, yanıt `warning` taşır.

Önizleme **ayrı ve açık bir eylemdir**: kullanıcı "Önizle" düğmesine basar,
ekran eski/yeni değer tablosunu (`would`) gösterir ve hiçbir şeyin yazılmadığını
yazar.

## Yoğunluk yarışı

`bld_busy` anahtarını **mutfak ekranı da** değiştiriyor. Yönetici formu
09:00'da açar, mutfak 09:10'da yoğunluğu açar, yönetici 09:30'da kaydeder ve
yarım saat önceki hâli geri yazar. İki savunma var:

- **Kısmi yazma.** Panel yalnız kirli alanları gönderir; dokunulmamış bir
  anahtar gövdeye hiç girmez.
- **Taban çizgisi.** `GET /sales` bir jeton döndürür ve formun açıldığı andaki
  12 alanı yerelde saklar. Yazmada servis taze okuma yapar ve **yazılan
  alanların** taban çizgisinden farkını arar; fark varsa yazma yapılmaz ve
  hangi alanın kim tarafından değiştirildiği ekranda yazar.

Yazılmayan alanlar karşılaştırılmaz — mutfağın değiştirdiği bir anahtar
yüzünden kesim saati kaydını reddetmek ekranı kullanılamaz yapardı.

Panel arka planda `GET /sales?baseline=false` ile yokluyor: bu okuma **yeni
jeton üretmez**. Üretseydi, yarım saattir açık duran formun tabanı sessizce
"şu an" hâline çekilir ve yarış denetimi tam da engellemek için var olduğu
şeyi kaçırırdı. Yoklama formu da ellemez; yalnız farkı uyarı olarak yazar.

## Yerel tablolar

Uzak veri **kopyalanmaz**. `mod_bld_sales_settings_*` üç şey içindir: yazma
denemesinin izi (`audit`, satır silinmez), formun açıldığı andaki taban çizgisi
(`baseline`) ve ekran tercihi (`prefs`).

## Doğrulama

```
cd "Kontrol Merkezi" && .venv/bin/python -m pytest modules/bld_sales_settings
.venv/bin/ruff check .
node --check modules/bld_sales_settings/ui/panel/index.js
```
