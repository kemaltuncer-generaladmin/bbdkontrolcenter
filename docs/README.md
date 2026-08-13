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
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | Katmanlar, K1–K11, modül sözleşmesi |
| [permissions.md](permissions.md) | İzin kataloğu, rol → izin, rol → ekran matrisi |
| [identity-model.md](identity-model.md) | Kullanıcı/rol veri modeli, PIN girişi, `identity` yeteneği |
| [schemas/module.schema.json](schemas/module.schema.json) | Modül manifest şeması |

Bunlar tartışmaya kapalıdır. Değişiklik ancak yeni bir ADR ile olur.

## Kararlar

[adr/](adr/) — 10 karar, gerekçesi ve elenen alternatifleriyle. Karar
verildikten sonra dosya değiştirilmez; karar değişirse yenisi yazılır ve
eskisi `Superseded` işaretlenir.

## Araştırma notları

| Belge | İçerik |
|---|---|
| [netgsm-integration.md](netgsm-integration.md) | Netgsm SDK kaynak incelemesi, tespit edilen tuzaklar |

Bunlar sözleşme değildir; bir kararın arkasındaki bulguları kaydeder.
