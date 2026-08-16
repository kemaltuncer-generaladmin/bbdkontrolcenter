"""Saf kuralların sınaması — ağ yok, depo yok.

`people.py` bu ekranın sözlüğünü ve maskesini tutuyor. Buradaki testler üç
şeyi sabitliyor:

  1. YAZILABİLİR ALAN LİSTESİ SÖZLEŞMEDEKİNİN AYNISI. Listeye bir alan
     eklenmesi sessizce olmamalı.
  2. E-POSTA, PAROLA, HESAP TÜRÜ VE DURUM KENDİ GEREKÇESİYLE reddediliyor.
     Genel "tanınmayan alan" cümlesi işi görürdü ama yönetici NEDEN olmadığını
     okumalı, yoksa aynı isteği başka bir adla tekrar dener.
  3. DENETİM İZİNE GİDEN TELEFON MASKELİ. Sözleşme `PATCH` bölümünde biçimi
     örnekle veriyor (`532****567`) ve maske `sms.md`'dekiyle aynı olmalı.
"""

from __future__ import annotations

import pytest
from bld_customers_backend import people

# --------------------------------------------------------------- sözlükler

def test_yazilabilir_alanlar_sozlesmedeki_tam_liste() -> None:
    # `customers.md` → "Yazılabilir alanlar YALNIZ: first_name, last_name,
    # telephone, org_name, tax_office, tax_no, contact_person, org_phone."
    assert set(people.WRITABLE_FIELDS) == {
        "first_name", "last_name", "telephone",
        "org_name", "tax_office", "tax_no", "contact_person", "org_phone",
    }
    # E-posta ve parola listede OLMAMALI — hiçbir koşulda.
    assert "email" not in people.WRITABLE_FIELDS
    assert "password" not in people.WRITABLE_FIELDS
    assert "account_type" not in people.WRITABLE_FIELDS


def test_suzgec_kunyesi_sozlesmedeki_degerleri_tasir() -> None:
    spec = people.filter_spec()
    assert [item["value"] for item in spec["status"]] == ["all", "active", "disabled"]
    assert [item["value"] for item in spec["sort"]] == ["name", "created", "last_order"]
    assert spec["query_min"] == 2
    assert spec["per_page_max"] == 100
    assert spec["writable_fields"] == list(people.WRITABLE_FIELDS)


# --------------------------------------------------------------- maskeleme

@pytest.mark.parametrize(("ham", "beklenen"), [
    ("5321234567", "532****567"),
    ("0532 123 45 67", "053****567"),
    ("", ""),
    (None, ""),
    # Yedi haneden kısa numara MASKELENMEZ, TÜMÜYLE GİZLENİR: dört hanede ilk
    # üç ve son üç haneyi vermek numaranın kendisini vermektir.
    ("4321", "****"),
])
def test_telefon_maskesi(ham: object, beklenen: str) -> None:
    assert people.mask_phone(ham) == beklenen


def test_degisiklik_gunlugu_telefonu_maskeler_kurumu_maskelemez() -> None:
    once = {"telephone": "5321234567", "org_name": "Acme Gıda A.Ş.",
            "tax_no": "1234567890", "org_phone": "3124445566"}
    sonra = {"telephone": "5329876543", "org_name": "Acme Gıda ve Turizm A.Ş."}

    degisiklikler = {item["field"]: item for item in people.change_log(once, sonra)}
    assert degisiklikler["telephone"] == {"field": "telephone", "from": "532****567",
                                          "to": "532****543"}
    # Kurum adı MASKELENMEZ: ticari kayıttır, faturada zaten basılıdır ve
    # maskelenirse "ne değişti" sorusu cevapsız kalır.
    assert degisiklikler["org_name"]["to"] == "Acme Gıda ve Turizm A.Ş."
    # Gönderilmeyen alan listede YOK.
    assert "tax_no" not in degisiklikler
    assert "org_phone" not in degisiklikler


def test_degisiklik_gunlugu_ayni_degeri_degisiklik_saymaz() -> None:
    once = {"telephone": "5321234567", "org_name": ""}
    # Boş bir alanı `None` ile boşaltmak değişiklik DEĞİLDİR; sayılsaydı
    # denetim izine hiçbir şey anlatmayan bir satır düşerdi.
    assert people.change_log(once, {"telephone": "5321234567", "org_name": None}) == []


# -------------------------------------------------------------- doğrulama

def test_gerekce_alt_ve_ust_sinir() -> None:
    assert people.reason_error("x" * 10) == ""
    assert people.reason_error("x" * 500) == ""
    assert "en az 10" in people.reason_error("kısa")
    assert "en çok 500" in people.reason_error("x" * 501)


def test_aktor_kapisi_kvkk_gerekcesini_yazar() -> None:
    assert people.actor_error("Ayşe Yılmaz") == ""
    hata = people.actor_error("")
    assert "KVKK" in hata
    assert people.actor_error("x" * 121) != ""


@pytest.mark.parametrize(("deger", "gecerli"), [
    ("5321234567", True),
    ("0532 123 45 67", True),
    ("+90 (532) 123-45-67", True),
    ("", True),          # boş dize → `null` (sözleşme)
    (None, True),
    ("532123", False),   # on haneden kısa
    ("5" * 16, False),   # on beş haneden uzun
    ("532-ABC-4567", False),
])
def test_telefon_dogrulama(deger: object, gecerli: bool) -> None:
    assert (people.phone_error(deger, "Telefon") == "") is gecerli


@pytest.mark.parametrize(("deger", "gecerli"), [
    ("1234567890", True),    # vergi numarası, 10 hane
    ("12345678901", True),   # TC kimlik numarası, 11 hane
    ("", True),
    ("123456789", False),
    ("123456789012", False),
    ("12345678AB", False),
])
def test_vergi_numarasi_dogrulama(deger: str, gecerli: bool) -> None:
    assert (people.tax_no_error(deger) == "") is gecerli


def test_ad_bos_birakilamaz() -> None:
    assert people.name_error("", "Ad") != ""
    assert people.name_error("   ", "Ad") != ""
    assert people.name_error("Mehmet", "Ad") == ""


# ------------------------------------------------------------ kısmi gövde

def test_bos_govde_reddedilir() -> None:
    assert people.patch_error({}) != ""
    assert people.patch_error(None) != ""


@pytest.mark.parametrize("alan", ["email", "password", "account_type", "status"])
def test_yasak_alan_kendi_gerekcesiyle_reddedilir(alan: str) -> None:
    hata = people.patch_error({alan: "x"})
    assert hata == people.FORBIDDEN_FIELDS[alan]
    # Genel "tanınmayan alan" cümlesi DEĞİL: yönetici NEDEN olmadığını okumalı.
    assert "Tanınmayan alan" not in hata


def test_yasak_alan_baska_bir_alanla_birlikte_gelse_de_reddedilir() -> None:
    # Sözleşme: "Başka bir alan gönderilirse istek TÜMÜYLE reddedilir."
    # Yalnız yasak alanı düşürüp gerisini yazmak, e-posta değiştirdiğini sanan
    # bir yöneticiye "başarılı" demek olurdu.
    hata = people.patch_error({"telephone": "5329876543", "email": "yeni@ornek.com"})
    assert hata == people.FORBIDDEN_FIELDS["email"]


def test_taninmayan_alan_yazilabilirleri_listeler() -> None:
    hata = people.patch_error({"nickname": "memo"})
    assert "nickname" in hata
    assert "telephone" in hata


def test_temiz_govde_bos_metni_null_yapar_ama_adi_yapmaz() -> None:
    govde, hata = people.clean_patch({"org_name": "  ", "telephone": ""})
    assert hata == ""
    assert govde == {"org_name": None, "telephone": None}

    # Ad boş bırakılamaz; `null`'a düşmez, REDDEDİLİR.
    _, hata = people.clean_patch({"first_name": ""})
    assert hata != ""


def test_temiz_govde_hatali_degeri_gecide_gondermez() -> None:
    _, hata = people.clean_patch({"tax_no": "123"})
    assert "10" in hata and "11" in hata


# --------------------------------------------------------------- süzgeçler

def test_kisa_arama_istege_konmaz() -> None:
    # Tek harflik arama sunucuda 422 verirdi ve kullanıcı yazmaya devam
    # ederken hata görürdü.
    assert people.clean_query("a") == ""
    assert people.clean_query("ac") == "ac"
    assert people.clean_query("  acme  ") == "acme"


def test_bilinmeyen_suzgec_degeri_varsayilana_duser() -> None:
    assert people.clean_status("uydurma") == "all"
    assert people.clean_sort("uydurma") == "name"
    assert people.clean_direction("uydurma") == "asc"


def test_sayfa_boyu_tavanda_kirpilir() -> None:
    # Tavanın üstü sunucuda SESSİZCE kırpılıyor: 250 istemek hata vermez,
    # yalnız 100 döner ve "hepsini aldım" sanan istemci veri kaybeder.
    assert people.clean_per_page(250) == 100
    assert people.clean_per_page(0, 25) == 25
    assert people.clean_per_page("elli", 25) == 25


def test_sayfalama_kunyesi_eksik_metayi_elindekinden_uretir() -> None:
    meta = people.page_meta({}, page=2, per_page=25, rows=7)
    # `total` bilinmiyorsa ELDEKİ SATIR SAYISI yazılır; sıfır yazmak
    # sayfalayıcıyı "kayıt yok" göstermeye zorlardı — oysa ekranda satır var.
    assert meta["total"] == 7
    assert meta["last_page"] == 1
    assert meta["page"] == 2


# ------------------------------------------------------------ satır şekli

def test_musteri_satiri_maskelenmez() -> None:
    row = people.customer_row({"customer_id": 312, "first_name": "Mehmet",
                               "last_name": "Kaya", "telephone": "5321234567",
                               "email": "m@ornek.com", "status": 1,
                               "account_type": "corporate"})
    # LİSTE MASKELENMEZ (sözleşme): yönetici müşteriyi telefonundan tanır.
    assert row["telephone"] == "5321234567"
    assert row["email"] == "m@ornek.com"
    assert row["full_name"] == "Mehmet Kaya"
    assert row["status"] is True
    assert row["account_type_label"] == "Kurumsal"


def test_eksik_istatistik_sifir_degil_bilinmiyor_olur() -> None:
    detay = people.customer_detail({"customer_id": 312})
    # -1 "bilinmiyor" demektir ve panel o kutuyu çizmez. Sıfır yazmak, "hiç
    # sipariş vermemiş" ile "sayı gelmedi"yi aynı gösterirdi.
    assert detay["stats"]["order_count"] == -1
    assert detay["stats"]["total_spent_kurus"] == -1


def test_sms_satiri_ikinci_kez_maskelenmez() -> None:
    row = people.sms_row({"id": 1, "phone": "532****567", "body": "metin…"})
    assert row["phone"] == "532****567"


def test_sms_okumasinin_yerel_eylem_adi_ayridir() -> None:
    # Sunucu bu okuma için `customer.read` YAZMAZ (uç `control/sms/log`
    # altında). Aynı adı kullansaydık iki defteri karşılaştıran biri,
    # sunucuda karşılığı olmayan satırları "sunucu kayıp vermiş" diye okurdu.
    assert people.SMS_READ_ACTION != people.READ_ACTION
