"""Ödeme Talebi — iş kuralları.

KANTİNİN KURALLARI (değiştirilemez, ekran bunlara uyar):

- **Toplu talepte tutar seçilemez.** Toplu gönderim her zaman öğrencinin O
  ANKİ borcuyla açılır. Tek öğrenciye ise SERBEST TUTARLI talep açılabilir
  (`request_one`, kantin-veli-kontrolleri §1.5): en az 1,00 ₺, en çok borç
  (+ kurum ön ödemeye izin verdiyse `maxPrepayment`). Tutarlı talep yeniden
  fiyatlanmaz.
- **Açık talep yeniden fiyatlanır.** Bekleyen (tutarsız) bir talep varsa yenisi
  açılmaz; mevcut talebin tutarı güncel borca çekilir ve aynı `ref` döner.
  Öğrenci başına tek açık talep vardır.
- **Nakit tahsilat** borcu aşamaz; ön ödeme açıksa sınır `borç + maxPrepayment`
  olur. Sınırı kantin uygular; ekran aynı sınırı önceden gösterir.
- **Borcu olmayana / veli telefonu olmayana gönderilmez** (`no_debt`, `no_phone`).
- **Borç nakit kapanınca** o öğrencinin bekleyen talepleri `EXPIRED` olur.

KANTİNDE OLMAYAN KORUMA: çift gönderim. Aynı öğrenciye arka arkaya talep açmak
kantinde her seferinde YENİ BİR SMS kuyruğa atar; dedupe ya da bekleme süresi
yoktur. Bu modül son gönderimi kendi tablosundan bilir ve yakın zamanda talep
gönderilmiş öğrenciyi varsayılan olarak atlar.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

#: Kantin ayarı okunamazsa ön ödeme KAPALI sayılır (güvenli taraf).
NO_PREPAYMENT = {"allowPrepayment": False, "maxPrepayment": 0}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _empty() -> dict[str, Any]:
    return {}


def _parse(text: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _settled(call: Awaitable[Any]) -> Any:
    """Kantin çağrısının sonucu ya da hatası — yükseltmez (ekran ayakta kalmalı)."""
    try:
        return await call
    except Exception as failure:  # noqa: BLE001 — kantin dışarısı
        return failure


class PaymentRequestService:
    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any]) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config
        self._batches = store.table("batch")
        self._entries = store.table("entry")
        self._collections = store.table("collection")
        self._last_sent = store.table("last_sent")

    # -------------------------------------------------------------- okuma

    async def _last_sent_rows(self) -> list[dict[str, Any]]:
        """Öğrenci başına son başarılı gönderim — özet tablodan (bkz. göç 002)."""
        return await self._store.fetch_all(f"SELECT kantin_id, last_at FROM {self._last_sent}")

    async def _record_entry(self, *, batch_ref: str, kantin_id: str, name: str, phone: str,
                            amount: int, ref: str, status: str, reason: str) -> None:
        """Gönderim kaydı; başarılıysa öğrencinin "son gönderim" özeti de güncellenir."""
        stamp = _now()
        await self._store.execute(
            f"INSERT INTO {self._entries} (batch_ref, kantin_id, student_name, phone, "
            f"amount, ref, status, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_ref, kantin_id, name, phone, amount, ref, status, reason, stamp),
        )
        if status == "queued":
            await self._store.execute(
                f"INSERT INTO {self._last_sent} (kantin_id, last_at) VALUES (?, ?) "
                f"ON CONFLICT(kantin_id) DO UPDATE SET last_at = excluded.last_at "
                f"WHERE excluded.last_at > {self._last_sent}.last_at",
                (kantin_id, stamp),
            )

    async def workspace(self) -> dict[str, Any]:
        """Açılış: talepler, öğrenciler, şablonlar, son gönderimler, tahsilat geçmişi.

        Dört kantin okuması birbirinden bağımsızdır ve AYNI ANDA istenir
        (eskiden sırayla: her biri bir öncekini bekliyordu).
        """
        students_call, requests_call, settings_call, prepayment_call = await asyncio.gather(
            _settled(self._canteen.students()),
            _settled(self._canteen.payment_requests()),
            _settled(self._canteen.settings()),
            self._prepayment(),
        )
        if isinstance(students_call, BaseException):
            students, connected, error = [], False, str(students_call)
            self._log.warning("öğrenciler okunamadı", error=error)
        else:
            students, connected, error = students_call, True, ""

        requests: list[dict[str, Any]] = []
        templates: dict[str, Any] = {}
        if isinstance(requests_call, BaseException):
            self._log.warning("talepler okunamadı", error=str(requests_call))
        else:
            requests = requests_call
        if isinstance(settings_call, BaseException):
            self._log.warning("şablonlar okunamadı", error=str(settings_call))
        else:
            templates = settings_call.get("smsTemplates") or {}

        now = datetime.now(UTC)
        enriched = []
        for item in requests:
            created = int(item.get("createdAt") or 0)
            age_days = None
            if created:
                age_days = round((now.timestamp() * 1000 - created) / 86_400_000, 1)
            enriched.append({**item, "ageDays": age_days})

        last_sent = {
            str(row["kantin_id"]): str(row["last_at"]) for row in await self._last_sent_rows()
        }

        collections = await self._store.fetch_all(
            f"SELECT * FROM {self._collections} ORDER BY created_at DESC LIMIT 100"
        )
        prepayment = prepayment_call if connected else dict(NO_PREPAYMENT)

        debtors = [item for item in students if int(item.get("balance") or 0) > 0]

        return {
            "connected": connected,
            "error": error,
            "requests": enriched,
            "students": [self._student(item) for item in students],
            "templates": templates,
            "lastSent": last_sent,
            "collections": [dict(row) for row in collections],
            "summary": {
                "students": len(students),
                "debtors": len(debtors),
                "openDebt": sum(int(item.get("balance") or 0) for item in debtors),
                "pending": sum(1 for item in requests if item.get("status") == "PENDING"),
                "paid": sum(1 for item in requests if item.get("status") == "PAID"),
            },
            "limits": {
                "cooldownMinutes": int(self._config.get("resend_cooldown_minutes") or 120),
                "maxRecipients": int(self._config.get("max_recipients") or 300),
            },
            # Kantinin ön ödeme ayarı (salt okunur). Kapalıyken tahsilat ve
            # serbest tutarlı talep borcu aşamaz.
            "prepayment": prepayment,
        }

    async def _prepayment(self) -> dict[str, Any]:
        try:
            value = await self._canteen.canteen_settings_public()
        except Exception as failure:  # noqa: BLE001 — okunamazsa ön ödeme KAPALI sayılır
            self._log.warning("ön ödeme ayarı okunamadı", error=str(failure))
            return dict(NO_PREPAYMENT)
        return {"allowPrepayment": bool(value.get("allowPrepayment")),
                "maxPrepayment": max(0, int(value.get("maxPrepayment") or 0))}

    @staticmethod
    def _limit(balance: int, prepayment: dict[str, Any]) -> int:
        """Tahsilat / serbest talep için en büyük tutar: borç (+ izinli ön ödeme)."""
        extra = int(prepayment.get("maxPrepayment") or 0) if prepayment.get("allowPrepayment") \
            else 0
        return max(0, balance) + max(0, extra)

    # ----------------------------------------------------- tek öğrenci kartı

    async def student_state(self, kantin_id: str) -> dict[str, Any]:
        """Öğrenci ekranındaki "Kantin tahsilatı / Ödeme talebi" kartının verisi.

        Güncel borç kantinden TEK öğrenci olarak okunur; açık talep son 100
        talep içinde aranır (kantin ucu süzgeç almıyor).
        """
        # Öğrenci, ön ödeme ayarı ve talepler BAĞIMSIZ: aynı anda istenir. Talep
        # listesi öğrenci süzgeciyle istenir — kantin süzgeci tanıyorsa sunucuda,
        # tanımıyorsa istemcide süzülür (`canteen.api.payment_requests`).
        student_call, prepayment, requests_call = await asyncio.gather(
            _settled(self._canteen.student(kantin_id)),
            self._prepayment(),
            _settled(self._canteen.payment_requests(student_id=kantin_id)),
        )
        if isinstance(student_call, BaseException):
            return {"ok": False, "error": str(student_call)}
        student = student_call
        if not student:
            return {"ok": False, "error": "Öğrenci kantinde bulunamadı."}
        balance = int(student.get("balance") or 0)
        pending = None
        if isinstance(requests_call, BaseException):  # talep listesi yoksa kart yine çalışır
            self._log.warning("talepler okunamadı", error=str(requests_call))
        else:
            for item in requests_call:
                if str(item.get("studentId")) == kantin_id and item.get("status") == "PENDING":
                    pending = {"ref": item.get("ref"), "amount": int(item.get("amount") or 0),
                               "createdAt": item.get("createdAt"),
                               "amountFixed": bool(item.get("amountFixed"))}
                    break
        collections = await self._store.fetch_all(
            f"SELECT * FROM {self._collections} WHERE kantin_id = ? "
            f"ORDER BY created_at DESC LIMIT 5", (kantin_id,),
        )
        phone = str(student.get("parentPhone") or "").strip()
        return {
            "ok": True,
            "student": {"kantinId": kantin_id, "displayName": student.get("displayName"),
                        "balance": balance, "hasPhone": bool(phone)},
            "prepayment": prepayment,
            "maxCollect": self._limit(balance, prepayment),
            "maxRequest": self._limit(balance, prepayment),
            "pendingRequest": pending,
            "collections": [dict(row) for row in collections],
        }

    async def request_one(self, kantin_id: str, *, amount: int | None,
                          actor: str) -> dict[str, Any]:
        """Tek öğrenciye ödeme talebi — tam borç (`amount=None`) ya da serbest tutar.

        Kayıt toplu gönderimle aynı tablolara düşer (tek kişilik parti): geçmiş
        ve "yakında gönderildi" koruması tek yerden okunur.
        """
        try:
            student = await self._canteen.student(kantin_id)
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}
        name = str(student.get("displayName") or "")
        phone = str(student.get("parentPhone") or "").strip()
        balance = int(student.get("balance") or 0)

        status, reason, ref, sent_amount, extra = "failed", "", "", int(amount or balance), {}
        try:
            result = await self._canteen.create_payment_request(kantin_id, amount,
                                                                origin="admin")
            status, ref = "queued", str(result.get("ref") or "")
            sent_amount = int(result.get("amount") or sent_amount)
            extra = {"linkUrl": result.get("linkUrl"),
                     "amountFixed": bool(result.get("amountFixed"))}
        except Exception as failure:  # noqa: BLE001 — sebebi kayda ve ekrana geçer
            reason = str(getattr(failure, "reason", None) or failure)
            payload = getattr(failure, "payload", None) or {}
            if reason == "exceeds_debt" and payload.get("maxAmount") is not None:
                extra = {"maxAmount": int(payload["maxAmount"])}

        batch_ref = f"P{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        note = "tek öğrenci" + (" · serbest tutar" if amount is not None else "")
        await self._store.execute(
            f"INSERT INTO {self._batches} (batch_ref, note, total_count, amount_sum, "
            f"created_at, created_by, ok_count, fail_count) VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
            (batch_ref, note, sent_amount, _now(), actor,
             1 if status == "queued" else 0, 0 if status == "queued" else 1),
        )
        await self._record_entry(batch_ref=batch_ref, kantin_id=kantin_id, name=name,
                                 phone=phone, amount=sent_amount, ref=ref, status=status,
                                 reason=reason)
        if status != "queued":
            text = self._reason_text(reason)
            if reason == "exceeds_debt" and "maxAmount" in extra:
                text = f"Tutar sınırı aşıyor (en çok {extra['maxAmount'] / 100:.2f} ₺)."
            return {"ok": False, "reason": reason, "error": text, **extra}
        self._log.info("tek öğrenciye ödeme talebi", student=kantin_id, ref=ref,
                       amount=sent_amount, fixed=amount is not None, actor=actor)
        return {"ok": True, "ref": ref, "amount": sent_amount, "batchRef": batch_ref, **extra}

    @staticmethod
    def _student(student: dict[str, Any]) -> dict[str, Any]:
        phone = str(student.get("parentPhone") or "").strip()
        return {
            "kantinId": student.get("id"),
            "displayName": student.get("displayName"),
            "parentPhone": phone,
            "hasPhone": bool(phone),
            "balance": int(student.get("balance") or 0),
            "spendingLimit": student.get("spendingLimit"),
            "isBlocked": bool(student.get("isBlocked")),
        }

    async def batches(self, *, limit: int = 100) -> dict[str, Any]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._batches} ORDER BY created_at DESC LIMIT ?", (int(limit),)
        )
        return {"batches": [dict(row) for row in rows]}

    async def batch_detail(self, batch_ref: str) -> dict[str, Any]:
        batch = await self._store.fetch_one(
            f"SELECT * FROM {self._batches} WHERE batch_ref = ?", (batch_ref,)
        )
        if batch is None:
            return {"ok": False, "error": "Gönderim bulunamadı."}
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._entries} WHERE batch_ref = ? ORDER BY id", (batch_ref,)
        )
        return {"ok": True, "batch": dict(batch), "entries": [dict(row) for row in rows]}

    # ---------------------------------------------------------- ön izleme

    async def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Gönderim yapmadan: kime, hangi veli numarasına, hangi tutarla gidecek."""
        try:
            students = {str(item.get("id")): item for item in await self._canteen.students()}
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure), "rows": [], "summary": {}}

        cooldown = int(self._config.get("resend_cooldown_minutes") or 120)
        allow_repeat = bool(payload.get("allowRepeat"))
        threshold = datetime.now(UTC) - timedelta(minutes=cooldown)

        recent: dict[str, datetime] = {}
        for row in await self._last_sent_rows():
            when = _parse(row["last_at"])
            if when:
                recent[str(row["kantin_id"])] = when

        rows: list[dict[str, Any]] = []
        eligible = total = 0

        for kantin_id in payload.get("students") or []:
            kantin_id = str(kantin_id)
            student = students.get(kantin_id)
            if student is None:
                rows.append(self._row(kantin_id, "", "", 0, "missing",
                                      "Öğrenci kantinde bulunamadı."))
                continue

            name = str(student.get("displayName") or "")
            phone = str(student.get("parentPhone") or "").strip()
            balance = int(student.get("balance") or 0)

            if balance <= 0:
                rows.append(self._row(kantin_id, name, phone, balance, "no_debt",
                                      "Güncel borcu yok — kantin talep açmaz."))
            elif not phone:
                rows.append(self._row(kantin_id, name, "", balance, "no_phone",
                                      "Veli telefonu tanımlı değil — SMS gönderilemez."))
            elif kantin_id in recent and recent[kantin_id] > threshold and not allow_repeat:
                minutes = int((datetime.now(UTC) - recent[kantin_id]).total_seconds() // 60)
                rows.append(self._row(
                    kantin_id, name, phone, balance, "recent",
                    f"{minutes} dakika önce zaten talep gönderildi — tekrar için onay gerekir.",
                ))
            else:
                verdict = "repeat" if kantin_id in recent and recent[kantin_id] > threshold else "ok"
                rows.append(self._row(kantin_id, name, phone, balance, verdict, ""))
                eligible += 1
                total += balance

        limits = {
            "cooldownMinutes": cooldown,
            "maxRecipients": int(self._config.get("max_recipients") or 300),
        }

        return {
            "ok": True,
            "rows": rows,
            "summary": {
                "selected": len(payload.get("students") or []),
                "eligible": eligible,
                "noDebt": sum(1 for row in rows if row["verdict"] == "no_debt"),
                "noPhone": sum(1 for row in rows if row["verdict"] == "no_phone"),
                "recent": sum(1 for row in rows if row["verdict"] == "recent"),
                "totalAmount": total,
                "allowRepeat": allow_repeat,
                "overLimit": eligible > limits["maxRecipients"],
                **limits,
            },
        }

    @staticmethod
    def _row(kantin_id: str, name: str, phone: str, amount: int, verdict: str,
             message: str) -> dict[str, Any]:
        return {"kantinId": kantin_id, "name": name, "phone": phone,
                "amount": amount, "verdict": verdict, "message": message, "ref": ""}

    # -------------------------------------------------------------- yazma

    async def send(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        preview = await self.preview(payload)
        if not preview["ok"]:
            return preview
        if payload.get("dryRun"):
            return {**preview, "dryRun": True}

        summary = preview["summary"]
        if summary["eligible"] == 0:
            hint = ""
            if summary["recent"]:
                hint = (f" {summary['recent']} öğrenciye yakın zamanda gönderilmiş; "
                        "tekrar için “yeniden göndermeye izin ver” seçeneğini işaretleyin.")
            return {**preview, "sent": False, "error": f"Gönderilecek uygun öğrenci yok.{hint}"}
        if summary["overLimit"]:
            return {**preview, "sent": False,
                    "error": f"Alıcı sınırı aşılıyor ({summary['eligible']} > "
                             f"{summary['maxRecipients']})."}

        targets = [row for row in preview["rows"] if row["verdict"] in ("ok", "repeat")]
        batch_ref = f"P{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        await self._store.execute(
            f"INSERT INTO {self._batches} (batch_ref, note, total_count, amount_sum, "
            f"created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_ref, str(payload.get("note") or ""), len(targets),
             int(summary["totalAmount"]), _now(), actor),
        )

        # Talebi kimin açtığı kantinde kayda geçer: tek öğrenciye gönderim
        # personelin tekil işidir (`admin`), birden çoğu toplu gönderimdir.
        origin = "admin" if len(payload.get("students") or []) == 1 else "bulk"

        ok = failed = 0
        for row in targets:
            status, reason, ref = "failed", "", ""
            try:
                result = await self._canteen.create_payment_request(row["kantinId"],
                                                                    origin=origin)
                if result.get("queued"):
                    status, ref = "queued", str(result.get("ref") or "")
                    # Kantin tutarı gönderim anında yeniden hesaplar; kaydımız onu izler.
                    row["amount"] = int(result.get("amount") or row["amount"])
                    ok += 1
                else:
                    reason = str(result.get("reason") or "bilinmeyen")
                    failed += 1
            except Exception as failure:  # noqa: BLE001 — biri patlarsa diğerleri sürsün
                # Kantin reddi (`no_debt`, `no_phone`…) artık hata olarak gelir ve
                # kodunu taşır; kayıt eskisi gibi KODU yazsın.
                reason = str(getattr(failure, "reason", None) or failure)
                failed += 1
                self._log.warning("talep gönderilemedi", student=row["kantinId"], error=reason)

            await self._record_entry(batch_ref=batch_ref, kantin_id=row["kantinId"],
                                     name=row["name"], phone=row["phone"],
                                     amount=int(row["amount"]), ref=ref, status=status,
                                     reason=reason)

        for row in preview["rows"]:
            if row["verdict"] in ("ok", "repeat"):
                continue
            await self._store.execute(
                f"INSERT INTO {self._entries} (batch_ref, kantin_id, student_name, phone, "
                f"amount, ref, status, reason, created_at) "
                f"VALUES (?, ?, ?, ?, ?, '', 'skipped', ?, ?)",
                (batch_ref, row["kantinId"], row["name"], row["phone"],
                 int(row["amount"]), row["message"], _now()),
            )

        await self._store.execute(
            f"UPDATE {self._batches} SET ok_count = ?, fail_count = ? WHERE batch_ref = ?",
            (ok, failed, batch_ref),
        )
        self._log.info("ödeme talebi partisi", batch=batch_ref, ok=ok, failed=failed)

        detail = await self.batch_detail(batch_ref)
        return {"ok": True, "sent": True, "batchRef": batch_ref, "okCount": ok,
                "failCount": failed, "entries": detail.get("entries", []), "summary": summary}

    async def resend(self, ref: str) -> dict[str, Any]:
        """Bekleyen talebi güncel borca göre yeniden gönderir; aynı `ref` korunur."""
        try:
            result = await self._canteen.resend_payment_request(ref)
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}
        if not result.get("queued"):
            return {"ok": False, "reason": result.get("reason"),
                    "error": self._reason_text(str(result.get("reason") or ""))}
        return {"ok": True, "ref": result.get("ref"), "amount": result.get("amount")}

    async def cancel(self, ref: str) -> dict[str, Any]:
        """Bekleyen talebi iptal eder (`EXPIRED`). Satır silinmez, ödeme linki ölür."""
        try:
            result = await self._canteen.cancel_payment_request(ref)
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}
        return {"ok": bool(result.get("cancelled")), "ref": ref,
                "error": "" if result.get("cancelled") else "Talep iptal edilemedi."}

    @staticmethod
    def _reason_text(reason: str) -> str:
        return {
            "no_debt": "Öğrencinin güncel borcu yok.",
            "no_phone": "Veli telefonu tanımlı değil.",
            "not_found": "Talep bulunamadı.",
            "not_pending": "Talep zaten ödenmiş ya da kapanmış.",
            "student_not_found": "Öğrenci kantinde bulunamadı.",
            "exceeds_debt": "Tutar borçtan (ve izinli ön ödemeden) fazla.",
            "invalid_amount": "Tutar geçersiz (en az 1,00 ₺).",
            "validation_error": "Kantin isteği reddetti (geçersiz değer).",
        }.get(reason, f"Gönderilemedi ({reason}).")

    # ------------------------------------------------------- talep ayrıntısı

    async def request_detail(self, ref: str) -> dict[str, Any]:
        """Tek bir talebin BÜTÜN hikâyesi.

        Üç kaynağı birleştirir, çünkü hiçbiri tek başına "ne oldu" sorusuna
        cevap vermiyor:
          · kantinin talep kaydı  → tutar, durum, ödeme zamanı
          · kantinin SMS kuyruğu  → veliye ULAŞTI MI, kaç denemede, neden olmadı
          · bizim gönderim kaydımız → BU panelden kim, ne zaman, hangi partide gönderdi

        Öğrencinin o anki borcu da eklenir: talep açıldığındaki tutar ile bugünkü
        borç farklıysa (arada alışveriş ya da tahsilat olmuşsa) ekran bunu söyler.
        """
        try:
            requests = await self._canteen.payment_requests()
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}

        record = next((item for item in requests if str(item.get("ref")) == ref), None)
        if record is None:
            return {"ok": False, "error": "Talep kantinde bulunamadı. Kantin son 100 talebi verir."}

        kantin_id = str(record.get("studentId") or "")

        # Öğrencinin GÜNCEL durumu — talep tutarı donmuştur, borç akar. SMS
        # kuyruğu öğrenciden bağımsız okunur; ikisi aynı anda istenir.
        student: dict[str, Any] = {}
        student_call, queue_call = await asyncio.gather(
            _settled(self._canteen.student(kantin_id)) if kantin_id else _settled(_empty()),
            _settled(self._canteen.notifications(limit=200)),
        )
        if isinstance(student_call, BaseException):
            self._log.warning("öğrenci okunamadı", student=kantin_id, error=str(student_call))
        else:
            student = student_call or {}

        # Veliye giden SMS'in akıbeti.
        #
        # KUYRUK ÖĞRENCİ KİMLİĞİ VERMEZ, yalnız MASKELİ numara döner
        # ("5337****87"). Eşleştirme bu yüzden numaranın baş 4 ve son 2
        # hanesiyle yapılır. Maskeleme kasıtlıdır (kişisel veri); biz de tam
        # numarayı hiçbir yere yazmayız, yalnız karşılaştırırız.
        messages: list[dict[str, Any]] = []
        phone = "".join(ch for ch in str(student.get("parentPhone") or "") if ch.isdigit())
        try:
            if isinstance(queue_call, BaseException):
                raise queue_call
            payload = queue_call
            rows = payload.get("data") or payload.get("notifications") or []
            for item in rows:
                if phone and self._same_phone(phone, str(item.get("to") or "")):
                    messages.append({
                        "status": item.get("state") or item.get("status"),
                        "phone": item.get("to"),
                        "template": item.get("template"),
                        "attempts": item.get("attempts"),
                        "createdAt": item.get("createdAt"),
                        "sentAt": item.get("sentAt"),
                        "nextRetryAt": item.get("nextRetryAt"),
                    })
        except Exception as failure:  # noqa: BLE001
            self._log.warning("SMS kuyruğu okunamadı", error=str(failure))

        # Bu panelden yapılan gönderimler.
        entries = await self._store.fetch_all(
            f"SELECT * FROM {self._entries} WHERE kantin_id = ? ORDER BY id DESC LIMIT 20",
            (kantin_id,),
        )
        collections = await self._store.fetch_all(
            f"SELECT * FROM {self._collections} WHERE kantin_id = ? "
            f"ORDER BY created_at DESC LIMIT 20",
            (kantin_id,),
        )

        created = int(record.get("createdAt") or 0)
        age_days = None
        if created:
            age_days = round((datetime.now(UTC).timestamp() * 1000 - created) / 86_400_000, 1)

        balance = int(student.get("balance") or 0) if student else None
        amount = int(record.get("amount") or 0)

        return {
            "ok": True,
            "request": {**record, "ageDays": age_days},
            "student": {
                "kantinId": kantin_id,
                "displayName": student.get("displayName") if student else record.get("studentName"),
                "parentPhone": str(student.get("parentPhone") or "") if student else "",
                "balance": balance,
                "isBlocked": bool(student.get("isBlocked")) if student else False,
            },
            # Tutar donmuş, borç akmış olabilir; ekran bunu açıkça yazsın.
            "drift": None if balance is None else balance - amount,
            "messages": messages,
            "entries": [dict(row) for row in entries],
            "collections": [dict(row) for row in collections],
        }

    @staticmethod
    def _same_phone(full: str, masked: str) -> bool:
        """Maskeli numara ("5337****87") gerçek numarayla aynı mı.

        Yıldızların sayısı sürüme göre değişebilir; bu yüzden desen değil,
        yalnız görünen baş ve son haneler karşılaştırılır. En az 6 hane
        eşleşmezse "aynı" denmez — yanlış eşleşme, başkasının SMS'ini bu
        talebin altında göstermek olurdu.
        """
        visible = "".join(ch for ch in masked if ch.isdigit())
        if len(full) < 6 or len(visible) < 6:
            return False
        head, tail = masked.split("*", 1)[0], masked.rsplit("*", 1)[-1]
        head = "".join(ch for ch in head if ch.isdigit())
        tail = "".join(ch for ch in tail if ch.isdigit())
        if len(head) < 3 or len(tail) < 2:
            return False
        return full.startswith(head) and full.endswith(tail) and len(full) == len(masked)

    async def reopen(self, ref: str, *, actor: str) -> dict[str, Any]:
        """Kapanmış bir talebin öğrencisine YENİ talep açar.

        `resend` yalnız BEKLEYEN talep için çalışır (kantin `not_pending` der).
        Ödenmiş ya da iptal edilmiş bir talebi "tekrar göndermek" aslında yeni
        talep açmaktır; tutar yine kantinin bildiği GÜNCEL borçtur, eski tutar
        değil. Bu ayrım ekranda da yazılıdır — kullanıcı eski tutarın tekrar
        isteneceğini sanmamalı.
        """
        try:
            requests = await self._canteen.payment_requests()
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}

        record = next((item for item in requests if str(item.get("ref")) == ref), None)
        if record is None:
            return {"ok": False, "error": "Talep bulunamadı."}

        kantin_id = str(record.get("studentId") or "")
        if not kantin_id:
            return {"ok": False, "error": "Talepte öğrenci kimliği yok."}

        # Tek kişilik bir gönderim partisi: kayıt, çift gönderim koruması ve
        # denetim izi normal toplu gönderimle aynı yoldan geçsin.
        return await self.send(
            {"students": [kantin_id], "note": f"tek talep · {ref}", "allowRepeat": True},
            actor=actor,
        )

    # ---------------------------------------------------------- tahsilat

    async def collect(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Elden alınan nakit tahsilat.

        Kantin kuralları: tutar borçtan fazla olamaz, borç yoksa reddedilir,
        `localId` ile idempotenttir. Borç tümüyle kapanırsa o öğrencinin
        BEKLEYEN talepleri `EXPIRED` olur — ekran bunu önceden bildirir.
        """
        kantin_id = str(payload["kantinId"])
        amount = int(payload["amount"])
        # İstemci üretir: ağ koparsa aynı id ile tekrar gönderilir, çift alacak olmaz.
        local_id = str(payload.get("localId") or uuid.uuid4())

        try:
            student = await self._canteen.student(kantin_id)
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}

        name = str(student.get("displayName") or "")
        balance = int(student.get("balance") or 0)
        prepayment = await self._prepayment()
        limit = self._limit(balance, prepayment)

        if amount <= 0:
            return {"ok": False, "error": "Tutar sıfırdan büyük olmalı."}
        if limit <= 0:
            return {"ok": False, "error": f"{name} için güncel borç yok."}
        if amount > limit:
            what = "borç + izinli ön ödeme" if prepayment.get("allowPrepayment") else "borç"
            return {"ok": False, "maxAmount": limit,
                    "error": f"Tutar sınırı aşıyor (en çok {limit / 100:.2f} ₺ — {what})."}

        try:
            result = await self._canteen.record_collection(kantin_id, amount, local_id)
            status, reason = "ok", ""
        except Exception as failure:  # noqa: BLE001
            code = getattr(failure, "reason", None)
            reason = self._reason_text(str(code)) if code else str(failure)
            status, result = "failed", {}
            self._log.warning("tahsilat yazılamadı", student=kantin_id, error=str(failure))

        await self._store.execute(
            f"INSERT OR REPLACE INTO {self._collections} (local_id, kantin_id, student_name, "
            f"amount, balance_after, status, reason, note, created_at, created_by) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (local_id, kantin_id, name, amount, int(result.get("balance") or 0),
             status, reason, str(payload.get("note") or ""), _now(), actor),
        )

        if status != "ok":
            return {"ok": False, "error": reason}

        remaining = int(result.get("balance") or 0)
        return {
            "ok": True, "localId": local_id, "amount": amount, "balance": remaining,
            "settled": remaining <= 0, "studentName": name,
        }

    # ---------------------------------------------------------- şablonlar

    async def update_templates(self, templates: dict[str, str]) -> dict[str, Any]:
        """Ödeme SMS şablonları. Boş şablon YAZILAMAZ — kantin onu varsayılana düşürür
        ve ödeme linki SMS'ten sessizce kaybolur."""
        cleaned = {key: str(value or "").strip() for key, value in templates.items()}
        empty = [key for key, value in cleaned.items() if not value]
        if empty:
            return {"ok": False,
                    "error": f"Şablon boş bırakılamaz: {', '.join(sorted(empty))}"}

        missing = [key for key, value in cleaned.items()
                   if key == "payment_link" and "{linkUrl}" not in value]
        if missing:
            return {"ok": False,
                    "error": "Ödeme linki şablonunda {linkUrl} yer tutucusu bulunmalı; "
                             "yoksa veliye linksiz SMS gider."}

        try:
            result = await self._canteen.update_settings({"smsTemplates": cleaned})
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "templates": result.get("smsTemplates") or {}}
