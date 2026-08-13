# tests/

```bash
.venv/bin/python -m pytest              # tümü
.venv/bin/python -m pytest tests/core   # yalnızca çekirdek/platform
.venv/bin/python -m pytest -k netgsm    # ada göre süzme
```

Yapılandırma depo kökündeki `pytest.ini` dosyasındadır; `testpaths` hem
`tests` hem `modules` dizinini kapsar.

| Klasör | İçerik |
|---|---|
| `core/` | Çekirdek ve platform testleri |
| `integration/` | Modül yükleme, yetenek çözümleme, uçtan uca akışlar |
| `fixtures/` | Paylaşılan test verisi |

**Modül testleri modülün kendi `tests/` klasöründedir** (ADR 0005). Buraya
modüle özel test yazılmaz.

**Testler ağa çıkmaz.** Dış servisler taklit edilir; gerçek SMS gönderen veya
gerçek sunucuya bağlanan test yazılmaz.

Burada ayrıca mimari kuralların otomatik denetimi durur: import yönü (K1–K3),
tek kapı (K4), tablo izolasyonu (K5). Kurallar yorumla değil kapıyla korunur.

## Mevcut kapsam

`core/test_notify_sms.py` — 36 test: numara normalleştirme, GSM-7/UCS-2 parça
hesabı, üç tarih biçimi, saat dilimi çevrimi, hata kodu eşlemesi, kuru çalışma,
gövdedeki hata kodunun yakalanması.
