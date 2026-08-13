# scripts/

| Betik | Ne yapar |
|---|---|
| `install-deps.sh` | Bağımlılıkları üç kaynaktan toplayıp kurar: `backend/pyproject.toml`, `deploy/packaging/system-packages.yaml` ve her modülün `module.yaml` içindeki `dependencies` bloğu |

```bash
scripts/install-deps.sh                 # çekirdek + modül bağımlılıkları
scripts/install-deps.sh --dry-run       # hiçbir şey kurma, listeyi yaz
scripts/install-deps.sh --with-desktop  # Tauri derleme bağımlılıklarını da kur
```

Sistem paketleri için `sudo` sorar. Kurulum kılavuzu:
[../docs/setup-guide.md](../docs/setup-guide.md)
