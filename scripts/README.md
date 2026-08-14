# scripts/

| Betik | Ne yapar |
|---|---|
| `install-deps.sh` | Bağımlılıkları üç kaynaktan toplayıp kurar: `backend/pyproject.toml`, `deploy/packaging/system-packages.yaml` ve her modülün `module.yaml` içindeki `dependencies` bloğu |
| `launch-desktop.sh` | Masaüstü kısayolunun çağırdığı tek tıklık başlatıcı: menü kaydını üretir, arayüz değiştiyse kabuğu yeniden derler, sahipsiz çekirdeği indirir, pencereyi açar ve çekirdeğin ayağa kalktığını doğrular |

```bash
scripts/install-deps.sh                 # çekirdek + modül bağımlılıkları
scripts/install-deps.sh --dry-run       # hiçbir şey kurma, listeyi yaz
scripts/install-deps.sh --with-desktop  # Tauri derleme bağımlılıklarını da kur

scripts/launch-desktop.sh               # uygulamayı başlat (kısayol bunu çağırır)
```

Başlatıcı ne yaptığını `data/launcher.log` dosyasına yazar; açılışta bir sorun
çıkarsa terminal olmadığı için hatayı pencerede gösterir.

Sistem paketleri için `sudo` sorar. Kurulum kılavuzu:
[../docs/setup-guide.md](../docs/setup-guide.md)
