# 0017 — Çekirdek ekranlar kabukta ayrı hiyerarşide durur

**Durum:** Kabul edildi · 2026-08-16

## Bağlam

[permissions.md](../permissions.md) rol → ekran matrisi beş ekranı **çekirdeğe
ait** olarak tanımlar ve bunların hiçbiri modül değildir:

| Ekran | İzin |
|---|---|
| Kullanıcılar | `users.view` |
| Roller ve İzinler | `roles.view` |
| Ayarlar | `settings.view` |
| Denetim İzi | `audit.view` |
| Kimlik Kasası | `secrets.view` |

Beşi de matriste sabit, beşinin de **paneli yazılmamış.** Kabuk bugün yalnız
modül panelini tanıyor: `tools/build-ui-registry.py`, `modules/*/module.yaml`
dosyalarını okuyup `shell/registry.json` üretiyor ve `ui-kernel` menüyü ondan
kuruyor.

Yani çekirdek ekranı yazmanın bugün bir yolu yok.

## Karar

### 1. `shell/core-panels/` — ayrı hiyerarşi

```
apps/desktop/shell/
  core-panels/
    users/       index.js · panel.css
    roles/
    settings/
    audit/
    vault/
  panels/        ← modüllerden kopyalanan paneller (bugünkü yol)
```

`ui-kernel` iki kaynağı birleştirir ve tek bir menü kurar. Çekirdek ekranları
`registry.json`'a **girmez**; ayrı ve sabit bir listeden gelir.

### 2. Menüde ayrı grup: "Sistem"

Çekirdek ekranları modül gruplarının (BBD, BLD, BBD Store, Kurumsal) arasına
karışmaz; en altta kendi grubunda durur. Kullanıcı "bu ekran uygulamanın
kendisine mi, bir iş alanına mı ait" sorusunu menüde görerek yanıtlar.

### 3. Yetkilendirme farksızdır

Her çekirdek ekranı da `requires` ile izin ilan eder, menüde ona göre görünür
ve backend'de **aynı izin yeniden denetlenir**. K9 (çift kapı) çekirdek
ekranları için de geçerlidir; "çekirdek olduğu için güvenilir" diye bir istisna
açılmaz.

### 4. `modules/` silinse ekranlar durur

K1'in bu ADR'deki karşılığı budur: `modules/` klasörü tümüyle silindiğinde
uygulama açılmalı ve **Kullanıcılar, Ayarlar, Denetim İzi ekranları
çalışmalıdır.** Bu, çekirdek ekranlarının modül altyapısına bağlanmamasının
ölçütüdür ve testle sabitlenir.

## Elenen alternatifler

- **Çekirdek ekranlarını sahte modül olarak `modules/` altına koymak.** İlk
  bakışta bedava görünür (registry zaten çalışıyor). İki şeyi bozar: (1)
  `modules/` silindiğinde kimlik ekranı da gider — K1 ihlali, kimlik
  kapatılamaz olmalıydı; (2) `build-ui-registry.py` modül olmayan bir şeyi
  modülmüş gibi yazmaya zorlanır ve manifest doğrulaması anlamını yitirir.
- **Ekranları gerçekten modül yapmak.** ADR 0007/0016 ile doğrudan çelişir:
  kimlik çekirdektedir, `enabled: false` yapılamaz.
- **Kabuğa gömülü sabit HTML.** Giriş ekranı bugün böyle (`app.js` içinde) ve
  orada doğru — tek ekran, izin gerektirmiyor. Beş ekran için aynı yol
  `app.js`'i büyütür ve panel yaşam döngüsünü (mount/unmount, izin süzme)
  ikinci kez, farklı biçimde yazdırır.

## Sonuçlar

- `tools/build-ui-registry.py` iki kaynak üretir; çekirdek listesi elle
  bakılan sabit bir dosyadır, manifest taramasından gelmez.
- **Kabukta modül adı yine geçmez.** ARCHITECTURE §6'nın kuralı korunur:
  eklenen şey modül adı değil, çekirdeğin kendi ekranıdır.
- Çekirdek panelleri de `shell/ui-kit/` bileşenlerini kullanır (ADR 0011); ayrı
  bir bileşen seti doğmaz.
- Kimlik Kasası ekranı sırları **hiçbir zaman düz göstermez**; varlık, ad ve
  son değiştirilme bilgisi gösterir. K8 ekranda da geçerlidir.
