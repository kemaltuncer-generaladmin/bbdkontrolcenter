"""Ödeme Talebi — iş kuralları.

KANTİNİN KURALLARI (değiştirilemez, ekran bunlara uyar):

- **Tutar seçilemez.** Talep her zaman öğrencinin O ANKİ borcuyla açılır.
- **Açık talep yeniden fiyatlanır.** Bekleyen bir talep varsa yenisi açılmaz;
  mevcut talebin tutarı güncel borca çekilir ve aynı `ref` döner. Yani
  "oluştur" ile "yeniden gönder" aynı işlemdir.
- **Borcu olmayana / veli telefonu olmayana gönderilmez** (`no_debt`, `no_phone`).
- **Borç nakit kapanınca** o öğrencinin bekleyen talepleri `EXPIRED` olur.

KANTİNDE OLMAYAN KORUMA: çift gönderim. Aynı öğrenciye arka arkaya talep açmak
kantinde her seferinde YENİ BİR SMS kuyruğa atar; dedupe ya da bekleme süresi
yoktur. Bu modül son gönderimi kendi tablosundan bilir ve yakın zamanda talep
gönderilmiş öğrenciyi varsayılan olarak atlar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse(text: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class PaymentRequestService:
    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any]) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config
        self._batches = store.table("batch")
        self._entries = store.table("entry")
        self._collections = store.table("collection")

    # -------------------------------------------------------------- okuma

    async def workspace(self) -> dict[str, Any]:
        """Açılış: talepler, öğrenciler, şablonlar, son gönderimler, tahsilat geçmişi."""
        try:
            students = await self._canteen.students()
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; ekran ayakta kalmalı
            students, connected, error = [], False, str(failure)
            self._log.warning("öğrenciler okunamadı", error=error)

        requests: list[dict[str, Any]] = []
        templates: dict[str, Any] = {}
        try:
            requests = await self._canteen.payment_requests()
        except Exception as failure:  # noqa: BLE001
            self._log.warning("talepler okunamadı", error=str(failure))
        try:
            templates = (await self._canteen.settings()).get("smsTemplates") or {}
        except Exception as failure:  # noqa: BLE001
            self._log.warning("şablonlar okunamadı", error=str(failure))

        now = datetime.now(UTC)
        enriched = []
        for item in requests:
            created = int(item.get("createdAt") or 0)
            age_days = None
            if created:
                age_days = round((now.timestamp() * 1000 - created) / 86_400_000, 1)
            enriched.append({**item, "ageDays": age_days})

        last_sent = {
            str(row["kantin_id"]): str(row["last_at"])
            for row in await self._store.fetch_all(
                f"SELECT kantin_id, MAX(created_at) AS last_at FROM {self._entries} "
                f"WHERE status = 'queued' GROUP BY kantin_id"
            )
        }

        collections = await self._store.fetch_all(
            f"SELECT * FROM {self._collections} ORDER BY created_at DESC LIMIT 100"
        )

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
        }

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
        for row in await self._store.fetch_all(
            f"SELECT kantin_id, MAX(created_at) AS last_at FROM {self._entries} "
            f"WHERE status = 'queued' GROUP BY kantin_id"
        ):
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

        ok = failed = 0
        for row in targets:
            status, reason, ref = "failed", "", ""
            try:
                result = await self._canteen.create_payment_request(row["kantinId"])
                if result.get("queued"):
                    status, ref = "queued", str(result.get("ref") or "")
                    # Kantin tutarı gönderim anında yeniden hesaplar; kaydımız onu izler.
                    row["amount"] = int(result.get("amount") or row["amount"])
                    ok += 1
                else:
                    reason = str(result.get("reason") or "bilinmeyen")
                    failed += 1
            except Exception as failure:  # noqa: BLE001 — biri patlarsa diğerleri sürsün
                reason = str(failure)
                failed += 1
                self._log.warning("talep gönderilemedi", student=row["kantinId"], error=reason)

            await self._store.execute(
                f"INSERT INTO {self._entries} (batch_ref, kantin_id, student_name, phone, "
                f"amount, ref, status, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_ref, row["kantinId"], row["name"], row["phone"],
                 int(row["amount"]), ref, status, reason, _now()),
            )

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

        # Öğrencinin GÜNCEL durumu — talep tutarı donmuştur, borç akar.
        student: dict[str, Any] = {}
        try:
            if kantin_id:
                student = await self._canteen.student(kantin_id)
        except Exception as failure:  # noqa: BLE001
            self._log.warning("öğrenci okunamadı", student=kantin_id, error=str(failure))

        # Veliye giden SMS'in akıbeti.
        #
        # KUYRUK ÖĞRENCİ KİMLİĞİ VERMEZ, yalnız MASKELİ numara döner
        # ("5337****87"). Eşleştirme bu yüzden numaranın baş 4 ve son 2
        # hanesiyle yapılır. Maskeleme kasıtlıdır (kişisel veri); biz de tam
        # numarayı hiçbir yere yazmayız, yalnız karşılaştırırız.
        messages: list[dict[str, Any]] = []
        phone = "".join(ch for ch in str(student.get("parentPhone") or "") if ch.isdigit())
        try:
            payload = await self._canteen.notifications(limit=200)
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

        if amount <= 0:
            return {"ok": False, "error": "Tutar sıfırdan büyük olmalı."}
        if balance <= 0:
            return {"ok": False, "error": f"{name} için güncel borç yok."}
        if amount > balance:
            return {"ok": False,
                    "error": f"Tutar borçtan fazla olamaz (borç {balance / 100:.2f} ₺)."}

        try:
            result = await self._canteen.collect_cash(kantin_id, amount=amount, local_id=local_id)
            status, reason = "ok", ""
        except Exception as failure:  # noqa: BLE001
            status, reason, result = "failed", str(failure), {}
            self._log.warning("tahsilat yazılamadı", student=kantin_id, error=reason)

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
