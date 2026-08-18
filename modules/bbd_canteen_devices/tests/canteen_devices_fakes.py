"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeCanteen` `canteen.api` yeteneğinin testlik yüzüdür ve KANTİNİN KENDİ
DAVRANIŞINI TAKLİT EDER, boş bir kabuk değildir: kodu hash'leyerek saklar,
yeni kod eskisini geçersiz kılar, kod ATOMİK olarak bir kez yakılır ve iptal
edilen kiosk ne kod alır ne eşlenir. Sebep, bu davranışların Kontrol
Merkezi'nin ÜZERİNE KURULDUĞU sözleşme olması: `pairing_code` ucunun "iptal
edilmişe kod üretme" kuralı, kantinin de aynı kapıyı kurduğu varsayımıyla
yazıldı. Taklit gevşek olsaydı, testler kuralın YALNIZ bir tarafını sınardı.

Kantindeki gerçek uygulamanın kendi testi ayrıdır ve orada durur:
`bbdkantin/backend/tests/Feature/KioskPairingTest.php`.

METOT ADLARI VE İMZALARI `modules/bbd_canteen_api/backend/client.py` İLE
BİREBİR AYNI OLMALIDIR. Uydurma bir ad buradaki testleri yeşil tutar ama
canlıda `AttributeError` verir — ve servis istisnayı K7 gereği yuttuğu için
hata ekranda "kantine ulaşılamadı" diye görünür: yanlış metot adı, düşmüş bir
sunucudan AYIRT EDİLEMEZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı dört ifadeyi (iz satırı, iz
okuma, hatıra okuma, hatıra yazma) tanıyacak kadarını yapar. Amaç çekirdek
depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını görmek —
özellikle `result="denendi"` izinin geçit çağrısından ÖNCE düşmesini.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, message: str, **fields: Any) -> None:
        self.records.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._add("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._add("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._add("error", message, **fields)


class FakeBus:
    """Olay yolu. Yayınlanan olayları sırasıyla tutar."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    async def __call__(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self.fail:
            raise RuntimeError("dinleyici patladı")
        self.events.append((event, payload or {}))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "bbd_canteen_devices") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.seen: dict[int, dict[str, Any]] = {}
        #: `True` ise her yazma patlar — "iz yazılamazsa iş durmasın" (K7).
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("kiosk_id", "action", "reason", "actor", "result", "detail",
                    "created_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.audit) + 1
            self.audit.append(row)
        elif "_seen" in text and text.startswith("INSERT"):
            kiosk_id, paired_at, revoked_at, updated_at = params
            self.seen[int(kiosk_id)] = {"kiosk_id": int(kiosk_id),
                                        "paired_at": paired_at,
                                        "revoked_at": revoked_at,
                                        "updated_at": updated_at}

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_seen" in sql:
            return [dict(row) for row in self.seen.values()]
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE kiosk_id" in " ".join(sql.split()):
                rows = [row for row in rows if int(row["kiosk_id"]) == int(params[0])]
            return rows
        return []

    # ------------------------------------------------------------- kolaylık

    def actions(self, action: str) -> list[dict[str, Any]]:
        return [row for row in self.audit if row["action"] == action]

    def results(self, action: str) -> list[str]:
        return [row["result"] for row in self.audit if row["action"] == action]

    def details(self, action: str) -> list[dict[str, Any]]:
        return [json.loads(row["detail"] or "{}")
                for row in self.audit if row["action"] == action]


class CanteenDenied(RuntimeError):
    """Kantinin 4xx yanıtının karşılığı. Geçit de böyle bir istisna fırlatır."""

    def __init__(self, message: str, *, status: int = 422, reason: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class FakeCanteen:
    """`canteen.api` yeteneğinin testlik yüzü. Yalnız KIOSK metotları var.

    `calls` her çağrıyı sırasıyla tutar; `fail` kümesine bir metot adı atılırsa
    o metot patlar ve K7 (geçit düşerse ekran ayakta kalır) sınanır.
    """

    #: Kantinin varsayılanıyla aynı.
    TTL_MINUTES = 10

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        self._next_id = 1
        self._code_seq = 0
        #: Eşlenen kiosklara verilen token'lar — "tek kod tek token" iddiası
        #: ancak bu listenin uzunluğuyla kanıtlanır.
        self.tokens: list[str] = []

    # -------------------------------------------------------------- içeride

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _wire(self, row: dict[str, Any]) -> dict[str, Any]:
        """Kantinin tel üzerindeki gövdesi. KOD BURADA YOKTUR — kantin de
        listede kodu döndürmüyor, yalnız sha256'sını saklıyor."""
        now = datetime.now(UTC)
        expires = row.get("code_expires_at")
        usable = (row.get("code_hash") is not None and row.get("code_used_at") is None
                  and row.get("revoked_at") is None
                  and expires is not None and expires > now)
        return {
            "id": row["id"], "name": row["name"],
            "platform": row.get("platform"), "appVersion": row.get("app_version"),
            "paired": row.get("paired_at") is not None,
            "pairedAt": _stamp(row["paired_at"]) if row.get("paired_at") else None,
            "lastSeenAt": _stamp(row["last_seen_at"]) if row.get("last_seen_at") else None,
            "revokedAt": _stamp(row["revoked_at"]) if row.get("revoked_at") else None,
            "revokedReason": row.get("revoked_reason"),
            "createdAt": _stamp(row["created_at"]),
            "pairing": {
                "usable": usable,
                "expiresAt": _stamp(expires) if expires else None,
                "usedAt": _stamp(row["code_used_at"]) if row.get("code_used_at") else None,
            },
        }

    def _issue(self, row: dict[str, Any], ttl_minutes: int) -> dict[str, Any]:
        """Yeni kod üretir ve ESKİSİNİ GEÇERSİZ KILAR (kantindeki `issue`)."""
        # Kod testin izleyebilmesi için SAYAÇTAN üretiliyor; kantinde
        # `random_int` var. Burada sınanan şey rastgelelik değil, TEK KULLANIM.
        self._code_seq += 1
        code = f"{self._code_seq:08d}"
        expires = datetime.now(UTC) + timedelta(minutes=max(1, ttl_minutes))
        row["code_hash"] = self._digest(code)
        row["code_expires_at"] = expires
        row["code_used_at"] = None
        return {"code": code, "expiresAt": _stamp(expires)}

    def _record(self, name: str, /, *args: Any, **kwargs: Any) -> None:
        if name in self.fail:
            raise CanteenDenied(f"{name} patladı", status=500)
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def writes(self) -> list[str]:
        yazan = ("create_kiosk", "rename_kiosk", "new_kiosk_pairing_code", "revoke_kiosk")
        return [name for name, _, _ in self.calls if name in yazan]

    # ------------------------------------------------------- yetenek yüzeyi

    async def kiosks(self) -> list[dict[str, Any]]:
        self._record("kiosks")
        return [self._wire(row) for row in self.rows.values()]

    async def create_kiosk(self, *, name: str) -> dict[str, Any]:
        self._record("create_kiosk", name=name)
        row: dict[str, Any] = {
            "id": self._next_id, "name": name, "created_at": datetime.now(UTC),
            "code_hash": None, "code_expires_at": None, "code_used_at": None,
            "paired_at": None, "last_seen_at": None, "revoked_at": None,
            "revoked_reason": None, "platform": None, "app_version": None,
        }
        self.rows[self._next_id] = row
        self._next_id += 1
        pairing = self._issue(row, self.TTL_MINUTES)
        return {"data": self._wire(row), "pairing": pairing}

    async def rename_kiosk(self, kiosk_id: int, *, name: str) -> dict[str, Any]:
        self._record("rename_kiosk", kiosk_id, name=name)
        row = self.rows[int(kiosk_id)]
        row["name"] = name
        return {"data": self._wire(row)}

    async def new_kiosk_pairing_code(self, kiosk_id: int, *,
                                     ttl_minutes: int | None = None) -> dict[str, Any]:
        self._record("new_kiosk_pairing_code", kiosk_id, ttl_minutes=ttl_minutes)
        row = self.rows[int(kiosk_id)]
        if row["revoked_at"] is not None:
            # Kantinin kapısı: iptal edilmiş kioska kod üretilmez.
            raise CanteenDenied("Bu kiosk iptal edilmiş; yeniden eşleştirilemez.",
                                reason="kiosk_revoked")
        pairing = self._issue(row, ttl_minutes or self.TTL_MINUTES)
        return {"data": self._wire(row), "pairing": pairing}

    async def revoke_kiosk(self, kiosk_id: int, *, reason: str) -> dict[str, Any]:
        self._record("revoke_kiosk", kiosk_id, reason=reason)
        row = self.rows[int(kiosk_id)]
        row["revoked_at"] = datetime.now(UTC)
        row["revoked_reason"] = reason
        # Token ÖLÜR, bekleyen kod da ölür.
        row["code_hash"] = None
        row["code_expires_at"] = None
        return {"data": self._wire(row)}

    # --------------------------------------------- cihazın kendi eşlemesi

    def pair(self, code: str, *, device_name: str = "Kiosk",
             platform: str = "android", app_version: str = "1.0.0") -> str:
        """`POST /api/kiosks/pair` — kantindeki ATOMİK yakmanın aynası.

        Koşul, kaydın okunmasında değil YAZMASINDA durur: aynı kodu taşıyan
        ikinci istek `code_used_at` dolu bulur ve reddedilir. Gerçek uygulamada
        bu tek bir `UPDATE ... WHERE code_used_at IS NULL` ifadesidir ve
        veritabanı onu bölünmez uygular.
        """
        digest = self._digest(code)
        now = datetime.now(UTC)
        for row in self.rows.values():
            if row.get("code_hash") != digest:
                continue
            if (row["code_used_at"] is not None or row["revoked_at"] is not None
                    or row["code_expires_at"] is None or row["code_expires_at"] <= now):
                break
            row["code_used_at"] = now
            row["paired_at"] = now
            row["last_seen_at"] = now
            row["name"] = device_name
            row["platform"] = platform
            row["app_version"] = app_version
            token = f"token-{row['id']}-{len(self.tokens) + 1}"
            self.tokens.append(token)
            return token
        # SEBEP AYIRT EDİLMEZ: yok, süresi geçmiş, kullanılmış ve iptal edilmiş
        # kod aynı cümleyi verir.
        raise CanteenDenied("Eşleme kodu geçersiz.", reason="pairing_denied")
