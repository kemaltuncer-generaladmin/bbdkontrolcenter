"""İş kuralları — kuru prova, izin kapısı, denetim izi, K7 dayanıklılığı.

Hiçbir test AĞA ÇIKMAZ ve HİÇBİR TEST GERÇEK SMS GÖNDERMEZ: geçit bütünüyle
taklit edilir ve taklidin gönderim metotları yalnız çağrıyı kaydeder.

Dosyanın ana iddiası tek cümle: **bu ekrandan kazara toplu SMS çıkmaz.** Kapılar
üst üstedir ve her biri ayrı ayrı sınanır — ayrı izin, zorunlu gerekçe, zorunlu
kuru prova, tek kullanımlık jeton, alıcı sayısı eşleşmesi, jeton ömrü.
"""

from __future__ import annotations

import pytest
from bld_sms_backend.service import SmsService
from bld_sms_fakes import (
    TEMPLATE,
    TEMPLATE_OFF,
    FakeApi,
    FakeBus,
    FakeLog,
    FakeStore,
)

GEREKCE = "Sipariş SMS metnine teslim saati eklendi"
AKTOR = "Ayşe Yılmaz"


def kur(api: FakeApi | None = None, store: FakeStore | None = None,
        bus: FakeBus | None = None, notify: object | None = None,
        **config: object) -> tuple[SmsService, FakeApi, FakeStore, FakeBus]:
    api = api or FakeApi()
    store = store or FakeStore()
    bus = bus or FakeBus()
    service = SmsService(api=api, store=store, log=FakeLog(),
                         config={"dry_run_default": False, **config},
                         notify=notify, publish=bus)
    return service, api, store, bus


# ================================================================== okuma

@pytest.mark.asyncio
async def test_sablon_listesi_katalogla_zenginlestirilir() -> None:
    service, _, _, _ = kur(FakeApi([dict(TEMPLATE), dict(TEMPLATE_OFF)]))
    cevap = await service.templates()

    assert cevap["ok"] is True
    assert cevap["connected"] is True
    ilk = cevap["data"][0]
    # Sunucunun sözlüğü olduğu gibi taşınır…
    assert ilk["key"] == "order_created"
    assert ilk["segments"] == 1
    # …ekranın kendi kataloğu üstüne binerek gelir.
    assert ilk["group"] == "order"
    assert ilk["sample"]["order_no"] == "BLD-8421"
    assert cevap["sender"] == {"driver": "netgsm", "configured": True}


@pytest.mark.asyncio
async def test_saglayici_kurulu_degilse_ekran_bunu_gorur() -> None:
    # `sender_configured: false` → gönderimler yalnız günlüğe yazılır
    # (`LogSmsSender`). Panel bunu açıkça göstermeli; aksi hâlde "SMS gitti"
    # diyen bir ekran hiçbir şey göndermemiş olur.
    api = FakeApi()
    api.templates_meta = {"sender_driver": "log", "sender_configured": False}
    service, _, _, _ = kur(api)

    cevap = await service.templates()
    assert cevap["sender"]["configured"] is False


@pytest.mark.asyncio
async def test_gecit_dusunce_ekran_ayakta_kalir() -> None:
    # K7: okuma uçları İSTİSNA SIZDIRMAZ. `ok: True` ucun sağlığını anlatır,
    # `connected: False` ise "veri yok değil, ŞU AN okunamıyor" der. İkisini
    # ayırmayan bir panel "şablon yok" diye yalan söylerdi.
    api = FakeApi()
    api.fail = {"sms_templates", "sms_log", "sms_announcement"}
    service, _, _, _ = kur(api)

    for cevap in (await service.templates(), await service.log(),
                  await service.announcement()):
        assert cevap["ok"] is True
        assert cevap["connected"] is False
        assert cevap["error"]


@pytest.mark.asyncio
async def test_platform_seridi_bld_saglayicisindan_ayri_raporlanir() -> None:
    # Kontrol Merkezi'nin kendi Netgsm şeridi bu ekranın GÖNDERİM YOLU DEĞİL.
    # Ayrı satırda durmazsa, BLD'nin sırrı eksikken yönetici buradaki ayarı
    # düzeltmeye çalışır ve hiçbir şey değişmez.
    service, _, _, _ = kur(notify=object())
    cevap = await service.templates()
    assert cevap["platform_lane"]["available"] is True
    assert cevap["platform_lane"] != cevap["sender"]

    service, _, _, _ = kur()
    assert (await service.templates())["platform_lane"]["available"] is False


@pytest.mark.asyncio
async def test_bu_ekrandan_hic_acilmamis_sablon_isaretlenir() -> None:
    # Sunucudaki `enabled` "bugün açık" der, "bilinçli açıldı" demez. Açık
    # doğmuş bir şablon tek dağıtımı binlerce SMS'e çevirir; ekran farkı
    # görebilmeli.
    service, _, _, _ = kur(FakeApi([dict(TEMPLATE), dict(TEMPLATE_OFF)]))
    cevap = await service.templates()
    satirlar = {row["key"]: row for row in cevap["data"]}

    assert satirlar["order_created"]["unconfirmed_enabled"] is True
    assert satirlar["order_cancelled"]["unconfirmed_enabled"] is False
    assert satirlar["order_created"]["local"]["confirmed"] is False


@pytest.mark.asyncio
async def test_yerel_olcum_aga_cikmaz() -> None:
    service, api, store, _ = kur()
    cevap = service.measure(body="Sayın {customer_name}, {order_no} hazır.",
                            key="order_created")

    assert cevap["ok"] is True
    assert "Mehmet Kaya" in cevap["data"]["rendered"]
    assert cevap["data"]["unresolved_variables"] == []
    # Sayaç yerelde çalışır: geçidin dakikada 18 istekle sınırlı tek kovası
    # her tuş vuruşunda yanmaz ve denetim izi ölçüm satırlarıyla dolmaz.
    assert api.calls == []
    assert store.audit == []


# ============================================================== şablonlar

@pytest.mark.asyncio
async def test_gerekcesiz_yazma_istek_cikmadan_durur() -> None:
    service, api, store, _ = kur()
    cevap = await service.update_template("order_created", body="Yeni metin",
                                          enabled=None, reason="kısa", actor=AKTOR,
                                          dry_run=False)
    assert cevap["ok"] is False
    assert api.writes() == []
    assert store.results("template_update") == ["engellendi"]


@pytest.mark.asyncio
async def test_taninmayan_degisken_istek_cikmadan_reddedilir() -> None:
    # Sunucu da 422 verirdi; burada kesilmesinin sebebi anlaşılır bir cümle
    # vermek VE hız kovasını boşuna yakmamak.
    service, api, store, _ = kur()
    cevap = await service.update_template(
        "order_created", body="Sayın {musteri_adi}, siparişiniz alındı.", enabled=None,
        reason=GEREKCE, actor=AKTOR, dry_run=False)

    assert cevap["ok"] is False
    assert "musteri_adi" in cevap["error"]
    assert api.writes() == []
    assert store.detail(-1)["unknown_variables"] == ["musteri_adi"]


@pytest.mark.asyncio
async def test_kismi_yazma_yalniz_verilen_alani_gonderir() -> None:
    # Tam gövde göndermek, dokunulmamış alanı yanlışlıkla yazmak olurdu:
    # yalnız metni düzeltmek isteyen bir çağrı bildirimi de kapatabilirdi.
    service, api, _, _ = kur()
    await service.update_template("order_created", body=None, enabled=True,
                                  reason=GEREKCE, actor=AKTOR, dry_run=False)

    gonderilen = api.used("update_sms_template")[0]
    assert gonderilen["enabled"] is True
    assert "body" not in gonderilen


@pytest.mark.asyncio
async def test_yazma_zinciri_once_iz_sonra_gecit() -> None:
    # `denendi` satırı geçit çağrısından ÖNCE düşmeli: ağ koparsa geriye
    # YALNIZ o kalır ve "kim neyi denedi" sorusunun tek cevabı olur.
    service, _, store, _ = kur()
    await service.update_template("order_created", body="Sayın {customer_name}.",
                                  enabled=None, reason=GEREKCE, actor=AKTOR,
                                  dry_run=False)
    assert store.results("template_update") == ["denendi", "ok"]
    assert store.audit[0]["actor"] == AKTOR


@pytest.mark.asyncio
async def test_denetim_satirina_metnin_tamami_yazilmaz() -> None:
    # Sözleşmenin kuralı: `payload_json` metnin tamamını yazmaz, yalnız
    # ölçüsünü. Gönderilen cümlenin arşivi `veykemtu_sms_log` tarafındadır.
    metin = "Sayın {customer_name}, siparişiniz hazırlandı ve yola çıktı."
    service, _, store, _ = kur()
    await service.update_template("order_created", body=metin, enabled=None,
                                  reason=GEREKCE, actor=AKTOR, dry_run=False)

    for satir in store.actions("template_update"):
        assert metin not in satir["detail"]
    assert store.detail(-1)["length_to"] == len(metin)


@pytest.mark.asyncio
async def test_bildirim_acmak_ayri_bir_eylem_olarak_kaydedilir() -> None:
    # "Metni düzelttim" ile "bildirimi açtım" denetim izinde AYNI satır
    # olamaz: ikincisi müşteriye mesaj gitmeye başlaması demektir.
    service, _, store, _ = kur(FakeApi([dict(TEMPLATE_OFF)]))
    await service.update_template("order_cancelled", body=None, enabled=True,
                                  reason="İptal bildirimi bilerek açıldı", actor=AKTOR,
                                  dry_run=False)

    assert store.results("template_enable") == ["denendi", "ok"]
    assert store.triggers["order_cancelled"]["confirmed"] == 1


@pytest.mark.asyncio
async def test_kapatmak_bir_kez_acildi_bilgisini_silmez() -> None:
    service, _, store, _ = kur(FakeApi([dict(TEMPLATE_OFF)]))
    await service.update_template("order_cancelled", body=None, enabled=True,
                                  reason="İptal bildirimi bilerek açıldı", actor=AKTOR,
                                  dry_run=False)
    await service.update_template("order_cancelled", body=None, enabled=False,
                                  reason="Şablon geçici olarak kapatıldı", actor=AKTOR,
                                  dry_run=False)

    kayit = store.triggers["order_cancelled"]
    assert kayit["confirmed"] == 1      # geri dönmez
    assert kayit["last_state"] == 0     # bugünkü hâl kapalı


@pytest.mark.asyncio
async def test_kuru_provada_yerel_temel_cizgi_ilerlemez() -> None:
    # Provada BLD'de hiçbir şey değişmedi; yereli ilerletmek ekranı olmayan
    # bir yazmaya inandırmak olurdu.
    service, _, store, _ = kur()
    cevap = await service.update_template("order_created", body="Sayın {customer_name}.",
                                          enabled=True, reason=GEREKCE, actor=AKTOR,
                                          dry_run=True)
    assert cevap["dry_run"] is True
    assert store.baselines == {}
    assert store.triggers == {}


@pytest.mark.asyncio
async def test_gecit_provaya_dusurdugunde_ekran_yazildi_demez() -> None:
    # Bir kurulum geçidin varsayılanını geri açarsa `dry_run=False` istenen bir
    # çağrı yine provaya düşebilir. SORDUĞUMUZ DEĞİL, CEVAP OKUNUR.
    class SinsiApi(FakeApi):
        async def update_sms_template(self, key: str, *, reason: str, actor: str,
                                      dry_run: bool | None = None, **fields: object):
            await super().update_sms_template(key, reason=reason, actor=actor,
                                              dry_run=dry_run, **fields)
            return {"ok": True, "dry_run": True, "audit_id": 1, "would": {"key": key}}

    service, _, store, _ = kur(SinsiApi())
    cevap = await service.update_template("order_created", body="Sayın {customer_name}.",
                                          enabled=None, reason=GEREKCE, actor=AKTOR,
                                          dry_run=False)
    assert cevap["dry_run"] is True
    assert store.baselines == {}


@pytest.mark.asyncio
async def test_olmayan_sablona_yazilamaz() -> None:
    service, api, _, _ = kur()
    cevap = await service.update_template("otp_login", body="kod {code}", enabled=None,
                                          reason=GEREKCE, actor=AKTOR, dry_run=False)
    # `otp_login` sözleşmede YOK ve olmayacak: kimlik doğrulama metni yönetim
    # yüzeyinden uzak durur.
    assert cevap["ok"] is False
    assert api.writes() == []


@pytest.mark.asyncio
async def test_iz_yazilamazsa_is_durmaz() -> None:
    # K7: denetim satırı yazılamadı diye şablon güncellemesi düşmez.
    store = FakeStore()
    store.broken = True
    service, api, _, _ = kur(store=store)
    cevap = await service.update_template("order_created", body="Sayın {customer_name}.",
                                          enabled=None, reason=GEREKCE, actor=AKTOR,
                                          dry_run=False)
    assert cevap["ok"] is True
    assert api.writes() == ["update_sms_template"]


# ============================================================== deneme SMS

@pytest.mark.asyncio
async def test_gecersiz_numara_istek_cikmadan_reddedilir() -> None:
    service, api, _, _ = kur()
    cevap = await service.send_test(phone="0212 123 45 67", template_key="order_created",
                                    body="", sample=None, reason=GEREKCE, actor=AKTOR,
                                    dry_run=False)
    assert cevap["ok"] is False
    assert "5XXXXXXXXX" in cevap["error"]
    assert api.writes() == []


@pytest.mark.asyncio
async def test_sablon_ve_serbest_metin_birlikte_verilemez() -> None:
    service, api, _, _ = kur()
    cevap = await service.send_test(phone="5321234567", template_key="order_created",
                                    body="Serbest", sample=None, reason=GEREKCE,
                                    actor=AKTOR, dry_run=False)
    assert cevap["ok"] is False
    assert api.writes() == []


@pytest.mark.asyncio
async def test_deneme_numarasi_denetim_satirinda_maskelidir() -> None:
    service, _, store, _ = kur()
    await service.send_test(phone="0532 123 45 67", template_key="order_created",
                            body="", sample=None, reason=GEREKCE, actor=AKTOR,
                            dry_run=False)
    for satir in store.actions("send_test"):
        assert "5321234567" not in satir["detail"]
    assert store.detail(-1)["phone"] == "532****567"


@pytest.mark.asyncio
async def test_saglayici_hatasi_istek_hatasi_degildir() -> None:
    # Sunucu `502` değil, `ok: true` + `data.status: "failed"` döndürüyor:
    # gönderim denemesi kayda geçti, isteğin kendisi başarısız değil. Ekran
    # ikisini ayırmalı ki "sunucu bozuldu" ile "numara geçersiz" karışmasın.
    api = FakeApi()
    api.test_status = "failed"
    service, _, store, _ = kur(api)

    cevap = await service.send_test(phone="5321234567", template_key="order_created",
                                    body="", sample=None, reason=GEREKCE, actor=AKTOR,
                                    dry_run=False)
    assert cevap["ok"] is True
    assert cevap["data"]["status"] == "failed"
    assert store.results("send_test")[-1] == "hata"


# ================================================================= duyuru

@pytest.mark.asyncio
async def test_taslak_yazmak_gondermek_degildir() -> None:
    service, api, _, bus = kur()
    await service.set_announcement(body="30 Ağustos'ta hizmet veremeyeceğiz.",
                                   audience="active_customers", reason=GEREKCE,
                                   actor=AKTOR, dry_run=False)
    assert api.writes() == ["set_sms_announcement"]
    assert bus.names() == []


@pytest.mark.asyncio
async def test_bilinmeyen_kitle_reddedilir() -> None:
    service, api, _, _ = kur()
    cevap = await service.set_announcement(body="Metin", audience="everyone",
                                           reason=GEREKCE, actor=AKTOR, dry_run=False)
    assert cevap["ok"] is False
    assert api.writes() == []


@pytest.mark.asyncio
async def test_kuru_prova_yapilmadan_gonderim_yok() -> None:
    # TOPLU GÖNDERİMİN İLK KAPISI. Sunucunun `confirm_recipients` kapısı
    # "sayı değişti"yi yakalar; "hiç prova yapılmadı" durumunu yakalayamaz,
    # çünkü onun için ilk istek de geçerli bir istektir.
    service, api, store, bus = kur()
    cevap = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False, token="",
                                           allow_send=True)
    assert cevap["ok"] is False
    assert "kuru prova" in cevap["error"].lower()
    assert api.writes() == []
    assert bus.names() == []
    assert store.results("announcement_run") == ["engellendi"]


@pytest.mark.asyncio
async def test_kuru_prova_istegi_gercekten_sunucuya_gider() -> None:
    # Yerelde uydurulmuş bir tahmin ile onay almak, sunucunun reddedeceği bir
    # gönderimi onaylatmak olurdu. Bayrak sunucuya kadar iletilir.
    service, api, _, bus = kur()
    cevap = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=False)

    assert cevap["ok"] is True
    assert cevap["dry_run"] is True
    assert api.used("run_sms_announcement")[0]["dry_run"] is True
    # Kaç kişiye ve GERÇEK İŞLENMİŞ GÖVDE — ikisi de provadan gelir.
    assert cevap["data"]["recipients"] == 186
    assert cevap["data"]["sample_rendered"]
    assert bus.names() == []


@pytest.mark.asyncio
async def test_prova_izni_gonderim_izni_degildir() -> None:
    # `manage` provayı görebilir, gönderemez. Ayrım burada da denetlenir
    # (K9 — çift kapı); uçtaki `requires(MANAGE, ANNOUNCE)` "en az biri" der.
    service, api, _, _ = kur()
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=False)
    assert prova["ok"] is True

    cevap = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False,
                                           token=prova["data"]["token"],
                                           allow_send=False)
    assert cevap["ok"] is False
    assert "bld_sms.announce" in cevap["error"]
    # Provanın kendi çağrısı listede duruyor; GERÇEK gönderim hiç çıkmadı.
    assert [c for c in api.used("run_sms_announcement") if not c["dry_run"]] == []


@pytest.mark.asyncio
async def test_provadan_sonra_gonderim_yapilir_ve_olay_yayinlanir() -> None:
    service, _api, store, bus = kur()
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    cevap = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False,
                                           token=prova["data"]["token"], allow_send=True)

    assert cevap["ok"] is True
    assert cevap["data"]["sent"] == 184
    assert bus.names() == ["bld_sms.announcement_sent"]
    assert store.results("announcement_run")[-1] == "ok"


@pytest.mark.asyncio
async def test_jeton_bir_kez_kullanilir() -> None:
    # Çift tıklama ile aynı duyuruyu iki kez almak, müşterinin gördüğü tek
    # şeydir. Sunucunun 10 dakikalık soğuma penceresi ikinci savunma hattıdır;
    # ilki burada.
    service, api, _, _ = kur()
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    token = prova["data"]["token"]
    await service.run_announcement(confirm_recipients=186, reason=GEREKCE, actor=AKTOR,
                                   dry_run=False, token=token, allow_send=True)
    ikinci = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                            actor=AKTOR, dry_run=False, token=token,
                                            allow_send=True)

    assert ikinci["ok"] is False
    assert api.used("run_sms_announcement")[-1]["dry_run"] is False
    assert len([c for c in api.used("run_sms_announcement") if not c["dry_run"]]) == 1


@pytest.mark.asyncio
async def test_alici_sayisi_provadakinden_farkliysa_gonderilmez() -> None:
    # Yönetici ekranda 186 görüp onayladıysa ve arada beş müşteri daha
    # eklendiyse gönderim sessizce büyümemeli.
    service, api, _, _ = kur()
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    cevap = await service.run_announcement(confirm_recipients=191, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False,
                                           token=prova["data"]["token"], allow_send=True)

    assert cevap["ok"] is False
    assert "186" in cevap["error"]
    assert [c for c in api.used("run_sms_announcement") if not c["dry_run"]] == []


@pytest.mark.asyncio
async def test_suresi_dolmus_prova_ile_gonderim_yok() -> None:
    service, api, store, _ = kur(announcement_dry_run_ttl_minutes=1)
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    token = prova["data"]["token"]
    # Jetonu elle eskitmek, saat beklemekten iyidir; kural damgaya bakıyor.
    store.dry_runs[token]["created_at"] = "2020-01-01T00:00:00+00:00"

    cevap = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False, token=token,
                                           allow_send=True)
    assert cevap["ok"] is False
    assert [c for c in api.used("run_sms_announcement") if not c["dry_run"]] == []


@pytest.mark.asyncio
async def test_taslak_degisince_bekleyen_prova_duser() -> None:
    # Prova "şu metin, şu kadar kişiye" diyordu; metin değiştiyse o onay artık
    # başka bir mesaja aitti.
    service, api, _, _ = kur()
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    await service.set_announcement(body="Bambaşka bir duyuru metni.",
                                   audience="active_customers",
                                   reason="Duyuru metni yeniden yazıldı", actor=AKTOR,
                                   dry_run=False)

    cevap = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False,
                                           token=prova["data"]["token"], allow_send=True)
    assert cevap["ok"] is False
    assert [c for c in api.used("run_sms_announcement") if not c["dry_run"]] == []


@pytest.mark.asyncio
async def test_bos_taslakla_prova_yapilmaz() -> None:
    api = FakeApi()
    api.announcement_payload = {**api.announcement_payload, "body": "   "}
    service, _, _, _ = kur(api)

    cevap = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    assert cevap["ok"] is False
    assert api.used("run_sms_announcement") == []


@pytest.mark.asyncio
async def test_prova_istenip_gercek_gonderim_bildirilirse_uyarilir() -> None:
    # Geçit ya da sunucu provayı gerçek yazmaya çevirirse bu bir yapılandırma
    # hatasıdır ve SESSİZ GEÇİLEMEZ: SMS'ler gitmiş olabilir.
    class SinsiApi(FakeApi):
        async def run_sms_announcement(self, *, confirm_recipients: int, reason: str,
                                       actor: str, dry_run: bool | None = None):
            await super().run_sms_announcement(confirm_recipients=confirm_recipients,
                                               reason=reason, actor=actor,
                                               dry_run=dry_run)
            return {"ok": True, "dry_run": False, "audit_id": 9, "data": {"sent": 186}}

    service, _, store, _ = kur(SinsiApi())
    cevap = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    assert cevap["ok"] is False
    assert "gönderim" in cevap["error"].lower()
    assert store.results("announcement_run") == ["ok"]


@pytest.mark.asyncio
async def test_dinleyici_patlarsa_gonderim_basarili_sayilir() -> None:
    # K7: SMS'ler gitti; dinleyicinin patlaması onları geri getirmez.
    bus = FakeBus()
    bus.fail = True
    service, _, _, _ = kur(bus=bus)
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    cevap = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False,
                                           token=prova["data"]["token"], allow_send=True)
    assert cevap["ok"] is True


@pytest.mark.asyncio
async def test_gonderimde_ag_koparsa_iz_kalir() -> None:
    # Toplu duyuru KUYRUĞA ALINMAZ, akış hâlinde gider. İstek yarıda kesilirse
    # bazı müşteriler mesajı almış olabilir ve bunu bilen tek satır o izdir.
    api = FakeApi()
    service, _, store, bus = kur(api)
    prova = await service.run_announcement(confirm_recipients=0, reason=GEREKCE,
                                           actor=AKTOR, dry_run=True, token="",
                                           allow_send=True)
    api.fail = {"run_sms_announcement"}
    api.fail_after = 1

    cevap = await service.run_announcement(confirm_recipients=186, reason=GEREKCE,
                                           actor=AKTOR, dry_run=False,
                                           token=prova["data"]["token"], allow_send=True)
    assert cevap["ok"] is False
    assert store.results("announcement_run") == ["denendi", "hata"]
    assert bus.names() == []


@pytest.mark.asyncio
async def test_bekleyen_prova_duyuru_okumasinda_gorunur() -> None:
    service, _, _, _ = kur()
    await service.run_announcement(confirm_recipients=0, reason=GEREKCE, actor=AKTOR,
                                   dry_run=True, token="", allow_send=True)
    cevap = await service.announcement()
    assert cevap["dry_run"]["recipients"] == 186
