"""Set hesabının saf mantığı — ağa çıkmaz, sahte bile gerekmez.

Bu ekranın var olma sebebi tek bir soru: "bu seti bu fiyata satarsak kâr mı
ediyoruz?". Aşağıdaki testlerin adları o sorunun yanlış cevaplandığı yolları
sayar.
"""

from __future__ import annotations

from typing import Any

from store_bundles_backend import bundles


def bilesen(product_id: int, *, qty: int = 1, discount: int = 0, required: bool = True,
            unit: int | None = 10_000, cost: int | None = 6_000, stock: int = 10,
            status: bool = True, missing: bool = False) -> dict[str, Any]:
    """Birleştirilmiş bileşen satırı (tanım + mağaza verisi)."""
    return {"productId": product_id, "sku": f"SKU-{product_id}", "name": f"Ürün {product_id}",
            "qty": qty, "discount": discount, "required": required, "unitPrice": unit,
            "cost": cost, "stock": stock, "status": status, "missing": missing}


# ===================================================================== para

def test_para_kurusa_cevrilirken_bir_kurus_asagi_kaymaz() -> None:
    # `float("1234.35") * 100` bazı değerlerde 123434.99999 verir ve int() bir
    # kuruş aşağı yuvarlar; 1.419 üründe binde bir görünen, bulunması imkânsız
    # bir sapmadır. Decimal ile çevriliyor.
    assert bundles.to_kurus("1234.35") == 123_435
    assert bundles.to_kurus("1.250,00") == 125_000
    assert bundles.to_kurus("0.10") == 10


def test_maliyeti_girilmemis_bilesen_sifir_maliyet_sayilmaz() -> None:
    assert bundles.to_kurus(None) is None
    assert bundles.to_kurus("") is None
    assert bundles.to_kurus("0") == 0        # "sıfır" ile "yok" ayrı şeyler


# ============================================================ bileşen tanımı

def test_ayni_urun_iki_kez_verilirse_adetler_birlestirilir() -> None:
    rows = bundles.normalize_components([
        {"productId": 7, "qty": 2}, {"productId": 7, "qty": 3, "required": False},
    ])
    assert len(rows) == 1
    assert rows[0]["qty"] == 5
    assert rows[0]["required"] is True        # zorunluluk kaybolmaz


def test_urunsuz_satir_ve_asiri_bilesen_sessizce_kirpilir() -> None:
    rows = bundles.normalize_components(
        [{"qty": 3}, *[{"productId": index} for index in range(1, 40)]])
    assert len(rows) == bundles.MAX_COMPONENTS


def test_bilesen_tanimi_fiyat_tasimaz() -> None:
    # Fiyat tanımın parçası olsaydı, kaydedildiği günün fiyatı bugünün fiyatı
    # gibi gösterilirdi. Tanım yalnız ürün/adet/indirim/zorunluluk taşır.
    row = bundles.normalize_component({"productId": 4, "unitPrice": 999_999, "cost": 1})
    assert set(row) == {"productId", "qty", "discount", "required"}


def test_okunamayan_urun_bedava_bilesen_olmaz() -> None:
    row = bundles.merge_component({"productId": 9, "qty": 2, "discount": 0, "required": True},
                                  None)
    assert row["missing"] is True
    assert row["unitPrice"] is None
    assert row["cost"] is None


# ================================================================== hesap

def test_canli_hesap_kutusu_bilesenden_kara_kadar_yurur() -> None:
    rows = [bilesen(1, qty=2), bilesen(2, unit=5_000, cost=3_000, discount=10)]
    calc = bundles.compute(rows, pricing_mode="fixed", set_price=20_000, tax_rate=20)

    assert calc["componentsTotal"] == 25_000          # 2×100,00 + 1×50,00
    assert calc["componentDiscount"] == 500           # bileşen indirimi %10 × 50,00
    assert calc["afterComponentDiscount"] == 24_500
    assert calc["setPrice"] == 20_000
    assert calc["setDiscount"] == 4_500               # müşterinin kazancı
    assert calc["savingsPercent"] == 18.4
    assert calc["netPrice"] == 16_667                 # KDV dahil fiyattan çıkarıldı
    assert calc["tax"] == 3_333
    assert calc["costTotal"] == 15_000
    assert calc["profit"] == 1_667
    assert calc["state"] == "profit"


def test_kdv_dahil_fiyat_maliyetle_ayni_torbaya_konmaz() -> None:
    # Vitrin fiyatı KDV dahil, Bagisto `cost` KDV hariç. İkisini doğrudan
    # çıkarmak seti %20 daha kârlı gösterirdi.
    rows = [bilesen(1, unit=12_000, cost=10_000)]
    calc = bundles.compute(rows, set_price=12_000, tax_rate=20, prices_include_tax=True)
    assert calc["netPrice"] == 10_000
    assert calc["profit"] == 0
    assert calc["state"] == "even"

    haric = bundles.compute(rows, set_price=12_000, tax_rate=20, prices_include_tax=False)
    assert haric["netPrice"] == 12_000
    assert haric["tax"] == 2_400
    assert haric["profit"] == 2_000


def test_zararina_satilan_set_isaretlenir() -> None:
    rows = [bilesen(1, qty=2)]                       # maliyet 2×60,00 = 120,00
    calc = bundles.compute(rows, set_price=10_000, tax_rate=20)
    assert calc["profit"] < 0
    assert calc["state"] == "loss"
    assert bundles.flags(calc, rows)["loss"] is True
    assert "zararına" in bundles.warning_text(bundles.flags(calc, rows))


def test_maliyet_bilinmiyorsa_karli_denmez_zararli_da_denmez() -> None:
    rows = [bilesen(1), bilesen(2, cost=None)]
    calc = bundles.compute(rows, set_price=100_000, tax_rate=20)
    assert calc["profit"] is None
    assert calc["costTotal"] is None
    assert calc["state"] == "unknown"
    assert calc["unknownCosts"] == ["SKU-2"]
    assert bundles.flags(calc, rows)["loss"] is False


def test_yuzde_kipinde_set_fiyati_bilesen_toplamindan_turer() -> None:
    rows = [bilesen(1, qty=2), bilesen(2, unit=5_000, discount=10)]
    calc = bundles.compute(rows, pricing_mode="percent", discount_percent=20)
    assert calc["afterComponentDiscount"] == 24_500
    assert calc["setPrice"] == 19_600              # %20 indirim
    assert calc["setDiscount"] == 4_900


def test_set_fiyati_yoksa_kar_hesaplanmaz_sifir_uydurulmaz() -> None:
    calc = bundles.compute([bilesen(1)], pricing_mode="fixed", set_price=None)
    assert calc["setPrice"] is None
    assert calc["profit"] is None
    assert calc["state"] == "unknown"


def test_opsiyonel_bilesen_set_fiyatinin_icinde_degildir() -> None:
    rows = [bilesen(1), bilesen(2, unit=5_000, cost=3_000, required=False)]
    calc = bundles.compute(rows, pricing_mode="percent", discount_percent=0)
    assert calc["componentsTotal"] == 10_000       # yalnız zorunlu olan
    assert calc["optionalTotal"] == 5_000
    assert calc["costTotal"] == 6_000
    assert calc["optionalCount"] == 1


# ================================================================== stok

def test_set_stogunu_en_kisitli_zorunlu_bilesen_belirler() -> None:
    rows = [bilesen(1, qty=2, stock=9), bilesen(2, stock=3)]
    limit, culprit = bundles.stock_limit(rows)
    assert limit == 3                              # 9//2 = 4 değil; ikinci bileşen 3
    assert culprit == "Ürün 2"


def test_opsiyonel_bilesenin_stogu_seti_kisitlamaz() -> None:
    rows = [bilesen(1, stock=8), bilesen(2, stock=0, required=False)]
    limit, _ = bundles.stock_limit(rows)
    assert limit == 8


def test_okunamayan_bilesen_sinirsiz_stok_saydirmaz() -> None:
    limit, culprit = bundles.stock_limit([bilesen(1, missing=True)])
    assert limit is None
    assert culprit == "SKU-1"


def test_tukenmis_ve_pasif_bilesen_ayri_ayri_isaretlenir() -> None:
    rows = [bilesen(1, stock=0), bilesen(2, status=False)]
    marks = bundles.flags(bundles.compute(rows, set_price=10_000), rows)
    assert marks["outOfStock"] is True
    assert marks["passive"] is True
    metin = bundles.warning_text(marks)
    assert "bileşeni tükenmiş" in metin
    assert "bileşeni pasif" in metin


def test_adedi_karsilamayan_stok_da_tukenmis_sayilir() -> None:
    # 3 adet isteyen bir bileşenden depoda 2 varsa set satılamaz; "stokta var"
    # demek siparişi kabul edip sonra iptal etmek olurdu.
    rows = [bilesen(1, qty=3, stock=2)]
    assert bundles.flags(bundles.compute(rows, set_price=10_000), rows)["outOfStock"] is True


# ============================================================== geçerlilik

def test_gecerlilik_penceresi_dort_durum_uretir() -> None:
    assert bundles.validity_state("", "", "2026-08-13") == "always"
    assert bundles.validity_state("2026-09-01", "", "2026-08-13") == "scheduled"
    assert bundles.validity_state("2026-08-01", "2026-08-31", "2026-08-13") == "active"
    assert bundles.validity_state("", "2026-08-01", "2026-08-13") == "expired"


# =================================================================== sıra

def test_sorunlu_setler_listenin_basina_cikar() -> None:
    # Kullanıcı bu ekrana "hangi set beni yakıyor" diye geliyor; alfabetik sıra
    # o soruyu cevaplamıyor.
    saglikli = {"name": "A seti", "flags": {"loss": False, "outOfStock": False,
                                            "passive": False}, "calc": {"profit": 5_000}}
    zararli = {"name": "Z seti", "flags": {"loss": True, "outOfStock": False,
                                           "passive": False}, "calc": {"profit": -1_000}}
    assert min([saglikli, zararli], key=bundles.sort_key)["name"] == "Z seti"


# =================================================================== gerekçe

def test_kisa_gerekce_reddedilir() -> None:
    assert bundles.reason_error("ok") != ""
    assert bundles.reason_error("Yaz kampanyası seti açıldı") == ""


# ============================================================== ürün okuma

def test_eav_zarfli_kayittan_da_ozellik_okunur() -> None:
    raw = {"id": 3, "values": {"common": {"sku": "SET-1", "name": "Deneme Seti", "status": 1,
                                          "price": "250.00"}}}
    view = bundles.product_view(raw, today="2026-08-13")
    assert view["sku"] == "SET-1"
    assert view["effectivePrice"] == "250.00"


def test_suresi_gecmis_indirim_gecerli_sayilmaz() -> None:
    raw = {"id": 3, "sku": "A", "name": "A", "status": 1, "price": "100.00",
           "special_price": "70.00", "special_price_to": "2026-01-01"}
    view = bundles.product_view(raw, today="2026-08-13")
    assert view["effectivePrice"] == "100.00"
    assert view["specialActive"] is False


def test_gecerli_indirim_bilesen_fiyatina_yansir() -> None:
    raw = {"id": 3, "sku": "A", "name": "A", "status": 1, "price": "100.00",
           "special_price": "70.00", "special_price_from": "2026-08-01"}
    view = bundles.product_view(raw, today="2026-08-13")
    assert view["effectivePrice"] == "70.00"


def test_maliyet_attributes_dizisinden_okunur() -> None:
    """CANLIDA MALİYETİN TEK YERİ BURASI.

    13.08.2026'da `GET /api/admin/catalog/products/17` yanıtının gövdesinde
    `cost` alanı YOK; maliyet `attributes` dizisinde `{"code": "cost",
    "value": "385.0000"}` olarak geliyor. Gövdeden okuyan sürüm bütün
    setlerin kârını “—” gösteriyordu ve hiçbir test bunu yakalamıyordu.
    """
    raw = {"id": 17, "sku": "BBD2026SKU0010", "name": "Geometri SB", "status": 1,
           "price": "462", "quantity": 20,
           "attributes": [{"id": 11, "code": "price", "value": "462.0000"},
                          {"id": 12, "code": "cost", "value": "385.0000"}]}
    view = bundles.product_view(raw, today="2026-08-13")
    assert bundles.to_kurus(view["cost"]) == 38_500
    assert view["effectivePrice"] == "462.00"


def test_camelcase_alanlar_da_okunur() -> None:
    # Canlı yanıt camelCase konuşuyor: `specialPrice`, `baseImageUrl`, `urlKey`.
    # snake_case'e bakan sürüm indirimi ve görseli sessizce kaçırıyordu.
    raw = {"id": 1428, "sku": "A", "name": "A", "status": 1, "price": "2.0000",
           "specialPrice": "1.0000", "specialPriceFrom": "2026-08-01",
           "specialPriceTo": "2026-12-31", "quantity": 94,
           "baseImageUrl": "https://bbdstore.com.tr/storage/product/1428/a.png",
           "urlKey": "test-urunudur"}
    view = bundles.product_view(raw, today="2026-08-13")
    assert view["effectivePrice"] == "1.00"
    assert view["specialActive"] is True
    assert view["imageUrl"].endswith("/a.png")
    assert view["urlKey"] == "test-urunudur"


def test_govdedeki_bos_alan_attributes_dizisindeki_doluyu_ezmez() -> None:
    # Canlı ürün 1427: gövdede `specialPrice: null`, dizide `special_price:
    # "2363.0000"`. Boş olanı önce kabul etmek indirimi görünmez yapardı.
    raw = {"id": 1427, "sku": "BBD-SET-GEO-01", "name": "Geometri Seti", "status": 1,
           "price": "2780", "specialPrice": None,
           "attributes": [{"code": "special_price", "value": "2363.0000"},
                          {"code": "price", "value": "2780.0000"}]}
    view = bundles.product_view(raw, today="2026-08-13")
    assert view["effectivePrice"] == "2363.00"


def test_stok_envanter_kaynaklarindan_toplanir() -> None:
    raw = {"id": 3, "sku": "A", "name": "A", "status": 1, "price": "1.00", "quantity": 99}
    view = bundles.product_view(raw, inventories=[{"qty": 4}, {"qty": 6}])
    assert view["stock"] == 10          # vitrin alanı (99) değil
    assert view["stockExact"] is True


def test_satir_toplamlari_hesapla_birlikte_doner() -> None:
    # Ekran satır toplamını ikinci kez hesaplasaydı iki ayrı yuvarlama kuralı
    # doğar ve bileşen tablosu ile hesap kutusu birbirini tutmazdı.
    calc = bundles.compute([bilesen(1, qty=2, discount=10)], set_price=15_000)
    satir = calc["lines"][0]
    assert satir["lineGross"] == 20_000
    assert satir["lineDiscount"] == 2_000
    assert satir["lineNet"] == 18_000
    assert calc["afterComponentDiscount"] == satir["lineNet"]


def test_kdv_sifir_belirtilmemis_sayilmaz() -> None:
    # Kitapta yasal KDV %0/%1 olabiliyor. Sıfırı "belirtilmemiş" sayıp modül
    # ayarındaki %20'ye düşmek kârı yanlış hesaplardı.
    assert bundles.definition_row({"productId": 1, "taxRate": 0})["taxRate"] == 0.0
    assert bundles.definition_row({"productId": 1})["taxRate"] is None
    assert bundles.compute([bilesen(1)], set_price=10_000, tax_rate=0)["netPrice"] == 10_000


def test_set_fiyati_bilesen_toplaminin_ustundeyse_indirim_eksiye_duser() -> None:
    """Set indirimi NEGATİF olabilir: set fiyatı bileşen toplamının üstünde.

    Hesap bunu gizlemez, işareti veriye bırakır. Ekranın başına sabit “−”
    koyması bu satırı “− −50,00 ₺” diye yazdırıyor, kazanç ipucu da
    “müşteri %-25,0 kazanıyor” çıkıyordu; panel artık işarete bakıyor
    (`renderCalc` · Set farkı).
    """
    calc = bundles.compute([bilesen(1, unit=10_000, cost=6_000)], set_price=12_500)
    assert calc["afterComponentDiscount"] == 10_000
    assert calc["setDiscount"] == -2_500
    assert calc["savingsPercent"] == -25.0


def test_bilesen_indirimi_kurus_yuvarlamayi_bir_kez_yapar() -> None:
    # Satır indirimi ile toplam indirim iki ayrı yuvarlamadan geçseydi tablo
    # ile hesap kutusu birbirini tutmazdı.
    rows = [bilesen(1, unit=3_333, discount=33), bilesen(2, unit=3_333, discount=33)]
    calc = bundles.compute(rows, set_price=5_000)
    assert [line["lineDiscount"] for line in calc["lines"]] == [1_100, 1_100]
    assert calc["componentDiscount"] == 2_200
    assert calc["afterComponentDiscount"] == 6_666 - 2_200
