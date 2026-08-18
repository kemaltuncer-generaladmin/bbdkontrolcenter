# ADR 0027 — Zilden sonra anons geri geldi; tür açıkça saklanıyor

**Durum:** Kabul edildi · 18.08.2026
**Etkilediği:** `modules/bell`, `modules/bbd_class_schedule`
**Geçersiz kıldığı:** aynı gün alınan "zilden sonra anons geçmez" kararı
(ADR yazılmamıştı; karar `module.yaml`, `README.md` ve `service.py` başlıklarında
duruyordu). [ADR 0013](0013-anons-sesi-vertex-tts.md) ses üretim yolu için
geçerliliğini korur.

## Bağlam

Zil sistemi bir zamanlar otomatik zilin arkasından tek bir anons çalıyordu:
"Lütfen derse geçiniz." 18.08.2026'da bu yol **tümüyle kaldırıldı** — ayarda
kapatılabilir bir seçenek bile bırakılmadı, gerekçe "ekranda duran bir onay
kutusu yanlışlıkla geri açılabilir" idi.

Aynı gün kullanıcı bunun tersini istedi ve eksik olanı da söyledi: sorun anonsun
kendisi değil, **tek bir cümle olmasıydı**. Zil hem ders başlangıcında hem
teneffüs başlangıcında çalıyor; ikisinin arkasından aynı cümlenin gitmesi
yanlış. Teneffüse çıkan öğrenciye "derse geçiniz" demek, anons hiç olmamasından
kötü.

## Karar

**1. Zilin arkasından yeniden anons geçer.** Otomatik zil iki adımlı bir komut
üretir: önce zil sesi, hemen arkasından anons. Adımlar sırayla çalınır — köprü
komutu zaten bir dizi taşıyordu, ajan onları üst üste bindirmeden çalıyordu ve
yerel yol da `await` ile birini bitirmeden ötekine geçmiyordu. Altyapı yerinde
duruyordu; kaldırılan yalnız listenin ikinci elemanıydı.

**2. Zil saatinin türü açıkça saklanır.** `mod_bell_time` tablosuna `kind`
sütunu eklendi (`ders` | `teneffus`, varsayılan `ders` — 004 göçü).

Tür **etiketten türetilmez**. `label` serbest metindir: "teneffüs", "mola",
"büyük teneffüs", "" hepsi geçerli ve hiçbir kural bunlarla "ders" arasındaki
farkı güvenilir biçimde bilemez. Etiketten tahmin eden bir kural, bir gün
"ders arası" yazan bir satırda sessizce yanlış anonsu çalardı. Bu, 003 göçünün
grup türü için verdiği kararın aynısıdır ve aynı gerekçeye dayanır.

**3. Türe göre iki ayrı metin.** Ayarlarda `texts.lessonAfter` ("İyi dersler.")
ve `texts.breakAfter` ("İyi teneffüsler."). Hangi metnin çalacağı sorusunun tek
cevap yeri `after_text()`; `call_text()` grup çağrıları için ne yapıyorsa o.

**4. Tek zil sesi kalır.** Ders zili ve teneffüs zili için ayrı ses dosyası
tanımlanmaz (kullanıcı kararı). Ayrışan yalnız arkasındaki cümledir.

**5. Boş metin geçerli bir seçimdir.** Bir türün metni boşaltılırsa o zilin
arkasından anons gitmez. Bu yüzden ayar okunurken "anahtar hiç yok" (varsayılanı
kullan) ile "anahtar var ama boş" (anons istemiyorum) **ayrılır**. Ayrılmasaydı
kullanıcı alanı temizler, kaydeder ve varsayılan geri gelirdi — yani kaydettiğini
sandığı şey olmazdı. Kaldırılan kararın korkusu ("kutu yanlışlıkla açılır")
burada karşılanıyor: kapatma gizli bir kutu değil, ekranda duran ve içeriği
görünen bir metin alanıdır.

**6. Anons zili susturamaz.** Anons sesi hazır değilse (Vertex sırası bitmemiş,
kota dolmuş, taze kurulum, kalıcı disk yeni bağlanmış) **zil yalnız başına
çalar** ve sebep günlüğe `anons` türüyle yazılır. Tersi — anons yok diye zili de
çalmamak — okulu sessiz bırakırdı; bu, eksik bir cümleden çok daha büyük bir
arızadır.

**7. Zil sonrası anonslar ajanın ses kitaplığında durur.** `needed_sounds()`
listesine girerler. Girmeselerdi `sync_sounds()` onları köprüde "gereksiz" sayıp
sildirir, zil çalar ve arkasından sessizlik gelirdi — sebebi hiçbir ekranda
görünmeden.

## Sonuçlar

- Mevcut zil saatlerinin hepsi `ders` sayılır; zil aynı saatlerde aynı biçimde
  çalmaya devam eder. Hangi satırın teneffüs olduğunu kullanıcı bir kez
  işaretler. Sessiz bir davranış değişikliği yok — panel türü görünür kılar.
- 0.2'nin `texts.lesson` alanı **hâlâ düşürülür ve yeni alanlara kopyalanmaz**.
  O metnin hangi türe ait olduğu bilinmiyor; birine kopyalamak "derse geçiniz"
  cümlesini teneffüs zilinin arkasına takma riski taşırdı.
- Ses üretimi iki yeni metin kadar artar (grup sayısından bağımsız, toplam iki
  ses). Vertex çağrı bütçesi bundan etkilenmez.
- Ders Takvimi ekranı türü aynada salt okunur gösterir; girişi yine yalnız Zil
  Sistemi ekranından yapılır (K3, mevcut `bell.week` yeteneği genişledi).
