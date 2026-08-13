# SMS Sistemi modülü

**Serbest SMS.** Kurumun Netgsm başlığından velilere serbest metin gönderir —
duyuru, veli toplantısı, tatil bildirimi, hatırlatma. **Ödeme linki göndermez;**
o Ödeme Talebi modülünün işidir.

## Ne yapar

- **Toplu gönderim** — sınıfa göre gruplu seçim, "yalnız borçlular" / "yalnız veli
  telefonu olanlar" süzgeçleri.
- **Kişiselleştirme** — `{ad}`, `{sinif}`, `{borc}`, `{okul}` yer tutucuları her
  alıcı için çözülür; önizlemede gerçek değerlerle görünür.
- **Segment ve kredi hesabı** — Türkçe karakter (`ğ ı İ ş Ş ç`) mesajı UCS-2'ye
  düşürür ve segment **160 değil 70 karakter** olur. Ekran karakter setini,
  segment sayısını ve toplam krediyi yazar; "sadeleştir" tek tıkla GSM-7'ye çeker.
- **Kuru prova → onay → gönder** — kime, hangi numaraya, hangi metin, kaç kredi.
- **Hazır mesajlar** — sık kullanılan metinler kaydedilir.
- **Geçmiş** — bu panelden giden her SMS'in dökümü. Kantinin `POST /api/sms/send`
  ucu eş zamanlıdır ve **hiçbir yere kaydetmez**; kaydın tek yeri burasıdır.
- **Kuyruk** — ödeme SMS'lerinin durumu, deneme sayısı, yeniden deneme zamanı.
- **Netgsm ayarları** — kullanıcı kodu, parola, başlık (≤11 karakter).

## Mühürlü kural

**Alıcı daima velidir.** Kantinde öğrenci telefonu diye bir alan yoktur (26 Temmuz
göçüyle silindi) ve SMS ucu öğrenci verildiğinde serbest telefonu yok sayıp
`parent_phone` kullanır. Ekran her satırda hangi veli numarasına gideceğini yazar;
veli telefonu yoksa o satır gönderimden düşer.

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `bbd_sms.view`, `bbd_sms.send`, `bbd_sms.manage_settings`
