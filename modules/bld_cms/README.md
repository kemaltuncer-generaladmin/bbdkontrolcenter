# Site İçeriği (`bld_cms`)

Kurumsal sitenin (Next.js) beslendiği metinler: yedi içerik anahtarı, hizmet
sayfaları ve bilgi merkezi yazıları.

Grup: **BLD** · İzinler: `bld_cms.view`, `bld_cms.manage`, `bld_cms.delete`
· Sözleşme: `BLD/docs/control/cms.md` + `00-genel.md`
· Geçit: `bld.api` (K4) — metot adları `modules/bld_api/README.md`'nin donmuş
tablosundan alınır.

## Ekranın tek büyük vaadi

**"Kaydettim ama sitede yok" denmesin.** Site ISR ile önbellekleniyor; yazma
başarılı olsa bile önbellek tazelenmezse sayfa eski hâliyle durur. Sunucu bunu
bilerek bir hata saymıyor (içerik gerçekten yazıldı) ve `200` döndürüyor — o
yüzden **söylemek ekranın işi**. Her yazma yanıtı bir `revalidate` bloğu taşır
ve panel üstteki şeritte üç hâlden birini yazar:

| `status` | Ekranda | Ne yapılabilir |
|---|---|---|
| `ok` | Site tazelendi | — |
| `failed` | **Kayıt yazıldı, site tazelenmedi** | Yeniden dene |
| `skipped` | Tazeleme istenmedi | Şimdi tazele |

## Uçlar

| Metot | Yol | İzin |
|---|---|---|
| GET | `/content` | `bld_cms.view` |
| GET | `/services` · `/posts` | `bld_cms.view` |
| GET | `/revisions` · `/revisions/{id}` | `bld_cms.view` |
| PUT | `/content/{key}` | `bld_cms.manage` |
| POST/PATCH | `/services` · `/services/{id}` | `bld_cms.manage` |
| POST/PATCH | `/posts` · `/posts/{id}` | `bld_cms.manage` |
| POST | `/revalidate` · `/images` | `bld_cms.manage` |
| DELETE | `/services/{id}` · `/posts/{id}` | **`bld_cms.delete`** |

Silme ayrı bir izne bağlı çünkü sözleşme yumuşak silme sunmuyor: kayıt geri
gelmez ve o adrese verilen bağlantılar kırılır. Günlük ihtiyaç olan "sitede
görünmesin" `is_published = false` ile karşılanıyor ve `manage`e düşüyor.

## Bilinmesi gerekenler

- **Her yazmada `dry_run=` açıkça geçilir.** Geçidin `config/local.yaml`
  dosyası git dışıdır; bayrağı atlayan bir çağrı hiçbir şey yazmadan
  `{"ok": true}` alır ve ekran "kaydedildi" der.
- **İçerik değeri şemasızdır.** Sunucu yalnız geçerli JSON olduğunu ve 256
  KB'ı aşmadığını denetliyor; ekranın alan formu bu yüzden **veriden**
  türetilir ve her anahtarda "Ham JSON" çıkışı vardır.
- **HTML beyaz listesi üç yerde yaşıyor** ve üçü de aynı olmak zorunda:
  `apps/desktop/shell/ui-kit/richtext.js` (arayüz), `backend/content.py`
  (göndermeden önce) ve BLD'deki `HtmlSanitizer` (kaydederken). İlk ikisinin
  eşitliği `tests/test_bld_cms_content.py` içinde teste bağlıdır; **listeyi
  genişleten kişi sunucudaki aynayı da elle değiştirmelidir.**
- **`mod_bld_cms_revisions` yerel düzenleme geçmişidir**, uzakta karşılığı
  yoktur ve bir yedek değildir: geri yükleyen bir uç yok, eski sürüm
  düzenleyiciye getirilir ve normal bir yazma olarak kaydedilir.
- **Satır içi görsel yükleme ucu sözleşmede yok.** Düzenleyicideki görsel
  düğmesi bu yüzden hiç çizilmiyor; `POST /images` hazır duruyor ve geçide
  `upload_site_image` metodu eklendiği gün kendiliğinden açılır.

## Doğrulama

```bash
cd "Kontrol Merkezi"
.venv/bin/python -m pytest modules/bld_cms
.venv/bin/ruff check .
node --check modules/bld_cms/ui/panel/index.js
```
