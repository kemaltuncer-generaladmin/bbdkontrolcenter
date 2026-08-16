"""Yedek envanterinin saf hesapları. Ağ yok, dosya yok, durum yok."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from store_backups_backend import inventory

SIMDI = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _row(**fields: object) -> dict[str, object]:
    base = {"name": "bbd-2026-08-13-0300.tar.gz", "created_at": "2026-08-13T03:00:00Z",
            "scope": ["database", "uploads", "config"], "size": 100 * 1024 * 1024,
            "duration": 42, "actor": "Ayşe", "note": "haftalık",
            "sha256": "abc", "verify_state": "ok", "path": "/srv/backups/x.tar.gz"}
    base.update(fields)
    return base


# ================================================================ alan okuma

def test_alan_adi_snake_de_camel_de_olsa_okunur() -> None:
    # Bir dönem burada "BBD ucu henüz yayında değil, adların hangi biçimde
    # geleceği bilinmiyor" yazıyordu. ARTIK ÖYLE DEĞİL: uç 2026-08-16'da 200
    # dönüyor ve adlar camelCase geliyor (name · size · createdAt · actor ·
    # durationSeconds). İki yazımın da sınanması KALIYOR — mağaza tarafı hâlâ
    # gelişiyor ve tek ada bağlanan okuma, ad değiştiği gün istisna bile
    # atmadan ekranı "—" dolu gösterir.
    snake = inventory.backup_row({"name": "a", "created_at": "2026-08-13T03:00:00Z",
                                  "size_bytes": 10})
    camel = inventory.backup_row({"fileName": "a", "createdAt": "2026-08-13T03:00:00Z",
                                  "sizeBytes": 10})
    assert snake["createdAt"] == camel["createdAt"]
    assert snake["bytes"] == camel["bytes"] == 10


def test_eksik_alan_uydurulmaz() -> None:
    row = inventory.backup_row({})
    assert row["name"] == ""
    assert row["bytes"] == 0
    assert row["ageDays"] is None            # tarih yoksa yaş "bugün" sayılmaz
    assert row["verifyState"] == inventory.VERIFY_UNKNOWN


def test_taninmayan_tur_manuel_diye_etiketlenmez() -> None:
    # "Manuel" yazmak, rotasyonun bıraktığı bir yedeği elle alınmış gösterir ve
    # "bunu ben aldım, silebilirim" dedirtir. Bilinmeyen tür ham hâliyle durur.
    assert inventory.backup_row(_row(kind="rotation"))["kindLabel"] == "rotation"
    assert inventory.backup_row(_row(kind="scheduled"))["kindLabel"] == "Zamanlanmış"
    assert inventory.backup_row(_row())["kindLabel"] == "Manuel"        # tür hiç gelmedi


def test_milisaniye_gelen_sure_saniyeye_cevrilir() -> None:
    # 45.000 "saniye" (12,5 saat) süren bir yedek gerçekçi değil; ms gelmiştir.
    assert inventory.backup_row(_row(duration=45_000))["durationSeconds"] == 45.0
    assert inventory.backup_row(_row(duration=42))["durationSeconds"] == 42.0


# ================================================================ doğrulama

def test_dogrulanmamis_yedek_tamam_sayilmaz() -> None:
    # İkiye indirmek en tehlikeli hatayı üretirdi: hiç doğrulanmamış yedek
    # "tamam" görünür ve felaket gününde açılmaz.
    assert inventory.verify_state({}) == inventory.VERIFY_UNKNOWN
    assert inventory.verify_state({"verified": True}) == inventory.VERIFY_OK
    assert inventory.verify_state({"verify_state": "corrupt"}) == inventory.VERIFY_BAD
    assert inventory.verify_state({"verifiedAt": "2026-08-13T04:00:00Z"}) == inventory.VERIFY_OK


# ==================================================================== kapsam

def test_bilinmeyen_kapsam_atilir_ve_sira_sabittir() -> None:
    # Mağaza tanımadığı kapsamı yok sayıyor; kullanıcı "ayarları da yedekledim"
    # sanmasın diye liste burada süzülür.
    assert inventory.normalize_scope(["config", "database", "secrets"]) == ["database", "config"]
    assert inventory.normalize_scope("uploads, database") == ["database", "uploads"]
    assert inventory.normalize_scope(None) == []


def test_kapsam_etiketi_tamami_der() -> None:
    assert inventory.scope_label(list(inventory.SCOPES)) == "Tamamı"
    assert inventory.scope_label(["database"]) == "Veritabanı"
    assert inventory.scope_label([]) == "—"


# ============================================================== dosya adı

def test_yola_girecek_ad_beyaz_listeyle_denetlenir() -> None:
    # '..%2f' gönderen bozuk bir envanter yanıtı, denetlenmezse indirmeyi
    # rapor klasörünün DIŞINA yazdırırdı.
    assert inventory.safe_name("bbd-2026-08-13.tar.gz") == "bbd-2026-08-13.tar.gz"
    assert inventory.safe_name("../../etc/passwd") == ""
    assert inventory.safe_name("/srv/backups/x.tar.gz") == ""
    assert inventory.safe_name("yedek dosyası.tar") == ""
    assert inventory.safe_name("") == ""


# ==================================================================== disk

def test_disk_bilgisi_yoksa_sifir_yazilmaz() -> None:
    # Sıfır göstermek "disk dolu" demekle aynı şey olurdu.
    view = inventory.disk_view({}, [inventory.backup_row(_row())])
    assert view["known"] is False
    assert view["estimatedBackups"] is None
    assert "bilinmiyor" in inventory.disk_sentence(view)


def test_kalan_yedek_tahmini_son_yedeklerin_ortalamasindan_cikar() -> None:
    # Tek yedeğe bakmak yanıltır: kapsamı dar bir yedek birkaç yüz kilobayttır
    # ve "daha 900 yedek sığar" gibi anlamsız bir sayı üretir.
    rows = [inventory.backup_row(_row(size=size))
            for size in (1_000_000_000, 1_000_000_000, 4_000)]
    view = inventory.disk_view({"disk": {"total": 20_000_000_000, "free": 6_000_000_000}},
                               rows, sample=3)
    assert view["known"] is True
    assert view["usedBytes"] == 14_000_000_000
    assert view["estimatedBackups"] == 8       # ortalama ~667 MB
    assert "yaklaşık 8 yedek" in inventory.disk_sentence(view)


def test_bos_alan_gelmediyse_eksik_olanin_adi_soylenir() -> None:
    # Yedek boyutu BİLİNİYOR, eksik olan boş alan. "Yedek boyutu bilinmiyor"
    # demek kullanıcıyı yanlış tarafa bakmaya gönderirdi.
    view = inventory.disk_view({"total": 10_000_000_000},
                               [inventory.backup_row(_row(size=1_000_000))])
    assert view["known"] is True
    assert view["averageBytes"] == 1_000_000
    assert view["estimatedBackups"] is None
    cumle = inventory.disk_sentence(view)
    assert "BOŞ alan gelmedi" in cumle
    assert "yedek boyutu bilinmediği" not in cumle


def test_kalan_alan_bir_yedege_yetmiyorsa_soylenir() -> None:
    rows = [inventory.backup_row(_row(size=5_000_000_000))]
    view = inventory.disk_view({"free": 1_000_000, "total": 10_000_000_000}, rows)
    assert view["estimatedBackups"] == 0
    assert "yetmiyor" in inventory.disk_sentence(view)


# =============================================================== durum bandı

def test_hic_yedek_yoksa_bant_kirmizi_ve_aciktir() -> None:
    band = inventory.summary([], now=SIMDI)
    assert band["has"] is False
    assert band["tone"] == "bad"
    assert "Hiç yedek alınmamış" in band["text"]


def test_okunamayan_envanter_yedek_yok_demek_degildir() -> None:
    # Boş liste ile okunamamış liste AYNI ŞEY DEĞİLDİR. `summary([])` çağırmak
    # ekrana "Hiç yedek alınmamış. Canlı mağaza verisi yedeksiz duruyor."
    # yazdırırdı — dün gece yedeği alınmış bir mağaza için düpedüz yalan.
    bilinmiyor = inventory.unreadable_summary()
    assert bilinmiyor["known"] is False
    assert bilinmiyor["has"] is False
    assert "BİLİNMİYOR" in bilinmiyor["text"]
    assert "Hiç yedek alınmamış" not in bilinmiyor["text"]
    # Gerçekten boş envanter ise durum BİLİNİYOR ve söylenecek şey başkadır.
    assert inventory.summary([])["known"] is True


def test_taze_ve_dogrulanmis_yedek_iyi_gorunur() -> None:
    band = inventory.summary([inventory.backup_row(_row(), now=SIMDI)], now=SIMDI,
                             stale_after_hours=48)
    assert band["tone"] == "good"
    assert "doğrulandı" in band["text"]


def test_eski_yedek_uyarir_cok_eskisi_kirmizi_olur() -> None:
    eski = _row(created_at="2026-08-10T03:00:00Z")          # ~81 saat
    cok_eski = _row(created_at="2026-08-01T03:00:00Z")      # ~297 saat
    assert inventory.summary([inventory.backup_row(eski, now=SIMDI)],
                             now=SIMDI, stale_after_hours=48)["tone"] == "warn"
    assert inventory.summary([inventory.backup_row(cok_eski, now=SIMDI)],
                             now=SIMDI, stale_after_hours=48)["tone"] == "bad"


def test_bozuk_yedek_her_kosulda_kirmizidir() -> None:
    band = inventory.summary([inventory.backup_row(_row(verify_state="corrupt"), now=SIMDI)],
                             now=SIMDI)
    assert band["tone"] == "bad"
    assert "BOZUK" in band["text"]


def test_dogrulanmamis_taze_yedek_uyari_verir() -> None:
    band = inventory.summary([inventory.backup_row(_row(verify_state=None), now=SIMDI)],
                             now=SIMDI)
    assert band["tone"] == "warn"
    assert "doğrulanmadı" in band["text"]


# ================================================================ sıralama

def test_en_yeni_ustte_tarihsiz_satir_kaybolmaz() -> None:
    rows = inventory.sort_rows([
        inventory.backup_row(_row(name="eski", created_at="2026-08-01T03:00:00Z")),
        inventory.backup_row(_row(name="tarihsiz", created_at="")),
        inventory.backup_row(_row(name="yeni", created_at="2026-08-13T03:00:00Z")),
    ])
    assert [row["name"] for row in rows] == ["yeni", "eski", "tarihsiz"]


# ================================================================== silme

def test_yeni_yedek_silinemez() -> None:
    # Elde kalan tek sağlam kopya çoğu zaman en yenisidir.
    taze = inventory.backup_row(_row(), now=SIMDI)
    allowed, why = inventory.deletable(taze, min_age_days=30)
    assert allowed is False
    assert "30 günden eski" in why


def test_yasi_bilinmeyen_yedek_silinemez() -> None:
    allowed, why = inventory.deletable(inventory.backup_row(_row(created_at="")),
                                       min_age_days=30)
    assert allowed is False
    assert "tarihi okunamadı" in why


def test_yeterince_eski_yedek_silinebilir() -> None:
    eski = inventory.backup_row(
        _row(created_at=(SIMDI - timedelta(days=45)).isoformat()), now=SIMDI)
    allowed, why = inventory.deletable(eski, min_age_days=30)
    assert allowed is True
    assert why == ""


# ================================================================ gerekçe

def test_kisa_gerekce_metinle_reddedilir() -> None:
    assert inventory.reason_error("ok") != ""
    assert inventory.reason_error("Aylık arşiv yedeği alınıyor") == ""


# =============================================================== ucun hâli

class _Hata(RuntimeError):
    """Geçidin `StoreApiError`'ı gibi `code`/`status` taşıyan testlik hata."""

    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def test_uc_yok_ile_uc_kapali_ayri_hallerdir() -> None:
    # SAVUNMA DALI — canlıdaki güncel hâl değil. 2026-08-13'te GET
    # /api/admin/bbd/backups → 503 {"error":{"code":"CONTROL_API_DISABLED",...}}
    # dönüyordu; 2026-08-16'da 200 dönüyor (anahtar açılmış). Sınama KALIYOR:
    # geçit 503'e `code="server"` verir ve yalnız `bbd_endpoint_missing` koduna
    # bakmak bu hâli kaçırıp ayakta olan bir mağazayı "ulaşılamadı" gösterir.
    yok = _Hata("BBD'ye özel uç henüz yayında değil", code="bbd_endpoint_missing", status=404)
    kapali = _Hata("Mağaza hata verdi (503): {'code': 'CONTROL_API_DISABLED'}",
                   code="server", status=503)
    ariza = _Hata("Mağaza hata verdi (500): Internal Server Error",
                  code="server", status=500)

    assert inventory.endpoint_state(yok) == inventory.ENDPOINT_MISSING
    assert inventory.endpoint_state(kapali) == inventory.ENDPOINT_DISABLED
    assert inventory.endpoint_state(ariza) == ""          # gerçek arıza gizlenmez
    assert inventory.endpoint_state(RuntimeError("bağlantı koptu")) == ""


def test_iki_yayin_disi_halin_metni_ayridir() -> None:
    # Çareleri farklı: 404'te beklenir, 503'te bir anahtar açılır.
    assert inventory.endpoint_text(inventory.ENDPOINT_MISSING) != ""
    assert (inventory.endpoint_text(inventory.ENDPOINT_DISABLED)
            != inventory.endpoint_text(inventory.ENDPOINT_MISSING))
    assert "BBD_CONTROL_API_ENABLED" in inventory.endpoint_text(inventory.ENDPOINT_DISABLED)
    assert inventory.endpoint_text(inventory.ENDPOINT_LIVE) == ""
    assert inventory.endpoint_text("") == ""


# ================================================================ saat dilimi

def test_saat_dilimsiz_damga_yerel_sayilir() -> None:
    """TESTİ DÜZELTTİK, KODU DEĞİL — eski test yanlış davranışı sabitliyordu.

    Eskisi `age_hours("2026-08-13 06:00:00", SIMDI) == 6` diyordu, yani saat
    dilimsiz damgayı UTC sayıyordu. Mağaza canlıda saat dilimsiz ve YEREL damga
    gönderiyor (`"createdAt": "2026-08-13 19:54:51"`, GET /api/admin/orders ile
    doğrulandı; aynı tuzak store_orders README'sinde de yazılı). UTC saymak
    yedeği saat dilimi kadar gençleştiriyordu.
    """
    assert inventory.parse_time("2026-08-13 06:00:00") == datetime(2026, 8, 13, 6, 0).astimezone()
    # Saat dilimi TAŞIYAN damgaya dokunulmaz; "hepsini yerele çevir" değil.
    assert inventory.parse_time("2026-08-13T06:00:00Z") == datetime(2026, 8, 13, 6, 0, tzinfo=UTC)


def test_yerel_damga_bayatlama_esigini_kacirmaz() -> None:
    # Asıl zarar burada: UTC saymak 50 saatlik yedeği (UTC+3'te) 47 saatlik
    # gösterir, 48 saatlik eşik hiç tetiklenmez ve durum bandı yeşil kalır.
    # Makinenin saat dilimi ne olursa olsun geçerli olsun diye damga yerel
    # "şimdi"den üretilir ve saat dilimsiz yazılır — mağazanın gönderdiği gibi.
    simdi = datetime.now().astimezone()
    damga = (simdi - timedelta(hours=50)).strftime("%Y-%m-%d %H:%M:%S")
    band = inventory.summary([inventory.backup_row(_row(created_at=damga), now=simdi)],
                             now=simdi, stale_after_hours=48)
    assert band["tone"] == "warn"
    assert "48 saatten eski" in band["text"]
