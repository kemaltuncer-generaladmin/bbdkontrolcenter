"""HTML beyaz listesi iki kapıda da AYNI mı — panel kopyası ile sunucu kopyası.

NEDEN BU TEST VAR. HTML temizliği bilerek iki yerde yapılır (K9: arayüzde
gizlemek yetkilendirme değildir):

  panel   → apps/desktop/shell/ui-kit/richtext.js   (çizmeden önce)
  sunucu  → modules/store_cms/backend/content.py    (kaydetmeden önce)

İki kapı DOĞRUDUR; iki FARKLI LİSTE değildir. Listeler bugüne kadar elle eşit
tutuldu ve `store_cms` tam olarak bunun bedelini ödedi: panel kopyası sunucudan
sessizce ayrıştı — `img` etiketinde `width`/`height` yoktu, `frameset` atılacak
etiketler arasında eksikti.

AYRIŞMANIN BEDELİ TEK YÖNLÜ DEĞİL, İKİ YÖNLÜ DE ZARARLI:
  panel dar / sunucu geniş → kullanıcı ekranda gördüğü biçimi kaydeder,
                             geri yüklendiğinde biçim SESSİZCE kaybolur.
  panel geniş / sunucu dar → panel çizer, sunucu atar; daha kötüsü tersi
                             durumda sunucunun attığı bir etiket panelde
                             çizilmeye devam eder ve kapı fiilen tek kalır.

Hiçbiri istisna atmaz, hiçbiri log basmaz. Ayrışma ancak biri şikâyet edince
görünür — bu yüzden karara değil TESTE bağlanır.

NEDEN NODE ÇALIŞTIRMIYORUZ. Test ortamı JS motoru gerektirmez; listeler her iki
dosyada da düz sabit bildirimidir. Python tarafı `ast` ile (yorumlanmaz,
import edilmez), JS tarafı düzenli ifadeyle okunur. Bildirim biçimi değişip
ayrıştırma tutmazsa test SESSİZCE geçmez: `test_ayristirici_iki_dosyada_da_
listeleri_gercekten_buluyor` boş kümeyi hata sayar.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "apps" / "desktop" / "shell" / "ui-kit" / "richtext.js"
SUNUCU = ROOT / "modules" / "store_cms" / "backend" / "content.py"

PANEL_ADI = str(PANEL.relative_to(ROOT))
SUNUCU_ADI = str(SUNUCU.relative_to(ROOT))

#: Karşılaştırılan küme sabitleri. Adları iki dosyada da aynıdır; ad değişirse
#: ayrıştırıcı boş döner ve koruma testi bunu hata sayar.
KUME_SABITLERI = ("ALLOWED_TAGS", "DROP_TAGS", "VOID_TAGS", "STYLE_PROPS", "ALIGN_VALUES")

_JS_KUME = re.compile(r"const\s+(?P<ad>[A-Z_]+)\s*=\s*new Set\(\[(?P<govde>.*?)\]\)", re.DOTALL)
_JS_DIZI = re.compile(r"const\s+(?P<ad>[A-Z_]+)\s*=\s*\[(?P<govde>.*?)\]\s*;", re.DOTALL)
_JS_ESLEME = re.compile(r"const\s+ALLOWED_ATTRS\s*=\s*\{(?P<govde>.*?)\n\};", re.DOTALL)
_JS_ESLEME_SATIRI = re.compile(r"(?P<etiket>\w+)\s*:\s*\[(?P<govde>[^\]]*)\]")
_JS_DIZGE = re.compile(r"'([^']*)'|\"([^\"]*)\"")


# ============================================================== ayrıştırıcılar

def _js_dizgeler(govde: str) -> set[str]:
    return {tek or cift for tek, cift in _JS_DIZGE.findall(govde)}


def _js_sabitler(kaynak: str) -> dict[str, set[str]]:
    """`new Set([...])` ve `[...]` bildirimlerini ada göre toplar."""
    out: dict[str, set[str]] = {}
    for kalip in (_JS_KUME, _JS_DIZI):
        for eslesme in kalip.finditer(kaynak):
            out.setdefault(eslesme.group("ad"), _js_dizgeler(eslesme.group("govde")))
    return out


def _js_oznitelikler(kaynak: str) -> dict[str, set[str]]:
    eslesme = _JS_ESLEME.search(kaynak)
    if not eslesme:
        return {}
    return {
        satir.group("etiket"): _js_dizgeler(satir.group("govde"))
        for satir in _JS_ESLEME_SATIRI.finditer(eslesme.group("govde"))
    }


def _python_degeri(dugum: ast.expr) -> Any:
    """`frozenset({...})` çağrısını da düz sabitleri de değere çevirir."""
    if (
        isinstance(dugum, ast.Call)
        and isinstance(dugum.func, ast.Name)
        and dugum.func.id in ("frozenset", "set")
        and dugum.args
    ):
        return ast.literal_eval(dugum.args[0])
    return ast.literal_eval(dugum)


def _python_sabitler(kaynak: str) -> dict[str, Any]:
    """Modülü İMPORT ETMEDEN üst düzey atamaları okur.

    İçe aktarmak, testi modülün yüklenebilirliğine bağlardı; burada sorulan soru
    listenin İÇERİĞİ, modülün çalışıp çalışmadığı değil (K7).
    """
    out: dict[str, Any] = {}
    for dugum in ast.parse(kaynak).body:
        if not isinstance(dugum, ast.Assign) or len(dugum.targets) != 1:
            continue
        hedef = dugum.targets[0]
        if not isinstance(hedef, ast.Name):
            continue
        try:
            out[hedef.id] = _python_degeri(dugum.value)
        except ValueError:
            continue          # `re.compile(...)` gibi sabit olmayan atamalar
    return out


PANEL_KAYNAK = PANEL.read_text(encoding="utf-8")
SUNUCU_KAYNAK = SUNUCU.read_text(encoding="utf-8")

PANEL_SABIT = _js_sabitler(PANEL_KAYNAK)
SUNUCU_SABIT = _python_sabitler(SUNUCU_KAYNAK)
PANEL_OZNITELIK = _js_oznitelikler(PANEL_KAYNAK)
SUNUCU_OZNITELIK = {
    etiket: set(deger) for etiket, deger in (SUNUCU_SABIT.get("ALLOWED_ATTRS") or {}).items()
}


# ================================================================== raporlama

def _fark(ad: str, sunucu: set[str], panel: set[str]) -> str:
    """Ayrışmayı HANGİ ÖĞE NEREDE EKSİK diliyle anlatır.

    "Listeler farklı" demek yetmez: düzeltecek kişi hangi dosyaya hangi kelimeyi
    yazacağını bilmeli, yoksa iki listeyi yan yana koyup elle karşılaştırır —
    ayrışmanın ilk sebebi de zaten oydu.
    """
    panelde_eksik = sorted(sunucu - panel)
    sunucuda_eksik = sorted(panel - sunucu)
    if not panelde_eksik and not sunucuda_eksik:
        return ""
    satirlar = [f"{ad} iki kapıda ayrışmış:"]
    if panelde_eksik:
        satirlar.append(f"  PANELDE eksik ({PANEL_ADI}): {', '.join(panelde_eksik)}")
    if sunucuda_eksik:
        satirlar.append(f"  SUNUCUDA eksik ({SUNUCU_ADI}): {', '.join(sunucuda_eksik)}")
    satirlar.append("  İki kapı ayrı liste taşıyamaz: eksik olan tarafa eklenir (K9).")
    return "\n".join(satirlar)


def _kume(sabitler: dict[str, Any], ad: str) -> set[str]:
    return set(sabitler.get(ad) or ())


# ==================================================== listelerin karşılaştırması

def test_izin_verilen_etiketler_iki_kapida_ayni() -> None:
    # Sunucuda olup panelde olmayan etiket: kullanıcı o etiketi hiç göremez.
    # Panelde olup sunucuda olmayan: kullanıcı görür, kaydeder, içerik gider.
    hata = _fark("ALLOWED_TAGS", _kume(SUNUCU_SABIT, "ALLOWED_TAGS"),
                 _kume(PANEL_SABIT, "ALLOWED_TAGS"))
    assert not hata, hata


def test_icerigiyle_atilan_etiketler_iki_kapida_ayni() -> None:
    # `frameset` bir kez tam buradan kaçtı: sunucu atıyordu, panel atmıyordu.
    hata = _fark("DROP_TAGS", _kume(SUNUCU_SABIT, "DROP_TAGS"),
                 _kume(PANEL_SABIT, "DROP_TAGS"))
    assert not hata, hata


def test_kapanissiz_etiketler_iki_kapida_ayni() -> None:
    hata = _fark("VOID_TAGS", _kume(SUNUCU_SABIT, "VOID_TAGS"),
                 _kume(PANEL_SABIT, "VOID_TAGS"))
    assert not hata, hata


def test_style_ozellikleri_iki_kapida_ayni() -> None:
    # Bu üçlü güvenliğin kendisidir: `position`/`width`/`opacity`/`z-index`
    # listeye girerse kaplama saldırısı geri gelir. Tek taraflı genişletme
    # öbür kapıyı da fiilen açar, çünkü kullanıcı içeriği panelden geçer.
    hata = _fark("STYLE_PROPS", _kume(SUNUCU_SABIT, "STYLE_PROPS"),
                 _kume(PANEL_SABIT, "STYLE_PROPS"))
    assert not hata, hata


def test_hizalama_degerleri_iki_kapida_ayni() -> None:
    hata = _fark("ALIGN_VALUES", _kume(SUNUCU_SABIT, "ALIGN_VALUES"),
                 _kume(PANEL_SABIT, "ALIGN_VALUES"))
    assert not hata, hata


def test_guvenli_semalar_iki_kapida_ayni() -> None:
    # Panel `URL.protocol` ile karşılaştırdığı için şemaları iki nokta üst üste
    # ile yazar (`https:`); sunucu `urlsplit().scheme` okur ve yazmaz. Fark
    # biçimseldir, KÜME aynı olmalı — bu yüzden son karakter atılarak bakılır.
    panel = {sema.rstrip(":") for sema in _kume(PANEL_SABIT, "SAFE_SCHEMES")}
    hata = _fark("SAFE_SCHEMES", _kume(SUNUCU_SABIT, "SAFE_SCHEMES"), panel)
    assert not hata, hata


def test_etiket_basina_oznitelikler_iki_kapida_ayni() -> None:
    # `img` etiketinin `width`/`height` özniteliği bir kez tam burada ayrıştı:
    # sunucu kabul ediyordu, panel çizerken atıyordu; ölçüsü elle verilmiş her
    # görsel önizlemede bozuk görünüyordu.
    hata = _fark("ALLOWED_ATTRS (etiket listesi)",
                 set(SUNUCU_OZNITELIK), set(PANEL_OZNITELIK))
    assert not hata, hata

    hatalar = [
        _fark(f"ALLOWED_ATTRS[{etiket}]",
              SUNUCU_OZNITELIK.get(etiket, set()), PANEL_OZNITELIK.get(etiket, set()))
        for etiket in sorted(set(SUNUCU_OZNITELIK) | set(PANEL_OZNITELIK))
    ]
    birlesik = "\n".join(hata for hata in hatalar if hata)
    assert not birlesik, birlesik


# ======================================================= ayrıştırıcının kendisi

def test_ayristirici_iki_dosyada_da_listeleri_gercekten_buluyor() -> None:
    # Bu testin en büyük riski YALANCI GEÇMEK: bildirim biçimi değişirse (liste
    # başka bir dosyaya taşınır, `new Set` yerine dizi yazılır) ayrıştırıcı boş
    # döner ve boş küme boş kümeye eşit çıkar. O yüzden dolu olmaları şarttır.
    bos = [
        f"{ad}: panel={len(_kume(PANEL_SABIT, ad))} sunucu={len(_kume(SUNUCU_SABIT, ad))}"
        for ad in (*KUME_SABITLERI, "SAFE_SCHEMES")
        if not _kume(PANEL_SABIT, ad) or not _kume(SUNUCU_SABIT, ad)
    ]
    assert bos == [], (
        "Beyaz liste bildirimi okunamadı; karşılaştırma boş kümeleri eşit sayıp "
        f"sessizce geçerdi: {bos}"
    )
    assert PANEL_OZNITELIK, f"{PANEL_ADI} içinde ALLOWED_ATTRS okunamadı."
    assert SUNUCU_OZNITELIK, f"{SUNUCU_ADI} içinde ALLOWED_ATTRS okunamadı."


def test_ayrisma_raporu_eksik_ogeyi_ve_dosyayi_adiyla_soyler() -> None:
    # Karşılaştırmanın kendisi de kanıtlanır: gerçek ayrışma olmadığı sürece
    # yukarıdaki testler hep geçer ve raporun işe yarayıp yaramadığı görülmez.
    rapor = _fark("ALLOWED_TAGS", {"p", "img", "frameset"}, {"p", "table"})
    assert "frameset" in rapor and "img" in rapor        # panelde eksik olanlar
    assert "table" in rapor                              # sunucuda eksik olan
    assert PANEL_ADI in rapor and SUNUCU_ADI in rapor
    assert _fark("ALLOWED_TAGS", {"p"}, {"p"}) == ""
