"""Çekirdeğin kimlik göçleri.

ADR 0016 (giriş şifre ile) **reddedildi**, ama göçü bu dosyada koştu ve GERİ
ALINMADI: uygulanmış bir göçü geri sarmak veri riskidir. Sütun ve izin adları
bu yüzden "password" der; tutulan değer 6 haneli PIN'dir (bkz. `identity.py`
başlığı — "adı şifre, kuralı PIN").

`km_core/store/db.py` içindeki `CORE_SCHEMA` yalnız **yeni** bir veritabanını
kurar: `CREATE TABLE IF NOT EXISTS` var olan tabloya sütun eklemez. Zaten
çalışmış bir kurulumda `password_hash`, `secret_lookup` ve `password_set_at`
sütunları bu dosyadaki göçlerle açılır.

İki kural bağlayıcıdır:

  · **Hiçbir kayıt kaybolmaz.** `pin_hash` / `pin_lookup` / `pin_set_at`
    DÜŞÜRÜLMEZ; yalnız giriş yolunda okunmaz hâle gelir. Yeni sütunda sırrı
    olmayan kullanıcının ESKİ SIRRI yeni sütuna kopyalanır
    (`0006_backfill_secret_lookup`) ve kişi orijinal PIN'iyle girmeye devam eder
    — kimse kilitlenmez, kimseden yeni sır istenmez.
  · **Göçler iki kez uygulanmaz.** `schema_migrations` tablosu (`owner='core'`)
    zaten vardır; hangi göçün uygulandığı oradan okunur.

Her göç, çalıştırılmadan önce veritabanının GERÇEK hâline bakar: yeni kurulumda
sütun `CORE_SCHEMA` ile zaten gelmiştir ve o adım atlanır. Böylece aynı kod hem
yeni hem eski veritabanında çalışır.

Dosyada şema göçü olmayan İKİ göç var: `0008_restore_bld_staff_core` ve
`0009_bld_staff_bbd_ayrimi` birer YETKİ göçüdür. Burada durmalarının sebebi
`0003`ünkiyle aynı: izin atamaları veritabanında yaşar, kaynak dosyayı
değiştirmek onları tek başına geri getirmez/götürmez ve açılışta koşan tek yol
budur.

`0007_narrow_bld_staff_core` BU LİSTEDE ARTIK YOK. Daraltma kararı 17.08.2026'da
REDDEDİLDİ ve göç kaldırıldı; ama koştuğu makinelerde `schema_migrations`
satırı DURUYOR — uygulanmış bir göçün kaydı silinmez, tarih öyle. Sildiği
dokuz satırı `0008` geri koyar.

**"EKLEYEREK GERİ AL" KURALININ SINIRI.** Kural bir VERİ kuralıdır: bir kaydın
(öğrenci, satış, fiş) geri alınması ters kayıtla yapılır, satır silinmez.
`0009` bu kuralın kapsamında DEĞİLDİR ve bilerek satır siler. Sebep: istenen
şey bir kaydın geri alınması değil, bir YETKİNİN daraltılmasıdır; yetkinin
karşılığı `role_permissions` satırının varlığıdır ve "yetkiyi ekleyerek geri
almak" diye bir şey yoktur. `0007` bunun için reddedilmedi — o, kararın
KENDİSİ reddedildiği için kaldırıldı. Silinen her satır denetim izine yazılır;
göçün bıraktığı iz, silinen satırın yerini tutan kayıttır.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from km_core.store.db import Store

log = structlog.get_logger("km.security")

OWNER = "core"


async def table_columns(store: Store, table: str) -> set[str]:
    rows = await store.fetch_all(f"PRAGMA table_info({table})")
    return {str(row["name"]) for row in rows}


async def _password_columns(store: Store) -> str:
    """Yeni sır sütunlarını EKLER, eski PIN sütunlarına dokunmaz."""
    columns = await table_columns(store, "users")
    parts: list[str] = []
    if "password_hash" not in columns:
        parts.append("ALTER TABLE users ADD COLUMN password_hash TEXT;")
    if "secret_lookup" not in columns:
        parts.append("ALTER TABLE users ADD COLUMN secret_lookup TEXT;")
    if "password_set_at" not in columns:
        parts.append("ALTER TABLE users ADD COLUMN password_set_at TEXT;")
    # `ALTER TABLE` ile UNIQUE sütun eklenemez; benzersizlik indeksle kurulur.
    # NULL değerler SQLite'ta benzersizlik kısıtına takılmaz — yeni sütuna
    # henüz yazılmamış kullanıcılar birbirini engellemez.
    parts.append(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_secret_lookup ON users (secret_lookup);"
    )
    return "\n".join(parts)


async def _users_revision(store: Store) -> str:
    """ADR 0020 — iyimser kilit. `expectedRevision` gönderen istemci gerçekten
    korunur; korunuyormuş gibi davranmak hiç korumamaktan kötüdür."""
    if "revision" in await table_columns(store, "users"):
        return "-- revision sütunu zaten var"
    return "ALTER TABLE users ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;"


async def _rename_set_pin_permission(store: Store) -> str:
    """`users.set_pin` → `users.set_password`.

    İzin ANAHTARI değişti; rol atamaları veritabanında durduğu için ad
    değişikliği tek başına yetmez. Önce yeni satır eklenir, sonra eskisi
    silinir: arada hiçbir rol yetkisiz kalmaz.

    ADR 0016 reddedilince anahtar GERİ ÇEVRİLMEDİ: bu göç koşmuş kurulumlarda
    ikinci bir göç yazmak demekti. `users.set_password` bugün PIN atama/sıfırlama
    yetkisidir.
    """
    del store  # bu göç veritabanının hâline bakmaz
    return """
INSERT OR IGNORE INTO role_permissions (role_id, permission)
SELECT role_id, replace(permission, 'users.set_pin', 'users.set_password')
FROM role_permissions
WHERE permission = 'users.set_pin' OR permission LIKE 'users.set_pin:%';

DELETE FROM role_permissions
WHERE permission = 'users.set_pin' OR permission LIKE 'users.set_pin:%';
"""


async def _roster_projection(store: Store) -> str:
    """ADR 0021 §2 — merkezden gelen kadro YEREL tablolara yansıtılır.

    İki şey açılır:

      · **`users.origin`.** Satır merkezin kopyası mı (`central`), yoksa bu
        kurulumda mı doğdu (`local`)? Ayırt edilemezse bir sonraki yansıtma
        yerelde elle açılmış kaydı sessizce ezer ya da siler. Varsayılan
        `local`: göç koştuğu anda tabloda ne varsa bu kurulumun kendi kaydıdır
        — merkez henüz hiçbir şey göndermemiştir.
      · **`roster_projection`.** En son hangi `revision` yansıtıldı. Her giriş
        denemesinde tüm kadroyu yeniden yazmamak için buraya bakılır.

    Yansıtmanın kendisi `km_core/security/roster_projection.py` içindedir.
    """
    parts: list[str] = []
    if "origin" not in await table_columns(store, "users"):
        parts.append("ALTER TABLE users ADD COLUMN origin TEXT NOT NULL DEFAULT 'local';")
    parts.append("""
CREATE TABLE IF NOT EXISTS roster_projection (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    revision      INTEGER,
    users         INTEGER NOT NULL DEFAULT 0,
    projected_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_origin ON users (origin);
""")
    return "\n".join(parts)


async def _identity_audit_queue(store: Store) -> str:
    """ADR 0021 §5 — merkeze itilemeyen denetim kaydı YERELDE BİRİKİR.

    ADR'nin cümlesi kesindir: kayıt "yerelde birikir ve yeniden denenir; asla
    düşürülmez". Gönderim anında kaybolan bir kayıt, "kim ne yaptı" sorusunu
    tam da ağın koptuğu anlar için cevapsız bırakırdı.

    Tablo çekirdeğin deposundadır çünkü denetim izi (`audit_log`) da oradadır;
    kuyruk onun gönderilmeyi bekleyen kuyruğudur. Kuyruğu işleyen kod
    `km_platform/identity_sync/queue.py` içindedir — tablo burada açılır çünkü
    açılışta koşan tek göç yolu budur.

    `next_attempt_at` geri çekilmeli yeniden denemeyi taşır: merkez kapalıyken
    her saniye yeniden denemek ne kaydı kurtarır ne de ağı.
    """
    del store  # bu göç veritabanının hâline bakmaz
    return """
CREATE TABLE IF NOT EXISTS identity_audit_queue (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entry            TEXT NOT NULL,      -- tek denetim kaydı, JSON
    queued_at        TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TEXT NOT NULL,
    last_error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_identity_audit_queue_next
    ON identity_audit_queue (next_attempt_at);
"""


async def _backfill_secret_lookup(store: Store) -> str:
    """Reddedilmiş ADR 0016'nın son kalıntısını ONARIR — 17.08.2026.

    0016 reddedildiğinde "mevcut kullanıcı ilk girişte sır belirlemeye zorlanır"
    adımı kodda unutuldu. Gerçek bir kurulumda o akış devreye girdi, kullanıcının
    orijinal PIN'i yerine YENİ bir sır yazdı ve `secret_lookup` değiştiği için
    orijinal PIN o günden sonra reddedildi. Zorlama akışı ve girişteki
    `pin_lookup` yedek yolu kaldırıldı; geriye TEK bir soru kalıyor: yeni sütuna
    hiç yazılmamış, sırrı hâlâ eski sütunda duran satırlar ne olacak?

    Cevap: eski sır YENİ sütuna KOPYALANIR. Argon2 özeti olduğu gibi taşınır —
    düz PIN hiçbir yerde yok, yeniden hash'lenemez; `pin_lookup` da aynı pepper
    ile üretilmiş aynı biçimde bir HMAC olduğundan `secret_lookup` yerine
    doğrudan geçer. Kullanıcı ORİJİNAL PIN'iyle girmeye devam eder ve kimseden
    yeni sır istenmez.

    HİÇBİR SATIR SİLİNMEZ, HİÇBİR SÜTUN DÜŞÜRÜLMEZ: `pin_hash` / `pin_lookup` /
    `pin_set_at` okundukları yerde durur, yalnız kopyalanır.

    İki eleme bilerek vardır:

      · `pin_hash <> ''` — `Identity.create_user` ve kadro yansıtması sırrı
        OLMAYAN satıra `pin_hash = ''`, `pin_lookup = 'pin-yok:<id>'` yer
        tutucusunu yazar. O yer tutucuyu sır sütununa taşımak, sırsız kaydı
        sırlıymış gibi göstermek olurdu.
      · Çakışan satır ATLANIR. `secret_lookup` UNIQUE'tir; eski sırrı başka bir
        kullanıcının bugünkü PIN'iyle aynı olan satır (eski yol benzersizliği
        denetlemiyordu, mümkün) yazılamaz. Göçü patlatmak tüm kurulumu açılışta
        düşürürdü — satır olduğu gibi bırakılır, loga düşer, çözüm yöneticinin
        PIN'i sıfırlamasıdır.
    """
    kalanlar = await store.fetch_all(
        "SELECT id FROM users WHERE password_hash IS NULL AND pin_hash IS NOT NULL "
        "AND pin_hash <> ''"
    )
    if kalanlar:
        log.info("0016 kalıntısı onarılıyor: eski sır yeni sütuna taşınıyor",
                 users=len(kalanlar))
    catisan = await store.fetch_all(
        "SELECT u.id FROM users u WHERE u.password_hash IS NULL AND u.pin_hash IS NOT NULL "
        "AND u.pin_hash <> '' AND EXISTS ("
        "  SELECT 1 FROM users o WHERE o.secret_lookup = u.pin_lookup AND o.id <> u.id)"
    )
    for row in catisan:
        log.warning(
            "eski sır başka kullanıcının PIN'iyle çakışıyor; satır olduğu gibi "
            "bırakıldı, PIN sıfırlanmalı",
            user=str(row["id"]),
        )

    return """
UPDATE users
SET password_hash   = pin_hash,
    secret_lookup   = pin_lookup,
    password_set_at = pin_set_at
WHERE password_hash IS NULL
  AND pin_hash IS NOT NULL
  AND pin_hash <> ''
  AND NOT EXISTS (
      SELECT 1 FROM users o
      WHERE o.secret_lookup = users.pin_lookup AND o.id <> users.id
  );
"""


# Kaldırılmış `0007_narrow_bld_staff_core` göçünün SİLDİĞİ dokuz satır.
# DONMUŞ LİSTEDİR: `permissions.py`den türetilmez. Bu liste bir katalog değil,
# bir OLAYIN kaydıdır — o gün hangi satırlar gitmişse onlar. Katalogdan
# türetilseydi, yarın çekirdeğe eklenen bir anahtar bu göçün geriye dönük
# olarak "onu da geri koymuş" görünmesine yol açardı.
#
# Yalnız `grant_defaults()` biçimi yazılıdır (kapsamlı izin `izin:*`), çünkü
# `0007` de yalnız o biçimi silmişti. Elle yazılmış dar kapsamlı satır
# (`servers.view:bld` gibi) hiç silinmediği için geri konacak bir şey de yok.
BLD_STAFF_CORE_RESTORED = (
    "servers.view:*",
    "ssh.execute:*",
    "ssh.transfer:*",
    "database.view:*",
    "database.query:*",
    "database.write:*",
    "database.backup:*",
    "directory.view",
    "directory.view_external",
)


async def _restore_bld_staff_core(store: Store) -> str:
    """`0007`in aldığı dokuz çekirdek satırını GERİ KOYAR — 17.08.2026.

    KARAR REDDEDİLDİ. `bld_staff` BLD'ye dair her şeyi yapar; daraltma bir
    gün yürürlükte kaldı ve geri alınıyor. Tek istisna KDS cihaz ayarlarıdır
    ve onun çaresi bu göç değil, `bld_kds.settings` adında YENİ bir izin
    anahtarıdır: yeni anahtar hiç kimseye verilmemiş olarak doğar, bu yüzden
    onun için silinecek satır da yoktur (K6 — çare modülde, çekirdekte değil).

    GERİ ALMA EKLEYEREK YAPILIR. Bu göç `INSERT OR IGNORE` dışında hiçbir şey
    yapmaz: tek bir satır bile silmez, güncellemez. Kullanıcının kuralı budur
    ve `0007`in kendisi tam da bu kurala aykırı olduğu için reddedildi.

    ZATEN VAR OLAN SATIR İKİNCİ KEZ YAZILMAZ ve iz de bırakmaz — göç iki kez
    koşsa (ya da yönetici satırı elle geri vermiş olsa) sonuç aynıdır. Denetim
    izi bu yüzden `WHERE NOT EXISTS` ile süzülür: gerçekten geri konan satır
    kadar `roles.manage` kaydı düşer, ne bir eksik ne bir fazla.

    TEK ÇARE BU GÖÇ DEĞİLDİR, ama tek AÇIK olan çare budur. `permissions.py`
    HEAD'e döndüğü için `grant_defaults(CORE_PERMISSIONS)` bir sonraki açılışta
    aynı dokuz satırı zaten sessizce eklerdi (`http/app.py` — göçlerden sonra
    koşar). "Sessizce" sorunun kendisi: bir gün geri alınan yetki, ertesi gün
    hiçbir yere yazılmadan geri gelirdi. Göç önce koşar, satırları o koyar ve
    denetim izine kimin ne aldığını yazar; `grant_defaults` arkadan gelip
    yapacak iş bulamaz.

    `0007` KAYDI SİLİNMEZ. Koşmuş bir göçün `schema_migrations` satırı tarihin
    kendisidir; silmek "bu hiç olmadı" demek olurdu. Kaldırılan yalnız kodudur.

    KULLANICI SATIRI HİÇ OKUNMAZ. Dokunulan tek tablo `role_permissions`.
    """
    del store  # bu göç veritabanının hâline SQL içinde bakar, Python'da değil
    liste = ", ".join(f"('{entry}')" for entry in BLD_STAFF_CORE_RESTORED)
    return f"""
INSERT INTO audit_log (at, user_id, action, scope, result, detail)
SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), NULL, 'roles.manage', NULL, 'ok',
       '0008_restore_bld_staff_core: ' || geri.column1 || ' -> bld_staff'
FROM (VALUES {liste}) AS geri
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions
    WHERE role_id = 'bld_staff' AND permission = geri.column1
);

INSERT OR IGNORE INTO role_permissions (role_id, permission)
SELECT 'bld_staff', geri.column1 FROM (VALUES {liste}) AS geri;
"""


# `bld_staff`tan ALINAN on dört BBD anahtarı — 17.08.2026 kullanıcı kararı:
# "bld personeli bbd ekranlarını da göremesin."
#
# DONMUŞ LİSTEDİR: manifestlerden türetilmez. Bu liste bir katalog değil, bir
# KARARIN kaydıdır — o gün `bld_staff`ta duran BBD anahtarları bunlardır.
# Türetilseydi, yarın `bbd_*` altına eklenen bir anahtar bu göçün geriye dönük
# olarak "onu da almış" görünmesine yol açardı; üstelik manifest zaten
# daraltıldığı için türetme bugün BOŞ küme döndürürdü ve göç hiçbir şey yapmazdı.
#
# `grant_defaults()` biçimi yazılıdır. Anahtarların hiçbiri kapsamlı değildir,
# bu yüzden satırlar sade anahtardır (`bbd_students.view`). Elle yazılmış
# kapsamlı bir satır (`bbd_students.view:bbd` gibi) hiç yazılmamıştır ve
# yazılsaydı da yetki VERMEZDİ: `has_permission` kapsamsız bir izni yalnız sade
# anahtarla karşılar (bkz. `identity.py`).
BLD_STAFF_BBD_REVOKED = (
    "bbd_bulk_sale.manage",
    "bbd_bulk_sale.view",
    "bbd_canteen_backups.view",
    "bbd_canteen_products.manage",
    "bbd_canteen_products.view",
    "bbd_canteen_reports.view",
    "bbd_class_schedule.view",
    "bbd_lunch.manage",
    "bbd_lunch.view",
    "bbd_payment_request.view",
    "bbd_sms.view",
    "bbd_students.manage",
    "bbd_students.qr",
    "bbd_students.view",
)


async def _revoke_bld_staff_bbd(store: Store) -> str:
    """BLD personelinin BBD ekranlarını KAPATIR — 17.08.2026 kullanıcı kararı.

    Manifestler aynı gün daraltıldı, ama daraltma KURULU sistemde tek başına
    yürürlüğe girmez: `grant_defaults()` yalnız ekler ve bir kez açılmış her
    makinede on dört satır `role_permissions` içinde durmayı sürdürür. Ekran
    menüde kalır, `/api/bbd_*` uçları açık kalır — K9'un iki kapısı da açık.
    Kapatan tek şey bu göçtür.

    SATIR SİLER; dosyadaki tek silen göç budur ve gerekçesi başlıkta yazılıdır.
    Daraltmanın karşılığı satırın yokluğudur: eklenerek geri alınamaz.

    YALNIZ `bld_staff` OKUNUR. `admin`, `bbd_staff`, `org_staff` ve `accountant`
    aynı anahtarların çoğunu taşır; `role_id` süzgeci her ifadede vardır ve
    tekrarı bilinçlidir — süzgeçsiz tek bir `DELETE`, BBD personelini kendi
    ekranından etmek demekti.

    İKİ KEZ KOŞSA DA BOZMAZ. `DELETE` zaten yok olanı silmez; denetim izi
    `WHERE EXISTS` ile süzülür, yani GERÇEKTEN silinen satır kadar `roles.manage`
    kaydı düşer. Yönetici satırları elle kaldırmışsa göç sessiz kalır ve
    "on dört yetki alındı" diye olmamış bir olayı yazmaz.

    SIRA ÖNEMLİ: iz önce yazılır. `DELETE` önce koşsaydı `WHERE EXISTS`
    süzgeci hiçbir satır bulamaz ve göç hiç iz bırakmadan on dört satırı
    silerdi.

    KULLANICI VE ROL SATIRINA DOKUNULMAZ. `bld_staff` rolü durur, kişilerin rol
    atamaları durur; giden yalnızca rolün BBD anahtarlarıdır.
    """
    del store  # bu göç veritabanının hâline SQL içinde bakar, Python'da değil
    degerler = ", ".join(f"('{entry}')" for entry in BLD_STAFF_BBD_REVOKED)
    liste = ", ".join(f"'{entry}'" for entry in BLD_STAFF_BBD_REVOKED)
    return f"""
INSERT INTO audit_log (at, user_id, action, scope, result, detail)
SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'), NULL, 'roles.manage', NULL, 'ok',
       '0009_bld_staff_bbd_ayrimi: ' || alinan.column1 || ' -> bld_staff geri alındı'
FROM (VALUES {degerler}) AS alinan
WHERE EXISTS (
    SELECT 1 FROM role_permissions
    WHERE role_id = 'bld_staff' AND permission = alinan.column1
);

DELETE FROM role_permissions
WHERE role_id = 'bld_staff' AND permission IN ({liste});
"""


CORE_MIGRATIONS: list[tuple[str, Callable[[Store], Awaitable[str]]]] = [
    ("0001_password_columns", _password_columns),
    ("0002_users_revision", _users_revision),
    ("0003_users_set_password_permission", _rename_set_pin_permission),
    ("0004_roster_projection", _roster_projection),
    ("0005_identity_audit_queue", _identity_audit_queue),
    ("0006_backfill_secret_lookup", _backfill_secret_lookup),
    # `0007_narrow_bld_staff_core` REDDEDİLDİ ve kaldırıldı; numarası da
    # kullanılmaz. Koştuğu makinelerde `schema_migrations` kaydı durur.
    ("0008_restore_bld_staff_core", _restore_bld_staff_core),
    # `0009` `0008`in AKSİ DEĞİLDİR: `0008` çekirdek satırlarını (sunucu,
    # veritabanı, rehber) geri verir, `0009` BBD ekranlarını alır. Kesişmezler.
    ("0009_bld_staff_bbd_ayrimi", _revoke_bld_staff_bbd),
]


async def apply_core_migrations(store: Store) -> list[str]:
    """Uygulanmamış çekirdek göçlerini sırayla işler; uygulananları döndürür."""
    applied = await store.applied_migrations(OWNER)
    fresh: list[str] = []
    for name, build in CORE_MIGRATIONS:
        if name in applied:
            continue
        await store.apply_migration(OWNER, name, await build(store))
        log.info("çekirdek göçü uygulandı", migration=name)
        fresh.append(name)
    return fresh
