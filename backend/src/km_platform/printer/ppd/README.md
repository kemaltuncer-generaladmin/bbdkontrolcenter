# Model PPD dosyaları

Buraya **yalnızca** dağıtımda bulunmayan yazıcı modellerinin PPD dosyaları
konur. PPD küçük bir metin dosyasıdır; sürücünün kendisi değildir.

## Buraya KONMAZ

- `hplip`, `printer-driver-*` gibi sürücü paketleri
- İkili dosyalar, `.deb` paketleri, üretici kurulum betikleri
- CUPS filtreleri, rasterleştiriciler

Bunlar apt üzerinden gelir ve güncellemelerini oradan alır. Depoya kopyalanan
bir sürücü, güvenlik yamalarından kopar ve mimariye bağımlı hale gelir
(K11 — [ADR 0008](../../../../../docs/adr/0008-bagimlilik-yonetimi-ve-surucu-politikasi.md)).

## Mevcut durum

Kurulu yazıcıların hiçbiri için burada PPD tutulmasına gerek yoktur:

| Yazıcı | Sürücü kaynağı |
|---|---|
| HP LaserJet MFP M139-M142 (varsayılan) | `hplip` + `printer-driver-hpcups` (apt) |
| HP LaserJet MFP M141w | `hplip` + `printer-driver-hpcups` (apt) |
| Epson M2170 | dağıtım sürücüsü |
| 80mm termal | genel raster / üretici PPD'si |

80mm termal yazıcı için üretici PPD'si gerekiyorsa, **yalnızca `.ppd` dosyası**
buraya eklenir ve kaynağı bu dosyada belgelenir.
