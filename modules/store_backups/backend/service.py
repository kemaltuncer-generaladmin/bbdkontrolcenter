"""Manuel Yedekleme — iş kuralları.

BU EKRAN CANLI MAĞAZA VERİSİNE DOKUNUR. Yedek almak zararsızdır; GERİ YÜKLEME
mevcut veriyi değiştirir ve bu depodaki en yıkıcı işlemdir. Koruma dört
katmanlıdır ve hepsi burada da uygulanır (arayüzde gizlemek yetkilendirme
değildir — K9):

  1. Ayrı izin anahtarı            → `store_backups.restore`, yalnız admin.
  2. Gerekçe (>=10 karakter)       → `inventory.reason_error`, ekranda ve burada.
  3. `dryRun` varsayılanı          → geçit de varsayılan olarak kuru provada.
  4. GERİ YÜKLEMEDEN ÖNCE GÜVENLİK → `restore` önce yeni bir yedek aldırır;
     YEDEĞİ                           alınamazsa geri yükleme HİÇ BAŞLAMAZ.

SİLME. Mağaza tarafında yedek silme ucu HENÜZ YOK. Uydurulmuş bir çağrı
yazmak yerine yaş kapısı burada uygulanır ve ekran "uç hazır olunca
açılacak" der; sessizce patlamaz (bkz. `delete`).

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner, panel durumu anlatır. İstisna dışarı sızmaz.

UCUN ÜÇ HÂLİ (`endpointState`). "Mağaza çökmüş" ile "bu ekranın uçları
çalışmıyor" ayrı şeylerdir; ikincisinin de İKİ ayrı çaresi var:

    live      Uç çalışıyor.
    missing   404 — paket henüz yayınlanmadı. Yapılacak bir şey yok.
    disabled  503 CONTROL_API_DISABLED — paket yayında, anahtar kapalı.
              Mağaza sunucusunda BBD_CONTROL_API_ENABLED=true yeter.

CANLIDA DOĞRULANDI (2026-08-13): `GET /api/admin/bbd/backups` bugün 404 değil
**503 CONTROL_API_DISABLED** dönüyor; `/api/admin/settings/channels` ve
`/api/admin/orders` aynı anda 200. Yalnız 404'e bakan eski kod bu hâli "gerçek
arıza" sayıyordu: ekran "Mağazaya ulaşılamadı" diyor (mağaza ayaktayken) ve
"Yedeği başlat" düğmesi AÇIK kalıyordu — kullanıcı kapsam seçip gerekçe yazıp
onaylıyor, sonunda 503 görüyordu. Sessiz ölü düğmenin en pahalı biçimi tam
olarak buydu.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import csv_bytes, report_dir, write_private

from . import inventory

#: İndirme belleğe alınır (`bbd_download_backup` ham bayt döner). Bu tavanın
#: üstünde dosya çekmek sidecar'ı şişirir ve makineyi takar; kullanıcı
#: sunucudaki yolu kullanmaya yönlendirilir.
DEFAULT_DOWNLOAD_LIMIT_MB = 200

#: Doğrulama yazma değildir ama geçit GET dışı her isteğe gerekçe istiyor.
#: Kullanıcıya "sağlama toplamına bakmak için gerekçe yaz" dedirtmek yerine
#: sabit ve anlamlı bir metin taşınır; denetim izinde de bu görünür.
VERIFY_REASON = "Yedek bütünlüğü sağlama toplamıyla doğrulanıyor (salt okuma)."


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class BackupsService:
    """Manuel Yedekleme ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 publish: Any = None, category: str = "Mağaza", subcategory: str = "Denetim",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olay veri yoluna haber verir. Dinleyen yoksa da, patlarsa da iş durmaz."""
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyicinin hatası bizi düşürmez (K7)
            self._log.warning("olay yayımlanamadı", event=event, error=str(failure))

    # ------------------------------------------------------------- ayarlar

    @property
    def _stale_hours(self) -> int:
        return max(1, min(24 * 30, inventory.as_int(self._config.get("stale_after_hours"), 48)))

    @property
    def _delete_min_age(self) -> int:
        return max(1, min(3650, inventory.as_int(self._config.get("delete_min_age_days"), 30)))

    @property
    def _download_limit(self) -> int:
        """İndirme tavanı — BAYT."""
        megabytes = inventory.as_int(self._config.get("download_limit_mb"),
                                     DEFAULT_DOWNLOAD_LIMIT_MB)
        return max(1, min(4096, megabytes)) * 1024 * 1024

    @property
    def _safety_backup(self) -> bool:
        return bool(self._config.get("safety_backup_before_restore", True))

    @property
    def _default_scope(self) -> list[str]:
        return inventory.normalize_scope(self._config.get("default_scope")) or list(
            inventory.SCOPES)

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    @property
    def _download_dir(self) -> Path:
        """İndirilen yedeğin yazılacağı klasör.

        Ayrı anahtar: yedek dosyası rapor değildir ve gigabaytlarca yer
        kaplayabilir; yöneticinin onu başka bir diske yönlendirebilmesi gerek.
        Boşsa rapor rafı kullanılır — hiçbir yere yazamamaktan iyidir.
        """
        configured = str(self._config.get("download_path") or "").strip()
        if configured:
            return report_dir(self._category, subcategory=self._subcategory,
                              fallback=self._fallback, configured=configured)
        return self._export_dir

    # ------------------------------------------------------ yerel denetim

    async def _record(self, *, name: str, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(backup_name, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (inventory.text(name), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    @staticmethod
    def _endpoint(failure: Exception) -> str:
        """Ucun hâli: `""` (gerçek arıza) · `missing` (404) · `disabled` (503).

        Kod alanı `StoreApiError` üstünde durur; ÇEVRİLEBİLİR METNE bakarak
        tahmin etmek çeviri değişince kırılır. Sınıfı import etmiyoruz — modül
        modülü import etmez (K3), yalnız kod dizesi ve mağazanın kendi makine
        jetonu taşınır (bkz. `inventory.endpoint_state`).
        """
        return inventory.endpoint_state(failure)

    @classmethod
    def _pending(cls, failure: Exception) -> bool:
        """Uç yayında değil mi (404 ya da 503 fark etmez) — düğme kapanmalı mı."""
        return bool(cls._endpoint(failure))

    @classmethod
    def _fail(cls, failure: Exception) -> str:
        """Kullanıcıya gösterilecek hata metni.

        "Uç yayında değil" ile "mağaza çökmüş" AYRI şeylerdir ve aynı cümleyle
        anlatılamaz: ilkinde yapılacak bir şey yok, ikincisinde personel
        sunucuya bakar. "Uç kapalı" ise ÜÇÜNCÜ bir şeydir: yapılacak şey var
        ama sunucu odasında değil, bir ayar anahtarında.

        CANLIDA (2026-08-13) `/api/admin/bbd/backups` **503
        CONTROL_API_DISABLED** dönüyor — yani bugün ekranın gördüğü normal
        durum bu üçüncüsüdür.
        """
        note = inventory.endpoint_text(cls._endpoint(failure))
        if note:
            return note
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    def _guard(self, reason: str) -> str:
        return inventory.reason_error(reason)

    def _policy(self, *, state: str = inventory.ENDPOINT_LIVE) -> dict[str, Any]:
        """Ekranın uyması gereken kurallar — panel bunları YAZIYLA gösterir.

        `state`: envanter okunurken ucun ne hâlde olduğu (`live` · `missing` ·
        `disabled`). `live` dışındaki her hâlde YAZMA uçları da çalışmaz;
        "Yedeği başlat" düğmesi kapatılır. Açık bırakmak, kullanıcıya gerekçe
        yazdırıp onaylatıp sonra 404/503 göstermek olurdu — sessiz ölü düğmenin
        en pahalı biçimi.

        `endpointNote` iki hâlde iki AYRI cümledir: 404'te yapılacak bir şey
        yoktur, 503'te bir anahtar açılır. Aynı cümleyi yazmak, açılabilecek
        bir anahtarı "bekleyin" diye gösterirdi.
        """
        pending = state not in ("", inventory.ENDPOINT_LIVE)
        return {
            "scopes": [{"key": key, "label": inventory.SCOPE_LABELS[key]}
                       for key in inventory.SCOPES],
            "defaultScope": self._default_scope,
            "staleAfterHours": self._stale_hours,
            "deleteMinAgeDays": self._delete_min_age,
            "downloadLimitBytes": self._download_limit,
            "safetyBackupBeforeRestore": self._safety_backup,
            # Mağazada silme ucu yok; düğme kapalı gösterilir, sessizce patlamaz.
            "deleteAvailable": False,
            "deleteNote": ("Yedek silme ucu mağaza tarafında henüz yayında değil. "
                           "Uç hazır olunca bu düğme kendiliğinden açılacak; "
                           "o güne kadar dosyalar sunucudan elle taşınır."),
            "createAvailable": not pending,
            "endpointPending": pending,
            "endpointState": state or inventory.ENDPOINT_LIVE,
            "endpointNote": inventory.endpoint_text(state),
            "warning": ("Canlı mağaza verisi. Geri yükleme mevcut veriyi değiştirir."),
        }

    # ================================================================ liste

    async def backups(self) -> dict[str, Any]:
        """Yedek envanteri + durum bandı + disk göstergesi.

        Liste ONLARCA satırdır; sayfalama yoktur, süzme istemcide yapılır.
        Sunucu tarafı sayfalama 29 sayfalık katalog içindir, burada değil.
        """
        try:
            payload = await self._api.bbd_backups()
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("yedek envanteri okunamadı", error=str(failure))
            state = self._endpoint(failure)
            disk = inventory.disk_view({}, [])
            # `summary([])` DEĞİL: boş liste ile okunamamış liste aynı şey
            # değildir. İlki "yedek yok" der; burada bildiğimiz tek şey listeyi
            # alamadığımızdır.
            return {
                "ok": True, "connected": False, "endpointPending": bool(state),
                "endpointState": state or inventory.ENDPOINT_LIVE,
                "error": self._fail(failure), "items": [],
                "summary": inventory.unreadable_summary(inventory.endpoint_text(state)),
                "disk": disk, "diskNote": inventory.disk_sentence(disk),
                "policy": self._policy(state=state),
                "features": self.features(),
            }

        rows = inventory.sort_rows(
            [inventory.backup_row(item) for item in (payload.get("items") or [])])
        disk = inventory.disk_view(payload.get("meta"), rows,
                                   sample=inventory.as_int(self._config.get("disk_sample"), 5))
        return {
            "ok": True, "connected": True, "endpointPending": False,
            "endpointState": inventory.ENDPOINT_LIVE, "error": "",
            "items": rows,
            "summary": inventory.summary(rows, stale_after_hours=self._stale_hours),
            "disk": disk,
            "diskNote": inventory.disk_sentence(disk),
            "policy": self._policy(),
            "features": self.features(),
        }

    async def _rows(self) -> tuple[list[dict[str, Any]], str]:
        """Envanteri yalnız satır olarak getirir (eylemlerin ön denetimi için)."""
        try:
            payload = await self._api.bbd_backups()
        except Exception as failure:  # noqa: BLE001 — K7
            return [], self._fail(failure)
        return inventory.sort_rows(
            [inventory.backup_row(item) for item in (payload.get("items") or [])]), ""

    # =============================================================== alma

    async def create(self, *, scope: list[str], note: str = "", reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Yeni yedek aldırır.

        ADIMLAR (`Dışa aktarılıyor → Sıkıştırılıyor → Doğrulanıyor → Yazıldı`)
        ekranda gösterilir; ilk ikisi mağaza tarafında TEK istek içinde
        geçtiği için ayrı ayrı gözlenemez. Panel bunu uydurmaz: aradaki adımı
        atlar ve doğrulamayı gerçekten ayrı bir çağrı olarak yapar.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        chosen = inventory.normalize_scope(scope)
        if not chosen:
            return {"ok": False,
                    "error": "En az bir kapsam seçin (veritabanı, dosyalar ya da ayarlar)."}

        clean_note = inventory.text(note)[:255]
        detail = {"scope": chosen, "note": clean_note}
        await self._record(name="", action="create", reason=reason, actor=actor,
                           result="denendi", detail=detail)
        try:
            result = await self._api.bbd_create_backup(scope=chosen, note=clean_note,
                                                       reason=reason, actor=actor,
                                                       dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(name="", action="create", reason=reason, actor=actor,
                               result="hata", detail={**detail, "error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        # Boş gövdeli 204 de gelebilir; `None` üzerinde `.get` çağırmak ekranı
        # yedek gerçekten alınmışken hata gösterir hâle sokardı.
        result = result if isinstance(result, dict) else {}
        name = inventory.text(inventory.pick(result, "name", "file", "filename") or "")
        if not name and isinstance(result.get("data"), dict):
            name = inventory.text(inventory.pick(result["data"], "name", "file", "filename"))

        await self._record(name=name, action="create", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail=detail)
        if not dry_run:
            # `dryRun` alanı module.yaml'da İLAN EDİLDİ ({name, scope, bytes,
            # actor, dryRun}); dinleyici onu okuyunca KeyError almamalı. Olay
            # yalnız gerçek yedekte yayımlandığı için değeri hep False'tur —
            # ama alan var, çünkü sözleşme öyle diyor (K3: iletişim ilan
            # edilmiş yük üzerinden).
            await self._announce("store.backup.created",
                                 {"name": name, "scope": chosen, "actor": actor,
                                  "dryRun": False,
                                  "bytes": inventory.as_int(
                                      inventory.pick(result, "size", "bytes"))})
        return {"ok": True, "error": "", "name": name, "scope": chosen,
                "scopeLabel": inventory.scope_label(chosen),
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)),
                "bytes": inventory.as_int(inventory.pick(result, "size", "bytes"))}

    # ============================================================ doğrulama

    async def verify(self, name: str, *, actor: str) -> dict[str, Any]:
        """Sağlama toplamını (sha256) mağazaya doğrulatır. VERİYİ DEĞİŞTİRMEZ.

        Gerekçe kullanıcıdan İSTENMEZ: doğrulama okuma işidir ve her seferinde
        "neden doğruluyorsun" diye sormak, gerekçe alanını anlamsızlaştırır.
        Sabit metin denetim izine ve geçide yine de gider.
        """
        clean = inventory.safe_name(name)
        if not clean:
            return {"ok": False, "error": "Yedek adı kabul edilmedi (beklenmeyen karakter)."}

        try:
            result = await self._api.bbd_verify_backup(clean, reason=VERIFY_REASON, actor=actor,
                                                       dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(name=clean, action="verify", reason=VERIFY_REASON, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        state = inventory.verify_state(result if isinstance(result, dict) else {})
        checksum = inventory.text(inventory.pick(result, "sha256", "checksum", "hash"))
        await self._record(name=clean, action="verify", reason=VERIFY_REASON, actor=actor,
                           result=state, detail={"sha256": checksum})

        message = {
            inventory.VERIFY_OK: "Sağlama toplamı tuttu; dosya bozulmamış.",
            inventory.VERIFY_BAD: "SAĞLAMA TOPLAMI TUTMADI. Bu yedek geri yüklenmemeli.",
            inventory.VERIFY_UNKNOWN: ("Mağaza doğrulama sonucu döndürmedi; durum "
                                       "bilinmiyor sayılıyor."),
        }[state]
        return {"ok": state != inventory.VERIFY_BAD, "error": "", "state": state,
                "label": inventory.VERIFY_LABELS[state], "sha256": checksum,
                "message": message}

    # ============================================================== indirme

    async def download(self, name: str, *, actor: str) -> dict[str, Any]:
        """Yedeği yerel diske indirir (rapor/indirme klasörüne, 0600).

        Tarayıcıya Blob olarak akıtmak yerine dosya diske yazılır: panelin
        `api()` katmanı JSON taşıyor ve yüz megabaytlık bir gövdeyi belleğe
        alıp base64'e çevirmek sidecar'ı da kabuğu da düşürürdü.
        """
        clean = inventory.safe_name(name)
        if not clean:
            return {"ok": False, "error": "Yedek adı kabul edilmedi (beklenmeyen karakter)."}

        rows, error = await self._rows()
        if error:
            return {"ok": False, "error": error}
        row = inventory.find_row(rows, clean)
        if row is None:
            return {"ok": False, "error": "Yedek envanterde bulunamadı."}

        limit = self._download_limit
        if row["bytes"] and row["bytes"] > limit:
            return {"ok": False, "error": (
                f"Dosya {row['bytes'] // (1024 * 1024)} MB; buradan indirme sınırı "
                f"{limit // (1024 * 1024)} MB. Sunucudaki yolu kullanın: "
                f"{row['path'] or 'yol bilgisi gelmedi'}")}

        try:
            content = await self._api.bbd_download_backup(clean)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(name=clean, action="download", reason="", actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        blob = bytes(content or b"")
        if len(blob) > limit:
            # Envanterdeki boyut yanlış olabilir; gerçek boyut da denetlenir.
            return {"ok": False, "error": ("Dosya beklenenden büyük geldi ve diske "
                                           "yazılmadı. Sunucudaki yolu kullanın.")}
        try:
            path = write_private(self._download_dir / clean, blob)
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}

        await self._record(name=clean, action="download", reason="", actor=actor, result="ok",
                           detail={"bytes": len(blob), "path": str(path)})
        return {"ok": True, "error": "", "path": str(path), "name": clean, "bytes": len(blob)}

    # =========================================================== GERİ YÜKLE

    #: GERİ YÜKLEME BU EKRANDAN BAŞLATILMIYOR — geçit ucu bilerek yazmadı.
    #:
    #: Gerekçe tek cümle: canlı veriyi ezer. Yedek almak, doğrulamak ve
    #: indirmek geri alınabilir işlerdir ve buradan yapılır; yedeği geri
    #: yüklemek 1.422 ürünü, siparişleri ve müşteri kayıtlarını yedek anındaki
    #: hâline döndürür ve aradaki her değişikliği siler.
    RESTORE_REASON = (
        "Geri yükleme Kontrol Merkezi'nden başlatılmıyor: CANLI VERİYİ EZER — yedek "
        "anından bu yana yapılan sipariş, ürün ve müşteri değişiklikleri kaybolur. "
        "İşlem sunucuda, hazırlanmış geri yükleme yordamıyla yapılır. Yedek almak, "
        "doğrulamak ve indirmek bu ekrandan yapılır ve hepsi geri alınabilir."
    )

    def features(self) -> dict[str, Any]:
        """Hangi düğme açılabilir — kapalı olanın NEDENİ yazılıdır."""
        return {
            "restore": {"available": False, "reason": self.RESTORE_REASON},
            "create": {"available": True, "reason": ""},
            "verify": {"available": True, "reason": ""},
            "download": {"available": True, "reason": ""},
        }

    async def restore(self, name: str, *, reason: str, actor: str,
                      dry_run: bool = True) -> dict[str, Any]:
        """EN YIKICI İŞLEM — ve bu geçitten YAPILMIYOR.

        UÇ KALDIRILMADI (K9): arayüzde düğmeyi kapatmak yetkilendirme değildir
        ve şemayı atlatan bir istemci de aynı cevabı almalı.

        GÜVENLİK YEDEĞİ ARTIK ALINMIYOR — ve bu bilinçli. Eskiden bu metot
        önce tam bir güvenlik yedeği alıyor, sonra geri yükleme isteğini
        gönderiyor ve geçitten "bu uç bilerek yok" cevabını alıyordu. Yani
        yapılmayacak bir iş için her seferinde gerçek bir yedek üretiliyordu:
        disk doluyor, mağaza meşgul ediliyor, kullanıcı ham bir hata metni
        görüyordu.
        """
        await self._record(name=inventory.safe_name(name), action="restore", reason=reason,
                           actor=actor, result="uc_yok", detail={"dryRun": dry_run})
        return {"ok": False, "blocked": True, "feature": "restore",
                "error": self.RESTORE_REASON}

    # ================================================================ silme

    async def delete(self, name: str, *, reason: str, actor: str) -> dict[str, Any]:
        """Eski yedeği siler — MAĞAZA UCU HENÜZ YAYINDA DEĞİL.

        Yaş kapısı yine de burada uygulanır: uç yayınlandığı gün kural zaten
        yazılmış ve test edilmiş olur, "sonra ekleriz" borcuna kalmaz.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = inventory.safe_name(name)
        if not clean:
            return {"ok": False, "error": "Yedek adı kabul edilmedi (beklenmeyen karakter)."}

        rows, error = await self._rows()
        if error:
            return {"ok": False, "error": error}
        allowed, why = inventory.deletable(inventory.find_row(rows, clean),
                                           min_age_days=self._delete_min_age)
        if not allowed:
            return {"ok": False, "error": why}

        note = self._policy()["deleteNote"]
        await self._record(name=clean, action="delete", reason=reason, actor=actor,
                           result="uc_yok", detail={"note": note})
        return {"ok": False, "code": "endpoint_missing", "error": note}

    # ============================================================== denetim

    async def audit(self, *, name: str = "", limit: int = 100) -> dict[str, Any]:
        """Bu ekrandan yapılan işlemlerin YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT backup_name, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        clean = inventory.text(name)
        if clean:
            sql += "WHERE backup_name = ? "
            params = (clean,)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"name": row["backup_name"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ================================================================= CSV

    async def export_csv(self) -> dict[str, Any]:
        """Yedek envanterinin tamamı — rapor rafına CSV.

        Denetimde sorulan soru "hangi tarihte hangi yedek vardı" olduğu için
        dosya rapor klasörüne yazılır ve orada kalır; ekrandaki anlık CSV
        (panel tarafı) bunun yerini tutmaz.
        """
        rows, error = await self._rows()
        if error:
            return {"ok": False, "error": error}
        if not rows:
            return {"ok": False, "error": "Envanterde yedek yok; yazılacak satır bulunamadı."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-yedek-envanteri-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name,
                                 csv_bytes(inventory.CSV_HEADERS, inventory.csv_table(rows)))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        self._log.info("yedek envanteri yazıldı", rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(rows)}

    # ================================================== dosya yolu güvenliği

    def within_output_dir(self, path: str) -> bool:
        """Bir yolun bizim yazdığımız klasörlerden birinde olup olmadığı.

        Ekrana yol gösterirken ve ileride yazdırma/açma eklenirse kullanılır:
        serbest yol kabul etmek, makinedeki herhangi bir dosyayı bu ekran
        üzerinden dolaştırmaya açık kapı bırakır.
        """
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError:
            return False
        for allowed in (self._export_dir, self._download_dir):
            root = str(allowed.resolve())
            if str(resolved).startswith(root + os.sep):
                return True
        return False
