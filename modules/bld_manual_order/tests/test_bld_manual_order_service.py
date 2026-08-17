"""Servisin iş kuralları — geçit taklit edilir, ağa çıkılmaz.

En çok değer taşıyan iki iddia burada: HER YAZMADA `dry_run=` AÇIKÇA GEÇİYOR
(geçidin varsayılanına güvenilmiyor) ve OKUMALAR ASLA FIRLATMIYOR (K7).
"""

from __future__ import annotations

import pytest
from bld_manual_order_fakes import (
    SERVER_TIME_LATE,
    TODAY,
    FakeApi,
    FakeApiWithoutCreate,
    FakeLog,
    draft,
    make_service,
)

pytestmark = pytest.mark.asyncio


# =========================================================== yazma: dry_run

async def test_her_yazmada_dry_run_acikca_gecer_varsayilana_birakilmaz() -> None:
    # `config/local.yaml` git dışıdır ve orada `dry_run_default: true` yazıyor
    # olabilir; bayrağı atlayan bir çağrı hiçbir şey yazmadan `{"ok": true}`
    # alır ve ekran "sipariş açıldı" der. Personel telefonu kapatır, müşteri
    # aç kalır.
    api = FakeApi()
    servis = make_service(api=api)

    await servis.create(**draft())
    assert api.used("create_order")[0]["dry_run"] is False

    await servis.create(**draft(dry_run=True))
    assert api.used("create_order")[1]["dry_run"] is True


async def test_dry_run_verilmezse_modul_ayari_uygulanir() -> None:
    api = FakeApi()
    servis = make_service(api=api, config={"dry_run_default": True})
    await servis.create(**draft())
    # `None` GİTMEZ: geçidin kendi varsayılanına düşmesine izin verilmiyor.
    assert api.used("create_order")[0]["dry_run"] is True


async def test_kuru_provada_siparis_numarasi_uydurulmaz() -> None:
    # Prova "gövde doğru mu" sorusunun cevabıdır, "sipariş geçecek mi"
    # sorusunun değil: kalem geçerliliği, fiyat ve stok DENETLENMEZ.
    servis = make_service()
    sonuc = await servis.create(**draft(dry_run=True))

    assert sonuc["ok"] is True
    assert sonuc["dry_run"] is True
    assert sonuc["order"] == {}
    assert sonuc["would"]["action"] == "order.create"
    assert "DENETLENMEDİ" in sonuc["note"]


# =========================================================== yazma: gövde

async def test_kayitli_musteri_kimlikle_gider_yeni_musteri_nesneyle() -> None:
    api = FakeApi()
    servis = make_service(api=api)

    await servis.create(**draft())
    ilk = api.used("create_order")[0]
    assert ilk["customer_id"] == 312
    assert ilk["customer"] is None

    await servis.create(**draft(customer_id=0, customer_name="Acme Gıda",
                                customer_phone="0532 123 45 67"))
    ikinci = api.used("create_order")[1]
    assert ikinci["customer_id"] is None
    # TELEFON ULUSAL BİÇİMDE GİDER: aynı numaranın iki farklı yazımı gövdede
    # farklı görünürse denetim izi iki ayrı arayan gibi okunur.
    assert ikinci["customer"] == {"name": "Acme Gıda", "phone": "5321234567"}


async def test_ikisi_birden_gonderilirse_reddedilir_ve_aga_cikilmaz() -> None:
    # Sunucu `customer_id` doluysa `customer` nesnesini SESSİZCE yok sayar.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(customer_id=312, customer_name="Acme",
                                        customer_phone="5321234567"))
    assert sonuc["ok"] is False
    assert "birini bırakın" in sonuc["error"]
    assert api.names() == [], "geçersiz gövde için ağ turu harcanmamalı"


async def test_gel_al_siparisinde_adres_hic_gonderilmez() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    await servis.create(**draft(delivery_type="pickup",
                                address={"line1": "yanlışlıkla girilmiş"}))
    # Gel-al siparişe adres yazmak, listede teslimat sanılan bir kayıt üretirdi.
    assert api.used("create_order")[0]["address"] is None


async def test_teslimatta_adres_eksikse_sunucuya_gidilmez() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(delivery_type="delivery",
                                        address={"line1": "Örnek Mah."}))
    assert sonuc["ok"] is False
    assert api.names() == []


async def test_teslimat_adresi_yalniz_sozlesmedeki_alanlari_tasir() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    await servis.create(**draft(
        delivery_type="delivery",
        address={"line1": "Örnek Mah. 12. Sk No:3", "district": "Selçuklu",
                 "city": "Konya", "note": "Zili çalmayın"}))
    adres = api.used("create_order")[0]["address"]
    assert set(adres) == {"line1", "district", "city", "note"}


async def test_odeme_yontemi_account_sunucuya_hic_gitmez() -> None:
    # Kapı sunucuda da var (`422 VALIDATION_FAILED`); buradaki denetim ağ
    # turunu ve telefonda bekleyen müşteriyi bir tur beklemekten kurtarır.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(payment_method="account"))
    assert sonuc["ok"] is False
    assert "online, cash" in sonuc["error"]
    assert api.names() == []


async def test_kalemler_ayiklanmadan_gecer() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    await servis.create(**draft(items=[
        {"menu_id": 88, "quantity": 2, "option_value_ids": [4, 9], "note": "acısız"}]))
    kalem = api.used("create_order")[0]["items"][0]
    assert kalem["option_value_ids"] == [4, 9]
    assert kalem["note"] == "acısız"


async def test_aktor_her_yazmada_gider() -> None:
    # `actor` İSTİSNASIZ zorunludur: "kim yaptı" sorusunun cevabı hiçbir
    # yazmada boş kalmaz (`00-genel.md` §3). Gerekçe seyreldi, iz seyrelmedi.
    api = FakeApi()
    servis = make_service(api=api)
    await servis.create(**draft(reason=""))
    cagri = api.used("create_order")[0]
    assert cagri["actor"] == "Ayşe Yılmaz"
    assert cagri["reason"] == ""


# =========================================================== yazma: kapılar

async def test_izin_yoksa_servis_de_reddeder_cift_kapi() -> None:
    # K9: arayüzde düğmeyi gizlemek yetkilendirme değildir; istemci gövdeyi
    # elle kurabilir.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(allow_manage=False))
    assert sonuc["ok"] is False
    assert "manage" in sonuc["error"]
    assert api.names() == []


async def test_gecitte_metot_yoksa_hata_acikca_soylenir() -> None:
    # Sessizce `AttributeError` yutulsaydı, eksik metot adı DÜŞMÜŞ BİR
    # SUNUCUDAN ayırt edilemezdi: ikisi de "BLD'ye ulaşılamadı" görünürdü.
    log = FakeLog()
    servis = make_service(api=FakeApiWithoutCreate(), log=log)
    sonuc = await servis.create(**draft())

    assert sonuc["ok"] is False
    assert sonuc["code"] == "gateway_method_missing"
    assert "create_order" in sonuc["error"]
    assert "okuma bölümleri çalışmaya devam eder" in sonuc["error"]
    assert "error" in log.levels()


async def test_gecit_patlarsa_yazma_ok_false_doner_ve_istisna_sizmaz() -> None:
    api = FakeApi()
    api.fail.add("create_order")
    servis = make_service(api=api)
    sonuc = await servis.create(**draft())
    assert sonuc["ok"] is False
    assert sonuc["dry_run"] is False


async def test_sunucu_uyarisi_sessiz_gecilmez() -> None:
    # Bugün tek bir hâlde doluyor: sipariş yazıldı ama `onaylandi` geçişi
    # patladı. Sipariş VARDIR ve "olmadı" demek personelin siparişi ikinci kez
    # girmesine, müşteriye iki kere yemek çıkmasına yol açardı.
    api = FakeApi()
    api.order_payload["warnings"] = [
        'Sipariş kaydedildi ama "onaylandı" durumuna geçirilemedi.']
    servis = make_service(api=api)
    sonuc = await servis.create(**draft())

    assert sonuc["ok"] is True
    assert sonuc["warnings"]
    assert sonuc["order"]["order_number"] == "BLD-8422"


async def test_yeni_musteri_acildi_mi_yanittan_okunur() -> None:
    # AYNI TELEFONLA İKİNCİ SİPARİŞ İKİNCİ MÜŞTERİ YARATMAZ; hangisinin
    # olduğunu `customer.created` söyler ve ekran onu yazar.
    api = FakeApi()
    api.order_payload["customer"] = {"id": 411, "created": True}
    servis = make_service(api=api)
    sonuc = await servis.create(**draft())
    assert sonuc["customer"] == {"id": 411, "created": True}


# ================================================================== okuma

async def test_okumalar_gecit_dustugunde_asla_firlatmaz() -> None:
    # K7: uç yine 200 verir, panel çökmez. `ok: True` OKUMANIN BAŞARISINI
    # değil UCUN SAĞLIĞINI anlatır; ayrımı `connected` taşır.
    api = FakeApi()
    api.fail.update({"customers", "product_picker", "sales_settings",
                     "menu_day", "menu_stock"})
    servis = make_service(api=api)

    musteriler = await servis.customers(q="532", actor="Ayşe Yılmaz")
    assert musteriler["ok"] is True and musteriler["connected"] is False
    assert musteriler["items"] == []

    urunler = await servis.products()
    assert urunler["ok"] is True and urunler["connected"] is False

    gun = await servis.service_day(TODAY)
    assert gun["connected"] is False
    assert gun["error"]


async def test_kisa_arama_hic_gonderilmez() -> None:
    # Tek harflik arama bütün müşteri tablosunu döndürür ve personel telefonu
    # yazarken her tuşta bir KVKK denetim satırı doğardı.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.customers(q="53", actor="Ayşe Yılmaz")
    assert sonuc["too_short"] is True
    assert api.names() == []


async def test_musteri_okumasi_aktoru_gecide_tasir() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    await servis.customers(q="5321234567", actor="Ayşe Yılmaz")
    assert api.used("customers")[0]["actor"] == "Ayşe Yılmaz"


async def test_urun_secicisi_yalniz_satistakileri_ister() -> None:
    # Satıştan kaldırılmış bir ürün seçiciye konsaydı personel onu telefonda
    # müşteriye önerir, sunucu da `422 ITEM_UNAVAILABLE` ile reddederdi.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.products()
    assert api.used("product_picker")[0]["only_active"] is True
    assert sonuc["items"][0]["menu_id"] == 88
    assert sonuc["items"][0]["price_kurus"] == 18000


async def test_servis_gunu_kesim_stok_ve_odeme_yontemini_tek_cevapta_verir() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.service_day(TODAY)

    assert sonuc["ok"] is True and sonuc["connected"] is True
    assert sonuc["cutoff"]["cutoff"] == "08:00"
    assert sonuc["cutoff"]["source"] == "general"
    assert sonuc["payment_methods"] == ["online", "cash"]
    assert sonuc["delivery_fee_kurus"] == 2500
    assert sonuc["stock"]["items"]["27"]["remaining"] == 2
    assert api.names() == ["sales_settings", "menu_day", "menu_stock"]


async def test_gune_ozel_kesim_geneli_ezer() -> None:
    # `settings.md`: birleştirme kuralı `gün.cutoff_time ?? ayar.order_cutoff`.
    api = FakeApi()
    api.menu_day_payload["data"]["cutoff_time"] = "10:30"
    servis = make_service(api=api)
    sonuc = await servis.service_day(TODAY)
    assert sonuc["cutoff"]["cutoff"] == "10:30"
    assert sonuc["cutoff"]["source"] == "day"


async def test_kesim_gecmis_gun_kapali_isaretlenir_ama_hata_degildir() -> None:
    api = FakeApi()
    api.settings_payload["server_time"] = SERVER_TIME_LATE
    servis = make_service(api=api)
    sonuc = await servis.service_day(TODAY)
    assert sonuc["ok"] is True
    assert sonuc["cutoff"]["closed"] is True


async def test_gun_menusu_yoksa_hata_degil_bilinmiyor() -> None:
    # `adminContext: true` menü üyeliğini atlıyor: menüsü tanımlanmamış bir
    # güne de sipariş açılabilir. Kırmızı bir kutu, olağan bir durumu arıza
    # gibi gösterir ve personel gerçek arızayı ciddiye almaz.
    api = FakeApi()
    api.fail.update({"menu_day", "menu_stock"})
    api.fail_code = "not_found"
    servis = make_service(api=api)
    sonuc = await servis.service_day(TODAY)

    assert sonuc["ok"] is True
    assert sonuc["connected"] is True
    assert sonuc["error"] == ""
    assert sonuc["menu_missing"] is True
    assert sonuc["stock_missing"] is True
    # Genel kesim saati yine okundu: gün menüsünün yokluğu onu düşürmez.
    assert sonuc["cutoff"]["cutoff"] == "08:00"


async def test_stok_denetimi_tek_okuma_yapar() -> None:
    # `service_day` üç okuma yapıyor ve gün değişince BİR KEZ çağrılıyor; sepet
    # ise telefonda defalarca değişiyor. Her değişikliğe üç okuma harcamak,
    # paylaşılan hız kovasını boşuna yakardı.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.stock_check(service_date=TODAY,
                                     items=[{"menu_id": 27, "quantity": 5}])

    assert api.names() == ["menu_stock"]
    assert sonuc["ok"] is True
    assert any("tavan aşılıyor" in satir for satir in sonuc["warnings"])


async def test_stok_denetimi_engellemez_sadece_soyler() -> None:
    # `allowOvershoot: true`: aşım siparişi reddetmez, kayda geçer.
    servis = make_service()
    sonuc = await servis.stock_check(service_date=TODAY,
                                     items=[{"menu_id": 27, "quantity": 99}])
    assert sonuc["ok"] is True
    assert all("yine de açılır" in satir or "Gün toplamı" in satir
               for satir in sonuc["warnings"])


async def test_stok_okunamazsa_asim_yok_denmez() -> None:
    # Boş bir uyarı listesi, "tavan aşılmıyor" diye okunurdu; `connected:false`
    # ayrımı taşır ve panel sessizce güven vermez.
    api = FakeApi()
    api.fail.add("menu_stock")
    servis = make_service(api=api)
    sonuc = await servis.stock_check(service_date=TODAY,
                                     items=[{"menu_id": 27, "quantity": 5}])
    assert sonuc["ok"] is True
    assert sonuc["connected"] is False
    assert sonuc["warnings"] == []


async def test_yarim_sepet_hata_degil() -> None:
    # Personel henüz kalem eklemedi; bu bir taslak, arıza değil.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.stock_check(service_date=TODAY, items=[])
    assert sonuc["ok"] is True
    assert sonuc["incomplete"] is True
    assert api.names() == []


async def test_gecersiz_gun_aga_cikmadan_reddedilir() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.service_day("17.08.2026")
    assert sonuc["ok"] is False
    assert api.names() == []


async def test_acilis_ucu_aga_cikmaz() -> None:
    # Geçit düşükken bile form çizilebilmeli (K7).
    api = FakeApi()
    api.fail.update({"customers", "product_picker", "sales_settings"})
    servis = make_service(api=api)
    sonuc = await servis.overview()

    assert sonuc["ok"] is True
    assert sonuc["connected"] is None
    assert sonuc["contract"]["reason_required"] is False
    assert api.names() == []


# ================================================ anlaşmalı sepet fiyatı

async def test_anlasmali_tutar_gecide_aynen_gecer() -> None:
    # Personel telefonda 400,00 ₺ dedi; geçide giden sayı tam olarak o olmalı.
    # Aradaki her dönüşüm (bölme, yuvarlama, TL'ye çevirme) müşterinin duyduğu
    # tutardan başka bir sipariş üretirdi.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(agreed_total_kurus=40000,
                                        allow_price_override=True))

    assert sonuc["ok"] is True
    assert api.used("create_order")[0]["agreed_total_kurus"] == 40000


async def test_alan_bos_birakilinca_gecide_HIC_gonderilmez() -> None:
    # "Anlaşma yok" hâlinin tek bir biçimi olmalı: `None`. Gövdeye `null`
    # koymak, denetim izine hiçbir şey anlatmayan bir alan yazdırırdı.
    api = FakeApi()
    servis = make_service(api=api)
    await servis.create(**draft())

    assert api.used("create_order")[0]["agreed_total_kurus"] is None


async def test_fiyat_kirma_ayri_yetki_ister_cift_kapi() -> None:
    # K9, İKİNCİ ANAHTAR. Sipariş AÇMAK ile katalog fiyatını KIRMAK aynı iş
    # değil; `manage` taşıyan ama `price_override` taşımayan biri gövdeyi elle
    # kurabilir ve kapı serviste de durmalı.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(agreed_total_kurus=40000,
                                        allow_price_override=False))

    assert sonuc["ok"] is False
    assert "price_override" in sonuc["error"]
    # AĞA HİÇ ÇIKILMADI: reddedilen bir istek geçidin hız kovasından pay
    # yememeli ve denetim izine "denendi" satırı bırakmamalı.
    assert api.names() == []


async def test_yetkisiz_personel_alani_bos_birakirsa_reddedilmez() -> None:
    # Kutuya hiç dokunmamış personel, yetkisi yok diye sipariş açamaz hâle
    # gelmemeli — siparişlerin ezici çoğunluğu katalog fiyatıyla giriliyor.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(allow_price_override=False))

    assert sonuc["ok"] is True
    assert "create_order" in api.names()


async def test_sifir_tutar_aga_cikmadan_reddedilir() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(agreed_total_kurus=0,
                                        allow_price_override=True))

    assert sonuc["ok"] is False
    assert "BOŞ bırakın" in sonuc["error"]
    assert api.names() == []


async def test_kuru_provada_da_anlasmali_tutar_gider_ve_yansir() -> None:
    # Prova gövdenin nasıl OKUNDUĞUNU söyler; fiyattan hiç söz etmeyen bir
    # prova, personeli "400 yazdım ama geçti mi" belirsizliğinde bırakırdı.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.create(**draft(agreed_total_kurus=40000, dry_run=True,
                                        allow_price_override=True))

    assert sonuc["dry_run"] is True
    assert api.used("create_order")[0]["dry_run"] is True
    assert sonuc["would"]["agreed_total_kurus"] == 40000


async def test_acilis_ucu_fiyat_kirma_yetkisini_soyler_ve_varsayilani_kapali() -> None:
    # Bayrak YETKİLENDİRME DEĞİL, çizim bilgisi (asıl kapı `create` içinde).
    # Varsayılanı `False`: bayrağı vermeyi unutan bir çağıran kutuyu AÇMAZ.
    servis = make_service()
    assert (await servis.overview())["can_price_override"] is False
    assert (await servis.overview(allow_price_override=True))["can_price_override"] is True
