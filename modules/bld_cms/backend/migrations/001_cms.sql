-- Site İçeriği modülünün YEREL tabloları.
--
-- Buraya yalnız BLD'de KARŞILIĞI OLMAYAN veri yazılır. Yedi içerik anahtarı,
-- hizmet sayfaları ve bilgi merkezi yazıları `veykemtu_site_*` tablolarındadır
-- ve KOPYALANMAZ: içerik siteye ISR ile gidiyor, sunucudaki hâli tek gerçektir
-- ve yerel bir kopya her zaman bir tur geride kalır. Ekranın yanlış metni
-- doğru gibi göstermesi, hiç göstermemesinden kötüdür.
--
-- İki şeyin karşılığı yok:
--
--  1. YAZMA DENEMESİ. BLD `veykemtu_control_audit` tutuyor ama o kayıt yalnız
--     SUNUCUYA ULAŞAN isteği bilir. Ağ koparsa, geçit patlarsa ya da istek
--     yarıda kalırsa "kim neyi denedi" sorusunun cevabı yalnız burada kalır.
--
--  2. DÜZENLEME GEÇMİŞİ. `PUT /content/{key}` değeri ÜSTÜNE yazıyor ve sunucu
--     denetim satırına bilerek yalnız künye koyuyor: "İçeriğin tam kopyasını
--     denetime yazmak, tabloyu bir sürüm deposu hâline getirirdi" (cms.md).
--     Karar doğru — ama sonucu şu: sunucuda "dün ne yazıyordu" sorusunun
--     cevabı YOK. Bir yönetici SSS listesini yanlışlıkla boşaltırsa geri
--     getirecek hiçbir şey olmazdı. Bu tablo o cevabı tutar.
--
--     TABLO BİR YEDEK DEĞİLDİR. Geri yükleyen bir uç yoktur ve olmayacaktır:
--     eski sürüm düzenleyiciye GETİRİLİR, yönetici bakar ve normal bir yazma
--     olarak — kendi gerekçesiyle — kaydeder. Sessizce geri yazan bir düğme,
--     aradaki bütün değişiklikleri de görünmez biçimde silerdi.
--
-- K5: tablo VE index adlarının hepsi `mod_bld_cms_` önekiyle başlar.

-- Yazma denemelerinin yerel izi. SATIR SİLİNMEZ.
CREATE TABLE IF NOT EXISTS mod_bld_cms_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL DEFAULT 'site_content',  -- site_content |
                                                       -- site_service | site_post
    target_id   INTEGER NOT NULL DEFAULT 0,   -- 0 = kimliği yok (içerik anahtarı,
                                              -- yeni kayıt, toplu çizdirme)
    target_key  TEXT NOT NULL DEFAULT '',     -- içerik anahtarı ya da slug.
                                              -- `veykemtu_site_content` birincil
                                              -- anahtarı bir METİNDİR; sunucu da
                                              -- onu `payload_json.key` içinde
                                              -- taşıyor (cms.md) ve burada da
                                              -- ayrı bir sütun ister.
    action      TEXT NOT NULL,                -- cms.content.update |
                                              -- cms.service.create/update/delete |
                                              -- cms.post.create/update/delete |
                                              -- cms.revalidate | cms.image.upload
    reason      TEXT NOT NULL DEFAULT '',
    actor       TEXT NOT NULL DEFAULT '',     -- oturumdan gelir, gövdeden DEĞİL
    result      TEXT NOT NULL DEFAULT '',     -- denendi | ok | dry_run |
                                              -- engellendi | hata
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_cms_audit_target
    ON mod_bld_cms_audit (target_type, target_id, created_at);

CREATE INDEX IF NOT EXISTS mod_bld_cms_audit_time
    ON mod_bld_cms_audit (created_at);

-- Düzenleme geçmişi. YALNIZ GERÇEK YAZMADAN SONRA yazılır; kuru provada
-- yazılmaz, çünkü BLD'de hiçbir şey değişmedi ve olmamış bir değişikliği kayda
-- geçirmek geçmişi yalancı yapardı.
--
-- `before_json` yazmadan HEMEN ÖNCE sunucudan taze okunan hâldir, panelin
-- elindeki satır değil: ekranda açık duran form yarım saat önce okunmuş
-- olabilir ve o aralıkta başkası yazmış olabilir. Panelin kopyasını "önceki
-- hâl" diye saklamak, hiç var olmamış bir sürümü kayda geçirmek olurdu.
--
-- `truncated` = gövde `revision_max_bytes` sınırını aştı ve JSON yerine künye
-- yazıldı. Kesilmiş bir metni "eski hâl" diye saklamak, geri getirildiğinde
-- yarım bir sayfa üretirdi; bu yüzden kırpma yok, künyeye düşme var ve ekran
-- o satırın gövdesini GETİREMEYECEĞİNİ düğmeyi çizmeden önce bilir.
CREATE TABLE IF NOT EXISTS mod_bld_cms_revisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,                -- site_content | site_service | site_post
    target_id   INTEGER NOT NULL DEFAULT 0,   -- içerik anahtarında 0
    target_key  TEXT NOT NULL DEFAULT '',     -- içerik anahtarı ya da slug
    title       TEXT NOT NULL DEFAULT '',     -- listede okunacak ad
    action      TEXT NOT NULL,                -- sunucudaki denetim eylemiyle aynı ad
    before_json TEXT NOT NULL DEFAULT 'null', -- yazmadan önceki TAZE hâl
    after_json  TEXT NOT NULL DEFAULT 'null', -- silmede null
    truncated   INTEGER NOT NULL DEFAULT 0,   -- 1 = gövde saklanmadı, künye var
    actor       TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    audit_id    INTEGER NOT NULL DEFAULT 0,   -- SUNUCUNUN denetim satırı; 0 = gelmedi.
                                              -- İki izi yan yana koymanın tek yolu.
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS mod_bld_cms_revisions_target
    ON mod_bld_cms_revisions (target_type, target_id, id);

CREATE INDEX IF NOT EXISTS mod_bld_cms_revisions_key
    ON mod_bld_cms_revisions (target_type, target_key, id);

CREATE INDEX IF NOT EXISTS mod_bld_cms_revisions_time
    ON mod_bld_cms_revisions (created_at);
