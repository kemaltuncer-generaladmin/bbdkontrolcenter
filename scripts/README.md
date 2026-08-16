# scripts/

| Betik | Ne yapar |
|---|---|
| `install-deps.sh` | Bağımlılıkları üç kaynaktan toplayıp kurar: `backend/pyproject.toml`, `deploy/packaging/system-packages.yaml` ve her modülün `module.yaml` içindeki `dependencies` bloğu |
| `launch-desktop.sh` | Masaüstü kısayolunun çağırdığı tek tıklık başlatıcı: menü kaydını üretir, arayüz değiştiyse kabuğu yeniden derler, sahipsiz çekirdeği indirir, pencereyi açar ve çekirdeğin ayağa kalktığını doğrular |
| `reconcile-permissions.py` | Manifestlerdeki `default_roles` ile `role_permissions` tablosunu karşılaştırır: manifesti daraltmak kurulu bir sistemde tek başına yetmez, çünkü izin tohumlama yalnızca ekler |

```bash
scripts/install-deps.sh                 # çekirdek + modül bağımlılıkları
scripts/install-deps.sh --dry-run       # hiçbir şey kurma, listeyi yaz
scripts/install-deps.sh --with-desktop  # Tauri derleme bağımlılıklarını da kur

scripts/launch-desktop.sh               # uygulamayı başlat (kısayol bunu çağırır)

scripts/reconcile-permissions.py        # izin sapması raporu (hiçbir şeyi değiştirmez)
scripts/reconcile-permissions.py --uygula   # yalnız açık onayla daraltmayı yürürlüğe koyar
```

`reconcile-permissions.py` otomatik budama yapmaz; raporlar, kararı insan
verir. Gerekçe: [../docs/permissions.md](../docs/permissions.md) →
"Manifest'i daraltmak tek başına yetmez".

Başlatıcı ne yaptığını `data/launcher.log` dosyasına yazar; açılışta bir sorun
çıkarsa terminal olmadığı için hatayı pencerede gösterir.

Sistem paketleri için `sudo` sorar. Kurulum kılavuzu:
[../docs/setup-guide.md](../docs/setup-guide.md)
