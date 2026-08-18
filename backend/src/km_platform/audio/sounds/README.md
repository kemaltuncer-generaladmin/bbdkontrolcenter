# Hazır zil sesleri — ÜRÜNLE BİRLİKTE GELİR

Bu klasördeki `.wav` dosyaları ürünün kendi varlıklarıdır ve **imaja girer**
(`deploy/server/Dockerfile` → `COPY backend`). Kullanıcı verisi değildirler.

## Neden burada, `data/sounds` altında değil

`data/` git'te izlenmiyor (`.gitignore`) ve sunucu imajına kopyalanmıyor; orası
kullanıcı verisinin yeri (yüklenen sesler, Vertex'in ürettiği anonslar) ve
kalıcı diske bağlanıyor. Hazır sesler oraya konduğunda **sunucuda hiç ses
bulunmuyordu**: `AudioPlayer.resolve()` `None` dönüyor, `BellService._bell_items()`
boş liste veriyor ve tetikleyici hiçbir şey çalmadan dönüyordu — zil sessizce
hiç çalmıyordu ve belirti hiçbir ekranda görünmüyordu.

`deploy/README.md` bunu "sesleri elle kopyalayın, yoksa ZİL ÇALMAZ" diye
uyarıyordu. Ürünün kendi varsayılan sesinin elle kopyalanmaya bağlı olması
doğru değil: atlanabilen her adım bir gün atlanır ve bu adım atlandı.

## Öncelik

`AudioPlayer` iki yere birden bakar ve **veri dizini kazanır**: kullanıcı
`classic_electric.wav` adıyla kendi sesini yüklerse onun sesi çalar. Buradaki
dosyalar yalnızca yedeği, yani "hiç ses yok" durumunu ortadan kaldırıyor.

`tenefus.wav` BİLEREK BURADA DEĞİL: 3,5 MB ve kullanıcının kendi kaydı; imajı
şişirmesinin gerekçesi yok. Kalıcı diskte durmaya devam eder.
