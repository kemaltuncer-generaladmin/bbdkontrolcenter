"""Kiosk verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın kararlarının çoğu tek bir sözlüğe bakıp "bu kayıt
ekranda ne gösterir, bu değer kabul edilir mi" sorusuna cevap vermekten ibaret:
kiosk çevrimiçi mi, bekleyen kodu kullanılabilir mi, gerekçe yeterince uzun mu.
Servise gömülselerdi tek satırı bile ağ taklidi olmadan sınanamazdı; burada
hepsi girdi→çıktı fonksiyonudur.

ALAN ADLARI camelCase GELİR, snake_case ÇIKAR. Kantin (Laravel) tel üzerinde
`pairedAt`, `lastSeenAt`, `revokedAt` yazıyor; Kontrol Merkezi panelleri
snake_case okuyor. Çeviri TEK YERDE, `kiosk_row` içinde yapılır — iki yerde
yapılsaydı biri unutulduğunda ekran alanı sessizce boş gösterirdi.

EŞLEME KODU BU DOSYADA HİÇ GEÇMEZ. Kantin listede kodu döndürmüyor (yalnız
sha256'sı saklanıyor), bu yüzden burada da yoktur: kod tek bir yerde, üretildiği
anın yanıtında görünür.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# ============================================================ gerekçe ve ad

#: Gerekçenin en az uzunluğu. Kantin de 10 istiyor (`KioskController::revoke`);
#: burada TEKRAR doğrulanır çünkü arayüzde alanı zorunlu göstermek
#: yetkilendirme değildir (K9) ve istemci gövdeyi elle kurabilir.
MIN_REASON = 10

#: En çok. Kantin `max:255` uyguluyor; burada daha dar bir sınır seçildi ki
#: denetim izi okunabilir kalsın ve iki uçta farklı sınır sürprizi olmasın.
MAX_REASON = 160

#: Kiosk adının sınırları. Kantin `min:2, max:100` istiyor; birebir aynı.
MIN_NAME = 2
MAX_NAME = 100

#: Kiosk kaç dakika sessiz kalırsa çevrimdışı sayılsın (ayarla ezilir).
ONLINE_AFTER_MINUTES = 5


def text(value: Any) -> str:
    """Değeri kırpılmış metne çevirir. `None` boş dizedir."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    """Tamsayıya çevirir; çevrilemiyorsa `default`. İstisna atmaz."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def now_iso() -> str:
    """Yerel iz için zaman damgası. UZAK VERİ DAMGALANMAZ — o kantinden gelir."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 damgasını okur. Okunamazsa `None` — istisna atmaz.

    Kantin `toIso8601String()` kullanıyor ve damga saat dilimi taşıyor
    (`+03:00`); `Z` ile biten biçim de kabul edilir. Dilimsiz bir damga gelirse
    UTC varsayılır — tahmin etmek, damgayı tümüyle atmaktan iyidir ve tek
    etkisi "en son görülme" satırıdır.
    """
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def minutes_since(value: Any, *, now: datetime | None = None) -> float | None:
    """Damganın üzerinden kaç dakika geçti. Damga okunamazsa `None`."""
    stamp = parse_iso(value)
    if stamp is None:
        return None
    moment = now or datetime.now(UTC)
    return (moment - stamp).total_seconds() / 60.0


def reason_error(value: str, *, max_length: int = MAX_REASON) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    clean = text(value)
    if len(clean) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı; "
                "denetim kaydına bu metin yazılır.")
    if len(clean) > max_length:
        return f"Gerekçe en çok {max_length} karakter olabilir."
    return ""


def name_error(name: str) -> str:
    """Kiosk adı kabul edilebilir mi.

    Sınırlar kantinin doğrulamasıyla BİREBİR aynı: burada geçip orada 422 alan
    bir ad, kullanıcıya ham bir doğrulama hatası olarak görünürdü.
    """
    clean = text(name)
    if len(clean) < MIN_NAME:
        return f"Kiosk adı en az {MIN_NAME} karakter olmalı."
    if len(clean) > MAX_NAME:
        return f"Kiosk adı en çok {MAX_NAME} karakter olabilir."
    return ""


# ==================================================================== kiosk

def pairing_view(raw: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Bekleyen eşleme kodunun ekranda görünen hâli. KOD BURADA YOKTUR.

    `usable` KANTİNDEN GELİR ama burada süreye karşı bir kez daha denetlenir:
    liste 9 dakika önce çekilmiş olabilir ve o sırada kullanılabilir olan kod
    ekrana bakılırken ölmüş olabilir. İki kaynağın da "evet" demesi gerekir —
    ekranda "kod hazır" yazarken kantinde ölmüş bir kod, yöneticiyi kantine
    boşuna yollar.
    """
    source = raw if isinstance(raw, dict) else {}
    expires = text(source.get("expiresAt"))
    kalan = minutes_since(expires, now=now)
    # `minutes_since` GEÇEN süreyi verir; gelecekteki bir damgada negatiftir.
    canli = kalan is not None and kalan < 0
    return {
        "usable": bool(source.get("usable")) and canli,
        "expires_at": expires or None,
        "used_at": text(source.get("usedAt")) or None,
        # Kalan dakika: panel geri sayımı bununla çizer. Negatif değer
        # gösterilmez; ölmüş kodun "−3 dk" diye görünmesi anlamsız olurdu.
        "expires_in_minutes": max(0, int(-kalan)) if canli else 0,
    }


def kiosk_row(raw: dict[str, Any], *, online_after: int = ONLINE_AFTER_MINUTES,
              now: datetime | None = None) -> dict[str, Any]:
    """Kiosk kaydının ekranda görünen hâli — ÜÇ DURUM.

    `state`:
      · `revoked`  iptal edilmiş — bir daha bağlanamaz, kaydı durur
      · `online`   son görülme eşiğin içinde
      · `offline`  eşleşmiş ama sessiz, ya da hiç eşleşmemiş

    HİÇ EŞLEŞMEMİŞ KIOSK "ÇEVRİMDIŞI" DEĞİLDİR, "BEKLİYOR"DUR ve bu ayrım
    `paired` bayrağıyla taşınır: ikisini aynı göstermek, kurulumu bekleyen yeni
    bir cihazı arızalı bir cihazla karıştırırdı.
    """
    revoked_at = text(raw.get("revokedAt"))
    last_seen = text(raw.get("lastSeenAt"))
    paired_at = text(raw.get("pairedAt"))
    paired = bool(raw.get("paired")) or bool(paired_at)

    gecen = minutes_since(last_seen, now=now)
    online = (not revoked_at) and paired and gecen is not None and gecen <= max(1, online_after)

    state = "revoked" if revoked_at else ("online" if online else "offline")
    return {
        "id": as_int(raw.get("id")),
        "name": text(raw.get("name")),
        "platform": text(raw.get("platform")) or None,
        "app_version": text(raw.get("appVersion")) or None,
        "paired": paired,
        "paired_at": paired_at or None,
        "last_seen_at": last_seen or None,
        "last_seen_minutes": None if gecen is None else max(0, int(gecen)),
        "revoked": bool(revoked_at),
        "revoked_at": revoked_at or None,
        "revoked_reason": text(raw.get("revokedReason")) or None,
        "created_at": text(raw.get("createdAt")) or None,
        "online": online,
        "state": state,
        "state_label": {"online": "Çevrimiçi", "offline": "Çevrimdışı",
                        "revoked": "İptal edildi"}[state],
        "tone": {"online": "good", "offline": "warn", "revoked": "dim"}[state],
        # Eşleşmeyi BEKLEYEN kiosk ayrı bir rozet alır: "çevrimdışı" demek,
        # hiç kurulmamış bir cihaza arıza yakıştırmak olurdu.
        "awaiting_pairing": (not paired) and not revoked_at,
        "pairing": pairing_view(raw.get("pairing"), now=now),
    }


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Panelin üst şeridi. Sayılar `kiosk_row` çıktısından türer."""
    return {
        "total": len(rows),
        "online": sum(1 for row in rows if row["state"] == "online"),
        "offline": sum(1 for row in rows if row["state"] == "offline"),
        "revoked": sum(1 for row in rows if row["revoked"]),
        "awaiting": sum(1 for row in rows if row["awaiting_pairing"]),
        "usable_codes": sum(1 for row in rows if row["pairing"]["usable"]),
    }


def newly_paired(rows: list[dict[str, Any]], seen: dict[int, str]) -> list[dict[str, Any]]:
    """Bu okumada İLK KEZ eşlenmiş görünen kiosklar.

    `seen` bir önceki okumanın hatırasıdır: {kiosk_id: paired_at}. Eşlemeyi
    Kontrol Merkezi başlatmıyor — kodu cihaz giriyor — bu yüzden "yeni eşlendi"
    ancak karşılaştırarak anlaşılır.

    HİÇ GÖRÜLMEMİŞ ama ZATEN EŞLEŞMİŞ kiosk yeni sayılmaz: modül ilk kez
    çalıştığında (ya da yerel tablo boşaldığında) sahadaki bütün kiosklar
    "az önce eşlendi" diye ilan edilirdi. Yeni sayılmak için kaydın ÖNCEDEN
    görülmüş ve o zaman eşleşmemiş olması gerekir.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row["paired"] or row["revoked"]:
            continue
        before = seen.get(row["id"])
        if before is None or before:
            # `None` = hiç görülmemiş (ilk okuma), dolu = zaten eşliydi.
            continue
        out.append(row)
    return out
