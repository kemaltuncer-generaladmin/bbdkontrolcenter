"""Antivirüs — iş kuralları.

Servis üç şeyi yönetir: tarama koşusu, tarama geçmişi ve imza güncelliği.
Motorun kendisi `engine.py` içindedir; burası "ne zaman, hangi yolla, sonuç
nereye yazılır" sorusunu çözer.

TEK TARAMA. Aynı anda iki tarama koşmaz: iki clamdscan birbirinin G/Ç'sini
yer ve ekranda hangisinin ilerlediği anlaşılmaz. İkinci istek 409 alır.

TARAMA ÇEKİRDEĞİ BEKLETMEZ (K7). `start()` koşuyu arka plan görevine bırakıp
hemen döner; ekran `/state` ucunu yoklayarak ilerlemeyi okur.

SATIR TARAMA BİTİNCE YAZILIR. Başlangıçta "koşuyor" satırı açmak, süreç
ölürse geride sonsuza dek "koşuyor" görünen bir kayıt bırakırdı; devam eden
taramanın tek doğru evi bellektir ve ekran onu `active` alanından okur.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

from km_sdk import Trigger

from .engine import (
    STATE_READY,
    VERDICT_FAILED,
    ClamAvEngine,
    EngineNotReady,
    ScanOutcome,
    now_utc,
)

#: Zamanlayıcının gün anahtarları — `km_platform.scheduler` ile aynı sıra.
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: Zamanlanmış tam taramanın denetim izine yazılan aktörü. Kullanıcı adı
#: yazmak yanlış olurdu: bu taramayı kimse tıklamadı.
SCHEDULED_ACTOR = "Zamanlanmış tarama"

#: Motor durumu bu kadar süre önbellekte tutulur. Ekran tarama sırasında iki
#: saniyede bir yokluyor; her yoklamada clamd'e ping atmak boşuna süreç açardı.
STATUS_TTL = 15.0

#: `signatures_stale` olayı duruma GİRİŞTE yayınlanır, sonra en fazla günde
#: bir tekrarlanır. Saatlik denetimin her turunda yayınlamak, bildirim
#: kanalını kullanılamaz hâle getirirdi.
RENOTIFY_HOURS = 24.0

#: Ekrana gönderilen en fazla tehdit / atlanan yol satırı. Gerisi sayaçta.
PANEL_ROWS = 20


class ScanBusy(RuntimeError):
    """Zaten bir tarama koşuyor."""


def as_int(value: Any, fallback: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def weekdays(field: str) -> list[str]:
    """Cron'un haftanın günü alanını gün anahtarlarına çevirir. 0 ve 7 = pazar."""
    if field == "*":
        return list(DAYS)
    found: list[str] = []
    for piece in field.split(","):
        if not piece.isdigit():
            return []
        value = int(piece)
        if not 0 <= value <= 7:
            return []
        found.append(DAYS[6] if value % 7 == 0 else DAYS[value - 1])
    return sorted(set(found), key=DAYS.index)


def cron_to_triggers(expression: str, *, label: str = "") -> list[Trigger]:
    """5 alanlı cron ifadesini haftalık tetikleyicilere çevirir.

    NEDEN TAM BİR CRON AYRIŞTIRICISI YOK: zamanlayıcı yeteneği cron değil,
    HAFTALIK TABLO üzerine kurulu (`km_platform/scheduler`). Desteklenen
    biçim, sözleşmenin gerçekten karşılığı olan kesimdir: dakika ve saat sabit
    sayı, ayın günü ve ay `*`, haftanın günü `*` ya da virgüllü liste.

    Anlaşılmayan ifade sessizce yanlış bir saate kurulmaz; boş liste döner ve
    çağıran varsayılana düşerken nedenini loglar.
    """
    parts = str(expression or "").split()
    if len(parts) != 5:
        return []
    minute, hour, day_of_month, month, day_of_week = parts
    if not (minute.isdigit() and hour.isdigit()):
        return []
    if day_of_month != "*" or month != "*":
        return []
    minute_value, hour_value = int(minute), int(hour)
    if not (0 <= minute_value < 60 and 0 <= hour_value < 24):
        return []
    days = weekdays(day_of_week)
    if not days:
        return []
    stamp = f"{hour_value:02d}:{minute_value:02d}"
    return [Trigger(day=day, time=stamp, label=label) for day in days]


class AntivirusService:
    def __init__(self, *, store: Any, log: Any, config: dict[str, Any],
                 engine: ClamAvEngine, publish: Any = None) -> None:
        self._store = store
        self._log = log
        self._config = config or {}
        self._engine = engine
        self._publish = publish

        self._scan_table = store.table("scan")
        self._signature_table = store.table("signature")

        self._active: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._status_cache: tuple[float, dict[str, Any]] | None = None

    # ================================================================= ayar

    @property
    def quick_paths(self) -> list[str]:
        return as_list(self._config.get("quick_paths")) or ["~"]

    @property
    def full_paths(self) -> list[str]:
        return as_list(self._config.get("full_paths")) or ["/home"]

    @property
    def exclude_paths(self) -> list[str]:
        return as_list(self._config.get("exclude_paths"))

    @property
    def schedule(self) -> str:
        return str(self._config.get("schedule") or "0 3 * * *")

    @property
    def signature_max_age_hours(self) -> int:
        return max(1, min(720, as_int(self._config.get("signature_max_age_hours"), 48)))

    def timeout_seconds(self, kind: str) -> float:
        key = "full_timeout_minutes" if kind == "full" else "quick_timeout_minutes"
        minutes = max(1, min(1440, as_int(self._config.get(key), 240 if kind == "full" else 30)))
        return float(minutes * 60)

    def paths_for(self, kind: str) -> list[str]:
        return self.full_paths if kind == "full" else self.quick_paths

    def triggers(self) -> list[Trigger]:
        """Zamanlanmış tam taramanın haftalık tetikleyicileri."""
        found = cron_to_triggers(self.schedule, label="tam tarama")
        if found:
            return found
        self._log.warning("tarama takvimi anlaşılmadı, varsayılana dönülüyor",
                          schedule=self.schedule)
        return cron_to_triggers("0 3 * * *", label="tam tarama")

    # ================================================================ olay

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olay veri yoluna haber verir. Dinleyen yoksa da, patlarsa da iş durmaz."""
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyicinin hatası bizi düşürmez (K7)
            self._log.warning("olay yayımlanamadı", event=event, error=str(failure))

    # =============================================================== durum

    async def engine_status(self, *, fresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not fresh and self._status_cache is not None:
            stamped, cached = self._status_cache
            if now - stamped < STATUS_TTL:
                return cached
        try:
            status = await self._engine.status()
        except Exception as failure:  # noqa: BLE001 — motor sorgusu ekranı düşürmez (K7)
            self._log.error("motor durumu okunamadı", error=str(failure))
            status = {"state": "unavailable", "installed": False, "ready": False,
                      "engine": "", "daemon": False, "primary": "", "fallback": "",
                      "note": f"Motor durumu okunamadı: {failure}",
                      "database": {"ready": False, "ageHours": None}}
        self._status_cache = (now, status)
        return status

    def _active_view(self) -> dict[str, Any] | None:
        active = self._active
        if active is None:
            return None
        return {
            "kind": active["kind"],
            "startedAt": active["startedAt"],
            "files": active["files"],
            "seconds": round(time.monotonic() - active["monotonic"], 1),
            "paths": active["paths"],
            "actor": active["actor"],
            "stopping": self._stopping,
        }

    def signature_view(self, database: dict[str, Any]) -> dict[str, Any]:
        """İmza durumunun ekran karşılığı.

        YAŞ OKUNAMADIYSA "eski" DENMEZ. Bilinmeyeni eski saymak, imza dizini
        okunamayan her makinede yanlış bir uyarı üretirdi; ekran "bilinmiyor"
        der ve nedenini gösterir.
        """
        raw = database.get("ageHours")
        threshold = self.signature_max_age_hours
        hours = float(raw) if isinstance(raw, (int, float)) else None
        return {
            "ready": bool(database.get("ready")),
            "ageHours": hours,
            "updatedAt": database.get("updatedAt", ""),
            "thresholdHours": threshold,
            "stale": hours is not None and hours > threshold,
            "known": hours is not None,
            "reason": database.get("reason", ""),
            "path": database.get("path", ""),
        }

    async def state(self) -> dict[str, Any]:
        """Panelin tek okuma ucu."""
        status = await self.engine_status()
        return {
            "engine": status,
            "signatures": self.signature_view(status.get("database") or {}),
            "active": self._active_view(),
            "last": await self.last(),
            "schedule": self.schedule,
            "paths": {
                "quick": self.quick_paths,
                "full": self.full_paths,
                "exclude": self.exclude_paths,
            },
        }

    # =============================================================== tarama

    async def start(self, kind: str, *, actor: str = "") -> dict[str, Any]:
        """Taramayı başlatır ve HEMEN döner. Koşu arka plandadır."""
        wanted = "full" if str(kind).strip().lower() == "full" else "quick"

        if self._active is not None:
            raise ScanBusy("Zaten bir tarama sürüyor. Bitmesini bekleyin ya da durdurun.")

        status = await self.engine_status(fresh=True)
        if status.get("state") != STATE_READY:
            raise EngineNotReady(str(status.get("note") or "Antivirüs motoru hazır değil."))

        paths = self.paths_for(wanted)
        self._stopping = False
        self._active = {
            "kind": wanted,
            "startedAt": now_utc(),
            "monotonic": time.monotonic(),
            "files": 0,
            "paths": paths,
            "actor": actor,
        }

        await self._announce("antivirus.scan_started",
                             {"kind": wanted, "paths": paths, "actor": actor,
                              "engine": status.get("engine", ""), "at": self._active["startedAt"]})

        self._task = asyncio.get_running_loop().create_task(
            self._run(wanted, paths, actor), name=f"antivirus-scan-{wanted}")
        return {"ok": True, "started": True, "kind": wanted, "paths": paths}

    async def cancel(self) -> dict[str, Any]:
        """Çalışan taramayı durdurur. Süreç öldürülür; sonuç 'başarısız' yazılır.

        İSTEK HİÇBİR ANDA DÜŞMEZ. Üç ayrı an vardır ve üçü de kapalıdır:
        arka plan görevi henüz başlamamış olabilir (`_stopping` bayrağı
        yakalar), motor süreci henüz açmamış olabilir (motorun kendi bayrağı
        yakalar) ya da süreç koşuyordur (öldürülür). Aksi hâlde "Tara"dan
        hemen sonra "Durdur"a basan kullanıcının isteği sessizce kaybolurdu.
        """
        if self._active is None:
            return {"ok": False, "stopped": False, "detail": "Çalışan tarama yok."}
        self._stopping = True
        self._engine.stop()
        return {"ok": True, "stopped": True, "detail": "Tarama durduruluyor."}

    def _progress(self, files: int) -> None:
        if self._active is not None:
            self._active["files"] = files

    async def _run(self, kind: str, paths: list[str], actor: str) -> None:
        """Arka plan koşusu. İSTİSNA SIZDIRMAZ (K7)."""
        started_at = self._active["startedAt"] if self._active else now_utc()
        if self._stopping:
            # Durdurma isteği görev daha ilk satırını koşmadan geldi; tarayıcı
            # hiç açılmaz. `start()` ile `cancel()` arasındaki tek boşluk budur.
            outcome = ScanOutcome(verdict=VERDICT_FAILED, error="Tarama durduruldu.")
        else:
            try:
                outcome = await self._engine.scan(
                    paths,
                    timeout=self.timeout_seconds(kind),
                    exclude=self.exclude_paths,
                    on_progress=self._progress,
                )
            except Exception as failure:  # noqa: BLE001 — modül sınırı; çekirdek düşmez
                self._log.error("tarama patladı", kind=kind, error=str(failure))
                outcome = ScanOutcome(verdict=VERDICT_FAILED,
                                      error=f"Tarama patladı: {failure}")

        # DURDURMA AYRICA İŞARETLENMEZ: `stop()` süreci öldürdüğünde çıkış kodu
        # negatif olur ve motor sonucu zaten "Tarama durduruldu." hatasıyla
        # başarısız yazar. Burada bir kez daha ezmek, durdurma isteği tam
        # bitmiş bir taramanın üstüne düştüğünde doğru sonucu bozardı.
        self._active = None
        self._stopping = False
        self._status_cache = None

        try:
            await self._record(kind, paths, started_at, outcome, actor)
        except Exception as failure:  # noqa: BLE001 — kayıt hatası olayı engellemesin
            self._log.error("tarama sonucu yazılamadı", kind=kind, error=str(failure))

        payload = {"kind": kind, "startedAt": started_at, "finishedAt": now_utc(),
                   "actor": actor, **outcome.as_dict()}
        await self._announce("antivirus.scan_completed", payload)
        if outcome.threats:
            # TEK OLAY, LİSTEYLE. Bin bulaşmalı bir makinede tehdit başına
            # olay yayınlamak bildirim kanalını boğardı; dinleyenin ihtiyacı
            # "bulaşma var ve şunlar" bilgisidir.
            await self._announce("antivirus.threat_found", {
                "kind": kind, "at": payload["finishedAt"], "count": len(outcome.threats),
                "threats": outcome.threats[:PANEL_ROWS], "engine": outcome.engine,
            })
        self._log.info("tarama bitti", kind=kind, verdict=outcome.verdict,
                       files=outcome.files, threats=len(outcome.threats),
                       skipped=len(outcome.blocking))

    async def scheduled_scan(self) -> dict[str, Any]:
        """Zamanlanmış tam tarama — başlatır ve BİTMESİNİ bekler.

        Elle başlatmadan farkı budur: zamanlanmış işin çağıranı bir ekran
        değil, koşucudur; sonucu görmesi gerekir.
        """
        try:
            await self.start("full", actor=SCHEDULED_ACTOR)
        except ScanBusy as busy:
            # Bir önceki tarama hâlâ sürüyorsa üstüne binilmez.
            self._log.warning("zamanlanmış tarama atlandı", reason=str(busy))
            return {"ok": False, "started": False, "error": str(busy)}
        except EngineNotReady as failure:
            self._log.warning("zamanlanmış tarama atlandı", reason=str(failure))
            return {"ok": False, "started": False, "error": str(failure)}

        await self.wait()
        return {"ok": True, "started": True, "last": await self.last()}

    async def wait(self) -> None:
        """Süren tarama varsa bitmesini bekler.

        Zamanlanmış iş bunu kullanır: koşucunun "başladı" demesi taramanın
        yapıldığı anlamına gelmez. Arka plan görevinin hatası burada YUTULUR;
        sonuç zaten satır olarak yazılmıştır.
        """
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    # ================================================================ kayıt

    async def _record(self, kind: str, paths: list[str], started_at: str,
                      outcome: ScanOutcome, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._scan_table} "
            "(kind, engine, started_at, finished_at, seconds, files, threat_count, "
            " skipped_count, verdict, error, actor, paths, threats, skipped) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                kind, outcome.engine, started_at, now_utc(), round(outcome.seconds, 2),
                outcome.files, len(outcome.threats), len(outcome.blocking),
                outcome.verdict, outcome.error, actor,
                json.dumps(paths, ensure_ascii=False),
                json.dumps(outcome.threats, ensure_ascii=False),
                json.dumps(outcome.skipped, ensure_ascii=False),
            ),
        )

    async def last(self) -> dict[str, Any] | None:
        """Son biten tarama. Hiç tarama yapılmadıysa None."""
        row = await self._store.fetch_one(
            f"SELECT * FROM {self._scan_table} ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None

        threats = _loads(row.get("threats"))
        skipped = _loads(row.get("skipped"))
        blocking = [entry for entry in skipped if isinstance(entry, dict) and entry.get("blocking")]
        return {
            "id": row.get("id"),
            "kind": row.get("kind", ""),
            "engine": row.get("engine", ""),
            "startedAt": row.get("started_at", ""),
            "finishedAt": row.get("finished_at", ""),
            "seconds": row.get("seconds", 0),
            "files": row.get("files", 0),
            "verdict": row.get("verdict", VERDICT_FAILED),
            "error": row.get("error", ""),
            "actor": row.get("actor", ""),
            "paths": _loads(row.get("paths")),
            "threatCount": int(row.get("threat_count") or 0),
            "threats": threats[:PANEL_ROWS],
            "skippedCount": int(row.get("skipped_count") or 0),
            "excludedCount": max(0, len(skipped) - len(blocking)),
            "skipped": skipped[:PANEL_ROWS],
        }

    # ================================================================ imza

    async def check_signatures(self) -> dict[str, Any]:
        """İmza yaşını okur; eşiği aşarsa olay yayınlar.

        FRESHCLAM BURADAN ÇALIŞTIRILMAZ (ADR 0009 §5): servis zaten güncelliyor
        ve elle çalıştırmak kilit çakışması yaratır. Bu iş yalnızca OKUR.
        """
        database = self._engine.database()
        view = self.signature_view(database)
        previous = await self._store.fetch_one(
            f"SELECT * FROM {self._signature_table} WHERE id = 1"
        )

        notify = view["stale"] and self._should_notify(previous)
        checked_at = now_utc()
        notified_at = checked_at if notify else str((previous or {}).get("notified_at") or "")

        await self._store.execute(
            f"INSERT INTO {self._signature_table} (id, checked_at, age_hours, stale, notified_at) "
            "VALUES (1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET checked_at = excluded.checked_at, "
            "age_hours = excluded.age_hours, stale = excluded.stale, "
            "notified_at = excluded.notified_at",
            (checked_at, view["ageHours"], 1 if view["stale"] else 0, notified_at),
        )

        if notify:
            await self._announce("antivirus.signatures_stale", {
                "ageHours": view["ageHours"],
                "thresholdHours": view["thresholdHours"],
                "updatedAt": view["updatedAt"],
                "at": checked_at,
            })
            self._log.warning("imzalar eskimiş", ageHours=view["ageHours"],
                              thresholdHours=view["thresholdHours"])

        return {"ok": True, "notified": notify, "checkedAt": checked_at, **view}

    def _should_notify(self, previous: dict[str, Any] | None) -> bool:
        """Duruma girişte yayınla; sonra en fazla günde bir tekrarla."""
        if previous is None or not previous.get("stale"):
            return True
        stamp = str(previous.get("notified_at") or "")
        if not stamp:
            return True
        try:
            last = datetime.fromisoformat(stamp)
        except ValueError:
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return (datetime.now(UTC) - last).total_seconds() >= RENOTIFY_HOURS * 3600


def _loads(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []
