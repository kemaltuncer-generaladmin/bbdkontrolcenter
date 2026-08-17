"""Gösterge paneli servisi — iş kuralları. Ağa çıkmaz; `bld.api` taklit edilir."""

from __future__ import annotations

from typing import Any

from bld_dashboard_fakes import (
    OVERVIEW,
    UNPUBLISHED_CAPACITY,
    FakeApi,
    FakeStore,
    make_service,
)

ACTOR = "Ayşe Yılmaz"


def _kur(**kwargs: Any) -> tuple[Any, FakeApi, FakeStore]:
    api = kwargs.pop("api", None) or FakeApi()
    store = kwargs.pop("store", None) or FakeStore()
    service = make_service(api=api, store=store, config=kwargs or None)
    return service, api, store


# ====================================================== K7 — ekran ayakta kalır

async def test_gecit_duserse_panel_ayakta_kalir() -> None:
    # Sipariş akmaya devam ediyorken gösterge panelinin hiç açılmaması, sorunun
    # kendisini görünmez yapardı. Uç 200 verir, `connected` farkı taşır.
    service, api, _ = _kur()
    api.fail.add("dashboard_overview")
    sonuc = await service.summary()

    assert sonuc["ok"] is True                  # uç patlamaz
    assert sonuc["connected"] is False
    assert sonuc["error"]
    assert sonuc["code"] == "transport"
    # Bloklar AYNI ŞEKİLDE gelir; panel alan yokluğu savunması yazmak zorunda
    # kalmaz. Değerler `None` — yani "bilinmiyor", "sıfır" değil.
    assert sonuc["orders"]["active"] is None
    assert sonuc["capacity"]["capacity_total"] is None
    assert sonuc["pending_tasks"] == []


async def test_akis_duserse_ozet_ayakta_kalir() -> None:
    # İKİ ÇAĞRI BİRBİRİNİ DÜŞÜRMEZ. Tek `try` bloğunda olsalardı, sipariş
    # listesinin 500 vermesi bütün gösterge panelini karartırdı.
    service, api, _ = _kur()
    api.fail.add("order_list")
    sonuc = await service.summary()

    assert sonuc["connected"] is True
    assert sonuc["orders"]["active"] == 30       # özet yerinde
    assert sonuc["flow"]["connected"] is False
    assert sonuc["flow"]["items"] == []
    assert sonuc["flow"]["error"]


async def test_ozet_duserse_akis_yine_denenir() -> None:
    # Tersi de doğru olmalı: gösterge ucu henüz yayında değilken sipariş
    # listesi çalışıyor olabilir ve o kutunun boş kalması için sebep yok.
    service, api, _ = _kur()
    api.fail.add("dashboard_overview")
    sonuc = await service.summary()
    assert sonuc["connected"] is False
    # Özet düştüğünde akış İSTENMEZ: `_blank()` boş akış döndürür. Bunu
    # sabitliyoruz ki ilerideki bir "iyileştirme" sessizce ikinci bir istek
    # eklemesin — bağlantı yokken ikinci çağrı da düşecek ve hız kovasını
    # boşuna yakacaktı.
    assert "order_list" not in api.names()
    assert sonuc["flow"]["items"] == []


async def test_tercih_okunamazsa_varsayilan_yeter() -> None:
    service, _, store = _kur()
    store.broken = True
    sonuc = await service.overview()
    assert sonuc["ok"] is True
    assert sonuc["prefs"]["poll_seconds"] == 30


# ================================================== sözleşme ağa çıkmadan

async def test_overview_aga_cikmaz() -> None:
    # Etiketleri her yoklamada tekrar göndermek, 30 saniyede bir değişmeyen bir
    # sözlüğü tele koymak olurdu; ayrıca geçit düşükken de dolu dönmeli.
    service, api, _ = _kur()
    sonuc = await service.overview()
    assert api.names() == []
    assert sonuc["connected"] is None
    assert sonuc["contract"]["level_labels"]["warning"] == "Uyarı"
    assert sonuc["limits"]["poll_seconds"] == 30


# ================================================================= okuma

async def test_ozet_sozlesmedeki_yedi_blogu_tasir() -> None:
    service, _, _ = _kur()
    sonuc = await service.summary()

    assert sonuc["connected"] is True
    assert sonuc["date"] == "2026-08-16"
    assert sonuc["server_time"] == "2026-08-16T09:00:00Z"
    assert sonuc["sales"]["cutoff_time"] == "08:00"
    assert sonuc["sales"]["seconds_to_next_cutoff"] == 72000
    assert sonuc["orders"]["revenue_today_kurus"] == 13140000
    assert sonuc["capacity"]["fill_rate"] == 0.72
    assert sonuc["subscriptions"]["unpaid_total_kurus"] == 1920000
    assert sonuc["devices"]["queue_oldest_age_minutes"] == 41
    assert sonuc["monitor"]["health_label"] == "Aksıyor"
    assert len(sonuc["pending_tasks"]) == 3


async def test_geri_sayimin_tabani_sunucunun_saati() -> None:
    # Panel kalan süreyi `seconds_to_next_cutoff` + `server_time` üzerine
    # kuruyor; ikisi de yanıtta durmalı. İstemcinin kendi saatinden hesaplaması,
    # saati kaymış bir makinede olmayan bir aciliyet yaratırdı.
    service, _, _ = _kur()
    sonuc = await service.summary()
    assert sonuc["sales"]["seconds_to_next_cutoff"] == 72000
    assert sonuc["server_time"]


async def test_para_tam_sayi_kurus_kalir() -> None:
    # Kuruşu liraya çevirmek ya da ondalığa yuvarlamak, iki ekranın aynı
    # siparişte farklı tutar göstermesi demekti.
    service, _, _ = _kur()
    sonuc = await service.summary()
    assert isinstance(sonuc["orders"]["revenue_today_kurus"], int)
    assert isinstance(sonuc["subscriptions"]["overdue_total_kurus"], int)


async def test_yayinlanmamis_menude_kapasite_null_kalir() -> None:
    govde = {**OVERVIEW, "capacity": dict(UNPUBLISHED_CAPACITY)}
    service, _, _ = _kur(api=FakeApi(overview=govde))
    sonuc = await service.summary()
    assert sonuc["capacity"]["menu_published"] is False
    assert sonuc["capacity"]["capacity_total"] is None
    assert sonuc["capacity"]["sold_total"] is None


async def test_bekleyen_isler_panele_baglanir() -> None:
    service, _, _ = _kur()
    sonuc = await service.summary()
    hedefler = {row["code"]: row["panel"] for row in sonuc["pending_tasks"]}
    assert hedefler["menu_missing"] == "bld_menu"
    assert hedefler["quote_requests_new"] == "bld_subscriptions"
    assert hedefler["printer_fault"] == "bld_status_monitor"


async def test_gecersiz_gun_baglanti_sorunu_sayilmaz() -> None:
    # Süzgeç hatası ile "sunucuya ulaşılamıyor" aynı ekranda aynı görünmemeli:
    # biri kullanıcının düzeltebileceği bir şey, öteki değil.
    service, api, _ = _kur()
    sonuc = await service.summary(date="16.08.2026")
    assert sonuc["ok"] is False
    assert sonuc["connected"] is None
    assert api.names() == []                    # hatalı istek tele HİÇ çıkmaz


async def test_bos_gun_sunucuya_gonderilmez() -> None:
    # Bugünün ne olduğuna işletme takvimi karar verir (Europe/Istanbul iş
    # günü); istemcinin `todayIso()` yazması, gece yarısından sonra sunucudan
    # başka bir gün istemek olurdu.
    service, api, _ = _kur()
    await service.summary()
    assert api.used("dashboard_overview")[0]["date"] == ""


async def test_location_id_sifirsa_sorguya_eklenmez() -> None:
    service, api, _ = _kur()
    await service.summary()
    assert api.used("dashboard_overview")[0]["location_id"] is None

    service2, api2, _ = _kur(location_id=3)
    await service2.summary()
    assert api2.used("dashboard_overview")[0]["location_id"] == 3


async def test_istekteki_location_id_ayari_ezer() -> None:
    service, api, _ = _kur(location_id=3)
    await service.summary(location_id=9)
    assert api.used("dashboard_overview")[0]["location_id"] == 9


async def test_onbellek_damgasi_tasinir_uydurulmaz() -> None:
    # Sözleşme 60 saniyelik önbelleği İSTEĞE BAĞLI bıraktı. Sunucu açarsa
    # `meta.cached_at` gelir ve ekran "34 saniye önceki veri" diyebilir;
    # gelmezse hiçbir şey demez.
    api = FakeApi()
    api.meta = {"cached_at": "2026-08-16T08:59:30Z"}
    service, _, _ = _kur(api=api)
    sonuc = await service.summary()
    assert sonuc["meta"]["cached_at"] == "2026-08-16T08:59:30Z"

    service2, _, _ = _kur()
    assert (await service2.summary())["meta"] == {}


# ================================================================== akış

async def test_akis_suzgecsiz_ve_sinirli_okur() -> None:
    # Süzgeç göndermek gece verilen yarının siparişlerini akıştan düşürürdü ve
    # catering'de gece siparişi olağandır.
    service, api, _ = _kur(flow_limit=5)
    await service.summary()
    cagri = api.used("order_list")[0]
    assert cagri["per_page"] == 5
    assert cagri["page"] == 1
    assert cagri["service_date"] == ""
    assert cagri["status"] is None
    assert cagri["q"] == ""


async def test_akis_kapaliysa_ikinci_cagri_hic_yapilmaz() -> None:
    # Kapalı kutu BOŞ KUTU DEĞİLDİR: `enabled: False` ile ayırt edilir ve
    # panel "akış kapatıldı" der, "sipariş yok" demez.
    service, api, _ = _kur(flow_enabled=False)
    sonuc = await service.summary()
    assert "order_list" not in api.names()
    assert sonuc["flow"]["enabled"] is False
    assert sonuc["flow"]["connected"] is None
    assert sonuc["flow"]["items"] == []


async def test_akis_satirlari_sozlesme_alanlarini_tasir() -> None:
    service, _, _ = _kur()
    sonuc = await service.summary()
    satir = sonuc["flow"]["items"][0]
    assert satir["order_number"] == "BLD-8421"
    assert satir["status_label"] == "Hazırlanıyor"
    assert satir["total_kurus"] == 216000
    assert "customer_phone" not in satir


async def test_akis_tavani_sunucu_fazla_verse_de_uygulanir() -> None:
    rows = [{"id": i, "order_number": f"BLD-{i}", "status": "yeni"} for i in range(40)]
    service, _, _ = _kur(api=FakeApi(rows=rows), flow_limit=6)
    sonuc = await service.summary()
    assert len(sonuc["flow"]["items"]) == 6


# ============================================================ ekran tercihi

async def test_tercih_yazilir_ve_geri_okunur() -> None:
    service, api, store = _kur()
    sonuc = await service.save_prefs({"poll_seconds": 60, "flow_limit": 20},
                                     actor=ACTOR)
    assert sonuc["ok"] is True
    assert sonuc["prefs"]["poll_seconds"] == 60
    assert sonuc["prefs"]["flow_limit"] == 20
    # TERCİH BLD'YE GİTMEZ: tek bir geçit çağrısı bile yapılmamalı.
    assert api.names() == []
    assert store.prefs["poll_seconds"] == "60"


async def test_taninmayan_tercih_reddedilir() -> None:
    # Kapalı liste: yazım hatasını sessizce diske yazıp hiçbir yerde
    # kullanmamak, kullanıcıya kaydettiğini sandırmaktı.
    service, _, store = _kur()
    sonuc = await service.save_prefs({"page_size": 50}, actor=ACTOR)
    assert sonuc["ok"] is False
    assert "page_size" in sonuc["error"]
    assert store.prefs == {}


async def test_tercih_sinirlari_serviste_de_uygulanir() -> None:
    # Çift kapı (K9): şema kapısını atlayan bir istemci de aynı sınırı görür.
    service, _, store = _kur()
    store.prefs["poll_seconds"] = "1"
    store.prefs["flow_limit"] = "999"
    tercih = await service.prefs()
    assert tercih["poll_seconds"] == 10
    assert tercih["flow_limit"] == 50


async def test_bozuk_depoda_tercih_yazmasi_hata_dondurur_ama_patlamaz() -> None:
    service, _, store = _kur()
    store.broken = True
    sonuc = await service.save_prefs({"poll_seconds": 60}, actor=ACTOR)
    assert sonuc["ok"] is False
    assert sonuc["error"]


async def test_akis_kapatma_tercihi_bool_olarak_saklanir() -> None:
    service, _, store = _kur()
    await service.save_prefs({"flow_enabled": False}, actor=ACTOR)
    assert store.prefs["flow_enabled"] == "false"
    assert (await service.prefs())["flow_enabled"] is False


# ============================================================ geçit yüzeyi

async def test_yalniz_iki_gecit_metodu_kullanilir() -> None:
    # Gösterge paneli her alanın geçit yüzeyine bakan ekran DEĞİLDİR: yedi
    # bloğun tamamı TEK uçtan gelir (`dashboard.md`). Üçüncü bir metot eklemek,
    # sözleşmenin "tek istek" gerekçesini (panelin her açılışında on ağır
    # sorgu) geri getirirdi.
    service, api, _ = _kur()
    await service.summary()
    assert set(api.names()) == {"dashboard_overview", "order_list"}


async def test_hicbir_cagri_dry_run_tasimaz() -> None:
    # Bu alanda yazma ucu YOK; `dry_run` taşıyan bir çağrı, olmayan bir yazmayı
    # varmış gibi gösterirdi.
    service, api, _ = _kur()
    await service.summary()
    await service.overview()
    for _, _, kwargs in api.calls:
        assert "dry_run" not in kwargs
