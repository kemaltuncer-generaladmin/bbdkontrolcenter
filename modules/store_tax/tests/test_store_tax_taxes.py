"""Vergi dönüşümleri — saf mantık, ağa çıkmaz.

Buradaki her test bir TUZAĞA karşılık gelir; testin adı tuzağın kendisidir.
"""

from __future__ import annotations

from store_tax_backend import taxes

#: 2026-08-13'te `GET /api/admin/settings/tax-rates` yanıtından alınan GERÇEK
#: kayıt. Testin sahte verisi canlı biçimden ayrılırsa ekran boş açılır ve
#: kimse fark etmez — bu yüzden burada duruyor.
CANLI_ORAN = {"id": 1, "identifier": "Katma Değer Vergisi", "isZip": False, "zipCode": "",
              "zipFrom": None, "zipTo": None, "state": "Tüm Ülke", "country": "TR",
              "taxRate": 20, "createdAt": "2026-07-20T22:45:38+03:00",
              "updatedAt": "2026-07-20T22:45:38+03:00", "message": None}


# ======================================================== canlı alan adları

def test_camel_donusumu() -> None:
    assert taxes.camel("tax_category_id") == "taxCategoryId"
    assert taxes.camel("identifier") == "identifier"
    assert taxes.camel("base_sub_total") == "baseSubTotal"


def test_alan_iki_adla_da_aranir() -> None:
    assert taxes.pick({"taxRate": 20}, "tax_rate") == 20
    assert taxes.pick({"tax_rate": 20}, "tax_rate") == 20
    assert taxes.pick({"taxRate": None}, "tax_rate") is None
    # `attribute` de aynı kuralı EAV zarfında uygular.
    assert taxes.attribute({"values": {"common": {"taxCategoryId": 3}}}, "tax_category_id") == 3


def test_canli_oran_kaydi_eksiksiz_okunur() -> None:
    row = taxes.rate_row(CANLI_ORAN)
    assert row["percent"] == 20.0
    assert row["percentLabel"] == "%20"
    assert row["name"] == "Katma Değer Vergisi"
    assert row["country"] == "TR"
    assert row["region"] == "Türkiye · Tüm Ülke"
    assert row["zip"]["kind"] == "any"
    assert row["createdAt"].startswith("2026-07-20")


def test_canli_kayittan_kurulan_govde_snake_case_gider() -> None:
    # Yanıt camelCase, İSTEK snake_case (OpenAPI şeması böyle ilan ediyor).
    body = taxes.rate_body(CANLI_ORAN, taxes.rate_patch({"percent": 18}))
    assert body["identifier"] == "Katma Değer Vergisi"
    assert body["tax_rate"] == "18.0000"
    assert body["country"] == "TR"
    assert body["is_zip"] == 0
    assert body["zip_code"] == "*"          # boş posta kodu joker'e çevrilir
    assert "taxRate" not in body


# ================================================================== para

def test_ondalik_para_kurusa_cevrilirken_bir_kurus_kaybolmaz() -> None:
    # `float("1234.35") * 100` bazı değerlerde 123434.999… verir ve int() bir
    # kuruş aşağı yuvarlar. KDV icmalinde bu sapma binlerce satırda birikir.
    assert taxes.to_kurus("1234.35") == 123435
    assert taxes.to_kurus("0.10") == 10
    assert taxes.to_kurus("8.615") == 862


def test_bos_ve_bozuk_para_degeri_sifir_degil_none_dondurur() -> None:
    assert taxes.to_kurus(None) is None
    assert taxes.to_kurus("") is None
    assert taxes.to_kurus("abc") is None
    assert taxes.to_kurus("0") == 0


# ================================================================== oran

def test_oran_metni_okunur_ve_okunamayan_oran_sifir_sayilmaz() -> None:
    # `None` = "oran okunamadı", 0.0 = "KDV'siz" (kitap, gazete). İkisini
    # birleştirmek muaf ürünü okunamayan ürünle aynı kovaya atardı.
    assert taxes.rate_value("20.0000") == 20.0
    assert taxes.rate_value("8,5") == 8.5
    assert taxes.rate_value("0.0000") == 0.0
    assert taxes.rate_value("") is None
    assert taxes.rate_value("abc") is None


def test_oran_etiketi_turkce_ondalik_ayraci_kullanir() -> None:
    assert taxes.rate_label(20.0) == "%20"
    assert taxes.rate_label(8.5) == "%8,5"
    assert taxes.rate_label(None) == "Oran okunamadı"


def test_oran_kovasi_ondalik_sifirlari_atar() -> None:
    assert taxes.rate_key(20.0) == "20"
    assert taxes.rate_key(8.5) == "8.5"
    assert taxes.rate_key(None) == "?"


# ======================================================= geçerlilik tarihi

def test_gecerlilik_tarihi_gecmise_verilemez() -> None:
    # Geçmiş bir tarih, o tarihten beri kesilen faturaların yeni orandan
    # kesildiğini iddia eder. Kesilmemiştir.
    assert taxes.effective_error("2020-01-01", today="2026-08-13")
    assert taxes.effective_error("2026-08-13", today="2026-08-13") == ""
    assert taxes.effective_error("2026-09-01", today="2026-08-13") == ""
    assert taxes.effective_error("", today="2026-08-13") == ""


def test_bozuk_gecerlilik_tarihi_reddedilir() -> None:
    assert "YYYY-AA-GG" in taxes.effective_error("13.08.2026", today="2026-08-13")
    assert taxes.effective_error("2026-13-45", today="2026-08-13")


def test_kisa_gerekce_reddedilir() -> None:
    assert taxes.reason_error("ok")
    assert taxes.reason_error("düzeltme")
    assert taxes.reason_error("KDV oranı %20'ye çıktı") == ""


# ============================================================ yazma gövdesi

def test_kismi_yazma_zorunlu_alanlari_bosaltmaz() -> None:
    # TUZAK 6: yalnız değişen alanı göndermek `country`/`zip_code` gibi zorunlu
    # alanları boşaltıp 422 üretiyordu; gövde mevcut kayıttan kurulur.
    current = {"identifier": "KDV 20", "tax_rate": "20.0000", "country": "TR",
               "state": "", "is_zip": 0, "zip_code": "*"}
    body = taxes.rate_body(current, {"tax_rate": "10"})
    assert body["identifier"] == "KDV 20"
    assert body["country"] == "TR"
    assert body["zip_code"] == "*"
    assert body["tax_rate"] == "10.0000"


def test_oncelik_ve_bilesik_vergi_govdeye_KONMAZ() -> None:
    # TUZAK 3: Bagisto çekirdeğinde öncelikli/bileşik vergi yok. Gönderilen
    # alan sessizce yok sayılır ve kullanıcı "önceliği ayarladım" sanır.
    body = taxes.rate_body({"identifier": "KDV", "country": "TR"},
                           {"priority": 5, "is_compound": 1})
    assert "priority" not in body
    assert "is_compound" not in body


def test_posta_kodu_araligi_ile_tekil_kod_ayni_govdede_bulunmaz() -> None:
    # `is_zip` gönderilmezse Bagisto tekil koda düşüyor ve aralık sessizce
    # kayboluyor.
    aralik = taxes.rate_body({}, {"identifier": "A", "country": "TR", "is_zip": 1,
                                  "zip_from": "01000", "zip_to": "01999", "zip_code": "34000"})
    assert aralik["is_zip"] == 1
    assert "zip_code" not in aralik

    tekil = taxes.rate_body({}, {"identifier": "A", "country": "TR", "is_zip": 0,
                                 "zip_from": "1", "zip_to": "2"})
    assert tekil["is_zip"] == 0
    assert "zip_from" not in tekil
    assert tekil["zip_code"] == "*"


def test_ekran_alan_adlari_bagisto_adlarina_cevrilir_bilinmeyen_dusurulur() -> None:
    patch = taxes.rate_patch({"name": "KDV 20", "percent": 20, "zipFrom": "01000",
                              "uydurma": "x"})
    assert patch == {"identifier": "KDV 20", "tax_rate": 20, "zip_from": "01000"}


def test_oran_formu_mağazaya_gitmeden_denetlenir() -> None:
    assert "adı zorunlu" in taxes.rate_form_error({"country": "TR", "tax_rate": "20"})
    assert "%0 ile %100" in taxes.rate_form_error(
        {"identifier": "A", "country": "TR", "tax_rate": "120"})
    assert "Ülke zorunlu" in taxes.rate_form_error({"identifier": "A", "tax_rate": "20"})
    assert taxes.rate_form_error({"identifier": "A", "country": "TR", "tax_rate": "20"}) == ""


def test_aralik_baslangici_bitisinden_buyuk_olamaz() -> None:
    problem = taxes.rate_form_error({"identifier": "A", "country": "TR", "tax_rate": "20",
                                     "is_zip": 1, "zip_from": "99999", "zip_to": "01000"})
    assert "başlangıcı bitişinden" in problem


# ================================================================ kapsam

def test_joker_posta_kodu_her_seyle_kesisir() -> None:
    joker = taxes.zip_scope({})
    aralik = taxes.zip_scope({"is_zip": 1, "zip_from": "01000", "zip_to": "01999"})
    assert joker["kind"] == "any"
    assert taxes.zip_overlap(joker, aralik) is True


def test_posta_kodu_araliklari_metin_degil_sayi_olarak_karsilastirilir() -> None:
    # `"01000" < "9"` metinde doğrudur ama posta kodunda değildir.
    bir = taxes.zip_scope({"is_zip": 1, "zip_from": "01000", "zip_to": "01999"})
    iki = taxes.zip_scope({"is_zip": 1, "zip_from": "9", "zip_to": "10"})
    assert taxes.zip_overlap(bir, iki) is False

    uc = taxes.zip_scope({"is_zip": 1, "zip_from": "01500", "zip_to": "02000"})
    assert taxes.zip_overlap(bir, uc) is True


def test_sayiya_cevrilemeyen_posta_kodunda_kesisim_VARSAYILIR() -> None:
    # Bilmediğimiz durumda "çakışma yok" demek, çakışmayı gizler.
    harfli = taxes.zip_scope({"is_zip": 1, "zip_from": "SW1A", "zip_to": "SW9Z"})
    sayili = taxes.zip_scope({"is_zip": 1, "zip_from": "01000", "zip_to": "01999"})
    assert taxes.zip_overlap(harfli, sayili) is True


# ============================================================ çakışma

def _rate(rate_id: int, percent: str, *, categories: list[int] | None = None,
          **extra: object) -> dict[str, object]:
    return taxes.rate_row({"id": rate_id, "identifier": f"Oran {rate_id}", "tax_rate": percent,
                           "country": "TR", **extra}, category_ids=categories)


def test_ayni_kategoride_ayni_bolgeyi_karsilayan_iki_oran_cakisma_sayilir() -> None:
    rates = [_rate(1, "20.0000"), _rate(2, "10.0000")]
    categories = [taxes.category_row({"id": 5, "code": "std", "name": "Standart",
                                      "tax_rates": [1, 2]}, rates=rates)]
    found = taxes.conflicts(rates, categories)
    assert len(found) == 1
    assert found[0]["kind"] == "conflict"
    assert found[0]["rateIds"] == [1, 2]


def test_farkli_kategorilerdeki_oranlar_cakisma_sayilmaz() -> None:
    # Mağaza ürüne önce vergi kategorisine bakar; kitap %0, kalem %20 normaldir.
    rates = [_rate(1, "20.0000"), _rate(2, "0.0000")]
    categories = [
        taxes.category_row({"id": 5, "code": "std", "name": "Standart", "tax_rates": [1]},
                           rates=rates),
        taxes.category_row({"id": 6, "code": "kitap", "name": "Kitap", "tax_rates": [2]},
                           rates=rates),
    ]
    assert taxes.conflicts(rates, categories) == []


def test_ayni_oranli_ortusme_cakisma_degil_yinelenmis_sayilir() -> None:
    rates = [_rate(1, "20.0000"), _rate(2, "20.0000")]
    categories = [taxes.category_row({"id": 5, "code": "std", "name": "Standart",
                                      "tax_rates": [1, 2]}, rates=rates)]
    found = taxes.conflicts(rates, categories)
    assert found[0]["kind"] == "duplicate"


def test_kesismeyen_posta_kodlari_cakisma_uretmez() -> None:
    rates = [_rate(1, "20.0000", is_zip=1, zip_from="01000", zip_to="01999"),
             _rate(2, "10.0000", is_zip=1, zip_from="34000", zip_to="34999")]
    categories = [taxes.category_row({"id": 5, "code": "std", "name": "Standart",
                                      "tax_rates": [1, 2]}, rates=rates)]
    assert taxes.conflicts(rates, categories) == []


# ============================================================== süzgeçler

def test_kategorisiz_oran_hicbir_urune_atanmamis_sayilir() -> None:
    rates = [_rate(1, "20.0000")]
    taxes.mark_flags(rates, [], [])
    assert rates[0]["unassigned"] is True
    assert "kategorisine bağlı değil" in rates[0]["unassignedReason"]


def test_urun_sayisi_taranmadiysa_atanmamis_DENMEZ() -> None:
    # Sıfır uydurmak, var olmayan bir soruna koşturur.
    rates = [_rate(1, "20.0000", categories=[5])]
    categories = [taxes.category_row({"id": 5, "code": "std", "name": "Standart",
                                      "tax_rates": [1]}, rates=rates)]
    taxes.mark_flags(rates, categories, [])
    assert rates[0]["unassigned"] is False
    assert rates[0]["unassignedReason"] == "Ürün sayısı henüz taranmadı."


def test_taranmis_ve_sifir_urunlu_kategorideki_oran_atanmamis_sayilir() -> None:
    rates = [_rate(1, "20.0000", categories=[5])]
    categories = [taxes.category_row({"id": 5, "code": "std", "name": "Standart",
                                      "tax_rates": [1]},
                                     usage={"product_count": 0, "scanned_at": "2026-08-13"},
                                     rates=rates)]
    taxes.mark_flags(rates, categories, [])
    assert rates[0]["unassigned"] is True


# ============================================================== bölgeler

def test_bolgeler_oranlardan_turetilir_ayri_kayit_yoktur() -> None:
    rates = [_rate(1, "20.0000"), _rate(2, "10.0000", state="34")]
    rows = taxes.region_rows(rates)
    assert [row["label"] for row in rows] == ["Türkiye", "Türkiye · 34"]
    assert rows[0]["rateCount"] == 1
    assert rows[1]["percentLabel"] == "%10"


# ========================================================== ürün eşlemesi

def test_vergi_kategorisi_alani_hic_yoksa_atanmamis_DENMEZ() -> None:
    # Ürünün kategorisi olabilir, biz görmüyoruzdur. "Atanmamış" demek yalandır.
    row = taxes.product_row({"id": 1, "sku": "A"}, categories={})
    assert row["taxKnown"] is False
    assert row["taxCategoryName"] == "Bilinmiyor"

    bos = taxes.product_row({"id": 1, "sku": "A", "tax_category_id": 0}, categories={})
    assert bos["taxKnown"] is True
    assert bos["taxCategoryName"] == "Atanmamış"


def test_camelCase_vergi_kategorisi_okunur() -> None:
    # Canlı ürün tekil ucu: {"taxCategoryId": 1, "urlKey": …}
    row = taxes.product_row({"id": 1, "sku": "A", "taxCategoryId": 5}, categories={5: "Standart"})
    assert row["taxKnown"] is True
    assert row["taxCategoryId"] == 5
    assert row["taxCategoryName"] == "Standart"


def test_eav_zarfindaki_vergi_kategorisi_de_bulunur() -> None:
    row = taxes.product_row({"id": 1, "values": {"common": {"sku": "A", "tax_category_id": 5}}},
                            categories={5: "Standart"})
    assert row["taxCategoryId"] == 5
    assert row["taxCategoryName"] == "Standart"


def test_kullanim_sayimi_alan_hic_gorulmediyse_sifir_uretmez() -> None:
    # CANLI: liste ucu `taxCategoryId` alanını hep `null` döndürüyor; sıfır
    # yazmak «hiçbir ürüne atanmamış» süzgecini bütün kategorilerde yakardı.
    counts, seen = taxes.usage_counts([{"id": 1, "taxCategoryId": None},
                                       {"id": 2, "sku": "B"}])
    assert seen is False
    assert counts == {}

    counts, seen = taxes.usage_counts([{"id": 1, "taxCategoryId": 5},
                                       {"id": 2, "taxCategoryId": 5},
                                       {"id": 3, "taxCategoryId": 0}])
    assert seen is True
    assert counts == {5: 2, 0: 1}


def test_fark_tablosu_zaten_o_kategoride_olani_atlar() -> None:
    rows = [taxes.product_row({"id": 1, "sku": "A", "tax_category_id": 5}, categories={5: "Std"}),
            taxes.product_row({"id": 2, "sku": "B", "tax_category_id": 0}, categories={})]
    diff = taxes.assign_rows(rows, category_id=5, category_name="Std")
    assert diff[0]["skipped"] is True
    assert diff[1]["skipped"] is False
    summary = taxes.assign_summary(diff)
    assert summary == {"total": 2, "changed": 1, "skipped": 1, "fromUnassigned": 1}


def test_urun_govdesi_oku_degistir_yaz_kurar_ve_aileyi_GONDERMEZ() -> None:
    # Canlı ürün yanıtı camelCase; gövde snake_case gitmeli.
    current = {"id": 1, "sku": "KLM-1", "name": "Kalem", "urlKey": "kalem", "status": 1,
               "visibleIndividually": True, "shortDescription": "kısa", "price": "2.0000",
               "taxCategoryId": 3, "attributeFamilyId": 2}
    body = taxes.product_body(current, tax_category_id=7, channel="default", locale="tr")
    assert body["tax_category_id"] == 7
    assert body["sku"] == "KLM-1"
    assert body["url_key"] == "kalem"
    assert body["short_description"] == "kısa"
    assert body["visible_individually"] == 1     # JSON boolean → 0/1
    assert body["price"] == "2.00"
    assert body["channel"] == "default"
    assert body["locale"] == "tr"
    assert "attribute_family_id" not in body
    assert "attributeFamilyId" not in body


# ============================================================= kategoriler

def test_kategori_oran_iliskisi_uc_bicimde_de_okunur() -> None:
    assert taxes.rate_ids_of({"tax_rates": [3, 1]}) == [1, 3]
    assert taxes.rate_ids_of({"taxrates": [{"id": 2}, {"id": 4}]}) == [2, 4]
    # Canlı biçim: camelCase + nesne listesi.
    assert taxes.rate_ids_of({"taxRates": [{"id": 1, "identifier": "KDV", "taxRate": 20}]}) == [1]
    assert taxes.rate_ids_of({}) == []


def test_iliski_null_ise_BILINMIYOR_bos_liste_ile_karistirilmaz() -> None:
    # Canlı liste ucu her kategori için "taxRates": null döndürüyor.
    assert taxes.rate_ids_known({"taxRates": [{"id": 1}]}) is True
    assert taxes.rate_ids_known({"taxRates": []}) is True
    assert taxes.rate_ids_known({"taxRates": None}) is False
    assert taxes.rate_ids_known({}) is False


def test_oransiz_kategori_kaydedilemez() -> None:
    problem = taxes.category_form_error({"code": "std", "name": "Standart", "taxrates": []})
    assert "en az bir oran" in problem


def test_kategori_govdesinde_oran_listesi_TAM_gonderilir() -> None:
    # Bagisto kısmi kabul etmiyor: gönderilmeyen oran kategoriden düşüyor.
    current = {"code": "std", "name": "Standart", "description": "", "tax_rates": [1, 2]}
    body = taxes.category_body(current, {"taxrates": [2]})
    assert body["taxrates"] == [2]
    body_unchanged = taxes.category_body(current, {"name": "Yeni"})
    assert body_unchanged["taxrates"] == [1, 2]


def test_sifir_ve_negatif_kimlik_reddedilir() -> None:
    # `PUT /rates/0` yolundan gelen 0, `if rate_id:` denetiminde "yeni kayıt"
    # gibi görünüyor ama `0 is None` yanlış olduğu için geçit onu güncelleme
    # sayıyor ve mağazaya `PUT /settings/tax-rates/0` gidiyordu.
    assert "geçersiz" in taxes.id_error(0, "Oran")
    assert "geçersiz" in taxes.id_error(-1, "Oran")
    assert "geçersiz" in taxes.id_error("", "Oran")
    assert "geçersiz" in taxes.id_error(None, "Oran")
    assert taxes.id_error(1, "Oran") == ""
    assert taxes.id_error("12", "Oran") == ""
