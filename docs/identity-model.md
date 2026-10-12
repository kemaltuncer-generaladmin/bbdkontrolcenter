# Kimlik Veri Modeli ve `identity` Yeteneği (SABİT)

Gerekçe: [adr/0007-kimlik-ve-yetkilendirme.md](adr/0007-kimlik-ve-yetkilendirme.md)
· İzinler: [permissions.md](permissions.md)

> **Adı şifre, kuralı PIN.** [ADR 0016](adr/0016-giris-sifre-ile.md) (kişiye özel
> şifreyle giriş) reddedildi ve giriş 6 haneli PIN'de kaldı; ancak 0016'nın göçü
> koşmuştu. `password_hash`, `secret_lookup`, `password_set_at` sütunları ve
> `users.set_password` izin anahtarı ADI DEĞİŞMEDEN kaldı — geri çevirmek ikinci
> bir göç ve veri riski demekti. Aşağıda bu adlar geçtiğinde tutulan şey PIN'dir.

Kimlik çekirdeğe aittir (`km_core/security` + `km_core/store`). Modül değildir,
kapatılamaz. Modüller kullanıcı bilgisine `km_sdk` üzerinden `identity`
yeteneğiyle erişir.

---

## Kullanıcı kaydı

Alanlar iç rehberi besleyecek biçimde baştan tanımlıdır; rehber modülü
geldiğinde veri modeli değişmez.

| Alan | Tip | Zorunlu | Not |
|---|---|:---:|---|
| `id` | UUID | ✓ | Değişmez |
| `first_name` | metin | ✓ | Ad |
| `last_name` | metin | ✓ | Soyad |
| `title` | metin | | Unvan / görev |
| `department` | metin | | Birim |
| `org_scope` | sabit liste | ✓ | `bbd` · `bld` · `org` — kişinin bağlı olduğu taraf |
| `phone_mobile` | metin | | Cep telefonu |
| `phone_ext` | metin | | Dahili numara |
| `email` | metin | | |
| `photo` | dosya ref | | |
| `note` | metin | | |
| `directory_visible` | boole | ✓ | İç rehberde görünsün mü (varsayılan: evet) |
| `roles` | rol id listesi | ✓ | **Birden fazla olabilir.** En az bir rol zorunlu |
| `status` | sabit liste | ✓ | `active` · `disabled` |
| `password_hash` | metin | ✓ | Argon2id. Düz PIN hiçbir yerde saklanmaz |
| `secret_lookup` | metin | ✓ | Sabit anahtarlı arama hash'i — girişte kullanıcıyı bulmak için |
| `password_set_at` | zaman | ✓ | |
| `failed_attempts` | tam sayı | ✓ | Ardışık başarısız deneme |
| `locked_until` | zaman | | Kilit bitiş zamanı |
| `last_login_at` | zaman | | |
| `created_at` / `updated_at` | zaman | ✓ | |
| `created_by` | UUID | | Kaydı ekleyen yönetici |
| `kind` | sabit liste | ✓ | `person` (varsayılan) · `service` — ADR 0028. Servis kaydının PIN'i yoktur |

Kısıtlar:
- `secret_lookup` **benzersizdir.** Aynı PIN ikinci kullanıcıya atanamaz;
  çakışma denemesi hata verir. Hata **kiminle çakıştığını söylemez** — aksi hâli,
  yöneticinin deneme yoluyla başkasının PIN'ini öğrenmesine kapı açardı.
- `status = disabled` olan kullanıcı giriş yapamaz, rehberde görünmez.
- Son `admin` rolü taşıyan aktif kullanıcı pasifleştirilemez ve rolü alınamaz.
- `org_scope` kişinin nereye bağlı olduğunu söyler; **yetkiyi belirlemez.**
  Yetki rollerden gelir. İkisi karıştırılmaz.

## Rol kaydı

| Alan | Tip | Not |
|---|---|---|
| `id` | metin | `admin`, `bld_staff`, `bbd_staff`, `org_staff` veya yeni tanım |
| `name` | metin | Görünen ad |
| `description` | metin | |
| `permissions` | liste | `izin:kapsam` biçiminde. `*` tüm kapsamlar |
| `builtin` | boole | Ön tanımlı dört rol `true`. Silinemez, izinleri düzenlenebilir |

## PIN

- **En az 6 hane, yalnızca rakam.** Uzunluk `config/default.yaml` →
  `auth.pin_min_length` ile gelir.
- Argon2id ile hash'lenir. Ek olarak sabit anahtarlı (pepper) `secret_lookup`
  üretilir — giriş anında tüm kullanıcıları tarayarak deneme yapılmaz.
- Pepper kasada durur, veritabanında değil.
- Yönetici PIN atar/sıfırlar (`users.set_password` — anahtarın adı göçten
  kalmıştır); kullanıcı kendi PIN'ini değiştirebilir, bunun için mevcut PIN'ini
  girer.
- Basit PIN'ler (`123456`, `000000`, tekrar eden/ardışık diziler) reddedilir.
- **Göç yolu.** `password_hash` sütunu boş olan (0016 göçünden önce açılmış)
  kullanıcı eski `pin_hash` sütunuyla doğrulanır ama **oturumu açılmaz**: ilk
  girişinde kendi PIN'ini kurar, PIN yeni sütunlara yazılır. Hiçbir satır
  silinmez, kimse kilitlenmez.

## Giriş akışı

```
PIN girilir
  → kilitli mi / oran sınırı aşıldı mı?  → evet: reddet, denetime yaz
  → secret_lookup ile kullanıcı bulunur  → yok: sabit süreli sahte doğrulama, reddet
  → Argon2id doğrulaması                 → hatalı: failed_attempts++, gerekirse kilitle
  → status active mi?                    → hayır: reddet
  → oturum açılır: kullanıcı + rollerin birleşimi = etkin izin kümesi
  → arayüz izin kümesine göre menüyü kurar
```

Kullanıcı bulunamadığında da doğrulama süresi harcanır — yanıt süresinden
"bu PIN kimseye ait değil" bilgisi sızdırılmaz. Hata mesajı her durumda aynıdır.

## Oturum

- Oturum belirteci çekirdek tarafından verilir, süresi ayarlanabilir.
- Boşta kalma süresi dolunca kilitlenir; yeniden PIN istenir.
- Yıkıcı işlemler açık oturumda bile PIN teyidi ister.
- Rol veya izin değişirse ilgili kullanıcının etkin izin kümesi anında
  yenilenir; oturumu kapatmak gerekmez.

## `identity` yeteneği (modüllerin gördüğü yüzey)

Modüller `km_sdk` üzerinden erişir. Doğrudan kullanıcı tablosu okumak yasaktır
(K4 — tek kapı).

| Çağrı | Döner | Not |
|---|---|---|
| `current_user()` | kullanıcı özeti | id, ad, soyad, `org_scope`, roller |
| `has_permission(key, scope)` | boole | Yetki denetiminin **tek** yolu |
| `require_permission(key, scope)` | — | Yoksa reddeder |
| `list_users(filter)` | kullanıcı özeti listesi | `users.view` ister |
| `directory_entries()` | rehber kaydı listesi | `directory_visible = true` olan aktif kullanıcılar. İç rehberin kaynağı |

**Kural:** Modül `has_permission` sorar, rol adı sormaz.

## Servis sorumlusu ve servis anahtarı (ADR 0028)

Başka bir sunucu KM'ye kişi gibi değil, **servis anahtarıyla** bağlanır.

- Servis kullanıcısı `kind = 'service'` olan bir kullanıcı kaydıdır: PIN'i
  yoktur, giriş yapamaz, oturum açamaz, ona PIN atanamaz, rehberde görünmez.
  Yetkisi yalnız rollerinden gelir (ör. `mobile_service`).
- Anahtar biçimi `kmsk_<önek 8>_<sır ≥32 bayt>`. `Authorization: Bearer` ile
  gönderilir ve kapı onu aynı `CurrentUser`a çözer (`kind = 'service'`).
- `service_keys` tablosu:

| Alan | Not |
|---|---|
| `id` | UUID |
| `user_id` | Bağlı servis kullanıcısı |
| `name` | Anahtarın adı |
| `prefix` | 8 karakter, düz; tekil indeks. Listede ve denetimde anahtarı anar |
| `key_hash` | Tam anahtarın sha256 özeti. Anahtarın kendisi saklanmaz |
| `allowed_cidrs` | JSON liste; boş = her adres. İstemci adresi en sağdaki `X-Forwarded-For` |
| `created_at` / `created_by` | |
| `last_used_at` | En çok dakikada bir yazılır |
| `revoked_at` | İptal; satır silinmez |

- Yönetim yalnız komut satırından: `scripts/service-key.py create|list|revoke`.
  Tam anahtar yalnız `create` çıktısında bir kez yazılır.
- `X-KM-On-Behalf-Of` (≤160 karakter, Türkçe ad yüzde kodlanır) ve
  `X-Request-Id` denetim izinin `on_behalf_of` / `request_id` sütunlarına
  yazılır. Yetki vermez; kişi oturumunda `on_behalf_of` yok sayılır.
- Hız sınırı PIN girişinden ayrıdır: anahtar başına dakikada
  `auth.service_key_requests_per_minute` (varsayılan 1200), geçersiz anahtar
  denemesi kaynak başına dakikada 10.

## Denetim izi

Şunlar her durumda yazılır: giriş başarısı ve başarısızlığı, kilitlenme, PIN
atama/sıfırlama/değiştirme, kullanıcı ekleme/düzenleme/pasifleştirme, rol ve
izin değişikliği, yetki reddi, yıkıcı işlemler.

Kayıtta kim, ne zaman, hangi işlem, hangi kapsam ve sonuç bulunur. Servis
anahtarıyla gelen istekte ayrıca "kimin adına" (`on_behalf_of`) ve istek
kimliği (`request_id`) bulunur (ADR 0028).
**PIN ve sır değerleri denetim iziyle birlikte yazılmaz.**

## Rehber — ileriye dönük not

- **İç rehber**, kullanıcı kayıtlarından türer (`directory_entries()`). Ayrı
  veri tutulmaz, tek kaynak kullanıcı kaydıdır.
- **Dış rehber**, kurum dışı kişi/firma kayıtlarıdır; kullanıcı değildir, giriş
  yapamaz, ayrı tabloda durur.
- İkisi rehber modülünde ortak bir görünümde birleşir. Rehber bir **modüldür**
  (silinebilir iş özelliği), çekirdek değil — istendiğinde açılacak.
