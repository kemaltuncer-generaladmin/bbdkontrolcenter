"""Metin katmanı — segment aritmetiği, değişkenler, maskeleme.

Bu dosyanın tamamı SAF: ağ yok, depo yok, geçit yok. Segment sayısı doğrudan
paradır ve yanlış hesap yalnızca ekranı değil, faturayı da yanıltır.
"""

from __future__ import annotations

from bld_sms_backend import text as txt

# ----------------------------------------------------------------- ölçüm

def test_turkce_harf_faturalanan_segmenti_yariya_indirir() -> None:
    # Sözleşme ("Uzunluk ve segment"): GSM-7 tablosunda olmayan bir karakter
    # varsa mesaj UCS-2 gider ve tek segment 160 değil 70'tir. 100 karakterlik
    # bir metin tek "ş" yüzünden 1 kredi yerine 2 krediye mal olur.
    ascii_metin = "a" * 100
    turkce = "ş" + "a" * 99

    assert txt.measure(ascii_metin)["billed"]["segments"] == 1
    assert txt.measure(turkce)["billed"]["segments"] == 2
    assert txt.measure(ascii_metin)["billed"]["has_turkish_chars"] is False
    assert txt.measure(turkce)["billed"]["has_turkish_chars"] is True


def test_iki_olcu_ayri_raporlanir_ve_ikisi_de_dogru() -> None:
    # `billed` sözleşmenin (faturanın) ölçüsü: UCS-2, 70 karakter.
    # `provider` platformun SMS şeridinin ölçüsü: Netgsm Türkçe kaydırma
    # tablosu, harf başına 2 septet, 160 karakter.
    # İkisi ÇAKIŞIR ve aradaki fark paradır; tek sayı göstermek iki şekilde de
    # yanlış olurdu.
    metin = "ş" + "a" * 99
    olcum = txt.measure(metin)

    assert olcum["billed"]["encoding"] == "ucs2"
    assert olcum["provider"]["encoding"] == "gsm7-tr"
    assert olcum["provider"]["segments"] < olcum["billed"]["segments"]


def test_sadelestirme_kazanci_hesaplanir_ama_metin_degismez() -> None:
    metin = "Ödemeniz için şubemize uğrayınız" * 3
    olcum = txt.measure(metin)

    assert olcum["simplified"]["gain"] > 0
    assert "ş" not in olcum["simplified"]["text"]
    # Metnin KENDİSİ değiştirilmez; öneri kullanıcıya bırakılır.
    assert olcum["simplified"]["text"] != metin


def test_bos_metin_sifir_segment() -> None:
    olcum = txt.measure("")
    assert olcum["length"] == 0
    assert olcum["billed"]["segments"] == 0
    assert olcum["simplified"]["gain"] == 0


def test_genisletme_karakteri_gsm7de_iki_birim_sayilir() -> None:
    # `{ } [ ] ~ ^ \\ | €` GSM-7'de ESC öneki alır ve İKİ birim yer kaplar.
    # Ham karakter sayısına bakan bir sayaç 160 karakterde durur, mesaj ise
    # ikinci segmente taşar.
    assert txt.measure("€" * 80)["billed"]["units"] == 160
    assert txt.measure("€" * 80)["billed"]["segments"] == 1
    assert txt.measure("€" * 81)["billed"]["segments"] == 2


# ------------------------------------------------------------- değişken

def test_degisken_sozdizimi_tek_suslu_parantezdir() -> None:
    # Sözleşme: `{degisken}` — süslü parantez, boşluksuz, küçük harf ve alt
    # çizgi. Çift parantez ya da boşluklu yazım TANINMAZ.
    assert txt.variables("Sayın {customer_name}, {order_no}") == \
        ["customer_name", "order_no"]
    assert txt.variables("{{ad}}") == ["ad"]      # içteki tek parantez okunur
    assert txt.variables("{ ad }") == []
    assert txt.variables("{Ad}") == []


def test_taninmayan_degisken_kaydetmeden_once_bulunur() -> None:
    # Sunucu bunu 422 ile reddediyor. Sessizce boş bırakılan bir değişken,
    # müşteriye "Sayın , siparişiniz…" diye giden bir SMS üretirdi.
    bilinmeyen = txt.unknown_variables("Sayın {musteri_adi}, {order_no} hazır.",
                                       ["order_no", "customer_name"])
    assert bilinmeyen == ["musteri_adi"]


def test_cozulemeyen_degisken_oldugu_gibi_birakilir() -> None:
    # Boşa çevirmek eksiği GİZLER ve yönetici cümleyi tam sanar.
    metin, eksik = txt.render("Sipariş {order_no}, kurye {eta} varır.",
                              {"order_no": "BLD-1"})
    assert metin == "Sipariş BLD-1, kurye {eta} varır."
    assert eksik == ["eta"]


def test_bos_ornek_degeri_cozulmus_sayilmaz() -> None:
    # Boş dize bir değer değildir: "Sayın , siparişiniz…" cümlesi tam olarak
    # böyle doğar.
    metin, eksik = txt.render("Sayın {customer_name}.", {"customer_name": ""})
    assert metin == "Sayın {customer_name}."
    assert eksik == ["customer_name"]


def test_katalog_ornekleri_sablonun_degiskenlerini_karsilar() -> None:
    # Katalogdaki örnek değerler eksikse yerel önizleme her açılışta
    # "çözülemedi" gösterir ve sayaç yanlış uzunluk ölçer.
    for key, kayit in txt.CATALOG.items():
        assert kayit["sample"], f"{key} için örnek değer yok"
        assert kayit["group"] in {oberk["key"] for oberk in txt.GROUPS}, key


# ------------------------------------------------------------ maskeleme

def test_denetim_satirina_acik_numara_yazilmaz() -> None:
    assert txt.mask_phone("5321234567") == "532****567"
    assert txt.mask_phone("0532 123 45 67") == "053****567"
    assert txt.mask_phone("") == "***"


# ----------------------------------------------------------------- diğer

def test_gerekce_siniri_on_uctakiyle_ayni() -> None:
    assert txt.reason_error("kısa")
    assert txt.reason_error("x" * (txt.MAX_REASON + 1))
    assert txt.reason_error("Netgsm entegrasyonu doğrulandı") == ""


def test_eski_damga_suresi_dolmus_sayilir() -> None:
    # Çözülemeyen damga ESKİ sayılır: okunamayan bir jetonu geçerli saymak,
    # süresi dolmuş bir provayla gerçek gönderim yapmak olurdu.
    assert txt.older_than("bozuk damga", 15) is True
    assert txt.older_than("2020-01-01T00:00:00+00:00", 15) is True
    assert txt.older_than(txt.now_iso(), 15) is False


def test_para_ornekleri_kurustan_uretilir() -> None:
    # Para HER ZAMAN tam sayı kuruştur; örnek değerler de kuruştan üretilir ki
    # ekranda kayan nokta hiç doğmasın.
    assert txt.CATALOG["order_created"]["sample"]["total"] == "180,00"
    assert txt.CATALOG["subscription_payment_due"]["sample"]["amount"] == "1.800,00"
