# 0005 — Modül sınırı: dikey dilim

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
Modül, katmana göre mi (tüm API'ler bir yerde, tüm servisler başka yerde) yoksa
özelliğe göre mi bölünecek?

## Karar
Modül bir **iş özelliğidir** ve kendi dikey dilimini tümüyle taşır: backend,
arayüz paneli, göçler, ayar şeması, çeviriler, testler.

Örnekler: "BLD ürün yönetimi", "zil sistemi", "baskı yönetimi".
Karşı örnek: "ssh", "database" — bunlar özellik değil altyapıdır (bkz. 0006).

## Gerekçe
- Ölçüt nettir: **klasörü sil, özellik tümüyle gitsin.** Katmana göre bölünmüş
  bir yapıda bu imkânsızdır; her kaldırma işlemi beş ayrı yerde iz bırakır.
- Bir özellik üzerinde çalışan kişi tek klasörde kalır.
- Modülün arayüzü de kendisine aitse, kabuk modülleri tanımak zorunda kalmaz.

## Sonuçlar
- Her modül kendi göçlerini yönetir ve yalnızca kendi tablolarına yazar.
- Bir miktar tekrar kabul edilir; paylaşılan şey gerçekten paylaşılıyorsa
  platform yeteneğine yükseltilir (0006).
- Modüller arası doğrudan tablo okuma yasaktır.
