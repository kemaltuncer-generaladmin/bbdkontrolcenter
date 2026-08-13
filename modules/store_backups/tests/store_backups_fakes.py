"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i ayrıştırmaz; servisin yazdığı tek ifadeyi (denetim satırı)
tanıyacak kadarını yapar. Amaç çekirdek depoyu taklit etmek değil, servisin
doğru anda doğru satırı yazdığını görmek.

`FakeApi` yalnız `store.api` geçidinin bu modülde KULLANILAN metotlarını
taşır. Uydurulmuş metot yoktur: gerçek geçitte olmayan bir çağrıyı sahtede
yazmak, olmayan bir uca güvenen bir testi "geçiyor" gösterirdi.
"""

from __future__ import annotations

from typing import Any


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

    def __init__(self, module_id: str = "store_backups") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("backup_name", "action", "reason", "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if "_audit" in sql:
            rows = list(reversed(self.audit))
            if "WHERE backup_name" in " ".join(sql.split()):
                rows = [row for row in rows if row["backup_name"] == params[0]]
            return rows
        return []


class FakeStoreApiError(RuntimeError):
    """`StoreApiError`'ın testlik ikizi — geçidin `code` alanını taşır.

    Gerçek sınıf `store_api` modülünde; modül modülü import etmez (K3), bu
    yüzden servis de test de yalnız `getattr(failure, "code", "")` okur. Canlıda
    `GET /api/admin/bbd/backups` şu an 404 dönüyor ve geçit tam olarak bu kodu
    üretiyor (`client._fail`, `code="bbd_endpoint_missing"`).
    """

    def __init__(self, message: str, *, code: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


#: CANLIDAN ALINMIŞ GÖVDE (2026-08-13). `GET /api/admin/bbd/backups` bugün 404
#: değil **503** dönüyor:
#:     {"error": {"code": "CONTROL_API_DISABLED",
#:                "message": "Kontrol Merkezi API'si kapalı. Açmak için
#:                            BBD_CONTROL_API_ENABLED=true yapıp yapılandırma
#:                            önbelleğini yenileyin.", "details": []}}
#: Geçit (`store_api.client._fail`) 503'ü `code="server"` diye işaretler ve
#: gövdedeki `error` alanını olduğu gibi metne koyar; jeton metinde kalır
#: (`mask_text` yalnız sır ADLARINI maskeler, `code` sırlı sayılmaz).
#: Aşağıdaki metin o zincirin gerçek çıktısıdır — uydurulmuş değildir.
DISABLED_MESSAGE = (
    "Mağaza hata verdi (503): {'code': 'CONTROL_API_DISABLED', 'message': "
    '"Kontrol Merkezi API\'si kapalı. Açmak için BBD_CONTROL_API_ENABLED=true '
    "yapıp yapılandırma önbelleğini yenileyin.\", 'details': []}"
)


class FakeApi:
    """`store.api` yeteneğinin testlik yüzü — yalnız yedek uçları."""

    def __init__(self, items: list[dict[str, Any]] | None = None,
                 meta: dict[str, Any] | None = None) -> None:
        self.items = items if items is not None else []
        self.meta = meta or {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail: set[str] = set()
        #: Bu metotlar "uç henüz yayında değil" (404) ile patlar. `fail`'den
        #: ayrıdır: ekranın ikisini AYRI cümleyle anlatması gerekiyor.
        self.missing: set[str] = set()
        #: Bu metotlar "uç yayında ama Kontrol Merkezi API'si KAPALI" (503) ile
        #: patlar. `missing`'den de ayrıdır: 404'te yapılacak bir şey yok,
        #: 503'te açılacak bir anahtar var. CANLIDA BUGÜN GEÇERLİ OLAN HÂL BU.
        self.disabled: set[str] = set()
        self.verify_payload: dict[str, Any] = {"verify_state": "ok", "sha256": "abc123"}
        self.download_bytes = b"yedek-icerigi"
        self.created_name = "bbd-2026-08-13-1200.tar.gz"

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        if name in self.missing:
            raise FakeStoreApiError(
                "BBD'ye özel uç henüz yayında değil: /api/admin/bbd/backups. Mağaza "
                "tarafındaki paket yayınlanınca bu ekran kendiliğinden çalışacak.",
                code="bbd_endpoint_missing", status=404)
        if name in self.disabled:
            # Geçit 503'e `code="server"` verir; "uç yayında değil" kodunu
            # VERMEZ. Modülün bu hâli yalnız mağazanın kendi jetonundan
            # tanıması gerekiyor — sahtede de tam olarak öyle gelir.
            raise FakeStoreApiError(DISABLED_MESSAGE, code="server", status=503)
        if name in self.fail:
            raise RuntimeError(f"{name} patladı")
        self.calls.append((name, args, kwargs))

    def used(self, name: str) -> list[dict[str, Any]]:
        return [kwargs for called, _, kwargs in self.calls if called == name]

    def count(self, name: str) -> int:
        return len([1 for called, _, _ in self.calls if called == name])

    async def bbd_backups(self) -> dict[str, Any]:
        self._record("bbd_backups")
        return {"items": list(self.items), "meta": dict(self.meta), "truncated": False}

    async def bbd_create_backup(self, *, scope: list[str], note: str = "", reason: str,
                                actor: str = "", dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_create_backup", scope=list(scope), note=note, reason=reason,
                     actor=actor, dry_run=dry_run)
        return {"ok": True, "name": self.created_name, "dryRun": bool(dry_run),
                "sent": not dry_run, "size": 1024}

    async def bbd_verify_backup(self, name: str, *, reason: str, actor: str = "",
                                dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_verify_backup", name, reason=reason, actor=actor, dry_run=dry_run)
        return dict(self.verify_payload)

    async def bbd_download_backup(self, name: str) -> bytes:
        self._record("bbd_download_backup", name)
        return self.download_bytes

    async def bbd_restore_backup(self, name: str, *, reason: str, actor: str = "",
                                 dry_run: bool | None = None) -> dict[str, Any]:
        self._record("bbd_restore_backup", name, reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dryRun": bool(dry_run), "sent": not dry_run}
