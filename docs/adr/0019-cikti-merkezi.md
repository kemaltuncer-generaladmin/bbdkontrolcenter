# 0019 — Çıktı Merkezi: kayıt, dosyayı yazan tek yerde doğar

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

Yirmiden fazla modül diske PDF ve CSV yazıyor. Hepsi
`km_core/files/outputs.py` hiyerarşisini kullanıyor:

```
Masaüstü/Kontrol Merkezi/Raporlar/Kantin/2026/08 - Ağustos/…
```

Hiyerarşi doğru kurulmuş ama **hiçbir kayıt tutulmuyor.** Sonuçları:

- "Geçen ayın kantin raporunu kim aldı" sorusunun cevabı yok.
- Bir rapor yeniden basılamıyor; yeniden **üretiliyor** — kaynak veriler
  değiştiyse çıkan kâğıt eskisiyle aynı değil.
- Baskı gönderildi mi, kaç kez basıldı, bilinmiyor.

`modules/print` iskeleti tam bunun için yazılmış ve manifestinde şöyle diyor:
*"yazıcı donanımına erişim bu modüle ait değildir — bu modül kuyruk, iş
geçmişi, yetkilendirme ve arayüzü yönetir."* Sözleşme hazır, kod yok.

## Karar

### 1. Kayıt, dosyayı yazan fonksiyonda doğar

Çıktı kaydı `km_core/files` katmanında, dosya diske yazılırken oluşur.
**Yirmi modülün hiçbirinde tek satır değişmez.**

Alternatifi — her modülün "çıktı ürettim" diye ayrıca bildirmesi — yirmi ayrı
çağrı noktası demektir ve biri mutlaka unutulur; unutulduğunda hiçbir test
kırılmaz, çıktı yalnızca listede görünmez. Aynı hata `pdftoppm`'in yirmi
kopyaya dağılmasıyla zaten bir kez yapıldı.

### 2. Tablo çekirdek deposundadır

K5 modülün yalnız kendi tablolarına yazmasını söyler. Yirmi modülün ortak
kaydı hiçbir modülün tablosu olamaz; `km_core/store` içinde durur.

| Alan | Not |
|---|---|
| `id`, `created_at` | |
| `user_id` | üreten kişi |
| `source` | üreten modül kimliği (çekirdek bunu **veri** olarak taşır, dallanmaz — K1) |
| `kind`, `title` | rapor türü ve görünen ad |
| `path`, `bytes`, `pages` | dosya |
| `printed_count`, `last_printed_at` | |
| `params_digest` | hangi tarih aralığı/süzgeçle üretildi |

`source` alanının çekirdekte bulunması K1'i bozmaz: çekirdek modül adına göre
**davranmaz**, yalnız saklar ve gösterir.

### 3. Ekran: `print` modülü genişler, adı "Çıktı Merkezi" olur

Liste + süzme (tarih, tür, üreten, modül) + önizleme + **yeniden bas** + klasörü aç.

Yeniden basma `printer` yeteneğinden geçer, yani ADR 0014'e uyar: Linux'ta
sessizce basar, Windows/macOS'ta sistem yazdırma penceresini açar.

### 4. Dosya kaybolmuşsa satır bunu söyler

Kullanıcı masaüstündeki klasörü temizleyebilir, dosyayı taşıyabilir. Kayıt
silinmez; satır **"dosya bulunamadı"** durumuna geçer ve yeniden bas düğmesi
kapanır, nedeni yazılır.

Kaydı sessizce gizlemek en kötü seçenektir: "ben bu raporu almıştım" diyen
kullanıcı hiçbir iz bulamaz.

### 5. Saklama süresi kayıt içindir, dosya için değil

`keep_job_history_days` (varsayılan 30) **kayıt satırlarını** budar. Uygulama
kullanıcının masaüstündeki dosyalarını silmez — orası kullanıcının alanıdır.

### 6. Görünürlük izinle sınırlanır

Kayıtlar öğrenci adı, veli telefonu ve tutar taşıyan dosyaların **adlarını ve
yollarını** tutar; ad tek başına bilgi taşır (`ogrenci-kartlari-3A.pdf`).
Ekran `outputs.view` izniyle korunur; `outputs.reprint` ayrı izindir.

## Elenen alternatifler

- **Her modül elle bildirir.** Yukarıda: yirmi çağrı noktası, sessiz kayıp.
- **Kuyruğu dosya sistemini tarayarak kurmak.** Meta veri yok (kim, hangi
  süzgeç, kaç kez basıldı), `export_path` ile başka yere yazılmış çıktılar hiç
  görünmez.
- **Kaydı `printer` yeteneğine bağlamak.** O zaman yalnız basılanlar listelenir;
  oysa çıktıların çoğu basılmadan paylaşılıyor.

## Sonuçlar

- `km_core/files/outputs.py` ve `private.py` artık depoya yazan bir bağımlılık
  taşır; yazma başarısız olsa bile **dosya üretimi durmaz** (K7) — kayıt
  düşmezse loglanır, kullanıcı raporunu yine alır.
- `modules/print` `enabled: true` olur ve `module.yaml` adı/izinleri güncellenir.
- Yeniden basmanın Windows/macOS'ta "pencere açıldı" demek olduğu, ADR 0014'ün
  sonucu olarak burada da geçerlidir: `printed_count` **denendi** sayar.
