"""Tahsilat servisi — iş kuralları. Ağa çıkmaz; `store.api` ve `notify` taklit edilir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from store_payment_gateway_backend import collect
from store_payment_gateway_backend.service import STANDALONE_METHOD, PaymentGatewayService
from store_payment_gateway_fakes import (
    FakeApi,
    FakeLog,
    FakeNotify,
    FakeSmsResult,
    FakeStore,
    FakeStoreApiError,
    FakeVault,
)

REASON = "Müşteri telefonda kartla ödemek istedi"

#: Alan adları CANLI YANITTAN alınmıştır: Bagisto admin API'si camelCase
#: döner (`taxCategoryId`, `specialPrice`, `price` ondalık METİN).
URUN = {"id": 7, "sku": "KTP-1", "name": "Deneme kitabı", "price": "100.0000",
        "specialPrice": None, "taxCategoryId": 3}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             notify: Any = None, secrets: Any = None,
             **config: Any) -> tuple[PaymentGatewayService, FakeApi, FakeStore]:
    api = api or FakeApi({7: dict(URUN)})
    api.tax_categories_payload = {"items": [{"id": 3, "code": "Kitap Vergi", "name": "KDV"}]}
    api.tax_rates_payload = {"items": [{"id": 9, "taxCategoryId": 3, "taxRate": 20}]}
    store = store or FakeStore()
    service = PaymentGatewayService(
        api=api, store=store, log=FakeLog(), notify=notify, secrets=secrets,
        config={"sms_dry_run": True, "org_name": "BBD Store", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


async def _create(service: PaymentGatewayService, **body: Any) -> dict[str, Any]:
    """Bağlantı üretilebilir bir talep açar.

    ADRES/İL/İLÇE BİLEREK DOLU: mağaza fatura adresini ZORUNLU tutuyor
    (banka kart sahibi bloğunu istiyor) ve eksik adresli bir talep için
    bağlantı üretilemez. Eksik hâlin ne yaptığı ayrı bir testte sınanır.
    """
    payload = {"fullName": "Ayşe Yılmaz", "phone": "05321234567",
               "city": "Konya", "district": "Selçuklu", "address": "Ferhuniye Mah. 1. Sk. No:3",
               "lines": [{"kind": "free", "amount": 125_000, "quantity": 1}], **body}
    return await service.create(payload=payload, actor="Kemal")


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_durum_ucu_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("health")
    result = await service.state()
    assert result["ok"] is True
    assert result["store"]["connected"] is False
    assert "patladı" in result["store"]["error"]


async def test_odeme_uclari_yayinda_degilse_acilista_soylenir() -> None:
    """Geçitte METODUN olması, mağazadaki UCUN ayakta olduğunu göstermez.
    13.08.2026'da tam bu olmuştu: `bbd_create_payment_link` geçitte duruyordu,
    uç 404'tü; yalnız metoda bakan ekran "hazır" diyor, personel formu
    doldurup onayladıktan SONRA hatayı görüyordu.

    Uç 16.08.2026'da 200 dönüyor — ama bu test o TARİHİ değil, uç ulaşılamaz
    olduğunda ekranın ne yaptığını sınıyor ve öyle KALIYOR (K7)."""
    service, api, _ = _service()
    api.fail.add("bbd_payment_links")
    result = await service.state()
    assert result["ok"] is True
    assert result["payments"]["available"] is False
    assert result["payments"]["notice"]


async def test_odeme_uclari_yayindaysa_uyari_cikmaz() -> None:
    service, _, _ = _service()
    result = await service.state()
    assert result["payments"]["available"] is True
    assert result["payments"]["notice"] == ""


async def test_magaza_kapaliyken_odeme_ucu_yoklamasi_istek_atmaz() -> None:
    service, api, _ = _service()
    api.fail.add("health")
    result = await service.state()
    assert result["payments"]["available"] is False
    assert api.used("bbd_payment_links") == []


async def test_vergi_okunamazsa_onizleme_yine_calisir() -> None:
    service, api, _ = _service()
    api.fail.add("tax_categories")
    result = await service.preview({"fullName": "Ayşe", "phone": "5321234567",
                                    "lines": [{"kind": "free", "amount": 10_000}]})
    assert result["ok"] is True
    assert result["amounts"]["gross"] == 10_000


# ================================================================ önizleme

async def test_onizleme_hicbir_sey_yazmaz() -> None:
    service, api, store = _service()
    await service.preview({"fullName": "Ayşe", "phone": "5321234567",
                           "lines": [{"kind": "free", "amount": 10_000}]})
    assert store.requests == []
    assert store.events == []
    assert not [name for name, _, _ in api.calls if name.startswith("bbd_")]


async def test_onizleme_urunun_kendi_vergisini_uygular() -> None:
    service, _, _ = _service()
    result = await service.preview({
        "fullName": "Ayşe", "phone": "5321234567",
        "lines": [{"kind": "product", "productId": 7, "amount": 100_000, "quantity": 1}],
    })
    assert result["amounts"]["tax"] == 20_000
    assert result["amounts"]["gross"] == 120_000


async def test_onizleme_serbest_tutar_ve_urunu_birlikte_kirar() -> None:
    service, _, _ = _service()
    result = await service.preview({
        "fullName": "Ayşe", "phone": "5321234567",
        "lines": [{"kind": "free", "amount": 50_000},
                  {"kind": "product", "productId": 7, "amount": 100_000}],
    })
    assert result["amounts"]["net"] == 150_000
    assert result["amounts"]["tax"] == 20_000        # serbest tutardan vergi çıkmaz
    assert result["ready"] is True


async def test_vergi_orani_okunamayan_urun_tahsilati_durdurur() -> None:
    """Oran çözülemiyorsa ekran "KDV %0" yazıp geçmez: sessiz sıfır, faturayı
    KDV kadar eksik keser. Ön izleme hazır DEMEZ ve talep açılmaz."""
    service, api, store = _service()
    api.tax_categories_payload = {"items": [{"id": 3, "name": "KDV", "taxRates": None}]}
    api.tax_rates_payload = {"items": [{"id": 9, "taxRate": 20}]}   # kategori bağı YOK
    body = {"fullName": "Ayşe", "phone": "5321234567",
            "lines": [{"kind": "product", "productId": 7, "amount": 100_000}]}
    preview = await service.preview(body)
    assert preview["ready"] is False
    assert any("KDV oranı" in problem for problem in preview["problems"])

    created = await service.create(payload=body, actor="Kemal")
    assert created["ok"] is False
    assert store.requests == []


async def test_urun_aramasi_urun_basina_ek_istek_atmaz() -> None:
    """Geçidin hız kovası dakikada 55 istek. Vergi oranı için ürün başına
    detay çağırmak, 20 sonuçlu tek bir aramada kovayı tüketiyordu; oysa
    `taxCategoryId` zaten listede geliyor."""
    service, api, _ = _service()
    api.products_payload = {
        "items": [{"id": 100 + i, "sku": f"S{i}", "name": f"Kitap {i}",
                   "price": "100.0000", "taxCategoryId": 3} for i in range(20)],
        "meta": {"total": 20, "currentPage": 1, "perPage": 25},
    }
    result = await service.search_products(q="kitap")
    assert len(result["items"]) == 20
    assert result["items"][0]["taxRate"] == 20.0
    assert api.used("product") == []          # tek bir ürün detayı bile çekilmedi


async def test_urun_aramasi_okunamayan_orani_sifir_diye_gostermez() -> None:
    service, api, _ = _service()
    api.tax_rates_payload = {"items": [{"id": 9, "taxRate": 20}]}   # kategori bağı YOK
    api.products_payload = {"items": [{"id": 100, "sku": "S", "name": "Kitap",
                                       "price": "100.0000", "taxCategoryId": 3}],
                            "meta": {"total": 1}}
    result = await service.search_products(q="kitap")
    assert result["items"][0]["taxRate"] is None
    assert result["items"][0]["taxNote"]


async def test_vergi_tablolari_her_onizlemede_yeniden_cekilmez() -> None:
    service, api, _ = _service()
    body = {"fullName": "Ayşe", "phone": "5321234567",
            "lines": [{"kind": "free", "amount": 10_000}]}
    await service.preview(body)
    await service.preview(body)
    await service.preview(body)
    assert len(api.used("tax_rates")) == 1


async def test_vergi_okunamayinca_basarisiz_sonuc_onbellege_alinmaz() -> None:
    """Mağaza beş dakika boyunca "oran yok" demeye devam etmesin."""
    service, api, _ = _service()
    api.fail.add("tax_categories")
    await service.preview({"fullName": "Ayşe", "phone": "5321234567", "lines": []})
    api.fail.discard("tax_categories")
    result = await service.preview({
        "fullName": "Ayşe", "phone": "5321234567",
        "lines": [{"kind": "product", "productId": 7, "amount": 100_000}]})
    assert result["amounts"]["tax"] == 20_000


async def test_onizleme_eksikleri_sayar_ve_hazir_demez() -> None:
    service, _, _ = _service()
    result = await service.preview({"lines": []})
    assert result["ready"] is False
    assert any("tutar" in problem for problem in result["problems"])
    assert any("Ad soyad" in problem for problem in result["problems"])


async def test_onizlemede_eposta_varsayilani_doldurulur() -> None:
    service, _, _ = _service()
    result = await service.preview({"fullName": "Ayşe", "phone": "5321234567"})
    assert result["email"] == collect.DEFAULT_EMAIL


# ================================================================== kayıt

async def test_talep_yerel_kaydedilir_magazaya_yazilmaz() -> None:
    service, api, store = _service()
    result = await _create(service)
    assert result["ok"] is True
    assert result["code"].startswith("TAH-")
    assert store.requests[0]["gross"] == 125_000
    assert store.requests[0]["status"] == collect.DRAFT
    assert not [name for name, _, _ in api.calls if name.startswith("bbd_")]


async def test_telefonu_bozuk_talep_acilmaz() -> None:
    service, _, store = _service()
    result = await _create(service, phone="123")
    assert result["ok"] is False
    assert "5XXXXXXXXX" in result["error"]
    assert store.requests == []


async def test_tutarsiz_talep_acilmaz() -> None:
    service, _, _ = _service()
    result = await _create(service, lines=[])
    assert result["ok"] is False
    assert "tutar" in result["error"].lower()


# ============================================== bağlantı üretimi ve eksik uç
#
# KALDIRILAN İKİ TEST VE NEDENİ (geçit eşlemesi bağlandığında):
#
#  · `test_siparise_bagli_tahsilat_zinciri_uctan_uca_isler` — sildim çünkü
#    ölçtüğü şey ARTIK YANLIŞ: çağrının `order_id=42` ve `amount=125000` (kuruş)
#    taşımasını doğruluyordu. Gerçek geçit `order_id` verilen çağrıyı reddediyor
#    ve `amount`ı ondalık metin istiyor; yani test yeşilken canlı akış kırıktı.
#    Yerine `test_siparis_kimligi_govdeye_hic_konmaz` ve
#    `test_serbest_tutar_baglantisi_magazanin_sozlesmesiyle_gider` geçti.
#
#  · `test_serbest_tahsilat_uc_gelince_kendiliginden_acilir` — sildim çünkü
#    varsaydığı dünya yok: taklit geçide `add_standalone()` ile hiç var olmamış
#    bir metot takıp onun gövdesini (`payload.customer.phone`) ölçüyordu. O gövde
#    mağazanın sözleşmesi değildi. Yerine gerçek sözleşmeyi ölçen fatura/tutar/
#    tür testleri geçti.

async def test_gecit_metodu_yoksa_ozellik_aciklamayla_kapalidir() -> None:
    """Metot bir gün yeniden adlandırılırsa ekran ne yapar.

    Adı bir dönem `test_serbest_tahsilat_ucu_yoksa_...`tı ve o gün gerçekten
    eksik olan metot buydu; bugün metot BAĞLI (`_standalone` varsayılan True),
    o yüzden test eksikliği kendisi kuruyor. Sessizce patlamaz: talep
    kaydedilir, ekran nedeni yazar, kayıt taslak kalır.
    """
    service, api, store = _service()
    api.drop_link_method()
    created = await _create(service)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert STANDALONE_METHOD in result["error"]
    assert result["standalone"] is False
    assert store.requests[0]["status"] == collect.DRAFT      # kayıt duruyor
    assert any(event["action"] == "link" and event["result"] == "hata"
               for event in store.events)


async def test_serbest_tutar_baglantisi_magazanin_sozlesmesiyle_gider() -> None:
    """Serbest tutar: `kind=custom`, tutar ONDALIK METİN, `items` YOK.

    Bir dönem bu çağrı kuruş tam sayısı gönderiyordu; mağaza `amount`ı TL
    sayıp sepetin toplamıyla karşılaştırdığı için sonuç garantili 422
    AMOUNT_DRIFT'ti (1.250,00 TL'lik tahsilat için 125.000,00 TL istenirdi).
    """
    service, api, store = _service()
    created = await _create(service)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                                 send_sms=False)
    assert result["ok"] is True
    call = api.used("bbd_create_payment_link")[0]
    assert call["kind"] == "custom"
    assert call["amount"] == "1250.00"          # 125.000 kuruş → ondalık TL METNİ
    assert call["items"] is None                # serbest tutara ürün eklenmez
    assert call["reason"] == REASON
    assert store.requests[0]["status"] == collect.LINKED
    assert store.requests[0]["token"] == "TKN-1"


async def test_urunlu_talep_tutar_gondermez_urun_listesi_gonderir() -> None:
    """Ürün seçilmişse geçerli fiyat MAĞAZANINKİDİR: `amount` gönderilmez."""
    service, api, _ = _service()
    created = await _create(service, lines=[
        {"kind": "product", "productId": 7, "amount": 100_000, "quantity": 2}])
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                                 send_sms=False)
    assert result["ok"] is True
    call = api.used("bbd_create_payment_link")[0]
    assert call["kind"] == "product"
    assert call["items"] == [{"productId": 7, "quantity": 2}]
    assert call["amount"] == ""                 # tutar YOK (mağaza reddediyor)


async def test_siparis_kimligi_govdeye_hic_konmaz() -> None:
    """Siparişe bağlı talepte bile `order_id` gönderilmez.

    Mağaza ucu gövdedeki `orderId` alanını HİÇ OKUMAZ ve geçit bu alanı
    taşıyan çağrıyı doğrudan reddeder. Sipariş kimliği YEREL satırda durmaya
    devam eder; bağ mağazada, ödeme tamamlanınca kurulur.
    """
    service, api, store = _service()
    created = await _create(service, orderId=42)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                                 send_sms=False)
    # Çağrının BAŞARILI olması tek başına kanıttır: taklit geçit de gerçeği gibi
    # `order_id` taşıyan çağrıyı reddediyor (aşağıda ayrıca gösteriliyor).
    assert result["ok"] is True
    call = api.used("bbd_create_payment_link")[0]
    assert "order_id" not in call
    assert "orderId" not in call["billing"]
    assert store.requests[0]["order_id"] == 42  # yerel kayıtta duruyor

    with pytest.raises(FakeStoreApiError):
        await api.bbd_create_payment_link(order_id=42, amount="1250.00", reason=REASON,
                                          billing={"firstName": "Ayşe", "lastName": "Yılmaz"})


async def test_fatura_adresi_il_state_ilce_city_alanina_yazilir() -> None:
    """ÇAPRAZ EŞLEME KODDAN DOĞRULANDI (`PaymentLinkService::validateBilling`):
    `state` 81 ilin listesine karşı doğrulanır, `city` bankanın `BillAddrCity`
    alanına geçer. Yani mağazanın "city"si bizim İLÇEmizdir."""
    service, api, _ = _service()
    created = await _create(service, fullName="Ayşe Nur Yılmaz")
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    billing = api.used("bbd_create_payment_link")[0]["billing"]
    assert billing["state"] == "Konya"          # İL
    assert billing["city"] == "Selçuklu"        # İLÇE
    assert billing["firstName"] == "Ayşe Nur"   # son boşluktan bölünür
    assert billing["lastName"] == "Yılmaz"
    assert billing["phone"] == "5321234567"     # ülke kodunu mağaza ekler
    assert billing["address"] == ["Ferhuniye Mah. 1. Sk. No:3"]
    assert billing["country"] == "TR"


async def test_soyadsiz_talep_icin_baglanti_uretilmez() -> None:
    """Soyad UYDURULMAZ. Adı soyad diye tekrarlamak bankaya var olmayan bir
    soyad yazmaktır; mağaza da boş soyadı reddediyor. Ekran alanı söyler."""
    service, api, store = _service()
    created = await _create(service, fullName="Ayşe")
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "soyad" in result["error"]
    assert api.used("bbd_create_payment_link") == []      # mağazaya istek GİTMEDİ
    assert store.requests[0]["status"] == collect.DRAFT


async def test_adres_ve_il_eksikse_magazaya_istek_gitmez() -> None:
    service, api, _ = _service()
    created = await _create(service, city="", district="", address="")
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    for field in ("adres", "il", "ilçe"):
        assert field in result["error"]
    assert api.used("bbd_create_payment_link") == []


async def test_magazanin_fatura_reddi_ekranda_okunabilir_turkce_metne_cevrilir() -> None:
    """K7: geçit/mağaza reddedince ekran DÜŞMEZ, ret metnini yazar.

    SENARYO GERÇEK: yerel kapı yalnız BOŞLUK denetler, il listesini burada
    TUTMAYIZ (mağaza kendi `TurkishProvinces` listesiyle doğruluyor; kopya
    tutmak iki listenin ayrışması demekti). Yani yanlış yazılmış bir il yerel
    kapıdan geçer ve reddi mağaza verir — o ret personele olduğu gibi görünür.
    """
    service, api, store = _service()
    api.link_error = ('Fatura adresindeki il tanınmadı. 81 ilden biri yazılmalıdır '
                      '(ör. "Konya").')
    created = await _create(service, city="Konyaa")
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert result["error"] == api.link_error          # metin sadeleşmeden ekrana çıkar
    assert "Traceback" not in result["error"]
    assert store.requests[0]["status"] == collect.DRAFT
    assert any(event["result"] == "hata" for event in store.events)


async def test_karma_talep_magazaya_gonderilmez() -> None:
    """Serbest tutar + ürün aynı linke konamaz (mağaza karma sepeti yasaklıyor).
    Birini sessizce düşürmek eksik tahsilat ya da kayıp ürün olurdu."""
    service, api, _ = _service()
    created = await _create(service, lines=[
        {"kind": "free", "amount": 50_000},
        {"kind": "product", "productId": 7, "amount": 100_000}])
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "karma sepet" in result["error"]
    assert api.used("bbd_create_payment_link") == []


async def test_kalem_listesi_bozuksa_serbest_tutara_dusulmez() -> None:
    """Okunamayan kalem listesi BOŞ SAYILMAZ.

    Boş saymak, ürünlü bir talebi sessizce serbest tutara çevirirdi: müşteriye
    ürünsüz, mağazaya kalemsiz bir tahsilat gitmesi demek. Ekran bozukluğu
    söyler, mağazaya istek gitmez ve iş burada durur (K7 — patlamaz).
    """
    service, api, store = _service()
    created = await _create(service)
    store.requests[0]["items"] = "{bozuk"
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "kalem listesi okunamadı" in result["error"]
    assert api.used("bbd_create_payment_link") == []


async def test_gecit_metodu_bagliyken_ekran_ozelligi_acik_gelir() -> None:
    service, _, _ = _service()
    state = await service.state()
    assert state["standalone"] is True
    assert state["standaloneNotice"] == ""


async def test_gerekce_on_karakterden_kisaysa_baglanti_uretilmez() -> None:
    service, api, _ = _service()
    created = await _create(service)
    result = await service.start(created["id"], reason="kısa", actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert api.used(STANDALONE_METHOD) == []


async def test_kuru_provada_baglanti_uretilmez_ve_durum_degismez() -> None:
    """Kuru provada MÜŞTERİYE GİDECEK HİÇBİR ŞEY üretilmez.

    "Mağazaya istek gitmez" demek bu uçta yanlış olurdu ve testin yalan
    söylemesi gerekirdi: mağaza kuru provada sepeti gerçekten kurup toplamı
    hesaplar, sonra işlemi geri sarar (`PaymentLinkService::create` →
    `DB::rollBack`). Ölçülen şey doğru olan: bayrak aşağı taşınır, ortada link
    yoktur, yerel durum taslak kalır ve SMS aşamasına geçilmez.
    """
    service, api, store = _service()
    created = await _create(service)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=True)
    assert result["dryRun"] is True
    assert api.used("bbd_create_payment_link")[0]["dry_run"] is True
    assert store.requests[0]["status"] == collect.DRAFT
    assert store.requests[0]["token"] == ""
    assert store.requests[0]["link"] == ""
    assert "sms" not in result
    assert "Kuru prova" in result["notice"]


async def test_odenmis_talebe_ikinci_baglanti_uretilmez() -> None:
    # ÇİFT ÇEKİM KAPISI.
    service, api, store = _service()
    created = await _create(service)
    store.requests[0]["status"] = collect.PAID
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert api.used(STANDALONE_METHOD) == []


async def test_durumu_okunamayan_talebe_yeni_baglanti_uretilmez() -> None:
    service, api, store = _service()
    created = await _create(service)
    store.requests[0]["status"] = collect.UNKNOWN
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert collect.DOUBLE_CHARGE_WARNING in result["error"]
    assert api.used(STANDALONE_METHOD) == []


# ============================================================ SMS üç fren

async def test_modul_kuru_provasi_acikken_gercek_sms_gitmez() -> None:
    notify = FakeNotify(dry_run=False)
    service, _api, _store = _service(notify=notify, sms_dry_run=True)
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is False
    assert notify.provider.sent == []
    assert "sms_dry_run" in result["notice"]


async def test_platform_kuru_provasi_acikken_gonderim_isaretlenmez() -> None:
    notify = FakeNotify(dry_run=True,
                        provider=None)
    notify.provider.result = FakeSmsResult(accepted=True, dry_run=True)
    service, _api, _ = _service(notify=notify, sms_dry_run=False)
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is False
    assert result["dryRun"] is True


async def test_istegin_kendi_kuru_provasi_da_freni_tutar() -> None:
    notify = FakeNotify(dry_run=False)
    service, _api, _ = _service(notify=notify, sms_dry_run=False)
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=True)
    assert result["sent"] is False
    assert notify.provider.sent == []


async def test_beyaz_liste_disindaki_numaraya_gercek_sms_gitmez() -> None:
    notify = FakeNotify(dry_run=False)
    service, _api, _ = _service(notify=notify, sms_dry_run=False,
                               sms_allowlist=["5559998877"])
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is False
    assert "beyaz liste" in result["notice"]
    assert notify.provider.sent == []


async def test_uc_fren_de_kapaliyken_sms_gercekten_gider() -> None:
    notify = FakeNotify(dry_run=False)
    service, _api, store = _service(notify=notify, sms_dry_run=False)
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is True
    assert notify.provider.sent[0][0] == "5321234567"
    assert "https://ode.me/TKN-1" in notify.provider.sent[0][1]
    assert store.requests[0]["sms_state"] == "sent"
    assert store.requests[0]["status"] == collect.SENT


async def test_baglantisiz_talebe_sms_gonderilmez() -> None:
    notify = FakeNotify(dry_run=False)
    service, _, _ = _service(notify=notify, sms_dry_run=False)
    created = await _create(service)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "bağlantı" in result["error"]


async def test_sms_yetenegi_yoksa_ekran_calisir_gonderim_reddedilir() -> None:
    service, _api, _ = _service(notify=None, sms_dry_run=False)
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert result["sent"] is False
    assert "Bildirim yeteneği yok" in result["notice"]


# ================================================================ yoklama

async def _linked(notify: Any = None) -> tuple[PaymentGatewayService, FakeApi, FakeStore, int]:
    service, api, store = _service(notify=notify)
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    return service, api, store, created["id"]


async def test_yoklama_odendi_durumunu_yazar() -> None:
    service, api, store, request_id = await _linked()
    # Satırlar MAĞAZA BİÇİMİNDE: kimlik `id`, kod `code`. Mağaza "token" diye
    # bir alan döndürmüyor (`PaymentLinkService::present`).
    api.links_payload = {"items": [{"id": 41, "code": "TKN-1", "status": "paid",
                                    "orderId": 91}]}
    api.orders_by_id[91] = {"id": 91, "incrementId": "91",
                            "invoices": [{"id": 55, "incrementId": "55"}]}
    result = await service.poll(request_id)
    assert result["request"]["status"]["code"] == collect.PAID
    assert store.requests[0]["order_id"] == 91
    assert store.requests[0]["invoice_id"] == 55
    # Yoklama TEKİL ucu çağırdı: liste sayfalamasına hiç bağlı kalmadı.
    assert [args[0] for name, args, _ in api.calls if name == "bbd_payment_link"] == [41]
    assert api.used("bbd_payment_links") == []


async def test_fatura_siparisin_kendisinden_okunur_liste_suzgecine_guvenilmez() -> None:
    """CANLI TUZAK: `/admin/invoices?order_id=1` sipariş kimliğine değil,
    sipariş NUMARASININ PARÇASINA bakıyor ve mağazadaki 11 faturanın hepsini
    döndürüyor; fatura kaydı `orderId` alanını da boş bırakıyor. Fatura
    listesini süzüp ilk satırı almak, başka müşterinin fatura numarasını bu
    talebe yazmaktı."""
    service, api, store, request_id = await _linked()
    api.links_payload = {"items": [{"id": 41, "code": "TKN-1", "status": "paid",
                                    "orderId": 91}]}
    api.orders_by_id[91] = {"id": 91, "invoices": [{"id": 7}, {"id": 9}]}
    # Fatura listesi yanlış cevap verse bile ona hiç sorulmaz.
    api.invoices_payload = {"items": [{"id": 999}]}
    await service.poll(request_id)
    assert store.requests[0]["invoice_id"] == 7
    assert api.used("invoices") == []


async def test_yoklama_bilinmeyen_durumu_basarisiz_yazmaz() -> None:
    service, api, store, request_id = await _linked()
    api.links_payload = {"items": [{"id": 41, "code": "TKN-1",
                                    "status": "bank_says_something_new"}]}
    result = await service.poll(request_id)
    assert result["request"]["status"]["code"] == collect.UNKNOWN
    assert "başarısız" not in result["request"]["status"]["label"].lower()
    # Ham durum saklanır: eşlememiz yanlışsa veri elimizde kalsın.
    assert store.requests[0]["store_status"] == "bank_says_something_new"


async def test_magazada_baglanti_bulunamazsa_bilinmiyor_denir() -> None:
    service, api, _, request_id = await _linked()
    api.links_payload = {"items": []}
    api.link_payload = {}                       # tekil uç da bulamasın (404)
    result = await service.poll(request_id)
    assert result["request"]["status"]["code"] == collect.UNKNOWN
    assert collect.DOUBLE_CHARGE_WARNING in result["notice"]


async def test_yoklama_magaza_dusunce_durumu_bozmaz() -> None:
    service, api, store, request_id = await _linked()
    api.fail.add("bbd_payment_link")
    result = await service.poll(request_id)
    assert result["ok"] is True
    assert result["connected"] is False
    assert store.requests[0]["status"] == collect.LINKED     # eski durum korunur


async def test_yoklama_YABANCI_LINKI_bu_talebe_yazmaz() -> None:
    """GERİLEME KAPISI — ölçülmüş zarar: ödenmemiş bir talep "Ödendi" oluyordu.

    Eski `poll()` mağazanın OKUMADIĞI süzgeçleri (`token`, `order_id`)
    gönderiyor, uç bunları sessizce yok sayıp "en yeni 50 link"i döndürüyor,
    kod da eşleşme bulamayınca `match = rows[0]` ile İLK YABANCI SATIRI
    alıyordu. O satırın `status`/`orderId`/`invoiceId` alanları yerel talebe
    yazılıyordu; sonuç: hiç ödenmemiş 1.250,00 TL'lik talep "Ödendi" görünüyor,
    başkasının siparişine bağlanıyor ve `paid` kilidi yüzünden yeni bağlantı
    da üretilemiyordu.

    Burada mağazada BİZİM linkimiz YOK, yabancı ödenmiş linkler VAR.
    """
    service, api, store, request_id = await _linked()
    api.link_payload = {}                        # tekil uç bizimkini bulamasın
    api.links_payload = {"items": [
        {"id": 900 + n, "code": f"BASKASI{n:04d}", "status": "paid",
         "orderId": 5000 + n, "invoiceId": 7000 + n}
        for n in range(50)
    ]}
    result = await service.poll(request_id)

    assert result["request"]["status"]["code"] == collect.UNKNOWN
    assert collect.DOUBLE_CHARGE_WARNING in result["notice"]
    # Yabancı satırın HİÇBİR alanı yerel talebe geçmedi.
    assert store.requests[0]["order_id"] == 0
    assert store.requests[0]["invoice_id"] == 0
    assert store.requests[0]["store_status"] == ""


async def test_yoklama_arama_yaparken_magazanin_okudugu_suzgeci_gonderir() -> None:
    """Uç yalnız `status` ve `q` okur (`PaymentLinkController::index`).
    `token`/`order_id` göndermek hata değil SESSİZLİK üretir: uç süzmez ve
    "en yeni 50 link"i döndürür."""
    service, api, store, request_id = await _linked()
    store.requests[0]["link_id"] = 0             # eski satır: sayısal kimlik yok
    api.links_payload = {"items": [{"id": 41, "code": "TKN-1", "status": "paid"}]}
    await service.poll(request_id)

    gonderilen = [args[0] for name, args, _ in api.calls if name == "bbd_payment_links"]
    assert gonderilen == [{"q": "TKN-1"}]
    assert all("token" not in (filters or {}) for filters in gonderilen)
    assert all("order_id" not in (filters or {}) for filters in gonderilen)
    # Arama yoluyla bulundu → sayısal kimlik yerel satıra yazıldı: bir sonraki
    # yoklama listeye hiç uğramayacak.
    assert store.requests[0]["link_id"] == 41


async def test_yalnizca_siparis_numarasi_varken_magazaya_hic_gidilmez() -> None:
    """Sipariş kimliği link listesinde ARANABİLİR BİR ŞEY DEĞİL. Elde yalnız
    sipariş no varken mağazaya gitmek, "bulamadım" yerine "başkasının linkini
    buldum" ile dönme riskidir — taslak talep bu yoldan "Ödendi" oluyordu."""
    service, api, store = _service()
    created = await _create(service, orderId=31)
    api.links_payload = {"items": [{"id": 900, "code": "BASKASI", "status": "paid",
                                    "orderId": 4477}]}
    result = await service.poll(created["id"])

    assert result["changed"] is False
    assert api.used("bbd_payment_links") == []
    assert api.used("bbd_payment_link") == []
    assert store.requests[0]["order_id"] == 31       # ezilmedi
    assert store.requests[0]["status"] == collect.DRAFT


# ================================================== çekmece: POS denemeleri

DENEMELER = {"items": [
    {"id": 17, "order_id": 20, "state": "captured", "gateway": "iyzico",
     "masked_number": "52820801****7358", "amount_minor": 399_000},
    {"id": 16, "order_id": 19, "state": "failed", "gateway": "iyzico",
     "masked_number": "52820801****7358", "amount_minor": 129_500},
    {"id": 15, "order_id": 42, "state": "captured", "gateway": "iyzico",
     "masked_number": "41598472****1122", "amount_minor": 125_000},
]}


async def test_pos_denemeleri_BASKA_musterilerin_kartlarini_gostermez() -> None:
    """GERİLEME KAPISI — yanlış veri VE kart verisi sızıntısı.

    `PaymentAttemptController::applyFilters` yalnız `state · orderId · from ·
    to` okur. Eski kod `order_id` (snake_case) gönderiyordu; Laravel tanımadığı
    parametreyi yok sayıyor ve uç SÜZÜLMEMİŞ listeyi döndürüyordu. Canlıda
    ölçüldü (16.08.2026, salt GET): `?order_id=999999` → 17 satır,
    `?orderId=999999` → 0 satır. Ekran o 17 satırı "bu talebin POS denemeleri"
    diye çiziyordu — başka müşterilerin maskeli kart numaralarıyla birlikte.
    """
    service, api, store, request_id = await _linked()
    api.attempts_payload = DENEMELER
    store.requests[0]["order_id"] = 42
    result = await service.card(request_id)

    assert [row["id"] for row in result["attempts"]] == [15]
    kartlar = {row["card"] for row in result["attempts"]}
    assert "52820801****7358" not in kartlar
    # Süzgeç adı CAMEL CASE gitti; snake_case gönderilseydi taklit de (mağaza
    # gibi) süzmez ve üç satırın hepsi dönerdi.
    gonderilen = [args[0] for name, args, _ in api.calls if name == "bbd_payment_attempts"]
    assert gonderilen == [{"orderId": 42}]


async def test_siparissiz_talepte_deneme_listesi_uydurulmaz() -> None:
    """Belirteçle deneme aranamaz: uçta `token` süzgeci YOK. Süzgeçsiz çağırıp
    "bunlar bu talebin denemeleri" demek uydurmadır; boş liste ve NEDENİNİ
    söyleyen bir not doğrusudur."""
    service, api, _store, request_id = await _linked()
    api.attempts_payload = DENEMELER
    result = await service.card(request_id)          # sipariş no henüz yok

    assert result["attempts"] == []
    assert api.used("bbd_payment_attempts") == []    # mağazaya HİÇ sorulmadı
    # Not `attemptsNote`ta, `warning`da DEĞİL: bu beklenen hâl, arıza değil.
    # `warning` uyarı kutusu çıkarır ve ödemesi yapılmamış her talepte
    # çıksaydı gerçek arızayı görünmez yapardı.
    assert result["warning"] == ""
    assert "sipariş" in result["attemptsNote"].lower()


# ======================================================== elden kapatma

async def _with_invoice(order_id: int = 42, invoice_id: int = 55) -> tuple[
        PaymentGatewayService, FakeApi, FakeStore, int]:
    service, api, store = _service()
    api.orders_by_id[order_id] = {"id": order_id, "invoices": [{"id": invoice_id}]}
    created = await _create(service, orderId=order_id)
    return service, api, store, created["id"]


async def test_elden_kapatma_magazaya_odeme_kaydi_yazar() -> None:
    """Gövde MAĞAZANIN şemasıdır: `POST /admin/transactions` zorunlu alanları
    `invoiceId` · `paymentMethod` · `amount`. Eski gövde (`order_id`/`method`/
    `reference`) canlıda 422 alırdı: elden kapatma mağazaya hiç işlenmezdi."""
    service, api, store, request_id = await _with_invoice()
    result = await service.settle(request_id, method="havale", reference="DEK-9",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    payload = api.used("record_transaction")[0]["payload"]
    assert set(payload) == {"invoiceId", "paymentMethod", "amount"}
    assert payload["invoiceId"] == 55
    assert payload["paymentMethod"] == "moneytransfer"   # mağazanın kod adı
    assert payload["amount"] == 1250.0                   # telde sayı, içeride kuruş
    assert store.requests[0]["status"] == collect.SETTLED
    assert store.requests[0]["settle_ref"] == "DEK-9"
    assert store.requests[0]["invoice_id"] == 55


async def test_nakit_beyani_magazanin_kendi_kod_adiyla_gider() -> None:
    service, api, _, request_id = await _with_invoice()
    await service.settle(request_id, method="nakit", reference="", amount=0,
                         reason=REASON, actor="Kemal", dry_run=False)
    assert api.used("record_transaction")[0]["payload"]["paymentMethod"] == "cashondelivery"


async def test_odeme_yontemi_kodu_ayardan_ezilebilir() -> None:
    service, api, _ = _service(settle_payment_methods={"havale": "banka_havalesi"})
    api.orders_by_id[42] = {"id": 42, "invoices": [{"id": 55}]}
    created = await _create(service, orderId=42)
    await service.settle(created["id"], method="havale", reference="", amount=0,
                         reason=REASON, actor="Kemal", dry_run=False)
    assert api.used("record_transaction")[0]["payload"]["paymentMethod"] == "banka_havalesi"


async def test_faturasi_olmayan_siparis_magazaya_islenmez_ama_beyan_durur() -> None:
    """Mağaza ödeme kaydını FATURAYA bağlıyor; faturası kesilmemiş siparişe
    kayıt yazılamaz. Beyan kaybolmaz, ekran nedenini söyler."""
    service, api, store = _service()
    api.orders_by_id[42] = {"id": 42, "invoices": []}
    created = await _create(service, orderId=42)
    result = await service.settle(created["id"], method="havale", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert api.used("record_transaction") == []
    assert "fatura" in result["notice"].lower()
    assert store.requests[0]["status"] == collect.SETTLED


async def test_dekont_numarasinin_magazada_karsiligi_olmadigi_soylenir() -> None:
    service, _, _, request_id = await _with_invoice()
    result = await service.settle(request_id, method="havale", reference="DEK-9",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert "saklanmaz" in result["notice"]


async def test_magaza_yazamazsa_beyan_yine_de_kaydedilir() -> None:
    service, api, store, request_id = await _with_invoice()
    api.fail.add("record_transaction")
    result = await service.settle(request_id, method="nakit", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert "mağazaya işlenemedi" in result["notice"]
    assert store.requests[0]["status"] == collect.SETTLED


async def test_parasi_cekilmis_olabilecek_talep_elden_kapatilmaz() -> None:
    service, _, store = _service()
    created = await _create(service, orderId=42)
    store.requests[0]["status"] = collect.VOID_REQUIRED
    result = await service.settle(created["id"], method="havale", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert collect.DOUBLE_CHARGE_WARNING in result["error"]


async def test_gecersiz_yontem_reddedilir() -> None:
    service, _, _ = _service()
    created = await _create(service)
    result = await service.settle(created["id"], method="kripto", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False


# ================================================================ iptal

async def test_iptal_kaydi_silmez_baglantiyi_oldurur() -> None:
    service, api, store, request_id = await _linked()
    result = await service.cancel(request_id, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert api.used("bbd_cancel_payment_link")[0]["reason"] == REASON
    assert len(store.requests) == 1
    assert store.requests[0]["status"] == collect.CANCELLED


async def test_odenmis_tahsilat_iptal_edilmez() -> None:
    service, _, store, request_id = await _linked()
    store.requests[0]["status"] = collect.PAID
    result = await service.cancel(request_id, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "İadeler" in result["error"]


async def test_iptal_SAYISAL_kimlikle_cagirilir_kodla_degil() -> None:
    """GERİLEME KAPISI — "İptal" düğmesi canlıda HİÇ çalışmıyordu.

    Rota `->whereNumber('id')` ile daraltılmış, geçit de bu yüzden
    `key.isdigit()` istiyor. Eski kod çağrıya `token`ı (mağazanın `code`u)
    veriyordu; `LinkCode` alfabesi `0123456789ABCDEFGHJKMNPQRSTVWXYZ` olduğu
    için 12 hanenin tamamının rakam çıkma olasılığı ≈1,2e-6 — yani ret
    pratikte HER SEFERİNDE geliyordu. Ekran düşmüyordu (K7), ama yanlış giden
    bir bağlantıyı kapatmanın yolu yoktu.

    Taklit artık gerçeğin denetimini uyguluyor: kod gönderilse test kırılır.
    """
    service, api, store, request_id = await _linked()
    assert store.requests[0]["link_id"] == 41        # üretimde satıra yazıldı
    result = await service.cancel(request_id, reason=REASON, actor="Kemal", dry_run=False)

    assert result["ok"] is True
    gonderilen = [args[0] for name, args, _ in api.calls if name == "bbd_cancel_payment_link"]
    assert gonderilen == [41]
    assert "TKN-1" not in [str(item) for item in gonderilen]


async def test_kimligi_bilinmeyen_eski_kayit_iptal_edildi_denmez() -> None:
    """Sütun eklenmeden önce üretilmiş satırda sayısal kimlik yok. Yerel satırı
    "iptal edildi" yapmak, mağazada ödenebilir duran bir bağlantıyı kapatıldı
    sanmaktır — söylenmez."""
    service, api, store, request_id = await _linked()
    store.requests[0]["link_id"] = 0
    result = await service.cancel(request_id, reason=REASON, actor="Kemal", dry_run=False)

    assert result["ok"] is False
    assert "yokla" in result["error"].lower()
    assert api.used("bbd_cancel_payment_link") == []
    assert store.requests[0]["status"] == collect.LINKED      # ezilmedi


async def test_yeni_baglanti_uretmeden_ONCE_eskisi_magazada_kapatilir() -> None:
    """GERİLEME KAPISI — iki ödenebilir link, biri izlenemez.

    `PaymentLinkService::persistLink` yalnız INSERT yapar; eski link
    `expires_at` dolana kadar (varsayılan 48 saat) ödenebilir kalır. Eski
    davranışta "Yeni bağlantı üret" yerel `token`/`link` alanlarını üzerine
    yazıyor, mağazadaki eski kaydı ELLEMİYORDU. Müşteri elindeki ilk SMS'i
    öderse o belirteç artık yerel satırda yok — yoklama onu hiç aramaz, talep
    sonsuza kadar "SMS gönderildi" görünür ve personel parayı ikinci kez ister.
    """
    service, api, store, request_id = await _linked()
    api.link_payload = {"id": 42, "code": "TKN-2", "url": "https://ode.me/TKN-2"}
    result = await service.start(request_id, reason=REASON, actor="Kemal", dry_run=False,
                                 send_sms=False)

    assert result["ok"] is True
    # Sıra ÖNEMLİ: önce iptal, sonra üretim.
    sira = [name for name, _, _ in api.calls
            if name in ("bbd_cancel_payment_link", "bbd_create_payment_link")]
    assert sira == ["bbd_create_payment_link", "bbd_cancel_payment_link",
                    "bbd_create_payment_link"]
    assert store.requests[0]["link_id"] == 42
    assert store.requests[0]["token"] == "TKN-2"
    # Eski belirteç olay zincirinde duruyor: satır değişse de "hangi bağlantı
    # vardı" sorusu cevapsız kalmıyor (olaylar silinmez).
    assert any("TKN-1" in event["detail"] for event in store.events)


async def test_onceki_baglanti_kapatilamazsa_yenisi_URETILMEZ() -> None:
    """"Kapatamadım ama yenisini ürettim" tam olarak iki-link durumudur; doğru
    cevap personele NEDEN kapatılamadığını söyleyip durmaktır."""
    service, api, store, request_id = await _linked()
    onceki = len([name for name, _, _ in api.calls if name == "bbd_create_payment_link"])
    api.cancel_error = "Mağaza şu an yanıt vermiyor."
    result = await service.start(request_id, reason=REASON, actor="Kemal", dry_run=False,
                                 send_sms=False)

    assert result["ok"] is False
    assert "ÜRETİLMEDİ" in result["error"]
    uretim = len([name for name, _, _ in api.calls if name == "bbd_create_payment_link"])
    assert uretim == onceki                      # ikinci link HİÇ üretilmedi
    assert store.requests[0]["token"] == "TKN-1"  # eldeki bağlantı duruyor


# =============================================================== şablon

async def test_sablonda_link_yer_tutucusu_zorunludur() -> None:
    service, _, _ = _service()
    result = await service.save_template(body="Sayin {ad}, borcunuz {tutar}",
                                         reason=REASON, actor="Kemal")
    assert result["ok"] is False
    assert "{link}" in result["error"]


async def test_taninmayan_yer_tutucu_kaydedilmez() -> None:
    service, _, _ = _service()
    result = await service.save_template(body="Sayin {isim}, {link}", reason=REASON,
                                         actor="Kemal")
    assert result["ok"] is False
    assert "{isim}" in result["error"]


async def test_kaydedilen_sablon_gonderimde_kullanilir() -> None:
    notify = FakeNotify(dry_run=False)
    service, _api, _ = _service(notify=notify, sms_dry_run=False)
    await service.save_template(body="Odeme: {link} - {kurum}", reason=REASON, actor="Kemal")
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert notify.provider.sent[0][1] == "Odeme: https://ode.me/TKN-1 - BBD Store"


async def test_hazir_sablon_sunucudan_gelir() -> None:
    """Panelin "geri yükle" düğmesi varsayılanı SUNUCUDAN okur. Panelde ikinci
    bir kopya tutulsaydı, varsayılan değişince ekran eskisini yüklerdi."""
    service, _, _ = _service()
    result = await service.template()
    assert result["defaultBody"] == collect.DEFAULT_TEMPLATE
    assert result["required"] == "{link}"


async def test_varsayilan_sablon_borcu_soyler_ve_link_tasir() -> None:
    """Kullanıcının istediği ifade: mesaj tutarın NEYİN karşılığı olduğunu
    söylemeli. `{link}` ise her hâlükârda bulunmak zorunda."""
    assert "{link}" in collect.DEFAULT_TEMPLATE
    assert "borcunuz" in collect.DEFAULT_TEMPLATE


# ========================================================== SMS kurulumu

async def test_netgsm_bilgileri_kasaya_yazilir_ayar_tablosuna_degil() -> None:
    """K8: parola depoya/ayara düşmez. Anahtar adları `km_platform/notify`
    ile aynıdır — kantin de aynı hesabı bu adlardan okur."""
    vault = FakeVault()
    service, _, store = _service(secrets=vault)
    result = await service.save_sms_settings(username="8503021234", password="gizli",
                                             header="BBDUNYAM", reason=REASON, actor="Kemal")

    assert result["ok"] is True
    assert vault.values["notify.netgsm.username"] == "8503021234"
    assert vault.values["notify.netgsm.password"] == "gizli"
    assert vault.values["notify.netgsm.header"] == "BBDUNYAM"
    assert store.prefs == {}                       # ayar tablosuna hiçbir şey yazılmadı


async def test_parola_ekrana_geri_verilmez() -> None:
    vault = FakeVault({"notify.netgsm.username": "850", "notify.netgsm.password": "gizli",
                       "notify.netgsm.header": "BBDUNYAM"})
    service, _, _ = _service(secrets=vault)
    result = await service.sms_settings()

    assert result["passwordConfigured"] is True
    assert "gizli" not in str(result)
    assert result["username"] == "850"


async def test_denetim_izine_parola_dusmez() -> None:
    vault = FakeVault()
    service, _, store = _service(secrets=vault)
    await service.save_sms_settings(username="850", password="gizli", header="BBDUNYAM",
                                    reason=REASON, actor="Kemal")
    kayit = [row for row in store.events if row["action"] == "sms_settings"]
    assert len(kayit) == 1
    assert "gizli" not in kayit[0]["detail"]


async def test_ilk_kayitta_parola_zorunludur() -> None:
    """Yarım kurulum hiç kurulmamış olmaktan kötüdür: ekran "hazır" der,
    mesaj gitmez."""
    service, _, _ = _service(secrets=FakeVault())
    result = await service.save_sms_settings(username="850", password="", header="BBDUNYAM",
                                             reason=REASON, actor="Kemal")
    assert result["ok"] is False
    assert "parola" in result["error"].lower()


async def test_kayitli_parola_bos_birakilinca_korunur() -> None:
    vault = FakeVault({"notify.netgsm.password": "eski"})
    service, _, _ = _service(secrets=vault)
    result = await service.save_sms_settings(username="850", password="", header="YENIBASLIK",
                                             reason=REASON, actor="Kemal")
    assert result["ok"] is True
    assert vault.values["notify.netgsm.password"] == "eski"
    assert vault.values["notify.netgsm.header"] == "YENIBASLIK"


async def test_bos_baslik_reddedilir() -> None:
    """Başlıksız gönderim Netgsm'de kod 40 ile reddedilir; kapıyı burada tut."""
    service, _, _ = _service(secrets=FakeVault())
    result = await service.save_sms_settings(username="850", password="gizli", header="",
                                             reason=REASON, actor="Kemal")
    assert result["ok"] is False
    assert "40" in result["error"]


async def test_uzun_baslik_reddedilir() -> None:
    service, _, _ = _service(secrets=FakeVault())
    result = await service.save_sms_settings(username="850", password="gizli",
                                             header="ONIKIKARAKTER", reason=REASON,
                                             actor="Kemal")
    assert result["ok"] is False
    assert "11" in result["error"]


async def test_gerekcesiz_netgsm_kaydi_reddedilir() -> None:
    vault = FakeVault()
    service, _, _ = _service(secrets=vault)
    result = await service.save_sms_settings(username="850", password="gizli",
                                             header="BBDUNYAM", reason="kısa", actor="Kemal")
    assert result["ok"] is False
    assert vault.values == {}


async def test_kasa_yokken_ekran_ayakta_kalir() -> None:
    """K7: kasa açılmamış bir kurulumda tahsilat ekranının tamamı düşmez;
    yalnız SMS ayarları kartı gerekçesiyle kapalı görünür."""
    service, _, _ = _service()
    result = await service.sms_settings()
    assert result["ok"] is True
    assert result["available"] is False
    assert result["error"]


async def test_kuru_prova_acikken_ekranda_acikca_yazar() -> None:
    """Kimlik bilgisi girilmiş bir kurulumda ekranın "hazır" demesi, mesajın
    gideceği anlamına gelmiyor: modül freni ayrı bir katman."""
    service, _, _ = _service(secrets=FakeVault(), sms_dry_run=True)
    result = await service.sms_settings()
    assert "sms_dry_run" in result["dryRunNotice"]

    service, _, _ = _service(secrets=FakeVault(), sms_dry_run=False)
    assert (await service.sms_settings())["dryRunNotice"] == ""


async def test_netgsm_40_hatasi_acik_metinle_doner() -> None:
    """Ham "[40] Gönderici başlığı sistemde tanımlı değil" cümlesi doğrudur ama
    personele ne yapacağını söylemez."""
    hata = FakeStoreApiError("[40] Gönderici başlığı sistemde tanımlı değil")
    hata.provider_code = "40"                      # type: ignore[attr-defined]
    notify = FakeNotify(dry_run=False)
    notify.provider.failure = hata
    service, _, _ = _service(notify=notify, sms_dry_run=False)
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)

    assert result["ok"] is False
    assert "Mesaj başlığı sistemde tanımlı değil" in result["hint"]


# ================================================================ liste

async def test_liste_ozeti_tahsil_edileni_ve_bekleyeni_ayirir() -> None:
    service, _, store = _service()
    await _create(service)
    await _create(service, phone="5322223344")
    store.requests[0]["status"] = collect.PAID
    result = await service.requests()
    assert result["summary"]["collected"] == 125_000
    assert result["summary"]["waiting"] == 125_000


async def test_liste_okunamazsa_ekran_ayakta_kalir() -> None:
    service, _, store = _service()

    async def patla(*_: Any, **__: Any) -> Any:
        raise RuntimeError("depo patladı")

    store.fetch_one = patla  # type: ignore[method-assign]
    result = await service.requests()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []


async def test_acik_link_raporu_sms_gonderilmis_satirlari_da_kapsar() -> None:
    """Link üretilince durum `linked`, SMS gidince `sent` oluyor. Rapor yalnız
    `linked` süzdüğü sürece tam da göstermesi gereken satırları atlıyordu."""
    service, _, store = _service()
    await _create(service)
    store.reads.clear()
    await service.build_report("openlinks", {})
    sql, params = store.reads[-1]
    assert "status IN (?, ?)" in sql
    assert collect.LINKED in params and collect.SENT in params


# ============================================================ yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_basilmaz() -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.printed: list[Any] = []

        async def print_file(self, path: Any, **_: Any) -> dict[str, Any]:
            self.printed.append(path)
            return {"printer": "HP"}

        async def status(self) -> dict[str, Any]:
            return {"ready": True}

    printer = FakePrinter()
    service, _, _ = _service()
    service._printer = printer  # testte yazıcı takılıyor
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"] or "bulunamadı" in result["error"]
    assert printer.printed == []
