"""SMS Sistemi — iş kuralları.

SERBEST SMS. Bu modül ödeme linki göndermez; kurumun Netgsm başlığından velilere
serbest metin gönderir. Ödeme linki Ödeme Talebi modülünün işidir.

ÜÇ GÜVENCE:

1. **Alıcı daima veli.** Kantinde öğrenci telefonu diye bir alan yok (26 Temmuz
   göçüyle silindi) ve SMS ucu öğrenci verildiğinde serbest telefonu yok sayıp
   `parent_phone` kullanıyor. Ekran hangi veli numarasına gideceğini satır satır
   yazar; velisi olmayan gönderimden düşer.
2. **Kör gönderim yok.** `preview()` her alıcı için çözülmüş metni, numarayı,
   segment ve kredi maliyetini gönderimden ÖNCE verir.
3. **Geçmiş bizde.** Kantinin `POST /api/sms/send` ucu eş zamanlıdır ve
   gönderdiğini hiçbir yere yazmaz. Ne gönderdiğimizin tek kaydı bu modüldedir.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from . import segments as seg

#: Metinde çözülen yer tutucular. Kantinin şablon yer tutucularıyla karışmasın
#: diye Türkçe: bu metin serbesttir, kantin şablonu değildir.
PLACEHOLDERS = {
    "{ad}": "Öğrencinin adı",
    "{sinif}": "Sınıfı (Öğrenci Yönetimi'nden)",
    "{borc}": "Güncel borcu (₺)",
    "{okul}": "Kurum adı",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _money(kurus: Any) -> str:
    return f"{int(kurus or 0) / 100:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")


class SmsService:
    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any]) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config
        self._batches = store.table("batch")
        self._messages = store.table("message")
        self._presets = store.table("preset")

    # -------------------------------------------------------------- okuma

    async def workspace(self) -> dict[str, Any]:
        """Açılış verisi: alıcılar, hazır mesajlar, Netgsm durumu, kuyruk özeti."""
        try:
            students = await self._canteen.students()
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; ekran ayakta kalmalı
            students, connected, error = [], False, str(failure)
            self._log.warning("öğrenciler okunamadı", error=error)

        settings: dict[str, Any] = {}
        queue: dict[str, Any] = {}
        try:
            settings = await self._canteen.settings()
            queue = (await self._canteen.notifications(limit=1)).get("summary") or {}
        except Exception as failure:  # noqa: BLE001
            self._log.warning("ayar/kuyruk okunamadı", error=str(failure))

        presets = await self._store.fetch_all(
            f"SELECT id, name, body, created_by FROM {self._presets} ORDER BY name"
        )

        return {
            "connected": connected,
            "error": error,
            "students": [self._recipient(item) for item in students],
            # Velisi olmayan öğrenciye SMS gitmez; ekran bunu baştan sayı olarak göstersin.
            "withPhone": sum(1 for item in students
                             if str(item.get("parentPhone") or "").strip()),
            "presets": [dict(row) for row in presets],
            "placeholders": PLACEHOLDERS,
            # Ekran, yazarken canlı örnek gösterirken {okul} yer tutucusunu da
            # çözmek zorunda; sunucudakiyle aynı değeri kullansın diye buradan verilir.
            "schoolName": str(self._config.get("school_name") or "").strip(),
            "netgsm": {
                "usercode": settings.get("netgsmUsercode", ""),
                "header": settings.get("netgsmHeader", ""),
                "passwordConfigured": bool(settings.get("netgsmPasswordConfigured")),
                "paymentConfirmedEnabled": bool(settings.get("smsPaymentConfirmedEnabled")),
                # Netgsm kimliği eksikse HİÇBİR SMS gitmez; ekran bunu baştan söylesin.
                "ready": bool(settings.get("netgsmUsercode")) and bool(settings.get("netgsmHeader"))
                and bool(settings.get("netgsmPasswordConfigured")),
            },
            "queue": queue,
            "limits": {
                "maxRecipients": int(self._config.get("max_recipients") or 500),
                "creditWarning": int(self._config.get("credit_warning_threshold") or 300),
            },
        }

    @staticmethod
    def _recipient(student: dict[str, Any]) -> dict[str, Any]:
        phone = str(student.get("parentPhone") or "").strip()
        return {
            "kantinId": student.get("id"),
            "displayName": student.get("displayName"),
            "parentPhone": phone,
            "hasPhone": bool(phone),
            "balance": int(student.get("balance") or 0),
            "isBlocked": bool(student.get("isBlocked")),
        }

    async def history(self, *, limit: int = 200) -> dict[str, Any]:
        """Bu panelden gönderilmiş SMS'ler — kantinde bu kayıt yok."""
        batches = await self._store.fetch_all(
            f"SELECT * FROM {self._batches} ORDER BY created_at DESC LIMIT ?", (int(limit),)
        )
        return {"batches": [dict(row) for row in batches]}

    async def batch_detail(self, batch_ref: str) -> dict[str, Any]:
        batch = await self._store.fetch_one(
            f"SELECT * FROM {self._batches} WHERE batch_ref = ?", (batch_ref,)
        )
        if batch is None:
            return {"ok": False, "error": "Gönderim bulunamadı."}
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._messages} WHERE batch_ref = ? ORDER BY id", (batch_ref,)
        )
        return {"ok": True, "batch": dict(batch), "messages": [dict(row) for row in rows]}

    async def queue(self, state: str | None = None) -> dict[str, Any]:
        """Kantin SMS kuyruğu (ödeme SMS'leri) — durum ve yeniden deneme bilgisi."""
        try:
            return await self._canteen.notifications(state=state, limit=200)
        except Exception as failure:  # noqa: BLE001
            return {"data": [], "summary": {}, "error": str(failure)}

    # ---------------------------------------------------------- ön izleme

    async def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Gönderim yapmadan: kime, hangi numaraya, hangi metin, kaç kredi."""
        body = str(payload.get("body") or "").strip()
        include_debt = bool(payload.get("includeDebt"))
        include_daily = bool(payload.get("includeDaily"))

        if not body and not include_debt and not include_daily:
            return {"ok": False, "error": "Mesaj boş olamaz.", "rows": [], "summary": {}}

        try:
            students = {str(item.get("id")): item for item in await self._canteen.students()}
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure), "rows": [], "summary": {}}

        classes = {str(k): str(v) for k, v in (payload.get("classes") or {}).items()}
        school = str(self._config.get("school_name") or "").strip()
        simplify = bool(payload.get("simplify"))

        rows: list[dict[str, Any]] = []
        eligible = credits = 0

        for kantin_id in payload.get("students") or []:
            kantin_id = str(kantin_id)
            student = students.get(kantin_id)
            if student is None:
                rows.append(self._row(kantin_id, "", "", "skipped", "Öğrenci kantinde bulunamadı."))
                continue

            name = str(student.get("displayName") or "")
            phone = str(student.get("parentPhone") or "").strip()
            if not phone:
                rows.append(self._row(kantin_id, name, "", "skipped",
                                      "Veli telefonu tanımlı değil — SMS gönderilmez."))
                continue

            text = self._render(body, student, classes.get(kantin_id, ""), school)
            if simplify:
                text = seg.simplify(text)

            # Kantin `includeDebt`/`includeDaily` cümlelerini metnin BAŞINA ekler;
            # önizleme bunu birebir taklit eder ki segment sayısı gerçeği göstersin.
            prefix = []
            if include_debt:
                prefix.append(f"Sayın veli, {name} güncel borç: "
                              f"{_money(student.get('balance'))}.")
            if include_daily:
                prefix.append(f"{name} bugün toplam … alışveriş yaptı.")
            full = " ".join([*prefix, text]).strip()

            measure = seg.measure(full)
            rows.append({
                **self._row(kantin_id, name, phone, "ready", ""),
                # `text`: velinin göreceği TAM metin (ön ek cümleleri dahil) — önizleme
                # ve geçmiş kaydı bunu kullanır.
                # `body`: kantine `message` olarak gidecek kısım. Ön ek cümlelerini
                # kantin kendisi ekler; buraya koyarsak iki kez yazılır.
                "text": full,
                "body": text,
                "segments": measure["segments"],
                "encoding": measure["encoding"],
            })
            eligible += 1
            credits += int(measure["segments"])

        sample = next((row["text"] for row in rows if row["verdict"] == "ready"), body)
        measure = seg.measure(seg.simplify(sample) if simplify else sample)

        limits = {
            "maxRecipients": int(self._config.get("max_recipients") or 500),
            "creditWarning": int(self._config.get("credit_warning_threshold") or 300),
        }

        return {
            "ok": True,
            "rows": rows,
            "measure": measure,
            "summary": {
                "selected": len(payload.get("students") or []),
                "eligible": eligible,
                "skipped": sum(1 for row in rows if row["verdict"] == "skipped"),
                "credits": credits,
                "overLimit": eligible > limits["maxRecipients"],
                "overCredit": credits > limits["creditWarning"],
                **limits,
            },
        }

    @staticmethod
    def _row(kantin_id: str, name: str, phone: str, verdict: str,
             message: str) -> dict[str, Any]:
        return {"kantinId": kantin_id, "name": name, "phone": phone,
                "verdict": verdict, "message": message, "text": "", "body": "", "segments": 0}

    @staticmethod
    def _render(body: str, student: dict[str, Any], class_name: str, school: str) -> str:
        """Yer tutucuları çözer. Bilinmeyen `{...}` olduğu gibi kalır."""
        return (body
                .replace("{ad}", str(student.get("displayName") or ""))
                .replace("{sinif}", class_name)
                .replace("{borc}", _money(student.get("balance")))
                .replace("{okul}", school))

    # -------------------------------------------------------------- yazma

    async def send(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """SMS'leri gönderir. Kuru provada hiçbir şey gitmez."""
        preview = await self.preview(payload)
        if not preview["ok"]:
            return preview
        if payload.get("dryRun"):
            return {**preview, "dryRun": True}

        summary = preview["summary"]
        if summary["eligible"] == 0:
            return {**preview, "sent": False, "error": "Gönderilecek uygun alıcı yok."}
        if summary["overLimit"]:
            return {**preview, "sent": False,
                    "error": f"Alıcı sayısı sınırı aşıyor ({summary['eligible']} > "
                             f"{summary['maxRecipients']}). Ayardan yükseltilebilir."}

        targets = [row for row in preview["rows"] if row["verdict"] == "ready"]
        batch_ref = f"S{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        measure = preview["measure"]

        await self._store.execute(
            f"INSERT INTO {self._batches} (batch_ref, title, body, encoding, segments, "
            f"include_debt, include_daily, total_count, created_at, created_by) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (batch_ref, str(payload.get("title") or ""), str(payload.get("body") or ""),
             str(measure["encoding"]), int(measure["segments"]),
             1 if payload.get("includeDebt") else 0, 1 if payload.get("includeDaily") else 0,
             len(targets), _now(), actor),
        )

        sent = failed = credits = 0
        for row in targets:
            status, reason = "failed", ""
            try:
                # Metin kişiselleştirildiği için tek tek gider. `studentId` verildiğinde
                # kantin alıcıyı VELİ numarasına sabitler — bizim çözdüğümüz metinle aynı.
                result = await self._canteen.send_sms(
                    student_id=row["kantinId"],
                    include_debt=bool(payload.get("includeDebt")),
                    include_daily=bool(payload.get("includeDaily")),
                    message=row["body"] or None,
                )
                if result.get("sent"):
                    status = "sent"
                    sent += 1
                    credits += int(row["segments"])
                else:
                    reason = str(result.get("reason") or "bilinmeyen")
                    failed += 1
            except Exception as failure:  # noqa: BLE001 — biri patlarsa diğerleri sürsün
                reason = str(failure)
                failed += 1
                self._log.warning("SMS gönderilemedi", student=row["kantinId"], error=reason)

            await self._store.execute(
                f"INSERT INTO {self._messages} (batch_ref, kantin_id, student_name, phone, "
                f"text, segments, status, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_ref, row["kantinId"], row["name"], row["phone"], row["text"],
                 int(row["segments"]), status, reason, _now()),
            )

        for row in preview["rows"]:
            if row["verdict"] != "skipped":
                continue
            await self._store.execute(
                f"INSERT INTO {self._messages} (batch_ref, kantin_id, student_name, phone, "
                f"text, segments, status, reason, created_at) VALUES (?, ?, ?, ?, '', 0, 'skipped', ?, ?)",
                (batch_ref, row["kantinId"], row["name"], row["phone"], row["message"], _now()),
            )

        await self._store.execute(
            f"UPDATE {self._batches} SET sent_count = ?, fail_count = ?, credits = ? "
            f"WHERE batch_ref = ?", (sent, failed, credits, batch_ref),
        )
        self._log.info("SMS gönderimi tamamlandı", batch=batch_ref, sent=sent, failed=failed)

        detail = await self.batch_detail(batch_ref)
        return {"ok": True, "sent": True, "batchRef": batch_ref, "sentCount": sent,
                "failCount": failed, "credits": credits,
                "messages": detail.get("messages", []), "summary": summary}

    # -------------------------------------------------------- hazır mesaj

    async def save_preset(self, name: str, body: str, *, actor: str) -> dict[str, Any]:
        if not body.strip():
            return {"ok": False, "error": "Boş mesaj kaydedilemez."}
        await self._store.execute(
            f"INSERT INTO {self._presets} (name, body, created_at, created_by) VALUES (?, ?, ?, ?) "
            f"ON CONFLICT(name) DO UPDATE SET body = excluded.body, "
            f"created_at = excluded.created_at, created_by = excluded.created_by",
            (name.strip(), body.strip(), _now(), actor),
        )
        return {"ok": True, "name": name.strip()}

    async def delete_preset(self, preset_id: int) -> dict[str, Any]:
        # Hazır mesaj kullanıcı kısayoludur, iş verisi değil.
        await self._store.execute(f"DELETE FROM {self._presets} WHERE id = ?", (int(preset_id),))
        return {"ok": True}

    # ------------------------------------------------------------ ayarlar

    async def update_netgsm(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Netgsm kimlik bilgileri. Parola boş bırakılırsa mevcut korunur."""
        payload: dict[str, Any] = {}
        for key in ("netgsmUsercode", "netgsmHeader", "smsPaymentConfirmedEnabled"):
            if key in changes and changes[key] is not None:
                payload[key] = changes[key]
        password = str(changes.get("netgsmPassword") or "").strip()
        if password:
            payload["netgsmPassword"] = password

        if not payload:
            return {"ok": False, "error": "Değişiklik yok."}

        try:
            result = await self._canteen.update_settings(payload)
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}
        return {"ok": True, "settings": result}
