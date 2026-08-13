"""CMS içerik dönüşümleri — saf mantık, ağa çıkmaz.

Her testin adı bir tuzağı söyler; tuzakların listesi `backend/content.py`
tepesindedir.
"""

from __future__ import annotations

from store_cms_backend import content

# ======================================================= TUZAK 2 — HTML kapısı


def test_script_etiketi_icerigiyle_birlikte_atilir() -> None:
    kirli = '<p>Merhaba</p><script>fetch("/api/admin/orders")</script>'
    assert content.sanitize_html(kirli) == "<p>Merhaba</p>"


def test_on_ozniteligi_duser_style_uc_ozellige_indirgenir() -> None:
    # ESKİ AD YALAN SÖYLÜYORDU: "style hiçbir etikette kalmaz" deniyordu ve test
    # geçiyordu — ama geçme sebebi `position` özelliğinin listede olmaması, style
    # özniteliğinin yasak olması değil. Sözleşme şu: `on*` HER ZAMAN düşer,
    # `style` KALIR ama yalnız `color` · `background-color` · `text-align` ile.
    kirli = '<p onclick="alert(1)" style="position:fixed">Metin</p>'
    assert content.sanitize_html(kirli) == "<p>Metin</p>"


def test_renk_ve_hizalama_ozellikleri_gecer() -> None:
    # Bu üçü kaplama saldırısı kuramaz; yasaklanmaları "yazıyı kırmızı yap"
    # isteğini kod bilgisi gerektiren bir işe çeviriyordu.
    temiz = content.sanitize_html('<p style="color:#b91c1c;text-align:center">Uyarı</p>')
    assert temiz == '<p style="color:#b91c1c;text-align:center">Uyarı</p>'
    vurgu = content.sanitize_html('<span style="background-color:#fef08a">Vurgu</span>')
    assert vurgu == '<span style="background-color:#fef08a">Vurgu</span>'


def test_kaplama_kurabilecek_ozellikler_duser() -> None:
    # Görünmez katman saldırısının istediği dört özellik: konum, boyut,
    # saydamlık, katman sırası. Dördü de listede yok; biri eklenirse gerekçe
    # çöker ve `style` yeniden yasaklanması gereken bir öznitelik olur.
    kirli = '<p style="position:fixed;width:100%;opacity:0.01;z-index:9999">Kapak</p>'
    assert content.sanitize_html(kirli) == "<p>Kapak</p>"

    # Güvenli özellikle birlikte gelirse yalnız tehlikeli olan atılır; kullanıcı
    # yazdığı rengi kaybetmez.
    karisik = content.sanitize_html('<p style="position:fixed;color:#b91c1c">Metin</p>')
    assert karisik == '<p style="color:#b91c1c">Metin</p>'

    # `img` ölçüsü ÖZNİTELİK olarak meşrudur; `style` içindeki `width` değildir.
    gorsel = content.sanitize_html('<img src="/a.png" width="120" style="width:9999px">')
    assert gorsel == '<img src="/a.png" width="120">'


def test_rgb_degeri_hex_e_normalize_edilir() -> None:
    # Tarayıcı `execCommand` sonrası rengi `rgb(...)` yazar, düzenleyici hex
    # verir. İkisi aynı rengi anlatır; kayıt tek biçime indirilmezse aynı içerik
    # her açılışta "değişmiş" görünür ve gereksiz yazma isteği doğurur.
    temiz = content.sanitize_html('<span style="color:rgb(17, 24, 39)">Siyah</span>')
    assert temiz == '<span style="color:#111827">Siyah</span>'
    assert content.normalize_color("rgba(255, 0, 0, 0.5)") == "#ff0000"
    assert content.normalize_color("mavi") == ""


def test_kisa_hex_alti_haneye_acilir() -> None:
    assert content.normalize_color("#FFF") == "#ffffff"
    assert content.sanitize_html('<p style="COLOR: #FFF">Beyaz</p>') == \
        '<p style="color:#ffffff">Beyaz</p>'


def test_on_oznitelikleri_hangi_etikette_ve_nasil_yazilirsa_yazilsin_duser() -> None:
    # Beyaz liste "izin verilmeyen düşer" mantığıyla çalışır; `on*` için ayrı
    # bir kural YOKTUR — bu test o mantığın büyük/küçük harf ve etiket
    # değişince de tuttuğunu sabitler.
    assert content.sanitize_html('<a href="/iade" ONCLICK="x()">İade</a>') == \
        '<a href="/iade">İade</a>'
    assert content.sanitize_html('<img src="/a.png" onerror="alert(1)">') == \
        '<img src="/a.png">'
    assert content.sanitize_html('<p onmouseover="x()">M</p>') == "<p>M</p>"
    assert content.sanitize_html('<td onfocus="x()">H</td>') == "<td>H</td>"
    assert content.sanitize_html('<span OnLoad="x()">S</span>') == "<span>S</span>"


def test_javascript_semali_baglanti_dusurulur_metin_kalir() -> None:
    kirli = '<a href="javascript:alert(1)">Tıkla</a>'
    temiz = content.sanitize_html(kirli)
    assert "javascript" not in temiz
    assert "Tıkla" in temiz


def test_satir_sonu_serpistirilmis_javascript_de_dusurulur() -> None:
    # `java\nscript:` bazı tarayıcılarda hâlâ çalışıyor; boşluklar atılmadan
    # şema denetimi yapmak kapıyı açık bırakırdı.
    kirli = '<a href="java\nscript:alert(1)">Tıkla</a>'
    assert "script:" not in content.sanitize_html(kirli)


def test_data_semasi_reddedilir() -> None:
    kirli = '<a href="data:text/html;base64,PHNjcmlwdD4=">Aç</a>'
    assert "data:" not in content.sanitize_html(kirli)


def test_gorece_ve_guvenli_baglanti_korunur() -> None:
    temiz = content.sanitize_html('<a href="/iade" title="İade">İade</a>')
    assert 'href="/iade"' in temiz
    assert 'title="İade"' in temiz


def test_dis_baglanti_yeni_sekmede_ve_opener_sizdirmadan_acilir() -> None:
    temiz = content.sanitize_html('<a href="https://ornek.com">Dış</a>')
    assert 'rel="noopener noreferrer"' in temiz
    assert 'target="_blank"' in temiz


# ================================================= TUZAK 3 — metin kaybolmaz

def test_taninmayan_etiket_acilir_icerigi_durur() -> None:
    # `<div>` beyaz listede yok ama içindeki metin kullanıcının yazısıdır;
    # etiketle birlikte silmek veri kaybıdır.
    assert content.sanitize_html("<div><p>Metin</p></div>") == "<p>Metin</p>"


def test_kapatilmamis_etiket_kapatilir() -> None:
    assert content.sanitize_html("<ul><li>Bir") == "<ul><li>Bir</li></ul>"


def test_metin_kacisi_yapilir_enjeksiyon_olmaz() -> None:
    assert content.sanitize_html("<p>5 < 7 & 8 > 6</p>") == "<p>5 &lt; 7 &amp; 8 &gt; 6</p>"


def test_strip_html_duz_metin_birakir_script_icerigini_almaz() -> None:
    plain = content.strip_html("<p>Bir</p><script>gizli()</script><p>İki</p>")
    assert plain == "Bir İki"


# ================================================= TUZAK 5 — içerikte arama

def test_arama_icerik_metninde_calisir() -> None:
    row = {"title": "Hakkımızda", "slug": "hakkimizda", "metaTitle": "", "metaDescription": ""}
    body = content.strip_html("<p>Kargo ücreti 2026 yılında güncellenmiştir.</p>")
    assert content.page_matches(row, body, "kargo ücreti") is True
    assert content.page_matches(row, body, "iade") is False


def test_arama_turkce_harflerde_de_eslesir() -> None:
    row = {"title": "İade ve Cayma", "slug": "iade", "metaTitle": "", "metaDescription": ""}
    assert content.page_matches(row, "", "İADE") is True
    assert content.page_matches(row, "", "iade") is True


def test_alinti_eslesen_yerden_kesilir() -> None:
    body = "A" * 200 + " kargo ücreti " + "B" * 200
    parca = content.snippet(body, "kargo")
    assert "kargo" in parca
    assert len(parca) < 120


# ============================================== TUZAK 1 — çeviri satırı

def test_alan_ceviri_satirindan_okunur() -> None:
    raw = {"id": 3, "translations": [{"locale": "tr", "page_title": "Gizlilik"},
                                     {"locale": "en", "page_title": "Privacy"}]}
    assert content.field(raw, "tr", "page_title") == "Gizlilik"
    assert content.field(raw, "en", "page_title") == "Privacy"


def test_duz_kayit_da_okunur() -> None:
    assert content.field({"page_title": "Çerez"}, "tr", "page_title") == "Çerez"


# ================================================ TUZAK 6 — durum uydurulmaz

def test_durum_alani_yoksa_none_kalir_pasif_gosterilmez() -> None:
    row = content.page_row({"id": 1, "page_title": "Sayfa", "url_key": "sayfa"})
    assert row["status"] is None


def test_durum_alani_varsa_okunur() -> None:
    row = content.page_row({"id": 1, "status": 1, "page_title": "Sayfa"})
    assert row["status"] is True


# ================================================== SEO ölçüsü ve önizleme

def test_meta_baslik_bos_ise_onizleme_sayfa_basligini_kullanir() -> None:
    view = content.seo_view(title="Gizlilik Politikası", meta_title="",
                            meta_description="Kısa", slug="gizlilik")
    assert view["preview"]["title"] == "Gizlilik Politikası"
    assert view["preview"]["usesPageTitle"] is True
    assert view["title"]["state"] == "missing"
    assert view["complete"] is False


def test_uzun_meta_aciklama_uzun_diye_isaretlenir() -> None:
    view = content.seo_view(title="X", meta_title="B" * 45, meta_description="A" * 400,
                            slug="x")
    assert view["description"]["state"] == "long"
    assert view["title"]["state"] == "ok"


def test_onizleme_adresi_site_koku_ile_kurulur() -> None:
    view = content.seo_view(title="X", meta_title="X", meta_description="Y", slug="iade",
                            base_url="https://bbdstore.com.tr/")
    assert view["preview"]["url"] == "https://bbdstore.com.tr/iade"


# ============================================ TUZAK 4 — adres değişikliği

def test_slug_degisince_yonlendirme_onerilir() -> None:
    mesaj = content.slug_change_notice("eski-adres", "yeni-adres")
    assert "/eski-adres" in mesaj
    assert "301" in mesaj


def test_slug_ayni_ise_uyari_yok() -> None:
    assert content.slug_change_notice("adres", "adres") == ""


# =============================================== iç bağlantı denetimi

def test_bilinmeyen_cms_adresi_kirik_sayilir() -> None:
    html = '<a href="/olmayan-sayfa">Bak</a>'
    kirik = content.broken_links(html, known_slugs={"iade"})
    assert [item["href"] for item in kirik] == ["/olmayan-sayfa"]


def test_urun_adresi_kirik_sayilmaz_bilinmiyor_kalir() -> None:
    # Ürün adresi katalogdan gelir; buradan doğrulanamaz. "Kırık" demek her
    # sayfada yalancı uyarı üretirdi.
    assert content.broken_links('<a href="/products/kalem">Ürün</a>', known_slugs=set()) == []


def test_yonlendirmesi_olan_eski_adres_kirik_sayilmaz() -> None:
    html = '<a href="/eski-adres">Eski</a>'
    assert content.broken_links(html, known_slugs=set(),
                                rewrite_sources={"eski-adres"}) == []


def test_dis_baglanti_denetlenmez() -> None:
    assert content.internal_links('<a href="https://ornek.com/x">Dış</a>') == []


# ==================================================== SSS soru-cevap çifti

def test_sss_sayfasi_soru_cevap_ciftlerine_ayrilir() -> None:
    html = "<h3>Kargo ne zaman gelir?</h3><p>2-3 iş günü.</p><h3>İade var mı?</h3><p>14 gün.</p>"
    pairs = content.faq_parse(html)
    assert [pair["question"] for pair in pairs] == ["Kargo ne zaman gelir?", "İade var mı?"]
    assert pairs[0]["answer"] == "<p>2-3 iş günü.</p>"


def test_sss_duzeninde_olmayan_sayfa_bos_doner_icerik_yeniden_yazilmaz() -> None:
    assert content.faq_parse("<p>Serbest metin</p>") == []


def test_sss_yazarken_cevap_temizlenir_ve_soru_kacirilir() -> None:
    html = content.faq_render([
        {"question": "5 < 7 mi?", "answer": '<p onclick="x()">Evet</p><script>y()</script>'},
    ])
    assert "<h3>5 &lt; 7 mi?</h3>" in html
    assert html.endswith("<p>Evet</p>")
    assert "script" not in html


def test_ciplak_cevap_paragrafa_sarilir() -> None:
    assert content.faq_render([{"question": "S", "answer": "Cevap"}]).endswith("<p>Cevap</p>")


# ==================================================== yazma gövdesi

def test_gonderilmeyen_alan_mevcut_degeriyle_geri_gider() -> None:
    current = {"page_title": "Eski", "url_key": "eski", "html_content": "<p>Metin</p>",
               "meta_title": "M", "channels": [{"id": 1}]}
    body = content.write_body(current, {"page_title": "Yeni"}, locale="tr")
    assert body["page_title"] == "Yeni"
    assert body["url_key"] == "eski"           # dokunulmayan alan korunur
    assert body["html_content"] == "<p>Metin</p>"
    assert body["locale"] == "tr"
    assert body["channels"] == [1]


def test_mevcut_kirli_icerik_de_temizlenerek_geri_yazilir() -> None:
    # Kirli HTML'i olduğu gibi geri yazmak, temizlemeyi ilk kaydetmede etkisiz
    # kılardı: kullanıcı "temizlendi" der, mağazada script durmaya devam eder.
    current = {"html_content": '<p>Metin</p><script>x()</script>'}
    body = content.write_body(current, {"page_title": "Yeni"}, locale="tr")
    assert body["html_content"] == "<p>Metin</p>"


def test_taninmayan_yama_anahtari_dusurulur() -> None:
    assert content.normalize_patch({"title": "A", "bilinmeyen": "B"}) == {"page_title": "A"}


# ============================================ TUZAK 7 — yönlendirme döngüsü

def test_kaynak_ve_hedef_ayni_ise_reddedilir() -> None:
    assert "döngü" in content.redirect_error("/iade", "/iade/", 301)


def test_ayni_kaynaga_ikinci_yonlendirme_reddedilir() -> None:
    mevcut = [{"id": 4, "source": "eski", "target": "/yeni"}]
    assert "#4" in content.redirect_error("/eski", "/baska", 301, mevcut)


def test_ters_yonde_kayit_kilitlenmeyi_yakalar() -> None:
    mevcut = [{"id": 4, "source": "yeni", "target": "/eski"}]
    hata = content.redirect_error("/eski", "/yeni", 301, mevcut)
    assert "kilitler" in hata


def test_gecerli_yonlendirme_kabul_edilir() -> None:
    assert content.redirect_error("/eski", "/yeni", 301, []) == ""


def test_bilinmeyen_yonlendirme_turu_reddedilir() -> None:
    assert "301" in content.redirect_error("/eski", "/yeni", 307, [])


def test_adres_normalizasyonu_alan_adini_ve_egik_cizgiyi_atar() -> None:
    assert content.normalize_path("https://bbdstore.com.tr/iade/") == "iade"


# =========================================================== gerekçe ve dal

def test_kisa_gerekce_reddedilir() -> None:
    assert content.reason_error("kısa") != ""
    assert content.reason_error("yeterince uzun gerekçe") == ""


def test_yasal_sayfa_kendi_dalina_dusar() -> None:
    row = {"slug": "mesafeli-satis-sozlesmesi"}
    assert content.page_group(row, legal_slugs={"distance_sales": "mesafeli-satis-sozlesmesi"},
                              faq_slug="") == "legal"


def test_ayarda_olmayan_yasal_sayfa_da_isimden_taninir() -> None:
    assert content.page_group({"slug": "kvkk-aydinlatma"}, legal_slugs={}, faq_slug="") == "legal"


def test_slugify_turkce_harfleri_dogru_cevirir() -> None:
    assert content.slugify("Mesafeli Satış Sözleşmesi") == "mesafeli-satis-sozlesmesi"
    assert content.slugify("Isı ve Işık") == "isi-ve-isik"


def test_yasal_durum_eksik_metni_bildirir() -> None:
    pages = [{"slug": "gizlilik-politikasi", "id": 3, "chars": 4000, "updatedAt": "2026-01-01"}]
    durum = content.legal_status(pages, {"privacy": "gizlilik-politikasi",
                                         "refund": "iade-ve-cayma-hakki"})
    kayitlar = {item["key"]: item for item in durum}
    assert kayitlar["privacy"]["found"] is True
    assert kayitlar["refund"]["found"] is False
    assert kayitlar["cookies"]["found"] is False        # ayarda yok → yine sorulur
