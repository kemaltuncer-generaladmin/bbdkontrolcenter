# Kimlik Veri Modeli ve `identity` Yeteneği (SABİT)

Gerekçe: [adr/0016-giris-sifre-ile.md](adr/0016-giris-sifre-ile.md)
(rol modeli: [adr/0007](adr/0007-kimlik-ve-yetkilendirme.md), kimlik doğrulama
bölümü 0016 ile değişti) · İzinler: [permissions.md](permissions.md)

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
| `password_hash` | metin | ✓ | Argon2id. Düz şifre hiçbir yerde saklanmaz |
| `secret_lookup` | metin | ✓ | Sabit anahtarlı arama hash'i — girişte kullanıcıyı bulmak için |
| `password_set_at` | zaman | ✓ | |
| `failed_attempts` | tam sayı | ✓ | Ardışık başarısız deneme |
| `locked_until` | zaman | | Kilit bitiş zamanı |
| `last_login_at` | zaman | | |
| `created_at` / `updated_at` | zaman | ✓ | |
| `created_by` | UUID | | Kaydı ekleyen yönetici |

Kısıtlar:
- `secret_lookup` **benzersizdir.** Aynı şifre ikinci kullanıcıya atanamaz;
  çakışma denemesi hata verir. Hata **kiminle çakıştığını söylemez** — aksi hâli,
  yöneticinin deneme yoluyla başkasının şifresini öğrenmesine kapı açardı.
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

## Şifre

- **En az 10 karakter.** Karmaşıklık dayatılmaz (büyük harf/rakam/simge
  zorunluluğu yazılmaz) — o dayatma kullanıcıyı kısa ve tahmin edilebilir
  şifrelere iter; uzunluk tek başına daha iyi korur.
- Argon2id ile hash'lenir. Ek olarak sabit anahtarlı (pepper) `secret_lookup`
  üretilir — giriş anında tüm kullanıcıları tarayarak deneme yapılmaz.
- Pepper kasada durur, veritabanında değil.
- Yönetici şifre atar/sıfırlar (`users.set_password`); kullanıcı kendi şifresini
  değiştirebilir, bunun için mevcut şifresini girer.
- Yaygın şifreler ve kişinin ad/soyad/telefonunu içeren şifreler reddedilir.
- Sıfırlama tek kullanımlık kodla yapılabilir; kod `phone_mobile` numarasına SMS
  ile gider (`notify` yeteneği). Telefon **giriş anahtarı değildir.**

## Giriş akışı

```
Şifre girilir
  → kilitli mi / oran sınırı aşıldı mı?  → evet: reddet, denetime yaz
  → secret_lookup ile kullanıcı bulunur  → yok: sabit süreli sahte doğrulama, reddet
  → Argon2id doğrulaması                 → hatalı: failed_attempts++, gerekirse kilitle
  → status active mi?                    → hayır: reddet
  → oturum açılır: kullanıcı + rollerin birleşimi = etkin izin kümesi
  → arayüz izin kümesine göre menüyü kurar
```

Kullanıcı bulunamadığında da doğrulama süresi harcanır — yanıt süresinden
"bu şifre kimseye ait değil" bilgisi sızdırılmaz. Hata mesajı her durumda aynıdır.

## Oturum

- Oturum belirteci çekirdek tarafından verilir, süresi ayarlanabilir.
- Boşta kalma süresi dolunca kilitlenir; yeniden şifre istenir.
- Yıkıcı işlemler açık oturumda bile şifre teyidi ister.
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

## Denetim izi

Şunlar her durumda yazılır: giriş başarısı ve başarısızlığı, kilitlenme, şifre
atama/sıfırlama/değiştirme, kullanıcı ekleme/düzenleme/pasifleştirme, rol ve
izin değişikliği, yetki reddi, yıkıcı işlemler.

Kayıtta kim, ne zaman, hangi işlem, hangi kapsam ve sonuç bulunur.
**Şifre ve sır değerleri denetim iziyle birlikte yazılmaz.**

## Rehber — ileriye dönük not

- **İç rehber**, kullanıcı kayıtlarından türer (`directory_entries()`). Ayrı
  veri tutulmaz, tek kaynak kullanıcı kaydıdır.
- **Dış rehber**, kurum dışı kişi/firma kayıtlarıdır; kullanıcı değildir, giriş
  yapamaz, ayrı tabloda durur.
- İkisi rehber modülünde ortak bir görünümde birleşir. Rehber bir **modüldür**
  (silinebilir iş özelliği), çekirdek değil — istendiğinde açılacak.
