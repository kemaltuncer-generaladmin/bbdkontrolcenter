# Zil Sistemi modülü

Haftalık zil saatleri, sesli anons ve elle grup çağrısı.

## Üç iş, üçü de ayrı

1. **Otomasyon** — haftalık zil saatleri. Saati gelince yalnız teneffüs zili
   çalar. Başka karar içermez.
2. **Grup çağrısı** — elle. Grup seçilir, "Çağır" denir; o grubun anonsu çalar
   ("İlayda, Hüseyin hoca ile dersiniz başlıyor."). Zil çalmaz.
3. **Elle zil** — yalnız zil sesi.

> **Zilden sonra anons geçmez** (18.08.2026, kullanıcı kararı). Otomatik zil
> bir zamanlar arkasından "Lütfen derse geçiniz." çalıyordu; o yol tümüyle
> kaldırıldı — ayarda kapatılabilir bir seçenek de bırakılmadı. Anons yalnız
> elle basılan grup çağrısında duyulur.

## Ses nereden geliyor

Anons sesleri **Vertex AI (Gemini TTS)** ile ÖNCEDEN üretilir ve
`data/sounds/anons-<özet>.wav` olarak saklanır. Çalma anında buluta çıkılmaz.
Yeni istek yalnız içerik değişince doğar: grup eklendi, adı değişti, metin
şablonu düzenlendi. Ömür boyu toplam çağrı ≈ *grup sayısı*.

Servis hesabı kasadan gelir: `bell.vertex_service_account` (K8, depoda durmaz).
429 disiplini `backend/voices.py` içindedir — tek işçi, çağrı arası bekleme,
üstel geri çekilme, kalıcı hatada yeniden deneme yok.

## Ses nereden çıkıyor

Asıl hoparlör okulun zil sistemine bağlı **Windows ajanındadır**
(`agent/`). Komut bbdstore köprüsü üzerinden gider (`backend/bridge.py`,
kasa anahtarı `bell.bridge_token`). Ses dosyaları ajanın yerelinde durur;
komut gelince indirme beklenmez.

Yerel `audio` yeteneği ikinci hedeftir (`play_locally`) — yedek ve geliştirme
içindir, kapatılabilir.

## Yapı

- Sözleşme: [module.yaml](module.yaml) · Giriş: `backend/module.py`
- `backend/speech.py` Vertex istemcisi · `backend/voices.py` önbellek + kuyruk
- `backend/bridge.py` köprü istemcisi · `backend/service.py` iş kuralları
- `agent/` Windows zil ajanı (arayüzsüz `.exe`)
- Testler: `tests/` — ağa çıkmaz, Vertex ve köprü taklit edilir

Haftalık saatler ve gruplar bu modülündür; Ders Takvimi ekranı onları
`bell.week` yeteneğinden okuyup salt okunur gösterir (K3).
