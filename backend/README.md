# backend/

Python 3 + FastAPI çekirdeği. Üç paket, tek yönlü bağımlılık:

| Paket | Nedir | Kim import eder |
|---|---|---|
| `src/km_core/` | Kernel: keşif, yükleme, registry, olay yolu, HTTP, ayar, güvenlik. İş bilgisi taşımaz. | yalnızca `km_sdk` |
| `src/km_platform/` | Paylaşılan yetenekler: ssh, database (bbd/bld), printer, audio, scheduler, secrets, notify. **Modül değildir, silinemez.** | yalnızca `km_sdk` |
| `src/km_sdk/` | Modüllerin görebildiği TEK kararlı yüzey. Çekirdeği modülden yalıtır. | modüller |

`km_core` ve `km_platform` içinde modül adı geçemez (K1).
`km_core/store` çekirdeğin kendi metadata deposudur; yönetilen uzak
veritabanları `km_platform/database` sorumluluğundadır — karıştırılmaz.

## Yazılmış olan

```
src/km_platform/notify/          bildirim katmanı — ÇALIŞIYOR
├── contracts.py                 SmsProvider protokolü, SmsMessage, SmsResult
├── errors.py                    sağlayıcıdan bağımsız hata tipleri
├── text.py                      numara normalleştirme, GSM-7/UCS-2 parça hesabı
└── providers/netgsm/            Netgsm uygulaması (SDK sarmalayıcı)
    ├── adapter.py
    └── codes.py                 hata kodları, üç tarih biçimi, İYS, saat dilimi
```

Kullanımı: [../docs/sms-guide.md](../docs/sms-guide.md) ·
Tasarımı: [ADR 0010](../docs/adr/0010-sms-saglayici-entegrasyonu.md)

Geri kalan klasörler sözleşmesi sabitlenmiş iskeletlerdir; kod yazılmadı.

## Bu dosyada duran ayar

`pyproject.toml` bağımlılık ilanını ve mypy ayarını taşır. pytest ve ruff
yapılandırmaları **depo kökündedir** (`pytest.ini`, `ruff.toml`) — araçlar
kökten çalıştırılır ve `modules/` ile `tests/` dizinlerini de kapsar.
