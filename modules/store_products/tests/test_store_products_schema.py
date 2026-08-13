"""Nitelik ve aile mantığı — saf dönüşümler, ağa çıkmaz.

Örnek kayıtlar CANLI uçtan alınmış biçimdedir (camelCase, `isRequired: 0/1`).
Uydurma bir biçime göre yazılmış test, gerçek yanıtta sessizce boş satır
üretir.
"""

from __future__ import annotations

from typing import Any

from store_products_backend import schema

# GET /api/admin/catalog/attributes — canlı biçim (2026-08-13).
YAYINEVI = {
    "id": 33, "code": "publisher", "type": "select", "adminName": "Yayınevi",
    "isRequired": 1, "isUnique": 0, "valuePerLocale": 0, "valuePerChannel": 0,
    "isFilterable": 1, "isConfigurable": 0, "isVisibleOnFront": 1, "isUserDefined": 1,
    "position": 1, "options": [
        {"id": 21, "adminName": "ACİL YAYINLARI", "sortOrder": 100},
        {"id": 10, "adminName": "Benim Başarı Dünyam", "sortOrder": 1},
    ],
}
SKU = {"id": 1, "code": "sku", "type": "text", "adminName": "Stok Kodu",
       "isRequired": 1, "isUserDefined": 0}
DESI = {"id": 39, "code": "desi", "type": "text", "adminName": "Desi", "isUserDefined": 1}

# GET /api/admin/catalog/families/{id} — canlı biçim.
KITAP: dict[str, Any] = {
    "id": 2, "code": "kitap", "name": "Kitap",
    "attributeGroups": [
        {"id": 13, "code": "price", "name": "Fiyat", "column": 2, "position": 0,
         "attributes": [{"id": 11, "code": "price", "type": "price", "isRequired": 1,
                         "position": 1}]},
        {"id": 10, "code": "general", "name": "Genel", "column": 1, "position": 0,
         "attributes": [{"id": 1, "code": "sku", "type": "text", "isRequired": 1,
                         "position": 1},
                        {"id": 33, "code": "publisher", "type": "select", "isRequired": 1,
                         "position": 2}]},
    ],
}
VARSAYILAN: dict[str, Any] = {
    "id": 1, "code": "default", "name": "Varsayılan",
    "attributeGroups": [
        {"id": 1, "code": "general", "name": "Genel", "column": 1, "position": 0,
         "attributes": [{"id": 1, "code": "sku", "type": "text", "isRequired": 1}]},
    ],
}


# ============================================================ satır çevirisi

def test_camelcase_alanlar_okunur() -> None:
    row = schema.attribute_row(YAYINEVI)
    assert row["code"] == "publisher"
    assert row["name"] == "Yayınevi"
    assert row["typeLabel"] == "Tek seçim"
    assert row["required"] is True
    assert row["filterable"] is True
    assert row["system"] is False
    assert row["hasOptions"] is True
    assert row["optionCount"] == 2


def test_snake_case_yanit_da_okunur() -> None:
    # Aynı kayıt başka bir sürümde snake_case gelebiliyor; ekran ikisini de
    # bilmek zorunda kalmasın.
    row = schema.attribute_row({"id": 33, "code": "publisher", "type": "select",
                                "admin_name": "Yayınevi", "is_required": 1,
                                "is_user_defined": 0})
    assert row["name"] == "Yayınevi"
    assert row["required"] is True
    assert row["system"] is True


def test_kod_ve_tip_her_zaman_kilitli_isaretlenir() -> None:
    assert schema.attribute_row(DESI)["locked"] is True
    assert schema.attribute_row(SKU)["locked"] is True


def test_secenekler_sirasina_gore_gelir() -> None:
    rows = schema.option_rows(YAYINEVI)
    assert [row["name"] for row in rows] == ["Benim Başarı Dünyam", "ACİL YAYINLARI"]


# ================================================================= kullanım

def test_kullanim_ailelerdeki_urun_sayisinin_toplamidir() -> None:
    index = schema.usage_index([KITAP, VARSAYILAN], {2: 1420, 1: 1})
    # `sku` iki ailede de var: 1420 + 1.
    assert index[1]["products"] == 1421
    assert sorted(index[1]["families"]) == ["Kitap", "Varsayılan"]
    # `publisher` yalnız Kitap ailesinde.
    assert index[33]["products"] == 1420
    assert index[33]["families"] == ["Kitap"]


def test_bir_ailenin_sayisi_okunamazsa_kullanim_BILINMIYOR_olur() -> None:
    # Eksik toplamı "kullanım" diye göstermek, silme kapısını yanlış açardı.
    index = schema.usage_index([KITAP, VARSAYILAN], {1: 1})
    assert index[1]["products"] is None
    assert index[33]["products"] is None


def test_kullanim_verilmezse_sifir_degil_bilinmiyor_doner() -> None:
    assert schema.attribute_row(DESI)["usageProducts"] is None


# =============================================================== doğrulama

def test_rezerve_kelime_kod_olarak_kullanilamaz() -> None:
    assert "rezerve" in schema.code_error("type")
    assert "rezerve" in schema.code_error("attribute_family_id")


def test_kod_deseni_zorlanir() -> None:
    assert schema.code_error("Raf Kodu")
    assert schema.code_error("9raf")
    assert schema.code_error("a")
    assert schema.code_error("raf_kodu") == ""


def test_bos_kod_reddedilir_ve_degistirilemezligi_soylenir() -> None:
    assert "DEĞİŞTİRİLEMEZ" in schema.code_error("")


def test_bilinmeyen_tip_reddedilir() -> None:
    assert schema.type_error("renk")
    assert schema.type_error("multiselect") == ""


def test_kod_degistirme_denemesi_backendde_de_durdurulur() -> None:
    # Ekran alanı kilitler ama kilit arayüzdedir; istek elle kurulabilir (K9).
    message = schema.locked_error(YAYINEVI, {"code": "yayinevi"})
    assert "kodu oluşturulduktan sonra değiştirilemez" in message


def test_tip_degistirme_denemesi_backendde_de_durdurulur() -> None:
    message = schema.locked_error(YAYINEVI, {"type": "text"})
    assert "tipi oluşturulduktan sonra değiştirilemez" in message


def test_ayni_kod_ve_tip_gonderilmesi_engel_degildir() -> None:
    assert schema.locked_error(YAYINEVI, {"code": "publisher", "type": "select"}) == ""


# ================================================== silme yerine pasifleştirme

def test_sistem_niteligi_silinemez() -> None:
    verdict = schema.delete_verdict(schema.attribute_row(SKU))
    assert verdict["allowed"] is False
    assert "SİSTEM" in verdict["reason"]
    assert "pasifleştirin" in verdict["alternative"].lower()


def test_kullanimdaki_nitelik_silinemez_ve_kac_urun_oldugu_soylenir() -> None:
    row = schema.attribute_row(YAYINEVI, usage={"products": 1420, "families": ["Kitap"]})
    verdict = schema.delete_verdict(row)
    assert verdict["allowed"] is False
    assert "1420" in verdict["reason"]
    assert "geri alınamaz" in verdict["reason"]


def test_kullanimi_bilinmeyen_nitelik_silinemez() -> None:
    # Belirsizlik "evet" değil "hayır"dır: bilinmeyeni sıfır saymak veri kaybı.
    # `families_known` verilmedi → aile düzeni okunamadı demektir.
    verdict = schema.delete_verdict(schema.attribute_row(DESI))
    assert verdict["allowed"] is False
    assert "okunamadı" in verdict["reason"]


def test_hicbir_ailede_olmayan_nitelik_silinebilir() -> None:
    # TEST DÜZELTİLDİ (eski hâli `usage={"products": 0, "families": []}` veriyordu).
    # O şekil `usage_index`ten ÇIKAMAZ: kullanım kaydı yalnız niteliğin bir
    # ailenin grubunda görülmesiyle açılır, dolayısıyla `families` hiç boş
    # olmaz. Boş kullanımın anlamı "hiçbir ailede yok"tur ve bu ancak aile
    # düzeninin TAMAMI okunabildiyse bilgidir — sinyal `families_known`.
    row = schema.attribute_row(DESI)
    assert schema.delete_verdict(row, families_known=True)["allowed"] is True


def test_urunsuz_ailedeki_nitelik_silinebilir_ve_hangi_aile_soylenir() -> None:
    # Nitelik bir ailede tanımlı ama o ailede hiç ürün yok: silmek ürün verisi
    # götürmez. Kullanıcı yine de hangi ailenin şemasından düşeceğini görür.
    row = schema.attribute_row(DESI, usage={"products": 0, "families": ["Boş Aile"]})
    verdict = schema.delete_verdict(row, families_known=True)
    assert verdict["allowed"] is True
    assert "Boş Aile" in verdict["reason"]


def test_ailesi_bilinen_ama_urun_sayisi_bilinmeyen_nitelik_silinemez() -> None:
    row = schema.attribute_row(DESI, usage={"products": None, "families": ["Kitap"]})
    verdict = schema.delete_verdict(row, families_known=True)
    assert verdict["allowed"] is False
    assert "Kitap" in verdict["reason"]


def test_pasiflestirme_yalniz_bayraklari_indirir_veri_silmez() -> None:
    patch = schema.deactivate_patch(schema.attribute_row(YAYINEVI))
    assert patch == {"is_visible_on_front": 0, "is_filterable": 0, "is_required": 0}
    # Değer taşıyan hiçbir alan gövdeye girmez.
    assert "code" not in patch
    assert "type" not in patch


def test_zaten_pasif_nitelikte_yapacak_is_yoktur() -> None:
    row = schema.attribute_row(DESI)
    assert schema.deactivate_patch(row) == {}
    assert "zaten pasif" in schema.deactivate_summary(row)


def test_pasiflestirme_ozeti_ne_olacagini_tek_tek_yazar() -> None:
    summary = schema.deactivate_summary(schema.attribute_row(YAYINEVI))
    assert "vitrinde görünmeyecek" in summary
    assert "süzgeçlerden çıkacak" in summary
    assert "SİLİNMEZ" in summary


# ============================================================ yazma gövdesi

def test_guncelleme_govdesinde_kod_ve_tip_YOKTUR() -> None:
    body = schema.attribute_body({"name": "Yayın Evi", "code": "x", "type": "text",
                                  "filterable": True}, creating=False)
    assert body == {"admin_name": "Yayın Evi", "is_filterable": 1}


def test_olusturma_govdesine_kod_ve_tip_konur() -> None:
    body = schema.attribute_body({"name": "Raf"}, creating=True, code="raf_kodu", kind="text")
    assert body["code"] == "raf_kodu"
    assert body["type"] == "text"
    assert body["admin_name"] == "Raf"


def test_bayraklar_0_1_olarak_gider() -> None:
    body = schema.attribute_body({"required": False, "perChannel": True}, creating=False)
    assert body == {"is_required": 0, "value_per_channel": 1}


# ==================================================================== aile

def test_aile_listesinde_grup_sayisi_bilinmiyor_olarak_gelir() -> None:
    # Liste ucu `attributeGroups` alanını NULL veriyor (canlıda doğrulandı);
    # sıfır göstermek "bu ailede grup yok" yalanı olurdu.
    row = schema.family_row({"id": 2, "code": "kitap", "name": "Kitap",
                             "attributeGroups": None}, product_count=1420)
    assert row["groupCount"] is None
    assert row["attributeCount"] is None
    assert row["productCount"] == 1420


def test_aile_detayinda_gruplar_ve_nitelikler_sirali_gelir() -> None:
    detail = schema.family_detail(KITAP)
    assert [group["name"] for group in detail["groups"]] == ["Fiyat", "Genel"]
    genel = detail["groups"][1]
    assert [item["code"] for item in genel["attributes"]] == ["sku", "publisher"]
    assert genel["attributes"][0]["core"] is True


def test_gruplar_verilmezse_grup_duzeni_govdeye_KONMAZ() -> None:
    # TUZAK: boş liste göndermek ailenin bütün gruplarını siler.
    body = schema.family_body(name="Kitap", groups=None)
    assert body == {"name": "Kitap"}
    assert "attribute_groups" not in body


def test_gruplar_verilirse_nitelikler_kimlikle_gider() -> None:
    body = schema.family_body(name="Kitap", groups=[
        {"code": "general", "name": "Genel", "column": 1, "position": 0,
         "attributeIds": [1, 2, 33]},
    ])
    assert body["attribute_groups"][0]["custom_attributes"] == [{"id": 1}, {"id": 2}, {"id": 33}]


def test_cekirdek_nitelik_aileden_cikarilamaz() -> None:
    groups = [{"name": "Genel", "attributes": [{"code": "name"}, {"code": "publisher"}]}]
    message = schema.family_guard(groups)
    assert "sku" in message
    assert "çıkarılamaz" in message


def test_bos_aile_duzeni_reddedilir() -> None:
    assert "en az bir grup" in schema.family_guard([])


def test_cekirdek_nitelikler_duruyorsa_duzen_kabul_edilir() -> None:
    groups = [{"name": "Genel",
               "attributes": [{"code": code} for code in schema.CORE_CODES]}]
    assert schema.family_guard(groups) == ""
