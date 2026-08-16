# Faturalar

BLD bilgi belgelerinin listesi, kesilmesi, iptali, A4 baskısı ve yerel arşivi.

Grup: **BLD** · İzinler: `bld_invoices.view`, `bld_invoices.manage`,
`bld_invoices.void` · Sözleşme: `BLD/docs/control/invoices.md`

## Bu belgenin mali değeri yoktur

Yazdırılabilir bir A4 belgedir: resmî fatura değildir, e-Fatura/e-Arşiv
değildir, GİB'e gitmez, vergi hesaplamaz. Ekranda kapatılamaz bir bant,
üretilen her dosyanın **her sayfasında** ise zorunlu dipnot durur:

> Bu belge mali değeri olmayan bilgi amaçlı bir dokümandır; fatura yerine
> geçmez.

Metin iki yerde sabittir — `backend/documents.py` (`NOTICE`) ve
`ui/panel/index.js`. İkilik bilinçli: bant, sunucu düşükken de görünmelidir.

## Belge düzenlenmez

Sözleşmede `PATCH` ve `DELETE` **yoktur**, bu modülde de yoktur ve
`tests/test_bld_invoices_routes.py` bunu router'a karşı doğrular. Yanlış bir
belge **iptal** edilir (`void`) ve yerine yenisi kesilir; iptal edilen numara
seride ölü kalır, boşluk bırakılmaz. Arayüz bu yolu "İptal et ve yenisini kes"
diye adlandırır — "Düzelt" düğmesi yoktur.

## Uçlar

| Metot | Yol | İzin |
|---|---|---|
| GET | `/invoices` | `view` |
| GET | `/invoices/{id}` | `view` |
| GET | `/archive` · `/audit` | `view` |
| POST | `/invoices` | `manage` |
| POST | `/invoices/{id}/void` | **`void`** |
| POST | `/invoices/{id}/html` | `view` |
| POST | `/preview` · `/print` · GET `/printer` | `view` |

Belge kesme **iki adımdır**: panel önce `dryRun: true` ile prova alır (numara
üretmez, kalem sayısı ve toplamı döner), sonucu onaylatır, sonra `dryRun:
false` ile gerçek çağrıyı yapar. **Her iki çağrıda da bayrak açıkça gönderilir**
— geçidin varsayılanı `config/local.yaml` ile değişebilir ve o dosya git
dışıdır.

## Yerel tablolar

Uzak veri **kopyalanmaz**. `mod_bld_invoices_audit` yazma denemesinin izini
(BLD'ye ulaşamayan istekler de dâhil), `mod_bld_invoices_archive` ise
**üretilen dosyanın** künyesini tutar: yol, sha256, boyut, basıldığı an.
Belgenin içeriği yerelde durmaz.

## Baskı

`report.js` → `reportChain`: üret → önizle → CUPS'a bas. Yazıcı yeteneği
**isteğe bağlıdır**; yoksa belge yine üretilir ve rapor klasörüne 0600 ile
yazılır, yalnız baskı düğmesi kapanır (K7). Önizleme görüntüsü `pdftoppm`
ile üretilir; araç yoksa dosya yine yazılır.

## Sunucu tarafı henüz yayında değil

Fatura uçları sonraki fazda yazılıyor. Geçit bugün temiz bir
`control_endpoint_missing` döndürüyor ve ekran bunu "beklenen durum" diye
yazıyor; yerel arşiv sekmesi çalışmaya devam ediyor.

## Doğrulama

```
.venv/bin/python -m pytest modules/bld_invoices
.venv/bin/ruff check .
node --check modules/bld_invoices/ui/panel/index.js
```
