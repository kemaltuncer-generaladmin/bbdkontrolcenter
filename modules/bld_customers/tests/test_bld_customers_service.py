"""İş kurallarının sınaması. AĞA ÇIKMAZ.

Bu dosyanın asıl derdi bu ekranın diğerlerinden ayrıldığı yer: **KVKK.**
Sınanan iddialar sırayla:

  · HER OKUMA bir erişim satırı bırakır — başarılı da, başarısız da.
  · Erişim satırı YALNIZ SÜZGEÇLERİ taşır; dönen kayıt ASLA yazılmaz.
  · Aktörsüz okuma geçide HİÇ GİTMEZ ama DENENDİĞİ yazılır.
  · E-posta ve parola yazması geçide HİÇ GİTMEZ.
  · Yazmalarda `dry_run` HER ZAMAN açıkça geçilir (asla `None`).
  · Kuru provada BLD'ye yazma gitmez sayılmaz — gider, ama olay YAYINLANMAZ.
  · Geçit düşerse ekran ayakta kalır (K7): `ok: True`, `connected: False`.
"""

from __future__ import annotations

import pytest
from bld_customers_backend import people
from bld_customers_fakes import ACTOR, REASON, build

# ================================================================== açılış

@pytest.mark.asyncio
async def test_acilis_blde_hic_gitmez() -> None:
    service, api, store, _, _ = build()

    payload = await service.overview()

    # BU EKRANIN EN ÖNEMLİ KARARI: açılışta sayaç çekmek, her biri bir
    # `customer.read` satırı yazan istekler atmak olurdu — kimsenin sormadığı
    # bir soru için deftere satır.
    assert api.calls == []
    assert store.access == []
    assert payload["ok"] is True
    assert "KVKK" not in payload  # uyarı METNİ döner, bayrak değil
    assert payload["kvkk_notice"]
    assert payload["readonly_notice"]
    # `connected` YOKTUR: bağlantı durumu ancak gerçek bir okumada bilinir.
    assert "connected" not in payload


# ============================================================ KVKK okuma izi

@pytest.mark.asyncio
async def test_her_okuma_erisim_izi_birakir() -> None:
    service, _, store, _, _ = build()

    await service.customers(actor=ACTOR, q="acme")
    await service.customer(312, actor=ACTOR)
    await service.orders(312, actor=ACTOR)
    await service.subscriptions(312, actor=ACTOR)
    await service.addresses(312, actor=ACTOR)
    await service.sms(312, actor=ACTOR)

    assert store.scopes() == ["list", "detail", "orders", "subscriptions",
                              "addresses", "sms"]
    assert all(row["result"] == people.READ_OK for row in store.access)
    assert all(row["actor"] == ACTOR for row in store.access)


@pytest.mark.asyncio
async def test_erisim_izi_yalniz_suzgecleri_tasir() -> None:
    service, _, store, _, _ = build()

    await service.customers(actor=ACTOR, q="acme", status="active", page=2)

    filtreler = store.filters(0)
    assert filtreler["q"] == "acme"
    assert filtreler["status"] == "active"
    assert filtreler["page"] == 2
    # DÖNEN KAYITLAR ASLA YAZILMAZ (sözleşme §9.4) — yazılsaydı denetim izi
    # ikinci bir müşteri veritabanına dönerdi.
    ham = store.access[0]["filters"]
    assert "5321234567" not in ham
    assert "mehmet.kaya@acme.com.tr" not in ham
    assert "Kaya" not in ham


@pytest.mark.asyncio
async def test_sms_okumasinin_yerel_eylemi_ayridir() -> None:
    service, _, store, _, _ = build()

    await service.customer(312, actor=ACTOR)
    await service.sms(312, actor=ACTOR)

    # Sunucu SMS okuması için `customer.read` satırı yazmaz (uç başka alanda);
    # yerel defterde ayrı ad taşır ki iki defter karşılaştırılabilsin.
    assert store.read_actions() == [people.READ_ACTION, people.SMS_READ_ACTION]


@pytest.mark.asyncio
async def test_aktorsuz_okuma_gecide_gitmez_ama_denendigi_yazilir() -> None:
    service, api, store, _, _ = build()

    payload = await service.customers(actor="")

    assert api.calls == []
    assert payload["connected"] is False
    assert payload["code"] == "actor_required"
    # Adı olmayan bir oturumun müşteri defterini açmaya çalışması, tam olarak
    # bu defterin kaydetmesi gereken şeydir.
    assert len(store.access) == 1
    assert store.access[0]["result"] == people.READ_FAILED


@pytest.mark.asyncio
async def test_basarisiz_okuma_da_iz_birakir_ve_ekran_ayakta_kalir() -> None:
    service, api, store, _, log = build()
    api.fail.add("customers")
    api.fail_code = "control_endpoint_missing"

    payload = await service.customers(actor=ACTOR)

    # K7: uç 200 döner, panel çökmez; ayrımı `connected` taşır.
    assert payload["ok"] is True
    assert payload["connected"] is False
    assert payload["code"] == "control_endpoint_missing"
    assert payload["items"] == []
    assert store.access[0]["result"] == people.READ_FAILED
    assert "müşteri okuması başarısız" in log.levels("warning")


@pytest.mark.asyncio
async def test_iz_yazilamazsa_okuma_durmaz_ama_hata_seviyesinde_bildirilir() -> None:
    service, _, store, _, log = build()
    store.broken = True

    payload = await service.customers(actor=ACTOR)

    # Sunucu tarafı denetim satırını ZATEN yazdı; yerel defterin yazılamaması
    # yöneticiyi ekrandan mahrum bırakmayı haklı çıkarmaz.
    assert payload["connected"] is True
    assert payload["items"]
    # Uyarı DEĞİL hata: bu bir gözetim boşluğudur.
    assert "KVKK erişim izi yazılamadı" in log.levels("error")


@pytest.mark.asyncio
async def test_erisim_izi_musteriye_ve_aktore_gore_suzulur() -> None:
    service, _, _, _, _ = build()

    await service.customer(312, actor=ACTOR)
    await service.customer(313, actor="Veli Demir")

    hepsi = await service.access_log()
    assert len(hepsi["items"]) == 2

    tek = await service.access_log(customer_id=312)
    assert [row["customer_id"] for row in tek["items"]] == [312]

    kisi = await service.access_log(actor="Veli Demir")
    assert [row["actor"] for row in kisi["items"]] == ["Veli Demir"]


# ================================================================== okuma

@pytest.mark.asyncio
async def test_arama_gecide_aktoru_ve_temiz_suzgeci_gecirir() -> None:
    service, api, _, _, _ = build()

    payload = await service.customers(actor=ACTOR, q="a", status="uydurma",
                                      has_subscription=True, per_page=250)

    cagri = api.used("customers")[0]
    assert cagri["actor"] == ACTOR
    # Tek harflik arama isteğe KONMAZ (sunucu 422 verirdi).
    assert cagri["q"] == ""
    # Bilinmeyen süzgeç varsayılana düşer, uydurma değer gönderilmez.
    assert cagri["status"] == "all"
    # Üç değerli bayrak olduğu gibi gider.
    assert cagri["has_subscription"] is True
    # Tavan kırpılır: 250 istemek sessizce 100 almak olurdu.
    assert cagri["per_page"] == 100
    assert payload["meta"]["total"] == 214


@pytest.mark.asyncio
async def test_musteri_karti_istatistikleri_ayni_yanitta_getirir() -> None:
    service, api, _, _, _ = build()

    payload = await service.customer(312, actor=ACTOR)

    # `stats` ayrı bir uçta olsaydı ikinci bir denetim satırı yazardı.
    assert len(api.calls) == 1
    assert payload["customer"]["stats"]["total_spent_kurus"] == 27648000
    assert payload["customer"]["email"] == "mehmet.kaya@acme.com.tr"


@pytest.mark.asyncio
async def test_adres_defteri_salt_okunur_isaretlenir() -> None:
    service, _, _, _, _ = build()

    payload = await service.addresses(312, actor=ACTOR)

    # Adres yazan bir uç YOK; panel düğme çizmemek için bunu okur.
    assert payload["read_only"] is True
    assert payload["items"][0]["city"] == "Ankara"


@pytest.mark.asyncio
async def test_abonelikler_sayfalanmaz() -> None:
    service, _, _, _, _ = build()

    payload = await service.subscriptions(312, actor=ACTOR)

    # Bir müşterinin abonelik sayısı tek hanelidir (sözleşme); boş bir `meta`
    # yollamak istemciye olmayan bir sayfalayıcı çizdirirdi.
    assert "meta" not in payload
    assert payload["items"][0]["unpaid_total_kurus"] == 640000


@pytest.mark.asyncio
async def test_sms_kaydi_segment_toplamini_tasir() -> None:
    service, api, _, _, _ = build()

    payload = await service.sms(312, actor=ACTOR)

    assert api.used("sms_log")[0]["customer_id"] == 312
    assert payload["segment_total"] == 6840
    # Telefon SUNUCUDA maskeli gelir; ikinci bir maske okunamaz hâle getirirdi.
    assert payload["items"][0]["phone"] == "532****567"


@pytest.mark.asyncio
async def test_sms_meta_segment_vermezse_sifir_degil_bilinmiyor() -> None:
    service, api, _, _, _ = build()
    api.sms_meta = {"page": 1, "per_page": 25, "total": 3, "last_page": 1}

    payload = await service.sms(312, actor=ACTOR)

    # Sıfır yazmak "hiç segment harcanmadı" demek olurdu.
    assert payload["segment_total"] == -1


# ================================================================== yazma

@pytest.mark.asyncio
async def test_guncelleme_taze_okur_yazar_ve_iz_birakir() -> None:
    service, api, store, _, _ = build()

    payload = await service.update(312, fields={"telephone": "5329876543"},
                                   reason=REASON, actor=ACTOR, dry_run=False)

    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["audit_id"] == 2001
    # Yazmadan önce TAZE OKUMA: eski değer ancak oradan gelir.
    assert api.names() == ["customer", "update_customer"]
    # Yazma zinciri: denendi → ok.
    assert store.results("customer.update") == [people.TRIED, people.DONE]


@pytest.mark.asyncio
async def test_guncelleme_izi_telefonu_maskeler() -> None:
    service, _, store, _, _ = build()

    await service.update(312, fields={"telephone": "5329876543"},
                         reason=REASON, actor=ACTOR, dry_run=False)

    degisiklikler = store.detail(0)["changes"]
    assert degisiklikler == [{"field": "telephone", "from": "532****567",
                             "to": "532****543"}]
    # Ham numara denetim izinin HİÇBİR yerinde geçmemeli.
    assert "5329876543" not in store.audit[0]["detail"]


@pytest.mark.asyncio
async def test_yazma_her_zaman_acik_dry_run_gecirir() -> None:
    service, api, _, _, _ = build()

    await service.update(312, fields={"org_name": "Yeni A.Ş."}, reason=REASON,
                         actor=ACTOR)          # bayrak HİÇ verilmedi

    # `None` geçide gönderilseydi geçit kendi ayarına düşerdi ve o ayar
    # `config/local.yaml` ile açılmış olabilir — "kaydedildi" diyen ekranın
    # arkasında hiçbir yere yazılmamış bir değer kalırdı.
    assert api.used("update_customer")[0]["dry_run"] is False


@pytest.mark.asyncio
async def test_kuru_prova_yaniti_sunucudan_okunur() -> None:
    service, _, store, _, _ = build({"dry_run_default": True})

    payload = await service.update(312, fields={"org_name": "Yeni A.Ş."},
                                   reason=REASON, actor=ACTOR)

    # Bir kurulum provayı ayardan açarsa ekran "yapıldı" DEMEMELİ.
    assert payload["dry_run"] is True
    assert store.results("customer.update") == [people.TRIED, people.DRY]


@pytest.mark.asyncio
@pytest.mark.parametrize("alan", ["email", "password", "account_type", "status"])
async def test_yasak_alan_gecide_hic_gitmez(alan: str) -> None:
    service, api, store, _, _ = build()

    payload = await service.update(312, fields={alan: "x"}, reason=REASON,
                                   actor=ACTOR, dry_run=False)

    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["error"] == people.FORBIDDEN_FIELDS[alan]
    # Taze okuma bile YAPILMAZ: gövde daha kapıda reddedildi.
    assert api.calls == []
    assert store.audit == []


@pytest.mark.asyncio
async def test_degismeyen_yazma_gonderilmez() -> None:
    service, api, _, _, _ = build()

    payload = await service.update(312, fields={"telephone": "5321234567"},
                                   reason=REASON, actor=ACTOR, dry_run=False)

    # Gönderilseydi sunucu denetim izine "güncellendi" diye bir satır yazar ve
    # gerçek değişiklikleri arayan kişi onun içinde kaybolurdu.
    assert payload["blocked"] is True
    assert api.writes() == []


@pytest.mark.asyncio
async def test_sunucunun_changed_listesi_bizimkini_ezer() -> None:
    service, api, _, _, _ = build()
    # Sahte geçit gönderilen alan adlarını geri veriyor; gerçek sunucu bir
    # alanı reddedip ötekini yazabilir ve o zaman doğru olan onunkidir.
    payload = await service.update(312, fields={"org_name": "Yeni A.Ş.",
                                                "contact_person": "Zeynep Demir"},
                                   reason=REASON, actor=ACTOR, dry_run=False)

    assert payload["changed"] == sorted(["org_name", "contact_person"])
    assert api.used("update_customer")[0]["org_name"] == "Yeni A.Ş."


@pytest.mark.asyncio
async def test_kisa_gerekce_gecide_gitmez() -> None:
    service, api, store, _, _ = build()

    payload = await service.update(312, fields={"org_name": "X"}, reason="kısa",
                                   actor=ACTOR, dry_run=False)

    assert payload["blocked"] is True
    assert api.calls == []
    assert store.audit == []


# ============================================================ hesap kapatma

@pytest.mark.asyncio
async def test_kapatma_izinsiz_engellenir_ve_iz_birakir() -> None:
    service, api, store, bus, _ = build()

    payload = await service.disable(312, reason=REASON, actor=ACTOR, dry_run=False,
                                    allow_destructive=False)

    # ÇİFT KAPI (K9): uç noktanın izni gevşetilse bile burası durur.
    assert payload["blocked"] is True
    assert api.calls == []
    assert store.results("customer.disable") == [people.BLOCKED]
    assert bus.events == []


@pytest.mark.asyncio
async def test_kapatma_uyariyi_tasir_ve_olay_yayinlar() -> None:
    service, api, store, bus, _ = build()
    api.disable_warnings = [{"code": "active_subscriptions", "subscription_ids": [18]}]

    payload = await service.disable(312, reason=REASON, actor=ACTOR, dry_run=False,
                                    allow_destructive=True)

    assert payload["ok"] is True
    assert payload["already"] is False
    assert payload["active_subscriptions"] == 1
    # Uyarı OLDUĞU GİBİ taşınır: ayıklamak, yarın eklenecek bir kodu sessizce
    # düşürürdü.
    assert payload["warnings"] == [{"code": "active_subscriptions",
                                    "subscription_ids": [18]}]
    assert store.results("customer.disable") == [people.TRIED, people.DONE]
    assert bus.names() == ["bld_customers.account_disabled"]


@pytest.mark.asyncio
async def test_kapatma_olayinda_kisisel_veri_yoktur() -> None:
    service, _, _, bus, _ = build()

    await service.disable(312, reason=REASON, actor=ACTOR, dry_run=False,
                          allow_destructive=True)

    _, yuk = bus.events[0]
    # Olay yolu bir kişisel veri kanalı değildir; dinleyicileri KVKK yüzeyine
    # sokmak istemiyoruz.
    assert set(yuk) == {"customerId", "name", "reason", "actor"}
    assert "5321234567" not in str(yuk)
    assert "mehmet.kaya@acme.com.tr" not in str(yuk)


@pytest.mark.asyncio
async def test_kuru_provada_olay_yayinlanmaz() -> None:
    service, _, _, bus, _ = build()

    payload = await service.disable(312, reason=REASON, actor=ACTOR, dry_run=True,
                                    allow_destructive=True)

    # BLD'de hiçbir şey değişmedi; dinleyicileri "hesap kapatıldı" diye
    # uyandırmak yalan olurdu.
    assert payload["dry_run"] is True
    assert bus.events == []


@pytest.mark.asyncio
async def test_zaten_kapali_hesapta_olay_yayinlanmaz() -> None:
    service, api, _, bus, _ = build()
    api.detail_row = {**api.detail_row, "status": False}

    payload = await service.disable(312, reason=REASON, actor=ACTOR, dry_run=False,
                                    allow_destructive=True)

    # Sözleşme `409` vermiyor; istek gider ve `ok: true` döner. Ama yeni bir
    # olay olmadı — panel de "kapatıldı" yerine "zaten kapalıydı" der.
    assert payload["ok"] is True
    assert payload["already"] is True
    assert bus.events == []


@pytest.mark.asyncio
async def test_dinleyici_patlasa_bile_kapatma_basarilidir() -> None:
    service, _, _, bus, log = build()
    bus.fail = True

    payload = await service.disable(312, reason=REASON, actor=ACTOR, dry_run=False,
                                    allow_destructive=True)

    # Hesap BLD'de kapatıldı; dinleyicinin patlaması onu geri açmaz (K7).
    assert payload["ok"] is True
    assert "olay yayınlanamadı" in log.levels("warning")


@pytest.mark.asyncio
async def test_acma_ayri_izin_istemez() -> None:
    service, api, store, _, _ = build()
    api.detail_row = {**api.detail_row, "status": False}

    payload = await service.enable(312, reason=REASON, actor=ACTOR, dry_run=False)

    # Kapatmak yıkıcı, açmak onarıcıdır: `allow_destructive` parametresi bile
    # yoktur. Açmayı da üçüncü anahtara bağlamak, yanlışlıkla kapatılmış bir
    # hesabı düzeltebilecek kişi sayısını azaltırdı.
    assert payload["ok"] is True
    assert payload["already"] is False
    assert api.used("enable_customer")[0]["dry_run"] is False
    assert store.results("customer.enable") == [people.TRIED, people.DONE]


@pytest.mark.asyncio
async def test_gecit_yazmada_patlarsa_iz_hata_ile_kapanir() -> None:
    service, api, store, _, _ = build()
    api.fail.add("update_customer")

    payload = await service.update(312, fields={"org_name": "Yeni A.Ş."},
                                   reason=REASON, actor=ACTOR, dry_run=False)

    assert payload["ok"] is False
    # "denendi" satırı çağrıdan ÖNCE düştü; ağ koparsa geriye YALNIZ o kalır.
    assert store.results("customer.update") == [people.TRIED, people.FAILED]


# ================================================================ tercihler

@pytest.mark.asyncio
async def test_tercih_yazilir_ve_okunur() -> None:
    service, _, _, _, _ = build()

    payload = await service.save_prefs({"page_size": 50, "sort": "created"}, actor=ACTOR)

    assert payload["ok"] is True
    assert payload["page_size"] == 50
    assert payload["sort"] == "created"
    assert (await service.prefs())["page_size"] == 50


@pytest.mark.asyncio
async def test_taninmayan_tercih_reddedilir() -> None:
    service, _, store, _, _ = build()

    payload = await service.save_prefs({"tema": "koyu"}, actor=ACTOR)

    # Sessizce yutulan bir tercih, kaydettiğini sanan kullanıcıya her açılışta
    # eski ekranı gösterirdi.
    assert payload["blocked"] is True
    assert store.prefs == {}


@pytest.mark.asyncio
async def test_tercih_yazmasi_erisim_izine_dusmez() -> None:
    service, _, store, _, _ = build()

    await service.save_prefs({"page_size": 50}, actor=ACTOR)

    # Tercih müşteri verisine dokunmuyor; KVKK defterine satır eklemek izi
    # anlamsız satırlarla doldururdu.
    assert store.access == []
