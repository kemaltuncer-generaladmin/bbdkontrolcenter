"""Tahsilatın saf hesapları: KDV kırılımı, durum eşlemesi, SMS planı, süzgeç.

Bu dosyanın tamamı ağsızdır. Paraya ve müşteriye dokunan üç kararın —
"ne kadar tahsil edilecek", "banka ne dedi", "kaç SMS gidecek" — burada
tek tek denenmesi, bu ekranın güvenilir olmasının tek yolu.
"""

from __future__ import annotations

import inspect
import re
from decimal import Decimal

from store_payment_gateway_backend import collect

# ==================================================== KDV: serbest vs ürün


def _free(amount: int) -> collect.Line:
    return collect.Line(kind="free", label="Serbest tutar", quantity=1, amount=amount,
                        tax_rate=Decimal(0))


def _product(amount: int, rate: str, quantity: int = 1) -> collect.Line:
    return collect.Line(kind="product", label="Kitap", quantity=quantity, amount=amount,
                        tax_rate=Decimal(rate), product_id=7, sku="KTP-1")


def test_serbest_tutar_kdvsiz_gider() -> None:
    # Personelin yazdığı rakam neyse müşteriden o çekilir; üstüne vergi eklenmez.
    result = collect.breakdown([_free(125_000)])
    assert result["net"] == 125_000
    assert result["tax"] == 0
    assert result["gross"] == 125_000
    assert result["lines"][0]["taxFree"] is True


def test_serbest_tutar_vergi_orani_verilse_bile_kdvsiz_kalir() -> None:
    # `free` kaleminin oranı yanlışlıkla doldurulsa bile uygulanmaz: kural
    # kalemin TİPİNDE, verinin içinde değil.
    line = collect.Line(kind="free", label="Serbest", quantity=1, amount=100_000,
                        tax_rate=Decimal(20))
    assert collect.breakdown([line])["tax"] == 0


def test_urun_kaleminde_kendi_vergi_orani_uygulanir() -> None:
    result = collect.breakdown([_product(100_000, "20")])
    assert result["net"] == 100_000
    assert result["tax"] == 20_000
    assert result["gross"] == 120_000


def test_urun_fiyati_kdv_dahilse_brutten_ayristirilir() -> None:
    result = collect.breakdown([_product(120_000, "20")], prices_include_tax=True)
    assert result["net"] == 100_000
    assert result["tax"] == 20_000
    assert result["gross"] == 120_000


def test_serbest_tutar_ve_urun_birlikte_toplanir() -> None:
    result = collect.breakdown([_free(50_000), _product(100_000, "10", quantity=2)])
    assert result["net"] == 250_000          # 50.000 + 200.000
    assert result["tax"] == 20_000           # yalnız ürün kaleminden
    assert result["gross"] == 270_000


def test_kurus_yuvarlamasi_yarim_yukari_gider() -> None:
    # 3,33 TL üzerinden %18: 59,94 kuruş → 60. Aşağı yuvarlamak faturayı
    # kuruş kadar eksik keser ve mutabakatta fark üretir.
    result = collect.breakdown([_product(333, "18")])
    assert result["tax"] == 60


def test_kalem_tutari_sifirsa_sessizce_dusmez() -> None:
    lines, problems = collect.lines_from_payload([{"kind": "free", "amount": 0}])
    assert lines == []
    assert problems and "tutarı" in problems[0]


def test_urun_kalemi_orani_yuku_icinden_okur() -> None:
    lines, problems = collect.lines_from_payload(
        [{"kind": "product", "productId": 7, "amount": 100_000, "taxRate": 20}])
    assert not problems
    assert lines[0].tax_rate == Decimal(20)


def test_vergi_orani_kategoriden_cozulur() -> None:
    categories = {"items": [{"id": 3, "name": "KDV %20"}]}
    rates = {"items": [{"id": 9, "tax_category_id": 3, "tax_rate": "20.0000"}]}
    assert collect.tax_rate_for(3, categories, rates) == Decimal("20.0000")


def test_vergi_orani_canli_camelcase_alan_adlariyla_cozulur() -> None:
    """CANLI ALAN ADLARI camelCase'tir: `/settings/tax-rates` satırı oranı
    `taxRate` diye veriyor, kategori bağı `taxCategoryId`. Yalnız snake_case
    aramak canlıda HER ÜRÜNÜ KDV'siz gösteriyordu."""
    categories = {"items": [{"id": 1, "code": "Kitap Vergi", "name": "KDV"}]}
    rates = {"items": [{"id": 1, "identifier": "Katma Değer Vergisi",
                        "taxCategoryId": 1, "taxRate": 20}]}
    assert collect.tax_rate_for(1, categories, rates) == Decimal(20)


def test_vergi_orani_kategoriye_gomulu_camelcase_listeden_okunur() -> None:
    # `/settings/tax-categories/{id}` oranları böyle gömüyor (canlı yanıt).
    categories = {"items": [{"id": 1, "name": "KDV",
                             "taxRates": [{"id": 1, "taxRate": 20}]}]}
    assert collect.tax_rate_for(1, categories, {"items": []}) == Decimal(20)


def test_vergi_orani_bulunamazsa_none_doner_sifir_uydurulmaz() -> None:
    """Sıfır da %20 da uydurmadır: biri faturayı KDV kadar eksik keser,
    diğeri fazla. Çözülemeyen oran `None` döner ve ekran tahsilatı durdurur."""
    assert collect.tax_rate_for(3, {"items": []}, {"items": []}) is None


def test_canli_vergi_listeleri_orani_cozemez_ve_bunu_gizlemez() -> None:
    """Bugünün canlı gerçeği: kategori LİSTESİ `taxRates: null` döndürüyor
    (oranlar yalnız tekil uçta gömülü) ve oran satırları kategori kimliği
    taşımıyor. Yani geçidin iki listesiyle oran ÇÖZÜLEMEZ — bu, sessizce
    "KDV %0" yazılacak bir durum değil, söylenmesi gereken bir eksiktir."""
    categories = {"items": [{"id": 1, "code": "Kitap Vergi", "name": "KDV",
                             "taxRates": None}]}
    rates = {"items": [{"id": 1, "identifier": "Katma Değer Vergisi", "taxRate": 20}]}
    assert collect.tax_rate_for(1, categories, rates) is None


def test_vergi_kategorisi_olmayan_urun_kdvsizdir_bu_kesin_cevaptir() -> None:
    # Canlı katalogda 1.421 ürünün tamamı `taxCategoryId: null`.
    assert collect.tax_rate_for(0, {"items": []}, {"items": []}) == Decimal(0)


def test_urun_vergi_kategorisi_canli_alan_adiyla_okunur() -> None:
    assert collect.product_category_id({"id": 7, "taxCategoryId": 3}) == 3
    assert collect.product_category_id({"id": 7, "taxCategoryId": None}) == 0


def test_urun_fiyati_indirim_penceresi_gecerliyken_indirimlidir() -> None:
    row = {"price": "2780.0000", "specialPrice": "2363.0000",
           "specialPriceFrom": "2026-01-01", "specialPriceTo": "2026-12-31"}
    assert collect.product_price(row, today="2026-08-13") == (236_300, True)


def test_urun_fiyati_indirim_penceresi_gectiyse_liste_fiyatidir() -> None:
    """Süresi dolmuş kampanyayı yeniden açmak, müşteriden eksik tahsilattır."""
    row = {"price": "2780.0000", "specialPrice": "2363.0000",
           "specialPriceFrom": "2026-07-20", "specialPriceTo": "2026-07-23"}
    assert collect.product_price(row, today="2026-08-13") == (278_000, False)


def test_urun_fiyati_snake_case_alan_adini_da_okur() -> None:
    assert collect.product_price({"price": "100.00", "special_price": None}) == (10_000, False)


# ============================================== durum eşlemesi (en kritik)

def test_bilinmeyen_durum_asla_basarisiz_yazilmaz() -> None:
    verdict = collect.map_status("some_new_bank_code")
    assert verdict["code"] == collect.UNKNOWN
    assert "başarısız" not in verdict["label"].lower()
    assert verdict["moneyMayBeTaken"] is True
    assert verdict["relinkLocked"] is True
    assert "tekrar link göndermeyin" in verdict["note"]


def test_bos_durum_da_bilinmeyendir() -> None:
    assert collect.map_status("")["code"] == collect.UNKNOWN


def test_provizyon_acik_basarisiz_degildir_ve_kilitler() -> None:
    verdict = collect.map_status("authorized")
    assert verdict["code"] == collect.VOID_REQUIRED
    assert "başarısız" not in verdict["label"].lower()
    assert verdict["relinkLocked"] is True
    assert "void" in verdict["note"]


def test_net_red_basarisizdir_ve_yeni_link_serbesttir() -> None:
    verdict = collect.map_status("declined")
    assert verdict["code"] == collect.FAILED
    assert verdict["moneyMayBeTaken"] is False
    assert verdict["relinkLocked"] is False


def test_odendi_kilitlidir_ikinci_link_uretilmez() -> None:
    verdict = collect.map_status("paid")
    assert verdict["code"] == collect.PAID
    assert verdict["relinkLocked"] is True


def test_durum_sozcugu_bicimden_bagimsiz_okunur() -> None:
    for word in ("PAID", "Paid", "  paid  "):
        assert collect.map_status(word)["code"] == collect.PAID
    for word in ("void-required", "VOID_REQUIRED", "void required"):
        assert collect.map_status(word)["code"] == collect.VOID_REQUIRED


def test_bekleyen_durum_yerel_durumu_ezmez() -> None:
    # SMS gönderilmiş bir talebi "link üretildi"ye geri düşürmek, personele
    # mesajın gitmediğini düşündürürdü.
    assert collect.map_status("pending", local=collect.SENT)["code"] == collect.SENT
    assert collect.map_status("pending", local=collect.DRAFT)["code"] == collect.LINKED


def test_kilitli_satir_icin_gerekce_metni_uretilir() -> None:
    view = {"status": collect.map_status("unknown")}
    allowed, block = collect.can_relink(view)
    assert allowed is False
    assert collect.DOUBLE_CHARGE_WARNING in block


# ==================================================================== SMS

def test_sms_plani_turkce_harfleri_ve_tasarrufu_gosterir() -> None:
    plan = collect.sms_plan("Ödemeniz için şu bağlantıyı kullanın")
    assert plan["parts"] >= 1
    assert plan["offending"]                 # ş/ğ/ı pahalı karakterler
    assert "Odemeniz" in plan["simplified"]
    assert plan["savedParts"] >= 0


def test_sablon_doldurulur_ve_eksikler_bildirilir() -> None:
    result = collect.render_template("Sayin {ad}, {tutar} icin {link}",
                                     {"ad": "Ayşe", "tutar": "", "link": "https://x/1"})
    assert "Ayşe" in result["text"]
    assert "{tutar}" in result["text"]       # boş bırakılmaz, süslü parantez kalır
    assert result["missing"] == ["tutar"]


def test_taninmayan_yer_tutucu_bildirilir() -> None:
    result = collect.render_template("Merhaba {isim}", {"ad": "Ayşe"})
    assert result["unknown"] == ["isim"]


def test_saglayici_kodu_40_acik_metne_cevrilir() -> None:
    """Kod 40'ın Netgsm'deki karşılığı "Mesaj başlığı sistemde tanımlı değil";
    personelin yapacağı iş başlığı düzeltmektir ve metin bunu söyler."""
    hata = RuntimeError("[40] Gönderici başlığı sistemde tanımlı değil")
    hata.provider_code = "40"          # type: ignore[attr-defined]
    assert "Mesaj başlığı sistemde tanımlı değil" in collect.provider_hint(hata)
    assert "Netgsm panelinde" in collect.provider_hint(hata)     # ne yapılacağı da yazar


def test_kodsuz_hata_bos_metin_dondurur() -> None:
    # Kodu olmayan hata için uydurma bir açıklama yazmak, personeli yanlış
    # yere bakmaya iter.
    assert collect.provider_hint(RuntimeError("ağ kapalı")) == ""


def test_varsayilan_sablon_tek_sms_e_sigar() -> None:
    filled = collect.render_template(collect.DEFAULT_TEMPLATE, {
        "ad": "Ayse Yilmaz", "tutar": "1.250,00 TL", "link": "https://ode.me/abc123",
        "kod": "TAH-20260813-7F3A", "kurum": "BBD Store",
    })
    assert collect.sms_plan(filled["text"])["parts"] == 1


def test_para_gosteriminde_lira_isareti_kullanilmaz() -> None:
    # '₺' GSM-7'de yok: tek başına tüm mesajı UCS-2'ye düşürür ve 160
    # karakterlik sınır 70'e iner.
    assert collect.money_tr(125_000) == "1.250,00 TL"
    assert "₺" not in collect.money_tr(125_000)
    assert collect.sms_plan(collect.money_tr(125_000))["unicode"] is False


def test_binlik_ayraci_dogru_yerlestirilir() -> None:
    assert collect.money_tr(123_456_789) == "1.234.567,89 TL"
    assert collect.money_tr(50) == "0,50 TL"


# =========================================== kuruş → TL (telde giden tutar)

def test_kurus_magazanin_bekledigi_ondalik_metne_cevrilir() -> None:
    """Mağaza `amount` alanını ONDALIK TL METNİ olarak okuyor ("125.00").

    Kuruş tam sayısını olduğu gibi göndermek, 125,00 TL'lik bir tahsilat için
    12.500,00 TL isteyen bir link üretme denemesiydi (mağaza 422 AMOUNT_DRIFT
    ile reddederdi).
    """
    assert collect.from_kurus(12_500) == "125.00"
    assert collect.from_kurus(8_615) == "86.15"
    assert collect.from_kurus(100) == "1.00"
    assert collect.from_kurus(5) == "0.05"
    assert collect.from_kurus(0) == "0.00"
    assert collect.from_kurus(123_456_789) == "1234567.89"


def test_kurus_cevriminde_kayan_nokta_kullanilmaz() -> None:
    """FLOAT TUZAĞI ÖNCE GÖSTERİLİR, SONRA UZAK DURULDUĞU SINANIR.

    Aynı çevrim kayan noktayla yapılsaydı bir kuruş sessizce kaybolurdu:
    aşağıdaki iki `int(...)` satırı bu makinede 1998 ve 123456788 verir —
    yani 19,99 TL'lik tahsilat 19,98 TL'ye, 1.234.567,89 TL'lik tahsilat bir
    kuruş eksiğe düşerdi. `Decimal` yolunda kayıp YOKTUR: metin tekrar kuruşa
    çevrildiğinde başlangıç değeri birebir geri gelir.
    """
    assert int(1999 / 100 * 100) == 1998                  # float — bir kuruş eksik
    assert int(123_456_789 / 100 * 100) == 123_456_788    # float — bir kuruş eksik

    for kurus in (1, 5, 29, 100, 1_999, 8_615, 12_500, 99_999, 123_456_789):
        wire = collect.from_kurus(kurus)
        # Gidiş-dönüş kayıpsız: mağazanın kuruş kuruş karşılaştırdığı sayı bu.
        assert collect.to_kurus(wire) == kurus
        # Basamak sayısı HER ZAMAN iki: float'ın "19.990000000000002" ya da
        # "125.0" yazımlarının hiçbiri üretilemez.
        assert re.fullmatch(r"-?\d+\.\d{2}", wire), wire
        assert Decimal(wire) == Decimal(kurus) / 100

    # Kaynakta da float yok: kural yorumla değil, kodun kendisiyle duruyor.
    assert "float(" not in inspect.getsource(collect.from_kurus)


def test_ad_soyad_son_bosluktan_ayrilir_ve_soyad_uydurulmaz() -> None:
    """Mağaza `firstName`/`lastName` alanlarını ayrı ve dolu istiyor.

    Türkçede ikinci AD yaygın ("Ayşe Nur"), ikinci SOYAD değil; bu yüzden son
    sözcük soyaddır. Tek sözcüklü adda soyad UYDURULMAZ — adı soyad diye
    tekrarlamak faturaya ve bankaya var olmayan bir soyad yazmaktır.
    """
    assert collect.split_name("Ayşe Nur Yılmaz") == ("Ayşe Nur", "Yılmaz")
    assert collect.split_name("Ayşe Yılmaz") == ("Ayşe", "Yılmaz")
    assert collect.split_name("  Ayşe   Yılmaz  ") == ("Ayşe", "Yılmaz")
    assert collect.split_name("Ayşe") == ("Ayşe", "")
    assert collect.split_name("") == ("", "")
    assert collect.split_name(None) == ("", "")


# =============================================================== yardımcı

def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert collect.reason_error("kısa")
    assert collect.reason_error("Müşteri telefonda tahsilat istedi") == ""


def test_telefon_dogrulamasi_gonderimden_once_yapilir() -> None:
    assert collect.phone_error("0532 123 45 67") == ""
    assert collect.phone_error("123") != ""
    assert collect.normal_phone("+90 532 123 45 67") == "5321234567"


def test_talep_numarasi_sirali_kimlik_sizdirmaz() -> None:
    code = collect.request_code("abcdef123456")
    assert code.startswith("TAH-")
    assert code != collect.request_code("999999999999")


# ======================================================== süzgeç → SQL

def test_suzgec_degeri_sqle_gomulmez_yer_tutucu_kullanilir() -> None:
    clause, params = collect.filter_clause(q="O'Brien")
    assert "O'Brien" not in clause
    assert clause.count("?") == len(params)
    assert params[0] == "%O'Brien%"


def test_bos_suzgec_hicbir_kosul_uretmez() -> None:
    clause, params = collect.filter_clause()
    assert clause == ""
    assert params == []


def test_tarih_suzgeci_gun_oneki_uzerinden_karsilastirir() -> None:
    # `created_at` saat dilimi eki taşır; tam damgayla kıyaslamak son günün
    # kayıtlarını dışarıda bırakırdı.
    clause, params = collect.filter_clause(start="2026-08-01", end="2026-08-13")
    assert clause.count("substr(created_at, 1, 10)") == 2
    assert params == ["2026-08-01", "2026-08-13"]


def test_coklu_durum_suzgeci_hepsini_yer_tutucuyla_sorar() -> None:
    """"Açık link" raporu hem `linked` hem `sent` ister: SMS gidince durum
    değiştiği için tek `status` süzgeci raporu boşaltıyordu."""
    where, params = collect.filter_clause(statuses=[collect.LINKED, collect.SENT])
    assert where == " WHERE status IN (?, ?)"
    assert params == [collect.LINKED, collect.SENT]


def test_coklu_durum_suzgecinde_taninmayan_kod_dusurulur() -> None:
    where, params = collect.filter_clause(statuses=["linked", "uydurma"])
    assert where == " WHERE status IN (?)"
    assert params == ["linked"]


def test_taninmayan_durum_suzgeci_yok_sayilir() -> None:
    clause, _ = collect.filter_clause(status="uydurma")
    assert "status" not in clause


def test_tutar_araligi_kurus_olarak_gider() -> None:
    clause, params = collect.filter_clause(min_amount=10_000, max_amount=50_000)
    assert "gross >= ?" in clause and "gross <= ?" in clause
    assert params == [10_000, 50_000]
