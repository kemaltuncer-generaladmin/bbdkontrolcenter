# Kontrol Merkezi

Ubuntu üzerinde çalışan, tam modüler kurum kontrol merkezi. Sunuculara SSH ile
bağlanır, BBD (Bagisto çekirdekli) ve BLD (Laravel tabanlı) veritabanlarını
yönetir, kurum zilini çalar, yazıcıları denetler, sistemi virüse karşı tarar,
SMS ile uyarır.

## Durum

Mimari kararlar sabitlendi, ortam kuruldu, ilk katman yazıldı.

| Alan | Durum |
|---|---|
| Mimari kararlar (10 ADR), kural seti K1–K11 | Sabit |
| Kimlik, rol ve izin sözleşmesi | Sabit — kod bekliyor |
| Geliştirme ortamı (`.venv`, sistem paketleri) | Kurulu ve doğrulandı |
| `km_platform/notify` — SMS/bildirim katmanı | **Çalışıyor**, 36 test |
| `km_core`, `km_sdk`, diğer platform yetenekleri | İskelet — sözleşmesi sabit, kod yok |
| `bell` — Zil Sistemi | **Çalışıyor**, 77 test · Vertex anonsu + Windows zil ajanı |
| Modüller (`print`, `antivirus`) | İskelet, `enabled: false` |
| Masaüstü kabuk (Tauri) | İskelet |

## Başlarken

```bash
scripts/install-deps.sh          # bağımlılıklar
.venv/bin/python -m pytest       # testler
```

Sıfırdan kurulum ve sorun giderme: [docs/setup-guide.md](docs/setup-guide.md)

## Belgeler

### Kılavuzlar
| Belge | İçerik |
|---|---|
| [setup-guide.md](docs/setup-guide.md) | Sıfırdan kurulum, doğrulama, bilinen sorunlar |
| [development-guide.md](docs/development-guide.md) | Günlük komutlar, test/lint, bağımlılık ekleme |
| [module-guide.md](docs/module-guide.md) | Yeni modül nasıl yazılır |
| [sms-guide.md](docs/sms-guide.md) | SMS gönderimi, hata yönetimi, maliyet hesabı |

### Sözleşmeler
| Belge | İçerik |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Bağlayıcı mimari — katmanlar, K1–K11, modül sözleşmesi |
| [CLAUDE.md](CLAUDE.md) | Çalışma kuralları ve kararların özeti |
| [permissions.md](docs/permissions.md) | İzin kataloğu, rol → izin ve rol → ekran matrisi |
| [identity-model.md](docs/identity-model.md) | Kullanıcı veri modeli, PIN girişi, `identity` yeteneği |
| [module.schema.json](docs/schemas/module.schema.json) | Modül manifest şeması |

### Kararlar ve araştırma
| Belge | İçerik |
|---|---|
| [docs/adr/](docs/adr/) | 10 karar, gerekçeleri ve elenen alternatifleriyle |
| [netgsm-integration.md](docs/netgsm-integration.md) | Netgsm SDK bulguları ve tuzakları |

## Yapı

```
backend/src/km_core/       çekirdek — keşif, yükleme, registry, olay yolu, HTTP
backend/src/km_platform/   paylaşılan yetenekler — ssh, database(bbd/bld),
                           printer, audio, scheduler, secrets, notify
backend/src/km_sdk/        modüllerin görebildiği tek kararlı yüzey
modules/                   iş modülleri — silinebilir özellikler
apps/desktop/              Tauri 2 kabuğu + Python sidecar
config/                    katmanlı ayar (sırlar git dışı)
deploy/                    systemd birimleri, paketleme
docs/                      kılavuzlar, sözleşmeler, kararlar
scripts/                   kurulum ve işletim betikleri
tests/                     çekirdek ve entegrasyon testleri
tools/module-template/     yeni modül şablonu
```

Kök dosyalar: `pytest.ini` ve `ruff.toml` — araçlar depo kökünden çalıştırılır.

## Temel ayrım

**Modül** silinebilir bir iş özelliğidir (zil sistemi, baskı yönetimi, BLD ürün
yönetimi). Klasörünü silmek özelliği tümüyle kaldırır.

**Platform yeteneği** silinemez altyapıdır — `ssh` ve `database` buraya girer:
SSH gereken her şey oraya bağlanır, veritabanı erişimi oradan geçer. Kimlik ve
yetkilendirme de çekirdeğe aittir.

Bu ayrım [ADR 0006](docs/adr/0006-ssh-ve-database-platform-yetenegidir.md)'da
sabitlenmiştir.
