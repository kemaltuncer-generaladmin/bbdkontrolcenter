"""Panellerin DOM'a verdiği şey gerçekten bir DÜĞÜM mü.

BULUNAN ARIZA (2026-08-18). Abonelikler ekranında dört sekme — Bekleyenler,
Aktif, Duraklatılmış, İptal edilmiş — tablo yerine dümdüz `[object Object]`
yazıyordu. Sebep tek satırdı:

    nodes.listSlot.append(dataTable({ ... }));        # YANLIŞ
    nodes.listSlot.append(dataTable({ ... }).node);   # DOĞRU

`dataTable()` bir DÜĞÜM değil, DENETLEYİCİ NESNE döndürür (`{node, update,
selection, ...}`) — çünkü çağıran sonradan `update()` diyebilmeli. O nesne
`append()`e verilince DOM onu düğüm sanmaz, dizgeye çevirir: ekrana
`[object Object]` düşer, tablo hiç çizilmez ve HİÇBİR test bunu görmez —
dosya geçerli ESM'dir, sözdizimi denetiminden geçer.

Aynı tuzağın ikinci ağzı `h()`:

    h('div', 'sinif', button('Tekrar dene', {...}))   # YANLIŞ

`h(tag, className, text)` üçüncü argümanı `textContent`e yazar. Düğüm verilince
`[object HTMLButtonElement]` yazılır ve düğme çizilmez. Bu, `store_dashboard`
ayar ekranında bağlantı koptuğunda "Tekrar dene" düğmesinin tam da gerektiği
anda yok olması demekti.

Bu test iki ağzı da kapatır. Denetleyici listesi ELLE YAZILMAZ: `ui-kit`
taranır ve `return { node, ... }` diyen her dışa aktarım denetleyici sayılır —
kit büyüdükçe kapı kendiliğinden genişler.

YORUMLAR ÖNCE SİLİNİR. Türkçe yorum kesme işareti doludur ("07:00'ı", "KDS'e");
yorum atlanmazsa tarayıcı orayı dizge başlangıcı sanar, parantez saymayı
kaybeder ve sağlam çağrıları hatalı işaretler. Bu, testin ilk halinde gerçekten
oldu.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "apps/desktop/shell/ui-kit"

#: Bir düğüm bekleyen DOM alıcıları.
ALICILAR = ("append", "replaceChildren", "prepend", "replaceWith", "before", "after")

#: Denetlenecek kaynaklar. `shell/panels/` ÜRETİLEN kopyadır (git dışı);
#: kaynağı `modules/*/ui/panel/` altında zaten denetleniyor.
DESENLER = (
    "modules/*/ui/panel/**/*.js",
    "apps/desktop/shell/core-panels/**/*.js",
    "apps/desktop/shell/*.js",
)


def _yorumsuz(kaynak: str) -> str:
    """Yorumları boşlukla değiştirir; satır numaraları korunur."""
    cikti: list[str] = []
    i, n = 0, len(kaynak)
    while i < n:
        ch = kaynak[i]
        if ch in "\"'`":
            tirnak = ch
            cikti.append(ch)
            i += 1
            while i < n:
                if kaynak[i] == "\\":
                    cikti.append(kaynak[i:i + 2])
                    i += 2
                    continue
                cikti.append(kaynak[i])
                if kaynak[i] == tirnak:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and i + 1 < n and kaynak[i + 1] == "/":
            while i < n and kaynak[i] != "\n":
                cikti.append(" ")
                i += 1
            continue
        if ch == "/" and i + 1 < n and kaynak[i + 1] == "*":
            while i < n and not (kaynak[i] == "*" and i + 1 < n and kaynak[i + 1] == "/"):
                cikti.append("\n" if kaynak[i] == "\n" else " ")
                i += 1
            cikti.append("  ")
            i += 2
            continue
        cikti.append(ch)
        i += 1
    return "".join(cikti)


def _kapanis(kaynak: str, acilis: int) -> int:
    """`(` konumundan eşleşen `)` konumu; dizgeler atlanır. Bulunamazsa -1."""
    derinlik, i = 0, acilis
    while i < len(kaynak):
        ch = kaynak[i]
        if ch in "\"'`":
            tirnak = ch
            i += 1
            while i < len(kaynak):
                if kaynak[i] == "\\":
                    i += 2
                    continue
                if kaynak[i] == tirnak:
                    break
                i += 1
        elif ch in "([{":
            derinlik += 1
        elif ch in ")]}":
            derinlik -= 1
            if derinlik == 0:
                return i
        i += 1
    return -1


def _denetleyiciler() -> set[str]:
    """`return { node, ... }` diyen kit dışa aktarımları."""
    bulunan: set[str] = set()
    for path in sorted(KIT.glob("*.js")):
        kaynak = _yorumsuz(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"^export function (\w+)", kaynak, re.MULTILINE):
            ad = m.group(1)
            govde = kaynak[m.end():]
            sonraki = re.search(r"\nexport (?:function|const) ", govde)
            if sonraki:
                govde = govde[: sonraki.start()]
            for rm in re.finditer(r"\n  return \{", govde):
                if re.match(r"\s*\n?\s*node[,\s}]", govde[rm.end(): rm.end() + 400]):
                    bulunan.add(ad)
                    break
    return bulunan


def _kaynaklar() -> list[Path]:
    bulunan: list[Path] = []
    for desen in DESENLER:
        bulunan.extend(
            path for path in ROOT.glob(desen)
            if path.is_file() and "node_modules" not in path.parts
            and "panels" not in path.relative_to(ROOT).parts[:3]
        )
    return sorted(set(bulunan))


def test_kit_denetleyicileri_bulunuyor() -> None:
    """Kapının kendisi çalışıyor mu: liste boşsa test hiçbir şey kanıtlamaz."""
    denetleyiciler = _denetleyiciler()
    assert "dataTable" in denetleyiciler, (
        "dataTable denetleyici olarak tanınmadı; tarayıcı bozulmuş ve bu kapı "
        "artık hiçbir şeyi korumuyor olabilir."
    )
    assert len(denetleyiciler) >= 5


def test_denetleyici_nesne_DOM_a_verilmez() -> None:
    """`append(dataTable({...}))` — `.node` unutulmuş mü."""
    denetleyiciler = _denetleyiciler()
    adlar = "|".join(sorted(denetleyiciler))
    desen = re.compile(rf"\.(?:{'|'.join(ALICILAR)})\s*\(\s*({adlar})\s*\(")

    hatalar: list[str] = []
    for path in _kaynaklar():
        kaynak = _yorumsuz(path.read_text(encoding="utf-8", errors="replace"))
        for m in desen.finditer(kaynak):
            acilis = kaynak.index("(", m.end() - 1)
            kapanis = _kapanis(kaynak, acilis)
            if kapanis < 0:
                continue
            if kaynak[kapanis + 1: kapanis + 6] == ".node":
                continue
            satir = kaynak[: m.start()].count("\n") + 1
            hatalar.append(
                f"{path.relative_to(ROOT)}:{satir} — {m.group(1)}() bir DÜĞÜM değil "
                f"denetleyici nesne döndürür; sonuna `.node` gerekiyor."
            )

    assert not hatalar, (
        "Denetleyici nesne doğrudan DOM'a verilmiş; ekranda `[object Object]` "
        "yazar ve bileşen hiç çizilmez:\n  " + "\n  ".join(hatalar)
    )


def test_h_ucuncu_argumanina_dugum_verilmez() -> None:
    """`h(tag, class, text)` üçüncü argümanı METİNDİR."""
    dugum_dondurenler = (
        "h", "button", "blockedButton", "badge", "card", "alertBox", "hintBox",
        "emptyState", "skeletonRows",
    )
    desen = re.compile(
        rf"(?<![\w.$])h\s*\(\s*[^,()]+,\s*[^,()]+,\s*({'|'.join(dugum_dondurenler)})\s*\("
    )

    hatalar: list[str] = []
    for path in _kaynaklar():
        kaynak = _yorumsuz(path.read_text(encoding="utf-8", errors="replace"))
        for m in desen.finditer(kaynak):
            satir = kaynak[: m.start()].count("\n") + 1
            hatalar.append(
                f"{path.relative_to(ROOT)}:{satir} — h()'nin üçüncü argümanı "
                f"`textContent`e yazılır; {m.group(1)}() bir düğüm döndürüyor."
            )

    assert not hatalar, (
        "Düğüm, metin beklenen yere verilmiş; ekranda `[object HTMLElement]` "
        "yazar ve bileşen çizilmez:\n  " + "\n  ".join(hatalar)
    )


def test_kapi_bozuk_kullanimi_GERCEKTEN_yakalar(tmp_path: Path) -> None:
    """Kapının yalancı yeşil vermediğini kanıtlar.

    Yukarıdaki iki test "hiç bulgu yok" diye geçiyor. Tarayıcı sessizce
    bozulsaydı da aynı şekilde geçerdi; bu yüzden bilerek bozuk bir örnek
    verilir ve yakalandığı görülür.
    """
    denetleyiciler = _denetleyiciler()
    adlar = "|".join(sorted(denetleyiciler))
    desen = re.compile(rf"\.(?:{'|'.join(ALICILAR)})\s*\(\s*({adlar})\s*\(")

    bozuk = "slot.append(dataTable({ columns: [], rows: [] }));\n"
    saglam = "slot.append(dataTable({ columns: [], rows: [] }).node);\n"

    for kaynak, beklenen in ((bozuk, True), (saglam, False)):
        temiz = _yorumsuz(kaynak)
        yakalandi = False
        for m in desen.finditer(temiz):
            acilis = temiz.index("(", m.end() - 1)
            kapanis = _kapanis(temiz, acilis)
            if kapanis >= 0 and temiz[kapanis + 1: kapanis + 6] != ".node":
                yakalandi = True
        assert yakalandi is beklenen


def test_turkce_yorum_kesme_isareti_taramayi_bozmaz() -> None:
    """Yorumdaki kesme işareti sağlam çağrıyı hatalı işaretlememeli.

    Bu testin ilk halinde `calendarCard()` hatalı işaretlendi: yorumdaki
    "07:00'ı" dizge başlangıcı sanılmış, parantez sayımı kaymıştı.
    """
    kaynak = (
        "// ÜÇÜNCÜ HÂL: üretildi ama 07:00'ı bekliyor, KDS'e düşmedi.\n"
        "box.append(dataTable({ columns: [], rows: [] }).node);\n"
    )
    temiz = _yorumsuz(kaynak)
    desen = re.compile(r"\.append\s*\(\s*(dataTable)\s*\(")
    m = desen.search(temiz)
    assert m is not None
    acilis = temiz.index("(", m.end() - 1)
    kapanis = _kapanis(temiz, acilis)
    assert temiz[kapanis + 1: kapanis + 6] == ".node"
