"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ, SMS GÖNDERMEZ.

`FakeCanteen` `canteen.api` yeteneğinin testlik yüzüdür. `.sent` her gönderim
çağrısını sırasıyla tutar: "kuru provada hiçbir şey gitmedi" iddiası ancak bu
liste okunarak kanıtlanabilir — sayıya bakmak yetmez, LİSTENİN BOŞ olması
gerekir.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı beş ifadeyi (öbek satırı, mesaj
satırı, öbek özetinin güncellenmesi, hazır mesaj yazma/silme) tanıyacak kadarını
yapar. Amaç çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU
SATIRI yazdığını görmek — özellikle ATLANAN alıcıların da kayda geçmesini:
"göndermedik" ile "gönderemedik" arasındaki fark yalnız o satırlarda duruyor.
"""

from __future__ import annotations

from typing import Any

#: Kantinden gelen öğrenci satırı. `parentPhone` DOLU — mutlu yol.
STUDENT: dict[str, Any] = {
    "id": "101",
    "displayName": "Ali Demir",
    "parentPhone": "5321234567",
    "balance": -4550,
    "isBlocked": False,
}

#: VELİSİ OLMAYAN öğrenci. Kantinde öğrenci telefonu diye bir alan yok; velisi
#: tanımlı değilse SMS gidemez ve bu satır o dalı sınar.
STUDENT_NO_PHONE: dict[str, Any] = {
    "id": "102",
    "displayName": "Zeynep Kaya",
    "parentPhone": "",
    "balance": 0,
    "isBlocked": False,
}


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


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar."""

    def __init__(self, module_id: str = "bbd_sms") -> None:
        self.module_id = module_id
        self.batches: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []
        self.presets: list[dict[str, Any]] = []
        self._preset_id = 0

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())

        if "_batch" in text and text.startswith("INSERT"):
            keys = ("batch_ref", "title", "body", "encoding", "segments", "include_debt",
                    "include_daily", "total_count", "created_at", "created_by")
            row = dict(zip(keys, params, strict=False))
            row.update({"sent_count": 0, "fail_count": 0, "credits": 0})
            self.batches[str(row["batch_ref"])] = row
            return

        if "_batch" in text and text.startswith("UPDATE"):
            sent, failed, credits, ref = params
            self.batches[str(ref)].update(
                {"sent_count": sent, "fail_count": failed, "credits": credits})
            return

        if "_message" in text and text.startswith("INSERT"):
            # ATLANAN ALICILAR AYRI BİR İFADEYLE yazılıyor: sütun sırası aynı
            # değil ve iki dalı tek şablonla okumak, atlanan satırların
            # sessizce yanlış sütuna düşmesi demekti.
            if "'skipped'" in text:
                keys = ("batch_ref", "kantin_id", "student_name", "phone", "reason",
                        "created_at")
                row = dict(zip(keys, params, strict=False))
                row.update({"text": "", "segments": 0, "status": "skipped"})
            else:
                keys = ("batch_ref", "kantin_id", "student_name", "phone", "text",
                        "segments", "status", "reason", "created_at")
                row = dict(zip(keys, params, strict=False))
            self.messages.append(row)
            return

        if "_preset" in text and text.startswith("INSERT"):
            name, body, created_at, actor = params
            for row in self.presets:
                if row["name"] == name:
                    row.update({"body": body, "created_at": created_at, "created_by": actor})
                    return
            self._preset_id += 1
            self.presets.append({"id": self._preset_id, "name": name, "body": body,
                                 "created_at": created_at, "created_by": actor})
            return

        if "_preset" in text and text.startswith("DELETE"):
            self.presets = [row for row in self.presets if row["id"] != int(params[0])]
            return

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        text = " ".join(sql.split())
        if "_preset" in text:
            return [dict(row) for row in sorted(self.presets, key=lambda r: str(r["name"]))]
        if "_message" in text:
            ref = str(params[0])
            return [dict(row) for row in self.messages if str(row["batch_ref"]) == ref]
        if "_batch" in text:
            return [dict(row) for row in self.batches.values()]
        return []

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_batch" in sql:
            row = self.batches.get(str(params[0]))
            return dict(row) if row else None
        return None

    # ------------------------------------------------------------- kolaylık

    def statuses(self) -> list[str]:
        return [str(row["status"]) for row in self.messages]


class FakeCanteen:
    """`canteen.api` yeteneğinin testlik yüzü.

    METOT ADLARI VE İMZALARI gerçek geçitle aynı olmalıdır: uydurma bir ad
    testleri yeşil tutar ama canlıda `AttributeError` verir ve servis istisnayı
    yuttuğu için ekranda "kantine ulaşılamadı" diye görünür — yani yanlış metot
    adı, düşmüş bir sunucudan AYIRT EDİLEMEZ.
    """

    def __init__(self, students: list[dict[str, Any]] | None = None) -> None:
        self.student_rows = students if students is not None else [dict(STUDENT)]
        #: Gönderim çağrıları. KURU PROVADA BOŞ KALMALI.
        self.sent: list[dict[str, Any]] = []
        self.settings_payload: dict[str, Any] = {
            "netgsmUsercode": "8503030303",
            "netgsmHeader": "BBDKANTIN",
            "netgsmPasswordConfigured": True,
            "smsPaymentConfirmedEnabled": False,
        }
        self.updated: list[dict[str, Any]] = []
        #: Bu ada sahip metot patlar — "kantin düşerse ekran ayakta kalır" (K7).
        self.fail: set[str] = set()
        #: `send_sms` bu kadar çağrıdan sonra `sent: False` döndürür.
        self.reject_after: int | None = None

    async def students(self) -> list[dict[str, Any]]:
        if "students" in self.fail:
            raise RuntimeError("kantine ulaşılamadı")
        return [dict(row) for row in self.student_rows]

    async def settings(self) -> dict[str, Any]:
        if "settings" in self.fail:
            raise RuntimeError("ayar okunamadı")
        return dict(self.settings_payload)

    async def notifications(self, *, state: str | None = None,
                            limit: int = 200) -> dict[str, Any]:
        if "notifications" in self.fail:
            raise RuntimeError("kuyruk okunamadı")
        return {"data": [], "summary": {"pending": 0, "failed": 0}}

    async def send_sms(self, *, student_id: str, include_debt: bool = False,
                       include_daily: bool = False,
                       message: str | None = None) -> dict[str, Any]:
        if "send_sms" in self.fail:
            raise RuntimeError("sağlayıcı patladı")
        self.sent.append({"student_id": student_id, "include_debt": include_debt,
                          "include_daily": include_daily, "message": message})
        if self.reject_after is not None and len(self.sent) > self.reject_after:
            return {"sent": False, "reason": "Netgsm reddetti"}
        return {"sent": True}

    async def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "update_settings" in self.fail:
            raise RuntimeError("ayar yazılamadı")
        self.updated.append(dict(payload))
        self.settings_payload.update(payload)
        return dict(self.settings_payload)
