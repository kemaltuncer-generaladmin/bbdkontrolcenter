"""Saf dönüşümlerin testi — ağ yok, depo yok.

En ağır iddia dosyanın sonunda: arayüzdeki beyaz liste ile buradaki beyaz
listenin AYNI olduğu. İkisi ayrışırsa kullanıcı ekranda gördüğü biçimi
kaydettiğinde sessizce kaybeder ve bunu ancak siteyi açıp bakınca fark eder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bld_cms_backend import content as cx

KIT = Path(__file__).resolve().parents[3] / "apps" / "desktop" / "shell" / "ui-kit" \
    / "richtext.js"


# ==================================================================== gerekçe

def test_gerekce_alt_ve_ust_sinir() -> None:
    assert cx.reason_error("kısa")
    assert cx.reason_error("x" * 501)
    assert cx.reason_error("İletişim telefonu güncellendi") == ""


# ======================================================================= slug

def test_slug_kalibi_sozlesmedeki_gibidir() -> None:
    assert cx.slug_error("kurumsal-catering") == ""
    assert cx.slug_error("a") != ""            # 2 karakterden kısa
    assert cx.slug_error("Kurumsal") != ""     # büyük harf
    assert cx.slug_error("iki--tire") != ""    # çift tire
    assert cx.slug_error("-bas") != ""
    assert cx.slug_error("x" * 97) != ""


def test_slug_degisimi_yazmadan_once_uyarir() -> None:
    # Sunucu da `warnings.slug_changed` döndürüyor ama o, yazma BİTTİKTEN
    # sonra gelir. Adresi değiştirdiğini kaydettikten sonra öğrenen yönetici
    # kırılan bağlantıları geri getiremez.
    notice = cx.slug_change_notice("kurumsal-catering", "kurumsal-yemek")
    assert notice and notice["code"] == "slug_changed"
    assert notice["from"] == "kurumsal-catering"
    assert cx.slug_change_notice("aynı", "aynı") is None
    assert cx.slug_change_notice("", "yeni") is None    # yeni kayıt, uyarı yok


def test_slugify_turkce_harfleri_cozer() -> None:
    assert cx.slugify("Etkinlik Çatering Şubesi") == "etkinlik-catering-subesi"


# ================================================================ HTML kapısı

def test_script_icerigiyle_birlikte_atilir() -> None:
    temiz = cx.sanitize_html("<p>Merhaba</p><script>alert(1)</script>")
    assert temiz == "<p>Merhaba</p>"
    assert "alert" not in temiz


def test_taninmayan_etiket_acilir_metni_kalir() -> None:
    # TUZAK 2: etiketi içeriğiyle silmek, yapıştırılan metnin yarısını
    # kaybettirirdi. `section` beyaz listede yok ama içindeki cümle metindir.
    temiz = cx.sanitize_html("<section><p>Kalsın</p></section>")
    assert temiz == "<p>Kalsın</p>"


def test_javascript_ve_data_adresleri_dusurulur() -> None:
    # `data:text/html` tarayıcıda sayfa açar ve beyaz listeyi anlamsız kılar;
    # satır içi görselin "önce yükle, sonra adresi ekle" akışının sebebi budur.
    assert 'href' not in cx.sanitize_html('<a href="javascript:alert(1)">tık</a>')
    assert 'src' not in cx.sanitize_html('<img src="data:text/html,<b>x</b>">')
    assert 'href' not in cx.sanitize_html('<a href="java\nscript:alert(1)">tık</a>')


def test_gorece_ve_guvenli_adres_gecer() -> None:
    assert 'href="/hizmetler"' in cx.sanitize_html('<a href="/hizmetler">Hizmetler</a>')
    assert 'src="/img/mutfak.png"' in cx.sanitize_html('<img src="/img/mutfak.png">')
    assert 'href="mailto:info@bld.example"' in cx.sanitize_html(
        '<a href="mailto:info@bld.example">yaz</a>')


def test_dis_baglanti_yeni_sekmede_acilir() -> None:
    temiz = cx.sanitize_html('<a href="https://ornek.example">dış</a>')
    assert 'target="_blank"' in temiz
    assert 'rel="noopener noreferrer"' in temiz


def test_style_uc_ozellige_indirgenir() -> None:
    # Kaplama saldırısı `position`, `width/height`, `opacity`, `z-index`
    # ister; hiçbiri listede yok. Kalan üçüyle kaplama kurulamaz.
    assert cx.filter_style("color: #FFF; position: fixed; z-index: 99") == "color:#ffffff"
    assert cx.filter_style("text-align: center") == "text-align:center"
    assert cx.filter_style("text-align: yukarı") == ""
    assert cx.filter_style("background-color: rgb(17, 24, 39)") == "background-color:#111827"
    temiz = cx.sanitize_html('<p style="position:fixed;color:#ff0000">x</p>')
    assert temiz == '<p style="color:#ff0000">x</p>'


def test_kapatilmayan_etiket_kapatilir() -> None:
    # Yarım kalan `<ul>` kendinden sonraki bütün paragrafları içine alıyordu.
    assert cx.sanitize_html("<ul><li>bir") == "<ul><li>bir</li></ul>"


def test_html_metne_cevrilir_ve_degisiklik_soylenir() -> None:
    assert cx.html_to_text("<p>Bir <strong>iki</strong></p>") == "Bir iki"
    assert cx.html_changed_note("<p>a</p>", "<p>a</p>") == ""
    assert cx.html_changed_note("<p>a</p><script>x</script>", "<p>a</p>")


# =============================================================== içerik değeri

def test_anahtar_listesi_sabittir() -> None:
    assert cx.CONTENT_KEYS == ("brand", "contact", "company", "faq", "sectors",
                               "menus", "quality")
    assert cx.content_key_error("brand") == ""
    assert cx.content_key_error("uydurma") != ""


def test_boyut_siniri_sunucudan_once_yakalanir() -> None:
    # 256 KB'ı aşan gövdeyi gönderip 422 beklemek, hız kovasından pay yer ve
    # kullanıcıya sözleşme diliyle bir hata gösterirdi.
    assert cx.content_value_error("brand", {"name": "BLD"}) == ""
    buyuk = {"metin": "x" * (cx.MAX_CONTENT_BYTES + 10)}
    assert "KB" in cx.content_value_error("brand", buyuk)


def test_cevrilemeyen_deger_reddedilir() -> None:
    assert cx.content_value_error("brand", {"tarih": object()}) != ""


def test_sekil_uyusmazligi_REDDETMEZ_uyarir() -> None:
    # cms.md açıkça diyor ki sunucu içeriği doğrulamaz. Sözleşmenin izin
    # verdiğini arayüzün yasaklaması, veriyi düzeltilemez hâle getirirdi.
    assert cx.content_value_error("faq", {"q": "s"}) == ""      # hata YOK
    assert cx.content_shape_warning("faq", {"q": "s"}) != ""    # uyarı VAR
    assert cx.content_shape_warning("faq", [{"q": "s"}]) == ""


def test_kaydi_olmayan_anahtar_da_doner() -> None:
    # "Eksik anahtarı atlamak, panelin 'bu alan yok mu, yoksa boş mu' sorusunu
    # kendi cevaplamasını gerektirirdi" (cms.md).
    view = cx.content_view({"data": {"brand": {"value": {"name": "BLD"},
                                               "updated_at": "2026-08-02T10:00:00Z"}}})
    keys = [row["key"] for row in view["items"]]
    assert keys == list(cx.CONTENT_KEYS)

    bos = next(row for row in view["items"] if row["key"] == "sectors")
    assert bos["value"] == []          # dizi anahtarı boş DİZİ ile döner
    assert bos["updated_at"] == ""
    assert bos["filled"] is False

    dolu = next(row for row in view["items"] if row["key"] == "brand")
    assert dolu["filled"] is True
    assert dolu["bytes"] > 0


# =================================================================== satırlar

def test_hizmet_dizi_alanlari_kirpilir() -> None:
    row = cx.service_row({"id": "3", "audience": ["Ofisler", "", None, " Fabrikalar "]})
    assert row["audience"] == ["Ofisler", "Fabrikalar"]
    assert row["id"] == 3


def test_dizi_alani_sinirlari() -> None:
    assert cx.string_list_error("Kimler için", ["a"] * 20) == ""
    assert cx.string_list_error("Kimler için", ["a"] * 21) != ""
    assert cx.string_list_error("Kimler için", ["x" * 301]) != ""
    assert cx.string_list_error("Kimler için", "liste değil") != ""


def test_okuma_suresi_elle_ile_hesaplanan_ayri_durur() -> None:
    # İkisini ayrı vermek, panelin "hesaplandı" ipucunu gösterebilmesi
    # içindir; tek alana katlamak yöneticiye kendi yazdığı sanılan bir sayı
    # gösterirdi.
    hesaplanan = cx.post_row({"reading_minutes": None, "reading_minutes_effective": 4})
    assert hesaplanan["reading_estimated"] is True
    assert hesaplanan["reading_minutes"] is None
    assert hesaplanan["reading_minutes_effective"] == 4

    elle = cx.post_row({"reading_minutes": 7, "reading_minutes_effective": 7})
    assert elle["reading_estimated"] is False


def test_yayin_tarihi_tarihtir_an_degil() -> None:
    assert cx.date_error("2026-08-01") == ""
    assert cx.date_error("") == ""
    assert cx.date_error("2026-08-01T09:00:00Z") != ""
    assert cx.date_error("01.08.2026") != ""


def test_yazi_govdesi_bos_olamaz() -> None:
    # Boş gövdeli bir yazı, sitede başlığı olan boş bir sayfa üretirdi.
    hata = cx.post_fields_error({"title": "Başlık", "slug": "baslik",
                                 "body_html": "<p> </p>"}, creating=True)
    assert hata != ""
    assert cx.post_fields_error({"title": "Başlık", "slug": "baslik",
                                 "body_html": "<p>Metin</p>"}, creating=True) == ""


def test_kismi_guncellemede_govde_gonderilmisse_bos_olamaz() -> None:
    assert cx.post_fields_error({"body_html": ""}, creating=False) != ""
    assert cx.post_fields_error({"title": "Yeni"}, creating=False) == ""


# ============================================================ yeniden çizdirme

def test_yeniden_cizdirmenin_dort_hali_ayrilir() -> None:
    # "İstendi ve olmadı" ile "hiç istenmedi" aynı satıra yazılırsa, sonradan
    # bakan kişi sitenin neden eski göründüğünü bulamaz.
    assert cx.revalidate_view(None, requested=False)["status"] == "skipped"
    assert cx.revalidate_view({"revalidated": True}, requested=True)["status"] == "ok"
    assert cx.revalidate_view({"data": {"status": "ok"}},
                              requested=True)["status"] == "ok"

    # BAYRAK HİÇ GELMEZSE "tazelendi" DENMEZ: bilmediğimiz bir şeyi söylemek,
    # tam da bu ekranın engellemek için var olduğu cümleyi kurdururdu.
    bilinmiyor = cx.revalidate_view({"ok": True}, requested=True)
    assert bilinmiyor["status"] == "unknown"
    assert "YAZILDI" in bilinmiyor["note"]

    basarisiz = cx.revalidate_view(
        {"data": {"status": "failed", "error": "Bağlantı zaman aşımı (3 sn)"},
         "warnings": [{"code": "revalidate_failed"}]}, requested=True)
    assert basarisiz["status"] == "failed"
    assert "zaman aşımı" in basarisiz["error"]
    # Ekranda gösterilecek cümle KAYDIN YAZILDIĞINI söylemeli; yoksa yönetici
    # aynı kaydı ikinci kez yazar.
    assert "YAZILDI" in basarisiz["note"]


def test_yol_listesi_sinirlari() -> None:
    assert cx.revalidate_paths_error(None) == ""
    assert cx.revalidate_paths_error(["/hizmetler", "/blog/x"]) == ""
    assert cx.revalidate_paths_error(["hizmetler"]) != ""      # `/` ile başlamıyor
    assert cx.revalidate_paths_error(["/x"] * 21) != ""
    assert cx.clean_paths(["/a", "", "  ", "/b"]) == ["/a", "/b"]


# ====================================================== ARAYÜZLE EŞİTLİK (K9)

def _js_set(name: str, source: str) -> set[str]:
    """`const NAME = new Set([...])` içindeki dizeleri çıkarır."""
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"{name} `richtext.js` içinde bulunamadı"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _js_attrs(source: str) -> dict[str, tuple[str, ...]]:
    """`const ALLOWED_ATTRS = { a: ['href', …], … }` bloğunu çıkarır."""
    match = re.search(r"const ALLOWED_ATTRS = \{(.*?)\n\};", source, re.DOTALL)
    assert match, "ALLOWED_ATTRS `richtext.js` içinde bulunamadı"
    out: dict[str, tuple[str, ...]] = {}
    for tag, body in re.findall(r"(\w+): \[([^\]]*)\]", match.group(1)):
        out[tag] = tuple(re.findall(r"'([^']+)'", body))
    return out


def test_beyaz_liste_arayuzle_birebir_ayni() -> None:
    # ÜÇ KAPI, TEK LİSTE. Arayüz (richtext.js) yazarken ve çizerken, bu dosya
    # göndermeden önce, BLD'deki `HtmlSanitizer` kaydederken aynı listeyi
    # uygular. Üçüncüsüne test uzatılamaz — o yüzden ilk ikisi burada birbirine
    # bağlanır ve liste genişletildiğinde sunucudaki aynanın da elle
    # değiştirilmesi gerektiği `content.py` başlığında yazılıdır.
    assert KIT.exists(), "ui-kit/richtext.js bulunamadı"
    source = KIT.read_text(encoding="utf-8")

    assert _js_set("ALLOWED_TAGS", source) == set(cx.ALLOWED_TAGS)
    assert _js_set("DROP_TAGS", source) == set(cx.DROP_TAGS)
    assert _js_set("VOID_TAGS", source) == set(cx.VOID_TAGS)
    assert _js_attrs(source) == cx.ALLOWED_ATTRS

    js_style = set(re.findall(r"'([^']+)'",
                              re.search(r"const STYLE_PROPS = new Set\(\[(.*?)\]\)",
                                        source, re.DOTALL).group(1)))
    assert js_style == set(cx.STYLE_PROPS)

    # Şemalarda TEK BİLİNÇLİ FARK: arayüz `URL.protocol` ile karşılaştırıyor ve
    # o alan iki nokta üstü ile geliyor (`'http:'`), burada ise `urlsplit`
    # kullanılıyor ve o iki noktayı atıyor. Karşılaştırma bu yüzden iki noktayı
    # kırpar — listenin KENDİSİ aynı kalmalı, yazımı değil.
    js_schemes = {item.rstrip(":") for item in re.findall(
        r"'([^']+)'", re.search(r"const SAFE_SCHEMES = \[(.*?)\]",
                                source, re.DOTALL).group(1))}
    assert js_schemes == set(cx.SAFE_SCHEMES)


def test_panelde_ikinci_bir_beyaz_liste_yok() -> None:
    # Kit kuralı 10: "Panelde ikinci bir liste tutma — `store_cms` bunu denedi
    # ve iki liste sessizce ayrıştı." Panel izin verilen etiketleri
    # gösteriyor ama listeyi SUNUCUDAN alıyor; kendi kopyasını yazsaydı,
    # buradaki liste değiştiğinde ekran yanlış cümleyi göstermeye devam ederdi.
    panel = (Path(__file__).resolve().parents[1] / "ui" / "panel" / "index.js") \
        .read_text(encoding="utf-8")
    for tag_list in ("ALLOWED_TAGS", "DROP_TAGS"):
        assert f"const {tag_list}" not in panel, "panelde ikinci beyaz liste var"
    assert "screen.editor.allowed_tags" in panel or "editor.allowed_tags" in panel


def test_json_sozlesmesi_serilestirilebilir() -> None:
    # Ekran sözleşmesi HTTP gövdesine giriyor; içine `frozenset` sızarsa uç
    # 500 verir ve panel hiç açılmaz.
    assert json.dumps({"tags": sorted(cx.ALLOWED_TAGS),
                       "props": sorted(cx.STYLE_PROPS),
                       "limits": dict(cx.SERVICE_LIMITS)}, ensure_ascii=False)
