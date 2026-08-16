"""Saf kuralların testi — ağ yok, depo yok.

Buradaki her iddia sözleşmenin bir cümlesine bağlıdır
(`BLD/docs/control/products.md` + `00-genel.md`).
"""

from __future__ import annotations

from bld_products_backend import catalog as cat
from bld_products_fakes import CATEGORIES, PACKAGE_PRODUCT, PRODUCT


def test_gerekce_alt_ve_ust_sinir() -> None:
    assert cat.reason_error("kısa")
    assert cat.reason_error("A" * (cat.MAX_REASON + 1))
    assert cat.reason_error("Zam sonrası fiyat güncellendi") == ""
    # Üst sınır 500'dür, 160 DEĞİL: 160'lık sıkı sınır yalnız sipariş
    # revizyonu ve durum geçişindedir ve bu ekranın işi değildir.
    assert cat.MAX_REASON == 500


def test_ad_sinirlari_sozlesmeden() -> None:
    assert cat.name_error("A")
    assert cat.name_error("A" * 129)
    assert cat.name_error("Karnıyarık") == ""


def test_urun_satiri_turetilen_alanlari_tasir() -> None:
    row = cat.product_row(PRODUCT)
    assert row["menu_id"] == 27
    assert row["price_kurus"] == 9000          # KURUŞ, TL değil
    assert row["category_ids"] == [3]
    assert row["has_image"] is True
    assert row["sellable_today"] is True
    assert row["price_locked"] is False
    # `sold_out_reason` sunucuda `null`; ekranda boş dize olarak durur.
    assert row["sold_out_reason"] == ""


def test_paket_urununun_fiyat_alani_kilitli_isaretlenir() -> None:
    # Paket ürününün gerçek fiyatı GÜNÜN MENÜSÜNDEDİR; panel fiyat alanını
    # buna bakarak kapatır ve sunucu da yazmayı `422` ile reddeder.
    row = cat.product_row(PACKAGE_PRODUCT)
    assert row["is_package_product"] is True
    assert row["price_locked"] is True
    assert row["has_image"] is False


def test_tukenmis_urun_satilabilir_sayilmaz() -> None:
    row = cat.product_row({**PRODUCT, "sold_out_today": True})
    assert row["status"] is True
    assert row["sellable_today"] is False


def test_bozuk_satir_cokmez_varsayilana_duser() -> None:
    # Sunucu bir alanı `null` gönderebilir; ekranın çökmesi kabul edilemez.
    row = cat.product_row({"menu_id": "7", "name": None, "price_kurus": None})
    assert row["menu_id"] == 7
    assert row["name"] == ""
    assert row["price_kurus"] == 0
    assert row["minimum_qty"] == 1
    assert cat.product_row(None)["menu_id"] == 0


def test_kategori_kimlikleri_tekillestirilir_ve_sirasi_korunur() -> None:
    assert cat.category_ids([3, "4", 3, 0, None, 5]) == [3, 4, 5]
    assert cat.category_ids("3") == []          # dizi değilse boş liste


def test_secenekler_kimlikleriyle_aynen_tasinir() -> None:
    # `values[].id` sipariş revizyonundaki `option_value_ids` alanına doğrudan
    # konuyor; yeniden numaralamak siparişi bozardı.
    row = cat.option_row({"id": 7, "name": "Ekstra", "type": "checkbox",
                          "required": False,
                          "values": [{"id": 31, "name": "Ekstra pilav",
                                      "price_delta_kurus": 2500}]})
    assert row["id"] == 7
    assert row["values"][0]["id"] == 31
    assert row["values"][0]["price_delta_kurus"] == 2500


def test_kategori_agaci_derinlik_yazar_ve_oksuz_satiri_kaybetmez() -> None:
    rows = [cat.category_row(item) for item in CATEGORIES]
    tree = cat.category_tree(rows)
    assert [item["category_id"] for item in tree] == [3, 4, 5]
    assert [item["depth"] for item in tree] == [0, 0, 1]

    # Üst kategorisi listede olmayan satır KAYBOLMAZ, köke alınır: sessizce
    # düşürmek ekranda hiç görünmeyen ama sitede duran bir kategori bırakırdı.
    oksuz = [*rows, cat.category_row({"category_id": 9, "name": "Tatlı",
                                      "parent_id": 999, "priority": 5})]
    kimlikler = [item["category_id"] for item in cat.category_tree(oksuz)]
    assert 9 in kimlikler


def test_dongu_denetimi_kendisini_ve_alt_agacini_yakalar() -> None:
    rows = [cat.category_row(item) for item in CATEGORIES]
    assert cat.would_cycle(rows, 4, 4) is True          # kendisi
    assert cat.would_cycle(rows, 4, 5) is True          # kendi çocuğu
    assert cat.would_cycle(rows, 5, 3) is False         # başka bir kök
    assert cat.would_cycle(rows, 5, None) is False      # köke taşıma serbest


def test_suzgec_degerleri_sozlesmedeki_kumeye_indirilir() -> None:
    assert cat.clean_sort("fiyat") == "name"           # tanınmayan → varsayılan
    assert cat.clean_sort("price") == "price"
    assert cat.clean_direction("DESC") == "desc"
    assert cat.clean_per_page(500) == 100              # tavan sunucununki
    assert cat.clean_per_page(0) == cat.PER_PAGE_DEFAULT


def test_durum_suzgecinin_varsayilani_all() -> None:
    # `active` OLSAYDI satıştan kaldırılmış ürün ekrandan kaybolur ve
    # yönetimin ilk sorusu ("bu ürün nerede") cevapsız kalırdı.
    assert cat.clean_status("") == "all"
    assert cat.DEFAULT_STATUS == "all"


def test_bos_aciklama_null_olur() -> None:
    # Boş dize "boş bir açıklama", `null` "açıklama yok" demektir.
    assert cat.optional_text("  ") is None
    assert cat.optional_text(" abc ") == "abc"


def test_sayfa_kunyesi_eksik_meta_ile_sifir_yazmaz() -> None:
    # Dolu bir listenin altında "0 kayıt" yazan şerit, kullanıcıya kendi
    # gözüne inanmamasını söyler.
    meta = cat.page_meta({}, page=2, per_page=25, rows=7)
    assert meta["total"] == 32
    assert meta["last_page"] >= 2
    dolu = cat.page_meta({"page": 1, "per_page": 25, "total": 84, "last_page": 4},
                         page=1, per_page=25, rows=25)
    assert dolu["total"] == 84
    assert dolu["last_page"] == 4


def test_gorsel_kurallari_sozlesmedeki_uc_turu_tasir() -> None:
    rules = cat.image_rules()
    assert rules["accept"] == ["image/jpeg", "image/png", "image/webp"]
    assert rules["max_bytes"] == 5 * 1024 * 1024
    # Tek dosya: ürünün bir görseli var; çoklu seçim "ikincisi nereye gitti"
    # sorusunu doğururdu.
    assert rules["multiple"] is False


def test_as_bool_metin_sifiri_yanlis_sayar() -> None:
    # `bool("0")` Python'da True; tercih tablosundan metin okunuyor.
    assert cat.as_bool("0") is False
    assert cat.as_bool("true") is True
    assert cat.as_bool(0) is False
