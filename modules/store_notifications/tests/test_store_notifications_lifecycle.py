"""Müşteri aşama SMS'i — üç şablon, tek segment, tekrar engeli, üç katmanlı fren.

TESTLER GERÇEK SMS GÖNDERMEZ ve bunu KANITLAR: sahte sağlayıcının `sent`
listesi, gönderilen her mesajı tutar. "Hata almadık" demek gönderilmediğini
göstermez; iddia listenin BOŞ olmasıyla kurulur.

Test adları tuzağı söyler — her biri `backend/lifecycle.py` başındaki altı
tuzaktan ya da görevin bir şartından birinin karşılığıdır.
"""

from __future__ import annotations

from typing import Any

from store_notifications_backend import lifecycle
from store_notifications_backend.lifecycle_service import LifecycleService, StageNotifier
from store_notifications_fakes import FakeLog, FakeNotify, FakeSmsProvider, FakeStore

GEREKCE = "Kargo aşaması metni müşteri geri bildirimiyle sadeleştirildi."

SIPARIS = {
    "orderId": 4173,
    "orderNo": "SP-2026-004173",
    "customer": "Ayse Yilmaz",
    "phone": "0532 123 45 67",
    "total": "1.249,90 TL",
    "date": "2026-08-13",
    "carrier": "Aras Kargo",
    "track": "7350041982",
    "trackUrl": "https://bbdstore.com.tr/kargo/7350041982",
}


def _service(*, notify: Any = None, **config: Any) -> tuple[LifecycleService, FakeStore]:
    store = FakeStore()
    service = LifecycleService(
        store=store, log=FakeLog(), notify=notify,
        config={"lifecycle_store_name": "BBD Store", "lifecycle_sms_dry_run": False,
                **config})
    return service, store


async def _open(service: LifecycleService, stage: str, body: str = "") -> None:
    """Aşamayı açar. Varsayılan metinle açmak da bilinçli bir karardır."""
    result = await service.save_stage(
        stage=stage, body=body or lifecycle.DEFAULT_TEMPLATES[stage], enabled=True,
        reason=GEREKCE, actor="Test")
    assert result["ok"] is True, result.get("error")


# ================================================ üç şablon: tek segment, farklı

def test_uc_varsayilan_sablon_tek_segmenttir() -> None:
    # TUZAK 1 + 2: ölçüm İYİMSER örnekle değil, bilerek UZUN `GUARD_SAMPLE` ile
    # yapılır. "Ayse Yilmaz" ile sığan metin "Mehmet Emin Karaosmanoglu" ile
    # taşar ve fatura iki katına çıkar.
    for stage in lifecycle.STAGES:
        body = lifecycle.DEFAULT_TEMPLATES[stage]
        filled = lifecycle.render(body, lifecycle.guard_values(stage))
        counted = lifecycle.plan(filled["text"])
        assert counted["parts"] == 1, (stage, counted["units"], filled["text"])
        assert counted["unicode"] is False, stage


def test_varsayilan_sablonlar_turkce_harf_tasimaz() -> None:
    # TUZAK 3: küçük `ç` GSM-7 TEMEL kümesinde yok ve tek başına mesajı
    # UCS-2'ye düşürüyor (160 sınırı 70'e iner). `ğ ı ş` ise iki septet yiyor.
    for stage in lifecycle.STAGES:
        counted = lifecycle.plan(lifecycle.DEFAULT_TEMPLATES[stage])
        assert counted["offending"] == [], (stage, counted["offending"])
        assert counted["encoding"] == "", stage


def test_uc_sablon_birbirinden_farklidir() -> None:
    bodies = [lifecycle.DEFAULT_TEMPLATES[stage] for stage in lifecycle.STAGES]
    assert len(set(bodies)) == 3


def test_kargo_mesaji_takip_kodu_firma_ve_baglanti_tasir() -> None:
    # TUZAK 5: bu üçü olmadan mesaj müşteriye hiçbir şey söylemez.
    body = lifecycle.DEFAULT_TEMPLATES["shipped"]
    for key in ("kargo_firma", "kargo_takip", "kargo_takip_linki"):
        assert "{{" + key + "}}" in body


# ============================================= TUZAK 1 — tek segment KAPISI

async def test_tek_segmenti_asan_sablon_kaydedilmez() -> None:
    service, store = _service()
    uzun = ("Sayin {{musteri_adi}}, {{siparis_no}} numarali siparisinizi aldik ve "
            "hazirlamaya basladik. Kitaplariniz raftan indi, paketleniyor ve en gec "
            "yarin kargoya verilecek; kargoya verilir verilmez takip numarasini "
            "ayrica yollayacagiz. {{magaza_adi}}")
    result = await service.save_stage(stage="order_placed", body=uzun, enabled=True,
                                      reason=GEREKCE, actor="Test")
    assert result["ok"] is False
    assert "SMS parçası" in result["error"]
    # KAYIT AÇILMADI: ekran "kaydedildi" demesin diye tablo boş kalmalı.
    assert store.lifecycle == {}


async def test_turkce_yuzunden_tasan_sablonda_sadelestirme_onerilir() -> None:
    service, _ = _service()
    # Aynı metin ASCII'de tek parçaya sığıyor; öneri metnin kendisini
    # DEĞİŞTİRMEZ, kararı kullanıcıya bırakır.
    turkce = ("Sayın {{musteri_adi}}, {{siparis_no}} numaralı siparişinizi aldık. "
              "Kitaplarınız raftan iniyor, hazır olur olmaz haber vereceğiz. İyi günler. "
              "{{magaza_adi}}")
    result = await service.save_stage(stage="order_placed", body=turkce, enabled=True,
                                      reason=GEREKCE, actor="Test")
    assert result["ok"] is False
    assert "sadeleştirmek" in result["error"]


async def test_reddedilen_kayit_denetim_izine_yazilir() -> None:
    service, store = _service()
    await service.save_stage(stage="order_placed", body="x" * 400, enabled=True,
                             reason=GEREKCE, actor="Test")
    assert [row for row in store.audit if row["result"] == "engellendi"]


# ==================================== TUZAK 4/5 — değişken kapıları

async def test_asamada_dolmayan_degisken_kaydedilmez() -> None:
    service, _ = _service()
    result = await service.save_stage(
        stage="order_placed",
        body="Sayin {{musteri_adi}}, {{siparis_no}} takip {{kargo_takip}}.",
        enabled=True, reason=GEREKCE, actor="Test")
    assert result["ok"] is False
    assert "kargo_takip" in result["error"]


async def test_kargo_sablonu_takip_bilgisi_olmadan_kaydedilmez() -> None:
    service, _ = _service()
    result = await service.save_stage(
        stage="shipped", body="Sayin {{musteri_adi}}, siparisiniz yola cikti.",
        enabled=True, reason=GEREKCE, actor="Test")
    assert result["ok"] is False
    assert "kargo_takip_linki" in result["error"]


async def test_palette_olmayan_degisken_kaydedilmez() -> None:
    service, _ = _service()
    result = await service.save_stage(
        stage="delivered",
        body="Sayin {{musteri_adi}}, {{siparis_no}} teslim edildi. {{uydurma_alan}}",
        enabled=True, reason=GEREKCE, actor="Test")
    assert result["ok"] is False
    assert "uydurma_alan" in result["error"]


async def test_gerekce_olmadan_asama_kaydedilmez() -> None:
    service, _ = _service()
    result = await service.save_stage(stage="delivered", body="", enabled=True,
                                      reason="kısa", actor="Test")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]


# ================================================ önizleme ve segment sayacı

def test_onizleme_ornek_veriyle_dolar_ve_sayac_doner() -> None:
    service, _ = _service()
    view = service.preview_stage(stage="shipped",
                                 body=lifecycle.DEFAULT_TEMPLATES["shipped"])
    assert view["ok"] is True
    assert "{{" not in view["preview"]
    assert view["plan"]["parts"] == 1
    assert view["plan"]["remaining"] > 0
    assert view["problem"] == ""


def test_onizleme_ile_kayit_kapisi_ayni_metni_olcer() -> None:
    # Ekran "1 parça" derken kaydın "2 parça" diye reddetmesi, kullanıcıya
    # düzeltemeyeceği bir hata göstermek olurdu.
    service, _ = _service()
    body = lifecycle.DEFAULT_TEMPLATES["order_placed"] + " Ek bir cumle daha."
    view = service.preview_stage(stage="order_placed", body=body)
    assert view["problem"] == lifecycle.template_problem("order_placed", body)


def test_bilinmeyen_asama_reddedilir() -> None:
    service, _ = _service()
    assert service.preview_stage(stage="siparis_geldi", body="x")["ok"] is False


# ================================================== aşama başına AÇIK/KAPALI

async def test_asamalar_varsayilan_kapalidir() -> None:
    service, _ = _service()
    payload = await service.stages()
    assert [item["stage"] for item in payload["items"]] == list(lifecycle.STAGES)
    assert all(item["enabled"] is False for item in payload["items"])
    assert all(item["default"] is True for item in payload["items"])


async def test_kapali_asamada_sms_gitmez_ve_iz_yazilmaz() -> None:
    provider = FakeSmsProvider()
    service, store = _service(notify=FakeNotify(provider=provider))
    result = await service.notify_stage(stage="delivered", order=SIPARIS, dry_run=False)
    assert result["sent"] is False
    assert result["skipped"] == "stage_disabled"
    assert provider.sent == []          # GERÇEKTEN gönderilmedi
    # Kapalı aşama bir gönderim denemesi değildir; iz kirletilmez.
    assert store.lifecycle_log == {}


# ============================================ TUZAK 6 + şart 9 — numara

async def test_numarasi_olmayan_musteri_sessizce_gecilmez() -> None:
    provider = FakeSmsProvider()
    service, store = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed",
                                        order={**SIPARIS, "phone": ""}, dry_run=False)
    assert result["sent"] is False
    assert result["result"] == "no_phone"
    assert "numara" in result["note"].lower()
    assert provider.sent == []
    # KAYIT TUTULUR VE GÖSTERİLİR: görünmeyen atlama, hiç yaşanmamış sayılır.
    rows = (await service.stage_log())["items"]
    assert rows[0]["result"] == "no_phone"
    assert rows[0]["resultLabel"] == "Gönderilemedi: numara yok"
    assert store.lifecycle_log[("order_placed", 4173)]["result"] == "no_phone"


async def test_gecersiz_numara_ayri_bir_sonuc_olarak_kaydedilir() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed",
                                        order={**SIPARIS, "phone": "0212 555 11 22"},
                                        dry_run=False)
    assert result["result"] == "bad_phone"
    assert provider.sent == []


async def test_numara_duzeltilince_yeniden_denenebilir() -> None:
    # "Numara yok" tekrar engeli DEĞİLDİR: numara düzeltilince mesaj gitmeli.
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    await service.notify_stage(stage="order_placed", order={**SIPARIS, "phone": ""},
                               dry_run=False)
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert result["sent"] is True
    assert len(provider.sent) == 1


async def test_takip_bilgisi_eksikse_yarim_kargo_mesaji_gitmez() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "shipped")
    result = await service.notify_stage(
        stage="shipped", order={**SIPARIS, "trackUrl": "", "track": ""}, dry_run=False)
    assert result["sent"] is False
    assert result["result"] == "missing"
    assert "kargo_takip" in result["note"]
    assert provider.sent == []


async def test_baglanti_yoksa_yapilandirilmis_onekten_kurulur() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider),
                          lifecycle_tracking_url_base="https://bbdstore.com.tr/kargo")
    await _open(service, "shipped")
    result = await service.notify_stage(stage="shipped", order={**SIPARIS, "trackUrl": ""},
                                        dry_run=False)
    assert result["sent"] is True
    assert "https://bbdstore.com.tr/kargo/7350041982" in provider.sent[0]["text"]


# ================================================ şart 6 — tekrar engeli

async def test_ayni_asama_ikinci_kez_gonderilmez() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "delivered")
    first = await service.notify_stage(stage="delivered", order=SIPARIS, dry_run=False)
    second = await service.notify_stage(stage="delivered", order=SIPARIS, dry_run=False)
    assert first["sent"] is True
    assert second["sent"] is False
    assert second["duplicate"] is True
    # WEBHOOK İKİ KEZ DÜŞTÜ: müşteri bir kez rahatsız oldu, bir kez ödendi.
    assert len(provider.sent) == 1


async def test_farkli_asamalar_birbirini_engellemez() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    await _open(service, "delivered")
    await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    await service.notify_stage(stage="delivered", order=SIPARIS, dry_run=False)
    assert len(provider.sent) == 2


async def test_kuru_prova_gercek_gonderimi_engellemez() -> None:
    # Prova müşteriyi rahatsız etmedi ve para harcamadı; onu engel saymak
    # provadan sonra gerçek gönderimi imkânsız kılardı.
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=True)
    assert provider.sent == []
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert result["sent"] is True


async def test_gonderilmis_asama_izi_kuru_provayla_ezilmez() -> None:
    provider = FakeSmsProvider()
    service, store = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=True)
    assert store.lifecycle_log[("order_placed", 4173)]["result"] == "sent"


async def test_gonderilmis_siparisler_onden_sorulabilir() -> None:
    service, _ = _service(notify=FakeNotify())
    await _open(service, "order_placed")
    await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    payload = await service.already_sent(stage="order_placed", order_ids=[4173, 9999])
    assert payload["ids"] == [4173]


async def test_siparis_kimligi_olmadan_gonderim_yapilmaz() -> None:
    # Tekrar engeli sipariş kimliğine bağlı; kimliksiz gönderim, engelsiz
    # gönderim demektir.
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed",
                                        order={**SIPARIS, "orderId": 0}, dry_run=False)
    assert result["ok"] is False
    assert provider.sent == []


# ==================================== üç katmanlı fren — HER KATMAN AYRI

async def test_istegin_kendi_kuru_provasi_gonderimi_durdurur() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=True)
    assert provider.sent == []                      # KANIT: hiç gönderilmedi
    assert result["result"] == "dry_run"
    assert "kuru provası" in result["note"]


async def test_modul_freni_gonderimi_durdurur_ve_nedenini_yazar() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider),
                          lifecycle_sms_dry_run=True)
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert provider.sent == []
    assert "lifecycle_sms_dry_run" in result["note"]


async def test_platform_freni_gonderimi_durdurur() -> None:
    # Sağlayıcı isteği alır ama `dry_run` işaretiyle döner; ekran bunu
    # "gönderildi" saymamalı.
    provider = FakeSmsProvider(dry_run=True)
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert result["sent"] is False
    assert result["result"] == "dry_run"
    assert "platform.notify.sms.dry_run" in result["note"]


async def test_beyaz_liste_disindaki_numaraya_gercek_sms_gitmez() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider),
                          lifecycle_sms_allowlist=["05559998877"])
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert provider.sent == []
    assert "beyaz listede" in result["note"]


async def test_beyaz_listedeki_numara_bicimden_bagimsiz_taninir() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider),
                          lifecycle_sms_allowlist=["+90 532 123 45 67"])
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert result["sent"] is True
    assert provider.sent[0]["to"] == "5321234567"


async def test_sms_katmani_yoksa_ekran_ayakta_kalir_ve_neden_yazilir() -> None:
    service, _ = _service(notify=None)
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert result["sent"] is False
    assert "notify" in result["note"]
    state = await service.sms_state()
    assert state["available"] is False
    assert state["platformDryRun"] is True      # bilinmeyen fren AÇIK varsayılır


async def test_saglayici_patlarsa_iz_hata_olarak_yazilir() -> None:
    provider = FakeSmsProvider()
    provider.fail = True
    service, store = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert result["ok"] is False
    assert store.lifecycle_log[("order_placed", 4173)]["result"] == "error"


async def test_saglayici_kabul_etmezse_gonderildi_sayilmaz() -> None:
    provider = FakeSmsProvider(accepted=False)
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    result = await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    assert result["sent"] is False
    assert result["result"] == "error"


# ================================================================ metin

async def test_gonderilen_metin_musteri_verisiyle_dolar() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "shipped")
    await service.notify_stage(stage="shipped", order=SIPARIS, dry_run=False)
    text = provider.sent[0]["text"]
    assert "{{" not in text
    assert "Ayse Yilmaz" in text
    assert "7350041982" in text
    assert "Aras Kargo" in text


async def test_gonderim_izinde_numara_maskelidir() -> None:
    service, _ = _service(notify=FakeNotify())
    await _open(service, "order_placed")
    await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    row = (await service.stage_log())["items"][0]
    assert row["phone"].endswith("4567")
    assert "5321234567" not in row["phone"]


async def test_iz_asamaya_ve_sonuca_gore_suzulur() -> None:
    service, _ = _service(notify=FakeNotify())
    await _open(service, "order_placed")
    await _open(service, "delivered")
    await service.notify_stage(stage="order_placed", order=SIPARIS, dry_run=False)
    await service.notify_stage(stage="delivered", order={**SIPARIS, "phone": ""},
                               dry_run=False)
    assert len((await service.stage_log(stage="delivered"))["items"]) == 1
    assert len((await service.stage_log(result="no_phone"))["items"]) == 1


# =========================================================== yetenek yüzeyi

async def test_yetenek_yuzeyi_dar_ve_kuru_prova_varsayilan_aciktir() -> None:
    provider = FakeSmsProvider()
    service, _ = _service(notify=FakeNotify(provider=provider))
    await _open(service, "order_placed")
    notifier = StageNotifier(service)
    # `dry_run` VERİLMEDİ: varsayılan açık olmalı, yoksa çağıran modülün
    # unutkanlığı müşteriye gerçek SMS olurdu.
    result = await notifier.notify(stage="order_placed", order=SIPARIS)
    assert result["sent"] is False
    assert provider.sent == []
    state = await notifier.state()
    assert state["enabled"] == ["order_placed"]
    assert (await notifier.done(stage="order_placed", order_ids=[4173]))["ids"] == []
