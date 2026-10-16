# Ödeme Talebi modülü

Veliye borç bildirimi ve ödeme bağlantısı gönderir. Nakit tahsilat da buradadır —
ödeme akışının parçasıdır ve borç kapanınca bekleyen talepleri süresi dolmuş yapar.

## Kantinin değiştirilemez kuralları

- **Tutar seçilemez.** Talep her zaman öğrencinin *o anki* borcuyla açılır.
- **Açık talep yeniden fiyatlanır.** Bekleyen talep varsa yenisi açılmaz; mevcut
  talebin tutarı güncel borca çekilir ve aynı `ref` korunur. "Oluştur" ile
  "yeniden gönder" aynı işlemdir.
- Borcu olmayana (`no_debt`) ve veli telefonu olmayana (`no_phone`) gönderilmez.
- Borç nakit tahsilatla kapanınca bekleyen talepler `EXPIRED` olur.

## Kantinde olmayan, burada olan

- **Toplu talep** — çoklu seçim, kuru prova, sonuç raporu.
- **Çift gönderim koruması** — kantinde dedupe ya da bekleme süresi YOKTUR; aynı
  öğrenciye arka arkaya talep her seferinde veliye yeni bir SMS demektir. Bu ekran
  son gönderimi kendi tablosundan bilir ve yakın zamanda gönderilmiş öğrenciyi
  varsayılan olarak atlar; tekrar için açık onay ister.
- **İptal** — kantine eklenen `cancel` ucuyla bekleyen talep süresi dolmuş yapılır,
  velinin elindeki link ölür. Kayıt silinmez.
- **Süzgeç, arama, yaşlandırma** — kaç gündür bekliyor.
- **SMS şablonu düzenleme** — tablette bu ekran hiç yok. Boş şablon reddedilir;
  kantin boş şablonu varsayılana düşürüp ödeme linkini SMS'ten sessizce siler.
- **PDF raporları (63-finans-pdf)** — talepler (ekrandaki durum ve arama; durum
  ara toplamı), nakit tahsilatlar (başarısız denemeler toplama girmez) ve borçlu
  öğrenciler (kantin açık alacağı, sınıf ara toplamı). Ortak araç çubuğu
  "Önizle · Yazdır · PDF kaydet"; `POST /api/bbd_payment_request/report`,
  `POST …/report/pages`. Kantin parasıdır, kurum carisiyle birleşmez (A12);
  veli telefonu yazılmaz.

- Sözleşme: `module.yaml` · Giriş noktası: `backend/module.py` → `register(ctx)`
- Kurallar: [../../CLAUDE.md](../../CLAUDE.md) · Kılavuz: [../../docs/module-guide.md](../../docs/module-guide.md)

Grup: **BBD** · İzinler: `.view`, `.send`, `.collect`, `.manage_templates`
