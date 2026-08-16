"""Bildirim metninin saf mantığı — ağa çıkmaz, DB kullanmaz.

Test adları tuzağı söyler: her biri `backend/messaging.py` başındaki yedi
tuzaktan birinin karşılığıdır.
"""

from __future__ import annotations

from store_notifications_backend import messaging

# ============================================ TUZAK 1 — bilinmeyen değişken

def test_bilinmeyen_degisken_bosa_cevrilmez_yerinde_kalir() -> None:
    # Boşa çevirmek 1.800 kişiye "Sayın , siparişiniz kargolandı" göndermektir.
    result = messaging.render("Sayın {{musteri_adi}}, {{siparis_no}} kargolandı.",
                              {"siparis_no": "SP-1"})
    assert result["text"] == "Sayın {{musteri_adi}}, SP-1 kargolandı."
    assert result["missing"] == ["musteri_adi"]


def test_paletle_tanimsiz_degisken_ayrica_bildirilir() -> None:
    result = messaging.render("{{uydurma_alan}} · {{musteri_adi}}", {"musteri_adi": "Ayşe"})
    assert result["unknown"] == ["uydurma_alan"]
    assert "{{uydurma_alan}}" in result["text"]


def test_bosluklu_yazim_da_cozulur() -> None:
    assert messaging.render("{{ musteri_adi }}", {"musteri_adi": "Ayşe"})["text"] == "Ayşe"


def test_bos_degerli_degisken_eksik_sayilir() -> None:
    # "" göndermek, alanın dolduğu yanılgısını üretir.
    result = messaging.render("Sayın {{musteri_adi}}", {"musteri_adi": "   "})
    assert result["missing"] == ["musteri_adi"]


# ================================================ TUZAK 2 — SMS maliyeti

def test_turkce_harf_sms_parcasini_buyutur_ve_karakterler_gosterilir() -> None:
    plan = messaging.sms_plan("şğı" * 40)
    assert plan["parts"] >= 2
    assert set(plan["offending"]) == {"ş", "ğ", "ı"}
    assert plan["encoding"] == "tr"


def test_sadelestirilmis_metin_onerilir_ama_uygulanmaz() -> None:
    plan = messaging.sms_plan("Ödemeniz için teşekkürler")
    assert plan["simplified"] == "Odemeniz icin tesekkurler"
    assert plan["simplifiedParts"] <= plan["parts"]


def test_kredi_alici_sayisiyla_carpilir() -> None:
    plan = messaging.sms_plan("Merhaba", recipients=1800, price_kurus=25)
    assert plan["parts"] == 1
    assert plan["credits"] == 1800
    assert plan["cost"] == 1800 * 25


# ======================================== TUZAK 3 — örnek veride para birimi

def test_ornek_tutar_gsm7_disina_cikmaz() -> None:
    # `₺` tek başına mesajı UCS-2'ye düşürür (160 → 70). Önizleme gerçeği
    # temsil etmeli; örnek veride para birimi `TL` yazılır.
    plan = messaging.sms_plan(messaging.sample_values()["tutar"])
    assert plan["unicode"] is False
    assert plan["offending"] == []


def test_emoji_mesaji_ucs2ye_dusurur() -> None:
    plan = messaging.sms_plan("Siparişiniz hazır 🎉")
    assert plan["unicode"] is True
    assert plan["capacity"] == 70


# =========================================== TUZAK 4/5 — sessiz saatler

def test_gece_yarisini_asan_pencere_dogru_calisir() -> None:
    inside = messaging.quiet_state("23:30", start="22:00", end="08:00")
    outside = messaging.quiet_state("12:00", start="22:00", end="08:00")
    early = messaging.quiet_state("03:00", start="22:00", end="08:00")
    assert inside["active"] is True
    assert early["active"] is True
    assert outside["active"] is False


def test_gunduz_penceresi_dogru_calisir() -> None:
    assert messaging.quiet_state("10:00", start="09:00", end="17:00")["active"] is True
    assert messaging.quiet_state("18:00", start="09:00", end="17:00")["active"] is False


def test_baslangic_bitis_ayniysa_pencere_uygulanmaz_ve_soylenir() -> None:
    # Uygulansaydı 24 saatlik pencere olur ve tek bildirim bile çıkmazdı.
    state = messaging.quiet_state("13:00", start="09:00", end="09:00")
    assert state["active"] is False
    assert "aynı" in state["error"]


def test_bozuk_saat_bicimi_sessizce_yok_sayilmaz() -> None:
    state = messaging.quiet_state("13:00", start="9", end="akşam")
    assert state["active"] is False
    assert state["error"]


def test_pencere_kapaliysa_hicbir_zaman_engellemez() -> None:
    state = messaging.quiet_state("23:30", start="22:00", end="08:00", enabled=False)
    assert state["active"] is False
    assert state["error"] == ""


# ============================================ TUZAK 6 — şablon kimliği

def test_sablon_kimligi_kaynagi_tasir() -> None:
    assert messaging.template_id("store", 12) == "store:12"
    assert messaging.split_template_id("local:3") == ("local", 3)


def test_ciplak_sayi_sablon_kimligi_reddedilir() -> None:
    # Mağazadaki 12 numaralı e-posta şablonu ile yereldeki 12 numaralı SMS
    # şablonu aynı ekranda yan yana durur; çıplak sayı ikisini karıştırır.
    assert messaging.split_template_id("12") == ("", 0)
    assert messaging.split_template_id("baska:1") == ("", 0)


# ============================================== TUZAK 7 — bilinmeyen maliyet

def test_birim_fiyat_girilmemisse_maliyet_sifir_degil_bilinmiyor() -> None:
    plan = messaging.sms_plan("Merhaba", recipients=10, price_kurus=0)
    assert plan["cost"] is None


def test_maliyeti_bilinmeyen_satir_toplama_girmez() -> None:
    rows = [messaging.history_row({"id": 1, "cost": "0.25", "status": "delivered"}),
            messaging.history_row({"id": 2, "status": "delivered"})]
    summary = messaging.history_summary(rows)
    assert summary["cost"] == 25
    assert summary["costUnknown"] == 1


# ================================================================ doğrulama

def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert messaging.reason_error("kısa")
    assert messaging.reason_error("Müşteri iade talebi nedeniyle.") == ""


def test_bilinmeyen_kanal_reddedilir() -> None:
    assert messaging.channel_error("telepati")
    assert messaging.channel_error("sms") == ""


def test_bilinmeyen_alici_kitlesi_reddedilir() -> None:
    # `AUDIENCES` sabiti bu doğrulama için yazılmıştı ama HİÇBİR YERDEN
    # ÇAĞRILMIYORDU. Laravel tanımadığı alanı sessizce yok sayar (canlıda
    # kanıtlandı: `?uydurma_suzgec=xyz` → 5 kayıt, hata yok); yazım hatalı bir
    # kitle adı süzgeci düşürür ve iş HERKESE giden bir gönderime dönüşürdü.
    assert messaging.audience_error("herkes")
    assert messaging.audience_error("")
    assert messaging.audience_error("subscribers") == ""
    assert messaging.audience_error("recent_buyers") == ""


def test_kitle_hata_metni_secilebilenleri_sayar() -> None:
    problem = messaging.audience_error("uydurma")
    for label in messaging.AUDIENCE_LABELS.values():
        assert label in problem


def test_sms_sablonunda_konu_alani_kabul_edilmez() -> None:
    problem = messaging.template_problem(name="Kargo", channel="sms", subject="Konu",
                                         body="Metin")
    assert "konu" in problem.lower()


def test_eposta_sablonunda_konu_alani_yoktur_bos_konu_dogru_haldir() -> None:
    """TESTİN ESKİ HÂLİ YANLIŞTI ("konu zorunlu") — canlı şema tersini söylüyor.

    `GET /api/admin/docs` → `AdminMarketingTemplate` (2026-08-13): mağazanın
    e-posta şablonu yalnız `name` · `status` · `content` alanlarını tanıyor,
    POST gövdesinde bu üçü zorunlu ve `subject` diye bir alan YOK. Konuyu
    zorunlu tutmak, kullanıcıyı mağazaya HİÇ ULAŞMAYACAK bir alanı doldurmaya
    zorlamak olurdu: Laravel tanımadığı alanı sessizce yok sayar, personel
    yazdığı konunun gittiğini sanırdı. Doğru davranış: boş konu geçerli,
    DOLU konu reddedilir.
    """
    assert messaging.template_problem(name="Kargo", channel="email", subject="",
                                      body="Metin") == ""
    problem = messaging.template_problem(name="Kargo", channel="email", subject="Konu",
                                         body="Metin")
    assert "konu" in problem.lower()


def test_kural_bilinmeyen_olaya_baglanamaz() -> None:
    assert messaging.rule_problem(event="uydurma.olay", channel="sms",
                                  template="local:1", delay_minutes=0)


def test_kural_sablonsuz_kaydedilemez() -> None:
    assert messaging.rule_problem(event="order.shipped", channel="sms", template="",
                                  delay_minutes=0)


def test_gecerli_kural_sorunsuz() -> None:
    assert messaging.rule_problem(event="order.shipped", channel="sms", template="local:1",
                                  delay_minutes=30) == ""


# ==================================================================== limit

def test_gunluk_limit_asilirsa_gerekce_metni_doner() -> None:
    state = messaging.limit_state(1900, 2000, wanted=250)
    assert state["remaining"] == 100
    assert "kalan 100" in state["error"]


def test_limit_sifirsa_sinir_yok() -> None:
    state = messaging.limit_state(9999, 0, wanted=5000)
    assert state["limited"] is False
    assert state["error"] == ""


# ====================================================== sır ve ayar okuma

def test_sir_maskeli_doner_ham_deger_yanitta_bulunmaz() -> None:
    entry = messaging.config_entry({"emails.configure.email_settings.password": "gizli-parola"},
                                   "emails.configure.email_settings.password")
    assert entry["found"] is True
    assert entry["value"] is None
    assert entry["masked"].endswith("rola")
    assert "gizli" not in entry["masked"]


def test_anahtar_bulunamazsa_deger_uydurulmaz() -> None:
    entry = messaging.config_entry({}, "emails.configure.email_settings.host")
    assert entry["found"] is False
    assert entry["value"] is None


def test_anahtarin_son_parcasi_esleserse_bulunur() -> None:
    entry = messaging.config_entry({"emails.smtp.host": "mail.bbdstore.com.tr"},
                                   "emails.configure.email_settings.host")
    assert entry["found"] is True
    assert entry["value"] == "mail.bbdstore.com.tr"


# ================================================================== palet

def test_palet_olaya_gore_daralir_ama_alan_gizlemez() -> None:
    palette = messaging.variable_palette("stock.critical")
    relevant = [item["key"] for item in palette if item["relevant"]]
    assert "stok_adet" in relevant
    assert "kargo_takip" not in relevant
    # Gizlenmez: kullanıcı bilerek koyabilir, ekran dolmayacağını söyler.
    assert any(item["key"] == "kargo_takip" for item in palette)


def test_palet_jetonu_imlece_eklenecek_biçimde_gelir() -> None:
    palette = messaging.variable_palette()
    assert palette[0]["token"].startswith("{{")
    assert palette[0]["token"].endswith("}}")


# =========================================== CANLI ALAN ADLARI (camelCase)

def test_abone_satiri_canli_camelcase_alanlari_okur() -> None:
    # CANLIDA DOĞRULANDI (GET /api/admin/marketing/subscribers): zarf
    # camelCase'tir. snake_case okuyan kod hiçbir şey bulmaz ve İSTİSNA DA
    # ATMAZ — ekran sessizce boş görünürdü.
    row = messaging.subscriber_row({
        "id": 5, "email": "zafersaman42@gmail.com", "isSubscribed": True,
        "customerId": 26, "customerName": "Beray Şaman", "channel": None,
        "createdAt": "2026-08-02T11:41:49+03:00",
    })
    assert row["customerId"] == 26
    assert row["customerName"] == "Beray Şaman"
    assert row["subscribed"] is True
    assert row["createdAt"] == "2026-08-02T11:41:49"


def test_abonelikten_cikmis_musteri_abone_gorunmez() -> None:
    # `isSubscribed: false` bir JSON MANTIKSALIDIR. `as_int(False, 1)` → 1
    # olurdu: ayrılmış müşteri "Abone" görünür ve toplu gönderime girerdi.
    assert messaging.subscriber_row({"id": 9, "isSubscribed": False})["subscribed"] is False
    assert messaging.subscriber_row({"id": 9, "is_subscribed": 0})["subscribed"] is False
    # Alan hiç yoksa varsayım "abone": abone listesinde duran kayıttır.
    assert messaging.subscriber_row({"id": 9})["subscribed"] is True


def test_mobil_ayarda_acik_anahtar_kapali_gorunmez() -> None:
    # `as_int(True, 0)` → 0: açık olan push bildirimi ekranda "kapalı" görünür,
    # personel açık anahtarı bir daha açmaya çalışırdı.
    row = messaging.mobile_row({"pushEnabled": True, "forceUpdate": False,
                                "androidVersion": "2.4.1", "pushKey": "abcd1234efgh5678"})
    assert row["pushEnabled"] is True
    assert row["forceUpdate"] is False
    assert row["androidVersion"] == "2.4.1"
    assert row["pushKeyMasked"].endswith("5678")
    assert "abcd" not in row["pushKeyMasked"]


def test_kapali_kural_acik_gorunmez() -> None:
    # `active: false` VARDIR ama yanlıştır; sıradaki alana düşülürse kural açık
    # görünür ve kapatıldığı sanılan bildirim gitmeye devam ederdi.
    assert messaging.rule_row({"id": 1, "active": False, "status": "active"})["active"] is False
    assert messaging.rule_row({"id": 1, "enabled": False, "status": "active"})["active"] is False
    assert messaging.rule_row({"id": 1, "status": "active"})["active"] is True


def test_canli_kural_satiri_acik_kurali_kapali_gostermez() -> None:
    """Canlı yanıt (2026-08-16) `enabled` diyor, `active`/`status` demiyor.

    Bu satır bir dönem uç 404 dönerken varsayılan bir şema üzerine yazılmıştı;
    uç yayına girince ekran ÇALIŞAN beş kuralı da "Kapalı" ve "tanınmayan
    olay" diye çiziyordu — yani kullanıcıya sistemi bozuk gösteriyordu.
    """
    row = messaging.rule_row({
        "id": "order.created.telegram",
        "title": "Yeni sipariş bildirimi (Telegram)",
        "description": "Sipariş oluştuğunda mağaza sahibine Telegram mesajı gider.",
        "event": "checkout.order.save.after",
        "channel": "telegram",
        "enabled": True,
        "blockedBy": [],
    })
    assert row["active"] is True                       # `enabled` okunur
    assert row["id"] == "order.created.telegram"       # kimlik 0'a düşmez
    assert row["eventLabel"] == "Yeni sipariş bildirimi (Telegram)"
    assert row["known"] is True                        # "tanınmayan olay" rozeti çıkmaz


def test_hicbir_tarafin_adlandirmadigi_olay_hala_isaretlenir() -> None:
    # Mağaza başlık da göndermediyse rozet YERİNDE KALIR: adı olmayan bir
    # kuralı sessizce normal göstermek, olayın ne olduğunu kimsenin
    # bilmediğini gizlerdi.
    assert messaging.rule_row({"id": 7, "event": "uydurma.olay"})["known"] is False


def test_pick_yanlis_degeri_yok_saymaz() -> None:
    assert messaging.pick({"isSubscribed": False}, "is_subscribed") is False
    assert messaging.pick({"createdAt": "x"}, "created_at") == "x"
    assert messaging.pick({}, "created_at", default="—") == "—"


# ================================================================= geçmiş

def test_gecmis_satiri_parayi_kurusa_cevirir() -> None:
    row = messaging.history_row({"id": 7, "channel": "sms", "status": "DELIVERED",
                                 "cost": "1.35", "attempts": 2})
    assert row["cost"] == 135
    assert row["statusLabel"] == "İletildi"
    assert row["failed"] is False


def test_basarisiz_durumlar_isaretlenir() -> None:
    assert messaging.history_row({"status": "failed"})["failed"] is True
    assert messaging.history_row({"status": "rejected"})["failed"] is True
