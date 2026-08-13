"""Kantin Yedekleri — iş kuralları.

İKİ İŞ:

1. **Görmek.** Sunucudaki yedekleri listeler, tazeliğini denetler. Kantin
   `db:backup` komutunu beş dakikada bir çalıştırıyor; son yedek eskiyse
   (zamanlayıcı durmuş, disk dolmuş) bunu görmek gerekir.
2. **Elde tutmak.** Yedeği BU MAKİNEYE indirir ve sha256 ile doğrular. Sunucu
   tümden kaybolsa bile elde doğrulanmış bir kopya kalır.

GERİ YÜKLEME YOKTUR — tek tıkla canlı veriyi ezmek kabul edilemez risk.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import write_private


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse(text: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class BackupService:
    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any],
                 local_dir: Path) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config
        self._local_dir = local_dir
        self._table = store.table("local")

    # -------------------------------------------------------------- okuma

    async def overview(self) -> dict[str, Any]:
        """Sunucudaki yedekler + tazelik + yereldeki kopyalar."""
        try:
            payload = await self._canteen.backups()
            server = list(payload.get("data") or [])
            supported = bool(payload.get("supported", True))
            connected, error = True, ""
        except Exception as failure:  # noqa: BLE001 — kantin dışarısı; ekran ayakta kalmalı
            server, supported, connected, error = [], False, False, str(failure)
            self._log.warning("yedekler okunamadı", error=error)

        status: dict[str, Any] = {}
        try:
            status = await self._canteen.status()
        except Exception as failure:  # noqa: BLE001
            self._log.warning("durum okunamadı", error=str(failure))

        local_rows = {
            str(row["name"]): dict(row)
            for row in await self._store.fetch_all(f"SELECT * FROM {self._table}")
        }
        # Defterde yazan dosya gerçekten duruyor mu — elle silinmiş olabilir.
        for row in local_rows.values():
            row["exists"] = Path(str(row["path"])).is_file()

        stale_after = int(self._config.get("stale_after_minutes") or 60)
        newest = _parse(server[0]["createdAt"]) if server else None
        age_minutes = None
        if newest is not None:
            age_minutes = int((datetime.now(UTC) - newest).total_seconds() // 60)

        return {
            "connected": connected,
            "error": error,
            "supported": supported,
            "backups": [
                {**item, "local": local_rows.get(str(item.get("name")))}
                for item in server
            ],
            "local": sorted(local_rows.values(),
                            key=lambda row: str(row["downloaded_at"]), reverse=True),
            "localDir": str(self._local_dir),
            "status": status,
            "freshness": {
                "lastBackupAt": server[0]["createdAt"] if server else None,
                "ageMinutes": age_minutes,
                "staleAfter": stale_after,
                # Yedek yoksa da "bayat" sayılır: sessiz kalmak en kötüsü.
                "stale": age_minutes is None or age_minutes > stale_after,
            },
            "summary": {
                "serverCount": len(server),
                "serverBytes": sum(int(item.get("size") or 0) for item in server),
                "localCount": sum(1 for row in local_rows.values() if row["exists"]),
                "localBytes": sum(int(row["size"]) for row in local_rows.values()
                                  if row["exists"]),
                "keepLocal": int(self._config.get("keep_local") or 10),
            },
        }

    # -------------------------------------------------------------- yazma

    async def create(self) -> dict[str, Any]:
        """Sunucuda elle yedek aldırır."""
        try:
            result = await self._canteen.create_backup()
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}
        if not result.get("created"):
            return {"ok": False, "error": f"Yedek alınamadı ({result.get('reason')})."}
        self._log.info("elle yedek alındı", name=result.get("name"))
        return {"ok": True, **result}

    async def download(self, name: str, *, actor: str) -> dict[str, Any]:
        """Yedeği bu makineye indirir ve sha256 ile DOĞRULAR.

        Doğrulama tutmazsa dosya silinir: bozuk bir yedeği "elimde kopya var"
        diye saklamak, hiç kopyası olmamaktan tehlikelidir.
        """
        try:
            content, server_hash = await self._canteen.download_backup(name)
        except Exception as failure:  # noqa: BLE001
            return {"ok": False, "error": str(failure)}

        local_hash = hashlib.sha256(content).hexdigest()
        verified = bool(server_hash) and local_hash == server_hash
        if server_hash and not verified:
            return {"ok": False,
                    "error": "İndirilen dosya sunucudaki özetle uyuşmadı; "
                             "kopya saklanmadı. Bağlantıyı denetleyip tekrar deneyin."}

        # İNDİRİLEN YEDEK KİŞİSEL VERİ TAŞIR: öğrenci adı, veli telefonu, bakiye.
        # Varsayılan umask ile 0644 açılıyordu — makinedeki her kullanıcı okurdu.
        path = write_private(self._local_dir / name, content)

        # Sunucudaki oluşturma zamanını da saklayalım ki liste anlamlı sıralansın.
        created_at = ""
        try:
            for item in (await self._canteen.backups()).get("data") or []:
                if str(item.get("name")) == name:
                    created_at = str(item.get("createdAt") or "")
                    break
        except Exception as failure:  # noqa: BLE001 — zaman bilgisi kritik değil
            self._log.warning("yedek zamanı okunamadı", name=name, error=str(failure))

        await self._store.execute(
            f"INSERT OR REPLACE INTO {self._table} (name, path, size, sha256, server_sha256, "
            f"verified, created_at, downloaded_at, downloaded_by) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, str(path), len(content), local_hash, server_hash,
             1 if verified else 0, created_at, _now(), actor),
        )

        pruned = await self._prune()
        self._log.info("yedek indirildi", name=name, bytes=len(content), verified=verified)
        return {"ok": True, "name": name, "path": str(path), "size": len(content),
                "sha256": local_hash, "verified": verified, "pruned": pruned}

    async def _prune(self) -> list[str]:
        """Yerelde `keep_local` kopyadan fazlası varsa en eskileri siler."""
        keep = max(1, int(self._config.get("keep_local") or 10))
        rows = await self._store.fetch_all(
            f"SELECT name, path FROM {self._table} ORDER BY downloaded_at DESC"
        )
        removed: list[str] = []
        for row in rows[keep:]:
            path = Path(str(row["path"]))
            try:
                if path.is_file():
                    path.unlink()
            except OSError as failure:
                self._log.warning("eski yedek silinemedi", path=str(path), error=str(failure))
                continue
            await self._store.execute(f"DELETE FROM {self._table} WHERE name = ?",
                                      (str(row["name"]),))
            removed.append(str(row["name"]))
        return removed

    async def verify_local(self) -> dict[str, Any]:
        """Yereldeki kopyaları yeniden özetleyip defterle karşılaştırır.

        Sessiz bozulmayı (disk hatası, yarım kopya) ancak bu yakalar.
        """
        rows = await self._store.fetch_all(f"SELECT * FROM {self._table}")
        checked, ok, bad, missing = 0, 0, 0, 0
        details: list[dict[str, Any]] = []

        for row in rows:
            path = Path(str(row["path"]))
            if not path.is_file():
                missing += 1
                details.append({"name": row["name"], "state": "missing"})
                continue
            checked += 1
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            good = digest == str(row["sha256"])
            (ok, bad) = (ok + 1, bad) if good else (ok, bad + 1)
            details.append({"name": row["name"], "state": "ok" if good else "corrupt"})
            await self._store.execute(
                f"UPDATE {self._table} SET verified = ? WHERE name = ?",
                (1 if good else 0, str(row["name"])),
            )

        return {"ok": True, "checked": checked, "valid": ok, "corrupt": bad,
                "missing": missing, "details": details}
