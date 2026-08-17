# Çıktı Merkezi modülü

Üretilen her rapor, dışa aktarım ve yedeğin tek listesi: süzgeç, önizleme,
yeniden baskı ve klasörü açma.

Karar: [ADR 0019](../../docs/adr/0019-cikti-merkezi.md) · Baskı yolu:
[ADR 0014](../../docs/adr/0014-cok-platformlu-baski.md)

## Kayıt bu modülde DOĞMAZ

Çıktı kaydı, dosyayı yazan çekirdek fonksiyonunda (`km_core/files/private.py`
→ `outputs_log`) düşer ve `km_core/store` içindeki ortak `outputs` tablosunda
durur. Yirmi modülün hiçbirinde "çıktı ürettim" diyen tek satır yoktur; bu
modül kapatılsa bile kayıt düşmeye devam eder, yalnız ekranı kaybolur.

Bu yüzden modülün kendi tablosu ve göçü yoktur.

## Ekranın kuralları

- **Dosya kaybolmuşsa satır silinmez.** Durumu "dosya bulunamadı" olur,
  yeniden bas düğmesi kapanır ve nedeni yazılır (ADR 0019 §4).
- **Saklama süresi kayıt içindir.** `keep_job_history_days` (30) yalnız KAYIT
  satırlarını budar; masaüstündeki dosyalara dokunulmaz (§5).
- **"Basıldı" değil "denendi".** Linux'ta sessizce basılır; Windows/macOS'ta
  `printer` yeteneği `{mode: "system"}` döner, yani sistem yazdırma penceresi
  açılır ve kullanıcı iptal edebilir. Sayaç bu yüzden denemeyi sayar
  (ADR 0014).
- **Rapor yeniden ÜRETİLMEZ.** Önizlenen ve basılan şey kayıttaki dosyanın
  kendisidir; kaynak veri değiştiyse yeniden üretilen rapor eskisiyle aynı
  değildir.

## İzin adları

ADR 0019 §6 izinleri `outputs.view` / `outputs.reprint` diye anıyor. Çekirdek
manifest kapısı izin anahtarının modül kimliğiyle başlamasını şart koştuğu için
anahtarlar `print.view` ve `print.reprint` yazıldı; anlam birebir aynıdır
(görüntüleme ile yeniden basma ayrı izinlerdir). Gerekçe `module.yaml` içinde.

- Sözleşme: [module.yaml](module.yaml) · Durum: kodlu (`enabled: true`)
- Giriş noktası: `backend/module.py` → `register(ctx)`
- Uçlar: `GET /api/print/outputs` · `GET /api/print/printer` ·
  `POST /api/print/preview` · `POST /api/print/reprint` · `POST /api/print/folder`
