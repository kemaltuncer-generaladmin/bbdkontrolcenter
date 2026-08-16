# 0013 — Anons sesi: Vertex AI, önden üretim

**Durum:** Kabul edildi · 2026-08-14

## Bağlam

Zil sistemi artık yalnız zil çalmıyor; arkasından konuşuyor. İki tür anons var:

- **Otomatik** — her zil saatinde: "Lütfen derse geçiniz."
- **Elle çağrı** — grup seçilip düğmeye basılınca: "İlayda, Hüseyin hoca ile
  dersiniz başlıyor."

Kurumun Google projesinde (`medasilab`) yalnızca `aiplatform.googleapis.com`
açık; ayrı bir `texttospeech.googleapis.com` yok. Sağlayıcı seçimi bu yüzden
tartışmalı değil, ama **ne zaman çağrılacağı** kritik bir karar.

Kota gerçek bir sınır: Vertex TTS uçları istek başına ücretlendirilir ve
kısa aralıklı çağrılarda 429 döner. Bir okul zili günde 60–80 kez çalar.

## Karar

### 1. Ses ÖNCEDEN üretilir, çalma anında değil

Anons metni değiştiğinde üretilir ve `data/sounds/anons-<özet>.wav` olarak
diske yazılır. Zil çaldığında yalnızca dosya açılır.

Bunun alternatifi — "zil çalarken metni gönder, dönen sesi çal" — üç ayrı
biçimde arızalıdır:

| Sorun | Sonuç |
|---|---|
| Ağ ya da bulut gecikmesi | Zil 2–4 saniye geç çalar; her seferinde farklı |
| 429 | O anki zil **hiç çalmaz**, telafisi yok |
| Kota | Günde 80 çağrı × her gün, hiçbiri yeni içerik değil |

Önden üretimle ömür boyu toplam çağrı **grup sayısı + 1**'dir. Altı gruplu bir
kurum için yedi çağrı — bir kez.

### 2. Önbellek anahtarı `sha256(metin | model | ses)`

Metnin kendisi değil, üçlünün özeti. Ses ya da model değişirse aynı metin
yeniden üretilmelidir; metin aynı kaldığı için değil.

İki somut kazanç: kaldırılan bir grup aynı adla geri açılırsa ses yeniden
üretilmez; bir grubun adı değiştirilip eskiye dönülürse de üretilmez.

### 3. Üretim kuyruğu tek işçidir ve geri çekilir

`modules/bell/backend/voices.py`: aynı anda tek çağrı, çağrılar arası en az
1 saniye, 429/5xx'te 2→4→8→16→32 saniye (`Retry-After` varsa o yeğlenir),
en çok 5 deneme.

**Kalıcı hata yeniden denenmez.** 400/401/403/404 beklemekle düzelmez;
denemek kotayı harcar ve hatanın görünmesini geciktirir. Ayrım
`SpeechError.retryable` alanında taşınır.

### 4. Başarısızlık saklanmaz, düğmeyi kapatır

Denemeler bitince hata `mod_bell_voice.error` alanına yazılır. Ekran o sesi
kırmızı gösterir ve onu kullanan "Çağır" düğmesini **kapalı** çizer, nedenini
`title` ve `aria-label` içine yazar (`blockedButton`).

Basılabilen ama sessizlikle biten bir düğme, hiç olmayandan kötüdür:
kullanıcı anonsun geçtiğini sanır.

### 5. `google-auth` kullanılmaz, JWT elle imzalanır

`google-auth` erişim belirteci için senkron `requests` taşıyıcısını ister;
çekirdek tamamen `asyncio` üzerinde ve `httpx` kullanıyor. Servis hesabı akışı
küçük: JWT'yi `cryptography` ile imzalayıp (kasa zaten ona dayanıyor)
`oauth2.googleapis.com/token` ile değiştirmek ~40 satır.

Aynı yol bbdstore tarafında da izleniyor
(`packages/BBD/Notify/src/Push/FcmClient.php`, `firebase/php-jwt` ile).

### 6. Ham PCM'i kendimiz sarmalıyoruz

Gemini TTS `audio/L16;codec=pcm;rate=24000` döndürür — başlıksız PCM.
`paplay` ve `aplay` bunu tanımaz; `winsound` da tanımaz. WAV başlığı Python'un
`wave` modülüyle yazılır.

Bu sessiz bir arıza kaynağıydı: dosya oluşur, ekran "hazır" der, çalma anında
hiçbir ses çıkmaz. Testi bu yüzden `test_bell_speech.py` içinde açıkça duruyor.

### 7. Sır kasadan gelir

Servis hesabı JSON'u `bell.vertex_service_account` anahtarıyla kasada durur
(K8). Adlandırma `server.<uygulama>.<alan>` **değil**: bu kimlik bir sunucuya
değil, uygulamaya ait — `store.admin_token` ile aynı gerekçe
(`modules/store_api/backend/client.py:112-114`).

## Sonuçlar

- Zil vakti bulut çağrısı yok; gecikme ve 429 riski çalma yolundan tamamen çıktı.
- Metin ya da ses değiştirmek bir kerelik üretim maliyeti doğurur; ekran bunu
  değişiklikten **önce** yazar ("6 grubun sesi yeniden üretilir").
- Ses üretilemezse özellik kısmen çalışır: zil çalar, anons çalmaz, ekran
  nedenini gösterir. Modül düşmez (K7).
- Kurum sağlayıcı değiştirmek isterse `speech.py` tek dosyadır; kuyruk,
  önbellek ve ekran değişmez.

## Alternatifler

**Cloud Text-to-Speech (Chirp 3 HD).** Türkçe sesleri daha doğal. Projede API
açık değil; açılırsa `speech.py` yanına ikinci bir istemci konur ve önbellek
anahtarındaki `model` alanı ayrımı kendiliğinden yapar.

**Yerel TTS (`espeak-ng`/`piper`).** Bulut bağımlılığı yok ama Türkçe kalitesi
okul anonsu için yetersiz; ayrıca sesi Windows ajanının makinesinde üretmek
gerekirdi — o makine bilerek "yalnız çalar" tutuluyor.

**Sesleri elle kaydetmek.** Grup adı her değiştiğinde birinin mikrofon başına
oturması gerekirdi; "İlayda" gibi tek kişilik gruplar bunu imkânsız kılıyor.
