"""Bildirimler — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Duyuru kaydı ve okunma sayaçları BLD
sunucusunda durur (`veykemtu_notifications`, `veykemtu_notification_reads`) ve
buraya `bld.api` geçidinden gelir (K4); bu modül ham httpx kullanmaz ve UZAK
VERİNİN KOPYASINI TUTMAZ. Yerel tek tablo, BLD'de karşılığı olmayan tek şeyi
saklar: yazma DENEMESİNİN izi.

Neden kopya tutulmuyor: duyurunun `live` alanı sunucuda hesaplanıyor ve
zamanla kendiliğinden değişiyor (pencere açılıyor, kapanıyor). Yerel bir kopya
her zaman bir tur geride kalır ve "yayında" görünen bir duyuru aslında dün
sona ermiş olabilir. Ekranın yanlış bilgiyi doğru gibi göstermesi, hiç
göstermemesinden kötüdür.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISINI DEĞİL, UCUN SAĞLIĞINI anlatır; ayrımı
`connected` taşır ve panelin onu OKUMASI gerekir.

SUNUCU UCU HENÜZ YAYINDA OLMAYABİLİR. Duyuru tabloları ve `/api/control/
notifications` uçları başka bir ajanın kulvarında ve sonraki fazda geliyor.
Geçit bu durumda temiz bir `control_endpoint_missing` kodu veriyor; ekran o
zaman "sunucu eklentisi güncellenince çalışacak" der ve zarifçe bozulur (K7).
`not_found` ile karıştırılmaz: ilki "uç yok", ikincisi "kayıt yok".

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe + aktör denetimi (arayüzde zorunlu göstermek yetmez, K9)
    2. TAZE OKUMA (duyuru aradan yayınlanmış ya da arşivlenmiş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı — `dry_run` HER ÇAĞRIDA AÇIKÇA verilir
    5. yerel iz: `ok` / `dry_run` / `hata`

Dördüncü adımdaki kural mutlaktır: `bld_api` geçidinin `dry_run` varsayılanı
`config/local.yaml` ile açılabiliyor ve o dosya git dışında. Bayrağı atlayan
bir çağrı hiçbir şey yazmadan `{"ok": true}` alır, ekran "kaydedildi" der ve
"yazıldı" ile "sessizce atıldı" ayırt edilemez hâle gelir.

KURU PROVA ARAYÜZDE YOKTUR ama alan DURUR: uçlar `dryRun` alanını kabul eder
(sözleşme §4 additive) ve servis onu geçide taşır. Panel göndermez; varsayılan
`config/default.yaml` içinde KAPALIDIR. Yanıtta `dry_run: true` görürse panel
"hiçbir şey yazılmadı" der — sessizce "kaydedildi" DEMEZ.
"""

from __future__ import annotations

import json
from typing import Any

from . import notices as nt

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Yerel iz eylem adları. Sözleşmedeki sunucu eylemleriyle (`notification.*`)
#: BİLEREK aynı yazılır: iki iz yan yana konduğunda aynı satırın iki yüzü
#: olduğu görülsün.
CREATE = "notification.create"
UPDATE = "notification.update"
PUBLISH = "notification.publish"
ARCHIVE = "notification.archive"

#: Yerel iz için hedef türü (sözleşme §8.1 ile aynı ad).
TARGET = "notification"


class NoticeService:
    """Bildirimler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any,
                 config: dict[str, Any] | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._audit = store.table("audit")

    # ------------------------------------------------------------- ayarlar

    @property
    def _page_size(self) -> int:
        """Sayfa boyutu. Sözleşme tavanı 100 (`00-genel.md` §5)."""
        return max(1, min(100, nt.as_int(self._config.get("page_size"), 25)))

    @property
    def _refresh_seconds(self) -> int:
        """Panelin yoklama aralığı. Ekran bunu ayardan okur, kendi sabitinden değil.

        Varsayılan 120 sn: `00-genel.md` §2'deki bütçe tablosunda duyuru rozeti
        için ayrılan pay tam olarak bu. Kısaltmak paylaşılan `bld-control-panel`
        kovasından başka ekranların payını yer.
        """
        return max(30, min(3600, nt.as_int(self._config.get("refresh_seconds"), 120)))

    @property
    def _ending_soon_hours(self) -> int:
        """"Yakında bitiyor" uyarısının eşiği (saat).

        Ekran kararıdır, sözleşmede karşılığı yoktur: süresi dolmak üzere olan
        bir duyuru listede sessizce durur ve bir sabah kaybolur. Uyarı, süreyi
        uzatma fırsatını yöneticinin önüne koyar.
        """
        return max(1, min(720, nt.as_int(self._config.get("ending_soon_hours"), 48)))

    @property
    def _audit_limit(self) -> int:
        return max(1, min(500, nt.as_int(self._config.get("audit_limit"), 100)))

    def _dry(self, dry_run: bool | None) -> bool:
        """Kuru prova varsayılanı. İstemci bayrağı HİÇ göndermezse bu geçerlidir.

        Yedek değer `False`: ayar dosyası okunamadığında ekranın "yayınlandı"
        deyip hiçbir şey yayınlamaması, açık bir hatadan çok daha pahalıdır.
        """
        if dry_run is None:
            return bool(self._config.get("dry_run_default", False))
        return bool(dry_run)

    # ------------------------------------------------------ yerel denetim izi

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_id: int = 0, detail: Any = None) -> None:
        """Yerel iz. BLD de `veykemtu_control_audit` tutuyor (sözleşme §8);
        bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN istekleri bilir. Ağ
        koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim hangi duyuruyu
        yayınlamaya çalıştı" sorusunun cevabı yalnız burada kalır. Duyuru dışa
        dönük içeriktir ve "yayınlandı mı" sorusunun cevapsız kalması kabul
        edilemez.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (TARGET, int(target_id or 0), action, nt.text(reason), nt.text(actor),
                 result, json.dumps(detail or {}, ensure_ascii=False), nt.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def audit(self, *, limit: int = 0) -> dict[str, Any]:
        """Bu ekrandan yapılan yazma DENEMELERİ. Ağa çıkmaz.

        Sunucunun kendi izi (`GET /api/control/audit`) ayrı bir sorunun
        cevabıdır ve `bld_status_monitor` kulvarındadır; buradaki iz
        "gönderildi mi" sorusuna, oradaki "ne oldu" sorusuna bakar.
        """
        count = max(1, min(500, int(limit or self._audit_limit)))
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_id, action, reason, actor, result, detail, created_at "
                f"FROM {self._audit} ORDER BY id DESC LIMIT ?", (count,))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yerel iz okunamadı", error=str(failure))
            return {"ok": True, "connected": True, "error": "", "items": []}
        items = []
        for row in rows:
            detail = row.get("detail") or "{}"
            try:
                parsed = json.loads(detail)
            except (TypeError, ValueError):
                parsed = {}
            items.append({
                "id": nt.as_int(row.get("id")),
                "notification_id": nt.as_int(row.get("target_id")),
                "action": nt.text(row.get("action")),
                "reason": nt.text(row.get("reason")),
                "actor": nt.text(row.get("actor")),
                "result": nt.text(row.get("result")),
                "detail": parsed if isinstance(parsed, dict) else {},
                "created_at": nt.text(row.get("created_at")),
            })
        return {"ok": True, "connected": True, "error": "", "items": items}

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        """Geçidin hatasını ekranın yazabileceği bir cümleye çevirir.

        `control_endpoint_missing` AYRI ele alınır: "kayıt yok" değil, "uç
        sunucuya henüz dağıtılmamış" demektir ve yöneticinin yapacağı şey
        beklemektir. İkisini aynı cümleyle söylemek, var olmayan bir duyuruyu
        aramaya çıkarırdı.
        """
        message = str(failure).strip() or "BLD sunucusuna ulaşılamadı."
        if getattr(failure, "code", "") == "control_endpoint_missing":
            return (f"{message} Duyuru uçları BLD sunucusuna henüz dağıtılmadı; "
                    "sunucu eklentisi güncellenince bu ekran kendiliğinden çalışır.")
        return message

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit zarfı zaten açıyor ve `{"items": [...], "meta": {...}}` veriyor;
        `data` ve düz liste de kabul edilir çünkü tek bir ada bağlanmak, ad
        tutmadığında ekranı SESSİZCE boş gösterirdi.
        """
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Tekil yanıttan kaydı çıkarır (`{"data": {...}}` ya da düz sözlük)."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _warnings(payload: Any) -> list[dict[str, Any]]:
        """Sunucunun uyarıları — `audience_changed_after_publish` gibi.

        Uyarı bir hata DEĞİLDİR: yazma başarılı olmuştur ve panel bunu ayrıca
        göstermek zorundadır. Yutulursa yönetici, kapsamı daralttığında kaç
        müşterinin duyuruyu artık göremeyeceğini hiç öğrenemez.
        """
        if isinstance(payload, dict) and isinstance(payload.get("warnings"), list):
            return [item for item in payload["warnings"] if isinstance(item, dict)]
        return []

    def _meta(self, payload: Any, *, page: int, per_page: int) -> dict[str, Any]:
        raw = payload.get("meta") if isinstance(payload, dict) else None
        meta = dict(raw) if isinstance(raw, dict) else {}
        return {
            "page": nt.as_int(meta.get("page"), page),
            "per_page": nt.as_int(meta.get("per_page"), per_page),
            "total": nt.as_int(meta.get("total")),
            "last_page": nt.as_int(meta.get("last_page"), 1) or 1,
            # `live_count` SÜZGEÇTEN BAĞIMSIZDIR ve sözleşmede öyle tanımlı:
            # "üç duyuru yayında" ile "üçü de tarih aralığının dışında"
            # arasındaki farkı görmeyen yönetici, duyurusunun neden
            # görünmediğini anlayamaz. Eksikse `None` kalır — sıfır yazmak
            # "hiçbiri görünmüyor" demek olurdu ve bu ölçülmüş bir iddiadır.
            "live_count": None if meta.get("live_count") is None
            else nt.as_int(meta.get("live_count")),
        }

    def _settings(self) -> dict[str, Any]:
        """Panelin ekran davranışını belirleyen ayarlar. Sözleşme değil, tercih."""
        return {"page_size": self._page_size,
                "refresh_seconds": self._refresh_seconds,
                "ending_soon_hours": self._ending_soon_hours}

    async def _fresh_status(self, notification_id: int) -> tuple[str, str]:
        """Duyurunun TAZE durumu. `(durum, hata)` döner; hata varsa durum boştur.

        SÖZLEŞMEDE TEK DUYURU OKUYAN UÇ YOK: `GET /notifications/{id}`
        tanımlanmadı. En yakın taze kaynak `/{id}/stats` ve `status` alanını
        taşıyor; listeyi baştan taramak hem sayfalarca istek eder hem de
        aradaki değişikliği yine kaçırabilirdi.

        Okunamazsa YAZMA DURDURULMAZ: bu bir kolaylık kapısıdır, yetki kapısı
        değil. Asıl karar sunucudadır (`409 CONFLICT`); burada yalnız
        yöneticiye erken ve anlaşılır bir cevap verilir.
        """
        try:
            payload = await self._api.notification_stats(int(notification_id))
        except Exception as failure:  # noqa: BLE001 — K7, karar sunucuda
            return "", self._fail(failure)
        return nt.text(self._record_of(payload, "data").get("status")), ""

    # ================================================================= okuma

    async def notices(self, *, status: str = "", audience: str = "", level: str = "",
                      live: bool | None = None, q: str = "", page: int = 1,
                      per_page: int = 0) -> dict[str, Any]:
        """Duyuru listesi (sayfalı) + formun sözleşmesi.

        SÖZLEŞME YEREL VE HER ZAMAN DÖNER: geçit düşse bile açılır kutular,
        sınırlar ve yardım metinleri çizilebilir (K7). Panel bunları kendi
        içinde ikinci kez tutsaydı, sözleşme değiştiğinde iki yerden biri
        unutulurdu.
        """
        size = max(1, min(100, int(per_page or self._page_size)))
        number = max(1, int(page or 1))
        contract = {"reference": nt.reference(), "settings": self._settings()}
        try:
            payload = await self._api.notifications(
                status=nt.text(status), audience=nt.text(audience), level=nt.text(level),
                live=live, q=nt.text(q), page=number, per_page=size)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("duyuru listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "meta": {"page": number, "per_page": size, "total": 0,
                                          "last_page": 1, "live_count": None},
                    **contract}
        server_time = nt.text(payload.get("server_time")) if isinstance(payload, dict) else ""
        rows = [nt.notice_row(raw, server_time=server_time) for raw in self._items(payload)]
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "meta": self._meta(payload, page=number, per_page=size),
                "server_time": server_time, **contract}

    async def stats(self, notification_id: int) -> dict[str, Any]:
        """Görülme istatistiği.

        `trackable: false` yanıtı BİR HATA DEĞİLDİR: kitlesi `all` olan duyuru
        ölçülemez ve sayılar `null` döner. Panel bunu açıkça yazar; sıfır
        göstermek, çalışan bir duyuruyu başarısız gösterirdi.
        """
        try:
            payload = await self._api.notification_stats(int(notification_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("istatistik okunamadı", notificationId=notification_id,
                              error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "data": {}, "id": int(notification_id)}
        raw = self._record_of(payload, "data")
        if not raw:
            return {"ok": True, "connected": True,
                    "error": "İstatistik kaydı boş döndü.",
                    "data": {}, "id": int(notification_id)}
        return {"ok": True, "connected": True, "error": "",
                "data": nt.stats_view(raw), "id": int(notification_id),
                "server_time": nt.text(payload.get("server_time"))
                if isinstance(payload, dict) else ""}

    # ================================================================= yazma

    def _guard(self, reason: str, actor: str) -> str:
        problem = nt.reason_error(reason)
        return problem or nt.actor_error(actor)

    async def create(self, *, title: str, body: str, level: str, audience: str,
                     starts_at: str = "", ends_at: str = "",
                     action_label: str = "", action_url: str = "",
                     dismissible: bool = True, reason: str, actor: str,
                     dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni duyuru. HER ZAMAN `draft` doğar — yayın ayrı bir eylemdir.

        Taslak dışa dönük değildir; bu yüzden oluşturma `manage` iznindedir ve
        gerekçeli onay penceresi İSTEMEZ. Dışa dönük olan tek eşik yayındır ve
        onun ayrı bir izni vardır.
        """
        problem = self._guard(reason, actor)
        if problem:
            return {"ok": False, "error": problem}

        fields = {
            "title": nt.text(title), "body": nt.text(body),
            "level": nt.text(level) or "info",
            "audience": nt.text(audience) or "customers",
            "starts_at": nt.text(starts_at), "ends_at": nt.text(ends_at),
            "action_label": nt.text(action_label), "action_url": nt.text(action_url),
            "dismissible": bool(dismissible),
        }
        problem = nt.draft_error(fields)
        if problem:
            return {"ok": False, "error": problem}

        kuru = self._dry(dry_run)
        detail = {**nt.audit_detail(fields), "dry_run": kuru}
        await self._record(action=CREATE, reason=reason, actor=actor, result=TRIED,
                           detail=detail)
        try:
            payload = await self._api.create_notification(
                title=fields["title"], body=fields["body"], level=fields["level"],
                audience=fields["audience"],
                # BOŞ DİZE DEĞİL `None`: sözleşmede `null` "pencere yok"
                # demektir; boş dize gönderilseydi sunucu onu bir an olarak
                # ayrıştırmaya çalışır ve 422 verirdi.
                starts_at=fields["starts_at"] or None, ends_at=fields["ends_at"] or None,
                action_label=fields["action_label"] or None,
                action_url=fields["action_url"] or None,
                dismissible=fields["dismissible"],
                reason=reason, actor=actor, dry_run=kuru)  # dry_run AÇIK — varsayılana güvenilmez
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action=CREATE, reason=reason, actor=actor, result=FAILED,
                               detail={**detail, "error": message})
            return {"ok": False, "error": message}

        return await self._finish(payload, action=CREATE, reason=reason, actor=actor,
                                  detail=detail, key="notice")

    async def update(self, notification_id: int, *, changes: dict[str, Any],
                     reason: str, actor: str,
                     dry_run: bool | None = None) -> dict[str, Any]:
        """Kısmi güncelleme.

        YAYINLANMIŞ DUYURU DÜZENLENEBİLİR ve bu bilinçlidir (sözleşme §PATCH):
        yazım hatası düzeltmek, tarihi uzatmak gerçek ihtiyaçlar. `audience`
        değişimi sunucuda `warnings` üretir ve panel onu AYRICA gösterir;
        görülme kayıtları silinmez.
        """
        problem = self._guard(reason, actor)
        if problem:
            return {"ok": False, "error": problem}

        clean = {key: value for key, value in (changes or {}).items()}
        problem = nt.patch_error(clean)
        if problem:
            return {"ok": False, "error": problem}

        # Boş dize ile `None` ayrımı: pencere ve düğme alanlarında boş dize
        # "temizle" demektir ve sözleşmedeki karşılığı `null`'dır.
        body: dict[str, Any] = {}
        for key, value in clean.items():
            if key in ("starts_at", "ends_at", "action_label", "action_url"):
                body[key] = nt.text(value) or None
            elif key == "dismissible":
                body[key] = bool(value)
            else:
                body[key] = nt.text(value)

        kuru = self._dry(dry_run)
        detail = {**nt.audit_detail(clean), "fields": sorted(clean), "dry_run": kuru}
        await self._record(action=UPDATE, reason=reason, actor=actor, result=TRIED,
                           target_id=int(notification_id), detail=detail)
        try:
            payload = await self._api.update_notification(
                int(notification_id), reason=reason, actor=actor, dry_run=kuru, **body)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action=UPDATE, reason=reason, actor=actor, result=FAILED,
                               target_id=int(notification_id),
                               detail={**detail, "error": message})
            return {"ok": False, "error": message}

        return await self._finish(payload, action=UPDATE, reason=reason, actor=actor,
                                  detail=detail, key="notice",
                                  target_id=int(notification_id))

    async def publish(self, notification_id: int, *, reason: str, actor: str,
                      dry_run: bool | None = None,
                      allow_publish: bool = False) -> dict[str, Any]:
        """Duyuruyu yayına alır. DIŞA DÖNÜK — ayrı izin + gerekçeli onay.

        İzin BURADA DA denetlenir (K9 — çift kapı): uç noktadaki `requires`
        kapısı arayüzü kapatır, buradaki kapı gövdeyi elle kuran istemciyi de
        kapatır.

        `published_at` İLK yayında yazılır ve sonra değişmez; arşivden geri
        yayınlanan duyurunun ilk yayın tarihi korunur. `POST /{id}/unpublish`
        YOKTUR: yayından kaldırmanın yolu `ends_at`'i geçmişe çekmek ya da
        arşivlemektir.
        """
        if not allow_publish:
            await self._record(action=PUBLISH, reason=reason, actor=actor,
                               result=BLOCKED, target_id=int(notification_id),
                               detail={"why": "izin yok"})
            return {"ok": False,
                    "error": "Duyuru yayınlama yetkisi gerekiyor "
                             "(bld_notifications.publish). Yayınlanan duyuru "
                             "müşterilere gider ve geri alma ucu yoktur."}
        problem = self._guard(reason, actor)
        if problem:
            return {"ok": False, "error": problem}

        # TAZE OKUMA: duyuru aradan başka biri tarafından yayınlanmış olabilir.
        # Okunamazsa karar sunucuya bırakılır (409 CONFLICT) — bu bir kolaylık
        # kapısıdır, yetki kapısı değil.
        status, _ = await self._fresh_status(int(notification_id))
        if status == "published":
            await self._record(action=PUBLISH, reason=reason, actor=actor,
                               result=BLOCKED, target_id=int(notification_id),
                               detail={"status": status})
            return {"ok": False,
                    "error": "Bu duyuru zaten yayında. Yayından kaldırmak için "
                             "bitiş anını geçmişe çekin ya da arşivleyin."}

        kuru = self._dry(dry_run)
        detail = {"status_before": status, "dry_run": kuru}
        await self._record(action=PUBLISH, reason=reason, actor=actor, result=TRIED,
                           target_id=int(notification_id), detail=detail)
        try:
            payload = await self._api.publish_notification(
                int(notification_id), reason=reason, actor=actor, dry_run=kuru)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action=PUBLISH, reason=reason, actor=actor, result=FAILED,
                               target_id=int(notification_id),
                               detail={**detail, "error": message})
            return {"ok": False, "error": message}

        return await self._finish(payload, action=PUBLISH, reason=reason, actor=actor,
                                  detail=detail, key="publish",
                                  target_id=int(notification_id))

    async def archive(self, notification_id: int, *, reason: str, actor: str,
                      dry_run: bool | None = None,
                      allow_publish: bool = False) -> dict[str, Any]:
        """Duyuruyu arşivler — YUMUŞAK. Satır silinmez, `status = archived`.

        Arşivlenen duyuru ANINDA görünmez olur, `ends_at` beklenmez. Görülme
        kayıtları kalır ve `stats` çalışmaya devam eder: bir duyurunun kaç
        kişiye ulaştığı sonradan sorulan bir sorudur ve kaydı silinmiş bir
        duyuru o soruyu cevapsız bırakırdı.

        Yayınla AYNI izinle korunur: yayında duran bir duyuruyu görünmez yapmak
        da dışa dönük bir karardır ve aynı eşiği hak eder.
        """
        if not allow_publish:
            await self._record(action=ARCHIVE, reason=reason, actor=actor,
                               result=BLOCKED, target_id=int(notification_id),
                               detail={"why": "izin yok"})
            return {"ok": False,
                    "error": "Duyuru arşivleme yetkisi gerekiyor "
                             "(bld_notifications.publish). Arşivlenen duyuru "
                             "müşteride anında görünmez olur."}
        problem = self._guard(reason, actor)
        if problem:
            return {"ok": False, "error": problem}

        status, _ = await self._fresh_status(int(notification_id))
        if status == "archived":
            await self._record(action=ARCHIVE, reason=reason, actor=actor,
                               result=BLOCKED, target_id=int(notification_id),
                               detail={"status": status})
            return {"ok": False, "error": "Bu duyuru zaten arşivde."}

        kuru = self._dry(dry_run)
        detail = {"status_before": status, "dry_run": kuru}
        await self._record(action=ARCHIVE, reason=reason, actor=actor, result=TRIED,
                           target_id=int(notification_id), detail=detail)
        try:
            payload = await self._api.archive_notification(
                int(notification_id), reason=reason, actor=actor, dry_run=kuru)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action=ARCHIVE, reason=reason, actor=actor, result=FAILED,
                               target_id=int(notification_id),
                               detail={**detail, "error": message})
            return {"ok": False, "error": message}

        return await self._finish(payload, action=ARCHIVE, reason=reason, actor=actor,
                                  detail=detail, key="notice",
                                  target_id=int(notification_id))

    async def _finish(self, payload: Any, *, action: str, reason: str, actor: str,
                      detail: dict[str, Any], key: str,
                      target_id: int = 0) -> dict[str, Any]:
        """Yazma yanıtının ortak kuyruğu: izi kapatır, zarfı açar.

        KURU PROVADA `data` YERİNE `would` GELİR (sözleşme §3.1) ve yanıt
        bunu OLDUĞU GİBİ taşır. Panel `dry_run` bayrağını okuyup "hiçbir şey
        yazılmadı" demek zorunda; iki bloğu tek ada indirmek, provayı gerçek
        yazma gibi göstermek olurdu.
        """
        dry = bool(payload.get("dry_run")) if isinstance(payload, dict) else False
        audit_id = nt.as_int(payload.get("audit_id")) if isinstance(payload, dict) else 0
        record = self._record_of(payload, "data", "would")
        new_id = nt.as_int(record.get("id")) or int(target_id or 0)

        await self._record(action=action, reason=reason, actor=actor,
                           result=DRY if dry else DONE, target_id=new_id,
                           detail={**detail, "audit_id": audit_id})

        view: dict[str, Any]
        if key == "publish":
            view = nt.publish_view(record)
        else:
            view = nt.notice_row(record) if record else {}

        return {"ok": True, "connected": True, "error": "", "dry_run": dry,
                "audit_id": audit_id, "id": new_id, key: view,
                "would": record if dry else {},
                "warnings": self._warnings(payload)}
