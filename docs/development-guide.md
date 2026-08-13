# Geliştirme Kılavuzu

Kurulum: [setup-guide.md](setup-guide.md) · Mimari: [../ARCHITECTURE.md](../ARCHITECTURE.md)

---

## Günlük komutlar

Tümü **depo kökünden** çalıştırılır — pytest ve ruff yapılandırmaları kökte
durur (`pytest.ini`, `ruff.toml`).

```bash
.venv/bin/python -m pytest                  # tüm testler
.venv/bin/python -m pytest tests/core -q    # yalnızca çekirdek/platform
.venv/bin/python -m pytest -k netgsm        # ada göre süzme

.venv/bin/ruff check .                      # lint
.venv/bin/ruff check . --fix                # düzeltilebilirleri düzelt
.venv/bin/ruff format .                     # biçimlendirme

.venv/bin/mypy backend/src                  # tip denetimi
```

Sanal ortamı etkinleştirmek isterseniz `source .venv/bin/activate`; yukarıdaki
komutlar buna gerek kalmadan da çalışır.

## Testler nerede durur

| Ne | Nerede |
|---|---|
| Çekirdek ve platform testleri | `tests/core/` |
| Modül yükleme, uçtan uca akışlar | `tests/integration/` |
| Paylaşılan test verisi | `tests/fixtures/` |
| **Modül testleri** | **modülün kendi `modules/<id>/tests/` klasöründe** (ADR 0005) |

`pytest.ini` içindeki `testpaths` hem `tests` hem `modules` dizinini kapsar;
modül testleri ayrıca çağrılmadan çalışır.

Testler ağa çıkmaz. Dış servisler (Netgsm gibi) taklit edilir — gerçek SMS
gönderen bir test yazılmaz.

## Bağımlılık ekleme

Bağımlılık **ilan edilir, depoya kopyalanmaz** (K11). Nereye yazılacağı neye
ait olduğuna bağlıdır:

| Bağımlılık kime ait | Nereye yazılır |
|---|---|
| Çekirdeğe (`km_core`) | `backend/pyproject.toml` → `dependencies` |
| Bir platform yeteneğine | `backend/pyproject.toml` → `optional-dependencies` altında o yeteneğin extra'sı (`ssh`, `database`, `printer`, `audio`, `notify`) |
| Bir modüle | `modules/<id>/module.yaml` → `dependencies.python` |
| Sistem paketi (apt) | `deploy/packaging/system-packages.yaml` ya da modülün `dependencies.system` bloğu |

Sonra `scripts/install-deps.sh` yeniden çalıştırılır. **Modül eklerken
çekirdeğin bağımlılık listesine dokunulmaz** (K6).

## Yeni modül

Adımlar: [module-guide.md](module-guide.md)

Özetle: `tools/module-template/` klasörü `modules/<id>/` olarak kopyalanır,
`module.yaml` doldurulur, `backend/module.py` içine `register(ctx)` yazılır.
Çekirdekte tek satır değişmez.

Manifest'i şemaya karşı doğrulama:

```bash
.venv/bin/python -c "
import yaml, json, jsonschema
m = yaml.safe_load(open('modules/<id>/module.yaml'))
s = json.load(open('docs/schemas/module.schema.json'))
jsonschema.validate(m, s); print('şemaya uygun')
"
```

## Mimari kuralları bozmamak

Kurallar yorumla değil kapıyla korunur. `import-linter` kontratları
`backend/pyproject.toml` içinde tanımlıdır ve kaynak paketler yazıldıkça
etkinleşir.

En sık düşülen üç hata:

- **Modülde `from km_core...` yazmak.** Modüller yalnızca `km_sdk` görür (K2).
- **Modülde ham `asyncssh` / DB sürücüsü çağırmak.** Erişim platform
  yeteneğinden geçer (K4).
- **Kodda rol adı sormak.** `if role == "admin"` yasak; `has_permission(key, scope)`
  sorulur (K10).

## Yazım dili

Belgeler, arayüz metinleri, docstring'ler ve commit mesajları **Türkçe**.
Kod tanımlayıcıları, dosya ve klasör adları, API yolları **İngilizce ve ASCII**.

## Kod yazılmış alanlar

Proje kademeli ilerliyor. Şu an gerçek kod içeren tek yer:

```
backend/src/km_platform/notify/     SMS/bildirim katmanı — çalışır durumda, 36 test
```

Geri kalan klasörler sözleşmesi sabitlenmiş iskeletlerdir. `km_core`, `km_sdk`
ve diğer platform yetenekleri henüz yazılmadı; modüller `enabled: false`.

## Paketleme notu

Proje henüz kurulabilir paket değil. `tests/conftest.py` kaynak dizinini
`sys.path`'e ekler. Kaynak paketler tamamlandığında bu dosya kaldırılacak,
yerine `pip install -e backend` geçecektir.
