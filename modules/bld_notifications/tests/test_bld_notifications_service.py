"""Bildirimler servisi — iş kuralları. Ağa çıkmaz; `bld.api` taklit edilir."""

from __future__ import annotations

from typing import Any

from bld_notifications_backend.service import NoticeService
from bld_notifications_fakes import (
    NOTICE,
    STATS_UNTRACKABLE,
    FakeApi,
    FakeLog,
    FakeStore,
)

GEREKCE = "Bayram kapanışı duyurusu hazırlandı"
AKTOR = "Ayşe Yılmaz"

#: Geçmişte kalmayan bir pencere — testler 2026'dan sonra da koşacak.
BASLANGIC = "2099-08-20T00:00:00Z"
BITIS = "2099-08-31T00:00:00Z"


def _service(**config: Any) -> tuple[NoticeService, FakeApi, FakeStore]:
    api = FakeApi()
    store = FakeStore()
    service = NoticeService(api=api, store=store, log=FakeLog(), config=dict(config))
    return service, api, store


def _publishable(api: FakeApi) -> None:
    """Taze okumanın "taslak" demesini sağlar.

    Sözleşmenin `stats` örneği YAYINDAKİ bir duyuruya ait ve fixture ondan
    kopyalandı; yayın denemesi o hâlde haklı olarak engelleniyor.
    """
    api.stats_payload = {**api.stats_payload, "status": "draft"}


def _draft(**overrides: Any) -> dict[str, Any]:
    body = {"title": "30 Ağustos'ta kapalıyız",
            "body": "30 Ağustos Zafer Bayramı nedeniyle üretim yapılmayacaktır.",
            "level": "warning", "audience": "customers",
            "starts_at": BASLANGIC, "ends_at": BITIS,
            "action_label": "", "action_url": "", "dismissible": True,
            "reason": GEREKCE, "actor": AKTOR, "dry_run": False}
    body.update(overrides)
    return body


# ====================================================== K7 — ekran ayakta kalır

async def test_gecit_duserse_liste_ucu_ayakta_kalir() -> None:
    # Duyurular müşteride GÖRÜNMEYE DEVAM EDİYOR olabilir; boş liste "duyuru
    # yok" ile "sunucuya ulaşılamıyor"u aynı gösterirdi.
    service, api, _ = _service()
    api.fail.add("notifications")
    sonuc = await service.notices()
    assert sonuc["ok"] is True                 # uç patlamaz
    assert sonuc["connected"] is False
    assert sonuc["items"] == []
    assert "patladı" in sonuc["error"]
    # Sözleşme YEREL: geçit düşse bile form ve süzgeçler çizilebilir.
    assert len(sonuc["reference"]["levels"]) == 3
    assert len(sonuc["reference"]["audiences"]) == 3
    assert sonuc["settings"]["refresh_seconds"] == 120


async def test_gecit_duserse_istatistik_ucu_de_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("notification_stats")
    sonuc = await service.stats(12)
    assert sonuc["ok"] is True and sonuc["connected"] is False
    assert sonuc["data"] == {}


async def test_uc_yayinda_degilse_ekran_ne_yapacagini_soyler() -> None:
    # `control_endpoint_missing` "kayıt yok" DEĞİLDİR: uç sunucuya henüz
    # dağıtılmamıştır ve yöneticinin yapacağı şey beklemektir.
    service, api, _ = _service()
    api.fail.add("notifications")
    api.fail_code = "control_endpoint_missing"
    sonuc = await service.notices()
    assert sonuc["connected"] is False
    assert "sunucu eklentisi güncellenince" in sonuc["error"]


async def test_denetim_izi_yazilamazsa_is_durmaz() -> None:
    service, _, store = _service()
    store.broken = True
    sonuc = await service.create(**_draft())
    assert sonuc["ok"] is True


async def test_yerel_iz_okunamazsa_uc_bos_liste_doner() -> None:
    service, _, store = _service()
    store.broken = True
    sonuc = await service.audit()
    assert sonuc["ok"] is True and sonuc["items"] == []


# ================================================== kuru prova AÇIKÇA geçirilir

async def test_her_yazma_dry_run_bayragini_acikca_gecirir() -> None:
    # Geçidin varsayılanına GÜVENİLMEZ: `config/local.yaml` git dışında ve
    # orada `dry_run_default: true` yazıyor olabilir. Bayrağı atlayan bir çağrı
    # hiçbir şey yazmadan `{"ok": true}` alır ve ekran "yayınlandı" der.
    service, api, _ = _service()
    _publishable(api)
    await service.create(**_draft(dry_run=None))
    await service.update(12, changes={"title": "Yeni başlık"}, reason=GEREKCE,
                         actor=AKTOR, dry_run=None)
    await service.publish(12, reason=GEREKCE, actor=AKTOR, dry_run=None,
                          allow_publish=True)
    await service.archive(12, reason=GEREKCE, actor=AKTOR, dry_run=None,
                          allow_publish=True)

    for name in ("create_notification", "update_notification",
                 "publish_notification", "archive_notification"):
        cagrilar = api.used(name)
        assert cagrilar, f"{name} çağrılmadı"
        assert cagrilar[0]["dry_run"] is False, f"{name} bayrağı taşımıyor"


async def test_kuru_prova_yaniti_yazildi_diye_gosterilmez() -> None:
    service, api, store = _service(dry_run_default=True)
    api.dry_run_echo = True
    sonuc = await service.create(**_draft(dry_run=None))
    assert sonuc["dry_run"] is True
    assert sonuc["would"]                       # sunucu ne olacağını yazdı
    assert store.results("notification.create") == ["denendi", "dry_run"]


# ======================================================= doğrulama uzağa gitmez

async def test_gecmis_bitisli_duyuru_uzaga_hic_gitmez() -> None:
    service, api, _ = _service()
    sonuc = await service.create(**_draft(starts_at="", ends_at="2020-01-01T00:00:00Z"))
    assert sonuc["ok"] is False
    assert api.writes() == []


async def test_kapatilamaz_bilgilendirme_uzaga_hic_gitmez() -> None:
    service, api, _ = _service()
    sonuc = await service.create(**_draft(level="info", dismissible=False))
    assert sonuc["ok"] is False and api.writes() == []


async def test_etiketsiz_dugme_uzaga_hic_gitmez() -> None:
    service, api, _ = _service()
    sonuc = await service.create(**_draft(action_url="/abonelik", action_label=""))
    assert sonuc["ok"] is False and api.writes() == []


async def test_guvenilmeyen_adres_uzaga_hic_gitmez() -> None:
    service, api, _ = _service()
    sonuc = await service.create(**_draft(action_label="Bak",
                                          action_url="http://ornek.com"))
    assert sonuc["ok"] is False and api.writes() == []


async def test_kisa_gerekce_uzaga_hic_gitmez() -> None:
    # Sunucu da denetliyor (sözleşme §3); buradaki kapı ikincisidir (K9) ve
    # hız kovasından pay harcamaz.
    service, api, _ = _service()
    sonuc = await service.create(**_draft(reason="kısa"))
    assert sonuc["ok"] is False and api.writes() == []


async def test_aktorsuz_yazma_uzaga_hic_gitmez() -> None:
    service, api, _ = _service()
    sonuc = await service.create(**_draft(actor=""))
    assert sonuc["ok"] is False and api.writes() == []


async def test_bos_guncelleme_uzaga_hic_gitmez() -> None:
    service, api, _ = _service()
    sonuc = await service.update(12, changes={}, reason=GEREKCE, actor=AKTOR,
                                 dry_run=False)
    assert sonuc["ok"] is False and api.writes() == []


async def test_durum_alani_patch_ile_yazilamaz() -> None:
    service, api, _ = _service()
    sonuc = await service.update(12, changes={"status": "published"}, reason=GEREKCE,
                                 actor=AKTOR, dry_run=False)
    assert sonuc["ok"] is False and api.writes() == []


# ================================================================ yazma zinciri

async def test_iz_once_denendi_sonra_ok_yazar() -> None:
    # "Ne yapmaya çalıştık" kaydı, çağrı yarıda kaldığında tek kanıttır.
    service, _, store = _service()
    await service.create(**_draft())
    assert store.results("notification.create") == ["denendi", "ok"]


async def test_gecit_patlarsa_iz_hata_ile_kapanir() -> None:
    service, api, store = _service()
    api.fail.add("create_notification")
    sonuc = await service.create(**_draft())
    assert sonuc["ok"] is False
    assert store.results("notification.create") == ["denendi", "hata"]


async def test_ize_govdenin_tamami_yazilmaz() -> None:
    service, _, store = _service()
    await service.create(**_draft(body="x" * 400))
    kunye = store.detail(0)
    assert kunye["body_length"] == 400
    assert "body" not in kunye


async def test_bos_pencere_null_olarak_gider() -> None:
    # Boş DİZE gönderilseydi sunucu onu bir an olarak ayrıştırmaya çalışır ve
    # 422 verirdi; sözleşmede "pencere yok"un karşılığı `null`.
    service, api, _ = _service()
    await service.create(**_draft(starts_at="", ends_at=""))
    gonderilen = api.used("create_notification")[0]
    assert gonderilen["starts_at"] is None
    assert gonderilen["ends_at"] is None


async def test_guncellemede_yalniz_gonderilen_alanlar_gider() -> None:
    service, api, _ = _service()
    await service.update(12, changes={"title": "Düzeltilmiş başlık"}, reason=GEREKCE,
                         actor=AKTOR, dry_run=False)
    gonderilen = api.used("update_notification")[0]
    assert gonderilen["title"] == "Düzeltilmiş başlık"
    assert "body" not in gonderilen and "audience" not in gonderilen


async def test_pencere_temizleme_null_olarak_gider() -> None:
    service, api, _ = _service()
    sonuc = await service.update(12, changes={"starts_at": None, "ends_at": None},
                                 reason=GEREKCE, actor=AKTOR, dry_run=False)
    assert sonuc["ok"] is True
    gonderilen = api.used("update_notification")[0]
    assert gonderilen["starts_at"] is None and gonderilen["ends_at"] is None


async def test_kapsam_uyarisi_yutulmaz() -> None:
    # Uyarı bir hata değildir ama yutulursa yönetici, kapsamı daralttığında kaç
    # müşterinin duyuruyu artık göremeyeceğini hiç öğrenemez.
    service, api, _ = _service()
    api.warnings = [{"code": "audience_changed_after_publish", "from": "customers",
                     "to": "subscribers", "note": "84 müşteriden 61'i kapsam dışında."}]
    sonuc = await service.update(12, changes={"audience": "subscribers"},
                                 reason=GEREKCE, actor=AKTOR, dry_run=False)
    assert sonuc["warnings"][0]["code"] == "audience_changed_after_publish"


# ==================================================================== yayın

async def test_yayin_izni_yoksa_uzaga_hic_gidilmez() -> None:
    # Uç noktadaki `requires` kapısı arayüzü kapatır; buradaki kapı gövdeyi elle
    # kuran istemciyi de kapatır (K9 — çift kapı).
    service, api, store = _service()
    sonuc = await service.publish(12, reason=GEREKCE, actor=AKTOR, dry_run=False,
                                  allow_publish=False)
    assert sonuc["ok"] is False
    assert api.writes() == []
    assert store.results("notification.publish") == ["engellendi"]


async def test_arsiv_izni_yoksa_uzaga_hic_gidilmez() -> None:
    service, api, store = _service()
    sonuc = await service.archive(12, reason=GEREKCE, actor=AKTOR, dry_run=False,
                                  allow_publish=False)
    assert sonuc["ok"] is False
    assert api.writes() == []
    assert store.results("notification.archive") == ["engellendi"]


async def test_zaten_yayindaki_duyuru_ikinci_kez_yayinlanmaz() -> None:
    # TAZE OKUMA: duyuru aradan başka biri tarafından yayınlanmış olabilir.
    service, api, store = _service()
    api.stats_payload = {**api.stats_payload, "status": "published"}
    sonuc = await service.publish(12, reason=GEREKCE, actor=AKTOR, dry_run=False,
                                  allow_publish=True)
    assert sonuc["ok"] is False
    assert "zaten yayında" in sonuc["error"]
    assert api.writes() == []
    assert store.results("notification.publish") == ["engellendi"]


async def test_taze_okuma_basarisizsa_yayin_durmaz() -> None:
    # Taze okuma bir KOLAYLIK kapısıdır, yetki kapısı değil: asıl karar
    # sunucudadır (409 CONFLICT).
    service, api, _ = _service()
    api.fail.add("notification_stats")
    sonuc = await service.publish(12, reason=GEREKCE, actor=AKTOR, dry_run=False,
                                  allow_publish=True)
    assert sonuc["ok"] is True
    assert api.writes() == ["publish_notification"]


async def test_yayin_yaniti_henuz_gorunmuyor_bilgisini_tasir() -> None:
    # `live_from` olmadan, yayınla düğmesine basıp hiçbir şey görmeyen yönetici
    # düğmeye ikinci kez basardı.
    service, api, _ = _service()
    _publishable(api)
    sonuc = await service.publish(12, reason=GEREKCE, actor=AKTOR, dry_run=False,
                                  allow_publish=True)
    assert sonuc["publish"]["live_from"] == "2026-08-20T00:00:00Z"
    assert sonuc["publish"]["live"] is False
    assert sonuc["publish"]["estimated_audience"] == 214


async def test_zaten_arsivdeki_duyuru_tekrar_arsivlenmez() -> None:
    service, api, _ = _service()
    api.stats_payload = {**api.stats_payload, "status": "archived"}
    sonuc = await service.archive(12, reason=GEREKCE, actor=AKTOR, dry_run=False,
                                  allow_publish=True)
    assert sonuc["ok"] is False and api.writes() == []


# ==================================================================== okuma

async def test_liste_satirlari_sunucu_saatiyle_yorumlanir() -> None:
    service, api, _ = _service()
    api.rows = [{**NOTICE, "live": True}]
    sonuc = await service.notices()
    assert sonuc["items"][0]["visibility"] == "live"
    assert sonuc["meta"]["live_count"] == 1
    assert sonuc["server_time"] == "2026-08-16T09:00:00Z"


async def test_live_count_bildirilmezse_sifir_uydurulmaz() -> None:
    # Sıfır "hiçbiri görünmüyor" demektir ve bu ölçülmemiş bir iddia olurdu.
    service, api, _ = _service()
    api.meta = {"page": 1, "per_page": 25, "total": 1, "last_page": 1}
    sonuc = await service.notices()
    assert sonuc["meta"]["live_count"] is None


async def test_sayfa_boyutu_sozlesme_tavaniyla_sinirlidir() -> None:
    service, api, _ = _service()
    await service.notices(per_page=500)
    assert api.used("notifications")[0]["per_page"] == 100


async def test_olculemeyen_istatistikte_null_korunur() -> None:
    service, api, _ = _service()
    api.stats_payload = dict(STATS_UNTRACKABLE)
    sonuc = await service.stats(13)
    assert sonuc["data"]["trackable"] is False
    assert sonuc["data"]["seen_count"] is None
    assert sonuc["data"]["daily"] is None


async def test_yerel_iz_en_yeni_ustte_doner() -> None:
    service, _, _ = _service()
    await service.create(**_draft())
    sonuc = await service.audit()
    assert sonuc["items"][0]["result"] == "ok"
    assert sonuc["items"][0]["action"] == "notification.create"
    assert sonuc["items"][0]["actor"] == AKTOR
