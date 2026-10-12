# ADR 0028 — Servis anahtarları: makineden makineye kimlik

**Durum:** Kabul edildi · 12.10.2026
**Etkilediği:** `km_core/security` (`identity.py`, `service_keys.py`,
`audit_context.py`, `migrations.py` → `0011_service_keys`), `km_core/http/deps.py`,
`km_core/http/app.py`, `scripts/service-key.py`
**Genişlettiği:** [ADR 0007](0007-kimlik-ve-yetkilendirme.md) (kimlik ve
yetkilendirme). PIN ile giriş kuralı DEĞİŞMEZ; kişiler yine yalnız PIN'le girer.

## Bağlam

BBD Akademi API (ayrı bir sunucu, FastAPI) öğrenci/veli/yönetici mobil
uygulamasına hizmet verecek ve öğrencinin ana kaydı Kontrol Merkezi'ndedir.
Akademi KM'ye **sunucudan sunucuya** konuşmak zorunda: öğrenci dizini, kantin,
tablet, anons.

KM'nin bugün tek kimliği KİŞİDİR: 6 haneli PIN ve boşta kalınca düşen oturum.
İkisi de bir makine için yanlıştır:

- **PIN.** PIN'i olan bir makine, PIN'le giriş yapabilen bir makine demektir.
  PIN 1.000.000 ihtimallik bir sırdır; süresiz bir sunucu bağlantısının
  kimliği olamaz. Üstelik PIN benzersizdir ve bir kişinin seçebileceği bir PIN'i
  makineye vermek o PIN'i herkesten saklamak demekti.
- **Oturum.** 30 dakika boşta kalınca düşer; biri her gün yeniden açmak
  zorunda kalırdı. Uzatmak, kişi oturumlarının güvenlik kuralını gevşetmekti.

Ayrı bir yetki modeli kurmak da yanlış olurdu: modüllerin bütün uçları bugün
`requires("izin")` ile korunuyor (K9) ve yetki rollerden geliyor (K10). İkinci
bir model, her ucun iki kez düşünülmesi ve birinin unutulması demekti.

## Karar

**1. Servis sorumlusu bir kullanıcıdır.** `users.kind` sütunu eklenir:
`person` (varsayılan) ya da `service`. Servis kullanıcısının PIN'i yoktur
(`secret_lookup`/`password_hash` boş), rehberde görünmez, PIN'le giriş yapamaz,
oturum açamaz ve ona PIN atanamaz. Yetkisi YALNIZ rollerinden gelir; izin
denetimi kişininkiyle aynı yoldan geçer — `CurrentUser.has_permission`. Bütün
mevcut uçlar değişmeden çalışır.

**2. Kimliği servis anahtarıdır.** Biçim `kmsk_<önek 8>_<sır ≥32 bayt urlsafe>`.
Çekirdek tablosu `service_keys(id, user_id, name, prefix, key_hash,
allowed_cidrs, created_at, created_by, last_used_at, revoked_at)` — çekirdek
göçüyle (`0011_service_keys`) iki motorda da (SQLite/PostgreSQL) açılır.
Veritabanında anahtarın kendisi değil sha256 özeti durur; tam anahtar yalnız
üretildiği anda bir kez gösterilir. Anahtar iptal edilir, silinmez.

**3. Tek kapı, iki belirteç.** `Authorization: Bearer` ya oturum belirtecidir ya
da `kmsk_` ile başlayan anahtardır; kapı (`deps.resolve_once`) öneke bakar ve
ikisini de aynı `CurrentUser`a çözer. Oturum belirteci bu önekle başlayamaz.
`CurrentUser` geriye uyumlu iki alan kazanır: `kind` ve `on_behalf_of`.

**4. Ağ kısıtı.** Anahtar isteğe bağlı `allowed_cidrs` taşır. İstemci adresi
`X-Forwarded-For` listesinin **en sağdaki** değeridir (ters vekilin gördüğü
adres); yoksa bağlantının adresi. Listenin başı istemcinin kendi yazdığıdır ve
ağ kısıtında asla kullanılmaz.

**5. "Kimin adına" yetki vermez.** `X-KM-On-Behalf-Of` (≤160 yazdırılabilir
karakter; Türkçe ad yüzde kodlanır ya da ham UTF-8 gönderilir) yalnız servis
sorumlusunda okunur ve denetim izine yazılır. `X-Request-Id` her istekte denetim
izine geçer. `audit_log` iki sütun kazanır: `on_behalf_of`, `request_id`.
Değerler istek boyunca bağlam değişkeninde taşınır (`audit_context`), istek
bitince geri alınır.

**6. Ayrı hız sınırı.** Servis anahtarı istekleri kendi kovasında sayılır
(anahtar başına dakikada `auth.service_key_requests_per_minute`, varsayılan
1200); geçersiz anahtar denemesi kaynak başına dakikada 10 ile sınırlanır.
PIN girişinin kovasıyla karışmaz. `last_used_at` en çok dakikada bir yazılır.

**7. Yeni ön tanımlı rol: `mobile_service`.** Kişi rolü değildir; ayrı listede
durur (`BUILTIN_SERVICE_ROLES`). Çekirdek rolün yalnız VARLIĞINI garanti eder;
izinlerini onu `default_roles` ile öneren modüller verir. Çekirdekte modül adı
geçmez (K1). `admin` bütün izinleri taşıdığı için böyle bir izin `admin`e de
önerilir (`default_roles: [admin, mobile_service]`; bkz. docs/permissions.md).

**8. Anahtar yönetimi yalnız komut satırından.** `scripts/service-key.py
create|list|revoke`. HTTP ucu yoktur: süresiz bir giriş kapısı açmanın yolu
sunucuya erişimi olanın elinde kalmalıdır.

## Sonuçlar

- Mevcut her kullanıcı göçle `person` olur; hiçbir davranış değişmez.
- Servis kullanıcısı kullanıcı listesinde `kind: service` ile görünür;
  pasifleştirilirse bütün anahtarları o an çalışmaz.
- Yıkıcı işlemlerin PIN teyidi (`confirm_pin`) servis sorumlusunda her zaman
  reddedilir (PIN'i yoktur). Bu bilinçlidir: geri dönüşü olmayan işlemi bir
  makine başlatamaz.
- Anahtar kaybolursa geri getirilemez; iptal edilip yenisi üretilir.
- Anahtar tuzsuz sha256 ile saklanır: 256 bitlik rastgele sırrı sözlükle aramak
  anlamsızdır, tuz yalnız her istekte ek bir okuma olurdu.
