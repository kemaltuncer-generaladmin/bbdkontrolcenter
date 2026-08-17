"""Kimlik senkronu — platform yeteneği (ADR 0021, Kontrol Merkezi tarafı).

**Yetenektir, modül değildir**: `modules/` altına konamaz, kapatılamaz,
manifest taşımaz. Kimlik çekirdeğe aittir (CLAUDE.md — kavram ayrımı).

Üç iş yapar:

  1. **Eşleme.** Kurulum token'ını kasadan okur; yoksa merkezden eşleme
     kodu ile alır ve kasaya yazar (K8 — depoya yazılmaz).
  2. **Kadro çekme.** `revision` değişmişse roster'ı çeker, yerel önbelleğe
     yazar. Değişmemişse veri çekilmez.
  3. **Yazmayı merkeze taşıma.** Kullanıcı/rol yazması yalnız çevrimiçidir;
     ağ yoksa **denenmez** (ADR 0021 §3).

**SERVİS YOKSA HİÇBİR YETENEK GERİLEMEZ** (ADR 0021 — Sonuçlar). `enabled`
kapalıyken ya da merkez ulaşılamazken Kontrol Merkezi bugünkü gibi tek makinede
çalışır: giriş yerel depodan yapılır, hiçbir ekran kaybolmaz. Bu yüzden bu
paketteki hiçbir hata çekirdeği düşürmez (K7).
"""

from .errors import (
    IdentitySyncError,
    NotPaired,
    PairRejected,
    WriteRequiresConnection,
)
from .service import IdentitySync

__all__ = [
    "IdentitySync",
    "IdentitySyncError",
    "NotPaired",
    "PairRejected",
    "WriteRequiresConnection",
]
