"""Katalog dönüşümleri — saf mantık, ağa çıkmaz.

Buradaki her test bir TUZAĞA karşılık gelir; testin adı tuzağın kendisidir.
"""

from __future__ import annotations

from store_products_backend import catalog

# ================================================================== para

def test_ondalik_para_kurusa_cevrilirken_bir_kurus_kaybolmaz() -> None:
    # `float("1234.35") * 100` bazı değerlerde 123434.999… verir ve int() bir
    # kuruş aşağı yuvarlar. Decimal ile yuvarlama HALF_UP yapılır.
    assert catalog.to_kurus("1234.35") == 123435
    assert catalog.to_kurus("0.10") == 10
    assert catalog.to_kurus("8.615") == 862
    assert catalog.to_kurus(1234.35) == 123435


def test_bos_ve_bozuk_para_degeri_sifir_degil_none_dondurur() -> None:
    # 0 ile "fiyat girilmemiş" farklı şeylerdir: birincisi bedava, ikincisi bilinmiyor.
    assert catalog.to_kurus(None) is None
    assert catalog.to_kurus("") is None
    assert catalog.to_kurus("abc") is None
    assert catalog.to_kurus("0") == 0


def test_binlik_ayracli_yazim_da_okunur() -> None:
    assert catalog.to_kurus("1.250,00") == 125000
    assert catalog.to_kurus("1,250.00") == 125000
    assert catalog.to_kurus("1250,5") == 125050


def test_kurus_telde_ondalik_metne_doner() -> None:
    assert catalog.from_kurus(123435) == "1234.35"
    assert catalog.from_kurus(0) == "0.00"


# ============================================================= alan okuma

def test_oznitelik_duz_kayitta_da_eav_zarfinda_da_bulunur() -> None:
    duz = {"name": "Kalem", "sku": "KLM-1"}
    zarf = {"sku": "KLM-1", "values": {"common": {"name": "Kalem"}}}
    assert catalog.attribute(duz, "name") == "Kalem"
    assert catalog.attribute(zarf, "name") == "Kalem"
    assert catalog.attribute(zarf, "yok") is None


def test_bos_deger_ile_hic_olmayan_deger_ayrilir() -> None:
    kayit = {"meta_title": "", "values": {"common": {"meta_title": "Başlık"}}}
    # Boş olan ÜSTTE ama dolu olan tercih edilir; "boş" değeri ancak hiç dolu
    # yoksa döner.
    assert catalog.attribute(kayit, "meta_title") == "Başlık"
    assert catalog.attribute({"meta_title": ""}, "meta_title") == ""


# ================================================================== stok

def test_stok_urun_uzerinde_degil_envanter_kaynaklarinda_toplanir() -> None:
    kaynaklar = [{"inventory_source_id": 1, "qty": 12}, {"inventory_source_id": 2, "qty": 3}]
    assert catalog.stock_of(kaynaklar) == 15
    assert catalog.stock_of([]) == 0
    assert catalog.stock_of(None) == 0


def test_kaynak_satirlari_depo_adiyla_eslesir() -> None:
    rows = catalog.inventory_rows(
        [{"inventory_source_id": 4, "qty": 7}],
        [{"id": 4, "name": "Merkez depo"}],
    )
    assert rows == [{"sourceId": 4, "sourceName": "Merkez depo", "quantity": 7}]


def test_stok_durumu_esige_gore_belirlenir() -> None:
    assert catalog.stock_state(0, 5) == catalog.STOCK_OUT
    assert catalog.stock_state(3, 5) == catalog.STOCK_LOW
    assert catalog.stock_state(9, 5) == catalog.STOCK_IN
    assert catalog.stock_state(0, 5, manage_stock=False) == catalog.STOCK_OFF
    # Eşik 0 ise "kritik" kavramı yoktur; her pozitif adet stoktadır.
    assert catalog.stock_state(1, 0) == catalog.STOCK_IN


# ================================================================= fiyat

def test_indirim_tarih_penceresine_gore_gecerli_olur() -> None:
    assert catalog.special_state(900, "2026-08-01", "2026-08-31", "2026-08-13") == "active"
    assert catalog.special_state(900, "2026-09-01", "", "2026-08-13") == "scheduled"
    assert catalog.special_state(900, "", "2026-08-01", "2026-08-13") == "expired"
    assert catalog.special_state(None, "", "", "2026-08-13") == "none"
    # Pencere yoksa ve indirim varsa geçerlidir — Bagisto da böyle davranır.
    assert catalog.special_state(900, "", "", "2026-08-13") == "active"


def test_musterinin_odedigi_tutar_penceresi_kapaliyken_liste_fiyatidir() -> None:
    assert catalog.effective_price(1000, 900, "active") == 900
    assert catalog.effective_price(1000, 900, "expired") == 1000
    assert catalog.effective_price(1000, 900, "scheduled") == 1000


def test_fiyatin_uc_katmani_birden_cikarilir() -> None:
    raw = {"price": "100.00", "cost": "60.00", "special_price": "80.00",
           "special_price_from": "2026-08-01", "special_price_to": "2026-08-31"}
    gruplar = [{"id": 3, "customer_group_id": 2, "customer_group": {"name": "Bayi"},
                "qty": 10, "value_type": "fixed", "value": "70.00"}]
    view = catalog.price_view(raw, gruplar, today="2026-08-13")
    assert view["price"] == 10000
    assert view["specialPrice"] == 8000
    assert view["effective"] == 8000
    assert view["margin"] == 40.0
    assert view["groupPrices"][0]["groupName"] == "Bayi"
    assert view["groupPrices"][0]["value"] == 7000


def test_maliyet_yoksa_marj_sifir_degil_none_olur() -> None:
    assert catalog.margin_percent(10000, None) is None
    assert catalog.margin_percent(0, 5000) is None
    assert catalog.margin_percent(10000, 12000) == -20.0


# ============================================================ liste satırı

def test_liste_satiri_stogu_yaklasik_isaretler() -> None:
    raw = {"id": 7, "sku": "ABC", "name": "Deneme", "type": "simple", "status": 1,
           "price": "50.00", "quantity": 4}
    row = catalog.product_row(raw, threshold=5, today="2026-08-13")
    assert row["stock"] == 4
    assert row["stockExact"] is False      # vitrin alanı; kesin değil
    assert row["stockState"] == catalog.STOCK_LOW

    kesin = catalog.product_row(raw, threshold=5, today="2026-08-13",
                                inventories=[{"inventory_source_id": 1, "qty": 40}])
    assert kesin["stock"] == 40
    assert kesin["stockExact"] is True
    assert kesin["stockState"] == catalog.STOCK_IN


def test_liste_satirinda_tip_turkce_etiketiyle_gosterilir() -> None:
    row = catalog.product_row({"id": 1, "type": "configurable"}, threshold=5)
    assert row["typeLabel"] == "Varyantlı"
    assert row["name"] == "(adsız)"


# ======================================================== yazma gövdesi

def _urun() -> dict[str, object]:
    return {
        "id": 5, "sku": "KLM-1", "type": "simple", "attribute_family_id": 3,
        "name": "Kalem", "url_key": "kalem", "status": 1, "price": "10.00",
        "meta_title": "Kalem", "description": "uzun metin",
    }


def test_kismi_yama_dokunulmayan_alanlari_bosaltmaz() -> None:
    # TUZAK 1: yalnız `name` göndermek `url_key` ve `status` alanlarını
    # NULL'a düşürebiliyordu. Gövde MEVCUT değerlerden kurulur.
    body = catalog.write_body(_urun(), {"name": "Kurşun kalem"},
                              channel="default", locale="tr", sku="KLM-1")
    assert body["name"] == "Kurşun kalem"
    assert body["url_key"] == "kalem"
    assert body["status"] == 1
    assert body["description"] == "uzun metin"


def test_kanal_ve_dil_her_yazmada_gonderilir() -> None:
    body = catalog.write_body(_urun(), {}, channel="default", locale="tr", sku="KLM-1")
    assert body["channel"] == "default"
    assert body["locale"] == "tr"


def test_oznitelik_ailesi_asla_gonderilmez() -> None:
    # TUZAK 3: aile değişikliği ürünün öznitelik kümesini değiştirir; düzenleme
    # ekranının işi değildir ve kazayla gönderilmesi veri kaybıdır.
    body = catalog.write_body(_urun(), {"name": "X"}, channel="default", locale="tr",
                              sku="KLM-1")
    assert "attribute_family_id" not in body


def test_sku_yamadan_alinmaz_ayri_yolla_gelir() -> None:
    body = catalog.write_body(_urun(), {"sku": "YENI"}, channel="default", locale="tr",
                              sku="KLM-1")
    assert body["sku"] == "KLM-1"


def test_varyantli_urune_fiyat_yazilmaz() -> None:
    # TUZAK 10: configurable ürünün fiyatı varyantlarındadır; buraya yazılan
    # değer vitrinde görünmez ama raporlara girer.
    raw = {**_urun(), "type": "configurable"}
    body = catalog.write_body(raw, {"price": 12345}, channel="default", locale="tr",
                              sku="KLM-1")
    assert "price" not in body
    assert "special_price" not in body


def test_kurus_telde_ondalik_olarak_gider() -> None:
    body = catalog.write_body(_urun(), {"price": 12345}, channel="default", locale="tr",
                              sku="KLM-1")
    assert body["price"] == "123.45"


def test_bos_indirim_penceresi_none_gider_bos_metin_degil() -> None:
    raw = {**_urun(), "special_price_from": "2026-01-01"}
    body = catalog.write_body(raw, {"special_price_from": ""}, channel="default",
                              locale="tr", sku="KLM-1")
    assert body["special_price_from"] is None


def test_ekranin_camel_alanlari_bagisto_adlarina_cevrilir() -> None:
    patch = catalog.normalize_patch({"urlKey": "kalem", "specialFrom": "2026-01-01",
                                     "uydurma": 1})
    assert patch == {"url_key": "kalem", "special_price_from": "2026-01-01"}


# ============================================================== url_key

def test_url_anahtari_baskasinda_kullaniliyorsa_yakalanir() -> None:
    verdict = catalog.url_key_verdict("kalem", [{"id": 9, "sku": "X", "url_key": "kalem"}],
                                      product_id=5)
    assert verdict["state"] == "taken"
    assert verdict["takenBy"] == 9


def test_kendi_url_anahtari_cakisma_sayilmaz() -> None:
    verdict = catalog.url_key_verdict("kalem", [{"id": 5, "url_key": "kalem"}], product_id=5)
    assert verdict["state"] == "free"


def test_sunucu_suzgeci_yok_saymissa_bilinmiyor_denir() -> None:
    # TUZAK 6: Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar ve
    # tüm kataloğun ilk sayfasını döner. Onu "çakışma var" saymak doğru yazan
    # kullanıcıyı durdurur; "yok" saymak 422'yi kaydet düğmesine bırakır.
    verdict = catalog.url_key_verdict("kalem", [{"id": 1, "url_key": "defter"},
                                                {"id": 2, "url_key": "silgi"}])
    assert verdict["state"] == "unknown"


def test_hic_kayit_donmediyse_anahtar_serbesttir() -> None:
    assert catalog.url_key_verdict("yeni-anahtar", [])["state"] == "free"


def test_suzgec_uygulandi_mi_sorusunun_ucuncu_cevabi_vardir() -> None:
    assert catalog.filter_honored([{"categoryIds": [3, 4]}], "categoryIds", 3) is True
    assert catalog.filter_honored([{"categoryIds": [9]}], "categoryIds", 3) is False
    # Satırlar o alanı hiç taşımıyorsa ya da boş taşıyorsa "uygulandı" DENEMEZ:
    # bazı liste uçları kategori bilgisini hiç döndürmüyor ve boş listeyi
    # "kategoride değil" saymak boş yere uyarı çıkarırdı.
    assert catalog.filter_honored([{"id": 1}], "categoryIds", 3) is None
    assert catalog.filter_honored([{"categoryIds": []}], "categoryIds", 3) is None
    assert catalog.filter_honored([], "categoryIds", 3) is None


# ================================================================= slug

def test_turkce_harfler_slugta_kaybolmaz() -> None:
    assert catalog.slugify("Işık Isı Ölçer") == "isik-isi-olcer"
    assert catalog.slugify("9. Sınıf  Matematik") == "9-sinif-matematik"
    assert catalog.slugify("  ") == ""


# ============================================================ toplu işlem

def test_yuzde_indirimi_ve_kurus_yuvarlamasi() -> None:
    assert catalog.apply_price_rule(10000, mode="percent", amount=-10) == 9000
    assert catalog.apply_price_rule(10000, mode="amount", amount=-1500) == 8500
    assert catalog.apply_price_rule(10000, mode="set", amount=19990) == 19990
    # Negatife düşen fiyat sıfırlanır, eksi fiyat yazılmaz.
    assert catalog.apply_price_rule(1000, mode="amount", amount=-5000) == 0


def test_yuvarlama_bicimleri() -> None:
    assert catalog.round_price(12345, "whole") == 12300
    assert catalog.round_price(12345, "penny99") == 12299
    assert catalog.round_price(12345, "half") == 12350
    assert catalog.round_price(12345, "none") == 12345


def test_fark_tablosu_varyantli_urunu_atlar() -> None:
    rows = [
        {"id": 1, "sku": "A", "name": "Basit", "type": "simple", "price": 10000},
        {"id": 2, "sku": "B", "name": "Varyantlı", "type": "configurable", "price": 10000},
    ]
    diff = catalog.bulk_price_rows(rows, mode="percent", amount=10)
    assert diff[0]["after"] == 11000
    assert diff[0]["delta"] == 1000
    assert diff[1]["skipped"] is True
    assert diff[1]["after"] == diff[1]["before"]


def test_toplu_stok_farki_negatife_dusmez() -> None:
    rows = [{"id": 1, "sku": "A", "name": "A", "stock": 3, "stockExact": True}]
    assert catalog.bulk_stock_rows(rows, mode="add", amount=-10)[0]["after"] == 0
    assert catalog.bulk_stock_rows(rows, mode="set", amount=25)[0]["after"] == 25


def test_yaklasik_stok_toplu_uygulamadan_cikarilir() -> None:
    # Vitrin değeri yanlış olabilir; ona dayanarak mutlak stok yazmak veri bozar.
    rows = [{"id": 1, "sku": "A", "name": "A", "stock": 3, "stockExact": False}]
    assert catalog.bulk_stock_rows(rows, mode="set", amount=9)[0]["skipped"] is True


def test_zaten_kategoride_olan_urun_atlanir() -> None:
    rows = [{"id": 1, "sku": "A", "name": "A", "categoryIds": [4]},
            {"id": 2, "sku": "B", "name": "B", "categoryIds": []}]
    diff = catalog.bulk_category_rows(rows, action="add", category_id=4, category_name="Kitap")
    assert diff[0]["skipped"] is True
    assert diff[1]["categoryIds"] == [4]


def test_fark_ozeti_artan_azalan_ve_atlanani_sayar() -> None:
    diff = [
        {"delta": 100, "skipped": False},
        {"delta": -50, "skipped": False},
        {"delta": 0, "skipped": False},
        {"delta": 999, "skipped": True},
    ]
    summary = catalog.diff_summary(diff)
    assert summary == {"total": 4, "changed": 2, "skipped": 1, "up": 1, "down": 1,
                       "netDelta": 50}


# =============================================================== gerekçe

def test_kisa_gerekce_reddedilir() -> None:
    assert catalog.reason_error("ok") != ""
    assert catalog.reason_error("   ") != ""
    assert catalog.reason_error("Fiyat listesi güncellendi") == ""


# ================================================================= ağaç

def test_kategori_agaci_girintili_duz_listeye_iner() -> None:
    tree = [{"id": 1, "name": "Kitap", "children": [
        {"id": 2, "name": "Test", "children": [{"id": 3, "name": "TYT"}]},
    ]}]
    options = catalog.category_options(tree)
    assert [item["id"] for item in options] == [1, 2, 3]
    assert options[2]["label"] == "— — TYT"
    assert options[2]["depth"] == 2
