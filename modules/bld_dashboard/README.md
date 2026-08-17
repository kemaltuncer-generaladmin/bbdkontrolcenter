# Kontrol Paneli

BLD işletmesinin açılış ekranı: bugünün satışı, bekleyen işler ve canlı
sipariş akışı. Grup **BLD** · sıra **500** (grubun ilki, kabuk açılışta bunu
seçer).

Sözleşme: [`BLD/docs/control/dashboard.md`](../../../BLD/docs/control/dashboard.md)
(tek uç) + `00-genel.md` · Geçit: [`bld_api`](../bld_api/README.md)

## Ekranda ne var

**Bugünün satışı** — sipariş sayısı, aktif sipariş, satılan porsiyon, ciro,
teslim edilen, geciken (`kpiRow`); aktif siparişlerin durum dağılımı ve
abonelik/serbest satış kırılımı (`stackedBar`); stok doluluk oranı
(`progress`); kesim saatine kalan süre (`statusLine`, dakikada bir tazelenir).

**Bekleyen işler** — sunucunun ürettiği en çok 12 madde. Her satır ilgili
ekrana `ctx.open` ile atlar.

**Canlı sipariş akışı** — son siparişler (`timeline`); yeni gelen satır ve
durum değişikliği yazıyla işaretlenir.

## Üç karar

**1 · Sayı burada hesaplanmaz.** `active`, `fill_rate`,
`seconds_to_next_cutoff` ve bekleyen işlerin tamamı sunucuda üretilir.
İstemcide toplansaydı "kaç sipariş aktif" sorusunun cevabı panel sürümüne göre
değişirdi ve iki ekran aynı soruya farklı cevap verirdi.

**2 · Cümle burada kurulmaz.** Bekleyen iş satırlarının `title`/`detail`
metni sunucudan gelir ve olduğu gibi basılır. Aynı durumu iki ekranda iki
farklı cümleyle anlatmak, sahada telefonda konuşan iki kişinin farklı şey
söylemesidir.

**3 · Yazma ucu yok.** Sözleşme bu alanı salt okunur ilan ediyor: BLD'ye giden
tek bir yazma çağrısı yoktur, dolayısıyla `dry_run` taşıyan bir çağrı da
yoktur ve modül hiçbir olay yayınlamaz. Ekrandan yapılan tek yazma yereldir —
kullanıcının kendi görüntüleme tercihi.

## Uçlar

| Metot | Yol | İzin | Ağa çıkar |
|---|---|---|---|
| GET | `/api/bld_dashboard/overview` | `bld_dashboard.view` | hayır — ekran sözleşmesi + tercih |
| GET | `/api/bld_dashboard/summary` | `bld_dashboard.view` | evet — yoklanan uç |
| PUT | `/api/bld_dashboard/prefs` | `bld_dashboard.manage` | hayır — yalnız yerel tercih |

`PUT /prefs` neden `manage` istiyor: bu modülde ayrışacak bir iş yazması yok.
İkisini de `view`e bağlamak, `manage`i hiçbir kapıyı açmayan bir anahtar
yapardı; rol matrisinde söz veren ama karşılığı olmayan bir satır, olmayan bir
izinden kötüdür. (`bld_orders` aynı tercihi `view` altında tutuyor ve orada
doğrusu o: o modülde üç gerçek yazma ucu var.)

## Geçit yüzeyi — iki metot

| Metot | Ne için | Düşerse |
|---|---|---|
| `dashboard_overview(location_id, date)` | yedi blok | ekran ayakta, kutular "bilinmiyor" |
| `order_list(page, per_page)` | canlı akış satırları | yalnız akış kutusu boşalır |

İkinci çağrı neden var: sözleşmenin gösterge ucu **sayaç** döndürüyor,
**satır** değil (gövdede tek bir sipariş numarası yok). Akış kutusu satır ister
ve onları uydurmanın yolu yok. Yük hesabı tutuyor — 30 saniyede iki istek
saatte 240 çağrıdır, paylaşılan `bld-control-panel` kovası 3000/saat/IP.
Kutu `flow_enabled: false` ile kapatılabilir; kapandığında ekran tek çağrıyla
çalışır ve kutu "akış kapatıldı" der, **"sipariş yok" demez.**

## Yerel tablo

`mod_bld_dashboard_prefs` — yoklama aralığı, işletme seçimi, akış satır
sayısı. **Denetim tablosu yoktur:** denetlenecek bir yazma yok ve 30 saniyede
bir yoklanan bir ekranın her okumasını denetim izine yazmak, izi tamamen bu
trafiğe boğardı (`dashboard.md` → "okumalar denetlenmez").

## Bilinen sınır

`pending_tasks[].link` sözleşmede bir **yol** (`/menu/days/2026-08-17`), kabuk
ise **panel kimliği** ile geziniyor (`ctx.open(id, payload)`) ve kabuk modül
adı bilmez (K1). Çeviri `backend/dashboard.py` → `PANEL_ROUTES` içinde ve
**yolun ilk parçasına** bakar, koda değil: `menu_missing` de `menu_draft` de
`/menu/...` ile başlar. Tanınmayan bir önek gelirse satır **düğmesiz** durur ve
ham yol yazıyla gösterilir — hiçbir yere gitmeyen bir düğme, bozuk bir
düğmedir.

## Test

```bash
.venv/bin/python -m pytest modules/bld_dashboard    # 71 test
.venv/bin/ruff check .
```

`test_bld_dashboard_panel.py` panelin **kaynak** sözleşmesini sınar (kit
kuralları, temizlik, geri sayımın tabanı). Depoda JS koşucusu yok;
`store_dashboard` aynı yolu izliyor.
