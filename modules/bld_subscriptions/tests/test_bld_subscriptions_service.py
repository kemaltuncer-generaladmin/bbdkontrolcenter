"""İş kuralları — okuma, yazma zinciri, kuru prova, K7.

Testlerin hepsi `FakeApi` üzerinden gider ve geçidin metot adlarını BİREBİR
kullanır. Uydurma bir ad testleri yeşil tutar ama canlıda `AttributeError`
verir ve servis onu K7 gereği yuttuğu için hata ekranda "BLD'ye ulaşılamadı"
diye görünür — yani yanlış metot adı DÜŞMÜŞ BİR SUNUCUDAN AYIRT EDİLEMEZ.
"""

from __future__ import annotations

from typing import Any

import pytest
from bld_subscriptions_fakes import (
    GEREKCE,
    SUBSCRIPTION_ROW,
    FakeApi,
    FakeBus,
    FakeStore,
    make_service,
)

PRICE = 16000


def _block(**over: Any) -> dict[str, Any]:
    body = {
        "start_date": "2026-09-01",
        "end_date": "",
        "delivery_type": "delivery",
        "delivery_time_from": "11:30",
        "delivery_time_to": "12:30",
        "service_days": [1, 2, 3, 4, 5],
        "menu_mode": "daily_menu",
        "default_quantity": 20,
        "agreed_unit_price_kurus": PRICE,
        "lines": [],
        "delivery_points": [{"address_id": 704, "quantity": 20, "note": None}],
        "location_id": 1,
    }
    body.update(over)
    return body


# ==================================================================== okuma

async def test_overview_aga_cikmaz_ve_sozlesmeyi_yerelden_verir() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.overview()

    assert payload["ok"] is True
    # `connected` YOK değil `None`: bu uç ağa hiç çıkmadı ve bilinen bir
    # kopukluğu "düzeldi" saymamalı.
    assert payload["connected"] is None
    assert api.names() == []
    assert payload["contract"]["reason"]["max"] == 500


async def test_liste_sayfa_sayaclarini_ve_odenmemis_toplami_verir() -> None:
    api = FakeApi(rows=[dict(SUBSCRIPTION_ROW),
                        {**SUBSCRIPTION_ROW, "id": 19, "status": "paused",
                         "unpaid_total_kurus": 0, "unpaid_periods": 0}])
    service = make_service(api=api)
    payload = await service.subscriptions()

    assert payload["ok"] is True and payload["connected"] is True
    assert payload["page_counts"]["active"] == 1
    assert payload["page_counts"]["paused"] == 1
    # SAYFA toplamıdır, genel toplam değil; panel bu ayrımı yazıyla söyler.
    assert payload["page_unpaid_kurus"] == 640000


async def test_taninmayan_durum_suzgeci_baglanti_hatasi_gibi_gorunmez() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.subscriptions(status="uydurma")

    assert payload["ok"] is False
    assert payload["connected"] is None      # bağlantı sorunu DEĞİL
    assert api.names() == []                 # istek hiç çıkmadı


async def test_gecit_dusunce_okuma_ISTISNA_SIZDIRMAZ(caplog: Any) -> None:
    api = FakeApi()
    api.fail.add("subscriptions")
    service = make_service(api=api)
    payload = await service.subscriptions()

    # K7: uç 200 verir, panel çökmez. Ayrımı `connected` taşır.
    assert payload["ok"] is True
    assert payload["connected"] is False
    assert payload["items"] == []
    assert payload["missing_endpoint"] is False


async def test_dagitilmamis_uc_ARIZA_DEGIL_ayri_isaretlenir() -> None:
    # Sunucu tarafı paralel yazılıyor; geçit `control_endpoint_missing` veriyor
    # ve ekran ZARİFÇE bozulmalı. Kırmızı hata kutusu, personelin her açılışta
    # var olmayan bir sorunu bildirmesi olurdu.
    api = FakeApi()
    api.fail.add("subscription_contracts")
    api.fail_code = "control_endpoint_missing"
    service = make_service(api=api)
    payload = await service.contracts(18)

    assert payload["ok"] is True
    assert payload["connected"] is False
    assert payload["missing_endpoint"] is True
    assert payload["code"] == "control_endpoint_missing"


async def test_sozlesme_listesi_acik_ve_imzali_kaydi_isaretler() -> None:
    api = FakeApi()
    api.contract_rows = [{**api.contract_rows[0], "id": 7, "status": "signed"},
                         {**api.contract_rows[0], "id": 8, "status": "sent"}]
    service = make_service(api=api)
    payload = await service.contracts(18)

    # Açık sözleşme varken yenisi açılamaz (sunucu 409): ekran düğmeyi buna
    # göre kapatır ve nedenini yazar.
    assert payload["open_contract_id"] == 8
    assert payload["signed_contract_id"] == 7


async def test_odemeler_meta_toplamlarini_YENIDEN_TOPLAMAZ() -> None:
    # Satırların toplamını almak, sunucunun `void` kayıtları nasıl saydığını
    # tahmin etmek olurdu.
    service = make_service()
    payload = await service.payments(18)
    assert payload["meta"]["overdue_kurus"] == 640000
    assert payload["overdue_count"] == 1


async def test_takvim_penceresi_92_gunu_asamaz() -> None:
    api = FakeApi()
    service = make_service(api=api)
    await service.calendar(18, days=500)
    assert api.used("subscription_calendar")[0]["days"] == 92


# ============================================================== yazma zinciri

async def test_yazma_izi_gecit_cagrisindan_ONCE_dusuyor() -> None:
    # Ağ koparsa geriye YALNIZ bu satır kalır: sözleşme gönderilirken bağlantı
    # düşerse müşteriye SMS gidip gitmediği belirsizdir ve "kim denedi"
    # sorusunun cevabı başka hiçbir yerde yoktur.
    api = FakeApi()
    api.fail.add("create_subscription_contract")
    store = FakeStore()
    service = make_service(api=api, store=store)

    payload = await service.create_contract(18, reason=GEREKCE, actor="Ayşe Yılmaz")

    assert payload["ok"] is False
    assert store.results("subscription.contract.create") == ["denendi", "hata"]


async def test_gerekce_kisa_ise_istek_HIC_CIKMAZ() -> None:
    api = FakeApi()
    store = FakeStore()
    service = make_service(api=api, store=store)

    payload = await service.activate(18, reason="kısa", actor="Ayşe Yılmaz")

    assert payload["ok"] is False
    assert api.names() == []
    assert store.audit == []       # denenmemiş bir işlem ize de yazılmaz


async def test_her_yazmada_dry_run_ACIKCA_gecer() -> None:
    # Geçidin varsayılanına GÜVENİLMEZ: `config/local.yaml` git dışıdır ve
    # orada `true` yazıyor. Bayrağı atlayan bir modül hiçbir şey yazmadan
    # `{"ok": true}` alır ve ekran "kaydedildi" der.
    api = FakeApi()
    service = make_service(api=api)

    await service.activate(18, reason=GEREKCE, actor="A")
    await service.resume(18, reason=GEREKCE, actor="A")
    await service.cancel(18, effective_date="2026-08-31", reason=GEREKCE, actor="A")
    await service.create_contract(18, reason=GEREKCE, actor="A")
    await service.mark_paid(41, method="online", reason=GEREKCE, actor="A")

    yazmalar = [kwargs for name, _, kwargs in api.calls if "dry_run" in kwargs]
    assert len(yazmalar) == 5
    assert all(kwargs["dry_run"] is False for kwargs in yazmalar)


async def test_kuru_provada_UZAGA_ISTEK_GIDER_ama_olay_yayinlanmaz() -> None:
    # Sözleşme §3.1: kuru prova isteği GERÇEKTEN gönderir, yalnız sunucu
    # `$apply`'ı çağırmaz. BLD'de hiçbir şey değişmediği için dinleyicileri
    # "durum değişti" diye uyandırmak yalan olurdu.
    api = FakeApi()
    bus = FakeBus()
    store = FakeStore()
    service = make_service(api=api, store=store, bus=bus)

    payload = await service.activate(18, reason=GEREKCE, actor="A", dry_run=True)

    assert payload["ok"] is True and payload["dry_run"] is True
    assert payload["announced"] is False
    assert api.used("activate_subscription")[0]["dry_run"] is True
    assert bus.events == []
    assert store.results("subscription.activate") == ["denendi", "dry_run"]


async def test_iz_yazilamazsa_is_DURMAZ() -> None:
    api = FakeApi()
    store = FakeStore()
    store.broken = True
    service = make_service(api=api, store=store)

    payload = await service.activate(18, reason=GEREKCE, actor="A")

    assert payload["ok"] is True             # K7 — iz ikincil, iş birincil
    assert api.names().count("activate_subscription") == 1


async def test_dinleyici_patlarsa_yazma_yine_basarilidir() -> None:
    bus = FakeBus()
    bus.fail = True
    service = make_service(bus=bus)
    payload = await service.activate(18, reason=GEREKCE, actor="A")
    assert payload["ok"] is True
    assert payload["announced"] is True      # yayın DENENDİ; başarısı iş değil


# ================================================================ abonelik

async def test_yeni_abonelikte_tele_giden_odeme_kipi_prepaid_monthly() -> None:
    # Servis alanı HİÇ GEÇMEZ, geçidin varsayılanı koyar (imza `prepaid_monthly`
    # ile doğuyor). Ekrandan seçtirmek, geçidin istek çıkmadan kestiği bir
    # seçeneği seçilebilir göstermek olurdu — cari hesap kalktı (iş kararı 1).
    # Sınanan şey TELE GİDEN değer: alanın gövdede olup olmaması geçidin işi,
    # bu ekranın taahhüdü ise "asla `account` göndermem".
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.create(customer_id=312, reason=GEREKCE, actor="A",
                                   **{key: value for key, value in _block().items()
                                      if key in ("start_date", "service_days",
                                                 "default_quantity", "delivery_type",
                                                 "menu_mode", "delivery_points",
                                                 "agreed_unit_price_kurus")})
    assert payload["ok"] is True
    assert api.used("create_subscription")[0]["payment_mode"] == "prepaid_monthly"


async def test_yeni_abonelik_fiyati_AYRI_SUTUNA_yazilir() -> None:
    # "Fiyatı kim, ne zaman, neden anlaştı" bu ekranın en çok sorulan sorusu;
    # JSON içinden aranan bir alan ne sıralanabilir ne indekslenebilir.
    store = FakeStore()
    service = make_service(store=store)
    await service.create(customer_id=312, start_date="2026-09-01",
                         service_days=[1, 2, 3], default_quantity=20,
                         agreed_unit_price_kurus=PRICE,
                         delivery_points=[{"address_id": 704, "quantity": 20}],
                         reason=GEREKCE, actor="Ayşe Yılmaz")
    assert store.prices("subscription.create") == [PRICE, PRICE]


@pytest.mark.parametrize(("over", "parca"), [
    ({"service_days": []}, "en az bir servis günü"),
    ({"default_quantity": 0}, "en az 1"),
    ({"menu_mode": "fixed_list", "lines": []}, "kalem verilmedi"),
    ({"menu_mode": "daily_menu", "lines": [{"menu_id": 27}]}, "kalem listesi gönderilemez"),
    ({"delivery_type": "delivery", "delivery_points": []}, "teslimat noktası"),
    ({"end_date": "2026-08-01"}, "başlangıçtan sonra"),
    ({"start_date": "01.09.2026"}, "YYYY-AA-GG"),
])
async def test_yeni_abonelik_on_denetimleri_sozlesmeden(over: dict[str, Any],
                                                        parca: str) -> None:
    # Sunucu hepsini TEKRAR denetliyor (K9 — çift kapı); buradaki kapı ağ
    # turunu ve anlamsız bir denetim satırını önler.
    api = FakeApi()
    service = make_service(api=api)
    block = _block(**over)
    payload = await service.create(
        customer_id=312, reason=GEREKCE, actor="A", start_date=block["start_date"],
        service_days=block["service_days"], default_quantity=block["default_quantity"],
        delivery_type=block["delivery_type"], menu_mode=block["menu_mode"],
        end_date=block["end_date"], lines=block["lines"],
        delivery_points=block["delivery_points"])

    assert payload["ok"] is False
    assert parca.lower() in payload["error"].lower()
    assert api.names() == []


async def test_musteri_kimligi_zorunlu_ve_bu_uc_musteri_YARATMAZ() -> None:
    service = make_service()
    payload = await service.create(customer_id=0, start_date="2026-09-01",
                                   service_days=[1], default_quantity=20,
                                   delivery_points=[{"address_id": 1}],
                                   reason=GEREKCE, actor="A")
    assert payload["ok"] is False
    assert "müşteri YARATMAZ" in payload["error"]


async def test_kural_guncelleme_uyarilari_YUTMAZ() -> None:
    # Yarın için sipariş zaten üretildiyse bugün adedi değiştirmek onu
    # değiştirmez; uyarıyı yutan bir ekran yöneticiye yalan söylerdi.
    service = make_service()
    payload = await service.update(18, reason=GEREKCE, actor="A",
                                   changes={"default_quantity": 25})
    assert payload["ok"] is True
    assert payload["warnings"][0]["code"] == "generated_orders_unaffected"


async def test_kural_guncellemede_yazilamayan_alan_reddedilir() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.update(18, reason=GEREKCE, actor="A",
                                   changes={"customer_id": 5})
    assert payload["ok"] is False
    assert "customer_id" in payload["error"]
    assert api.names() == []


async def test_bos_guncelleme_denetim_izine_bos_satir_yazmaz() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.update(18, reason=GEREKCE, actor="A", changes={})
    assert payload["ok"] is False
    assert api.names() == []


async def test_durum_yazmasi_ONCEKI_durumu_izden_ve_olaydan_okunur_kilar() -> None:
    api = FakeApi()
    bus = FakeBus()
    store = FakeStore()
    service = make_service(api=api, store=store, bus=bus)

    payload = await service.pause(18, start_date="2026-09-01", end_date="2026-09-14",
                                  reason=GEREKCE, actor="Ayşe Yılmaz")

    assert payload["ok"] is True
    assert payload["announced"] is True
    olay, yuk = bus.events[0]
    assert olay == "bld_subscriptions.subscription_status_changed"
    assert yuk["from"] == "active" and yuk["to"] == "paused"
    assert yuk["action"] == "subscription.pause"
    assert store.detail(0)["from"] == "active"


async def test_duraklatma_uyarisi_uretilmis_siparisleri_LISTELER() -> None:
    service = make_service()
    payload = await service.pause(18, start_date="2026-09-01", end_date="2026-09-14",
                                  reason=GEREKCE, actor="A")
    assert payload["warnings"][0]["code"] == "generated_orders_in_range"
    assert payload["warnings"][0]["order_ids"] == [8501, 8502]


async def test_taze_okuma_dusse_bile_yazma_devam_eder() -> None:
    # Okumanın düşmesi yazmayı engellememeli; yalnız `from` bilinmez kalır.
    api = FakeApi()
    api.fail.add("subscription")
    bus = FakeBus()
    service = make_service(api=api, bus=bus)

    payload = await service.resume(18, reason=GEREKCE, actor="A")

    assert payload["ok"] is True
    assert bus.events[0][1]["from"] == ""


async def test_iptal_gecerlilik_gunu_ister() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.cancel(18, effective_date="", reason=GEREKCE, actor="A")
    assert payload["ok"] is False
    assert "zorunlu" in payload["error"]
    assert api.names() == []


# ============================================================== istisna/üretim

async def test_atla_ile_adet_birlikte_gonderilemez() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.create_exception(18, service_date="2026-08-20",
                                             skip=True, quantity_override=12,
                                             reason=GEREKCE, actor="A")
    assert payload["ok"] is False
    assert "tutarsız" in payload["error"]
    assert api.names() == []


async def test_ne_atla_ne_adet_olan_istisna_reddedilir() -> None:
    service = make_service()
    payload = await service.create_exception(18, service_date="2026-08-20",
                                             reason=GEREKCE, actor="A")
    assert payload["ok"] is False


async def test_atlanan_gunde_adet_gecide_HIC_gonderilmez() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.create_exception(18, service_date="2026-08-20", skip=True,
                                             reason=GEREKCE, actor="A")
    assert payload["ok"] is True
    assert api.used("create_subscription_exception")[0]["quantity_override"] is None


async def test_elle_uretim_release_now_bayragini_ACIKCA_tasir() -> None:
    # Sessizce erken düşen kırk sipariş, sabah işbaşı yapan mutfağın panosunu
    # doldurup o an gelen gerçek bir siparişi görünmez kılardı.
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.generate(18, service_date="2026-08-17", reason=GEREKCE,
                                     actor="A", release_now=True)
    assert payload["ok"] is True
    assert api.used("generate_subscription_orders")[0]["release_now"] is True
    assert payload["created"][0]["order_id"] == 8455
    assert payload["skipped"] == []


async def test_erken_serbest_birakmanin_denetim_hedefi_SIPARISTIR() -> None:
    # Soru "bu sipariş neden erken düştü" biçiminde sorulur (sözleşme denetim
    # tablosu); hedef abonelik olsaydı iz o soruya cevap veremezdi.
    store = FakeStore()
    service = make_service(store=store)
    payload = await service.release_order(8455, reason=GEREKCE, actor="A")

    assert payload["ok"] is True
    assert payload["order_id"] == 8455
    assert {row["target_type"] for row in store.actions("subscription.order.release")} \
        == {"order"}
    assert {int(row["target_id"]) for row
            in store.actions("subscription.order.release")} == {8455}


# ================================================================== talepler

async def test_talep_donusumu_YENI_ABONELIK_ile_ayni_denetimden_gecer() -> None:
    # Ayrı iki denetim, talepten açılan aboneliğin elle açılandan farklı
    # kurallara tabi olması demekti.
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.convert_request(88, customer_id=312,
                                            subscription=_block(service_days=[]),
                                            reason=GEREKCE, actor="A")
    assert payload["ok"] is False
    assert "servis günü" in payload["error"].lower()
    assert api.names() == []


async def test_talep_donusumu_odeme_kipini_govdeye_koyar_ve_pending_dogar() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.convert_request(88, customer_id=312, subscription=_block(),
                                            reason=GEREKCE, actor="A")

    assert payload["ok"] is True
    govde = api.used("convert_quote_request")[0]["subscription"]
    assert govde["payment_mode"] == "prepaid_monthly"
    # `daily_menu` iken `lines` gövdeye HİÇ konmaz (sunucu 422 verirdi).
    assert "lines" not in govde
    assert payload["subscription_status"] == "pending"
    assert payload["request_status"] == "kapandi"


async def test_talep_notu_ve_durumu_disinda_alan_yazilamaz() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.update_request(88, reason=GEREKCE, actor="A",
                                           status="uydurma")
    assert payload["ok"] is False
    assert api.names() == []

    payload = await service.update_request(88, reason=GEREKCE, actor="A",
                                           status="cevaplandi", admin_note="Arandı.")
    assert payload["ok"] is True
    assert set(api.used("update_quote_request")[0]) >= {"status", "admin_note"}


# ================================================================ sözleşmeler

async def test_sozlesme_baglantisi_YEREL_IZE_YAZILMAZ() -> None:
    # Denetim satırında duran bir imza bağlantısı, tam olarak sunucunun
    # engellediği şey olurdu: telefon da yazılmaz, yalnız verilip verilmediği.
    store = FakeStore()
    service = make_service(store=store)
    payload = await service.create_contract(18, reason=GEREKCE, actor="A",
                                            phone="5321234567", send_sms=False)

    assert payload["ok"] is True
    assert payload["sign_url"] == "https://bld.example/s/abc"
    iz = store.detail(0)
    assert iz["phone_given"] is True
    assert "phone" not in iz
    assert "sign_url" not in iz
    assert "5321234567" not in store.audit[0]["detail"]


async def test_sms_gonderilince_baglanti_donmez() -> None:
    service = make_service()
    payload = await service.create_contract(18, reason=GEREKCE, actor="A", send_sms=True)
    assert payload["sms_sent"] is True
    assert payload["sign_url"] == ""     # bağlantı zaten müşterinin telefonunda


@pytest.mark.parametrize("gun", [0, 31, 45])
async def test_baglanti_omru_1_30_disinda_reddedilir(gun: int) -> None:
    api = FakeApi()
    service = make_service(api=api, config={"expires_in_days": 7})
    if gun == 0:
        # 0 "varsayılanı kullan" demektir ve tercihe düşer; reddedilmez.
        payload = await service.create_contract(18, reason=GEREKCE, actor="A",
                                                expires_in_days=0)
        assert payload["ok"] is True
        assert api.used("create_subscription_contract")[0]["expires_in_days"] == 7
        return
    payload = await service.create_contract(18, reason=GEREKCE, actor="A",
                                            expires_in_days=gun)
    assert payload["ok"] is False
    assert api.names() == []


async def test_km_tarafinda_OTP_metodu_YOKTUR() -> None:
    # K3: OTP ve sözleşme SMS'i SUNUCU tarafı akışlarıdır; KM yalnız tetikler.
    # Buraya bir OTP metodu eklemek, bu modülü `bld_sms`e bağlar ve iki yerde
    # ayrışabilen bir güvenlik akışı üretirdi.
    service = make_service()
    yuzey = {name for name in dir(service) if not name.startswith("_")}
    assert not any("otp" in name.lower() for name in yuzey)
    assert not any("verify" in name.lower() for name in yuzey)
    assert not any("sms" in name.lower() for name in yuzey)


# ================================================================== ödemeler

async def test_donem_borcunda_bos_tutar_SUNUCUYA_hesaplatilir() -> None:
    # Elle tutar yazmak serbest ama varsayılan hesaplanmış olmalı: yönetici her
    # ay çarpma yapmamalı. `amount_source` ikisini ayırır.
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.create_payment(18, period_start="2026-08-01",
                                           period_end="2026-08-31",
                                           due_date="2026-09-05", reason=GEREKCE,
                                           actor="A")
    assert payload["ok"] is True
    assert api.used("create_subscription_payment")[0]["amount_kurus"] is None
    assert payload["amount_source"] == "calculated"
    assert payload["order_count"] == 40


async def test_kuru_provada_donem_hesabi_GERCEKTEN_yapilir() -> None:
    service = make_service()
    payload = await service.create_payment(18, period_start="2026-08-01",
                                           period_end="2026-08-31",
                                           due_date="2026-09-05", reason=GEREKCE,
                                           actor="A", dry_run=True)
    assert payload["dry_run"] is True
    assert payload["would"]["order_count"] == 40
    assert payload["would"]["amount_kurus"] == 640000
    assert payload["payment"] == {}     # kayıt yazılmadı, satır da yok


async def test_tahsilat_ayri_bir_olay_yayinlar() -> None:
    # Durum olayıyla aynı ada konsaydı, tahsilatı gövdeye bakarak ayırmak
    # zorunda kalan bir dinleyici onu kaçırdığında sessizce yanlış çalışırdı.
    bus = FakeBus()
    service = make_service(bus=bus)
    payload = await service.mark_paid(41, method="online", reason=GEREKCE, actor="A",
                                      create_invoice=True, subscription_id=18)

    assert payload["ok"] is True
    assert payload["invoice_no"] == "BLD-2026-000045"
    olay, yuk = bus.events[0]
    assert olay == "bld_subscriptions.payment_marked_paid"
    assert yuk["subscriptionId"] == 18 and yuk["paymentId"] == 41
    assert yuk["amountKurus"] == 640000


async def test_odeme_yontemi_zorunlu_ve_yalniz_iki_deger() -> None:
    api = FakeApi()
    service = make_service(api=api)
    for method in ("", "havale", "kredi"):
        payload = await service.mark_paid(41, method=method, reason=GEREKCE, actor="A")
        assert payload["ok"] is False
    assert api.names() == []


async def test_gelecekteki_tahsilat_ani_reddedilir() -> None:
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.mark_paid(41, method="cash", reason=GEREKCE, actor="A",
                                      paid_at="2099-01-01T00:00:00Z")
    assert payload["ok"] is False
    assert api.names() == []


async def test_taninmayan_damga_bicimi_kapiyi_KAPATMAZ() -> None:
    # Ofsetli bir damgayı burada çözmek, saat dilimi aritmetiğini ekranda
    # ikinci kez yazmak olurdu; yanlış yorumlanan bir damga yüzünden reddedilen
    # geçerli bir tahsilat, sunucuya hiç ulaşmadığı için teşhis edilemezdi.
    api = FakeApi()
    service = make_service(api=api)
    payload = await service.mark_paid(41, method="cash", reason=GEREKCE, actor="A",
                                      paid_at="2099-01-01T00:00:00+03:00")
    assert payload["ok"] is True          # kapı kapanmadı, karar sunucunun
    assert api.used("mark_subscription_payment_paid")[0]["paid_at"] \
        == "2099-01-01T00:00:00+03:00"


async def test_catisma_hatasi_ne_yapilacagini_soyler() -> None:
    # Aynı kod farklı alanlarda farklı şey anlatır; genel bir cümle personelin
    # ne yapacağını söylememekle aynı şey olurdu.
    api = FakeApi()
    api.fail.add("create_subscription_payment")
    api.fail_code = "conflict"
    service = make_service(api=api)
    payload = await service.create_payment(18, period_start="2026-08-01",
                                           period_end="2026-08-31",
                                           due_date="2026-09-05", reason=GEREKCE,
                                           actor="A")
    assert payload["ok"] is False
    assert "aynı dönem" in payload["error"]


# ================================================================== yerel iz

async def test_yerel_iz_hedefe_gore_suzulur_ve_fiyat_sutunu_okunur() -> None:
    store = FakeStore()
    service = make_service(store=store)
    await service.create(customer_id=312, start_date="2026-09-01", service_days=[1],
                         default_quantity=20, agreed_unit_price_kurus=PRICE,
                         delivery_points=[{"address_id": 704}], reason=GEREKCE,
                         actor="Ayşe Yılmaz")
    await service.release_order(8455, reason=GEREKCE, actor="Ayşe Yılmaz")

    hepsi = await service.audit()
    assert len(hepsi["items"]) == 4

    yalniz_siparis = await service.audit(target_type="order", target_id=8455)
    assert {row["action"] for row in yalniz_siparis["items"]} == \
        {"subscription.order.release"}

    fiyatlar = [row["price_kurus"] for row in hepsi["items"]
                if row["action"] == "subscription.create"]
    assert fiyatlar == [PRICE, PRICE]


async def test_iz_okunamazsa_ekran_ayakta_kalir() -> None:
    class Kirik(FakeStore):
        async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
            raise RuntimeError("depo okunamıyor")

    service = make_service(store=Kirik())
    payload = await service.audit()
    assert payload["ok"] is True and payload["items"] == []


# ============================================================== ekran tercihi

async def test_tercih_yazilir_okunur_ve_taninmayan_anahtar_reddedilir() -> None:
    store = FakeStore()
    service = make_service(store=store)

    payload = await service.save_prefs({"page_size": 50, "calendar_days": 60},
                                       actor="A")
    assert payload["ok"] is True
    assert payload["prefs"]["page_size"] == 50
    assert payload["prefs"]["calendar_days"] == 60

    payload = await service.save_prefs({"poll_seconds": 5}, actor="A")
    assert payload["ok"] is False
    assert "poll_seconds" in payload["error"]
