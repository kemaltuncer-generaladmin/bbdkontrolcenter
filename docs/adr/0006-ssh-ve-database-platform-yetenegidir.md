# 0006 — ssh ve database platform yeteneğidir, modül değil

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
İlk taslakta `ssh` ve `database` birer modül olarak konumlanmıştı. Oysa
uygulamada SSH'a ihtiyaç duyan **her şey** oraya bağlanacak; aynı biçimde
BBD (Bagisto çekirdekli) ve BLD (Laravel tabanlı) veritabanı erişimi birden çok
özelliğin ortak zemini.

## Karar
`ssh` ve `database` `km_platform/` altında **paylaşılan yetenek**tir. Modül
değildirler: kaldırılamazlar, `enabled: false` yapılamaz, manifest taşımazlar.
Modüller bunları `km_sdk` üzerinden tüketir.

`database/` içinde `engines/` sürücü sarmalayıcıları, `bbd/` ve `bld/` ise
ilgili şemaların adaptörleridir.

## Gerekçe
- Modül tanımı 0005'te "silinebilir iş özelliği" olarak sabitlendi. SSH
  silinemez — altyapıdır. Onu modül saymak tanımı bozar ve her modülü ona
  bağımlı kılarak modüller arası bağımlılık ağı doğurur.
- Bağlantı havuzu, kimlik yönetimi, denetim izi ve hız sınırı tek yerde
  toplanmalı. Bunlar bir modülün içinde durursa o modül fiilen çekirdek olur.

## Sonuçlar
- **Tek kapı kuralı:** modüllerde ham `asyncssh`/`paramiko` çağrısı veya doğrudan
  DB sürücüsü kullanımı yasaktır; erişim platform yeteneği üzerinden gider.
- Sunucu envanteri ve kimlik bilgileri platformun sorumluluğundadır; sırlar
  `km_platform/secrets` kasasından okunur.
- `km_core/store` (çekirdeğin kendi metadata deposu) ile
  `km_platform/database` (yönetilen uzak veritabanları) ayrı şeylerdir ve
  karıştırılmaz.
