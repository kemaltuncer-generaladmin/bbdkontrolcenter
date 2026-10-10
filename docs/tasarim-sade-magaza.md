# Sade Mağaza Ekranları — tasarım kuralları

10.10.2026 — kullanıcı kararı: BBD Store "karışık değil, daha sade, tık tık tık"
olacak. Menü yedi ekrana indi (Dashboard, Siparişler, Kargo Yönetimi, İade
Yönetimi, Müşteri Listesi, Ürün Yönetimi, Set Yönetimi). Bu belge o yedi
ekranın ortak görsel kuralıdır. Amaç: günlük iş **en çok üç tıkla** biter,
ekranın ilk görünüşünde yalnız o işe gereken şey durur.

Kural kitten gelen bileşenlerle uygulanır (ADR 0011); panel kendi kopyasını
yazmaz.

## 1. Süzgeçler katlanır

- `filterBar({ compact: true, ... })` kullanılır.
- Görünür kalan (`primary: true`) alan en çok **iki** tanedir: arama kutusu ve
  ekranın asıl ayrımı (durum ya da tarih aralığı).
- Gerisi "Filtreler" düğmesinin arkasına katlanır. Katlanan süzgeç doluysa
  düğme sayısını kendisi yazar; ayrıca bir şey yapılmaz.
- Tek kanallı mağazada "Kanal" gibi tek seçenekli süzgeç hiç çizilmez
  (`choice.js` zaten yapıyor).

## 2. İkincil eylemler tek menüde

- CSV, "Ekrandakiler", "Tümü", rapor PDF'leri, fiyat listesi gibi ara sıra
  kullanılan eylemler yan yana düğme olarak DİZİLMEZ:
  `menuButton('Dışa aktar', [...])` içine girer.
- Ekranda en çok **bir** birincil (mavi) düğme durur: o ekranın ana işi
  ("Yeni ürün ekle", "Yeni set oluştur", toplu "Kargoya ver").
- "Yenile" görünür kalabilir (hayalet düğme).

## 3. Açıklama kutusu yok, teknik dil yok

- Varsayılan görünümde **bilgilendirme kutusu (`hintBox`/mavi `alertBox`)
  bulunmaz.** "Mağaza şu süzgeci sunmuyor", "SAYFA İÇİNDE", "uç", "geçit",
  "tarama bellekte tutulur" gibi iç ayrıntılar kullanıcıya anlatılmaz;
  gerekiyorsa koddaki yorumda durur.
- Kalan uyarılar YALNIZ eyleme dönüşen durumlardır ("3 siparişte ölçü eksik —
  Ölçü gir") ve **tek satırdır**.
- Hata durumları kalır (K7) ama kısa: ne oldu + ne yapılabilir.

## 4. Az sekme

- Bir ekranda en çok **üç** üst sekme. Kurulum/yapılandırma sekmeleri tek bir
  **"Ayarlar"** sekmesinde toplanır (içinde alt başlıklar olabilir).

## 5. Tablo dar

- Varsayılan en çok **yedi sütun**. Ara toplam/KDV/indirim gibi kırılımlar
  satır detayına (çekmece) taşınır.
- Satırın ANA eylemi satırda görünür bir düğmedir ("Kargoya ver"); yıkıcı ya
  da seyrek eylemler (Sil, kopyala) satırda DURMAZ, detayda durur.

## 6. Üst özet kısa

- KPI şeridi en çok **dört** kutu. Çiplerde zaten sayısı yazan bir değer KPI
  olarak tekrarlanmaz.
- Çip satırı en çok **altı** çip; en çok kullanılanlar.

## 7. Dil

- Kısa, Türkçe, günlük dil. Düğme fiille başlar ("Kargoya ver", "İade oluştur").
- Gerekçe gereken yerde (ADR 0012) hazır gerekçe çipleri sunulur
  (`confirmWithReason(root, { presets: [...] })`); alan yine ≥10 karakter ister.

## Dokunulmayanlar

- Güvenlik kapıları (gerekçe, kuru prova, izinler — K9, ADR 0012) aynen kalır.
- Özellik silinmez; yalnız varsayılan görünümden kalkar ya da menüye/detaya
  taşınır.
