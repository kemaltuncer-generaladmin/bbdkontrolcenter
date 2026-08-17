# scripts/

| Betik | Ne yapar |
|---|---|
| `install-deps.sh` | Bağımlılıkları üç kaynaktan toplayıp kurar: `backend/pyproject.toml`, `deploy/packaging/system-packages.yaml` ve her modülün `module.yaml` içindeki `dependencies` bloğu |
| `launch-desktop.sh` | Masaüstü kısayolunun çağırdığı tek tıklık başlatıcı: menü kaydını üretir, arayüz değiştiyse kabuğu yeniden derler, sahipsiz çekirdeği indirir, pencereyi açar ve çekirdeğin ayağa kalktığını doğrular |
| `reconcile-permissions.py` | Manifestlerdeki `default_roles` ile `role_permissions` tablosunu karşılaştırır: manifesti daraltmak kurulu bir sistemde tek başına yetmez, çünkü izin tohumlama yalnızca ekler. Silinen modülden kalan yetim izin satırlarını da bulur — onların manifesti hiç yoktur, dolayısıyla karşılaştırılacak tarafları da yoktur |
| `push-roster.py` | Bu makinenin yerel kadrosunu merkezî kimlik servisine taşır (ADR 0021). Merkez kadro biriktikten sonra kuruldu; taşınmazsa eşlenen ikinci cihazda kimse kendi PIN'iyle giremez. **PIN'ler korunur:** düz PIN değil, `password_hash` + `secret_lookup` gider. Veritabanını SALT OKUNUR açar, tek seferliktir ve merkezde yalnız ekler |
| `push-secrets.py` | Bu makinenin **iş sırlarını ve geçit ayarlarını** merkeze yükler (ADR 0025); merkez de eşlenmiş her kuruluma dağıtır. `config/local.yaml` git dışıdır ve pakete girmez, kasadaki sırlar da makinede doğup orada kalır — bu yüzden eşlenen bir Mac'te kimlik çalışıyor ama BLD/BBD/mağaza geçitleri çalışmıyordu. **Gönderilecek anahtarlar açık listedir**; `identity_sync.*` ve `core.pin_pepper` hiçbir koşulda gönderilmez. Veritabanını SALT OKUNUR açar, **değerleri ekrana yazmaz** |

```bash
scripts/install-deps.sh                 # çekirdek + modül bağımlılıkları
scripts/install-deps.sh --dry-run       # hiçbir şey kurma, listeyi yaz
scripts/install-deps.sh --with-desktop  # Tauri derleme bağımlılıklarını da kur

scripts/launch-desktop.sh               # uygulamayı başlat (kısayol bunu çağırır)

scripts/reconcile-permissions.py        # izin sapması raporu (hiçbir şeyi değiştirmez)
scripts/reconcile-permissions.py --uygula   # yalnız açık onayla siler

scripts/push-roster.py                  # kuru prova: ne gönderileceğini yazar (varsayılan)
export KM_IDENTITY_ADMIN_TOKEN=...      # yönetim token'ı — depoya YAZILMAZ (K8)
scripts/push-roster.py --uygula         # kadroyu gerçekten merkeze gönderir

scripts/push-secrets.py                 # kuru prova: hangi sır/ayar gideceğini yazar
scripts/push-secrets.py --yalniz-ayar   # sır gönderme, yalnız modül ayarlarını yükle
scripts/push-secrets.py --uygula        # sırları ve ayarları gerçekten gönderir
```

`reconcile-permissions.py` otomatik budama yapmaz; raporlar, kararı insan
verir. `--uygula` iki silinebilir kutuyu iki AYRI onaya bağlar (`UYGULA` ve
`SIL`) ve etkileşimli terminal ister — zamanlayıcıya bağlanamaz. **Bir modül
silindikten sonra bir kez koşulur:** izin satırları modülle birlikte gitmez.
Gerekçe: [../docs/permissions.md](../docs/permissions.md) → "Manifest'i
daraltmak tek başına yetmez" ve "Silinen modülün izinleri kalır".

`push-roster.py` **varsayılan olarak hiçbir şey göndermez**: ne göndereceğini
yazar ve durur. Gerçek gönderim açık `--uygula` ister, token yoksa hiç başlamaz.
Kurulumun bootstrap yöneticisi (`created_by` boş olan satır) gönderilmez —
merkezin kendi yöneticisi zaten vardır ve ikisini birden taşımak kadroda aynı
adı taşıyan iki yönetici bırakırdı; gerekirse `--bootstrap-dahil` ile gönderilir.
Merkez adresi `--merkez`, `KM_IDENTITY_URL` ya da `config/local.yaml` →
`platform.identity_sync.base_url` sırasıyla aranır.

`push-secrets.py` de **varsayılan olarak hiçbir şey göndermez** ve kuru provada
tek bir ağ isteği yapmaz. **Değerler ekrana yazılmaz** — anahtar adı, uzunluk ve
sha256'nın ilk 8 hanesi yazılır; terminale düşen bir sır oradan kayıtlara ve
ekran görüntülerine düşer. Merkez tarafında `KM_IDENTITY_VAULT_KEY` tanımlı
olmalıdır; yoksa uç 503 döner ve servis düz metin yazmaz (ADR 0025 §2). Çıkış
kodu 1, bazı anahtarların bulunamadığını söyler — insan kararı bekler.

Başlatıcı ne yaptığını `data/launcher.log` dosyasına yazar; açılışta bir sorun
çıkarsa terminal olmadığı için hatayı pencerede gösterir.

Sistem paketleri için `sudo` sorar. Kurulum kılavuzu:
[../docs/setup-guide.md](../docs/setup-guide.md)
