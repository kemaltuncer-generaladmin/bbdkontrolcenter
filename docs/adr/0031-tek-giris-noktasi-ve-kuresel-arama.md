# ADR 0031 — Tek giriş noktası (`ui.nav.replacedBy`) ve küresel arama

**Durum:** Kabul edildi · 13.10.2026
**Etkilediği:** `docs/schemas/module.schema.json` (`ui.nav.replacedBy`),
`apps/desktop/shell/ui-kernel.js` (`menuPanels`, `searchProviders`,
`resetProviders`, yetenek bağlamında `capability`), `apps/desktop/shell/app.js`,
`apps/desktop/shell/global-search.js` (yeni), `apps/desktop/shell/index.html`,
`apps/desktop/shell/style.css`, `tools/build-ui-registry.py`
**K6 istisnası:** kullanıcı 13.10.2026 18:35 "KM çekirdeğiyle de ilgilenebilirsin"
dedi (iş paketi km-tek-ogrenci). Değişiklikler küçük, kuralları genişletmez
(K1 korunur: kabukta modül adı yok).

## Bağlam

Kullanıcı: "İki çekirdek tek yere baksın, senkron olsun. Adam bir öğrenciyi
güncellemek için saatlerce uğraşıp durmasın."

0.5.2'de öğrenci yönetimi BBD Akademi → Öğrenciler'e taşındı, ama menüde iki
satır kaldı: "Öğrenci Yönetimi" (yalnız yönlendiriyordu) ve "BBD Akademi".
Menü öğesini kaldırmak modülün `ui.nav` bloğunu silmek demekti; kabuk ise
`nav`ı olmayan modülün paneli hiç yüklemiyor, yani o modülün arayüz
yetenekleri (`bbd_students.manager`, `.record`) de kayboluyordu — Akademi
öğrenci listesini çizemezdi. Ayrıca bir öğrenciyi bulmanın tek yolu ilgili
ekrana gidip orada aramaktı (7 etkileşim).

## Karar

**1. `ui.nav.replacedBy: <arayüz yeteneği>`.** Bir ekranın işi başka bir
ekrana taşındıysa manifest bunu söyler. Kabuk (`menuPanels`) o yeteneği
KENDİSİ DIŞINDA görünür bir ekran ilan ediyorsa ekranı menüde çizmez; ekran
kayıtta kalır — yetenekleri yüklenir, `ctx.open` ile açılabilir (eski derin
bağlantılar). Yetenek hiçbir görünür ekranda yoksa (sağlayan modül kapalı ya da
kullanıcı göremiyor) ekran menüde kalır: iş ortada kalmaz (K7). Karar iki
manifest alanından çıkar; kabuk modül adı bilmez (K1).

**2. Küresel arama.** Üst çubukta tek "Ara" kutusu; `/` her ekrandan açar.
Sonuçları adı `.search` ile biten ARAYÜZ yetenekleri verir:
`{label, search(metin, {limit}) → [{id, title, detail?, badge?, open()}]}`.
Ekran adları da aynı kutuda aranır. Sağlayıcı patlarsa ötekiler gelir (K7).
Erişilebilirlik: WAI-ARIA combobox + listbox; `aria-activedescendant`,
`role=status` ile sonuç sayısı.

**3. Yetenek bağlamında `capability`.** `capabilities(ctx)` artık
`ctx.capability(ad)` da alır (çağrı anında çözülür): bir yetenek başka bir
yeteneğe bakabilir ("kartı aç" yeteneği kartın nerede durduğunu sorar).
Oturum kapanınca yetenek kaydı boşaltılır (`resetProviders`) — bir sonraki
kişi öncekinin aramasını devralmasın.

**4. Görünürlük yetki değildir (K9).** Menü süzgeci değişmedi (`requires`
herhangi biri). Arama uçları kendi izinleriyle korunur
(`GET /api/bbd_students/search` → `bbd_students.view`).

## Sonuçlar

- "Öğrenci Yönetimi" menüden kalktı (`replacedBy: bbd_akademi.students`);
  BBD Akademi menüsü `bbd_students.view` taşıyana da görünür ve Akademi izni
  olmayan kullanıcıya yalnız "Öğrenciler" bölümünü (KM kaydı) açar.
- Bir öğrencinin tek alanını değiştirmek: `/` → ad → Enter (kart "Kayıt ve
  veliler"de açılır) → alana tıkla → yaz → Enter. Eskiden menü → bölüm → arama
  → satır → sekme → alan → Kaydet.
- Eski sürüm kabuğu (`replacedBy`'yi tanımayan) ekranı menüde gösterir ve ekran
  Akademi'ye yönlendirir (0.5.2 davranışı) — geriye uyumlu.
