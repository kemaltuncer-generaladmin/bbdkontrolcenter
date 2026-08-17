"""HTTP yüzeyinin sözleşmesi — izin kapıları, gövde alan adları, rota sırası.

Bu dosya iş kuralı sınamaz (o `test_bld_subscriptions_service.py`'nin işi);
ucun DIŞ yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9), panelden gelen
gövde tam olarak beklenen alan adlarını mı taşıyor ve sabit parçalı yollar
`{subscription_id}` ÖNÜNDE mi duruyor.
"""

from __future__ import annotations

import pytest
from bld_subscriptions_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Acme Gıda ile aylık abonelik anlaşması yapıldı"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    # --- okuma
    ("GET", "/overview"): {"bld_subscriptions.view"},
    ("GET", "/audit"): {"bld_subscriptions.view"},
    ("GET", "/requests"): {"bld_subscriptions.view"},
    ("GET", "/requests/{request_id}"): {"bld_subscriptions.view"},
    ("GET", "/contracts/{contract_id}"): {"bld_subscriptions.view"},
    ("GET", "/subscriptions"): {"bld_subscriptions.view"},
    ("GET", "/subscriptions/{subscription_id}"): {"bld_subscriptions.view"},
    ("GET", "/subscriptions/{subscription_id}/calendar"): {"bld_subscriptions.view"},
    ("GET", "/subscriptions/{subscription_id}/runs"): {"bld_subscriptions.view"},
    ("GET", "/subscriptions/{subscription_id}/contracts"): {"bld_subscriptions.view"},
    ("GET", "/subscriptions/{subscription_id}/payments"): {"bld_subscriptions.view"},
    # --- yazma
    ("PATCH", "/requests/{request_id}"): {"bld_subscriptions.manage"},
    ("POST", "/requests/{request_id}/convert"): {"bld_subscriptions.manage"},
    ("POST", "/contracts/{contract_id}/resend"): {"bld_subscriptions.manage"},
    ("POST", "/contracts/{contract_id}/cancel"): {"bld_subscriptions.manage"},
    ("POST", "/payments/{payment_id}/mark-paid"): {"bld_subscriptions.manage"},
    ("POST", "/orders/{order_id}/release"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions"): {"bld_subscriptions.manage"},
    ("PATCH", "/subscriptions/{subscription_id}"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/activate"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/pause"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/resume"): {"bld_subscriptions.manage"},
    # İPTAL AYRI BİR ANAHTAR İSTEMEZ ve bu bilinçli: abonelik iptali PARA
    # ÜRETMEZ. Üretilmiş siparişleri düşürmek `bld_orders.cancel` iznini ister
    # ve iade orada doğar. Üçüncü bir anahtar, taşıdığı hiçbir ayrıcalık
    # olmadan izin kataloğunu şişirirdi.
    ("POST", "/subscriptions/{subscription_id}/cancel"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/exceptions"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/exceptions/{service_date}/delete"):
        {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/generate"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/contracts"): {"bld_subscriptions.manage"},
    ("POST", "/subscriptions/{subscription_id}/payments"): {"bld_subscriptions.manage"},
    # --- ekran tercihi (BLD'ye hiçbir şey gitmez; `view` yeter)
    ("PUT", "/prefs"): {"bld_subscriptions.view"},
}


def _endpoints() -> dict[tuple[str, str], object]:
    out = {}
    for route in routes.router.routes:
        for method in sorted(route.methods):
            out[(method, route.path)] = route
    return out


def _declared(route: object) -> set[str]:
    """Ucun `requires(...)` ile ilan ettiği izinler.

    `requires` bir kapanış (closure) döndürüyor ve izin demeti orada duruyor;
    FastAPI bağımlılığın kendisini saklamıyor. Kapanışı okumak kırılgan
    görünüyor ama alternatifi yok ve kırıldığında SESSİZ KALMIYOR: demet
    bulunamazsa küme boş çıkar ve aşağıdaki `assert izinler` düşer.
    """
    bulunan: set[str] = set()
    for param in route.dependant.dependencies:  # type: ignore[attr-defined]
        for cell in getattr(param.call, "__closure__", None) or ():
            icerik = cell.cell_contents
            if isinstance(icerik, tuple) and all(isinstance(x, str) for x in icerik):
                bulunan.update(icerik)
    return bulunan


def test_her_uc_izin_ilan_eder_ve_beklenen_izni_tasir() -> None:
    # K9: izin ilan etmeyen uç nokta reddedilir. Bir ucun `requires(...)`
    # bağımlılığını unutmak, o ucu OTURUM AÇAN HERKESE açar ve hiçbir iş
    # kuralı testi bunu yakalamaz — servis yine doğru çalışır, yalnız kapı
    # açık kalır.
    bulunan = _endpoints()
    assert set(bulunan) == set(BEKLENEN), "uç listesi sözleşmeden ayrıştı"

    for anahtar, route in bulunan.items():
        izinler = _declared(route)
        assert izinler, f"{anahtar} izin ilan etmiyor (K9)"
        assert izinler == BEKLENEN[anahtar], f"{anahtar} izni değişmiş: {izinler}"


def test_sabit_parcali_yollar_kimlikli_yollardan_ONCE_kayitli() -> None:
    # SÖZLEŞMEDEKİ TUZAĞIN AYNISI (`subscriptions.md` → "Rota sırası").
    # `/subscriptions/{id}` önce kaydedilseydi FastAPI `orders` metnini `int`
    # kimliğe çeviremeyip 422 verirdi — hata "uç yok" demez, "kimlik sayı
    # değil" derdi ve sahada teşhis edilemezdi.
    yollar = [route.path for route in routes.router.routes]
    kimlikli = yollar.index("/subscriptions/{subscription_id}")
    for sabit in ("/requests", "/contracts/{contract_id}",
                  "/payments/{payment_id}/mark-paid", "/orders/{order_id}/release"):
        assert yollar.index(sabit) < kimlikli, f"{sabit} kimlikli yoldan sonra kayıtlı"


def test_denetleyici_metot_adlari_rota_dosyasinda_SABIT() -> None:
    # Rota ile denetleyici metot adının ayrışması ne açılışta ne `route:list`te
    # hata verir; yalnız uç çağrılınca patlar (Faz 1'de 13 test bu yüzden
    # düşmüştü). Adları burada dondurmak, servis tarafındaki bir yeniden
    # adlandırmanın testi düşürmesini sağlar.
    adlar = {route.name for route in routes.router.routes}
    assert adlar == {
        "overview", "audit", "requests", "request_detail", "update_request",
        "convert_request", "contract_detail", "resend_contract", "cancel_contract",
        "mark_paid", "release_order", "subscriptions", "create_subscription",
        "subscription_detail", "update_subscription", "activate_subscription",
        "pause_subscription", "resume_subscription", "cancel_subscription",
        "calendar", "create_exception", "delete_exception", "runs", "generate",
        "contracts", "create_contract", "payments", "create_payment", "save_prefs",
    }


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi — yani kuru prova
    # sanılan bir istek GERÇEK YAZMA yapardı.
    govde = routes.ReasonBody(reason=GEREKCE, dryRun=True)
    assert govde.dryRun is True

    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dry_run=True)

    # Alan hiç verilmezse `None` kalır ve varsayılanı SERVİS uygular.
    assert routes.ReasonBody(reason=GEREKCE).dryRun is None


def test_gerekce_semada_da_denetlenir_ve_ust_sinir_500() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı). Üst sınır 500'DÜR, 160 DEĞİL:
    # o daralma `veykemtu_order_revisions.reason` sütunundan geliyor ve
    # abonelik yazmaları o sütuna hiç dokunmuyor. 160 yazmak, sunucunun kabul
    # edeceği bir gerekçeyi ekranın reddetmesi olurdu.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 501)
    assert routes.ReasonBody(reason="x" * 500).reason


def test_abonelik_blogu_odeme_kipi_alani_TASIMAZ() -> None:
    # Tek geçerli değer `prepaid_monthly` (iş kararı 1 — cari hesap kalktı).
    # Alanı gövdeye koymak, bir gün `account` yazılabileceği izlenimi verirdi;
    # geçit de onu istek çıkmadan keser.
    assert "payment_mode" not in routes.SubscriptionBlock.model_fields
    with pytest.raises(ValidationError):
        routes.SubscriptionBlock(payment_mode="account")


def test_yeni_abonelik_ve_talep_donusumu_AYNI_blogu_kullanir() -> None:
    # Sözleşme talebi çevirirken bloğu `POST /` gövdesiyle aynı doğrulamadan
    # geçiriyor; ayrı iki gövde, talepten açılan aboneliğin elle açılandan
    # farklı kurallara tabi olması demekti.
    assert routes.CreateBody.model_fields["subscription"].annotation \
        is routes.SubscriptionBlock
    assert routes.ConvertBody.model_fields["subscription"].annotation \
        is routes.SubscriptionBlock


def test_kismi_guncelleme_govdesi_yazilamayan_alanlari_TASIMAZ() -> None:
    # `customer_id`, `location_id`, `start_date` ve `status` yazılamaz:
    # müşteriyi değiştirmek yeni abonelik açmaktır, durum kendi uçlarındadır.
    alanlar = set(routes.UpdateBody.model_fields)
    assert alanlar & {"customer_id", "location_id", "start_date", "status"} == set()
    for yasak in ("customer_id", "location_id", "start_date", "status"):
        with pytest.raises(ValidationError):
            routes.UpdateBody(reason=GEREKCE, **{yasak: 1})


def test_duraklatma_govdesi_bitis_gununu_ZORUNLU_kilar() -> None:
    # Süresiz duraklatma iptalin adı konmamış hâlidir ve iptalin kendi ucu var.
    with pytest.raises(ValidationError):
        routes.PauseBody(reason=GEREKCE, start_date="2026-09-01")
    govde = routes.PauseBody(reason=GEREKCE, start_date="2026-09-01",
                             end_date="2026-09-14")
    assert govde.end_date == "2026-09-14"
    # Duraklama etiketi denetim gerekçesinden AYRIDIR: biri kaydın kendisinde,
    # öbürü denetim izinde durur.
    assert govde.pause_reason == ""


def test_iptal_govdesi_gecerlilik_gunu_ister() -> None:
    with pytest.raises(ValidationError):
        routes.CancelBody(reason=GEREKCE)


def test_uretim_govdesinde_erken_serbest_birakma_VARSAYILAN_KAPALI() -> None:
    # Açık olsaydı elle üretilen her sipariş anında mutfak panosuna düşerdi ve
    # serbest bırakma saati hiçbir işe yaramazdı.
    assert routes.GenerateBody(reason=GEREKCE, service_date="2026-08-17").release_now \
        is False


def test_sozlesme_govdesi_SMS_gonderimini_varsayilan_ACIK_tutar() -> None:
    # `send_sms=False` yolu SERBEST (yönetici linki elden iletir) ama
    # varsayılan gönderimdir: kapalı varsayılan, oluşturulan ama kimseye
    # ulaşmayan sözleşmeler biriktirirdi.
    govde = routes.ContractBody(reason=GEREKCE)
    assert govde.send_sms is True
    assert govde.expires_in_days == 0     # 0 = tercihteki varsayılan
    with pytest.raises(ValidationError):
        routes.ContractBody(reason=GEREKCE, expires_in_days=31)


def test_odeme_govdesi_bos_tutari_None_olarak_tasir() -> None:
    # `None` = SUNUCU HESAPLASIN. Sıfır olsaydı sunucu sıfır tutarlı bir borç
    # yazardı ve "hesaplat" yolu hiç kullanılamazdı.
    govde = routes.PaymentBody(reason=GEREKCE, period_start="2026-08-01",
                               period_end="2026-08-31", due_date="2026-09-05")
    assert govde.amount_kurus is None


def test_tahsilat_govdesi_yontemi_zorunlu_ve_fatura_varsayilani_KAPALI() -> None:
    with pytest.raises(ValidationError):
        routes.MarkPaidBody(reason=GEREKCE)
    govde = routes.MarkPaidBody(reason=GEREKCE, method="online")
    assert govde.create_invoice is False
    assert govde.paid_at == ""


def test_istisna_govdesi_atla_ve_adedi_AYRI_alanlarda_tasir() -> None:
    # Şema ikisini birlikte kabul eder; tutarsızlığı SERVİS ve geçit reddeder.
    # Kapıyı şemaya koymak, "atla ama 12" cümlesini 422 ile geri döndürürdü ve
    # kullanıcı kendi cümlesini göremezdi.
    govde = routes.ExceptionBody(reason=GEREKCE, service_date="2026-08-20",
                                 quantity_override=12)
    assert govde.skip is False
    with pytest.raises(ValidationError):
        routes.ExceptionBody(reason=GEREKCE, service_date="2026-08-20",
                             quantity_override=0)


def test_istisna_silme_POST_ile_gerekce_tasir() -> None:
    # `DELETE` gövdesi ara katmanlarda güvenilir taşınmıyor; gerekçesiz geçen
    # bir silme denetim izini boş bırakırdı. Sunucuya giden istek yine
    # `DELETE`tir — çeviriyi geçit yapar.
    yol = "/subscriptions/{subscription_id}/exceptions/{service_date}/delete"
    route = _endpoints()[("POST", yol)]
    assert route.name == "delete_exception"


def test_tercih_govdesi_yalniz_uc_anahtar_tanir() -> None:
    assert set(routes.PrefsBody.model_fields) == {
        "page_size", "calendar_days", "expires_in_days"}
    with pytest.raises(ValidationError):
        routes.PrefsBody(page_size=1000)
    with pytest.raises(ValidationError):
        routes.PrefsBody(expires_in_days=31)
    # YOKLAMA AYARI YOKTUR: abonelik saatler-günler ölçeğinde değişir ve
    # 15 saniyede bir yoklayan bir ekran paylaşılan kovayı boşuna yakardı.
    with pytest.raises(ValidationError):
        routes.PrefsBody(poll_seconds=15)
