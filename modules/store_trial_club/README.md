# Deneme Kulübü

BBD Store deneme sınavı kulübü: denemeler, kontenjan, katılımcılar, kurumdan
gelen sonuç dosyasının yüklenmesi, yoklama çizelgesi ve sonuç karnesi.

Grup: **BBD Store** · CSS öneki: `tc` · Rapor rafı:
`Raporlar/Mağaza/Müşteri/<yıl>/<ay>`

## Ekran

KPI şeridi (deneme · kaydı açık · yaklaşan · kontenjan · dolu · katılımcı ·
sonucu bekleyen) ve dört sekme:

| Sekme | Ne yapar |
|---|---|
| **Denemeler** | Ad · tarih · **kontenjan çubuğu (dolu/toplam)** · ücret · kayıt penceresi · sonuç durumu. Satır → çekmece: Deneme · Kontenjan · Bildirim · Geçmiş. |
| **Katılımcılar** | Sunucu tarafı sayfalanmış liste; deneme, ödeme, katılım, şehir, sınıf süzgeçleri. Satır → künye ve eylemler. |
| **Sonuçlar** | Deneme seçilir; **CSV yükle → eşleştirme önizlemesi → gerekçeli onay**; sonuçları yayınla; yoklama çizelgesi, karne ve sonuç listesi PDF; CSV. |
| **Ayarlar** | Yerel tercihler (kontenjan uyarı eşiği, yoklama boş satırı, varsayılan bildirim kanalı/şablonu), sınırlar ve **kapalı özelliklerin nedeni**. |

## Bilinçli kararlar

**Yüklemek ≠ yayınlamak.** Yüklenen sonuç katılımcıya görünmez; yayınlama ayrı
izin (`store_trial_club.publish`), ayrı onay ve ayrı uyarı ister. Yayın
`store.trial.results_published` olayını yayınlar.

**Eşleştirme önce, yazma sonra.** CSV satırları katılımcılarla kayıt no →
e-posta → telefon → ad sırasıyla eşlenir. Aynı ada sahip iki katılımcı varsa
satır *belirsiz* işaretlenir ve **hiçbiri seçilmez**; ikinci kez denk gelen
satır *mükerrer* sayılır. Yalnız `matched` satırlar mağazaya gider. Önizleme
yerel tabloya jetonla yazılır; uygulama o jetonu okur ve **yeniden
eşleştirmez** — kullanıcı neyi onayladıysa o yazılır. Yazma başarılı olup
jeton "uygulandı" diye işaretlenemezse uç **hata döndürmez**: sonuçlar
mağazaya çoktan yazıldı, 500 gören kullanıcı aynı jetonla yeniden dener ve
satırlar ikinci kez yazılırdı. Bunun yerine `warning` alanı döner ve ekran
kırmızı uyarı gösterir.

**Kontenjan kayıtlının altına çekilemez.** Kabul edilseydi fazla kayıtlar
asılı kalır, kimin sınava gireceği belirsizleşir ve yoklama çizelgesi
kontenjandan uzun çıkardı. Kural hem `capacity` ucunda hem deneme kaydetme
ucunda uygulanır.

**İki bildirim yolu.** Toplu bildirim `store.api.bbd_send_notification` ile
**tek istekte** gider (yüz kişiye tek tek çağrı, dakikada 55 istekli hız
kovasını tüketirdi) ve alıcı listesi gövdede açıkça taşınır: önizlenen kitle
ile gönderilen kitle aynıdır. Tek kişilik hatırlatma `store.notify.send`
yeteneğinden geçer; yetenek yoksa o düğme gizlenir.

**Yoklama çizelgesinde telefon/e-posta basılmaz.** Kâğıt sınıfta elden ele
dolaşır; iletişim bilgisi orada işe yaramaz ama kişisel veriyi kâğıda çıkarır.
Ekranda görünür, kâğıtta görünmez.

## Kapalı katılımcı eylemleri

İki düğme kapalı; ekran nedenini yazar (sessiz 404 yok). **Nedenleri aynı
değil** — biri mağazayı, diğeri geçidi bekliyor:

| İş | Mağaza ucu | Geçit (`store.api`) | Bekleyen |
|---|---|---|---|
| **Katılımcı ekleme** | yok | yok | mağaza tarafı |
| **Kayıt iptali** | **var** (2026-08-16) | yok | geçit metodu |

- **Katılımcı ekleme** — gereken uç `bbd_add_trial_member`
  (`POST /api/admin/bbd/trial-club/exams/{id}/members`). 2026-08-16'da
  sunucuda `route:list --path=api/admin/bbd` ile bakıldı: katılımcı ekleyen
  bir yol yok.
- **Kayıt iptali** — mağaza ucu **yayında**:
  `POST /api/admin/bbd/deneme-kulubu/members/{orderId}/cancel` (gerekçe
  zorunlu, `dryRun` varsayılan açık, sipariş *iptal* durumuna çekilir — satır
  **silinmez**, ADR 0012 ile birebir uyumlu). Bu belge bir dönem ucun
  `PUT /api/admin/bbd/trial-club/members/{id}` olarak *henüz yayınlanmadığını*
  söylüyordu; uç o yolda hiç olmadı, mağaza iptali sipariş kimliği üzerinden
  `deneme-kulubu` önekine yazdı. Eksik olan tek halka `store.api` içindeki
  saran metot (`bbd_cancel_trial_member`); K4 gereği bu modül mağazaya
  doğrudan istek atmadığı için düğme o metot açılana kadar kapalı kalır.

## Yerel tablolar

Yalnız mağazada karşılığı olmayan veri: `mod_store_trial_club_audit`
(gerekçeli denetim izi), `mod_store_trial_club_upload` (sonuç yükleme
önizlemesi + jeton), `mod_store_trial_club_prefs` (ekran tercihleri).

## İzinler

`store_trial_club.view` · `.manage` · `.enroll` · `.publish` (sonuç yükleme ve
yayınlama) · `.notify` (toplu bildirim — **SMS para harcar**).

## Testler

```bash
.venv/bin/python -m pytest modules/store_trial_club/tests -q
.venv/bin/ruff check modules/store_trial_club
```
