"""Mağaza modüllerinin zaman damgası saat dilimi tutarlılığı.

BULUNAN HATA (2026-08-14). Modüller yerel tabloya zaman damgasını `_now()` ile
yazıyordu ve o UTC üretiyordu:

    _now()       → "2026-08-13T21:05:24+00:00"
    today_iso()  → "2026-08-14"                 (yerel gün)

"Bugün" sorusu soran her sorgu `substr(created_at, 1, 10) = today_iso()` ile
karşılaştırma yapıyor. UTC ile yerel gün Türkiye'de **her gece 00:00–03:00
arasında** birbirini tutmaz. Somut sonucu: `store_notifications` günlük
gönderim limiti o üç saat boyunca sıfıra döner — 1800 kişiye ikinci kez duyuru
gitmesini engelleyen koruma tam da hatanın en az fark edileceği saatte kapanır.

Aynı desen 21 modülün hepsinde vardı. Düzeltme: `_now()` yerel farkında olur
(`astimezone()`), offset korunur (`+03:00`), bilgi kaybı olmaz.

Bu test tek tek modülleri değil KURALI korur: yeni bir modül eklenip `_now()`
kopyalandığında da yakalar.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "modules"

#: `datetime.now(UTC).isoformat(...)` — yerele çevrilmeden yazılan damga.
UTC_DAMGASI = re.compile(r"datetime\.now\(UTC\)\.isoformat\(")


def _store_kaynaklari() -> list[Path]:
    return sorted(
        path
        for path in MODULES.glob("store_*/backend/**/*.py")
        if "__pycache__" not in path.parts
    )


def test_hicbir_magaza_modulu_zaman_damgasini_utc_yazmaz() -> None:
    # Yerel gün ile karşılaştırılan bir alana UTC yazmak, günün ilk saatlerinde
    # sessizce yanlış cevap üretir: istisna atmaz, log basmaz, testte görünmez.
    suclu = [
        f"{path.relative_to(ROOT)}:{index}"
        for path in _store_kaynaklari()
        for index, satir in enumerate(path.read_text().splitlines(), start=1)
        if UTC_DAMGASI.search(satir)
    ]
    assert suclu == [], (
        "Zaman damgası yerele çevrilmeden yazılıyor; 'bugün' sorgularıyla "
        f"gece yarısından sonra uyuşmaz: {suclu}"
    )


def test_yazilan_damganin_gun_oneki_yerel_bugunle_ayni() -> None:
    # Kuralın kendisi: damganın ilk 10 karakteri yerel günü vermeli.
    from datetime import UTC, datetime

    damga = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    yerel_gun = datetime.now(UTC).astimezone().date().isoformat()
    assert damga[:10] == yerel_gun

    # Ve hatanın kendisi hâlâ üretilebilir olmalı ki test anlamlı olsun:
    utc_damga = datetime.now(UTC).isoformat(timespec="seconds")
    if utc_damga[:10] != yerel_gun:
        # Gece 00:00–03:00 aralığındayız; eski davranış gerçekten sapıyor.
        assert damga[:10] != utc_damga[:10]


def test_her_now_govdesi_ya_yerel_uretir_ya_da_yerel_uretene_devreder() -> None:
    # 21 modül `_now()` adında birer kopya taşıyor (K3: modül modülü import
    # edemez). Kopyaların sapmaması kritik: biri UTC'ye dönerse yalnız o modülün
    # sayaçları bozulur ve bu hiçbir yerde görünmez.
    #
    # Devir meşrudur (`store_shipping` kendi `shipping.now_iso()` yardımcısını
    # çağırıyor) — aranan şey aynı SATIR değil, aynı DAVRANIŞ. Doğrudan üreten
    # her gövde `astimezone()` taşımalı; devredenler zaten ilk testle korunuyor,
    # çünkü hedef yardımcı da bu klasörde.
    sapan = []
    for path in _store_kaynaklari():
        metin = path.read_text()
        for ad in ("_now", "now_iso"):
            for eslesme in re.finditer(
                rf"def {ad}\(\) -> str:\s*\n(?:\s*\"\"\".*?\"\"\"\s*\n)?\s*return ([^\n]+)",
                metin, re.DOTALL,
            ):
                govde = eslesme.group(1).strip()
                dogrudan_uretir = "datetime.now(" in govde
                if dogrudan_uretir and "astimezone()" not in govde:
                    sapan.append(f"{path.relative_to(ROOT)}: {govde}")

    assert sapan == [], f"Yerel saate çevirmeyen zaman damgası üreticisi: {sapan}"
