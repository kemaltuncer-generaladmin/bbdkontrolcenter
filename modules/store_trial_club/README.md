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

## Mağaza ucu bekleyenler

Şu iki iş `store.api` içinde **henüz yok**; ekran düğmeyi kapalı gösterir ve
nedenini yazar (sessiz 404 yok):

- **Katılımcı ekleme** — `bbd_add_trial_member`
  (`POST /api/admin/bbd/trial-club/exams/{id}/members`)
- **Kayıt iptali** — `bbd_update_trial_member`
  (`PUT /api/admin/bbd/trial-club/members/{id}`). Silme değil, pasifleştirme.

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
