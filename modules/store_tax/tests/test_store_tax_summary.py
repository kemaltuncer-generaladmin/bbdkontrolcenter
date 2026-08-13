"""KDV icmali hesabı — saf mantık, ağa çıkmaz.

Rakamlar mali müşavire gidiyor: burada "yaklaşık doğru" diye bir şey yok.
Her testin sorusu aynı: yanlış bir beyan üretebilir miyiz?

SAHTE BELGELER CANLI BİÇİMDEDİR: `GET /api/admin/invoices` alanları camelCase
döndürüyor (`createdAt`, `baseSubTotal`, `baseTaxAmount`, `channelName`) ve
`items` liste ucunda BOŞ geliyor. Testler snake_case sahte veriyle geçtiği
sürece icmalin canlıda boş çıktığı görülmedi.
"""

from __future__ import annotations

from typing import Any

from store_tax_backend import summary as vat


def _invoice(**extra: Any) -> dict[str, Any]:
    base = {"id": 1, "incrementId": "F-1", "createdAt": "2026-08-05 10:00:00",
            "channelName": "Varsayılan", "baseSubTotal": "1000.00",
            "baseDiscountAmount": "0.00", "baseTaxAmount": "200.00",
            "baseGrandTotal": "1200.00", "baseShippingAmount": "0.00"}
    base.update(extra)
    return base


def _line(percent: str, total: str, tax: str, discount: str = "0.00") -> dict[str, Any]:
    return {"taxPercent": percent, "baseTotal": total, "baseTaxAmount": tax,
            "baseDiscountAmount": discount}


WINDOW = {"start": "2026-08-01", "end": "2026-08-31"}


# ================================================================== temel

def test_taban_para_birimi_alani_tercih_edilir() -> None:
    # Beyan taban para birimine göre yapılır; mağaza para birimi farklı olabilir.
    row = {"subTotal": "500.00", "baseSubTotal": "1000.00"}
    assert vat.money_of(row, "sub_total") == 100_000


def test_tutar_hem_camel_hem_snake_adla_okunur() -> None:
    # Canlı yanıt camelCase; paket biçim değiştirirse ekran boşalmasın.
    assert vat.money_of({"baseTaxAmount": 200}, "tax_amount") == 20_000
    assert vat.money_of({"base_tax_amount": "200.00"}, "tax_amount") == 20_000
    assert vat.money_of({"taxAmount": 0}, "tax_amount") == 0


def test_alan_yoksa_sifir_doner_ama_uydurma_yapilmaz() -> None:
    assert vat.money_of({}, "tax_amount") == 0
    assert vat.money_of({"taxAmount": ""}, "tax_amount") == 0


def test_KDV_alani_YOK_ile_KDV_SIFIR_ayri_seylerdir() -> None:
    # Sıfır KDV mağazanın beyanıdır (%0 satırı); alanın hiç olmaması ise
    # okunamadı demektir ve belge `unresolved` kovasına düşer.
    assert vat.money_known({"baseTaxAmount": 0}, "tax_amount") is True
    assert vat.money_known({}, "tax_amount") is False


def test_gunu_camelCase_alandan_okur() -> None:
    # Bu satır tutmadığında icmal SESSİZCE BOŞ çıkıyordu: gün "" kalıyor,
    # belge aralık dışı sayılıyor ve matrah sıfır görünüyordu.
    assert vat.day_of({"createdAt": "2026-08-13 18:27:20"}) == "2026-08-13"
    assert vat.day_of({"created_at": "2026-08-13 18:27:20"}) == "2026-08-13"


def test_kalem_matrahindan_kalem_indirimi_dusulur() -> None:
    # Bagisto KDV'yi indirimden SONRAKİ tutar üzerinden hesaplıyor; indirimi
    # düşmemek matrahı şişirir.
    lines = vat.line_rows({"items": [_line("20", "1000.00", "160.00", "200.00")]})
    assert lines[0]["base"] == 80_000
    assert lines[0]["tax"] == 16_000


# ================================================== oran bazlı kırılım

def test_kalem_kirilimi_varsa_oran_bazinda_toplanir() -> None:
    doc = _invoice(baseSubTotal="1500.00", baseTaxAmount="250.00",
                   items=[_line("20", "1000.00", "200.00"),
                          _line("10", "500.00", "50.00"),
                          _line("0", "0.00", "0.00")])
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[], **WINDOW)
    by_key = {row["key"]: row for row in result["rates"]}
    assert by_key["20"]["base"] == 100_000
    assert by_key["20"]["tax"] == 20_000
    assert by_key["10"]["tax"] == 5_000
    assert result["totals"]["tax"] == 25_000
    assert result["unresolved"]["documents"] == 0


def test_yuksek_oran_ustte_cozulemeyen_en_altta_siralanir() -> None:
    doc = _invoice(baseSubTotal="200.00", baseTaxAmount="30.00",
                   items=[_line("10", "100.00", "10.00"), _line("20", "100.00", "20.00")])
    kalemsiz = _invoice(id=2, baseSubTotal="7.00", baseTaxAmount="3.00")
    result = vat.summarize(invoices=[doc, kalemsiz], refunds=[], canceled=[], **WINDOW)
    assert [row["key"] for row in result["rates"]] == ["20", "10"]
    assert result["unresolved"]["documents"] == 1


def test_kalem_toplami_belge_kdvsini_tutmuyorsa_fark_SAKLANMAZ() -> None:
    # Kargo KDV'si kalemlerde görünmüyor. Sessizce en yakın orana eklemek,
    # tutmayan bir beyanı tutuyor gibi göstermektir.
    doc = _invoice(baseTaxAmount="212.00", items=[_line("20", "1000.00", "200.00")])
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[], **WINDOW)
    assert result["unresolved"]["tax"] == 1_200
    assert result["unresolved"]["documents"] == 1
    assert result["totals"]["tax"] == 21_200
    assert any("tutmuyor" in item for item in result["unresolved"]["reasons"])


# ==================================================== oran türetme

def test_kalemsiz_belgenin_orani_bilinen_orandan_turetilir() -> None:
    # Fatura listesi ucu kalemleri her zaman taşımıyor; tek oranlı bir mağazada
    # doğrudan "çözülemedi" demek icmali boş bırakırdı.
    doc = _invoice(baseSubTotal="1000.00", baseTaxAmount="200.00")
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[],
                           known_percents=[20.0, 10.0], **WINDOW)
    by_key = {row["key"]: row for row in result["rates"]}
    assert by_key["20"]["base"] == 100_000
    assert by_key["20"]["derived"] == 1
    assert result["unresolved"]["documents"] == 0
    assert any("türetildi" in item for item in result["warnings"])


def test_karisik_sepette_oran_TURETILMEZ_cozulemedi_denir() -> None:
    # %20 ve %10 karışık bir sepette efektif oran %16,7 çıkar ve hiçbir orana
    # oturmaz. Tek orana yazmak beyanı bozardı.
    doc = _invoice(baseSubTotal="1500.00", baseTaxAmount="250.00")
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[],
                           known_percents=[20.0, 10.0], **WINDOW)
    assert result["rates"] == []
    assert result["unresolved"]["base"] == 150_000
    assert result["unresolved"]["tax"] == 25_000


def test_bilinen_oran_yoksa_turetme_yapilmaz() -> None:
    doc = _invoice()
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[], **WINDOW)
    assert result["unresolved"]["documents"] == 1


def test_KDV_sifir_olan_belge_YUZDE_SIFIR_satirina_yazilir() -> None:
    # CANLI: 16 faturanın hepsi `baseTaxAmount: 0`. Bunları "çözülemedi"
    # kovasına atmak, mağazanın açıkça sıfır beyan ettiği satışları şüpheli
    # göstermek olurdu — icmal tamamen kullanılamaz hâle geliyordu.
    doc = _invoice(baseSubTotal="8393.00", baseTaxAmount=0, baseGrandTotal="8393.00")
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[],
                           known_percents=[20.0], **WINDOW)
    assert [row["key"] for row in result["rates"]] == ["0"]
    assert result["rates"][0]["base"] == 839_300
    assert result["rates"][0]["tax"] == 0
    assert result["unresolved"]["documents"] == 0


def test_KDV_alani_HIC_YOKSA_cozulemedi_denir() -> None:
    doc = _invoice(baseSubTotal="100.00")
    doc.pop("baseTaxAmount")
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[],
                           known_percents=[20.0], **WINDOW)
    assert result["rates"] == []
    assert result["unresolved"]["documents"] == 1


def test_kalem_orani_gelmezse_kalemin_kendi_tutarindan_cozulur() -> None:
    # Canlı fatura kalemleri `taxPercent` taşımıyor; yalnız tutar var.
    doc = _invoice(baseSubTotal="1000.00", baseTaxAmount="200.00",
                   items=[{"baseTotal": "1000.00", "baseTaxAmount": "200.00"}])
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[],
                           known_percents=[20.0], **WINDOW)
    assert result["rates"][0]["key"] == "20"
    assert result["unresolved"]["documents"] == 0


def test_kanal_suzgeci_yerelde_uygulanir_ve_soylenir() -> None:
    # Mağaza `channel` parametresini yok sayıyor; ayıklama burada yapılır.
    web = _invoice(channelName="Web", items=[_line("20", "1000.00", "200.00")])
    mobil = _invoice(id=2, channelName="Mobil", items=[_line("20", "1000.00", "200.00")])
    result = vat.summarize(invoices=[web, mobil], refunds=[], canceled=[],
                           channel="Mobil", **WINDOW)
    assert result["documents"]["invoices"] == 1
    assert result["channels"][0]["channel"] == "Mobil"
    assert any("Kanal süzgeci" in item for item in result["warnings"])


def test_kurus_yuvarlamasi_turetmeyi_bozmaz() -> None:
    # Efektif oran hiçbir zaman tam 20,0000 çıkmaz; tolerans bunun içindir.
    doc = _invoice(baseSubTotal="333.33", baseTaxAmount="66.67")
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[],
                           known_percents=[20.0], **WINDOW)
    assert result["rates"][0]["key"] == "20"


def test_cok_kucuk_matrahta_oran_turetilmez() -> None:
    doc = _invoice(baseSubTotal="0.50", baseTaxAmount="0.10")
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[],
                           known_percents=[20.0], **WINDOW)
    assert result["unresolved"]["documents"] == 1


# ============================================================== iadeler

def test_iadeler_ayni_orandan_dusulur_ve_net_hesaplanir() -> None:
    satis = _invoice(items=[_line("20", "1000.00", "200.00")])
    iade = _invoice(id=9, baseSubTotal="200.00", baseTaxAmount="40.00",
                    baseGrandTotal="240.00", items=[_line("20", "200.00", "40.00")])

    result = vat.summarize(invoices=[satis], refunds=[iade], canceled=[], **WINDOW)
    row = result["rates"][0]
    assert row["base"] == 100_000
    assert row["refundBase"] == 20_000
    assert row["netBase"] == 80_000
    assert row["netTax"] == 16_000
    assert result["totals"]["netTax"] == 16_000


def test_iade_belgesi_oran_sayacini_sismez() -> None:
    # "Bu oranda kaç belge var" sorusu SATIŞ belgelerini sayar; iade ayrı sütun.
    satis = _invoice(items=[_line("20", "1000.00", "200.00")])
    iade = _invoice(id=9, baseSubTotal="200.00", baseTaxAmount="40.00",
                    items=[_line("20", "200.00", "40.00")])
    result = vat.summarize(invoices=[satis], refunds=[iade], canceled=[], **WINDOW)
    assert result["rates"][0]["documents"] == 1
    assert result["documents"]["refunds"] == 1


def test_cok_oranli_belge_her_oranin_sayacina_girer() -> None:
    doc = _invoice(baseSubTotal="200.00", baseTaxAmount="30.00",
                   items=[_line("20", "100.00", "20.00"), _line("10", "100.00", "10.00")])
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[], **WINDOW)
    assert all(row["documents"] == 1 for row in result["rates"])


# ============================================================== iptaller

def test_iptaller_icmalden_DUSULMEZ_yalniz_rapor_edilir() -> None:
    # Faturası kesilmiş bir iptalin düşümü iade kaydıyla zaten gelir; iki kez
    # düşmek beyanı eksiltirdi.
    satis = _invoice(items=[_line("20", "1000.00", "200.00")])
    iptal = _invoice(id=50, baseGrandTotal="600.00", baseTaxAmount="100.00")
    result = vat.summarize(invoices=[satis], refunds=[], canceled=[iptal], **WINDOW)
    assert result["totals"]["netTax"] == 20_000
    assert result["canceled"]["count"] == 1
    assert result["canceled"]["total"] == 60_000
    assert any("iptal edildi" in item for item in result["warnings"])


# ============================================================ kanal kırılımı

def test_kanal_kirilimi_iadeleri_de_tasir() -> None:
    web = _invoice(channelName="Web", items=[_line("20", "1000.00", "200.00")])
    mobil = _invoice(id=2, channelName="Mobil", baseSubTotal="500.00",
                     baseTaxAmount="100.00", items=[_line("20", "500.00", "100.00")])
    iade = _invoice(id=3, channelName="Web", baseSubTotal="100.00",
                    baseTaxAmount="20.00", items=[_line("20", "100.00", "20.00")])
    result = vat.summarize(invoices=[web, mobil], refunds=[iade], canceled=[], **WINDOW)
    by_channel = {row["channel"]: row for row in result["channels"]}
    assert by_channel["Web"]["netTax"] == 18_000
    assert by_channel["Web"]["refundDocuments"] == 1
    assert by_channel["Mobil"]["netTax"] == 10_000


def test_kanal_faturada_yoksa_siparisinden_okunur() -> None:
    doc = _invoice(channelName=None, order={"channelName": "Mobil"})
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[], **WINDOW)
    assert result["channels"][0]["channel"] == "Mobil"


def test_kanali_hic_olmayan_belge_belirtilmemis_kovasina_gider() -> None:
    doc = _invoice(channelName=None)
    result = vat.summarize(invoices=[doc], refunds=[], canceled=[], **WINDOW)
    assert result["channels"][0]["channel"] == "Belirtilmemiş"


# =========================================================== tarih süzgeci

def test_aralik_disi_belge_icmale_GIRMEZ() -> None:
    ici = _invoice(createdAt="2026-08-05 10:00:00", baseSubTotal="100.00",
                   baseTaxAmount="20.00", items=[_line("20", "100.00", "20.00")])
    disi = _invoice(id=2, createdAt="2026-09-05 10:00:00", baseSubTotal="900.00",
                    baseTaxAmount="180.00", items=[_line("20", "900.00", "180.00")])
    result = vat.summarize(invoices=[ici, disi], refunds=[], canceled=[], **WINDOW)
    assert result["documents"]["invoices"] == 1
    assert result["totals"]["tax"] == 2_000


def test_sunucu_suzgeci_uygulamadiysa_yakalanir() -> None:
    # Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar; aralık dışı
    # belge geldiyse süzgeç uygulanmamıştır. Alan adı camelCase okunmazsa bu
    # denetim de kör kalır: her belge "tarihsiz" görünüp geçip giderdi.
    ici = [{"createdAt": "2026-08-05 10:00:00"}]
    disi = [{"createdAt": "2026-09-05 10:00:00"}]
    assert vat.range_honored(ici, "2026-08-01", "2026-08-31") is True
    assert vat.range_honored(disi, "2026-08-01", "2026-08-31") is False


def test_tarihsiz_ya_da_bos_listede_suzgec_durumu_BILINMEZ_doner() -> None:
    # Bilinmeyeni "evet" saymak, eksik veriyle üretilmiş icmali doğru gibi
    # göstermek olurdu.
    assert vat.range_honored([], "2026-08-01", "2026-08-31") is None
    assert vat.range_honored([{"id": 1}], "2026-08-01", "2026-08-31") is None


def test_tarihsiz_belge_icmale_alinmaz() -> None:
    assert vat.in_range("", "2026-08-01", "2026-08-31") is False
