# 0002 — Arayüz: Tauri 2 masaüstü kabuğu + Python sidecar

**Durum:** Kabul edildi · 2026-08-12

## Bağlam
Kontrol merkezi kurum içinde bir makinede çalışacak; zil sesi ve yazıcı gibi
yerel donanıma erişimi var. Kullanıcı masaüstü uygulama istedi.

## Karar
Arayüz Tauri 2 kabuğudur. Python çekirdeği kabuğun yönettiği bir sidecar süreç
olarak çalışır; arayüz ona `127.0.0.1` üzerinden HTTP ile konuşur.

## Gerekçe
- Tauri Ubuntu'nun sistem webview'ini (webkit2gtk) kullanır: Electron'a göre
  onda bir paket boyutu ve belirgin biçimde düşük bellek.
- Sidecar mekanizması Python sürecinin yaşam döngüsünü kabuğa bağlar.
- HTTP sınırı, ileride uzaktan erişim istenirse aynı backend'i web paneli
  olarak sunmayı mümkün kılar — mimari değişmez.

## Sonuçlar
- Ubuntu'da `webkit2gtk-4.1` bağımlılığı paketlemede karşılanmalı.
- Kabuk seçimi `apps/desktop/` içinde izoledir. Electron'a geçiş yalnızca bu
  klasörü etkiler; backend, platform ve modüller etkilenmez.
- Modül arayüzleri `ui-kernel` tarafından dinamik yüklenir; kabuk hiçbir modülün
  adını bilmez.
