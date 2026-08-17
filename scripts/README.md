# scripts/

| Betik | Ne yapar |
|---|---|
| `install-deps.sh` | Bağımlılıkları üç kaynaktan toplayıp kurar: `backend/pyproject.toml`, `deploy/packaging/system-packages.yaml` ve her modülün `module.yaml` içindeki `dependencies` bloğu |
| `launch-desktop.sh` | Masaüstü kısayolunun çağırdığı tek tıklık başlatıcı: menü kaydını üretir, arayüz değiştiyse kabuğu yeniden derler, sahipsiz çekirdeği indirir, pencereyi açar ve çekirdeğin ayağa kalktığını doğrular |
| `reconcile-permissions.py` | Manifestlerdeki `default_roles` ile `role_permissions` tablosunu karşılaştırır: manifesti daraltmak kurulu bir sistemde tek başına yetmez, çünkü izin tohumlama yalnızca ekler. Silinen modülden kalan yetim izin satırlarını da bulur — onların manifesti hiç yoktur, dolayısıyla karşılaştırılacak tarafları da yoktur |

```bash
scripts/install-deps.sh                 # çekirdek + modül bağımlılıkları
scripts/install-deps.sh --dry-run       # hiçbir şey kurma, listeyi yaz
scripts/install-deps.sh --with-desktop  # Tauri derleme bağımlılıklarını da kur

scripts/launch-desktop.sh               # uygulamayı başlat (kısayol bunu çağırır)

scripts/reconcile-permissions.py        # izin sapması raporu (hiçbir şeyi değiştirmez)
scripts/reconcile-permissions.py --uygula   # yalnız açık onayla siler
```

`reconcile-permissions.py` otomatik budama yapmaz; raporlar, kararı insan
verir. `--uygula` iki silinebilir kutuyu iki AYRI onaya bağlar (`UYGULA` ve
`SIL`) ve etkileşimli terminal ister — zamanlayıcıya bağlanamaz. **Bir modül
silindikten sonra bir kez koşulur:** izin satırları modülle birlikte gitmez.
Gerekçe: [../docs/permissions.md](../docs/permissions.md) → "Manifest'i
daraltmak tek başına yetmez" ve "Silinen modülün izinleri kalır".

Başlatıcı ne yaptığını `data/launcher.log` dosyasına yazar; açılışta bir sorun
çıkarsa terminal olmadığı için hatayı pencerede gösterir.

Sistem paketleri için `sudo` sorar. Kurulum kılavuzu:
[../docs/setup-guide.md](../docs/setup-guide.md)
