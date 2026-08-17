"""Saf yardımcılar — biçim, etiket ve ön denetim.

Bu dosya ağa çıkmaz ve servis kurmaz. İddiaların hepsi
`BLD/docs/control/subscriptions.md` içindeki kurallardır; modülün kendi
uydurduğu bir kurala karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

from bld_subscriptions_backend import subs as sb
from bld_subscriptions_fakes import (
    CALENDAR_ROWS,
    CONTRACT_ROW,
    PAYMENT_ROW,
    REQUEST_DETAIL,
    REQUEST_ROW,
    RUN_ROWS,
    SUBSCRIPTION_DETAIL,
    SUBSCRIPTION_ROW,
)

# ================================================================= gerekçe

def test_gerekce_sinirlari_500_dur_160_degil() -> None:
    # `bld_orders` 160 kullanıyor çünkü gerekçe `veykemtu_order_revisions.reason`
    # sütununa yazılıyor. Abonelik yazmaları o sütuna hiç dokunmuyor ve geçit
    # bu metotları `reason_max=MAX_REASON` (500) ile çağırıyor. 160 yazmak,
    # sunucunun KABUL EDECEĞİ bir gerekçeyi ekranın reddetmesi olurdu.
    assert sb.MIN_REASON == 10
    assert sb.MAX_REASON == 500
    assert sb.reason_error("kısa")
    assert sb.reason_error("x" * 501)
    assert sb.reason_error("x" * 500) == ""
    assert sb.reason_error("x" * 200) == ""


# ============================================================== servis günleri

def test_servis_gunleri_bos_olamaz_tekrarsiz_ve_siralanir() -> None:
    days, problem = sb.service_days([5, 1, 3])
    assert problem == ""
    assert days == [1, 3, 5]

    # Günsüz bir kural hiçbir şey üretmez.
    _, problem = sb.service_days([])
    assert "en az bir servis günü" in problem.lower()

    _, problem = sb.service_days([1, 1])
    assert "iki kez" in problem

    _, problem = sb.service_days([0])
    assert "1–7" in problem
    _, problem = sb.service_days([8])
    assert "1–7" in problem


def test_servis_gunu_etiketi_kisa_adlarla_yazilir() -> None:
    assert sb.service_days_label([1, 2, 3, 4, 5]) == "Pzt, Sal, Çar, Per, Cum"
    assert sb.service_days_label([]) == "—"


# ============================================================== dönem/duraklatma

def test_donem_araligi_62_gunu_asamaz_ve_son_odeme_gunu_geriye_alinamaz() -> None:
    assert sb.period_error("2026-08-01", "2026-08-31", "2026-09-05") == ""

    problem = sb.period_error("2026-08-01", "2026-08-01", "2026-08-05")
    assert "sonra olmalı" in problem

    problem = sb.period_error("2026-01-01", "2026-06-01", "2026-06-05")
    assert str(sb.MAX_PERIOD_DAYS) in problem

    problem = sb.period_error("2026-08-01", "2026-08-31", "2026-07-20")
    assert "önce olamaz" in problem


def test_duraklatmada_bitis_gunu_zorunlu_ve_gerekce_iptali_isaret_eder() -> None:
    # SÜRESİZ DURAKLATMA İPTALİN ADI KONMAMIŞ HÂLİDİR (sözleşme): boş `end_date`
    # kabul edilseydi yönetici aboneliği sonsuza kadar durdurup iptal ettiğini
    # sanmazdı — ve iptal denetim izinde hiç görünmezdi.
    assert sb.pause_error("2026-09-01", "2026-09-14") == ""
    problem = sb.pause_error("2026-09-01", "")
    assert "süresiz" in problem.lower()
    assert "iptal" in problem.lower()
    assert "başlangıçtan önce" in sb.pause_error("2026-09-10", "2026-09-01")


# ================================================================= biçimciler

def test_abonelik_satiri_odenmemis_donemi_LISTEDEN_alir() -> None:
    # "Kim ödemedi" bu ekranın asıl sorusu ve cevabı listede geliyor; satır
    # başına ayrı bir ödeme çağrısı dokuz abonelikte dokuz istek demekti.
    row = sb.subscription_row(SUBSCRIPTION_ROW)
    assert row["unpaid_periods"] == 1
    assert row["unpaid_total_kurus"] == 640000
    assert row["status_label"] == "Aktif"
    assert row["contract_status_label"] == "İmzalandı"
    assert row["service_days_label"] == "Pzt, Sal, Çar, Per, Cum"
    assert row["needs_price"] is False


def test_fiyatsiz_abonelik_isaretlenir_ve_sifira_cevrilmez() -> None:
    # Sıfıra çevirmek "0,00 ₺" yazdırırdı ve yönetici anlaşma yapıldığını
    # sanardı; `pending` bir abonelikte fiyat `null` olabilir.
    row = sb.subscription_row({**SUBSCRIPTION_ROW, "agreed_unit_price_kurus": None})
    assert row["agreed_unit_price_kurus"] is None
    assert row["needs_price"] is True


def test_abonelik_gorunumu_alt_kayitlari_ve_sozlesmeyi_govdeden_alir() -> None:
    view = sb.subscription_view(SUBSCRIPTION_DETAIL)
    assert view["delivery_points"][0]["address_id"] == 704
    assert view["exceptions"][0]["label"] == "12 porsiyon"
    assert view["contract"]["status"] == "signed"
    # Tekil gövdede `contract_status` alanı YOK; sözleşme durumundan türetilir.
    assert view["contract_status"] == "signed"


def test_sozlesme_satirinda_token_ve_baglanti_HIC_GECMEZ() -> None:
    # Sunucu ikisini de döndürmüyor; yine de seçerek kurmak, sunucu bir gün
    # yanlışlıkla gönderse bile bağlantının ekrana çıkmamasını sağlar.
    row = sb.contract_row({**CONTRACT_ROW, "token": "gizli", "sign_url": "https://x"})
    assert "token" not in row
    assert "sign_url" not in row
    assert row["terminal"] is True
    assert row["open"] is False


def test_acik_sozlesme_pending_ve_sent_durumlaridir() -> None:
    assert sb.contract_row({**CONTRACT_ROW, "status": "pending"})["open"] is True
    assert sb.contract_row({**CONTRACT_ROW, "status": "sent"})["open"] is True
    assert sb.contract_row({**CONTRACT_ROW, "status": "expired"})["open"] is False


def test_takvim_uc_serbest_birakma_durumu_ayirir() -> None:
    # İkiye indirmek "mutfak bunu görüyor mu" sorusunu cevapsız bırakırdı.
    rows = [sb.calendar_row(raw) for raw in CALENDAR_ROWS]
    assert rows[0]["release_state"] == "released"
    assert rows[1]["release_state"] == "none"
    assert rows[2]["closed"] is True
    assert rows[2]["note"]


def test_uretim_defterinde_siparissiz_satir_basarisizlik_degil() -> None:
    # `order_id: null` = üretim DENENDİ, sipariş oluşmadı (kapalı gün, menü
    # yayınlanmamış, stok dolu). Satır yazılır ki gece işi yeniden denemesin.
    rows = [sb.run_row(raw) for raw in RUN_ROWS]
    assert rows[0]["produced"] is True
    assert rows[1]["produced"] is False
    assert rows[1]["outcome_label"] == "Denendi, sipariş oluşmadı"
    assert rows[1]["outcome_tone"] == "warn"


def test_odeme_satirinda_overdue_sunucudan_gelir() -> None:
    # İstemcide hesaplansaydı saati kaymış bir panelde borç bir gün erken
    # kırmızıya dönerdi (sözleşme).
    row = sb.payment_row(PAYMENT_ROW)
    assert row["overdue"] is True
    assert row["overdue_days"] == 11
    assert row["payable"] is True
    assert sb.payment_row({**PAYMENT_ROW, "status": "paid"})["payable"] is False


def test_talep_listesi_maskeyi_ACMAZ_tekil_kayit_maskesizdir() -> None:
    row = sb.request_row(REQUEST_ROW)
    assert row["telephone"] == "532****567"      # sunucunun maskesi olduğu gibi
    view = sb.request_view(REQUEST_DETAIL)
    assert view["telephone"] == "5321234567"     # tekil kayıt maskesiz
    assert view["kvkk_missing"] is False


def test_kvkk_onayi_eksikse_ekran_bunu_soylemek_zorunda() -> None:
    # Onaysız kayıt HİÇ OLUŞMAZ (sözleşme). Boş gelirse sessizce "—" yazmak,
    # olağan olmayan bir durumu olağan göstermek olurdu.
    view = sb.request_view({**REQUEST_DETAIL, "kvkk_accepted_at": None})
    assert view["kvkk_missing"] is True


# ==================================================================== akış

def test_akis_seridi_sunucu_alanlarindan_turetilir() -> None:
    view = sb.subscription_view(SUBSCRIPTION_DETAIL)
    flow = view["flow"]
    assert [step["key"] for step in flow["steps"]] == [
        "request", "price", "contract", "otp", "active"]
    # Aktif, imzalı ve fiyatlı bir abonelikte beş adımın hepsi tamam.
    assert flow["index"] == 4
    assert flow["next_key"] == ""


def test_akis_fiyatsiz_abonelikte_fiyat_adiminda_durur() -> None:
    row = sb.subscription_row({**SUBSCRIPTION_ROW, "status": "pending",
                               "agreed_unit_price_kurus": None,
                               "contract_status": "none"})
    flow = sb.flow_steps(row)
    assert flow["index"] == 0           # yalnız "Talep" tamam
    assert flow["next_key"] == "price"
    assert "fiyat" in flow["next_hint"].lower()


def test_akis_OTP_adimini_imzaya_baglar_ve_sunucuda_yurudugunu_soyler() -> None:
    # K3: OTP akışı Kontrol Merkezi'nde KURULMAZ. Ekran yalnız beklediğini
    # söyler; kod kutusu, doğrulama ya da yeniden gönderme YOKTUR.
    row = sb.subscription_row({**SUBSCRIPTION_ROW, "status": "pending",
                               "contract_status": "sent"})
    flow = sb.flow_steps(row)
    assert flow["next_key"] == "otp"
    assert "SUNUCUDA" in flow["next_hint"]


def test_akis_atlanmis_adimi_tamam_saymaz() -> None:
    # Fiyat girilmeden imzalanmış bir sözleşme olamaz ama veri öyle gelirse
    # ekran adımı "tamamlandı" göstermemeli: atlanmış bir adımı dolu saymak,
    # eksik olanı görünmez kılardı.
    row = sb.subscription_row({**SUBSCRIPTION_ROW, "agreed_unit_price_kurus": None,
                               "contract_status": "signed", "status": "active"})
    flow = sb.flow_steps(row)
    assert flow["index"] == 0
    assert flow["steps"][3]["done"] is True     # OTP verisi dolu…
    assert flow["steps"][1]["done"] is False    # …ama fiyat boş, zincir kırık


def test_iptal_ve_duraklatma_ipucu_akisi_degil_durumu_anlatir() -> None:
    iptal = sb.flow_steps(sb.subscription_row({**SUBSCRIPTION_ROW,
                                               "status": "cancelled"}))
    assert "geri dönüşü yoktur" in iptal["next_hint"].lower()
    durak = sb.flow_steps(sb.subscription_row({**SUBSCRIPTION_ROW, "status": "paused"}))
    assert "duraklat" in durak["next_hint"].lower()


# ================================================================= sözleşme

def test_ekran_sozlesmesi_odeme_kipini_TEK_deger_olarak_verir() -> None:
    # Cari hesap tamamen kalktı (iş kararı 1). Liste tek elemanlıdır ve öyle
    # kalmalı; ikinci bir değer eklemek, geçidin istek çıkmadan kestiği bir
    # seçeneği ekranda seçilebilir göstermek olurdu.
    contract = sb.screen_contract()
    assert [item["code"] for item in contract["payment_modes"]] == ["prepaid_monthly"]
    assert contract["reason"] == {"min": 10, "max": 500}
    assert contract["rules"]["max_lookahead_days"] == 7
    assert contract["rules"]["expires_in_days"]["default"] == 7
    assert len(contract["weekdays"]) == 7
    assert [item["code"] for item in contract["statuses"]] == list(sb.STATUS_CODES)


def test_durum_suzgeci_taninmayan_kodu_SESSIZCE_dusurmez() -> None:
    # Düşen bir kod, kullanıcının seçtiği süzgeçten BAŞKA bir kümeyi "sizin
    # süzgeciniz" diye göstermek olurdu.
    codes, problem = sb.status_filter("active,paused", sb.STATUS_CODES)
    assert codes == ["active", "paused"]
    assert problem == ""
    codes, problem = sb.status_filter("active,uydurma", sb.STATUS_CODES)
    assert codes == []
    assert "uydurma" in problem


def test_uyarilar_yutulmaz_ve_bicimi_korunur() -> None:
    payload = {"warnings": [{"code": "generated_orders_unaffected",
                             "order_ids": [8455]}]}
    assert sb.warnings_of(payload)[0]["code"] == "generated_orders_unaffected"
    assert sb.warnings_of({}) == []
    assert sb.warnings_of(None) == []
