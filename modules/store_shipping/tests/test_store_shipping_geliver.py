"""Geliver kurulum ekranı — saf kurallar + servis yolu.

BU DOSYA BİR SIRRI KORUYAN KODU SINAR. Testlerin çoğu tek bir cümleyi
kanıtlıyor: API tokenı hiçbir yoldan geri okunamaz, boş bırakılınca silinmez ve
canlıya geçiş sunucunun ön denetimini atlatamaz.

AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ, CANLIYA YAZMAZ. Geliver'a hiçbir çağrı
gitmez — geçit taklit edilir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_shipping_backend import geliver
from store_shipping_backend.service import ShippingService
from store_shipping_fakes import FakeApi, FakeLog, FakeStore

GEREKCE = "Müşteri talebi üzerine düzeltildi"
KURULUM_GEREKCESI = "Geliver kurulumu tamamlandı ve canlıya alındı"

#: Geliver tokenı 36 karakterdir; buradaki değer SAHTEDİR ve hiçbir yere gitmez.
SAHTE_TOKEN = "x" * 36


class SahteGecitHatasi(RuntimeError):
    """Geçidin `StoreApiError`ının testlik ikizi.

    Modül geçidin sınıfını import EDEMEZ (K3) ve etmez; servis `details`
    alanını `getattr` ile okur. Taklit de sınıfı değil YALNIZ alanı taklit
    eder.
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = "validation"
        self.status = 422
        self.details = details or {}


def _service(api: FakeApi | None = None, **config: Any) -> tuple[ShippingService, FakeApi,
                                                                 FakeStore]:
    api = api or FakeApi()
    store = FakeStore()
    service = ShippingService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ═══════════════════════════════════════════════════ token kuralı (SIR)

def test_bos_token_hata_degildir_dokunma_demektir() -> None:
    """Form kaydedilirken kutu çoğu zaman boş gelir; bu bir hata değil."""
    assert geliver.token_error("") == ""
    assert geliver.settings_patch({}, {"apiToken": ""}) == {}


def test_bos_token_mevcudu_silmez() -> None:
    """Boşu yazmak mağazanın kargo kimliğini silerdi ve belirtisi ancak ilk
    gerçek siparişte görünürdü: gönderi açılamaz."""
    patch = geliver.settings_patch({"active": True}, {"apiToken": "   ", "active": True})
    assert "api_token" not in patch


def test_maske_geri_gonderilemez() -> None:
    """Ekran tokenın yalnız son dört karakterini görüyor; onu "token" sanıp
    geri göndermek, aynı değeri yeniden yazan sessiz bir dal açardı."""
    problem = geliver.token_error("****abcd")
    assert "MASKE" in problem


def test_sifreli_deger_kabul_edilmez() -> None:
    assert "Şifreli" in geliver.token_error("enc:v1:AAAA")


def test_bosluk_iceren_token_reddedilir() -> None:
    """Kopyalarken satır sonu alınması en sık yapılan hata."""
    assert "boşluk" in geliver.token_error("abcd efgh ijkl mnop qrst")


def test_kisa_token_reddedilir_ve_uzunluk_soylenir() -> None:
    assert "karakter" in geliver.token_error("kisa")
    assert geliver.token_error(SAHTE_TOKEN) == ""


def test_token_durumu_degeri_degil_varligi_tasir() -> None:
    state = geliver.token_state({"hasToken": True, "tokenMask": "****abcd"})
    assert state == {"hasToken": True, "mask": "****abcd", "label": "****abcd"}
    assert geliver.token_state({})["hasToken"] is False


def test_onay_metni_token_degerini_yazmaz() -> None:
    """Onay metni ekranda durur, ekran görüntüsü alınır, destek kaydına
    yapıştırılır. Değerin oraya girmesi geri alınamaz."""
    labels = geliver.patch_labels({"api_token": SAHTE_TOKEN})
    assert labels == ["API tokenı (yeni değer)"]
    assert SAHTE_TOKEN not in " ".join(labels)


# ═══════════════════════════════════════════════ yalnız değişen alan yazılır

def test_degismeyen_alan_govdeye_konmaz() -> None:
    """Her kaydetmede beş alanı birden yazmak, denetim defterini anlamsız
    satırlarla doldurur ve iki ekranın aynı anda yazdığı değeri sessizce ezer."""
    now = {"active": True, "go_live": False, "test_mode": False, "sender_address_id": ""}
    patch = geliver.settings_patch(now, {"active": True, "goLive": False, "testMode": False,
                                         "senderAddressId": ""})
    assert patch == {}


def test_degisen_alan_bool_olarak_gider() -> None:
    now = {"active": True, "go_live": False}
    patch = geliver.settings_patch(now, {"goLive": True})
    assert patch == {"go_live": True}


def test_gonderici_adres_bosaltilabilir() -> None:
    """Boş `sender_address_id` "hesabın varsayılan adresini kullan" demektir —
    bir sırrın silinmesi değil, geçerli bir seçim."""
    patch = geliver.settings_patch({"sender_address_id": "abc-123"}, {"senderAddressId": ""})
    assert patch == {"sender_address_id": ""}


def test_adres_kutusuna_adres_yazilmasi_yakalanir() -> None:
    assert "KİMLİĞİ" in geliver.sender_error("Örnek Mah. No:3")
    assert geliver.sender_error("f81d4fae-7dec-11d0-a765-00a0c91e6bf6") == ""


# ═══════════════════════════════════════════════ denetim ve engel okuması

def test_denetlenmemis_kosul_yesile_donmez() -> None:
    """`None` = DENETLENMEDİ ve "geçti" SAYILMAZ. Denetlenmemiş bir koşulu
    yeşil göstermek, operatöre olmayan bir güvence verirdi."""
    lines = {item["key"]: item for item in geliver.check_lines(
        {"checks": {"apiReachable": None, "senderResolved": None}})}
    assert lines["api"]["state"] == "unknown"
    assert lines["sender"]["state"] == "unknown"


def test_gonderici_adresin_eksik_alanlari_ayrica_soylenir() -> None:
    """Gönderi oluşturma posta kodu ve telefon eksikken reddedilebiliyor;
    bu, "adres var" cevabıyla gizlenmemeli."""
    lines = {item["key"]: item for item in geliver.check_lines({"checks": {
        "apiReachable": True, "senderResolved": True,
        "senderAddress": {"name": "BBD Depo", "cityName": "İstanbul",
                          "hasPostcode": False, "hasPhone": True},
    }})}
    assert lines["sender"]["state"] == "ok"
    assert "posta kodu" in lines["sender"]["detail"]


def test_engel_mesaji_sunucudan_gelir_eylem_eklenir() -> None:
    """Sunucunun cevabını kendi cümlemizle değiştirmek gerçek sebebi gizler."""
    rows = geliver.blocker_lines({"goLiveBlockers": [
        {"code": "SENDER_ADDRESS_UNRESOLVED", "message": "Gönderici adres çözülemedi."}]})
    assert rows[0]["message"] == "Gönderici adres çözülemedi."
    assert "çıkış (gönderici) adresi" in rows[0]["action"]


def test_taninmayan_engel_kodu_listeden_dusmez() -> None:
    """Tanımadığımız bir engeli gizlemek, ekranı "her şey hazır" der hâle
    getirirdi."""
    rows = geliver.blocker_lines({"goLiveBlockers": [
        {"code": "YENI_BIR_ENGEL", "message": "Bilinmeyen sebep."}]})
    assert len(rows) == 1
    assert rows[0]["action"] == ""


def test_webhook_kayitli_degilse_ne_olacagi_yazilir() -> None:
    """Kayıtsız webhook sessiz bir arızadır: kargo yola çıkar, durum hiç
    güncellenmez ve ekranda her şey "etiket oluşturuldu"da donar."""
    line = geliver.webhook_line({"webhook": {
        "checked": True, "registered": ["https://baska/hook"],
        "defaultUrl": "https://bbdstore.com.tr/hook", "defaultRegistered": False}})
    assert line["state"] == "missing"
    assert "kendiliğinden güncellenmez" in line["message"]


def test_webhook_denetlenmediyse_kayitli_sayilmaz() -> None:
    assert geliver.webhook_line({"webhook": {"checked": False}})["state"] == "unknown"


# ═══════════════════════════════════════════════ yerel form denetimi

def test_canli_mod_kurulu_bayragi_olmadan_acilamaz() -> None:
    problems = geliver.draft_problems({"goLive": True, "active": False})
    assert problems and "Entegrasyon" in problems[0]


def test_gonderilmeyen_kurulu_bayragi_kapali_sayilmaz() -> None:
    """Gövdede olmayan alan "dokunma" demektir; yoksaydığımızı kapalı saymak,
    zaten kurulu bir mağazada canlıya geçişi yanlışlıkla engellerdi."""
    assert geliver.draft_problems({"goLive": True}) == []


# ═══════════════════════════════════════════════ servis yolu

async def test_ayarlar_okunur_ve_token_degeri_hic_gelmez() -> None:
    service, _, _ = _service()
    result = await service.geliver_settings()
    assert result["ok"] is True and result["connected"] is True
    assert result["token"] == {"hasToken": False, "mask": "", "label": "girilmemiş"}
    assert "api_token" not in result["settings"]
    assert result["blockers"][0]["code"] == "TOKEN_MISSING"


async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    """K7: kurulum ekranının en çok gerektiği an, bir şeyin bozuk olduğu an."""
    service, api, _ = _service()
    api.fail.add("bbd_geliver_settings")
    result = await service.geliver_settings()
    assert result["ok"] is True
    assert result["connected"] is False
    assert "patladı" in result["error"]


async def test_kisa_gerekce_reddedilir_para_uzunlugu_istenir() -> None:
    service, api, _ = _service()
    result = await service.save_geliver_settings(
        draft={"testMode": True}, reason="kısa", actor="Test", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_update_geliver_settings") == []


async def test_mevcut_ayar_okunamazsa_yazma_acilmaz() -> None:
    """OKU-DEĞİŞTİR-YAZ: neyin değiştiğini bilmeden yazmak, dokunulmayan
    bayrakları da ezerdi."""
    service, api, _ = _service()
    api.fail.add("bbd_geliver_settings")
    result = await service.save_geliver_settings(
        draft={"testMode": True}, reason=KURULUM_GEREKCESI, actor="Test", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_update_geliver_settings") == []


async def test_degisen_alan_yoksa_istek_cikmaz() -> None:
    service, api, _ = _service()
    result = await service.save_geliver_settings(
        draft={"active": True, "goLive": False, "testMode": False, "senderAddressId": ""},
        reason=KURULUM_GEREKCESI, actor="Test", dry_run=False)
    assert result["ok"] is False
    assert result["error"] == "Değişen alan yok."
    assert api.used("bbd_update_geliver_settings") == []


async def test_yalniz_degisen_alan_magazaya_gider() -> None:
    service, api, _ = _service()
    result = await service.save_geliver_settings(
        draft={"active": True, "goLive": False, "testMode": True, "senderAddressId": ""},
        reason=KURULUM_GEREKCESI, actor="Test", dry_run=False)
    assert result["ok"] is True
    assert api.used("bbd_update_geliver_settings")[0]["settings"] == {"test_mode": True}


async def test_token_govdeye_girer_ama_denetim_izine_degeri_yazilmaz() -> None:
    """Denetim izi diske yazılıyor ve UDİT ekranında görüntüleniyor."""
    service, api, store = _service()
    await service.save_geliver_settings(
        draft={"apiToken": SAHTE_TOKEN}, reason=KURULUM_GEREKCESI, actor="Test", dry_run=False)
    assert api.used("bbd_update_geliver_settings")[0]["settings"]["api_token"] == SAHTE_TOKEN
    izler = " ".join(str(row) for row in store.audit)
    assert SAHTE_TOKEN not in izler
    assert "api_token" in izler          # alan ADI iz için gerekli, DEĞERİ değil


async def test_prova_hicbir_sey_yazmaz_ama_ne_gidecegini_soyler() -> None:
    service, api, _ = _service()
    result = await service.save_geliver_settings(
        draft={"testMode": True}, reason=KURULUM_GEREKCESI, actor="Test", dry_run=True)
    assert result["ok"] is True and result["dryRun"] is True
    assert result["labels"] == ["test modu: açık"]
    assert api.used("bbd_update_geliver_settings")[0]["dry_run"] is True


async def test_gecersiz_token_magazaya_hic_gitmez() -> None:
    """Yerel denetim bir ağ turunu ve hız kovasından bir payı boşa harcamamak
    içindir; asıl kapı yine sunucudadır (K9)."""
    service, api, _ = _service()
    result = await service.save_geliver_settings(
        draft={"apiToken": "kisa"}, reason=KURULUM_GEREKCESI, actor="Test", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_update_geliver_settings") == []


async def test_on_denetim_reddi_nedenleriyle_birlikte_ekrana_tasinir() -> None:
    """`go_live` açılırken sunucu Geliver'a sorar ve geçemezse REDDEDER.

    Nedenler hata metnine sığmıyor; gövdede yapılı olarak geliyor ve ekranın
    asıl işi tam olarak onları göstermek.
    """
    service, api, _ = _service()
    api.geliver_write_error = SahteGecitHatasi(
        "Mağaza isteği doğrulayamadı: Canlıya geçilemedi",
        details={"blockers": [
            {"code": "SENDER_ADDRESS_UNRESOLVED",
             "message": "Gönderici (çıkış) adresi çözülemedi."}]})
    result = await service.save_geliver_settings(
        draft={"goLive": True}, reason=KURULUM_GEREKCESI, actor="Test", dry_run=False)
    assert result["ok"] is False
    assert result["blockers"][0]["code"] == "SENDER_ADDRESS_UNRESOLVED"
    assert "çıkış (gönderici) adresi" in result["blockers"][0]["action"]


async def test_yapili_neden_yoksa_ekran_yalniz_hata_metnini_gosterir() -> None:
    service, api, _ = _service()
    api.geliver_write_error = RuntimeError("bağlantı koptu")
    result = await service.save_geliver_settings(
        draft={"goLive": True}, reason=KURULUM_GEREKCESI, actor="Test", dry_run=False)
    assert result["ok"] is False
    assert result["blockers"] == []


async def test_baglanti_sinamasi_yazmaz_ve_defteri_kirletmez() -> None:
    """Okuma çağrısını deftere yazmak, defteri gerçek işlemlerin görünmez
    olacağı kadar gürültüyle doldurur."""
    service, api, store = _service()
    result = await service.test_geliver()
    assert result["ok"] is True
    assert api.used("bbd_test_geliver")
    assert store.audit == []


async def test_webhook_kaydi_ayri_bir_eylemdir_ve_gerekce_ister() -> None:
    service, api, _ = _service()
    assert (await service.register_webhook(url="", reason="kısa", actor="Test",
                                           dry_run=False))["ok"] is False
    assert api.used("bbd_register_shipment_webhook") == []

    result = await service.register_webhook(url="", reason=GEREKCE, actor="Test", dry_run=False)
    assert result["ok"] is True
    assert api.used("bbd_register_shipment_webhook")[0]["dry_run"] is False


async def test_ayar_kaydetmek_webhook_kaydi_acmaz() -> None:
    """Her "kaydet"te sessizce Geliver hesabında bir şey değiştirmek yanlış."""
    service, api, _ = _service()
    await service.save_geliver_settings(draft={"testMode": True}, reason=KURULUM_GEREKCESI,
                                        actor="Test", dry_run=False)
    assert api.used("bbd_register_shipment_webhook") == []
