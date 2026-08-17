"""Durum Monitörü — iş kuralları.

VERİ BLD'DEDİR, HÜKÜM SUNUCUDADIR, GEÇMİŞ BURADADIR. Hata olayları, cihaz
sağlığı ve `health.status` hükmü BLD sunucusunda durur ve buraya `bld.api`
geçidinden gelir (K4); bu modül ham `httpx`, ham `subprocess` ya da doğrudan
SSH kullanmaz.

ANCAK BU MODÜL UZAK VERİDEN TÜREMİŞ VERİ SAKLAR — on iki kardeşinin hiçbiri
saklamıyor, bu saklıyor ve haklı. Gerekçe tek cümlelik: gereksinim "en ufak
hata bile loglanıp orada kalacak" diyor ve uzak taraf BÖYLE BİR GEÇMİŞ
TUTMUYOR. `veykemtu_monitor_events` yalnız BİLEŞENLERİN yazabildiği hatayı
bilir; geçidin kopması, imzanın reddedilmesi, ucun henüz dağıtılmamış olması
ve sunucunun hiç cevap vermemesi sunucuya HİÇ ULAŞMAZ. Yani tam olarak izlemek
istediğimiz arıza, uzak defterde görünmez olandır.

SAKLANANIN SINIRI KESKİN: yalnız ARAŞTIRMA SONUCU ve HATA. Sipariş, müşteri,
stok, abonelik ve fatura buraya yazılmaz; bu modülün tablolarında tek bir
kuruş, tek bir telefon numarası bulunmaz.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır:
ayrımı `connected` taşır ve panelin onu OKUMASI gerekir.

UÇ HENÜZ YAYINDA OLMAYABİLİR. Sunucu tarafı paralel yazılıyor; dağıtılmamış
bir uçta geçit `control_endpoint_missing` döndürür. Bu BEKLENEN bir durumdur
ve hata gibi gösterilmez: ekran "sunucu eklentisi güncellenince çalışacak"
der, yerel geçmiş ve düzeltme defteri çalışmaya devam eder (zarif bozulma).

YAZMA ZİNCİRİ — üç yazma ucunun üçü de bu beş adımı bu sırayla uygular:

    1. gerekçe denetimi (10–160; arayüzde zorunlu göstermek yetmez, K9)
    2. TAZE OKUMA (olay aradan çözülmüş, kasa aradan iptal edilmiş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı — `dry_run=` HER ZAMAN AÇIKÇA verilir
    5. yerel iz: `ok` / `dry_run` / `hata`

HER YAZMADA AÇIK `dry_run=`. Geçidin varsayılanına güvenilmez: `config/
local.yaml` git dışıdır ve orada `dry_run_default: true` yazıyor olabilir;
bayrağı atlayan bir çağrı hiçbir şey yazmadan `{"ok": true}` alır ve ekran
"komut gönderildi" der (`bld_api/README.md`).
"""

from __future__ import annotations

import json
from typing import Any

from . import monitor as mon

#: Sağlık hükmü DEĞİŞTİĞİNDE yayınlanan olay (manifest). Her yoklamada değil:
#: 60 saniyede bir "hâlâ iyi" diyen bir olay, dinleyicinin gerçek değişimi
#: görmesini zorlaştırırdı.
HEALTH_EVENT = "bld_status_monitor.health_changed"

#: Defterden bir düzeltme komutu kasaya gönderildiğinde yayınlanır. Kuru
#: provada YAYINLANMAZ — BLD'de hiçbir şey değişmedi.
COMMAND_EVENT = "bld_status_monitor.command_sent"

#: Olay yükleri camelCase'tir: olay yolu Kontrol Merkezi'nin KENDİ iç
#: yüzeyidir. HTTP yanıtları ise sözleşmenin snake_case sözlüğünü korur.

#: Araştırma satırının seviye karşılığı. `unknown` UYARIDIR, HATA DEĞİL:
#: soramamak bir arıza kanıtı değildir ve kırmızı yazmak, ağdaki bir
#: dalgalanmayı çökmüş bir sisteme çevirirdi.
_RESULT_LEVELS = {
    "ok": "info",
    "degraded": "warning",
    "down": "error",
    "unknown": "warning",
}

#: Uç henüz dağıtılmamışsa seviye `warning`: bu BEKLENEN bir durum (sunucu
#: tarafı paralel yazılıyor) ve `error` yazmak, gerçek arızaların arasına her
#: dakika bir yalancı kırmızı satır koyardı.
_MISSING_ENDPOINT = "control_endpoint_missing"


class StatusMonitorService:
    """Durum Monitörü ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 publish: Any = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._publish = publish

        self._events = store.table("events")
        self._runbook = store.table("runbook")
        self._audit = store.table("audit")
        self._prefs = store.table("prefs")

        #: SON BİLİNEN HÜKÜM — yalnız bellekte. Diske yazılmıyor çünkü bu bir
        #: gözlem değil, "olayı iki kez yayınlama" kilidi. Süreç yeniden
        #: başladığında `None` olur ve ilk yoklama HİÇBİR ŞEY YAYINLAMAZ:
        #: "değişti" diyebilmek için önce bir öncekini bilmek gerekir ve
        #: uydurulmuş bir önceki, her açılışta sahte bir alarm üretirdi.
        self._last_health: str | None = None

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci `dryRun` alanını HİÇ göndermezse geçerli olan varsayılan.

        VARSAYILAN KAPALI. Açık bırakmak, panelden gönderilen her komutu
        sessizce provaya çevirmek olurdu: ekran "gönderildi" der, kasa hiçbir
        şey yapmaz ve fark ancak mutfak arayınca anlaşılır.
        """
        return bool(self._config.get("dry_run_default", False))

    @property
    def _page_size(self) -> int:
        """Sayfa boyutu varsayılanı. Tavan 100 (`00-genel.md` §5)."""
        return max(5, min(100, mon.as_int(self._config.get("page_size"), 25)))

    @property
    def _poll_seconds(self) -> int:
        """Özet yoklama aralığı. `00-genel.md` §2'deki bütçe bu ekran için 60
        saniye varsayıyor (saatte 60 istek) ve tek uç yokluyoruz."""
        return max(15, min(600, mon.as_int(self._config.get("poll_seconds"), 60)))

    @property
    def _history_limit(self) -> int:
        return max(10, min(500, mon.as_int(self._config.get("history_limit"), 60)))

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçidin hata kodu. `BldApiError` import EDİLMEZ (K2/K3): başka bir
        modülün sınıfına bağlanmak, o modül yüklenmediğinde bu modülü de
        düşürürdü. Kod bir dizedir ve `getattr` ile okunur."""
        return str(getattr(failure, "code", "") or "")

    @staticmethod
    def _missing(code: str) -> bool:
        """Uç sunucuya HENÜZ DAĞITILMAMIŞ mı?

        `not_found` ile ayrı şeydir: ilki "uç var, kayıt yok", ikincisi "uç
        yayında değil, bekle" (`bld_api/README.md`). Ekran ikisini aynı
        gösterirse yönetici olmayan bir kaydı arar.
        """
        return code == _MISSING_ENDPOINT

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit sayfalı uçları `{"items": [...], "meta": {...}}` biçiminde
        açıyor; `data` ve düz dizi de kabul edilir çünkü tek bir ada bağlanmak,
        ad tutmadığında ekranı SESSİZCE boş gösterirdi.
        """
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _meta(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return dict(payload["meta"])
        return {}

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _server_time(payload: Any) -> str:
        if isinstance(payload, dict):
            return mon.text(payload.get("server_time"))
        return ""

    async def _write_audit(self, *, action: str, reason: str, actor: str, result: str,
                           target_type: str = "monitor_event", target_id: Any = "",
                           detail: Any = None) -> None:
        """Yerel yazma izi. BLD de `veykemtu_control_audit` tutuyor
        (`00-genel.md` §8); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN isteği bilir. Ağ
        koparsa "kim neyi denedi" sorusunun cevabı yalnız burada kalır.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, mon.text(target_id), action, mon.text(reason), actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), mon.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("yazma izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3). Dinleyicinin patlaması bizi
        düşürmez (K7)."""
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    # ======================================================== yerel gözlem

    async def _observe(self, *, kind: str, source: str, component: str, result: str,
                       code: str, message: str, level: str = "",
                       detail: Any = None) -> None:
        """Bir gözlemi YEREL deftere işler. Aynı gözlem ikinci satır AÇMAZ.

        Parmak izine göre birleştirme sözleşmedeki kuralın aynısıdır
        (`monitor.md` → Tekilleştirme): `occurrence_count` artar, `last_seen_at`
        ilerler, `first_seen_at` HİÇ DEĞİŞMEZ. Ekran 60 saniyede bir yokluyor;
        her yoklamayı ayrı satır yazmak defteri bir günde okunamaz hâle
        getirirdi ve "hata orada kalsın" gereksinimini de KORUMAZDI — okunamaz
        bir defter, tutulmamış bir defterdir.

        DEFTER YAZILAMAZSA EKRAN ÇALIŞMAYA DEVAM EDER (K7). Gözlem kaybolur ve
        bu kötüdür; ama izleme ekranının yazamadığı için düşmesi, izlediği
        sistemden önce kendisinin çökmesi olurdu.
        """
        stamp = mon.now_iso()
        finger = mon.fingerprint(source=source, code=code, message=message)
        body = json.dumps(detail or {}, ensure_ascii=False)
        tone = level or _RESULT_LEVELS.get(result, "warning")
        try:
            existing = await self._store.fetch_one(
                f"SELECT id, occurrence_count FROM {self._events} WHERE fingerprint = ?",
                (finger,))
            if existing:
                await self._store.execute(
                    f"UPDATE {self._events} SET occurrence_count = occurrence_count + 1, "
                    "last_seen_at = ?, message = ?, level = ?, result = ?, detail = ? "
                    "WHERE fingerprint = ?",
                    (stamp, mon.text(message), tone, result, body, finger))
                return
            await self._store.execute(
                f"INSERT INTO {self._events} "
                "(kind, source, component, level, code, message, result, detail, "
                "fingerprint, occurrence_count, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (kind, source, component, tone, mon.text(code), mon.text(message),
                 result, body, finger, stamp, stamp))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yerel gözlem yazılamadı", source=source,
                              code=code, error=str(failure))

    async def _observe_fault(self, failure: Exception, *, what: str) -> None:
        """Araştırmanın KENDİSİ başarısız oldu — sunucuya hiç ulaşmayan hata.

        Bu satırın var olma sebebi tam olarak budur: uzak defterde karşılığı
        YOKTUR ve olamaz. Kaynak `kontrol_merkezi`dir; kopukluğu bir bileşenin
        arızası gibi yazmak dört kutuyu birden kırmızıya boyayıp asıl sorunu
        (ağ) gizlerdi.
        """
        code = self._code(failure) or "unknown_error"
        await self._observe(
            kind="fault", source="kontrol_merkezi", component="", result="unknown",
            code=code, message=f"{what}: {self._fail(failure)}",
            # Uç henüz dağıtılmamışsa bu BEKLENEN bir durum; her dakika bir
            # kırmızı satır, gerçek arızaları görünmez kılardı.
            level="warning" if self._missing(code) else "error",
            detail={"what": what, "code": code})

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Panel açılışı — SÖZLEŞME VE TERCİH, ağa çıkmaz.

        Kutuların, süzgeçlerin ve rozetlerin çizilmesi için gereken her şey
        burada ve hiçbiri sunucudan gelmiyor: geçit düşükken bile ekran
        kurulabilsin (K7). İzleme ekranının sunucuya ulaşamadığı için hiç
        açılmaması, sorunun kendisini görünmez yapardı.
        """
        return {
            "ok": True,
            "connected": None,   # bu uç ağa çıkmaz; bağlantı özetten anlaşılır
            "error": "",
            "contract": mon.screen_contract(),
            "prefs": await self.prefs(),
            "limits": {
                "page_size": self._page_size,
                "poll_seconds": self._poll_seconds,
                "history_limit": self._history_limit,
            },
        }

    async def summary(self) -> dict[str, Any]:
        """Dört kutu + sunucunun tek cümlelik hükmü. TEK UÇ YOKLANIR.

        `GET /summary` gövdesi zaten `devices` bloğunu taşıyor; kutuları
        çizmek için ayrıca `/devices` çağırmak yoklama başına ikinci bir istek
        demekti ve `00-genel.md` §2'deki bütçe bu ekran için TEK uç varsayıyor
        (saatte 60). Kasa LİSTESİ ayrı bir uçta ve yalnız kullanıcı sekmeyi
        açınca okunur.

        Her çağrı yerel deftere işlenir: dört bileşen için birer `probe`
        satırı, ulaşılamadıysa bir `fault` satırı.
        """
        try:
            payload = await self._api.monitor_summary()
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            self._log.warning("izleme özeti okunamadı", error=str(failure))
            await self._observe_fault(failure, what="İzleme özeti okunamadı")
            empty = mon.summary_view({})
            return {
                "ok": True, "connected": False, "error": self._fail(failure),
                "code": code, "endpoint_missing": self._missing(code),
                "summary": empty,
                "tiles": mon.component_tiles(empty, {}, connected=False),
                "health_changed": False,
            }

        view = mon.summary_view(payload)
        tiles = mon.component_tiles(view, view["devices"], connected=True)

        for tile in tiles:
            await self._observe(
                kind="probe", source=str(tile["source"]), component=str(tile["key"]),
                result=str(tile["status"]), code=f"probe_{tile['status']}",
                message="; ".join(tile["notes"]) or str(tile["status_label"]),
                detail={"open_events": tile["open_events"]})

        status = str(view["health"]["status"])
        previous = self._last_health
        changed = previous is not None and previous != status
        self._last_health = status
        if changed:
            await self._announce(HEALTH_EVENT, {
                "status": status, "previous": previous,
                "reasons": view["health"]["reasons"],
                "sources": view["events"]["by_source"], "actor": "",
            })

        return {
            "ok": True, "connected": True, "error": "", "code": "",
            "endpoint_missing": False,
            "summary": view, "tiles": tiles,
            "health_changed": changed,
            "previous_health": previous,
            "server_time": self._server_time(payload),
        }

    async def devices(self) -> dict[str, Any]:
        """Kasa sağlık özeti — `control/kds/devices` ucunun DAR bir yüzü.

        Ayar, komut ve eşleme bilgisi taşımaz: bu ekranı `bld_status_monitor.
        view` ile açan kişinin cihaz ayarlarını görmesi gerekmiyor. Kasa
        YÖNETİMİ `bld_kds` modülünün işidir ve burada kapısı yoktur.
        """
        try:
            payload = await self._api.monitor_devices()
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            self._log.warning("kasa sağlığı okunamadı", error=str(failure))
            await self._observe_fault(failure, what="Kasa sağlığı okunamadı")
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": code, "endpoint_missing": self._missing(code),
                    "items": [], "meta": {}}

        return {"ok": True, "connected": True, "error": "", "code": "",
                "endpoint_missing": False,
                "items": [mon.device_row(raw) for raw in self._items(payload)],
                "meta": self._meta(payload),
                "server_time": self._server_time(payload)}

    async def events(self, *, source: str = "", level: str = "", code: str = "",
                     device_id: int = 0, since: str = "", resolved: str = "",
                     q: str = "", page: int = 1, per_page: int = 0) -> dict[str, Any]:
        """Sunucudaki hata olayları — SAYFALI.

        `level` varsayılanı `warning,error,critical`: `info` seviyesindeki
        olaylar sayıca en kalabalık olanlardır ve listeyi doldurup gerçek
        hataları görünmez kılarlar (sözleşme). Varsayılan burada da tekrar
        edilir çünkü panel süzgeci temizlediğinde sunucunun sessiz
        varsayılanına düşmek, ekranda hiçbir yerde yazmayan bir süzgeç
        demekti.
        """
        sources, problem = mon.csv_filter(source, mon.SOURCES, field="kaynak")
        if problem:
            return {"ok": False, "connected": None, "error": problem,
                    "items": [], "meta": {}}
        levels, problem = mon.csv_filter(level, mon.LEVELS, field="seviye")
        if problem:
            return {"ok": False, "connected": None, "error": problem,
                    "items": [], "meta": {}}
        problem = mon.choice_error(resolved, mon.RESOLVED_FILTERS, field="Çözüm süzgeci")
        if problem:
            return {"ok": False, "connected": None, "error": problem,
                    "items": [], "meta": {}}
        problem = mon.since_error(since)
        if problem:
            return {"ok": False, "connected": None, "error": problem,
                    "items": [], "meta": {}}

        size = max(5, min(100, int(per_page) or self._page_size))
        try:
            payload = await self._api.monitor_events(
                source=sources or None, level=levels or list(mon.DEFAULT_LEVELS),
                code=mon.text(code), device_id=int(device_id) or None,
                since=mon.text(since), resolved=mon.text(resolved) or "false",
                q=mon.text(q), page=max(1, int(page)), per_page=size)
        except Exception as failure:  # noqa: BLE001 — K7
            failure_code = self._code(failure)
            self._log.warning("hata olayları okunamadı", error=str(failure))
            await self._observe_fault(failure, what="Hata olayları okunamadı")
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": failure_code, "endpoint_missing": self._missing(failure_code),
                    "items": [], "meta": {}}

        meta = self._meta(payload)
        return {
            "ok": True, "connected": True, "error": "", "code": "",
            "endpoint_missing": False,
            "items": [mon.event_row(raw) for raw in self._items(payload)],
            "meta": {**meta,
                     "page": mon.as_int(meta.get("page"), max(1, int(page))),
                     "per_page": mon.as_int(meta.get("per_page"), size)},
            "server_time": self._server_time(payload),
        }

    async def event(self, event_id: int) -> dict[str, Any]:
        """Tek olay + `context` + `related`.

        `related` bloğu olay bir cihaza bağlıysa o cihazın ŞU ANKİ sağlığını
        taşır (sözleşme): olay 05:12'de kaydedildi, yönetici 09:00'da bakıyor
        ve asıl merak ettiği "hâlâ bozuk mu" sorusu. Blok OLDUĞU GİBİ geçer;
        ikinci bir cihaz çağrısı yapılmaz.
        """
        try:
            payload = await self._api.monitor_event(int(event_id))
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            self._log.warning("olay okunamadı", eventId=event_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure),
                    "code": code, "endpoint_missing": self._missing(code), "event": {}}

        raw = self._record_of(payload, "data", "event")
        if not raw:
            return {"ok": False, "connected": True, "error": "Olay kaydı boş döndü.",
                    "event": {}}
        return {"ok": True, "connected": True, "error": "", "code": "",
                "event": mon.event_row(raw),
                "server_time": self._server_time(payload)}

    async def local_log(self, *, source: str = "", result: str = "", kind: str = "",
                        q: str = "", limit: int = 0) -> dict[str, Any]:
        """YEREL araştırma defteri — sunucununki değil.

        Buradaki satırlar sunucuya HİÇ ULAŞMAMIŞ gözlemleri de içerir; tam
        olarak bu yüzden var. Süzme SQL'de değil bellekte yapılıyor: defter
        parmak iziyle birleştiği için satır sayısı yüzlerle ölçülür ve tek
        `LIMIT` sorgusu her koşulda okunabilir bir küme döndürür.
        """
        sources, problem = mon.csv_filter(source, mon.SOURCES, field="kaynak")
        if problem:
            return {"ok": False, "connected": None, "error": problem, "items": []}
        problem = mon.choice_error(result, mon.LOCAL_RESULTS, field="Sonuç süzgeci")
        if problem:
            return {"ok": False, "connected": None, "error": problem, "items": []}
        problem = mon.choice_error(kind, mon.LOCAL_KINDS, field="Kayıt türü")
        if problem:
            return {"ok": False, "connected": None, "error": problem, "items": []}

        count = max(1, min(500, int(limit) or self._history_limit))
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, kind, source, component, level, code, message, result, "
                f"detail, fingerprint, occurrence_count, first_seen_at, last_seen_at "
                f"FROM {self._events} ORDER BY last_seen_at DESC, id DESC LIMIT ?",
                (count,))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yerel defter okunamadı", error=str(failure))
            return {"ok": True, "connected": None, "error": "", "items": []}

        needle = mon.foldable(q)
        items = []
        for raw in rows:
            row = mon.local_row(raw)
            if sources and row["source"] not in sources:
                continue
            if result and row["result"] != result:
                continue
            if kind and row["kind"] != kind:
                continue
            if needle and needle not in mon.foldable(f"{row['message']} {row['code']}"):
                continue
            items.append(row)
        return {"ok": True, "connected": None, "error": "", "items": items}

    async def history(self, *, limit: int = 0) -> dict[str, Any]:
        """Olay geçmişi — zaman çizelgesi için ESKİDEN YENİYE sıralı akış.

        İki defter birleştirilir: yerel gözlemler ("sistem ne durumdaydı") ve
        yerel yazma izi ("kim ne yaptı"). İkisini ayrı çizelgede göstermek,
        "kasayı yeniden başlattık ve on dakika sonra düzeldi" cümlesini ekranda
        kurmayı imkânsız kılardı — o cümle bu ekranın en çok işe yarayan
        çıktısı.

        SIRA ESKİDEN YENİYE: kit `timeline` yolculuğu yukarıdan aşağı okutuyor
        ve son satır "nereye kadar geldik" sorusunu cevaplıyor.
        """
        count = max(10, min(500, int(limit) or self._history_limit))
        events: list[dict[str, Any]] = []

        try:
            rows = await self._store.fetch_all(
                f"SELECT id, kind, source, component, level, code, message, result, "
                f"detail, fingerprint, occurrence_count, first_seen_at, last_seen_at "
                f"FROM {self._events} ORDER BY last_seen_at DESC, id DESC LIMIT ?",
                (count,))
            events.extend(mon.timeline_event(mon.local_row(raw)) for raw in rows)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yerel defter okunamadı", error=str(failure))

        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_type, target_id, action, reason, actor, result, "
                f"detail, created_at FROM {self._audit} ORDER BY id DESC LIMIT ?",
                (count,))
            events.extend(_audit_timeline(raw) for raw in rows)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yazma izi okunamadı", error=str(failure))

        # Damgası okunamayan satır SONA değil BAŞA konur: bilinmeyen bir zamanı
        # "şimdi" saymak, çizelgenin son satırını yalan yapardı.
        events.sort(key=lambda item: mon.text(item.get("at")))
        return {"ok": True, "connected": None, "error": "", "items": events[-count:]}

    async def audit(self, *, limit: int = 50) -> dict[str, Any]:
        """Bu EKRANDAN yapılan yazma denemelerinin YEREL izi."""
        count = max(1, min(500, int(limit) or 50))
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_type, target_id, action, reason, actor, result, "
                f"detail, created_at FROM {self._audit} ORDER BY id DESC LIMIT ?",
                (count,))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("yazma izi okunamadı", error=str(failure))
            return {"ok": True, "connected": None, "error": "", "items": []}

        items = []
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except (TypeError, ValueError):
                detail = {}
            items.append({
                "id": mon.as_int(row["id"]),
                "target_type": mon.text(row["target_type"]),
                "target_id": mon.text(row["target_id"]),
                "action": mon.text(row["action"]),
                "reason": mon.text(row["reason"]),
                "actor": mon.text(row["actor"]),
                "result": mon.text(row["result"]),
                "detail": detail,
                "created_at": mon.text(row["created_at"]),
            })
        return {"ok": True, "connected": None, "error": "", "items": items}

    # ------------------------------------------------------ ekran tercihi

    #: Yazılabilen tercihler. Kapalı liste: tanınmayan bir anahtarı kabul
    #: etmek, yazım hatasını sessizce diske yazıp hiçbir yerde kullanmamak
    #: olurdu.
    PREF_KEYS = ("poll_seconds", "page_size", "auto_refresh")

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı."""
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception as failure:  # noqa: BLE001 — varsayılan yeter (K7)
            self._log.warning("tercih okunamadı", error=str(failure))

        auto = mon.as_bool(stored.get("auto_refresh"))
        return {
            "poll_seconds": max(15, min(600, mon.as_int(stored.get("poll_seconds"),
                                                        self._poll_seconds))),
            "page_size": max(5, min(100, mon.as_int(stored.get("page_size"),
                                                    self._page_size))),
            "auto_refresh": True if auto is None else auto,
        }

    async def save_prefs(self, values: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Görüntüleme tercihini yazar. BLD'YE HİÇBİR ŞEY GİTMEZ.

        Gerekçe İSTENMEZ: yoklama aralığını değiştirmek bir iş yazması
        değildir ve kimse o gerekçeyi denetim izinde aramaz.
        """
        unknown = sorted(set(values or {}) - set(self.PREF_KEYS))
        if unknown:
            return {"ok": False, "error": f"Tanınmayan tercih: {', '.join(unknown)}."}

        stamp = mon.now_iso()
        for key, value in (values or {}).items():
            raw = "true" if value is True else "false" if value is False else mon.text(value)
            try:
                await self._store.execute(
                    f"INSERT INTO {self._prefs} (key, value, actor, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, actor = excluded.actor, "
                    "updated_at = excluded.updated_at",
                    (key, raw, actor, stamp))
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("tercih yazılamadı", key=key, error=str(failure))
                return {"ok": False, "error": f"Tercih yazılamadı: {failure}"}
        return {"ok": True, "error": "", "prefs": await self.prefs()}

    # ============================================================ defter

    async def runbook(self) -> dict[str, Any]:
        """Düzeltme defteri — "bu hata çıkınca ne yapıyoruz" sorusunun cevabı.

        Pasifleştirilmiş kayıtlar da DÖNER ve öyle işaretlenir: silinmiş gibi
        gizlemek, denetim izindeki anahtarın karşılığını okunamaz kılardı.
        """
        try:
            rows = await self._store.fetch_all(
                f"SELECT key, title, description, channel, action, device_id, enabled, "
                f"actor, created_at, updated_at FROM {self._runbook} "
                f"ORDER BY enabled DESC, key ASC")
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("defter okunamadı", error=str(failure))
            return {"ok": True, "connected": None, "error": "", "items": []}
        return {"ok": True, "connected": None, "error": "",
                "items": [mon.runbook_row(raw) for raw in rows]}

    async def save_runbook(self, key: str, *, title: str, description: str, channel: str,
                           action: str, device_id: int, enabled: bool, reason: str,
                           actor: str) -> dict[str, Any]:
        """Defter kaydı yazar ya da günceller. BLD'YE HİÇBİR ŞEY GİTMEZ.

        GEREKÇE YİNE DE İSTENİR ve bu, ekran tercihinden ayrıldığı yerdir: bu
        tablo neyin ÇALIŞTIRILABİLECEĞİNİ tanımlıyor. Deftere "bütün kasaları
        yeniden başlat" satırı ekleyen birinin izi, komutu çalıştıranın izi
        kadar önemlidir — ikincisi olmadan birincisi anlaşılmaz.

        SİLME YOK, PASİFLEŞTİRME VAR (`enabled = false`).
        """
        problem = mon.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        problem = mon.runbook_error(key=key, title=title, channel=channel,
                                    action=action, device_id=device_id)
        if problem:
            return {"ok": False, "error": problem}

        clean_key = mon.text(key)
        clean_action = mon.text(action) or mon.MANUAL_ACTION
        stamp = mon.now_iso()
        try:
            await self._store.execute(
                f"INSERT INTO {self._runbook} (key, title, description, channel, action, "
                "device_id, enabled, actor, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "title = excluded.title, description = excluded.description, "
                "channel = excluded.channel, action = excluded.action, "
                "device_id = excluded.device_id, enabled = excluded.enabled, "
                "actor = excluded.actor, updated_at = excluded.updated_at",
                (clean_key, mon.text(title), mon.text(description)[:1000],
                 mon.text(channel), clean_action, mon.as_int(device_id),
                 1 if enabled else 0, actor, stamp, stamp))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("defter yazılamadı", key=clean_key, error=str(failure))
            await self._write_audit(action="runbook.save", reason=reason, actor=actor,
                                    result=mon.FAILED, target_type="runbook",
                                    target_id=clean_key, detail={"error": str(failure)})
            return {"ok": False, "error": f"Defter kaydı yazılamadı: {failure}"}

        await self._write_audit(action="runbook.save", reason=reason, actor=actor,
                                result=mon.DONE, target_type="runbook",
                                target_id=clean_key,
                                detail={"channel": mon.text(channel),
                                        "action": clean_action,
                                        "device_id": mon.as_int(device_id),
                                        "enabled": bool(enabled)})
        return {"ok": True, "error": "", "runbook": (await self.runbook())["items"]}

    async def run_runbook(self, key: str, *, reason: str, actor: str,
                          dry_run: bool | None = None,
                          allow_manage: bool = False) -> dict[str, Any]:
        """Defterdeki bir düzeltme komutunu kasaya gönderir.

        KOMUT `bld.api` GEÇİDİNDEN GEÇER (K4). Ham `subprocess` yoktur: "hızlıca
        bir komut çalıştıralım" diyen bir izleme ekranı imzayı, oran sınırını ve
        denetim izini birden atlardı.

        EYLEM ADI KAPALI LİSTEDEN ÇÖZÜLÜR. Defter satırı bir veritabanı
        kaydıdır; oradan okunan adı `getattr(api, name)` ile çağırmak, deftere
        yazabilen birine geçidin bütün metotlarını açardı (`cancel_order`,
        `void_invoice`, `run_sms_announcement` dâhil).
        """
        if not allow_manage:
            # ÇİFT KAPI (K9): uç noktada da denetleniyor. Arayüzde düğmeyi
            # gizlemek yetkilendirme değildir; istemci gövdeyi elle kurabilir.
            await self._write_audit(action="runbook.run", reason=reason, actor=actor,
                                    result=mon.BLOCKED, target_type="runbook",
                                    target_id=key, detail={"why": "izin yok"})
            return {"ok": False, "error": "Düzeltme komutu göndermek ayrı bir yetki "
                                          "ister (`bld_status_monitor.manage`)."}

        problem = mon.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        # TAZE OKUMA: defter aradan değişmiş, kayıt pasifleştirilmiş olabilir.
        entry = await self._runbook_entry(key)
        if entry is None:
            return {"ok": False, "error": f"'{key}' defterde yok."}
        if not entry["enabled"]:
            return {"ok": False, "error": f"'{entry['title']}' pasifleştirilmiş; "
                                          "çalıştırmadan önce yeniden etkinleştirin."}
        if not entry["runnable"]:
            # ÇALIŞMAYAN DÜĞME BIRAKILMAZ, ama istemci gövdeyi elle kurabilir:
            # sebep burada da yazılır.
            return {"ok": False, "error": (
                f"'{entry['title']}' elle yapılan bir adım ({entry['channel_label']}); "
                "Kontrol Merkezi'nden çalıştırılamaz. Kabuk erişimi gerektiren "
                "adımlar için `ssh` platform yeteneği henüz yazılmadı.")}

        spec = mon.RUNBOOK_ACTIONS[entry["action"]]
        device_id = mon.as_int(entry["device_id"])
        dry = self._dry(dry_run)
        detail = {"action": spec.key, "command": spec.command, "device_id": device_id,
                  "destructive": spec.destructive, "dry_run": dry}
        await self._write_audit(action="runbook.run", reason=reason, actor=actor,
                                result=mon.TRIED, target_type="runbook",
                                target_id=entry["key"], detail=detail)

        try:
            # GÖVDEYİ GEÇİT KURAR ve `dry_run` AÇIKÇA geçer. Yüksüz komutta
            # `payload` gönderilmez: sözleşmede olmayan bir alan eklenmez.
            result = await self._api.send_command(
                device_id, command=spec.command, reason=mon.text(reason), actor=actor,
                dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            await self._write_audit(action="runbook.run", reason=reason, actor=actor,
                                    result=mon.FAILED, target_type="runbook",
                                    target_id=entry["key"],
                                    detail={**detail, "error": str(failure)})
            await self._observe_fault(failure, what=f"Düzeltme komutu: {entry['title']}")
            message = self._fail(failure)
            if self._missing(code):
                message = (f"{message} Komut ucu sunucuya henüz dağıtılmamış; "
                           "eklenti güncellenince çalışacak.")
            return {"ok": False, "error": message, "code": code,
                    "endpoint_missing": self._missing(code)}

        await self._write_audit(action="runbook.run", reason=reason, actor=actor,
                                result=mon.DRY if dry else mon.DONE,
                                target_type="runbook", target_id=entry["key"],
                                detail={**detail,
                                        "audit_id": mon.as_int(result.get("audit_id"))
                                        if isinstance(result, dict) else 0})
        if dry:
            return {"ok": True, "error": "", "dry_run": True, "announced": False,
                    "would": result.get("would") if isinstance(result, dict) else None,
                    "entry": entry}

        # KURU PROVADA OLAY YAYINLANMAZ: kasaya hiçbir şey gitmedi.
        await self._announce(COMMAND_EVENT, {
            "key": entry["key"], "action": spec.key, "deviceId": device_id,
            "reason": mon.text(reason), "actor": actor, "dryRun": False,
        })
        return {"ok": True, "error": "", "dry_run": False, "announced": True,
                "command": self._record_of(result, "data", "command"), "entry": entry}

    async def _runbook_entry(self, key: str) -> dict[str, Any] | None:
        try:
            row = await self._store.fetch_one(
                f"SELECT key, title, description, channel, action, device_id, enabled, "
                f"actor, created_at, updated_at FROM {self._runbook} WHERE key = ?",
                (mon.text(key),))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("defter kaydı okunamadı", key=key, error=str(failure))
            return None
        return mon.runbook_row(row) if row else None

    # ================================================================= yazma

    async def resolve_event(self, event_id: int, *, reason: str, actor: str,
                            note: str = "", dry_run: bool | None = None,
                            allow_manage: bool = False) -> dict[str, Any]:
        """Sunucudaki hata olayını çözüldü işaretler.

        SİLME YOKTUR ve olmayacaktır (sözleşme): bir hata kaydını silmek, o
        hatanın hiç olmadığını iddia etmektir. Çözülen olay `resolved_at` ile
        işaretlenir ve varsayılan listeden düşer.

        OLAY YENİDEN GELİRSE OTOMATİK YENİDEN AÇILIR ve çözüm notu SİLİNMEZ —
        "geçen sefer ne yapılmıştı" bilgisi, aynı hatanın ikinci kez teşhisinde
        en kısa yoldur. Bu kural sunucudadır ve burada tekrarlanmaz.
        """
        if not allow_manage:
            await self._write_audit(action="monitor.resolve", reason=reason, actor=actor,
                                    result=mon.BLOCKED, target_id=event_id,
                                    detail={"why": "izin yok"})
            return {"ok": False, "error": "Olayı çözüldü işaretlemek ayrı bir yetki "
                                          "ister (`bld_status_monitor.manage`)."}

        problem = mon.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean_note = mon.text(note)
        if len(clean_note) > mon.MAX_NOTE:
            return {"ok": False, "error": f"Not en çok {mon.MAX_NOTE} karakter olabilir."}
        dry = self._dry(dry_run)

        # TAZE OKUMA: olay aradan başka biri tarafından çözülmüş olabilir ve
        # sunucu ikinci çözümü 409 ile reddediyor. Erken okumak, kullanıcıya
        # ham bir çakışma hatası yerine kendi cümlesini göstermek demektir.
        fresh = await self.event(event_id)
        if not fresh.get("ok"):
            return {"ok": False, "error": fresh.get("error") or "Olay okunamadı.",
                    "code": fresh.get("code", "")}
        current = fresh["event"]
        if current.get("resolved"):
            return {"ok": False, "error": (
                "Bu olay zaten çözüldü işaretlenmiş "
                f"({mon.text(current.get('resolved_by_actor')) or 'bilinmeyen kişi'}). "
                "İkinci bir çözüm notu ilkini gizlerdi.")}

        detail = {"level": mon.text(current.get("level")),
                  "source": mon.text(current.get("source")),
                  "code": mon.text(current.get("code")), "dry_run": dry}
        await self._write_audit(action="monitor.resolve", reason=reason, actor=actor,
                                result=mon.TRIED, target_id=event_id, detail=detail)

        try:
            result = await self._api.resolve_monitor_event(
                int(event_id), note=clean_note, reason=mon.text(reason), actor=actor,
                dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            await self._write_audit(action="monitor.resolve", reason=reason, actor=actor,
                                    result=mon.FAILED, target_id=event_id,
                                    detail={**detail, "error": str(failure)})
            message = self._fail(failure)
            if code == "conflict":
                message = f"{message} Olay aradan çözülmüş olabilir; listeyi tazeleyin."
            return {"ok": False, "error": message, "code": code,
                    "endpoint_missing": self._missing(code)}

        await self._write_audit(action="monitor.resolve", reason=reason, actor=actor,
                                result=mon.DRY if dry else mon.DONE, target_id=event_id,
                                detail={**detail,
                                        "audit_id": mon.as_int(result.get("audit_id"))
                                        if isinstance(result, dict) else 0})
        data = self._record_of(result, "data")
        return {"ok": True, "error": "", "dry_run": dry,
                "would": result.get("would") if isinstance(result, dict) and dry else None,
                "event": mon.event_row({**current, **data}) if data and not dry
                else current}


def _audit_timeline(raw: dict[str, Any]) -> dict[str, Any]:
    """Yazma izi satırını zaman çizelgesi öğesine çevirir.

    SONUÇ TONU YAZIYLA DA SÖYLENİR: `denendi` satırı "sonucu bilinmiyor"
    demektir ve gri değil SARI çizilir — cevabı gelmemiş bir komut, başarısız
    olmuş bir komuttan daha acildir (kasa onu çalıştırmış olabilir).
    """
    tones = {mon.DONE: "good", mon.DRY: "info", mon.TRIED: "warn",
             mon.BLOCKED: "dim", mon.FAILED: "bad"}
    labels = {mon.DONE: "uygulandı", mon.DRY: "kuru prova", mon.TRIED: "denendi",
              mon.BLOCKED: "engellendi", mon.FAILED: "başarısız"}
    result = mon.text(raw.get("result"))
    actor = mon.text(raw.get("actor")) or "bilinmeyen kişi"
    target = mon.text(raw.get("target_id"))
    return {
        "title": f"{mon.text(raw.get('action'))} — {labels.get(result, result or '—')}",
        "detail": f"{actor} · {target}".strip(" ·") + (
            f" · {mon.text(raw.get('reason'))}" if mon.text(raw.get("reason")) else ""),
        "at": mon.text(raw.get("created_at")),
        "tone": tones.get(result, "dim"),
    }
