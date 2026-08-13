"""Bildirimler servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_notifications_backend import messaging
from store_notifications_backend.service import (
    REPORT_PAGE_CAP,
    REPORT_PAGE_SIZE,
    REPORT_ROW_CAP,
    NotificationsService,
    NotifySender,
)
from store_notifications_fakes import FakeApi, FakeLog, FakeNotify, FakeStore

GEREKCE = "Kargo şablonundaki telefon numarası yanlıştı."


def _service(**config: Any) -> tuple[NotificationsService, FakeApi, FakeStore]:
    api = FakeApi()
    store = FakeStore()
    service = NotificationsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "page_size": 100,
                "quiet_hours_enabled": False, "daily_send_limit": 0, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_gecmis_ekrani_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_notifications")
    result = await service.history()
    assert result["ok"] is True          # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_kural_ucu_yayinda_degilse_ekran_durumu_anlatir() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_notification_rules")
    result = await service.rules()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []


async def test_kanallar_parca_parca_hata_alsa_da_dolar() -> None:
    service, api, _ = _service()
    api.fail.add("configuration")
    api.fail.add("bbd_mobile_settings")
    result = await service.channels()
    assert result["ok"] is True
    assert result["storeAvailable"] is False
    assert result["mobileAvailable"] is False
    # Yerel disiplin her koşulda okunur: mağaza düşse de sessiz saat çalışır.
    assert result["local"]["quietStart"] == "22:00"


# ================================================= sunucu tarafı sayfalama

async def test_gecmis_sunucudan_sayfali_gelir_tam_liste_cekilmez() -> None:
    service, api, _ = _service()
    api.history_payload = {"items": [{"id": 1, "channel": "sms", "status": "sent"}],
                           "meta": {"total": 51_320, "currentPage": 4, "perPage": 100,
                                    "lastPage": 514}}
    result = await service.history(page=4)
    assert result["total"] == 51_320
    assert result["pages"] == 514
    assert api.used("bbd_notifications")[0]["page"] == 4
    assert api.used("bbd_notifications")[0]["per_page"] == 100


async def test_maliyet_suzgeci_kurustan_ondaliga_cevrilir() -> None:
    # Ekran kuruş taşır, mağaza ondalık bekler; çeviri tek yerde durmalı.
    service, api, _ = _service()
    await service.history(cost_min=135, cost_max=1000)
    filters = api.args("bbd_notifications")[0][0]
    assert filters["cost_from"] == "1.35"
    assert filters["cost_to"] == "10.00"


async def test_kanitlanamayan_kanal_suzgeci_gonderilmez() -> None:
    # Siparişler ekranında canlıda görüldü: `channel=default` hata vermeden
    # SIFIR kayıt döndürüyor. Canlıda tek kanal var (id 1 · code "default");
    # uygulandığı kanıtlanamayan bir süzgeç listeyi sessizce boşaltma riskidir.
    # `channel` ayarı yalnız mağaza ayarı okurken kullanılır.
    service, api, _ = _service(channel="default")
    await service.history()
    filters = api.args("bbd_notifications")[0][0]
    assert "channel_code" not in filters
    assert "channel" not in filters
    await service.channels()
    assert api.used("configuration")[0]["channel"] == "default"


async def test_bilinmeyen_durum_suzgeci_mağazaya_gonderilmez() -> None:
    service, api, _ = _service()
    await service.history(status="uydurma", event="uydurma.olay")
    filters = api.args("bbd_notifications")[0][0]
    assert "status" not in filters
    assert "event" not in filters


# ===================================================== şablon düzenleyici

async def test_onizleme_ornek_veriyle_dolar_ve_aga_cikmaz() -> None:
    service, api, _ = _service()
    result = service.preview_template(channel="sms",
                                      body="Sayın {{musteri_adi}}, {{siparis_no}} kargolandı.")
    assert "{{" not in result["body"]
    assert result["missing"] == []
    assert result["plan"]["parts"] >= 1
    assert api.calls == []          # önizleme hiçbir yere gitmez


async def test_eposta_sablonu_magazaya_sms_sablonu_yerele_yazilir() -> None:
    # `subject` VERİLMEZ: mağazanın şablon kaynağında böyle bir alan yok
    # (canlı şema: name · status · content). Testin eski hâli konu gönderip
    # kabul bekliyordu; o gövde canlıda sessizce düşerdi.
    service, api, store = _service()
    await service.save_template(name="Kargo bilgisi", channel="email",
                                body="Merhaba", reason=GEREKCE, actor="Kemal", dry_run=False)
    await service.save_template(name="Kargo SMS", channel="sms", body="Kargoda",
                                reason=GEREKCE, actor="Kemal", dry_run=False)
    assert len(api.used("save_email_template")) == 1
    assert len(store.templates) == 1
    assert store.templates[0]["channel"] == "sms"


async def test_magazaya_giden_sablon_govdesi_canli_semaya_uyar() -> None:
    # CANLI ŞEMA (`/api/admin/docs` → AdminMarketingTemplate): yalnız
    # name · status · content. `status` KELİMEDİR; `1`/`0` göndermek 422
    # döndürür ve ekran "kaydedildi" derken şablon değişmemiş olurdu.
    # `subject` alanı yoktur; göndermek Laravel'in sessizce yuttuğu bir alandır.
    service, api, _ = _service()
    await service.save_template(name="Kargo bilgisi", channel="email", body="Merhaba",
                                active=True, reason=GEREKCE, actor="Kemal", dry_run=False)
    payload = api.used("save_email_template")[0]["payload"]
    assert payload == {"name": "Kargo bilgisi", "content": "Merhaba", "status": "active"}


async def test_magaza_sablonu_pasiflestirilirken_durum_kelimesi_gider() -> None:
    service, api, _ = _service()
    await service.deactivate_template("store:12", reason=GEREKCE, actor="Kemal", dry_run=False)
    call = api.used("save_email_template")[0]
    assert call["payload"] == {"status": "inactive"}
    assert call["template_id"] == 12


async def test_yerel_sablon_epostaya_cevrilemez() -> None:
    service, _, _ = _service()
    result = await service.save_template(template="local:1", name="Eski şablon", channel="email",
                                         subject="Konu", body="Metin", reason=GEREKCE,
                                         actor="Kemal")
    assert result["ok"] is False
    assert "mağazada" in result["error"]


async def test_sablon_gerekcesiz_kaydedilemez() -> None:
    service, api, _ = _service()
    result = await service.save_template(name="Kargo", channel="sms", body="Metin",
                                         reason="ok", actor="Kemal")
    assert result["ok"] is False
    assert api.calls == []


async def test_sablon_silinmez_pasiflestirilir() -> None:
    service, _, store = _service()
    await service.save_template(name="Eski SMS", channel="sms", body="Metin", reason=GEREKCE,
                                actor="Kemal")
    result = await service.deactivate_template("local:1", reason=GEREKCE, actor="Kemal")
    assert result["ok"] is True
    assert store.templates[0]["active"] == 0
    assert len(store.templates) == 1          # satır duruyor


async def test_sablon_listesi_iki_kaynagi_kimlikle_ayirir() -> None:
    service, api, _ = _service()
    api.templates_payload = {"items": [{"id": 12, "name": "Sipariş", "subject": "S",
                                        "content": "Merhaba"}]}
    await service.save_template(name="SMS", channel="sms", body="Metin", reason=GEREKCE,
                                actor="Kemal")
    result = await service.templates()
    ids = {item["id"] for item in result["items"]}
    assert ids == {"store:12", "local:1"}


# ============================================================ kendime test

async def test_kendime_test_tek_aliciya_gider_ve_gercekten_gonderilir() -> None:
    service, api, _ = _service()
    result = await service.test_send(channel="sms", to="5321234567",
                                     body="Sayın {{musteri_adi}}, deneme.", actor="Kemal")
    assert result["ok"] is True
    call = api.used("bbd_send_notification")[0]
    assert call["payload"]["to"] == ["5321234567"]
    assert call["dry_run"] is False           # test gerçek gönderimdir
    assert "{{" not in call["payload"]["body"]


async def test_kendime_test_gunluk_limiti_tuketmez() -> None:
    service, _, _ = _service(daily_send_limit=10)
    await service.test_send(channel="sms", to="5321234567", body="deneme", actor="Kemal")
    assert await service._sent_today() == 0


# ========================================================= toplu gönderim

async def test_toplu_gonderim_once_kuru_prova_ister() -> None:
    service, api, _ = _service()
    result = await service.broadcast_send(token="yokboyle", reason=GEREKCE, actor="Kemal")
    assert result["ok"] is False
    assert "kuru prova" in result["error"].lower()
    assert api.calls == []


async def test_kuru_prova_alici_sayisini_ve_maliyeti_verir() -> None:
    service, api, store = _service(sms_price_kurus=25)
    api.send_payload = {"recipients": 1834}
    result = await service.broadcast_preview(channel="sms", body="Yeni kitaplar geldi.",
                                             actor="Kemal")
    assert result["ok"] is True
    assert result["recipients"] == 1834
    assert result["plan"]["credits"] == 1834
    assert result["plan"]["cost"] == 1834 * 25
    assert api.used("bbd_send_notification")[0]["dry_run"] is True
    assert store.sends[result["token"]]["status"] == "preview"


async def test_bilinmeyen_kitle_kuru_provaya_bile_girmez() -> None:
    # EN SESSİZ FELAKET. `audience` serbest metin olarak mağazaya geçiyordu;
    # Laravel tanımadığı alanı yok sayar, yani yazım hatalı bir kitle adı
    # süzgeci düşürür ve iş 1.800+ kişiye giden süzgeçsiz bir gönderim olurdu.
    # Kapı mağazaya İSTEK ÇIKMADAN kapanmalı.
    service, api, store = _service()
    api.send_payload = {"recipients": 12_000}
    result = await service.broadcast_preview(channel="email", body="Merhaba",
                                             audience="herkes", actor="Kemal")
    assert result["ok"] is False
    assert "kitle" in result["error"].lower()
    assert api.calls == []            # mağazaya hiç sorulmadı
    assert store.sends == {}          # jeton da üretilmedi


async def test_taninan_kitle_kuru_provayi_gecer() -> None:
    service, api, _ = _service()
    api.send_payload = {"recipients": 40}
    result = await service.broadcast_preview(channel="email", body="Merhaba",
                                             audience="recent_buyers", actor="Kemal")
    assert result["ok"] is True
    assert api.used("bbd_send_notification")[0]["payload"]["audience"] == "recent_buyers"


async def test_katalog_kitleleri_de_verir_panel_kendi_kopyasini_tutmasin() -> None:
    # Panel kendi listesini tutarsa backend'in kabul ettiği kümeyle ayrışır:
    # ekran kitleyi seçtirir, kuru prova "bilinmeyen kitle" der.
    service, _, _ = _service()
    catalog = service.catalog()
    assert [item["key"] for item in catalog["audiences"]] == list(messaging.AUDIENCES)
    assert all(item["label"] for item in catalog["audiences"])


async def test_alicisi_olmayan_gonderim_baslatilmaz() -> None:
    service, api, _ = _service()
    api.send_payload = {"recipients": 0}
    result = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    assert result["ok"] is False
    assert "alıcı bulunamadı" in result["error"]


async def test_tavani_asan_secim_reddedilir() -> None:
    service, api, _ = _service(bulk_recipient_cap=1000)
    api.send_payload = {"recipients": 12_000}
    result = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    assert result["ok"] is False
    assert "1000" in result["error"]


async def test_jeton_bir_kez_kullanilir() -> None:
    service, api, _ = _service()
    api.send_payload = {"recipients": 5}
    preview = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    first = await service.broadcast_send(token=preview["token"], reason=GEREKCE, actor="Kemal",
                                         dry_run=False)
    second = await service.broadcast_send(token=preview["token"], reason=GEREKCE, actor="Kemal",
                                          dry_run=False)
    assert first["ok"] is True
    assert second["ok"] is False
    assert "zaten kullanıldı" in second["error"]


async def test_bozuk_onizleme_govdesi_gonderimi_acmaz() -> None:
    # Jetonun tek işi "uygulanan iş, önizlenen iştir" demektir. Gövde
    # çözülemiyorsa o kanıt yoktur: istisna fırlatıp 500 dönmek yerine
    # gönderim reddedilir ve kuru prova yeniden istenir (K7).
    service, api, store = _service()
    api.send_payload = {"recipients": 5}
    preview = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    store.sends[preview["token"]]["params"] = "{bozuk-json"
    result = await service.broadcast_send(token=preview["token"], reason=GEREKCE, actor="Kemal",
                                          dry_run=False)
    assert result["ok"] is False
    assert "kuru prova" in result["error"].lower()
    assert len(api.used("bbd_send_notification")) == 1      # yalnız önizleme


async def test_gonderim_yapildiktan_sonra_jeton_kapatilamazsa_hata_donmez() -> None:
    # Mesaj GİTTİ; jeton satırı güncellenemedi. İstisnayı dışarı bırakmak 500
    # üretir, kullanıcı gönderimin gitmediğini sanıp yeniden dener ve 1.800
    # kişiye İKİNCİ kez gider. Sonuç başarıyla döner, iz kayda geçer.
    class KapanmayanStore(FakeStore):
        async def execute(self, sql: str, params: Any = ()) -> None:
            if "_sends" in sql and sql.strip().startswith("UPDATE"):
                raise RuntimeError("satır kilitli")
            await super().execute(sql, params)

    api, store = FakeApi(), KapanmayanStore()
    api.send_payload = {"recipients": 3}
    service = NotificationsService(api=api, store=store, log=FakeLog(),
                                   config={"quiet_hours_enabled": False},
                                   fallback_dir=Path("/tmp/km-test-raporlar"))
    preview = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    result = await service.broadcast_send(token=preview["token"], reason=GEREKCE, actor="Kemal",
                                          dry_run=False)
    assert result["ok"] is True
    assert result["recipients"] == 3
    assert any(row["result"] == "jeton_kapatilamadi" for row in store.audit)


async def test_yerel_yazma_patlarsa_ayar_ucu_500_dondurmez() -> None:
    class KirikStore(FakeStore):
        async def execute(self, sql: str, params: Any = ()) -> None:
            if "_prefs" in sql:
                raise RuntimeError("disk dolu")
            await super().execute(sql, params)

    service = NotificationsService(api=FakeApi(), store=KirikStore(), log=FakeLog(), config={},
                                   fallback_dir=Path("/tmp/km-test-raporlar"))
    result = await service.save_channels(quiet_start="21:30", reason=GEREKCE, actor="Kemal",
                                         dry_run=False)
    assert result["ok"] is False
    assert "disk dolu" in result["error"]


async def test_uygulanan_is_onizlenen_istir_yeniden_hesaplanmaz() -> None:
    service, api, _ = _service()
    api.send_payload = {"recipients": 7}
    preview = await service.broadcast_preview(channel="email", subject="Duyuru",
                                              body="Merhaba", actor="Kemal")
    api.send_payload = {"recipients": 99_999}     # arada süzgeç değişti
    result = await service.broadcast_send(token=preview["token"], reason=GEREKCE,
                                          actor="Kemal", dry_run=False)
    assert result["recipients"] == 7
    gonderilen = json.loads(json.dumps(api.used("bbd_send_notification")[1]["payload"]))
    assert gonderilen["subject"] == "Duyuru"


async def test_toplu_gonderim_gerekcesiz_yapilamaz() -> None:
    service, api, _ = _service()
    api.send_payload = {"recipients": 5}
    preview = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    result = await service.broadcast_send(token=preview["token"], reason="acil",
                                          actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert len(api.used("bbd_send_notification")) == 1   # yalnız kuru prova


async def test_sessiz_saatte_toplu_gonderim_durur() -> None:
    service, api, _ = _service(quiet_hours_enabled=True, quiet_hours_start="00:00",
                               quiet_hours_end="23:59")
    api.send_payload = {"recipients": 1834}
    preview = await service.broadcast_preview(channel="sms", body="Duyuru", actor="Kemal")
    assert preview["blocked"] is True
    result = await service.broadcast_send(token=preview["token"], reason=GEREKCE,
                                          actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "Sessiz saatler" in result["error"]


async def test_sessiz_saatte_kuru_prova_yine_de_calisir() -> None:
    # Prova mesaj göndermez; gece de alıcı sayısı sorulabilmeli.
    service, api, _ = _service(quiet_hours_enabled=True, quiet_hours_start="00:00",
                               quiet_hours_end="23:59")
    api.send_payload = {"recipients": 12}
    preview = await service.broadcast_preview(channel="sms", body="Duyuru", actor="Kemal")
    result = await service.broadcast_send(token=preview["token"], reason=GEREKCE,
                                          actor="Kemal", dry_run=True)
    assert result["ok"] is True
    assert result["dryRun"] is True


async def test_gunluk_limit_asilirsa_gonderim_reddedilir() -> None:
    service, api, _ = _service(daily_send_limit=10)
    api.send_payload = {"recipients": 8}
    first = await service.broadcast_preview(channel="email", body="A", actor="Kemal")
    await service.broadcast_send(token=first["token"], reason=GEREKCE, actor="Kemal",
                                 dry_run=False)
    second = await service.broadcast_preview(channel="email", body="B", actor="Kemal")
    result = await service.broadcast_send(token=second["token"], reason=GEREKCE, actor="Kemal",
                                          dry_run=False)
    assert result["ok"] is False
    assert "limiti" in result["error"]


async def test_gercek_gonderim_olay_yayinlar_kuru_prova_yayinlamaz() -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def publish(name: str, payload: dict[str, Any]) -> None:
        events.append((name, payload))

    api, store = FakeApi(), FakeStore()
    api.send_payload = {"recipients": 3}
    service = NotificationsService(api=api, store=store, log=FakeLog(),
                                   config={"quiet_hours_enabled": False}, publish=publish,
                                   fallback_dir=Path("/tmp/km-test-raporlar"))
    preview = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    await service.broadcast_send(token=preview["token"], reason=GEREKCE, actor="Kemal",
                                 dry_run=True)
    assert events == []
    preview = await service.broadcast_preview(channel="email", body="Merhaba", actor="Kemal")
    await service.broadcast_send(token=preview["token"], reason=GEREKCE, actor="Kemal",
                                 dry_run=False)
    assert events[0][0] == "store.notification.sent"


# ================================================ store.notify.send yeteneği

async def test_yetenek_cagrisi_da_gerekce_ister() -> None:
    service, api, _ = _service()
    sender = NotifySender(service)
    result = await sender.send(template="store:1", recipients=["a@b.com"], reason="ok",
                               actor="Siparişler")
    assert result["ok"] is False
    assert api.calls == []


async def test_yetenek_cagrisi_varsayilan_kuru_provadir() -> None:
    service, api, _ = _service()
    sender = NotifySender(service)
    result = await sender.send(template="store:1", recipients=["a@b.com"], reason=GEREKCE,
                               actor="Siparişler")
    assert result["dryRun"] is True
    assert api.used("bbd_send_notification")[0]["dry_run"] is True


async def test_yetenek_cagrisi_sessiz_saate_takilir() -> None:
    # "Siparişler ekranından gönderildi" diye gece 03:00'te müşteri uyanmamalı.
    service, api, _ = _service(quiet_hours_enabled=True, quiet_hours_start="00:00",
                               quiet_hours_end="23:59")
    sender = NotifySender(service)
    result = await sender.send(template="store:1", recipients=["a@b.com"], reason=GEREKCE,
                               actor="Siparişler", dry_run=False)
    assert result["ok"] is False
    assert api.calls == []


async def test_yetenek_cagrisi_bozuk_sablon_kimligini_reddeder() -> None:
    service, _, _ = _service()
    sender = NotifySender(service)
    result = await sender.send(template="12", recipients=["a@b.com"], reason=GEREKCE,
                               actor="Siparişler")
    assert result["ok"] is False
    assert "store:12" in result["error"]


async def test_yetenek_gercek_gonderimi_gunluk_sayaca_yazar() -> None:
    service, _, _ = _service(daily_send_limit=100)
    sender = NotifySender(service)
    await sender.send(template="store:1", recipients=["a@b.com", "c@d.com"], reason=GEREKCE,
                      actor="Siparişler", dry_run=False)
    assert await service._sent_today() == 2


# ============================================================ yeniden gönder

async def test_yeniden_gonderim_gerekce_ister_ve_kuru_prova_varsayilan() -> None:
    service, api, _ = _service()
    assert (await service.resend(5, reason="ok", actor="Kemal"))["ok"] is False
    result = await service.resend(5, reason=GEREKCE, actor="Kemal")
    assert result["dryRun"] is True
    assert api.used("bbd_send_notification")[0]["payload"] == {"resendOf": 5}


# ================================================================= kanallar

async def test_smtp_parolasi_ekrana_maskeli_gider() -> None:
    service, api, _ = _service()
    api.config_payload = {"values": {
        "emails.configure.email_settings.host": "mail.bbdstore.com.tr",
        "emails.configure.email_settings.password": "cok-gizli-parola",
    }}
    result = await service.channels()
    assert result["store"]["host"]["value"] == "mail.bbdstore.com.tr"
    assert result["store"]["password"]["value"] is None
    assert result["store"]["password"]["masked"].endswith("rola")
    assert "cok-gizli" not in json.dumps(result, ensure_ascii=False)


async def test_smtp_anahtarlari_canli_yollardan_okunur() -> None:
    # CANLIDA DOĞRULANDI (GET /api/admin/configuration?slug=emails): bağlantı
    # bilgisi `configure.smtp.*`, gönderen kimliği `configure.email_settings.*`
    # altında — İKİ AYRI GRUP. Hepsini `email_settings` altında arayan eski
    # kod gönderen adresini "anahtar bulunamadı" gösteriyordu.
    service, api, _ = _service()
    api.config_payload = {"values": {
        "emails.configure.smtp.host": "smtp.resend.com",
        "emails.configure.smtp.port": "587",
        "emails.configure.email_settings.sender_email": "noreply@bbdstore.com.tr",
        "emails.configure.email_settings.sender_name": "Benim Başarı Dünyam",
    }}
    result = await service.channels()
    assert result["store"]["host"]["value"] == "smtp.resend.com"
    assert result["store"]["sender_email"]["found"] is True
    assert result["store"]["sender_email"]["value"] == "noreply@bbdstore.com.tr"
    assert result["store"]["sender_name"]["value"] == "Benim Başarı Dünyam"


async def test_sessiz_saat_bozuk_bicimde_kaydedilmez() -> None:
    service, _, store = _service()
    result = await service.save_channels(quiet_start="25:99", reason=GEREKCE, actor="Kemal")
    assert result["ok"] is False
    assert store.prefs == {}


async def test_sessiz_saat_ve_limit_yerel_tercihe_yazilir() -> None:
    service, _, store = _service()
    result = await service.save_channels(quiet_start="21:30", quiet_end="07:45",
                                         daily_limit=500, reason=GEREKCE, actor="Kemal",
                                         dry_run=False)
    assert result["ok"] is True
    assert store.prefs["quiet_start"] == "21:30"
    assert store.prefs["daily_limit"] == "500"


async def test_yerel_tercih_modul_ayarini_ezer() -> None:
    service, _, _ = _service(quiet_hours_start="22:00")
    await service.save_channels(quiet_start="19:00", reason=GEREKCE, actor="Kemal")
    channels = await service.channels()
    assert channels["local"]["quietStart"] == "19:00"


async def test_baglanti_sinamasi_gercek_mesaj_gondermez() -> None:
    service, api, _ = _service()
    result = await service.test_connection("store")
    assert result["ready"] is True
    assert api.used("bbd_send_notification") == []


async def test_sms_yetenegi_yoksa_ekran_soyler() -> None:
    service, _, _ = _service()
    result = await service.test_connection("sms")
    assert result["ready"] is False
    assert "notify" in result["message"]


async def test_sms_katmani_kuru_provadaysa_uyarilir() -> None:
    api, store = FakeApi(), FakeStore()
    service = NotificationsService(api=api, store=store, log=FakeLog(), config={},
                                   notify=FakeNotify(dryRun=True),
                                   fallback_dir=Path("/tmp/km-test-raporlar"))
    result = await service.test_connection("sms")
    assert result["ready"] is True
    assert "KURU PROVA" in result["message"]


# ============================================================== abonelikler

async def test_abone_listesi_sayfali_gelir() -> None:
    service, api, _ = _service()
    api.subscribers_payload = {"items": [{"id": 3, "email": "a@b.com", "is_subscribed": 1}],
                               "meta": {"total": 1834, "currentPage": 2, "lastPage": 19}}
    result = await service.subscribers(page=2)
    assert result["total"] == 1834
    assert result["items"][0]["subscribed"] is True


# ================================================================= denetim

async def test_her_yazma_gerekcesiyle_yerel_ize_yazilir() -> None:
    service, _, store = _service()
    await service.save_template(name="SMS", channel="sms", body="Metin", reason=GEREKCE,
                                actor="Kemal")
    actions = {row["action"] for row in store.audit}
    assert "save_local_template" in actions
    assert all(row["reason"] == GEREKCE for row in store.audit)


async def test_basarisiz_yazma_da_ize_gecer() -> None:
    service, api, store = _service()
    api.fail.add("bbd_save_notification_rule")
    await service.save_rule(rule_id=None, event="order.shipped", channel="sms",
                            template="local:1", reason=GEREKCE, actor="Kemal")
    assert any(row["result"] == "hata" for row in store.audit)


# =================================================================== rapor

async def test_rapor_yol_dogrulamasi_disaridaki_dosyayi_basmaz() -> None:
    class FakePrinter:
        async def print_file(self, path: Any, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("bu dosya basılmamalıydı")

    api, store = FakeApi(), FakeStore()
    service = NotificationsService(api=api, store=store, log=FakeLog(), config={},
                                   printer=FakePrinter(),
                                   fallback_dir=Path("/tmp/km-test-raporlar"))
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]


async def test_bilinmeyen_rapor_turu_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.build_report("uydurma", {})
    assert result["ok"] is False


async def test_csv_disa_aktarimi_da_taninmayan_suzgeci_atar() -> None:
    # Liste ucu süzgeci tanınan kümeye karşı süzüyordu, CSV ucu ise route'un
    # kurduğu ham sözlüğü olduğu gibi mağazaya veriyordu. Laravel tanımadığı
    # parametreyi YOK SAYAR: `channel=uydurma` ile alınan dosya BÜTÜN kanalları
    # içerir ama kullanıcı onu süzülmüş sanır.
    service, api, _ = _service()
    api.history_payload = {"items": [{"id": 1, "channel": "sms", "status": "sent"}],
                           "meta": {"lastPage": 1, "currentPage": 1}}
    await service.export_csv(channel="uydurma", status="uydurma", date_from="2026-08-01")
    filters = api.args("bbd_notifications")[0][0]
    assert "channel" not in filters
    assert "status" not in filters
    assert filters["date_from"] == "2026-08-01"


async def test_csv_disa_aktariminda_gecerli_suzgec_korunur() -> None:
    service, api, _ = _service()
    api.history_payload = {"items": [{"id": 1, "channel": "sms", "status": "failed"}],
                           "meta": {"lastPage": 1, "currentPage": 1}}
    await service.export_csv(channel="sms", status="failed")
    filters = api.args("bbd_notifications")[0][0]
    assert filters["channel"] == "sms"
    assert filters["status"] == "failed"


async def test_rapor_tavani_sunucunun_kirptigi_sayfa_boyuyla_tutarli() -> None:
    # Sunucu `per_page`'i 50'ye KIRPAR (canlıda doğrulandı: ?per_page=200 →
    # meta.perPage 50). Eski kod 100 istiyor, 50 alıyor ve 50 sayfada duruyordu:
    # gerçek tavan 2.500 satırdı, oysa `REPORT_ROW_CAP` 5.000 diyordu — satır
    # tavanı ulaşılamayan ölü bir sabitti.
    assert REPORT_PAGE_SIZE * REPORT_PAGE_CAP == REPORT_ROW_CAP

    service, api, _ = _service()
    page = {"items": [{"id": n, "channel": "sms", "status": "sent"}
                      for n in range(REPORT_PAGE_SIZE)],
            "meta": {"lastPage": 10_000, "currentPage": 1}}
    api.history_pages = [page] * (REPORT_PAGE_CAP + 5)
    rows, truncated = await service._scan({})
    assert len(rows) == REPORT_ROW_CAP
    assert truncated is True
    # Sunucunun zaten kırpacağı 100 yerine gerçek boy istenir.
    assert api.used("bbd_notifications")[0]["per_page"] == REPORT_PAGE_SIZE


async def test_rapor_taramasi_sirayla_sayfalanir_ve_tavanlanir() -> None:
    service, api, _ = _service()
    api.history_pages = [
        {"items": [{"id": 1, "channel": "sms", "status": "delivered", "cost": "0.25"}],
         "meta": {"lastPage": 2, "currentPage": 1}},
        {"items": [{"id": 2, "channel": "sms", "status": "failed"}],
         "meta": {"lastPage": 2, "currentPage": 2}},
    ]
    rows, truncated = await service._scan({})
    assert [row["id"] for row in rows] == [1, 2]
    assert truncated is False
    assert [call["page"] for call in api.used("bbd_notifications")] == [1, 2]
