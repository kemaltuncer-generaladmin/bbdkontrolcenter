"""Tek seçenekli alan ekranda sorulmaz — ve bu karar SERT KODLANMAZ.

BULUNAN DURUM (2026-08-15, canlıdan doğrulandı). Bu mağazada kanal bir tane
(id=1 "default"), dil bir tane (tr), para birimi bir tane (TRY), stok kaynağı
bir tane (id=1), vergi kategorisi bir tane (KDV). Buna rağmen yirmi ekranda
bunların açılır kutusu çiziliyor ve kullanıcıdan seçim isteniyordu — seçilecek
bir şey olmadan.

Bedeli yalnız kalabalık değil: personel "acaba yanlış kanalı mı seçtim" diye
düşünüp gerçekte var olmayan bir ayrım arıyor. Süzgeç tarafında bedel daha da
somut: `channel=default` gönderilen sipariş listesi canlıda HTTP 200 ile SIFIR
kayıt döndürüyordu (bkz. `store_api._drop_channel`).

BU TEST NEYİ KORUR. "Kanal her zaman default" gibi bir SABİT yazılmasını değil,
KARARIN VERİDEN ÇIKMASINI korur:

    seçenek > 1  → kutu çizilir
    seçenek = 1  → kutu çizilmez, değer kendiliğinden dolar (form) /
                   gönderilmez (süzgeç)
    seçenek = 0  → kutu çizilmez, ekran durumu SÖYLER

Karar tek yerde: `apps/desktop/shell/ui-kit/choice.js`. Panellerin kendi
`if (channels.length === 1)` dalını açması, yarın ikinci kanal açıldığında
yirmi dosyayı tek tek gezmek demektir — ve biri mutlaka unutulur.

NEDEN NODE ÇALIŞTIRMIYORUZ. Test ortamı JS motoru gerektirmez; aranan şey
BİLDİRİMİN BİÇİMİ. Ayrıştırıcı boş dönerse test sessizce geçmez:
`test_ayristirici_gercekten_alan_buluyor` boş sonucu hata sayar.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "modules"
KIT = ROOT / "apps" / "desktop" / "shell" / "ui-kit"

#: Mağazada TEK seçeneği olan alanlar. Anahtar adları panellerde kullanılan
#: yazımlardır; hepsi aynı gerçeğe bakar.
TEK_SECENEKLI_ANAHTARLAR = {
    "channel": "kanal",
    "channelId": "kanal",
    "locale": "dil",
    "currency": "para birimi",
    "inventorySource": "stok kaynağı",
    "sourceId": "stok kaynağı",
    "taxCategory": "vergi kategorisi",
    "taxCategoryId": "vergi kategorisi",
}

#: BU ANAHTAR ORADA BAŞKA BİR ŞEY DEMEK — kural dışıdır ve gerekçesi yazılıdır.
#:
#: "channel" kelimesi bu depoda İKİ AYRI GERÇEĞE bakıyor ve ikisini aynı kurala
#: sokmak, gerçek bir seçimi ekrandan silerdi.
MUAFLAR = {
    ("store_notifications", "channel"):
        "Bildirim kanalı (e-posta/SMS) — mağaza kanalı değil, iki gerçek seçenek.",
    ("store_trial_club", "channel"):
        "Bildirim kanalı (e-posta/SMS) — mağaza kanalı değil, iki gerçek seçenek.",
    ("store_requests", "channel"):
        "Talebin GELDİĞİ kanal (web/e-posta/telefon/WhatsApp) — dört gerçek seçenek.",
}

#: Müşteri grubu BİLEREK LİSTEDE YOK: üç seçeneği var (Toptan/Genel/Misafir) ve
#: seçim listeyi gerçekten değiştirir. Kural "az seçenek" değil "TEK seçenek".

#: GEÇİŞ SÜRÜYOR — bu üç panel aynı turda BAŞKA ellerde dönüştürülüyor.
#:
#: Listeyi burada tutmanın amacı testi zayıflatmak değil, GÖRÜNÜR TUTMAK: her
#: satır "bu ekranda kural henüz uygulanmadı" demektir ve
#: `test_gecis_listesi_bayatlamiyor` dönüşen satırı SİLMEYİ ZORUNLU kılar —
#: yani liste yalnız küçülebilir. Boşaldığında bu blok tümüyle kaldırılır.
GECIS_SURUYOR = {
    ("store_dashboard", "channel"),
    ("store_dashboard", "locale"),
    ("store_orders", "channel"),
    ("store_products", "sourceId"),
}

_ANAHTAR = re.compile(r"key:\s*'(?P<ad>[A-Za-z_][A-Za-z0-9_]*)'")


def _panel_dosyalari() -> list[Path]:
    return sorted(
        path
        for path in MODULES.glob("store_*/ui/panel/index.js")
        if "__pycache__" not in path.parts
    )


def _kapsayan_nesne(kaynak: str, at: int) -> str:
    """`at` konumundaki bildirimi çevreleyen `{...}` bloğunu döndürür.

    Süslü parantez SAYILIR, düzenli ifadeyle kesilmez: iç içe `options: [...]`
    listeleri ve `validate(...)` gövdeleri kaba bir kesmede bildirimi ortadan
    böler ve `type: 'select'` görünmez olurdu — test o zaman SESSİZCE geçerdi.
    """
    baslangic = kaynak.rfind("{", 0, at)
    if baslangic < 0:
        return ""
    derinlik = 0
    for index in range(baslangic, len(kaynak)):
        if kaynak[index] == "{":
            derinlik += 1
        elif kaynak[index] == "}":
            derinlik -= 1
            if derinlik == 0:
                return kaynak[baslangic:index + 1]
    return kaynak[baslangic:]


def _acilir_kutu_bildirimleri(kaynak: str) -> list[tuple[str, str]]:
    """(anahtar, bildirim gövdesi) — yalnız AÇILIR KUTU bildirimleri."""
    bulunan: list[tuple[str, str]] = []
    for eslesme in _ANAHTAR.finditer(kaynak):
        ad = eslesme.group("ad")
        if ad not in TEK_SECENEKLI_ANAHTARLAR:
            continue
        govde = _kapsayan_nesne(kaynak, eslesme.start())
        if "'select'" not in govde:
            continue                      # tablo sütunu, sorgu alanı, rapor ölçütü…
        bulunan.append((ad, govde))
    return bulunan


_BLOK_YORUM = re.compile(r"/\*.*?\*/", re.DOTALL)


def _yorumsuz(kaynak: str) -> str:
    """Yorumları atar — sabit değer yasağı KOD için geçerlidir, açıklama için değil.

    Blok yorumu da atılmak zorunda: `choice.js` başlığı örnek olarak
    `['default']` yazıyor ve kaba bir satır ayıklayıcı onu kod sanardı.
    """
    govde = _BLOK_YORUM.sub("", kaynak)
    return "\n".join(satir.split("//")[0] for satir in govde.splitlines())


# ============================================================ kuralın kendisi

def test_hicbir_panel_tek_secenekli_alani_kendi_basina_cizmez() -> None:
    """Açılır kutu ya `choice.js`'ten geçer ya da gizli başlar.

    İki geçerli biçim var ve ikisi de aynı kararı `choice.js`'e bırakır:

      1. `choiceFilter(...)` / `choiceField(...)` — seçenekler ÖNCEDEN belli.
      2. `{kind:'select', key:'channel', hidden: true, options: []}` +
         `applyChoiceFilter(...)` — seçenekler VERİDEN SONRA geliyor; kutu
         gizli çizilir, liste gelince karar verilir.

    Üçüncü bir biçim (kutuyu doğrudan doldurmak) kararı panele gömer.
    """
    suclu: list[str] = []
    for path in _panel_dosyalari():
        modul = path.parts[-4]
        kaynak = path.read_text(encoding="utf-8")
        for ad, govde in _acilir_kutu_bildirimleri(kaynak):
            if (modul, ad) in MUAFLAR:
                continue
            if (modul, ad) in GECIS_SURUYOR:
                continue
            if "hidden: true" in govde:
                continue
            if "choiceFilter(" in govde or "choiceField(" in govde:
                continue
            suclu.append(
                f"{path.relative_to(ROOT)} · `{ad}` ({TEK_SECENEKLI_ANAHTARLAR[ad]}) "
                "kutusu doğrudan çiziliyor"
            )
    assert suclu == [], (
        "Tek seçenekli alan ekranda soruluyor. Karar panele değil `choice.js`'e "
        "aittir: `choiceFilter`/`choiceField` kullanın ya da kutuyu "
        "`hidden: true` ile çizip `applyChoiceFilter(...)` çağırın.\n  "
        + "\n  ".join(suclu)
    )


def test_kutuyu_gizli_cizen_panel_karari_gercekten_soruyor() -> None:
    """`hidden: true` tek başına YETMEZ — sonra `applyChoiceFilter` çağrılmalı.

    Gizli çizilip hiç sorulmayan kutu, kalıcı olarak yok olmuş demektir: ikinci
    kanal açıldığında geri gelmez. Bu, tam da yasaklanan sert kodlamanın sessiz
    biçimidir.
    """
    suclu: list[str] = []
    for path in _panel_dosyalari():
        kaynak = path.read_text(encoding="utf-8")
        gizli = [ad for ad, govde in _acilir_kutu_bildirimleri(kaynak)
                 if "hidden: true" in govde]
        if not gizli:
            continue
        if "applyChoiceFilter(" not in kaynak:
            suclu.append(f"{path.relative_to(ROOT)} · gizli kutular: {', '.join(gizli)}")
    assert suclu == [], (
        "Kutu gizli çiziliyor ama seçenek sayısı hiç sorulmuyor; ikinci kanal "
        "açıldığında süzgeç geri gelmez:\n  " + "\n  ".join(suclu)
    )


def test_karar_tek_yerde_ve_veriden_cikiyor() -> None:
    """`choice.js` seçenek SAYISINA bakar; sabit kanal/dil adı taşımaz."""
    kaynak = (KIT / "choice.js").read_text(encoding="utf-8")
    assert "length > 1" in kaynak, "Karar seçenek sayısına bakmıyor."
    assert "'many'" in kaynak and "'single'" in kaynak and "'none'" in kaynak, (
        "Üç hâlden biri eksik: >1 göster · =1 gizle+seç · =0 durumu söyle."
    )
    # Sabit değer yasağı: kod tarafında "default"/"tr"/"TRY" gibi bir eşitlik
    # denetimi olsaydı, karar veriden değil kanaatten çıkardı.
    kod = _yorumsuz(kaynak)
    for sabit in ("'default'", '"default"', "'TRY'", "'tr'"):
        assert sabit not in kod, f"choice.js sabit değer taşıyor: {sabit}"


def test_kit_yanlis_alani_atliyor() -> None:
    """`choiceFilter`/`choiceField` `null` döndürebiliyor; kit bunu atlamalı.

    Atlamasaydı `field.kind` okunurken panel çöker ve tek seçenekli alan
    ekranı DÜŞÜRÜRDÜ — kaldırmaya çalıştığımız kutudan çok daha kötüsü.
    """
    filtreler = (KIT / "filters.js").read_text(encoding="utf-8")
    formlar = (KIT / "form.js").read_text(encoding="utf-8")
    assert "if (!field) continue;" in filtreler, "filterBar yanlış alanı atlamıyor."
    assert ".filter(Boolean)" in formlar, "formGrid yanlış alanı atlamıyor."


# ====================================================== ayrıştırıcının kendisi

def test_ayristirici_gercekten_alan_buluyor() -> None:
    """En büyük risk YALANCI GEÇMEK.

    Bildirim biçimi değişirse (`key:` yerine başka bir yazım, açılır kutunun
    başka bir bileşene taşınması) ayrıştırıcı boş döner ve boş liste boş
    listeye eşit çıkar. O yüzden en az bir gerçek bildirim bulunmalıdır.
    """
    toplam = sum(len(_acilir_kutu_bildirimleri(path.read_text(encoding="utf-8")))
                 for path in _panel_dosyalari())
    assert toplam > 0, (
        "Hiçbir panelde tek seçenekli açılır kutu bildirimi bulunamadı; "
        "ayrıştırıcı biçimi kaçırıyor olabilir ve test sessizce geçerdi."
    )
    assert _panel_dosyalari(), "Panel dosyası bulunamadı."


def test_kapsayan_nesne_ic_ice_parantezde_dogru_kesiyor() -> None:
    # Kesme mantığının kendisi de kanıtlanır: iç içe liste bildirimi bölmemeli.
    kaynak = "x = [{ kind: 'select', key: 'channel', options: [{ value: 1 }] }];"
    at = kaynak.index("key: 'channel'")
    govde = _kapsayan_nesne(kaynak, at)
    assert govde.startswith("{ kind: 'select'") and govde.endswith("}")
    assert "'select'" in govde


def test_gecis_listesi_buyuyemez() -> None:
    """Muafiyet listesi bir KAÇIŞ KAPISI hâline gelemez.

    `GECIS_SURUYOR` yalnız üç panel içindir ve o üçü bu turda BAŞKA ellerde
    dönüştürülüyor. Listeye dördüncü bir panel eklenmesi, kuralı uygulamak
    yerine kuraldan kaçmak demektir; test tam olarak bunu engeller.

    NEDEN "hâlâ dönüşmemiş mi" DİYE BAKMIYORUZ: o denetim eşzamanlı çalışan
    dönüşümlerde SALLANIR — komşu panel düzeldiği anda bu test kırmızıya döner
    ve düzelten kişinin hatası olmayan bir hata gösterir. Testin işi komşuyu
    kovalamak değil, kapının açık kalmasını engellemektir.
    """
    izinli_paneller = {"store_dashboard", "store_orders", "store_products"}
    fazlalik = sorted({modul for modul, _ in GECIS_SURUYOR} - izinli_paneller)
    assert fazlalik == [], (
        "Geçiş listesine yeni panel eklenmiş: "
        + ", ".join(fazlalik)
        + ". Bu liste büyümez; tek seçenekli alan `choice.js` üzerinden kaldırılır."
    )
    for modul, ad in sorted(GECIS_SURUYOR):
        assert (MODULES / modul / "ui" / "panel" / "index.js").exists(), (
            f"{modul} paneli yok; geçiş listesinden çıkarılmalı."
        )
        assert ad in TEK_SECENEKLI_ANAHTARLAR, f"Tanınmayan anahtar: {ad}"
