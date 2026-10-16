# Belgeler

## Kılavuzlar — nasıl yapılır

| Belge | Ne zaman okunur |
|---|---|
| [setup-guide.md](setup-guide.md) | Yeni bir makinede sıfırdan kurarken |
| [development-guide.md](development-guide.md) | Günlük geliştirme: test, lint, bağımlılık ekleme |
| [module-guide.md](module-guide.md) | Yeni bir modül yazarken |
| [sms-guide.md](sms-guide.md) | SMS gönderirken, hata yönetirken |

## Sözleşmeler — bağlayıcı

| Belge | İçerik |
|---|---|
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Katmanlar, K1–K12, modül sözleşmesi, alan servisleri |
| [architecture/domains.yaml](architecture/domains.yaml) | Alan kataloğu (ADR 0033): alanlar, önekler, veri sınıfı üst sınırı |
| [permissions.md](permissions.md) | İzin kataloğu, rol → izin, rol → ekran matrisi |
| [identity-model.md](identity-model.md) | Kullanıcı/rol veri modeli, PIN girişi, `identity` yeteneği |
| [schemas/module.schema.json](schemas/module.schema.json) | Modül manifest şeması |

Bunlar tartışmaya kapalıdır. Değişiklik ancak yeni bir ADR ile olur.

## Kararlar

[adr/](adr/) — kararlar (dizin: [adr/README.md](adr/README.md)), gerekçesi ve elenen alternatifleriyle. Karar
verildikten sonra dosya değiştirilmez; karar değişirse yenisi yazılır ve
eskisi `Superseded` işaretlenir.

## Mimari envanter

| Belge | İçerik |
|---|---|
| [architecture/module-graph.md](architecture/module-graph.md) | ÜRETİLMİŞ: alan haritası, modül tablosu, bağımlılık grafiği, sınır bulguları, kopyalar (`tools/module-graph.py --write`) |
| [architecture/envanter-2026-10-16.md](architecture/envanter-2026-10-16.md) | 16.10.2026 envanterinin yorumu, temizlikler ve bbd_akademi bölünme planı |

## Araştırma notları

| Belge | İçerik |
|---|---|
| [netgsm-integration.md](netgsm-integration.md) | Netgsm SDK kaynak incelemesi, tespit edilen tuzaklar |

Bunlar sözleşme değildir; bir kararın arkasındaki bulguları kaydeder.
