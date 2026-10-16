"""`akademi.student_finance` yeteneği — "bu öğrencinin kurum cari borcu var mı"
(ve ön kontrol ekranı için tutarlar: `institution_balance`).

Tüketen: `bbd_students` kalıcı silme ön koşulu (km-merkezi-senkron §5.3).
Öğrenci yalnız `inactive` iken, kantin bakiyesi 0 ve açık ödeme talebi yokken
silinir; üçüncü koşul Akademi'de kurum cari borcunun olmamasıdır. Kurum parası
kantinden ayrıdır (A12) — burada yalnız ön koşul ortaktır, tutar toplanmaz.

Akış (iki Akademi okuması, silme seyrek bir işlemdir):

1. KM kimliği (`STU-…`) → Akademi öğrenci kimliği: `admin_listStudents`
   `externalRef=<STU-…>` önce `status=inactive` ile (silinecek öğrenci KM'de
   pasiftir), bulunamazsa süzgeçsiz (Akademi pasifleştirme olayını henüz
   uygulamamış olabilir). Akademi'de hiç yoksa kurum carisi de yoktur → borç yok.
2. `students_getFinanceSummary` (`/v1/students/{id}/finance`) →
   `openBalanceMinor > 0` borçtur. `404 student_not_found` → borç yok.
   Sözleşmede bu işlem `canteen` özellik bayrağına bağlı görünüyor (açıklama
   `finance` diyor); `403 feature_disabled` gelirse aynı özet
   `admin_getFinanceAccount`'tan (`finance` bayrağı) okunur, o da kapalıysa
   kurumda cari modülü yoktur → borç yok.

GÜVENLİ TARAFTA KAL. Akademi'ye ulaşılamaz, anahtar yok, beklenmeyen yanıt
gelirse istisna fırlar; tüketen bunu `503 akademi_unavailable` sayar ve silme
KAPALI kalır. "Bilmiyorum" hiçbir zaman "borç yok" demek değildir.

Kişisel veri yok: istekte yalnız `STU-` kimliği gider, yanıttan yalnız tutar
okunur; log satırında öğrenci adı yazılmaz.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

from ..core.gateway import AkademiError, AkademiGateway, on_behalf_of, request_id

ACTOR = ("bbd_students", "Kalıcı silme ön koşulu")


class FinanceCheckFailed(AkademiError):
    """Akademi yanıt verdi ama borç durumu güvenle okunamadı."""

    status = 503
    code = "akademi_unavailable"


class StudentFinance:
    """`akademi.student_finance` — `bbd_students` bu nesneyi çağırır."""

    def __init__(self, *, gateway: AkademiGateway, log: Any) -> None:
        self._gateway = gateway
        self._log = log

    async def has_institution_debt(self, student_id: str) -> bool:
        ref = str(student_id or "").strip()
        if not ref:
            raise FinanceCheckFailed("Öğrenci kimliği boş.")
        akademi_id = await self._akademi_id(ref)
        if akademi_id is None:
            return False
        summary = await self._summary(akademi_id)
        if summary is None:
            return False
        try:
            open_minor = int(summary.get("openBalanceMinor") or 0)
        except (TypeError, ValueError) as failure:
            raise FinanceCheckFailed("Akademi kurum cari özeti okunamadı.") from failure
        return open_minor > 0

    async def institution_balance(self, student_id: str) -> dict[str, int]:
        """Silme ön kontrolünün ekranda tutarı yazabilmesi için: `{debtMinor, creditMinor}`.

        Borç (`openBalanceMinor > 0`) silmeyi engeller; ALACAK (`creditMinor`,
        öğrenci lehine) engellemez — kullanıcı kararı, ekranda bilgi notudur.
        Akademi'de öğrenci / cari yoksa ikisi de 0. Okunamazsa istisna (güvenli taraf).
        """
        ref = str(student_id or "").strip()
        if not ref:
            raise FinanceCheckFailed("Öğrenci kimliği boş.")
        akademi_id = await self._akademi_id(ref)
        summary = await self._summary(akademi_id) if akademi_id else None
        if not summary:
            return {"debtMinor": 0, "creditMinor": 0}
        try:
            open_minor = int(summary.get("openBalanceMinor") or 0)
            credit_minor = int(summary.get("creditMinor") or 0)
        except (TypeError, ValueError) as failure:
            raise FinanceCheckFailed("Akademi kurum cari özeti okunamadı.") from failure
        return {"debtMinor": max(open_minor, 0), "creditMinor": max(credit_minor, 0)}

    # ------------------------------------------------------------ adımlar

    async def _akademi_id(self, ref: str) -> str | None:
        for status in ("inactive", None):
            query: dict[str, Any] = {"externalRef": ref, "limit": 5}
            if status:
                query["status"] = status
            code, payload = await self._get("/v1/admin/students", urlencode(query))
            if code != 200 or not isinstance(payload, dict):
                raise FinanceCheckFailed(f"Akademi öğrenci araması {code} döndü.")
            for item in payload.get("items") or []:
                if isinstance(item, dict) and str(item.get("externalRef") or "") == ref \
                        and item.get("id"):
                    return str(item["id"])
        return None

    async def _summary(self, akademi_id: str) -> dict[str, Any] | None:
        path = f"/v1/students/{quote(akademi_id, safe='')}/finance"
        code, payload = await self._get(path)
        if code == 200 and isinstance(payload, dict):
            return payload
        if code == 404:
            return None
        if code == 403 and _error_code(payload) == "feature_disabled":
            account_path = f"/v1/admin/finance/accounts/{quote(akademi_id, safe='')}"
            code, payload = await self._get(account_path)
            if code == 200 and isinstance(payload, dict) \
                    and isinstance(payload.get("summary"), dict):
                return payload["summary"]
            if code == 404 or (code == 403 and _error_code(payload) == "feature_disabled"):
                return None
        raise FinanceCheckFailed(f"Akademi kurum cari özeti {code} döndü "
                                 f"({_error_code(payload) or 'bilinmeyen hata'}).")

    async def _get(self, path: str, query: str = "") -> tuple[int, Any]:
        headers = {"accept": "application/json",
                   "X-Akademi-On-Behalf-Of": on_behalf_of(*ACTOR, kind="system"),
                   "X-Request-Id": request_id(None)}
        response = await self._gateway.send("GET", path, query=query, headers=headers)
        try:
            raw = await response.aread()
        finally:
            await response.aclose()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        if response.status_code >= 500 or response.status_code in (401, 429):
            self._log.warning("akademi kurum cari okunamadı", path=path.split("/")[2],
                              status=response.status_code)
        return response.status_code, payload


def _error_code(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return str(payload["error"].get("code") or "")
    return ""
