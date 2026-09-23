# BBD Store ürün, sipariş, kargo ve iade denetimi — 23.09.2026

Bu liste `bbdkontrolcenter` ve `bbd-store` depolarındaki akışa, ilgili testlere
ve canlı sunucuda **yazma yapmadan** çalıştırılan Geliver/POS ön kontrollerine
dayanır. Gerçek etiket satın alma ve banka para iadesi yapılmadı; bunlar ayrı
canlı işlem kanıtı gerektirir.

## Tamamlanan işler

- [x] Ürün desisi → sipariş kalemi → kargo desisi yolunu izledik. Yeni
  siparişlerde birim desi, kaynağı ve sayfa sayısı sipariş kaleminin `additional`
  alanına kaydediliyor; sonraki katalog düzenlemeleri o siparişin desisini
  değiştirmiyor. Şema göçü gerekmiyor.
- [x] Eski siparişte desi belirsizse yalnız ağırlıktan ücretli gönderi taslağı
  veya teklif oluşturulmasını engelledik; operatör ölçü veya doğrulanmış desi
  girmeli.
- [x] Kargoya ver sihirbazına fiziksel en/boy/yükseklik alanları eklendi. Üçü
  birlikte girilirse teklif ve gönderi aynı ölçülerden hesaplanıyor; Geliver'a
  santimetre cinsinden iki ondalık hassasiyetle aktarılıyor. Elle yazılmış desi
  ile fiziksel ölçü farklıysa fark gösteriliyor.
- [x] Etiket satın almadan önce sipariş, taşıyıcı, koli, ölçüler, ağırlık ve
  faturalanacak birim özeti gösteriliyor. Eksik fiziksel ölçü onaydan önce
  reddediliyor; Geliver'ın kesin teklifinin değişebileceği belirtiliyor.
- [x] `hepsijet_hepsijet` ve `HEPSIJET_STANDART` gibi iç kodlar okunur taşıyıcı
  adına çevrildi. Geliver HTTP/SDK hataları ham URL veya teknik exception
  yerine anlaşılır, güvenli mesajlarla gösteriliyor.
- [x] İade zinciri doğrulandı: Bagisto kredi notu → `sales.refund.save.after` →
  POS iade dinleyicisi → Kuveyt Türk. Kontrol Merkezi'nin ikinci, ayrı POS
  iadesi çağrısı kapalı ve ekran açıklaması gerçek para hareketini söylüyor.
- [x] Aynı siparişte RMA varken manuel kredi notu için yetkiliye çifte banka
  iadesi uyarısı ve ikinci onay eklendi. Onay izni sunucuda denetleniyor; RMA
  listesi işlemden hemen önce yeniden sorgulanıyor. Görülen RMA kimlikleri
  canlı listeyle eşleşmezse veya liste eksik/erişilemezse işlem duruyor. RMA
  kimlikleri, onay ve aktör denetim kaydına yazılıyor.
- [x] Canlı Geliver ön kontrolü geçti: token/erişim, tek açık Hepsijet fiyatı,
  gönderici adresi ve webhook kaydı. Test modu kapalı. Kuveyt Türk ön kontrolü
  18 geçti, 0 uyarı, 0 hata; iade/iptal dinleyicileri bağlı.

## Açık işler — öncelik sırası

1. **P1 · Gerçek işlem kanıtı:** Kontrollü bir siparişle Geliver teklifi,
   etiket satın alma, PDF/takip ve taşıyıcının faturalanan desisini karşılaştır.
   Ardından kontrollü bir iadenin banka referansı ve mutabakatını doğrula.
   Bu işlem para harcar ve gerçek müşteri/sipariş etkisi yaratır; test verisi
   ve işlem limiti önceden belirlenmeli.
2. **P1 · Çift iade mutabakatı:** Yetkili onayı RMA ile manuel kredi notu
   çakışmasını görünür ve denetlenebilir kılar; aynı iş niyetini farklı refund
   ID'leriyle bankaya gönderme olasılığını otomatik olarak ortadan kaldırmaz.
   İki akış için ortak korelasyon/idempotency anahtarı ve banka sonucunun tek
   yerde gösterimi ekle. Canlı örnekte banka mutabakatını doğrula.
3. **P1 · Eski sipariş desisi:** Snapshot öncesi siparişlerde katalog geri dönüşü
   tarihsel değer değildir. Etiket almadan önce fiziksel ölçü veya doğrulanmış
   desi gir; ileride tarihsel siparişler için güvenilir veri kaynağı varsa toplu
   düzeltme planla.
4. **P1 · Çoklu koli:** Geliver isteği tek ölçü seti taşıyor; formdaki koli
   adedi notta kalıyor. Farklı boyutlu koliler için ayrı paket modelini ve
   Geliver'ın desteklediği işlemi tanımla; her kolinin desi/ağırlığını ayrı
   doğrula.
5. **P2 · Ürün fiziksel ölçüleri:** Katalogda gerçek koli en/boy/yükseklik
   snapshot'ı yok; fiziksel ölçüler kargoya verirken elle giriliyor. Düzenli
   ürünlerde depo ölçülerini katalogda yönetmek tekrar girişi azaltır.

## Doğrulama sınırı

Kontrol Merkezi ürün, sipariş, kargo, iade, fatura, API ve ödeme geçidi hedef
testleri geçti. Mağazada Geliver testleri 143 test/271 assertion ile geçti;
POS RefundListener testleri 16 test/52 assertion ile başarısızlık olmadan
tamamlandı (Pest bunları deprecated sayıyor). PHP Pint/sözdizimi, Python Ruff,
panel JavaScript sözdizimi ve `git diff --check` geçti. Ön kontroller ağ
erişimini ve yapılandırmayı kanıtlar; gerçek ücretli işlemin sonucunu kanıtlamaz.
