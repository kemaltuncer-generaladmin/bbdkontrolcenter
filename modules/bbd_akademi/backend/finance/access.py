"""Kurum finansı kilidi — SMS onay kodlu giriş, KM OTURUMU BAŞINA 30 dk kayan izin (iş 83, Akademi README §14 madde 143).

KULLANICI (31.10.2026): "iki adımlı doğrulama olsun finansa tıklama kısmında … kim finansa girmek istiyorsa kendisi
de dahil sms gitsin o sms kodu girelim ardından finansal eylemlerde bulunabilsin. kantini kastetmiyorum kurum finansı
kısmı için konuşuyorum." Kararlar `devir/ajan-talimatlari/83-ortak.md`:

* Kod hep FİNANS YÖNETİCİSİ olarak işaretli personelin telefonuna gider (onay mantığı); isteyen kişi kodu ondan alır.
  SMS'i Akademi gönderir: KM `POST /v1/admin/finance-access/km/request {actorName, actorRef}` ile kodu ister,
  `.../km/verify {challengeId, code, actorRef}` ile doğrular (kod 6 hane, 5 dk, 5 deneme, hız sınırları Akademi'de).
* İzin KM SUNUCUSUNDA tutulur: anahtar (KM kullanıcısı, KM oturumu). Kod isteyen oturuma bağlıdır — başka oturum ya da
  başka kullanıcı aynı `challengeId` ile doğrulayamaz. Doğrulanınca 30 dk; her kurum-finansı isteği süreyi yeniden
  30 dk'ya uzatır (HAREKETSİZLİKTE kapanır). "Finansı kilitle" hemen kapatır. KM yeniden başlarsa izinler düşer
  (bellekte; güvenli taraf).
* Kilit KAPSAMI (`locked`): vekilin kurum-finansı işlemleri — `/v1/admin/finance/**` (cari, hesaplar, tahsilat,
  iade, ödeme planı/kalem/şablon, finans raporları, dışa aktarım, dönem kapanışı) ve öğrenci kartındaki kurum
  carisi okumaları (`/v1/students/{id}/finance…`). KANTİN, kitap satışı, sözleşmeler, Deneme Kulübü KİLİTLENMEZ.
  KM'nin arka plan işleri (personel eşitlemesi, kulüp izleyicisi, `akademi.student_finance` yeteneği) vekilden
  geçmez; etkilenmez.
* Yönetici özeti (`admin_getDashboard`) kilitliyken `institutionFinance` alanı `null` döner (`mask`).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from km_sdk import CurrentUser

from ..core.gateway import AkademiError, AkademiGateway, on_behalf_of, request_id
from ..core.operations import Operation

#: Akademi'nin KM uçları (`admin_requestKmFinanceAccess` / `admin_verifyKmFinanceAccess`; yalnız entegrasyon anahtarı,
#: izin `finance.manage`). Taslaktaki `/v1/integration/...` yerine bunlar (83-finans-hazir sapma 1).
REQUEST_PATH = "/v1/admin/finance-access/km/request"
VERIFY_PATH = "/v1/admin/finance-access/km/verify"
IDLE_MINUTES = 30
#: Doğrulanmamış kod isteği en çok bu kadar tutulur (Akademi'de kod 5 dk geçerlidir).
CHALLENGE_SECONDS = 10 * 60
MAX_CHALLENGES = 2000

LOCKED_MESSAGE = "Kurum finansı için onay kodu gerekli."
#: Yönetici özetinde kilitliyken gizlenen alanlar.
DASHBOARD_FINANCE_FIELDS = ("institutionFinance",)
DASHBOARD_OPERATIONS = frozenset({"admin_getDashboard"})


def locked(operation: Operation) -> bool:
    """İşlem kurum finansı kilidine tabi mi? (Kantin `canteen.*`, kitap `books.*`, sözleşme `contracts.*` DEĞİL.)"""
    path = operation.path
    if path.startswith("/v1/admin/finance/") or path == "/v1/admin/finance":
        return True
    return path.startswith("/v1/students/{studentId}/finance")


def masks(operation: Operation) -> bool:
    """Kilitliyken yanıtında finans alanı gizlenen işlem mi?"""
    return operation.operation_id in DASHBOARD_OPERATIONS


def mask(data: Any) -> Any:
    """Kilitliyken özet: finans alanları `null` + `institutionFinanceLocked: true` (Akademi'nin kişi oturumundaki
    davranışının aynısı; entegrasyon anahtarına Akademi kilit uygulamaz)."""
    if isinstance(data, dict):
        return {**data, **{name: None for name in DASHBOARD_FINANCE_FIELDS if name in data},
                "institutionFinanceLocked": True}
    return data


class FinanceAccessError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@dataclass(slots=True)
class _Challenge:
    user_id: str
    session: str
    expires: float
    approvers: list[dict[str, Any]]


class FinanceAccess:
    """KM oturumu başına kayan finans izni + bekleyen kod istekleri (bellekte)."""

    def __init__(self, *, gateway: AkademiGateway | None, log: Any,
                 idle_minutes: int = IDLE_MINUTES, clock: Callable[[], float] = time.time) -> None:
        self._gateway = gateway
        self._log = log
        self.idle_minutes = max(1, int(idle_minutes))
        self._clock = clock
        self._grants: dict[tuple[str, str], float] = {}
        self._challenges: dict[str, _Challenge] = {}
        #: Son bilinen onaylayıcılar (Akademi'nin son yanıtı; maskeli telefon) — kilit ekranı gösterir.
        self._approvers: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------ izin

    @staticmethod
    def _key(user: CurrentUser, session: str) -> tuple[str, str]:
        return (str(user.id), session or "")

    def until(self, user: CurrentUser, session: str) -> float | None:
        key = self._key(user, session)
        stamp = self._grants.get(key)
        if stamp is None:
            return None
        if stamp <= self._clock():
            self._grants.pop(key, None)
            self._log.info("finance_access.ended", user=user.id, reason="idle")
            return None
        return stamp

    def touch(self, user: CurrentUser, session: str) -> float | None:
        """İzin açıksa süreyi yeniden `idle_minutes`'a uzatır ve yeni bitişi döner; kapalıysa None."""
        if self.until(user, session) is None:
            return None
        stamp = self._clock() + self.idle_minutes * 60
        self._grants[self._key(user, session)] = stamp
        return stamp

    def grant(self, user: CurrentUser, session: str) -> float:
        stamp = self._clock() + self.idle_minutes * 60
        self._grants[self._key(user, session)] = stamp
        return stamp

    def end(self, user: CurrentUser, session: str) -> None:
        if self._grants.pop(self._key(user, session), None) is not None:
            self._log.info("finance_access.ended", user=user.id, reason="manual")
        for challenge_id in [cid for cid, item in self._challenges.items()
                             if item.user_id == str(user.id) and item.session == session]:
            self._challenges.pop(challenge_id, None)

    def state(self, user: CurrentUser, session: str) -> dict[str, Any]:
        stamp = self.until(user, session)
        approvers = self._approvers or []
        return {"active": stamp is not None, "until": iso(stamp) if stamp else None,
                "idleMinutes": self.idle_minutes, "approvers": approvers,
                "managerMissing": self._approvers is not None and not approvers}

    # ---------------------------------------------------- kod iste / doğrula

    async def request(self, user: CurrentUser, session: str) -> dict[str, Any]:
        body = {"actorName": user.full_name or str(user.id), "actorRef": f"km:user:{user.id}"}
        data = await self._post(REQUEST_PATH, body, user)  # 201
        challenge_id = str((data or {}).get("challengeId") or "")
        if not challenge_id:
            raise FinanceAccessError(502, "akademi_error", "Akademi onay kodu isteğine geçersiz yanıt verdi.")
        approvers = [item for item in (data.get("approvers") or []) if isinstance(item, dict)]
        self._approvers = [{"name": str(item.get("name") or ""),
                            "maskedPhone": str(item.get("maskedPhone") or "")} for item in approvers]
        self._prune()
        self._challenges[challenge_id] = _Challenge(user_id=str(user.id), session=session,
                                                    expires=self._clock() + CHALLENGE_SECONDS,
                                                    approvers=self._approvers)
        self._log.info("finance_access.requested", user=user.id, approvers=len(approvers))
        return {"challengeId": challenge_id, "expiresAt": data.get("expiresAt"),
                "approvers": self._approvers,
                "resendAfterSeconds": int(data.get("resendAfterSeconds") or 60)}

    async def verify(self, user: CurrentUser, session: str, challenge_id: str,
                     code: str) -> dict[str, Any]:
        challenge = self._challenges.get(challenge_id)
        if (challenge is None or challenge.user_id != str(user.id) or challenge.session != session
                or challenge.expires <= self._clock()):
            # Başka oturumun / kişinin kodu ya da süresi dolmuş istek: Akademi'ye hiç gitmez.
            self._log.info("finance_access.failed", user=user.id, reason="challenge_unknown")
            raise FinanceAccessError(400, "otp_expired",
                                     "Onay kodunun süresi doldu ya da bu oturuma ait değil. Yeni kod isteyin.")
        clean = "".join(ch for ch in str(code or "") if ch.isdigit())
        if len(clean) != 6:
            raise FinanceAccessError(422, "otp_invalid", "Onay kodu 6 haneli olmalı.")
        try:
            data = await self._post(VERIFY_PATH, {"challengeId": challenge_id, "code": clean,
                                                  "actorRef": f"km:user:{user.id}"}, user)
        except FinanceAccessError as failure:
            if failure.code in ("otp_expired", "otp_attempts_exceeded"):
                self._challenges.pop(challenge_id, None)
            self._log.info("finance_access.failed", user=user.id, reason=failure.code)
            raise
        if not (isinstance(data, dict) and data.get("granted") is True):
            self._log.info("finance_access.failed", user=user.id, reason="not_granted")
            raise FinanceAccessError(403, "otp_invalid", "Onay kodu doğrulanamadı.")
        self._challenges.pop(challenge_id, None)
        minutes = data.get("idleMinutes")
        if isinstance(minutes, int) and 1 <= minutes <= 240:
            self.idle_minutes = minutes
        self.grant(user, session)
        self._log.info("finance_access.granted", user=user.id)
        return self.state(user, session)

    # ------------------------------------------------------------ yardım

    def _prune(self) -> None:
        now = self._clock()
        for challenge_id in [cid for cid, item in self._challenges.items() if item.expires <= now]:
            self._challenges.pop(challenge_id, None)
        while len(self._challenges) >= MAX_CHALLENGES:
            self._challenges.pop(next(iter(self._challenges)))
        for key in [key for key, stamp in self._grants.items() if stamp <= now]:
            self._grants.pop(key, None)

    async def _post(self, path: str, body: dict[str, Any], user: CurrentUser) -> Any:
        if self._gateway is None:
            raise FinanceAccessError(503, "akademi_unavailable", "Akademi bağlantısı hazır değil.")
        headers = {"content-type": "application/json", "accept": "application/json",
                   "X-Akademi-On-Behalf-Of": on_behalf_of(user.id, user.full_name),
                   "X-Request-Id": request_id(None),
                   # Yazma uçları Idempotency-Key ister; her deneme yeni bir istektir (yeni kod / yeni doğrulama).
                   "Idempotency-Key": f"km-fin-{uuid.uuid4().hex}"}
        try:
            response = await self._gateway.send(
                "POST", path, headers=headers,
                content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
            try:
                raw = await response.aread()
            finally:
                await response.aclose()
        except AkademiError as failure:
            raise FinanceAccessError(failure.status, failure.code, str(failure), failure.details) from failure
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            error = payload.get("error") if isinstance(payload, dict) else None
            error = error if isinstance(error, dict) else {}
            status = 502 if response.status_code == 401 else response.status_code
            code = str(error.get("code") or "akademi_error")
            if code == "finance_manager_missing":
                self._approvers = []
            raise FinanceAccessError(status, code,
                                     str(error.get("message") or f"Akademi isteği reddetti ({response.status_code})."),
                                     error.get("details"))
        return payload


def iso(stamp: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
