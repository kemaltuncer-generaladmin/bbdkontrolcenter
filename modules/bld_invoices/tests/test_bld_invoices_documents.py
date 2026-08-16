"""Belge dönüşümleri — ağ ve dosya olmadan sınanır.

Bu dosya, kâğıda ne yazıldığına karar veren SAF katmanı sabitler: zorunlu
dipnot, iptal uyarısı, kaçış (`&` taşıyan bir unvan tüm belgeyi ayrıştırılamaz
kılabilir) ve kip doğrulaması.
"""

from __future__ import annotations

import pytest
from bld_invoices_backend import documents as doc
from bld_invoices_fakes import INVOICE_FULL, VOID_INVOICE


def test_zorunlu_dipnot_belgeden_kaldirilamaz() -> None:
    kart = doc.invoice_card(INVOICE_FULL)
    bolumler = doc.pdf_sections(kart)

    # İlk bölüm HER ZAMAN dipnottur; ayrıca `build_pdf` çağrısında `footer`
    # olarak her sayfanın altına da basılır (servis).
    assert bolumler[0]["kind"] == "note"
    assert doc.NOTICE in bolumler[0]["text"]


def test_belgenin_kendi_ibaresi_de_basilir() -> None:
    # Sunucunun `snapshot.notice` metni belgenin DONMUŞ içeriğine aittir:
    # şirket cümlesini sonradan değiştirse bile eski belge eskisini gösterir.
    kart = doc.invoice_card(INVOICE_FULL)
    metinler = [b.get("text", "") for b in doc.pdf_sections(kart) if b["kind"] == "note"]

    assert any("bilgilendirme amaçlıdır" in metin for metin in metinler)


def test_iptal_edilmis_belge_uyariyi_tasir() -> None:
    kart = doc.invoice_card(VOID_INVOICE)
    bolumler = doc.pdf_sections(kart)
    metinler = [b.get("text", "") for b in bolumler if b["kind"] == "note"]

    assert any("İPTAL EDİLMİŞTİR" in metin for metin in metinler)
    # Gerekçe de kâğıda basılır: iptalin nedenini görmeyen bir müşteri, elindeki
    # kâğıdın neden geçersiz olduğunu bilemez.
    assert any(VOID_INVOICE["void_reason"] in metin for metin in metinler)
    # Toplam kutusunda da geçer — tek yerde yazan bir uyarı gözden kaçar.
    kutu = next(b for b in bolumler if b["kind"] == "tiles" and b.get("title") == "Toplam")
    assert any("İPTAL" in etiket for etiket, _ in kutu["tiles"])


def test_xml_kacisi_belgeyi_ayristirilabilir_tutar() -> None:
    # `build_pdf` hücreleri reportlab'ın mini XML'iyle çizer ve kaçırılmamış bir
    # `<...>` parçasını ETİKET SANIP ATAR: `"Acme & Co <A.Ş.> Ltd"` kâğıda
    # `"Acme & Co  Ltd"` olarak basılırdı. Hata patlayarak değil, unvanın
    # yarısını sessizce düşürerek gelirdi.
    bozuk = {**INVOICE_FULL,
             "snapshot_json": {**INVOICE_FULL["snapshot_json"],
                               "customer": {"label": "Acme & Co <A.Ş.>"}}}
    bolumler = doc.pdf_sections(doc.invoice_card(bozuk))
    alici = next(b for b in bolumler if b.get("title") == "Alıcı")

    assert alici["rows"][0][1] == "Acme &amp; Co &lt;A.Ş.&gt;"


def test_kaynak_etiketi_tek_sutunda_okunur() -> None:
    siparis = doc.invoice_row(INVOICE_FULL)
    donem = doc.invoice_row({"id": 9, "subscription_id": 18,
                             "period_start": "2026-08-01", "period_end": "2026-08-31"})

    assert siparis["source_label"] == "Sipariş #8421"
    assert donem["source_label"] == "Abonelik #18 · 01.08.2026–31.08.2026"


def test_bos_bloklar_tire_ile_doldurulmaz() -> None:
    # Vergi dairesi olmayan bir müşteride o satır HİÇ olmamalı; tire dolu bir
    # belge okunmaz olur.
    kart = doc.invoice_card({**INVOICE_FULL,
                             "snapshot_json": {**INVOICE_FULL["snapshot_json"],
                                               "customer": {"label": "Ali Veli"}}})
    alici = next(b for b in doc.pdf_sections(kart) if b.get("title") == "Alıcı")

    assert alici["rows"] == [["Alıcı", "Ali Veli"]]


def test_snapshot_eksik_alani_uydurmaz() -> None:
    gorunum = doc.snapshot_view({"lines": [{"description": "Menü"}]})

    assert gorunum["issuer"]["name"] == ""
    assert gorunum["lines"][0]["quantity"] == 0
    assert gorunum["totals"]["currency"] == "TRY"


@pytest.mark.parametrize(("kisa", "beklenen"), [
    ("", False),
    ("dokuz kar", False),
    ("Müşteri belge talep etti", True),
])
def test_gerekce_alt_siniri(kisa: str, beklenen: bool) -> None:
    assert (doc.reason_error(kisa) == "") is beklenen


def test_gerekce_ust_siniri() -> None:
    assert doc.reason_error("x" * (doc.MAX_REASON + 1)) != ""


@pytest.mark.parametrize(("kwargs", "hatali"), [
    ({"order_id": 8421, "subscription_id": 0, "period_start": "", "period_end": ""}, False),
    ({"order_id": 0, "subscription_id": 18,
      "period_start": "2026-08-01", "period_end": "2026-08-31"}, False),
    ({"order_id": 8421, "subscription_id": 18,
      "period_start": "", "period_end": ""}, True),
    ({"order_id": 0, "subscription_id": 0, "period_start": "", "period_end": ""}, True),
    ({"order_id": 0, "subscription_id": 18,
      "period_start": "2026-08-31", "period_end": "2026-08-01"}, True),
    ({"order_id": 0, "subscription_id": 18,
      "period_start": "2026-01-01", "period_end": "2026-06-30"}, True),
    ({"order_id": 0, "subscription_id": 18,
      "period_start": "1 Ağustos", "period_end": "2026-08-31"}, True),
])
def test_kip_dogrulamasi(kwargs: dict[str, object], hatali: bool) -> None:
    assert (doc.create_error(**kwargs) != "") is hatali  # type: ignore[arg-type]


def test_donem_araligi_iki_ucu_de_sayar() -> None:
    # 62 gün tavanı "iki uç dâhil" sayılır: 1–31 Ağustos 31 gündür, 62 değil.
    assert doc.day_span("2026-08-01", "2026-08-31") == 31
    assert doc.day_span("2026-08-01", "2026-08-01") == 1


def test_an_ve_gun_bicimleri_turkcedir() -> None:
    assert doc.day("2026-08-16") == "16.08.2026"
    # Saat YEREL saate çevrilir; sunucu her anı UTC gönderiyor.
    assert doc.moment("2026-08-16T15:00:00Z").startswith("16.08.2026")
    # Ayrıştırılamayan değer OLDUĞU GİBİ kalır: boş göstermek, veriyi
    # kaybettiğini gizlemek olurdu.
    assert doc.moment("her zaman") == "her zaman"
    assert doc.moment("") == ""


def test_dosya_adi_belge_numarasindan_uretilir() -> None:
    assert doc.file_stem({"invoice_no": "BLD-2026-000044"}) == "BLD-2026-000044"
    # Numarası olmayan bir kayıt (kuru prova sonucu) kimliğe düşer; adsız bir
    # dosya arşivde hangi belge olduğunu söylemezdi.
    assert doc.file_stem({"id": 44}) == "belge-44"
    assert doc.file_stem({"invoice_no": "BLD/2026 #44"}) == "BLD-2026--44"


def test_liste_dokumu_toplami_sunucudan_alir() -> None:
    satirlar = [doc.invoice_row(INVOICE_FULL), doc.invoice_row(VOID_INVOICE)]
    bolumler = doc.list_sections(satirlar, meta={"total": 44, "issued_total_kurus": 8912000},
                                 filter_label="status=issued", truncated=True)
    kutular = dict(bolumler[1]["tiles"])

    # Toplam SÜZGECİN toplamıdır; sayfadaki iki satırdan hesaplanmaz.
    assert kutular["Geçerli toplam"] == "89.120,00 ₺"
    assert kutular["İptal"] == "1"
    # Tavana takılan liste bunu YAZAR: eksik listeyi tam gibi göstermek,
    # "toplam tutmuyor" diye saatler harcatır.
    assert any("tavana takıldı" in b.get("text", "") for b in bolumler if b["kind"] == "note")
