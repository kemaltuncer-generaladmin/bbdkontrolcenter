# Zil Sistemi modülü

Haftalık zil saatleri, sesli anons ve elle grup çağrısı.

## Üç iş, üçü de ayrı

1. **Otomasyon** — haftalık zil saatleri. Saati gelince zil çalar, hemen
   arkasından türe göre anons gider. Başka karar içermez.
2. **Grup çağrısı** — elle. Grup seçilir, "Çağır" denir; o grubun anonsu çalar
   ("İlayda, Hüseyin hoca ile dersiniz başlıyor."). Zil çalmaz.
3. **Elle zil** — yalnız zil sesi.

> **Zilden sonra anons geçer — türe göre** ([ADR 0027](../../docs/adr/0027-zil-sonrasi-anons-ders-teneffus.md)).
> Her zil saati `ders` ya da `teneffus` türündedir; ders zilinin arkasından
> "İyi dersler.", teneffüs zilinin arkasından "İyi teneffüsler." gider. Tür
> etiketten tahmin edilmez, açıkça saklanır — "mola" yazan bir satırın hangisi
> olduğunu hiçbir kural bilemez.
>
> **Anons zili susturamaz:** sesi hazır değilse zil yalnız başına çalar ve
> sebep günlüğe yazılır. Bir türün metni boşaltılırsa o zilin arkasından anons
> gitmez; bu geçerli bir seçimdir.

## Silme kalıcıdır

Grubu silmek **geri alınamaz**: satır, anons kaydı, `data/sounds` altındaki
`.wav` ve zil ajanının kitaplığındaki kopya birlikte gider. `bell.delete` izni
ve **PIN teyidi** ister.

Aynı ses başka bir canlı grup ya da zil sonrası anons tarafından da
kullanılıyorsa **dosya silinmez** — önce referans sayılır.

"Kullanılmayan sesleri sil" düğmesi, grup adı değişiklikleriyle biriken ve
artık hiçbir yere bağlı olmayan `.wav`'ları temizler.

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

## Öğrenci çağırma (`bell.announce`)

Serbest bir cümle okulun hoparlöründen duyurulur: "Elif Kaya, lütfen
danışmaya gel." Mobil geçit (BBD Akademi) ve personel ekranı
(`POST /api/bell/page`, `bell.ring_now`) aynı yoldan geçer.

- Ses önbellekte (`mod_bell_voice`, metin özeti) varsa hemen çalınır; yoksa
  Vertex ile **anlık** üretilir — gece kuyruğunun önüne geçer ama "aynı anda
  tek çağrı" kuralını delmez. Süre (`page.tts_timeout_seconds`) dolarsa
  üretim arkada sürer, çağrı `tts_unavailable` döner.
- Ses köprüde yoksa komuttan önce yüklenir; komut grup çağrısıyla aynı
  yoldan (`_dispatch`) gider.
- Sonuç hata değil `status`tur: `queued` · `disabled` · `tts_unavailable` ·
  `failed`. `X-Request-Id` / `request_id` tekildir; aynı istek ikinci kez
  çalınmaz.
- Geçmiş `mod_bell_page` tablosundadır (metin değil özeti). Son 24 saatte
  çalınan ve 60 gün içinde hazırlanan sayfa sesleri `needed_sounds()` ve
  `_live_voice_keys()` listesindedir: köprü eşitlemesi ve "kullanılmayan
  sesleri sil" onları silmez.
- Şablon `texts.page` (varsayılan "{ad} {soyad}, lütfen danışmaya gel.");
  metni çağıran kurar (`render`), bell öğrenci kaydını bilmez.
- Gece hazırlığı isteğe bağlıdır (`page.prepare_nightly: false`):
  açılırsa `bbd_students.directory` yeteneğinden öğrenci adları okunur ve
  her öğrencinin çağrı sesi önden üretilir.

## Yapı

- Sözleşme: [module.yaml](module.yaml) · Giriş: `backend/module.py`
- `backend/speech.py` Vertex istemcisi · `backend/voices.py` önbellek + kuyruk
- `backend/bridge.py` köprü istemcisi · `backend/service.py` iş kuralları
- `agent/` Windows zil ajanı (arayüzsüz `.exe`)
- Testler: `tests/` — ağa çıkmaz, Vertex ve köprü taklit edilir

Haftalık saatler ve gruplar bu modülündür; Ders Takvimi ekranı onları
`bell.week` yeteneğinden okuyup salt okunur gösterir (K3).
