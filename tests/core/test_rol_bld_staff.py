"""`bld_staff`in izin kümesi — 17.08.2026 kullanıcı kararı.

KARAR TEK CÜMLE: **BLD personeli BLD'ye dair her şeyi yapar; tek istisna KDS
CİHAZ AYARLARIDIR.**

Bu dosyanın öncülü `test_rol_daraltma.py` idi ve REDDEDİLMİŞ bir kararı
kodluyordu (rol altı ekrana, 63 izinden 7'ye iniyordu). Kayıt olsun: reddedilen
şey daraltmanın kendisiydi, ölçme biçimi değil. Sınanan söz üç parçalı kalıyor
ve üçü de ayrı ayrı kırılabilir:

  · **Manifestler.** `bld_staff`in izin kümesi tam olarak aşağıdaki 64
    anahtardır. Yeni `bld_kds.settings` bu listede YOKTUR — kararın tek
    istisnası odur.
  · **Belge.** `docs/permissions.md` → "Rol → izin matrisi" her satırda beş
    rolün ✓/✗ dağılımını yazıyor. Belge ile manifest ayrılırsa yetki tablosu
    sistemin gerçeğini anlatmayı bırakır; ikisi burada karşılaştırılır.
  · **Kurulu sistem.** Reddedilen daraltmanın göçü (`0007_narrow_bld_staff_core`)
    yerel veritabanında KOŞTU ve dokuz çekirdek satırını sildi. Kaynak dosyayı
    geri almak o satırları geri getirmez; getiren `0008_restore_bld_staff_core`
    göçüdür ve burada gerçek bir veritabanında koşturulur.

BEKLENEN KÜMELER ELLE YAZILDI. Manifestlerden türetilseydi test yalnız
"kendisiyle tutarlı" olurdu: birinin `default_roles` satırına `bld_staff`
eklemesi testi bozmaz, sessizce kabul ederdi. Sayının kendisi de yazılıdır —
64 anahtarlık bir listede tek bir satırın eksilmesi göze çarpmaz.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml

from km_core.security.migrations import (
    BLD_STAFF_CORE_RESTORED,
    CORE_MIGRATIONS,
    apply_core_migrations,
)
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.store.db import Store

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "modules"
BELGE = ROOT / "docs" / "permissions.md"

# --------------------------------------------------------------- beklenenler

#: `bld_staff`in TAM izin kümesi. Kapsamlı izinler `grant_defaults` biçiminde
#: (`izin:*`) yazılır; rolün `bld` kapsamı satırın kendisinde değil, kullanım
#: anında uygulanır.
BLD_STAFF_BEKLENEN = {
    # --- BLD ekranları: rol BLD'ye dair HER ŞEYİ yapar --------------------
    "bld_cms.manage",
    "bld_cms.view",
    "bld_customers.disable",
    "bld_customers.manage",
    "bld_customers.view",
    "bld_dashboard.manage",
    "bld_dashboard.view",
    "bld_invoices.manage",
    "bld_invoices.view",
    "bld_invoices.void",
    "bld_kds.devices",
    "bld_kds.manage",              # sipariş durumu, revizyon, cihaz adı
    "bld_kds.view",
    "bld_manual_order.manage",
    # Katalog fiyatını kırıp sepete anlaşmalı tutar yazma (17.08.2026 kullanıcı
    # kararı: "sepet başına etsin, günlük abone mantığıyla"). `manage`den AYRI
    # bir anahtar, çünkü ölçüt para: sipariş açmak rutin kayıt, fiyat kırmak
    # ciroyu değiştiren ticari karardır. Tek anahtarda kalsaydı fiyat kırmayı
    # engellemenin tek yolu sipariş girmeyi engellemek olurdu.
    #
    # `bld_staff`E VERİLİR: telefonu açan kişi pazarlığı yapan kişinin ta
    # kendisidir. Anahtarın değeri "varsayılan kısıtlı" olması değil, gerektiğinde
    # AYRILABİLİR olmasıdır — `bld_orders.cancel` ile aynı kalıp.
    "bld_manual_order.price_override",
    "bld_manual_order.view",
    "bld_menu.manage",
    "bld_menu.remove",
    "bld_menu.view",
    "bld_notifications.manage",
    "bld_notifications.publish",
    "bld_notifications.view",
    "bld_orders.cancel",
    "bld_orders.manage",
    "bld_orders.view",
    "bld_products.manage",
    "bld_products.retire",
    "bld_products.view",
    "bld_sales_settings.manage",   # kararın istisnası DEĞİL — bkz. aşağıdaki test
    "bld_sales_settings.ordering",
    "bld_sales_settings.view",
    "bld_sms.manage",
    "bld_sms.view",
    "bld_status_monitor.manage",
    "bld_status_monitor.view",
    "bld_subscriptions.manage",
    "bld_subscriptions.view",
    # --- BBD ve kantin ekranları ------------------------------------------
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
    # --- ortak ekranlar ----------------------------------------------------
    "antivirus.scan",
    "antivirus.view",
    "print.reprint",
    "print.view",
    # --- çekirdek: sunucu, veritabanı, rehber ------------------------------
    "servers.view:*",
    "ssh.execute:*",
    "ssh.transfer:*",
    "database.view:*",
    "database.query:*",
    "database.write:*",
    "database.backup:*",
    "directory.view",
    "directory.view_external",
}

#: Sayı da yazılıdır. 64 satırlık bir kümede tek bir eksilme göze çarpmaz;
#: kümenin kendisi değişirse bu sayı da elle düzeltilmek zorunda kalır.
BLD_STAFF_SAYISI = 64

#: `accountant` KARARIN DIŞINDADIR. Kullanıcı ondan hiç söz etmedi; rol
#: HEAD'deki hâlindedir ve burada yalnız "değişmediği" için duruyor.
ACCOUNTANT_BEKLENEN = {
    "bbd_bulk_sale.manage",
    "bbd_bulk_sale.view",
    "bbd_canteen_backups.view",
    "bbd_canteen_products.manage",
    "bbd_canteen_products.view",
    "bbd_canteen_reports.export",
    "bbd_canteen_reports.view",
    "bbd_class_schedule.view",
    "bbd_lunch.manage",
    "bbd_lunch.view",
    "bbd_payment_request.collect",
    "bbd_payment_request.view",
    "bbd_sms.view",
    "bbd_students.manage",
    "bbd_students.qr",
    "bbd_students.view",
    "directory.view",
    "directory.view_external",
    "store_invoices.legal_no",
    "store_invoices.view",
    "store_refunds.view",
    "store_reports.view",
    "store_tax.view",
    "store_udit_logs.view",
}

ACCOUNTANT_SAYISI = 24

#: Kararın TEK istisnası. Yeni bir anahtardır; `bld_kds.manage`ten ayrıldı.
KDS_AYAR = "bld_kds.settings"

#: `bld_staff`a VERİLMEYEN iki yıkıcı anahtar. HEAD'de de verilmemişlerdi;
#: karar bu noktada HEAD'i doğruladı, değiştirmedi.
GERI_DONUSSUZ = ("bld_cms.delete", "bld_sms.announce")

#: Belgedeki rol sütunlarının kimlik karşılığı — başlık sırasıyla.
BELGE_ROLLERI = ("admin", "bld_staff", "bbd_staff", "org_staff", "accountant")


# ------------------------------------------------------------------ yardımcı


def _manifests() -> list[dict]:
    found = []
    for path in sorted(MODULES.glob("*/module.yaml")):
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(manifest, dict), f"{path}: manifest bir eşleme değil"
        found.append(manifest)
    return found


def _entry(key: str, scoped: bool) -> str:
    """`grant_defaults()` hangi satırı yazıyorsa o."""
    return f"{key}:*" if scoped else key


def _rol_izinleri() -> dict[str, set[str]]:
    """Çekirdek + bütün manifestler → rol başına izin kümesi."""
    roles: dict[str, set[str]] = {}
    declared = [
        (permission["key"], bool(permission.get("scoped")), permission["default_roles"])
        for permission in CORE_PERMISSIONS
    ]
    for manifest in _manifests():
        for permission in manifest.get("permissions") or []:
            declared.append((
                permission["key"],
                bool(permission.get("scoped")),
                permission.get("default_roles") or [],
            ))
    for key, scoped, role_ids in declared:
        for role_id in role_ids:
            roles.setdefault(role_id, set()).add(_entry(key, scoped))
    return roles


def _izin_kayitlari() -> dict[str, dict]:
    """İzin anahtarı → ilan eden kayıt (çekirdek ve modül birlikte)."""
    records = {permission["key"]: permission for permission in CORE_PERMISSIONS}
    for manifest in _manifests():
        for permission in manifest.get("permissions") or []:
            records[permission["key"]] = permission
    return records


def _tablo_satirlari(baslik: str) -> list[list[str]]:
    """Belgedeki bir başlığın altındaki ilk markdown tablosunun veri satırları."""
    metin = BELGE.read_text(encoding="utf-8")
    parca = metin.split(baslik, 1)
    assert len(parca) == 2, f"belgede '{baslik}' başlığı yok"
    satirlar = []
    basladi = False
    for line in parca[1].splitlines():
        if line.startswith("|"):
            basladi = True
            hucreler = [cell.strip() for cell in line.split("|")]
            if set(hucreler[1].replace(" ", "")) <= {"-", ":"} and hucreler[1]:
                continue                      # ayraç satırı
            satirlar.append(hucreler)
        elif basladi:
            break
    return satirlar[1:]                       # başlık satırı atılır


def _kod_parcalari(hucre: str) -> list[str]:
    return re.findall(r"`([^`]+)`", hucre)


# ------------------------------------------------------------ manifest sözü


def test_bld_staff_kumesi_karara_uyuyor() -> None:
    izinler = _rol_izinleri()["bld_staff"]
    assert izinler == BLD_STAFF_BEKLENEN
    assert len(izinler) == BLD_STAFF_SAYISI


def test_kds_cihaz_ayarlari_kararin_tek_istisnasi() -> None:
    """Ayrım YENİ BİR ANAHTARLA yapıldı, eskisi daraltılarak değil.

    `bld_kds.manage` bölünmeseydi, kararın istisnasını uygulamanın tek yolu o
    anahtarı `bld_staff`tan almak olurdu — ve o zaman sipariş durumu
    değiştirmek de giderdi. Bölme sayesinde `bld_staff`tan HİÇBİR satır
    silinmedi: yeni anahtar ona hiç verilmemiş olarak doğdu (K6).
    """
    kayitlar = _izin_kayitlari()
    assert KDS_AYAR in kayitlar, "yeni anahtar hiçbir manifestte ilan edilmemiş"

    izinler = _rol_izinleri()["bld_staff"]
    assert KDS_AYAR not in izinler

    # Ekranın geri kalanı DURUYOR: personel siparişi ilerletir, cihazı
    # adlandırır, yıkıcı komutları bile gönderir. Alınan tek şey ayar yazmaktır.
    for key in ("bld_kds.view", "bld_kds.manage", "bld_kds.devices"):
        assert key in izinler, key

    # Anahtarı yalnız `admin` taşır; ikinci bir rol eklenmesi karar değişikliği
    # olurdu ve sessizce olmamalı.
    assert kayitlar[KDS_AYAR]["default_roles"] == ["admin"]


def test_satis_ayarlari_bld_staffta_kalir() -> None:
    """`bld_sales_settings` İSTİSNA DEĞİLDİR (kullanıcı kararı).

    İki ekranın adı da "ayar" diyor ve karışmaya en açık nokta burası. Ama
    ayrılan şey KASA (KDS cihazı) ayarıdır; satış kuralları — kesim saati,
    kapalı gün, satış şalteri — BLD'nin günlük işidir ve rolde kalır.
    """
    izinler = _rol_izinleri()["bld_staff"]
    for key in ("bld_sales_settings.view", "bld_sales_settings.manage",
                "bld_sales_settings.ordering"):
        assert key in izinler, key


def test_geri_donussuz_iki_anahtar_verilmedi() -> None:
    """`bld_cms.delete` ve `bld_sms.announce` admin'de kalır.

    İkisi de geri alınamaz: silinen içerik geri gelmez, gönderilen toplu SMS
    geri çağrılamaz. Anahtarlar ilan EDİLMİŞ olmalı — hiç var olmayan bir
    anahtarın "verilmemiş" olması bir şey kanıtlamazdı.
    """
    kayitlar = _izin_kayitlari()
    izinler = _rol_izinleri()
    for key in GERI_DONUSSUZ:
        assert key in kayitlar, f"{key} hiçbir manifestte ilan edilmemiş"
        assert key not in izinler["bld_staff"], key
        assert key in izinler["admin"], key


def test_accountant_karardan_etkilenmedi() -> None:
    """Kullanıcı `accountant`tan hiç söz etmedi; rol HEAD'deki hâlindedir."""
    izinler = _rol_izinleri()["accountant"]
    assert izinler == ACCOUNTANT_BEKLENEN
    assert len(izinler) == ACCOUNTANT_SAYISI
    assert KDS_AYAR not in izinler


def test_degismeyen_roller_degismedi() -> None:
    """`admin`, `bbd_staff`, `org_staff` karardan etkilenmedi."""
    izinler = _rol_izinleri()

    # Admin her ilan edilen izne sahiptir; yeni anahtar onu es geçemez.
    tum_izinler = {
        _entry(permission["key"], bool(permission.get("scoped")))
        for permission in _izin_kayitlari().values()
    }
    assert izinler["admin"] == tum_izinler
    assert KDS_AYAR in izinler["admin"]

    # BBD personeli sunucusunu ve veritabanını korudu.
    for key in ("servers.view", "ssh.execute", "ssh.transfer",
                "database.view", "database.query", "database.write",
                "database.backup"):
        assert f"{key}:*" in izinler["bbd_staff"], key
    assert "database.restore:*" not in izinler["bbd_staff"]

    # Kurum personeli zil, çıktı ve rehberde duruyor.
    for key in ("bell.view", "bell.manage", "bell.ring_now",
                "print.view", "print.reprint", "directory.view"):
        assert key in izinler["org_staff"], key


# ---------------------------------------------------------------- belge sözü


def test_belge_rol_izin_matrisi_manifestlerle_ayni() -> None:
    """Belgedeki ✓/✗ dağılımı manifestlerin söylediğiyle aynı mı.

    Matris tabloda YAZILI olan anahtarları kapsar (çekirdek + belgeye alınmış
    modül anahtarları); tek tek listelenmeyen ekran modülleri belgenin kendi
    kuralı gereği tabloda yoktur. Sınanan şey tablonun EKSİKSİZLİĞİ değil,
    yazdığının DOĞRULUĞU: yanlış bir ✓, olmayan bir yetkiyi varmış gibi
    gösterir ve yetki tablosuna bakan kişi onu düzeltmeye hiç gitmez.
    """
    izinler = _rol_izinleri()
    kayitlar = _izin_kayitlari()

    satirlar = _tablo_satirlari("## Rol → izin matrisi")
    assert satirlar, "matris tablosu okunamadı"

    for hucreler in satirlar:
        anahtarlar = _kod_parcalari(hucreler[1])
        if not anahtarlar:
            continue
        key = anahtarlar[0]
        assert key in kayitlar, f"belgede olan {key} hiçbir yerde ilan edilmemiş"
        entry = _entry(key, bool(kayitlar[key].get("scoped")))

        for sutun, role_id in enumerate(BELGE_ROLLERI, start=2):
            verildi = "✓" in hucreler[sutun]
            assert verildi == (entry in izinler.get(role_id, set())), (
                f"{key} · {role_id}: belge '{hucreler[sutun]}' diyor, "
                f"manifest tersini söylüyor"
            )


def test_belge_kds_ayar_anahtarini_aciklar() -> None:
    """Yeni bir anahtar belgeye YAZILMADAN var olamaz.

    Rol matrisi sistemin gerçeğini anlatıyorsa, bir anahtarın neden ayrıldığı
    da orada durmalı; yoksa altı ay sonra "bu neden ayrı?" sorusunun cevabı
    yalnızca git geçmişinde kalır.
    """
    metin = BELGE.read_text(encoding="utf-8")
    assert KDS_AYAR in metin
    assert "bld_kds.manage" in metin


# ------------------------------------------------------------ çift kapı (K9)


def test_her_http_yuzeyi_izin_ilan_eder() -> None:
    """K9: ekranı menüden gizlemek yetkilendirme değildir.

    `km_core/http/app.py` `http.requires` boşsa router'ı MONTE ETMEZ; burada
    aynı kapı manifest tarafından sınanır ki bir modülün ucu yanlışlıkla
    korumasız kalmasın.
    """
    for manifest in _manifests():
        http = manifest.get("http") or {}
        if not http:
            continue
        requires = http.get("requires") or []
        assert requires, f"{manifest['id']}: http var, http.requires boş"

        ilan_edilen = {
            permission["key"] for permission in manifest.get("permissions") or []
        }
        for entry in requires:
            key = entry.split(":", 1)[0]
            assert key in ilan_edilen, f"{manifest['id']}: {entry} ilan edilmemiş"


def test_ekranlar_izne_bagli() -> None:
    """Menü girdisi `requires` ilan etmezse ekran herkese görünür."""
    for manifest in _manifests():
        nav = ((manifest.get("ui") or {}).get("nav") or {})
        if not nav:
            continue
        assert nav.get("requires"), f"{manifest['id']}: ui.nav.requires boş"


# --------------------------------------------------------------- kurulu sistem


@pytest.fixture
async def depo(tmp_path: Path) -> AsyncIterator[Store]:
    """REDDEDİLEN DARALTMANIN KOŞTUĞU bir kurulum.

    `0007_narrow_bld_staff_core` dokuz çekirdek satırını sildi ve kaydı
    `schema_migrations` tablosuna düştü. Bugünkü kod o göçü artık tanımıyor —
    kaydı duruyor, kodu yok. Kurtarılacak durum tam olarak budur.
    """
    store = Store(tmp_path / "kurulu.sqlite")
    await store.open()
    await store.execute_many(
        "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
        [
            # `0007`in DOKUNMADIĞI satırlar — yerlerinde duruyorlar.
            ("bld_staff", "bld_menu.view"),
            ("bld_staff", "bld_kds.manage"),
            ("bbd_staff", "database.query:*"),
            ("bbd_staff", "directory.view"),
            ("admin", "database.restore:*"),
            # ELLE yazılmış dar kapsam; `grant_defaults` çıktısı olamaz.
            ("bld_staff", "servers.view:bld"),
        ],
    )
    await store.execute(
        "INSERT INTO schema_migrations (owner, name, applied_at) "
        "VALUES ('core', '0007_narrow_bld_staff_core', 'dun')"
    )
    await store.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
        "pin_lookup, pin_set_at, created_at, updated_at) "
        "VALUES ('u-1', 'BLD', 'Personeli', 'bld', 'active', '', 'pin-yok:u-1', "
        "'dun', 'dun', 'dun')"
    )
    await store.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES ('u-1', 'bld_staff')"
    )
    yield store
    await store.close()


async def _satirlar(store: Store) -> set[tuple[str, str]]:
    return {
        (row["role_id"], row["permission"])
        for row in await store.fetch_all("SELECT role_id, permission FROM role_permissions")
    }


def test_reddedilen_goc_artik_kosmuyor() -> None:
    """`0007` listeden çıktı; numarası yeniden kullanılmadı."""
    adlar = [name for name, _ in CORE_MIGRATIONS]
    assert "0007_narrow_bld_staff_core" not in adlar
    assert not [name for name in adlar if name.startswith("0007")]
    assert adlar[-1] == "0008_restore_bld_staff_core"


async def test_goc_dokuz_cekirdek_satirini_geri_koyar(depo: Store) -> None:
    await apply_core_migrations(depo)
    kalan = await _satirlar(depo)

    assert len(BLD_STAFF_CORE_RESTORED) == 9
    for entry in BLD_STAFF_CORE_RESTORED:
        assert ("bld_staff", entry) in kalan, entry

    # Geri konan satırların hepsi kataloğun bugün önerdiği satırlardır; biri
    # katalogda olmasaydı `grant_defaults` onu bir sonraki açılışta zaten
    # yazmazdı ve göç kataloğa aykırı bir satır bırakmış olurdu.
    onerilenler = {
        _entry(permission["key"], bool(permission.get("scoped")))
        for permission in CORE_PERMISSIONS
        if "bld_staff" in permission["default_roles"]
    }
    assert set(BLD_STAFF_CORE_RESTORED) == onerilenler


async def test_goc_hicbir_satir_silmez(depo: Store) -> None:
    """GERİ ALMA DAİMA EKLEYEREK YAPILIR (kullanıcı kuralı)."""
    onceki = await _satirlar(depo)
    await apply_core_migrations(depo)
    sonraki = await _satirlar(depo)

    assert onceki <= sonraki, "göç var olan bir satırı düşürdü"
    # Elle yazılmış dar kapsamlı satır da olduğu gibi duruyor.
    assert ("bld_staff", "servers.view:bld") in sonraki
    assert ("bld_staff", "bld_kds.manage") in sonraki
    assert ("bbd_staff", "database.query:*") in sonraki
    assert ("admin", "database.restore:*") in sonraki


async def test_goc_kds_ayar_anahtarini_kimseye_vermez(depo: Store) -> None:
    """Yeni anahtar için göç YOK; olmaması da sınanır.

    `bld_kds.settings` satırlarını `grant_defaults` yazar ve yalnız `admin`e
    yazar. Göçün onu `bld_staff`a eklemesi, kararın tek istisnasını sessizce
    geri almak olurdu.
    """
    await apply_core_migrations(depo)
    kalan = await _satirlar(depo)
    assert not [row for row in kalan if row[1] == KDS_AYAR]


async def test_goc_kullanici_satiri_silmez(depo: Store) -> None:
    await apply_core_migrations(depo)

    users = await depo.fetch_all("SELECT id FROM users")
    roles = await depo.fetch_all("SELECT user_id, role_id FROM user_roles")
    assert [row["id"] for row in users] == ["u-1"]
    assert [(row["user_id"], row["role_id"]) for row in roles] == [("u-1", "bld_staff")]


async def test_goc_denetim_izine_yazar(depo: Store) -> None:
    """Geri verilen yetki de bir yetki değişikliğidir; iz bırakmadan olmaz."""
    await apply_core_migrations(depo)

    rows = await depo.fetch_all(
        "SELECT action, result, detail FROM audit_log WHERE detail LIKE ?",
        ("0008_restore_bld_staff_core:%",),
    )
    assert len(rows) == len(BLD_STAFF_CORE_RESTORED)
    assert {row["action"] for row in rows} == {"roles.manage"}
    assert {row["result"] for row in rows} == {"ok"}


async def test_goc_iki_kez_uygulanmaz(depo: Store) -> None:
    ilk = await apply_core_migrations(depo)
    ikinci = await apply_core_migrations(depo)

    # `0007` kaydı duruyor ama kodu yok; koşan göç sayısı listenin boyudur.
    assert len(ilk) == len(CORE_MIGRATIONS)
    assert ikinci == []
    rows = await depo.fetch_all(
        "SELECT id FROM audit_log WHERE detail LIKE ?",
        ("0008_restore_bld_staff_core:%",),
    )
    assert len(rows) == len(BLD_STAFF_CORE_RESTORED)


async def test_goc_zaten_duran_satiri_ikizlemez(tmp_path: Path) -> None:
    """Yönetici satırı elle geri vermişse göç sessiz kalır — iz de bırakmaz.

    `INSERT OR IGNORE` satırı ikizlemez; asıl tuzak denetim izidir. Süzgeçsiz
    bir `INSERT ... SELECT`, hiçbir şey yapmadığı hâlde "dokuz yetki geri
    verildi" diye dokuz satır yazardı.
    """
    store = Store(tmp_path / "elle.sqlite")
    await store.open()
    try:
        await store.execute_many(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
            [("bld_staff", entry) for entry in BLD_STAFF_CORE_RESTORED],
        )
        await apply_core_migrations(store)

        satirlar = await store.fetch_all(
            "SELECT permission, COUNT(*) AS adet FROM role_permissions "
            "WHERE role_id = 'bld_staff' GROUP BY permission"
        )
        assert {row["adet"] for row in satirlar} == {1}
        assert await store.fetch_all("SELECT id FROM audit_log") == []
    finally:
        await store.close()


async def test_goc_bos_veritabaninda_da_ekler(tmp_path: Path) -> None:
    """Hiç açılmamış kurulumda `0007` hiç koşmadı; `0008` yine de ekleyicidir.

    Zararsız: kataloğun `bld_staff`a önerdiği satırların aynısını yazar ve
    `grant_defaults` birkaç satır sonra zaten aynısını yazacaktır.
    """
    store = Store(tmp_path / "yeni.sqlite")
    await store.open()
    try:
        await apply_core_migrations(store)
        kalan = await _satirlar(store)
        assert {entry for _, entry in kalan} == set(BLD_STAFF_CORE_RESTORED)
        assert {role for role, _ in kalan} == {"bld_staff"}
    finally:
        await store.close()
