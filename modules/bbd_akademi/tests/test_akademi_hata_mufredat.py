"""Merkezi müfredat kapsamı (Akademi README §14 madde 114, iş 62-hata-mufredat) — KM tarafı.

Kullanıcı: "müfredat mantığı merkezi olmalı. tyt ayt veya sınıf düzeyine göre öğrenci, seçip gönderebilmeli".

- Ayarlar → Kurum → "Müfredat kapsamı": `curriculum.selectableScopes` (`standard` | `assigned` | `all`); eski sunucu
  alanı göndermezse `standard` görünür; değişince yalnız `curriculum` grubu gider (kısmi güncelleme).
- Hata defteri gelen kutusunda kaydın kapsam etiketi önde ("TYT · Fizik · …"; mantık testi
  `test_akademi_hata_gelen_kutusu.py`).
"""

from __future__ import annotations

from typing import Any

from modules.bbd_akademi.tests.test_akademi_ogretmen_portali import SCOPE
from modules.bbd_akademi.tests.test_akademi_ogretmen_koc import node_run

CURRICULUM = (
    SCOPE.replace("includes('Öğretmen kapsamı')", "includes('Seçilebilir kapsamlar')")
    .replace("select.value = 'all';", "select.value = 'assigned';")
)


def test_kurum_ayari_mufredat_kapsami() -> None:
    assert CURRICULUM != SCOPE
    out: dict[str, Any] = node_run(CURRICULUM)
    assert out["cardFound"]
    assert out["options"] == [
        ["standard", "Sınıfı + komşu sınıflar + TYT / AYT (alanına göre) / LGS"],
        ["assigned", "Yalnız sınıfından gelenler"],
        ["all", "Kademenin bütün sınıfları ve sınavları (AYT bütün alanlar)"],
    ]
    assert out["initial"] == "standard"  # eski sunucu alanı göndermiyor
    assert out["a11y"] == []
    assert out["patches"] == [{"curriculum": {"selectableScopes": "assigned"}}]
    assert out["after"] == "assigned"
    assert ["Kurum ayarları kaydedildi.", "good"] in out["toasts"]
