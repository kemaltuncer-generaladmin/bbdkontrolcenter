"""Kontrol Merkezi hız ölçümü — BBD Akademi PERFORMANCE.md §4 (A14).

Gerçek uygulamayı (`create_app`, bütün modüller, gerçek depo) süreç içinde
ayağa kaldırır, §1 hacmini tohumlar ve sıcak yolları N kez çağırır. Her yol
için p50/p95 süre, istek başına veritabanı ifadesi ve kantine giden istek /
açılan bağlantı sayısı yazılır. Önce/sonra tablosu bu çıktıdan kurulur.

CANLI SİSTEME ÇIKMAZ. Kantin, aynı süreçte koşan sahte bir HTTPS sunucusudur
(kendinden imzalı sertifika, `127.0.0.1`); gerçek SMS, gerçek tablet yoktur.

Kullanım (depo kökünden):

    .venv/bin/python tools/perf/km_hiz.py seed    --db /tmp/km-hiz.sqlite
    .venv/bin/python tools/perf/km_hiz.py measure --db /tmp/km-hiz.sqlite --out once.json

PostgreSQL (yerel, işlemsiz `CREATE INDEX CONCURRENTLY` dahil) için aynı
komutlar ortam değişkenleriyle koşar; `--db` yalnız yardımcı dosyaların
(`.meta.json`) yeridir:

    KM_STORE_ENGINE=postgres KM_CENTRAL_DSN=postgresql://km@127.0.0.1:55432/km_perf \\
        .venv/bin/python tools/perf/km_hiz.py seed --db /tmp/km-hiz-pg

Seçenekler: `--scale` (hacim çarpanı, 1.0 = §1), `-n` (yol başına çağrı),
`--kantin-ms` (sahte kantinin istek başına gecikmesi; ağ + kantin işi),
`--kantin-filtre` (kantin `studentId` süzgecini tanıyormuş gibi davranır),
`--only` (yalnız adı bu önekle başlayan yollar).

Hacim (scale=1.0): 2.000 öğrenci / 3.500 veli (kantin), 1.000.000 denetim
satırı, 100.000 tablet oturumu, 400.000 kullanım paketi + 1.200.000 kalem,
500.000 günlük kullanım satırı, 600.000 SMS, 200.000 ödeme talebi kaydı,
50.000 tahsilat; geçit yüklüyse 500.000 geçit denetim satırı.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / "backend" / "src", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

PIN = "482913"
STUDENTS = 2000
NOW = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)
DAY_MS = 86_400_000


def student_id(index: int) -> str:
    return "STU-" + hashlib.sha1(f"ogrenci-{index}".encode()).hexdigest()[:10]


def phone(seed: int) -> str:
    return "5" + str(300000000 + (seed * 7919) % 699999999).zfill(9)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


# ===================================================================== kantin


class FakeKantin:
    """Kantin API'sinin ölçüme yeten kopyası (raw ASGI).

    `filter_students=False` bugünkü kantindir: `studentId` süzgecini tanımaz,
    `GET /api/transactions` aralıkta 5.000'e kadar satır, `GET
    /api/payment-requests` kurum geneli son 100 talebi döner. True ise
    süzgeci sunucuda uygular (kantin-veli dalının yeni sürümü).
    """

    def __init__(self, *, delay_ms: float, filter_students: bool) -> None:
        self.delay = delay_ms / 1000.0
        self.filter_students = filter_students
        self.requests = 0
        self.connections: set[tuple[str, int]] = set()
        rng = random.Random(7)
        self.students: list[dict[str, Any]] = []
        for index in range(STUDENTS):
            sid = student_id(index)
            balance = rng.choice([0, 0, 0, 1500, 4250, 12000, -500])
            self.students.append({
                "id": sid, "displayName": f"Öğrenci {index} Soyad{index % 97}",
                "parentPhone": phone(index), "balance": balance,
                "spendingLimit": rng.choice([None, 20000, 50000]),
                "isBlocked": index % 150 == 0, "dailyLimit": rng.choice([None, 15000]),
                "guardianBlocked": index % 400 == 0, "restrictedProductIds": [],
                "controlsUpdatedAt": None, "accessCode": str(100000 + index),
                "updatedAt": epoch_ms(NOW - timedelta(minutes=index * 7)),
            })
        self.by_id = {row["id"]: row for row in self.students}
        # Son 30 günün satışları (~820/gün: 1,5 M satış / 5 yıl).
        self.sales: list[dict[str, Any]] = []
        start = epoch_ms(NOW - timedelta(days=30))
        for number in range(24_600):
            index = rng.randrange(STUDENTS)
            at = start + int(number * (30 * DAY_MS / 24_600))
            items = [{"productId": 1 + rng.randrange(120), "name": "Tost", "qty": 1,
                      "unitPrice": 2500, "lineTotal": 2500} for _ in range(1 + number % 3)]
            self.sales.append({"serverId": f"tx-{number}", "localId": f"l-{number}",
                               "studentId": student_id(index),
                               "studentName": f"Öğrenci {index}",
                               "total": 2500 * len(items), "createdAt": at,
                               "reversedAt": None, "reversedReason": None, "items": items})
        self.sales.reverse()  # en yeni önce (kantin sırası)
        self.products = [{"id": n, "barcode": f"869{n:05d}", "name": f"Ürün {n}",
                          "imageUrl": None, "price": 500 + n * 25, "stock": 40,
                          "isActive": n % 9 != 0, "updatedAt": 1} for n in range(1, 121)]

    def payment_requests(self, wanted: str | None) -> list[dict[str, Any]]:
        def row(number: int, sid: str) -> dict[str, Any]:
            created = NOW - timedelta(hours=number * 3)
            return {"ref": f"PR-{number:08d}", "studentId": sid, "studentName": "x",
                    "amount": 4250, "status": "PENDING" if number % 9 == 0 else "PAID",
                    "amountFixed": False, "linkUrl": f"https://ode/p/{number}",
                    "payToken": f"tok.{number}", "origin": "admin",
                    "createdAt": epoch_ms(created), "updatedAt": iso(created),
                    "paidAt": None}
        if wanted and self.filter_students:
            return [row(number, wanted) for number in range(12)]
        return [row(number, student_id(number % STUDENTS)) for number in range(100)]

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
            return
        if scope["type"] != "http":
            return
        self.requests += 1
        client = scope.get("client")
        if client:
            self.connections.add((str(client[0]), int(client[1])))
        while True:  # gövdeyi tüket
            message = await receive()
            if not message.get("more_body"):
                break
        if self.delay:
            await asyncio.sleep(self.delay)
        status, body = self.route(scope["method"], scope["path"],
                                  parse_qs(scope.get("query_string", b"").decode()))
        payload = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(payload)).encode())]})
        await send({"type": "http.response.body", "body": payload})

    def route(self, method: str, path: str,
              query: dict[str, list[str]]) -> tuple[int, Any]:
        q = {key: values[-1] for key, values in query.items()}
        wanted = q.get("studentId")
        if path == "/api/students" and method == "GET":
            return 200, {"data": self.students}
        if path.startswith("/api/students/") and method == "GET":
            sid = path.rsplit("/", 1)[-1]
            row = self.by_id.get(sid)
            if row is None:
                return 404, {"message": "yok", "reason": "student_not_found"}
            return 200, {"data": {**row, "spentToday": 2500}}
        if path == "/api/transactions":
            low, high = int(q.get("from", 0)), int(q.get("to", 2**62))
            limit = min(int(q.get("limit", 100)), 5000)
            rows = [row for row in self.sales if low <= row["createdAt"] < high]
            if wanted and self.filter_students:
                rows = [row for row in rows if row["studentId"] == wanted]
            return 200, {"data": rows[:limit]}
        if path == "/api/payment-requests" and method == "GET":
            return 200, {"data": self.payment_requests(wanted)}
        if path == "/api/reports/collections":
            entries = [{"studentId": student_id(n % STUDENTS), "amount": 5000,
                        "createdAt": iso(NOW - timedelta(days=n % 30)), "method": "CASH"}
                       for n in range(400)]
            if wanted:
                entries = [row for row in entries if row["studentId"] == wanted]
            return 200, {"data": {"entries": entries, "total": 5000 * len(entries)}}
        if path == "/api/products":
            return 200, {"data": self.products}
        if path == "/api/settings":
            return 200, {"data": {"allowPrepayment": False, "maxPrepayment": 0,
                                  "smsTemplates": {"payment_request": "x {link}"}}}
        if path == "/api/reports/dashboard":
            return 200, {"openReceivables": 1, "todaySales": 2, "pendingRequests": 3,
                         "blockedStudents": 4, "totalStudents": STUDENTS}
        if path == "/api/device/qr-key":
            return 200, {"qrKey": base64.b64encode(b"k" * 32).decode()}
        if path == "/api/notifications":
            return 200, {"data": [], "nextSince": None, "hasMore": False}
        return 404, {"message": "yok"}


@contextlib.contextmanager
def kantin_server(app: FakeKantin, workdir: Path) -> Any:
    """Sahte kantini HTTPS ile ayrı iş parçacığında koşturur."""
    import uvicorn

    cert, key = workdir / "kantin.crt", workdir / "kantin.key"
    if not cert.exists():
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
             "-keyout", str(key), "-out", str(cert)],
            check=True, capture_output=True,
        )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error",
                            ssl_certfile=str(cert), ssl_keyfile=str(key), lifespan="on",
                            timeout_keep_alive=30)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("sahte kantin açılmadı")
        time.sleep(0.05)
    os.environ["SSL_CERT_FILE"] = str(cert)
    try:
        yield f"https://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# ===================================================================== depo


def engine() -> str:
    return os.environ.get("KM_STORE_ENGINE", "sqlite").strip().lower() or "sqlite"


async def open_store(db: Path) -> Any:
    from km_core.store.engine import create_store

    store = create_store(db)
    await store.open()
    return store


async def bulk(store: Any, table: str, columns: Sequence[str],
               rows: Iterable[Sequence[Any]], *, chunk: int = 20_000) -> int:
    """Toplu ekleme: SQLite'ta `executemany`, PostgreSQL'de COPY."""
    total = 0
    batch: list[Sequence[Any]] = []

    async def flush() -> None:
        nonlocal total
        if not batch:
            return
        if engine() == "postgres":
            async with store.pool.acquire() as connection:
                await connection.copy_records_to_table(table, records=batch, columns=list(columns))
        else:
            marks = ", ".join("?" for _ in columns)
            await store.db.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", batch)
            await store.db.commit()
        total += len(batch)
        batch.clear()

    for row in rows:
        batch.append(row)
        if len(batch) >= chunk:
            await flush()
    await flush()
    return total


async def seed(args: argparse.Namespace) -> None:
    from km_core.security.identity import Identity
    from km_core.security.permissions import CORE_PERMISSIONS
    from km_core.security.service_keys import ServiceKeys
    from km_core.store.bootstrap import build_schema

    db = Path(args.db)
    if engine() == "sqlite":
        for suffix in ("", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                Path(str(db) + suffix).unlink()
    else:
        import asyncpg

        connection = await asyncpg.connect(os.environ["KM_CENTRAL_DSN"])
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await connection.close()

    scale = float(args.scale)
    rng = random.Random(11)
    started = time.perf_counter()
    store = await open_store(db)
    await build_schema(store, ROOT / "modules")
    identity = Identity(store, pepper="hiz-olcumu")
    await identity.ensure_builtin_roles()
    await identity.grant_defaults(CORE_PERMISSIONS)
    admin = await identity.create_user(first_name="Ölçüm", last_name="Yönetici",
                                       org_scope="bbd", password=PIN, roles=["admin"])
    service_user = await identity.create_service_user(name="BBD Akademi API",
                                                      roles=["mobile_service"])
    key = await ServiceKeys(identity).create(user_id=service_user, name="ölçüm")

    def n(base: int) -> int:
        return max(1, int(base * scale))

    counts: dict[str, int] = {}
    actions = ["auth.login", "users.update", "permission.denied", "auth.service_key",
               "bbd_students.update", "settings.update"]
    counts["audit_log"] = await bulk(store, "audit_log",
        ("at", "user_id", "action", "scope", "result", "detail"),
        ((iso(NOW - timedelta(seconds=i * 30)), admin if i % 3 else service_user,
          actions[i % len(actions)], None, "ok" if i % 17 else "fail", f"satır {i}")
         for i in range(n(1_000_000))))

    profiles = "mod_bbd_students_profile"
    counts[profiles] = await bulk(store, profiles,
        ("kantin_id", "first_name", "last_name", "class_name", "school_no", "student_phone",
         "parent_name", "parent_name2", "parent_phone2", "note", "updated_at", "grade_level",
         "track", "exam_target"),
        ((student_id(i), f"Ad{i}", f"Soyad{i}", f"{5 + i % 8}-{'ABCD'[i % 4]}", str(1000 + i),
          phone(i + 50_000) if i % 3 else "", f"Veli {i}", f"Veli2 {i}" if i % 4 == 0 else "",
          phone(i + 90_000) if i % 4 == 0 else "", "",
          iso(NOW - timedelta(hours=i)), str(5 + i % 8), None, "LGS" if i % 8 == 3 else None)
         for i in range(STUDENTS)))

    # ---------------------------------------------------------------- tablet
    t = "mod_bbd_tablet_"
    profile_ids = [f"prof-{i}" for i in range(3)]
    stamp = iso(NOW)
    await bulk(store, t + "profiles", ("id", "name", "revision", "timezone", "created_at",
                                       "updated_at", "usage_windows"),
               [(pid, f"Profil {i}", 1, "Europe/Istanbul", stamp, stamp, "[]")
                for i, pid in enumerate(profile_ids)])
    packages = [f"com.uygulama.p{i}" for i in range(15)]
    await bulk(store, t + "profile_apps", ("profile_id", "revision", "package_name", "app_name",
                                           "allowed", "unlimited", "daily_limit_seconds",
                                           "sort_order"),
               [(pid, 1, package, f"Uygulama {j}", 1, j % 3 == 0, 3600, j)
                for pid in profile_ids for j, package in enumerate(packages)])
    device_ids = [f"dev-{i:02d}" for i in range(30)]
    await bulk(store, t + "devices", ("id", "uuid", "name", "profile_id", "token_hash",
                                      "last_seen_at", "created_at", "next_sync_seconds",
                                      "battery_percent", "charging", "foreground_package"),
               [(did, str(uuid.uuid4()), f"Tablet {i:02d}", profile_ids[i % 3], f"h{i}",
                 iso(NOW - timedelta(seconds=20)), stamp, 60, 50 + i, i % 2,
                 packages[i % 15]) for i, did in enumerate(device_ids)])
    await bulk(store, t + "inventory", ("device_id", "package_name", "app_label",
                                        "is_launchable"),
               [(did, f"com.envanter.a{j}", f"Envanter {j}", 1)
                for did in device_ids for j in range(60)])
    session_count = n(100_000)
    retention_start = NOW - timedelta(days=170)

    def sessions() -> Iterable[Sequence[Any]]:
        for i in range(session_count):
            login = retention_start + timedelta(seconds=i * (170 * 86400 / session_count))
            active = i >= session_count - 12
            yield (f"ses-{i}", student_id(rng.randrange(STUDENTS)), f"Öğrenci {i % STUDENTS}",
                   device_ids[i % 30] if not active else device_ids[i - (session_count - 12)],
                   f"st{i}", iso(login),
                   iso(NOW - timedelta(seconds=30)) if active else iso(login + timedelta(minutes=40)),
                   None if active else iso(login + timedelta(minutes=40)),
                   "active" if active else "logged_out", "logout" if not active else None,
                   profile_ids[i % 3])
    counts[t + "sessions"] = await bulk(store, t + "sessions",
        ("id", "student_id", "student_name", "device_id", "token_hash", "login_at",
         "last_heartbeat_at", "ended_at", "state", "end_reason", "profile_id"), sessions())

    batch_count = n(400_000)
    counts[t + "usage_batches"] = await bulk(store, t + "usage_batches",
        ("id", "device_id", "session_id", "student_id", "started_at", "ended_at", "accepted_at"),
        ((f"b-{i}", device_ids[i % 30], f"ses-{i // 4}", student_id(i % STUDENTS),
          iso(retention_start + timedelta(seconds=i * 36)),
          iso(retention_start + timedelta(seconds=i * 36 + 300)),
          iso(retention_start + timedelta(seconds=i * 36 + 301))) for i in range(batch_count)))
    counts[t + "usage_batch_items"] = await bulk(store, t + "usage_batch_items",
        ("batch_id", "package_name", "local_date", "used_seconds"),
        ((f"b-{i // 3}", packages[i % 15],
          (retention_start + timedelta(seconds=(i // 3) * 36)).date().isoformat(), 100)
         for i in range(batch_count * 3)))

    def daily() -> Iterable[Sequence[Any]]:
        target = n(500_000)
        made = 0
        for day in range(170):
            local = (NOW - timedelta(days=day)).date().isoformat()
            for index in range(STUDENTS):
                for package in packages[: 1 + (index + day) % 3]:
                    if made >= target:
                        return
                    if (index * 31 + day) % 4:
                        continue
                    made += 1
                    yield (student_id(index), package, local, 60 + (index + day) % 3000)
    counts[t + "daily_usage"] = await bulk(store, t + "daily_usage",
        ("student_id", "package_name", "local_date", "used_seconds"), daily())
    counts[t + "grants"] = await bulk(store, t + "grants",
        ("id", "student_id", "package_name", "local_date", "extra_seconds", "reason",
         "created_by", "created_at"),
        ((f"g-{i}", student_id(i % STUDENTS), packages[i % 15],
          (NOW - timedelta(days=i % 170)).date().isoformat(), 600, "ödev", "ölçüm", stamp)
         for i in range(n(20_000))))
    counts[t + "student_blocks"] = await bulk(store, t + "student_blocks",
        ("id", "student_id", "blocked_until", "reason", "created_by", "created_at"),
        ((f"k-{i}", student_id(i % STUDENTS), iso(NOW + timedelta(days=(i % 5) - 3)),
          "kural", "ölçüm", stamp) for i in range(n(2_000))))
    counts[t + "commands"] = await bulk(store, t + "commands",
        ("id", "device_id", "type", "created_at", "expires_at", "acked_at"),
        ((f"c-{i}", device_ids[i % 30], "lock", iso(NOW - timedelta(minutes=i)),
          iso(NOW - timedelta(minutes=i) + timedelta(hours=1)),
          None if i < 20 else iso(NOW - timedelta(minutes=i - 1))) for i in range(n(20_000))))

    # ---------------------------------------------------------------- SMS
    s = "mod_bbd_sms_"
    sms_batches = n(2_000)
    counts[s + "batch"] = await bulk(store, s + "batch",
        ("batch_ref", "title", "body", "encoding", "segments", "total_count", "sent_count",
         "created_at", "created_by"),
        ((f"SMS-{i:06d}", f"Duyuru {i}", "Sayın veli {ad}", "GSM-7", 1, 300, 300,
          iso(NOW - timedelta(hours=i * 2)), "ölçüm") for i in range(sms_batches)))
    counts[s + "message"] = await bulk(store, s + "message",
        ("batch_ref", "kantin_id", "student_name", "class_name", "phone", "text", "segments",
         "status", "created_at"),
        ((f"SMS-{i % sms_batches:06d}", student_id(i % STUDENTS), f"Öğrenci {i % STUDENTS}",
          "8-A", phone(i), "Sayın veli, kantin borcunuz 42,50 TL.", 1,
          "sent" if i % 20 else "failed", iso(NOW - timedelta(minutes=i)))
         for i in range(n(600_000))))

    # ------------------------------------------------------- ödeme talebi
    p = "mod_bbd_payment_request_"
    pr_batches = n(500)
    await bulk(store, p + "batch", ("batch_ref", "note", "total_count", "ok_count",
                                    "created_at", "created_by"),
               [(f"PRB-{i:05d}", "toplu", 400, 390, iso(NOW - timedelta(days=i)), "ölçüm")
                for i in range(pr_batches)])
    counts[p + "entry"] = await bulk(store, p + "entry",
        ("batch_ref", "kantin_id", "student_name", "phone", "amount", "ref", "status",
         "reason", "created_at"),
        ((f"PRB-{i % pr_batches:05d}", student_id(i % STUDENTS), f"Öğrenci {i % STUDENTS}",
          phone(i), 4250, f"PR-{i:08d}", "queued" if i % 10 else "failed", "",
          iso(NOW - timedelta(minutes=i * 3))) for i in range(n(200_000))))
    counts[p + "collection"] = await bulk(store, p + "collection",
        ("local_id", "kantin_id", "student_name", "amount", "balance_after", "status",
         "created_at", "created_by"),
        ((f"col-{i}", student_id(i % STUDENTS), f"Öğrenci {i % STUDENTS}", 5000, 0, "ok",
          iso(NOW - timedelta(minutes=i * 10)), "ölçüm") for i in range(n(50_000))))

    # ---------------------------------------------------------------- zil
    days = ["mon", "tue", "wed", "thu", "fri"]
    await bulk(store, "mod_bell_time", ("id", "day", "time", "label", "kind"),
               [(f"t-{d}-{h}", d, f"{8 + h // 2:02d}:{(h % 2) * 30:02d}", f"{h}. ders",
                 "ders" if h % 2 == 0 else "teneffus") for d in days for h in range(16)])
    await bulk(store, "mod_bell_group", ("id", "name", "sort", "created_at", "deleted_at",
                                         "kind"),
               [(f"grp-{i}", f"Grup {i}", i, stamp, "", "grup") for i in range(5)])

    # --------------------------------------------------------- mobil geçit
    if (ROOT / "modules" / "mobile_gateway").is_dir():
        g = "mod_mobile_gateway_"
        base = epoch_ms(NOW)
        counts[g + "audit"] = await bulk(store, g + "audit",
            ("created_at", "action", "student_id", "user_id", "on_behalf_of", "request_id",
             "result", "detail"),
            ((base - i * 60_000, "canteen.controls.update", student_id(i % STUDENTS),
              service_user, "akademi:staff:1", f"r-{i}", "ok", "{}")
             for i in range(n(500_000))))
        counts[g + "hits"] = await bulk(store, g + "hits", ("bucket", "subject", "at"),
            (("auth_phone_fail", hashlib.sha256(str(i % 3000).encode()).hexdigest(),
              base - i * 1000) for i in range(n(50_000))))

    meta = {"token": key.token, "adminPin": PIN, "engine": engine(), "scale": scale,
            "counts": counts, "seededAt": iso(datetime.now(UTC)),
            "seconds": round(time.perf_counter() - started, 1)}
    if engine() == "postgres":
        async with store.pool.acquire() as connection:
            await connection.execute("ANALYZE")
    else:
        await store.db.execute("ANALYZE")
        await store.db.commit()
    await store.close()
    meta_path(db).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


def meta_path(db: Path) -> Path:
    return Path(str(db) + ".meta.json")


# ===================================================================== ölçüm


class Probe:
    """Depo ifadelerini sayar; isteğe bağlı olarak ifade başına ağ gecikmesi ekler."""

    def __init__(self, store: Any, rtt_ms: float) -> None:
        self.statements = 0
        self.rtt = rtt_ms / 1000.0
        for name in ("fetch_all", "fetch_one", "execute", "execute_many"):
            original = getattr(store, name)
            setattr(store, name, self._wrap(original))

    def _wrap(self, original: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def counted(*args: Any, **kwargs: Any) -> Any:
            self.statements += 1
            if self.rtt:
                await asyncio.sleep(self.rtt)
            return await original(*args, **kwargs)

        return counted


def summary(samples: list[float], statements: list[int], upstream: list[int],
            connections: int) -> dict[str, Any]:
    ordered = sorted(samples)

    def pct(value: float) -> float:
        if not ordered:
            return 0.0
        index = min(len(ordered) - 1, max(0, round(value * (len(ordered) - 1))))
        return round(ordered[index], 2)

    return {
        "n": len(samples), "p50": pct(0.50), "p95": pct(0.95), "max": round(max(ordered), 2),
        "mean": round(statistics.fmean(ordered), 2),
        "queries": round(statistics.fmean(statements), 2) if statements else 0,
        "queriesMax": max(statements) if statements else 0,
        "kantinCalls": round(statistics.fmean(upstream), 2) if upstream else 0,
        "connections": connections,
    }


async def run(name: str, action: Callable[[int], Awaitable[Any]], *, probe: Probe,
              kantin: FakeKantin, n: int, warmup: int = 2) -> dict[str, Any]:
    for index in range(warmup):
        await action(index)
    samples: list[float] = []
    statements: list[int] = []
    upstream: list[int] = []
    connections_before = len(kantin.connections)
    for index in range(n):
        queries, calls = probe.statements, kantin.requests
        started = time.perf_counter()
        await action(index)
        samples.append((time.perf_counter() - started) * 1000)
        statements.append(probe.statements - queries)
        upstream.append(kantin.requests - calls)
    result = summary(samples, statements, upstream,
                     len(kantin.connections) - connections_before)
    result["name"] = name
    print(f"  {name:<44} p50 {result['p50']:>8.2f}  p95 {result['p95']:>8.2f} ms  "
          f"sorgu {result['queries']:>6}  kantin {result['kantinCalls']:>5}  "
          f"bağlantı {result['connections']}", flush=True)
    return result


async def measure(args: argparse.Namespace) -> None:
    import httpx

    from km_core.config.loader import ROOT as CONFIG_ROOT
    from km_core.config.loader import Config, load_config
    from km_core.http.app import create_app

    db = Path(args.db)
    meta = json.loads(meta_path(db).read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="km-hiz-"))
    kantin = FakeKantin(delay_ms=float(args.kantin_ms), filter_students=bool(args.kantin_filtre))
    results: list[dict[str, Any]] = []

    with kantin_server(kantin, work) as kantin_url:
        data = deepcopy(load_config().as_dict())
        data["core"] = {**data.get("core", {}), "store_path": str(db),
                        "secret_key_path": str(db) + ".secret.key"}
        data["auth"] = {**data.get("auth", {}), "bootstrap_pin": PIN,
                        "service_key_requests_per_minute": 1_000_000}
        modules = data.setdefault("modules", {})
        modules["bbd_canteen_api"] = {**modules.get("bbd_canteen_api", {}),
                                      "base_url": kantin_url}
        app = create_app(Config(data, root=CONFIG_ROOT))
        async with app.router.lifespan_context(app):
            state = app.state
            # Ölçüm tohumundaki pepper'la PIN'i yeniden bağla: kasa bu süreçte yeni
            # anahtar üretmiş olabilir; kişi oturumu için yöneticinin sırrını yenile.
            identity = state.identity
            admin_row = await state.store.fetch_one(
                "SELECT id FROM users WHERE kind = 'person' AND first_name = ?", ("Ölçüm",))
            await identity.set_password(str(admin_row["id"]), "739164")
            vault = state.registry.resolve("secrets")
            await vault.set("canteen.device_token", "olcum-token")
            probe = Probe(state.store, float(args.db_rtt_ms))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://km") as http:
                login = await http.post("/api/auth/login", json={"password": "739164"})
                login.raise_for_status()
                person = {"Authorization": f"Bearer {login.json()['token']}"}
                machine = {"Authorization": f"Bearer {meta['token']}",
                           "X-KM-On-Behalf-Of": "akademi:staff:42:Ay%C5%9Fe",
                           "X-Forwarded-For": "10.0.0.5"}
                rng = random.Random(3)
                ids = [student_id(rng.randrange(STUDENTS)) for _ in range(4096)]

                async def get(url: str, headers: dict[str, str],
                              expect: tuple[int, ...] = (200,)) -> httpx.Response:
                    response = await http.get(url, headers=headers)
                    if response.status_code not in expect:
                        raise RuntimeError(f"{url} → {response.status_code}: "
                                           f"{response.text[:300]}")
                    return response

                scenarios = build_scenarios(state, get, person, machine, ids, meta)
                for name, action in scenarios:
                    if args.only and not any(name.startswith(prefix) for prefix in args.only):
                        continue
                    try:
                        results.append(await run(name, action, probe=probe, kantin=kantin,
                                                 n=int(args.n)))
                    except Exception as error:  # noqa: BLE001 — bir yol düşerse diğerleri sürsün
                        print(f"  {name:<44} HATA: {error}", flush=True)
                        results.append({"name": name, "error": str(error)})

    report = {"engine": meta["engine"], "scale": meta["scale"], "counts": meta["counts"],
              "n": int(args.n), "kantinMs": float(args.kantin_ms),
              "kantinFilter": bool(args.kantin_filtre), "dbRttMs": float(args.db_rtt_ms),
              "measuredAt": iso(datetime.now(UTC)),
              "commit": _git_head(), "results": results}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


def build_scenarios(state: Any, get: Callable[..., Awaitable[Any]], person: dict[str, str],
                    machine: dict[str, str], ids: list[str],
                    meta: dict[str, Any]) -> list[tuple[str, Callable[[int], Awaitable[Any]]]]:
    registry = state.registry
    identity = state.identity
    token = meta["token"]
    scenarios: list[tuple[str, Callable[[int], Awaitable[Any]]]] = []

    def add(name: str, action: Callable[[int], Awaitable[Any]]) -> None:
        scenarios.append((name, action))

    # ------------------------------------------------------------ çekirdek
    async def service_key(_: int) -> None:
        verdict = await state.service_keys.verify(token, client_ip="10.0.0.5")
        assert verdict.user is not None, verdict.reason

    add("cekirdek.servis_anahtari.dogrula", service_key)

    session_token = person["Authorization"][7:]

    async def session(_: int) -> None:
        assert await identity.resolve_session(session_token) is not None

    add("cekirdek.oturum.coz", session)

    async def audit(index: int) -> None:
        await identity.audit(None, "olcum.yaz", result="ok", detail=f"{index}")

    add("cekirdek.denetim.yaz", audit)

    # ------------------------------------------------------------- öğrenci
    directory = registry.try_resolve("bbd_students.directory")
    if directory is not None:
        since = NOW - timedelta(hours=6)

        async def dir_full(_: int) -> None:
            listing = await directory.list()
            assert len(listing["students"]) == STUDENTS

        async def dir_full_cold(_: int) -> None:
            forget = getattr(directory, "invalidate", None)
            if callable(forget):
                forget()
            else:
                directory._cache = None
            await directory.list()

        async def dir_since(_: int) -> None:
            await directory.list(since=since)

        async def dir_get(index: int) -> None:
            assert await directory.get(ids[index % len(ids)]) is not None

        async def dir_phone(index: int) -> None:
            await directory.find_by_phone(phone(index % STUDENTS))

        add("ogrenci.dizin.tam", dir_full)
        add("ogrenci.dizin.tam.soguk", dir_full_cold)
        add("ogrenci.dizin.since", dir_since)
        add("ogrenci.dizin.get", dir_get)
        add("ogrenci.dizin.telefon", dir_phone)

    async def students_panel(_: int) -> None:
        await get("/api/bbd_students/students", person)

    add("ogrenci.panel.liste", students_panel)

    canteen = registry.try_resolve("canteen.api")
    if canteen is not None:
        async def canteen_get(index: int) -> None:
            await canteen.get_student(ids[index % len(ids)])

        add("kantin.istemci.get_student", canteen_get)

    # -------------------------------------------------------------- tablet
    tablet = registry.try_resolve("bbd_tablet.read")
    if tablet is not None:
        async def board(_: int) -> None:
            await tablet.board()

        async def usage(index: int) -> None:
            end = NOW.date()
            await tablet.student_usage(ids[index % len(ids)], end - timedelta(days=29), end)

        add("tablet.pano", board)
        add("tablet.kullanim.30gun", usage)

    async def tablet_overview(_: int) -> None:
        await get("/api/bbd_tablet/overview", person)

    async def tablet_sessions(index: int) -> None:
        await get(f"/api/bbd_tablet/sessions?studentId={ids[index % len(ids)]}", person)

    add("tablet.panel.overview", tablet_overview)
    add("tablet.panel.ogrenci_oturumlari", tablet_sessions)

    # ----------------------------------------------------------------- zil
    week = registry.try_resolve("bell.week")
    if week is not None:
        async def bell_week(_: int) -> None:
            await week()

        add("zil.hafta", bell_week)

    # --------------------------------------------------------- ödeme / SMS
    async def payment_workspace(_: int) -> None:
        await get("/api/bbd_payment_request/workspace", person)

    async def payment_student(index: int) -> None:
        await get(f"/api/bbd_students/students/{ids[index % len(ids)]}/canteen", person)

    async def sms_history(_: int) -> None:
        await get("/api/bbd_sms/history", person)

    add("odeme_talebi.workspace", payment_workspace)
    add("ogrenci.panel.kantin_karti", payment_student)
    add("sms.gecmis", sms_history)

    # --------------------------------------------------------- mobil geçit
    if registry.try_resolve("bbd_students.directory") is not None and \
            (ROOT / "modules" / "mobile_gateway").is_dir():
        v1 = "/api/mobile_gateway/v1"
        etag: dict[str, str] = {}

        async def gw(path: str, expect: tuple[int, ...] = (200,),
                     extra: dict[str, str] | None = None) -> Any:
            return await get(f"{v1}{path}", {**machine, **(extra or {})}, expect)

        async def gw_dir(_: int) -> None:
            response = await gw("/directory")
            etag["v"] = response.headers.get("etag", "")

        async def gw_dir_304(_: int) -> None:
            await gw("/directory", (304, 200), {"If-None-Match": etag.get("v", "")})

        async def gw_since(_: int) -> None:
            await gw("/directory?since=" + (NOW - timedelta(hours=6)).isoformat()
                     .replace("+", "%2B"))

        add("gecit.settings_public", lambda _: gw("/settings/public"))
        add("gecit.directory.tam", gw_dir)
        add("gecit.directory.304", gw_dir_304)
        add("gecit.directory.since", gw_since)
        add("gecit.ogrenci.kantin", lambda i: gw(f"/students/{ids[i % len(ids)]}/canteen"))
        add("gecit.ogrenci.odeme_talepleri",
            lambda i: gw(f"/students/{ids[i % len(ids)]}/payment-requests"))
        add("gecit.ogrenci.kantin_kontrolleri",
            lambda i: gw(f"/students/{ids[i % len(ids)]}/canteen/controls"))
        add("gecit.ogrenci.tablet_kullanimi",
            lambda i: gw(f"/students/{ids[i % len(ids)]}/tablet-usage"))
        add("gecit.tabletler", lambda _: gw("/tablets"))
        add("gecit.zil_haftasi", lambda _: gw("/schedule/bell-week"))
        add("gecit.kantin_ayarlari", lambda _: gw("/canteen/settings/public"))
        add("gecit.kantin_urunleri", lambda _: gw("/canteen/products"))
    return scenarios


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


# ===================================================================== giriş


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    seed_cmd = sub.add_parser("seed", help="hacmi tohumla")
    seed_cmd.add_argument("--db", required=True)
    seed_cmd.add_argument("--scale", default="1.0")
    measure_cmd = sub.add_parser("measure", help="sıcak yolları ölç")
    measure_cmd.add_argument("--db", required=True)
    measure_cmd.add_argument("--out")
    measure_cmd.add_argument("-n", default="200")
    measure_cmd.add_argument("--kantin-ms", default="8")
    measure_cmd.add_argument("--kantin-filtre", action="store_true")
    measure_cmd.add_argument("--db-rtt-ms", default="0")
    measure_cmd.add_argument("--only", nargs="*")
    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(seed(args))
    else:
        asyncio.run(measure(args))


if __name__ == "__main__":
    main()
