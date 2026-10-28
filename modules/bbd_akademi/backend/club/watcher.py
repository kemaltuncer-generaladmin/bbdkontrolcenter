"""BBDSTORE Deneme Kulübü sipariş izleyicisi (iş 81-km-kulup; bbd-akademi 81-api-taslak §C).

KULLANICI (28.10.2026): "pos bbdstore üzerinden ödeme … bbdstore ile iletişimde kalırız. oradan
gereken tüm bilgiler için de tek kullanımlık form göndeririz link ile sms ile kullanıcı doldurunca
aboneliği otomatik başlar".

Her `interval` saniyede (vars. 120) bir tur:

1. Akademi ayarı `club.autoInviteFromStore` kapalıysa (ya da kurumda `exam_club` bayrağı kapalı)
   tur hiçbir şey yapmaz — durum `disabled`.
2. `store.api` yeteneğinden (Mağaza Geçidi; K3) ödenmiş kulüp siparişleri okunur:
   `bbd_trial_members({paid: 1, updatedSince: <imleç>, sort: updated}, all_pages=True)`
   (bbd-store `GET /api/admin/bbd/deneme-kulubu/registrations`, şekil `devir/raporlar/81-magaza-api.md`).
3. `paid && !cancelled && !refunded && !simulated` olan ve henüz işlenmemiş her sipariş için
   Akademi `admin_createClubInvite` {source store, storeOrderRef = sipariş no, amountMinor,
   paymentMethod store, paid true, paidAt, phone = fatura telefonu, name = fatura adı}
   (Idempotency-Key `store-<sipariş no>`; Akademi storeOrderRef'i ayrıca tekil tutar → çift davet yok).
   Fatura telefonu cep değilse davet AÇILMAZ (`invalid_phone`; ekranda uyarı, kurum elle kaydeder).
4. Daha önce davet açılmış sipariş iptal/iade görünürse davet iptal edilir
   (`admin_cancelClubInvite`, işlenmiş/tamamlanmış davet iptal edilemez → uyarı).
5. Hatalı sipariş `failed` kalır ve sonraki turda yeniden denenir; imleç en eski hatalı
   siparişin `updatedAt`'inden ileri gitmez. Hata → günlük + ekranda uyarı (`status()`).

Kişisel veri (ad, telefon) KM tablosuna ve log'a YAZILMAZ; yalnız sipariş no, tutar, durum.
Mağazanın KM'ye olay kanalı yoktur; `wake()` (ör. ileride `store.trial_club.order_paid` olayı ya
da ekrandaki "Şimdi tara") turu hemen koşturur.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from ..core.gateway import AkademiError, AkademiGateway, on_behalf_of, request_id

ACTOR = ("bbd_akademi", "BBDSTORE kulüp izleyicisi")
STORE = "store.api"
INVITES = "/v1/admin/club/invites"
CANCEL_REASON = "BBDSTORE siparişi iptal / iade edildi"
#: İlk tam tarama (imleç yokken) — mağaza `updatedSince` ile akış sırası verir.
EPOCH = "1970-01-01T00:00:00Z"
_PHONE = re.compile(r"^5\d{9}$")

STATE_TEXT = {
    "ok": "BBDSTORE siparişleri izleniyor.",
    "disabled": "BBDSTORE'dan otomatik davet kapalı (kulüp ayarı).",
    "feature_off": "Deneme Kulübü kurumda kapalı (Ayarlar → Özellikler).",
    "store_missing": "Mağaza Geçidi (store_api) bu kurulumda yok; BBDSTORE siparişleri okunamıyor.",
    "store_unavailable": "BBDSTORE'a ulaşılamadı; 2 dk sonra yeniden denenecek.",
    "akademi_unavailable": "Akademi'ye ulaşılamadı; 2 dk sonra yeniden denenecek.",
    "akademi_off": "Akademi bağlantısı kapalı ya da kurulmamış.",
    "partial": "Bazı siparişler için davet açılamadı — aşağıdaki listeye bakın.",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "paid")
    return bool(value)


def local_phone(value: Any) -> str | None:
    """`+905321234567` / `05321234567` / `5321234567` → `5321234567`; cep değilse None."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("90") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if _PHONE.match(digits) else None


def store_order(row: dict[str, Any]) -> dict[str, Any] | None:
    """Mağaza satırı → izleyicinin kullandığı alanlar (yeni camelCase, eski snake_case yedek)."""
    number = _field(row, "orderNumber", "increment_id", "order_number", "orderId", "id")
    if number is None:
        return None
    amount = _field(row, "amountMinor")
    if amount is None:
        legacy = _field(row, "membershipAmount", "grand_total", "paid_amount", "amount")
        try:
            amount = round(float(str(legacy).replace(",", ".")) * 100) if legacy is not None else 0
        except ValueError:
            amount = 0
    phone_raw = _field(row, "phone")
    if phone_raw is None and isinstance(row.get("billing"), dict):
        phone_raw = _field(row["billing"], "phone")
    phone = local_phone(phone_raw)
    if row.get("phoneValid") is False:
        phone = None
    name = _field(row, "customerName", "customer_name", "name", "full_name")
    return {
        "orderNumber": str(number)[:64],
        "paid": _truthy(row.get("paid")),
        "paidAt": _field(row, "paidAt"),
        "simulated": _truthy(_field(row, "simulated", "isSimulated")),
        "cancelled": _truthy(row.get("cancelled")) or str(row.get("status") or "") == "canceled",
        "refunded": _truthy(row.get("refunded")),
        "amountMinor": max(0, int(amount or 0)),
        "phone": phone,
        "name": " ".join(str(name or "").split())[:120] or None,
        "updatedAt": str(_field(row, "updatedAt", "updated_at") or ""),
    }


class StoreClubWatcher:
    """2 dk'lık mağaza izleyicisi + ekranın okuduğu durum."""

    def __init__(self, *, gateway: AkademiGateway, store: Any, log: Any,
                 store_api: Callable[[], Any], interval: float = 120.0) -> None:
        self._gateway = gateway
        self._store = store
        self._log = log
        self._store_api = store_api
        self.interval = interval
        self._orders = store.table("club_store_order")
        self._watch = store.table("club_store_watch")
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._loop: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ döngü

    def start(self) -> None:
        if self.interval <= 0:
            return
        if self._loop is None or self._loop.done():
            self._loop = asyncio.get_running_loop().create_task(
                self._run(), name="bbd-akademi-club-store-watch")

    async def stop(self) -> None:
        if self._loop is None:
            return
        self._loop.cancel()
        try:
            await self._loop
        except asyncio.CancelledError:
            pass
        self._loop = None

    def wake(self, *_: Any) -> None:
        """Turu hemen koştur (olay ya da "Şimdi tara")."""
        self._wake.set()

    async def _run(self) -> None:
        await asyncio.sleep(min(30.0, self.interval))
        while True:
            try:
                await self.run_once()
            except Exception as failure:  # noqa: BLE001 — döngü ölmez (K7)
                self._log.warning("kulüp izleyicisi turu patladı", error=str(failure))
            try:
                await asyncio.wait_for(self._wake.wait(), self.interval)
            except TimeoutError:
                pass
            self._wake.clear()

    # ------------------------------------------------------------ durum

    async def _state_row(self) -> dict[str, Any]:
        row = await self._store.fetch_one(f"SELECT * FROM {self._watch} WHERE id = 1")
        return dict(row) if row else {"cursor": "", "state": "", "message": "", "last_run_at": "",
                                      "last_ok_at": "", "invited_total": 0}

    async def _save_state(self, *, state: str, message: str = "", cursor: str | None = None,
                          ok: bool = False, invited: int = 0) -> None:
        current = await self._state_row()
        stamp = _now()
        values = (
            cursor if cursor is not None else str(current.get("cursor") or ""),
            state, message or STATE_TEXT.get(state, ""), stamp,
            stamp if ok else str(current.get("last_ok_at") or ""),
            int(current.get("invited_total") or 0) + invited,
        )
        if await self._store.fetch_one(f"SELECT id FROM {self._watch} WHERE id = 1"):
            await self._store.execute(
                f"UPDATE {self._watch} SET cursor = ?, state = ?, message = ?, last_run_at = ?, "
                f"last_ok_at = ?, invited_total = ? WHERE id = 1", values)
        else:
            await self._store.execute(
                f"INSERT INTO {self._watch} (id, cursor, state, message, last_run_at, last_ok_at, "
                f"invited_total) VALUES (1, ?, ?, ?, ?, ?, ?)", values)

    async def status(self) -> dict[str, Any]:
        """Ekranın uyarı şeridi: durum + sorunlu siparişler (kişi verisi yok)."""
        row = await self._state_row()
        counts = {str(item["status"]): int(item["n"]) for item in await self._store.fetch_all(
            f"SELECT status, COUNT(*) AS n FROM {self._orders} GROUP BY status")}
        problems = await self._store.fetch_all(
            f"SELECT order_number, status, error, amount_minor, updated_at FROM {self._orders} "
            f"WHERE status IN ('failed', 'invalid_phone', 'cancel_failed') "
            f"ORDER BY updated_at DESC LIMIT 20")
        return {
            "state": str(row.get("state") or "") or "pending",
            "message": str(row.get("message") or "") or "İlk tarama bekleniyor.",
            "lastRunAt": row.get("last_run_at") or None,
            "lastOkAt": row.get("last_ok_at") or None,
            "intervalSeconds": int(self.interval),
            "storeConnected": self._store_api() is not None,
            "counts": {"invited": counts.get("invited", 0), "failed": counts.get("failed", 0),
                       "invalidPhone": counts.get("invalid_phone", 0),
                       "cancelled": counts.get("cancelled", 0),
                       "cancelFailed": counts.get("cancel_failed", 0)},
            "problems": [{"orderNumber": item["order_number"], "status": item["status"],
                          "error": item["error"] or None,
                          "amountMinor": int(item["amount_minor"] or 0),
                          "at": item["updated_at"]} for item in problems],
        }

    # ------------------------------------------------------------ Akademi

    async def _akademi(self, method: str, path: str, *, body: Any = None,
                       key: str | None = None) -> tuple[int, Any]:
        headers = {"accept": "application/json",
                   "X-Akademi-On-Behalf-Of": on_behalf_of(*ACTOR, kind="system"),
                   "X-Request-Id": request_id(None)}
        if key:
            headers["Idempotency-Key"] = key
        response = await self._gateway.send(method, path, headers=headers, json_body=body)
        try:
            raw = await response.aread()
        finally:
            await response.aclose()
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        return response.status_code, payload

    @staticmethod
    def _error(payload: Any, status: int) -> tuple[str, str]:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return str(error.get("code") or ""), str(error.get("message") or "")[:300]
        return "", f"Akademi {status} döndü."

    async def _auto_enabled(self) -> str | None:
        """None → çalış; değilse durum kodu."""
        code, payload = await self._akademi("GET", "/v1/admin/settings")
        if code == 403 and self._error(payload, code)[0] == "feature_disabled":
            return "feature_off"
        if code != 200 or not isinstance(payload, dict):
            return "akademi_unavailable"
        club = payload.get("club") if isinstance(payload.get("club"), dict) else {}
        flat = payload.get("club.autoInviteFromStore")
        value = club.get("autoInviteFromStore", flat)
        return "disabled" if value is False else None

    # ------------------------------------------------------------ tur

    async def run_once(self) -> dict[str, Any]:
        """Bir tur. Aynı anda ikinci tur beklemez, atlanır."""
        if self._lock.locked():
            return {"skipped": True}
        async with self._lock:
            return await self._turn()

    async def _turn(self) -> dict[str, Any]:
        client = self._store_api()
        if client is None or not callable(getattr(client, "bbd_trial_members", None)):
            await self._save_state(state="store_missing")
            return {"state": "store_missing"}
        try:
            blocked = await self._auto_enabled()
        except AkademiError as failure:
            state = "akademi_off" if getattr(failure, "code", "") in (
                "akademi_disabled", "akademi_not_configured") else "akademi_unavailable"
            await self._save_state(state=state)
            return {"state": state}
        if blocked:
            await self._save_state(state=blocked)
            return {"state": blocked}

        current = await self._state_row()
        since = str(current.get("cursor") or "") or EPOCH
        try:
            page = await client.bbd_trial_members(
                {"paid": 1, "updatedSince": since, "sort": "updated"}, per_page=100,
                all_pages=True)
        except Exception as failure:  # noqa: BLE001 — mağaza dışarısı
            self._log.warning("kulüp izleyicisi: mağaza okunamadı", error=str(failure)[:200])
            await self._save_state(state="store_unavailable")
            return {"state": "store_unavailable"}

        rows = page.get("items") if isinstance(page, dict) else page
        orders = [order for order in (store_order(row) for row in rows or []
                                      if isinstance(row, dict)) if order]
        known = {str(row["order_number"]): dict(row) for row in await self._store.fetch_all(
            f"SELECT order_number, status, invite_id, attempts FROM {self._orders}")}
        invited = 0
        failed_at: list[str] = []
        newest = since
        for order in orders:
            if order["updatedAt"] and order["updatedAt"] > newest:
                newest = order["updatedAt"]
            try:
                outcome = await self._handle(order, known.get(order["orderNumber"]))
            except AkademiError as failure:
                outcome = "failed"
                await self._record(order, "failed", error=str(failure)[:300],
                                   previous=known.get(order["orderNumber"]))
            if outcome == "invited":
                invited += 1
            elif outcome == "failed" and order["updatedAt"]:
                failed_at.append(order["updatedAt"])
        cursor = min(failed_at) if failed_at else newest
        state = "partial" if failed_at else "ok"
        await self._save_state(state=state, cursor=cursor, ok=not failed_at, invited=invited)
        if failed_at:
            self._log.warning("kulüp izleyicisi: davet açılamayan sipariş var",
                              count=len(failed_at))
        return {"state": state, "seen": len(orders), "invited": invited,
                "failed": len(failed_at)}

    async def _record(self, order: dict[str, Any], status: str, *, invite_id: str = "",
                      error: str = "", previous: dict[str, Any] | None = None) -> None:
        stamp = _now()
        attempts = int((previous or {}).get("attempts") or 0) + (1 if status == "failed" else 0)
        invite = invite_id or str((previous or {}).get("invite_id") or "")
        if previous is None:
            await self._store.execute(
                f"INSERT INTO {self._orders} (order_number, status, invite_id, amount_minor, "
                f"error, attempts, order_updated_at, created_at, updated_at) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order["orderNumber"], status, invite, order["amountMinor"], error, attempts,
                 order["updatedAt"], stamp, stamp))
        else:
            await self._store.execute(
                f"UPDATE {self._orders} SET status = ?, invite_id = ?, amount_minor = ?, "
                f"error = ?, attempts = ?, order_updated_at = ?, updated_at = ? "
                f"WHERE order_number = ?",
                (status, invite, order["amountMinor"], error, attempts, order["updatedAt"],
                 stamp, order["orderNumber"]))

    async def _handle(self, order: dict[str, Any], previous: dict[str, Any] | None) -> str:
        status = str((previous or {}).get("status") or "")
        gone = order["cancelled"] or order["refunded"]
        if gone:
            if status in ("invited", "cancel_failed") and (previous or {}).get("invite_id"):
                return await self._cancel(order, previous or {})
            if status in ("", "failed", "invalid_phone"):
                await self._record(order, "skipped", previous=previous)
                return "skipped"
            return status
        if not order["paid"] or order["simulated"]:
            return "ignored"
        if status in ("invited", "cancelled", "skipped", "cancel_failed"):
            return status
        if order["phone"] is None:
            if status != "invalid_phone":
                await self._record(order, "invalid_phone", previous=previous,
                                   error="Fatura telefonu cep numarası değil — kaydı Deneme "
                                         "Kulübü ekranından elle açın.")
            return "invalid_phone"
        body: dict[str, Any] = {
            "phone": order["phone"], "name": order["name"], "source": "store",
            "storeOrderRef": order["orderNumber"], "amountMinor": order["amountMinor"],
            "paymentMethod": "store", "paid": True,
        }
        if order["paidAt"]:
            body["paidAt"] = order["paidAt"]
        code, payload = await self._akademi("POST", INVITES, body=body,
                                            key=f"store-{order['orderNumber']}")
        if code in (200, 201) and isinstance(payload, dict) and payload.get("id"):
            await self._record(order, "invited", invite_id=str(payload["id"]), previous=previous)
            self._log.info("kulüp daveti açıldı (BBDSTORE)", order=order["orderNumber"],
                           replay=code == 200)
            return "invited"
        error_code, message = self._error(payload, code)
        await self._record(order, "failed", previous=previous,
                           error=message or f"Akademi {code} döndü ({error_code}).")
        self._log.warning("kulüp daveti açılamadı", order=order["orderNumber"], status=code,
                          code=error_code)
        return "failed"

    async def _cancel(self, order: dict[str, Any], previous: dict[str, Any]) -> str:
        invite = str(previous.get("invite_id") or "")
        code, payload = await self._akademi(
            "POST", f"{INVITES}/{quote(invite, safe='')}:cancel", body={"reason": CANCEL_REASON},
            key=f"store-cancel-{order['orderNumber']}")
        if code in (200, 201, 204):
            await self._record(order, "cancelled", previous=previous)
            self._log.info("kulüp daveti iptal edildi (sipariş iptal/iade)",
                           order=order["orderNumber"])
            return "cancelled"
        error_code, message = self._error(payload, code)
        if error_code == "club_invite_not_cancellable":
            message = ("Sipariş iptal/iade edildi ama kayıt işlendiği için davet iptal edilemedi "
                       "— öğrenci ve sözleşmeyi elle kontrol edin.")
        elif code >= 500 or code == 429:
            # Geçici: sonraki tur yeniden dener (durum `invited` kalır).
            return "failed"
        await self._record(order, "cancel_failed", previous=previous,
                           error=message or f"Akademi {code} döndü.")
        return "cancel_failed"
