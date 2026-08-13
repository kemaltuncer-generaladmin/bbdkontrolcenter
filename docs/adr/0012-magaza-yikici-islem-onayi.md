# 0012 — Mağaza yıkıcı işlemleri PIN yerine gerekçeli onay ister

**Durum:** Kabul edildi · 2026-08-13

## Bağlam

[docs/permissions.md](../permissions.md) uygulama kuralı 3 şunu söylüyor:

> Yıkıcı işlemler (`database.restore`, `users.manage` silme, `roles.manage`)
> izin yeterli olsa bile **PIN teyidi** ister.

Bu kural çekirdek yönetim işlemleri için yazıldı: bir veritabanını geri
yüklemek, bir kullanıcıyı silmek, bir rolü değiştirmek. Bunlar **seyrek**,
**tekil** ve **kurumsal** işlemlerdir; günde bir kez bile yapılmayabilir.

BBD Store 20 ekran ekliyor ve bunların yıkıcı sayılan işlemleri farklı bir
tabiatta: sipariş iptali, iade onayı, kargo etiketi satın alma, ödeme
bağlantısı gönderme, toplu fiyat değişikliği, ürün pasifleştirme. Bunlar
**operasyonel** işlemlerdir — mağaza personeli bir günde onlarcasını yapar.

Ayrıca kuralın bugün bir uygulaması yok: `destructive: true` bayrağı manifest
şemasında ve birkaç modülün manifestinde var, `config/default.yaml` içinde
`auth.require_pin_for_destructive: true` yazıyor, ama çekirdekte bu bayrağı
okuyan tek satır kod yok. `Identity` sınıfında `verify_pin()` benzeri bir
metot da yok. Yani kural bugüne kadar yazılı ama uygulanmamış durumda.

## Karar

BBD Store (`store_*`) modüllerinde yıkıcı ve para harcayan işlemler **PIN
teyidi istemez**. Yerine üç katmanlı bir kapı kullanılır:

1. **Ayrı izin anahtarı.** Yıkıcılık `<id>.manage` içine gizlenmez; kendi
   anahtarını taşır: `store_orders.cancel`, `store_refunds.approve`,
   `store_shipping.purchase`, `store_payment_gateway.create`,
   `store_backups.restore`, `store_customers.anonymize`. Rol matrisi bunları
   ayrı ayrı dağıtır.
2. **Gerekçeli onay.** Arayüzde `confirmWithReason()` — gerekçe zorunludur ve
   **backend'de** doğrulanır (arayüzde gizlemek yetkilendirme değildir, K9).
   Gerekçe en az 10 karakterdir.
3. **Çift denetim kaydı.** Gerekçe hem Kontrol Merkezi'nin yerel denetim
   tablosuna **gönderimden önce** yazılır (ağ koparsa "ne yapmaya çalıştık"
   kaydı kalır), hem de `X-Bbd-Reason` başlığıyla mağazaya iletilip
   `admin_api_audits` içine düşer.

Ek olarak, para harcayan veya geri alınamaz her uzak uç **`dryRun`** destekler
ve varsayılanı `true`'dur: ekran önce kuru prova çalıştırır, ne olacağını
gösterir, kullanıcı onaylayınca gerçek işlem gider.

Bunun sonucu olarak `store_*` izinlerinde **`destructive: true` bayrağı
kullanılmaz.** Bayrak çekirdekte bir gün PIN kapısına bağlanırsa, onu taşıyan
izinler PIN istemeye başlar; mağaza izinleri bunu istemediği için bayrağı
taşımaz. Yıkıcılık ayrı anahtarla ifade edilir, bayrakla değil.

## Gerekçe

- **PIN'in koruduğu şey zaten sağlanmış.** PIN kimlik teyididir: "klavyenin
  başındaki kişi gerçekten bu kullanıcı mı?" Bu soru oturum açılırken
  cevaplandı ve oturum 30 dakika hareketsizlikte düşüyor. Aynı soruyu günde
  40 kez tekrar sormak yeni bir bilgi üretmez.
- **Sürtünme yanlış yere konursa güvenliği azaltır.** Her iptalde PIN sorulan
  bir ekranda iki şey olur: ya PIN ekranın yanına yapıştırılır, ya da personel
  işi Bagisto'nun kendi panelinden yapar — orada hiçbir gerekçe kaydı yoktur.
  İkisi de bugünkünden kötüdür.
- **Gerekçenin koruduğu şey PIN'in koruyamadığıdır.** "Bu siparişi kim iptal
  etti?" sorusunu oturum zaten cevaplıyor. "Neden iptal etti?" sorusunu
  yalnızca gerekçe cevaplar; üç ay sonra müşteri aradığında aranan bilgi budur.
- **Kuru prova, onaydan daha güçlü bir korumadır.** Bir kullanıcı PIN'i
  refleksle girer; ama "bu işlem 47 üründe fiyatı %20 artıracak" tablosunu
  gördüğünde yanlış seçimi fark eder. Yanlış işlemi durduran şey kimlik teyidi
  değil, sonucun önceden gösterilmesidir.

## Sonuçlar

- `docs/permissions.md` kuralı 3 **çekirdek işlemler için geçerliliğini
  korur.** `database.restore`, `users.manage`, `roles.manage` PIN isteyecek —
  o mekanizma yazıldığında. Bu ADR yalnız `store_*` kapsamını ayırır.
- Çekirdekte PIN teyidi mekanizması **yazılmaz** (bu iş kapsamında). Yazılırsa
  mağaza modülleri etkilenmez, çünkü `destructive` bayrağı taşımıyorlar.
- `docs/permissions.md` bu ADR'ye atıfla güncellenir; geçici "tüm ekranlar tüm
  rollere açık" kuralı `store_*` için sona erer ve rol matrisi daraltılır.
- Gerekçe alanı **veri**dir, süs değil: `store_udit_logs` ekranı gerekçe
  metninde arama yapar ve "gerekçeli işlemler" süzgeci sunar.
- Risk kabul ediliyor: oturumu açık bırakılmış bir makinede başka biri yıkıcı
  işlem yapabilir. Karşı önlem PIN değil, oturum süresidir
  (`auth.session_idle_minutes`, varsayılan 30).
