# 0001 — Çekirdek: Python 3 + FastAPI

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
Kontrol merkezi Ubuntu üzerinde çalışacak; SSH, CUPS, systemd, ses aygıtları ve
MySQL/PostgreSQL ile konuşacak. Modüller çalışma anında keşfedilip yüklenecek.

## Karar
Çekirdek Python 3 ile yazılır, HTTP yüzeyi FastAPI'dir.

## Gerekçe
- Ubuntu sistem entegrasyonu (asyncssh, pycups, dbus, DB sürücüleri) en olgun
  burada; ek köprü katmanı gerekmez.
- `importlib` ile çalışma anında modül yükleme dilin doğal yeteneği — dinamik
  keşif için ek altyapı gerekmiyor.
- Pydantic, manifest ve ayar şeması doğrulamasını tek araçla çözer.
- FastAPI router'ları takılıp çıkarılabilir; modüllerin HTTP yüzeyi eklemesi
  çekirdeğe dokunmadan mümkün.

## Sonuçlar
- Dağıtım Python yorumlayıcısı taşımayı gerektirir; masaüstü kabuğunda sidecar
  olarak paketlenir (bkz. 0002).
- Tip güvenliği zorunlu kılınır: tüm sözleşmeler tip belirtimli, `mypy` kapıda.
- Go'nun tek-binary avantajı feda edilir; karşılığında modül eklemek yeniden
  derleme gerektirmez.

## Değerlendirilen alternatifler
- **Node.js + TypeScript:** tek dil avantajı var, ama CUPS/ses/sistem tarafında
  kabuk komutlarına düşüyor.
- **Go:** modülerlik derleme-zamanı registry'ye bağlanır, bu da 0003 ve 0006 ile
  çelişir.
