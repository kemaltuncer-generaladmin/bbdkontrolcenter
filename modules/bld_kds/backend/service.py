"""KDS Yönetimi — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Cihaz, komut, sipariş ve fiş kaydı BLD
sunucusunda durur ve buraya `bld.api` geçidinden gelir (K4); bu modül ham httpx
kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel tablolar yalnız BLD'de
KARŞILIĞI OLMAYAN üç şeyi saklar: yazma gerekçesi (denetim izi), ayar itme
önizlemesi ve bu ekrana özel tercihler.

Neden kopya tutulmuyor: kasa saniyede bir yoklama yapıyor, sipariş durumu ve
cihaz sağlığı sürekli değişiyor. Yerel bir kopya her zaman bir tur geride olur
ve "çevrimiçi" görünen bir kasa aslında yarım saattir kapalı olabilir.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. Bu ekranda K7'nin ayrı bir ağırlığı var — mutfak çalışmaya devam ediyor
ve yönetici "sunucuya ulaşılamıyor" ile "cihaz kapandı" arasındaki farkı GÖRMEK
zorunda; boş bir liste ikisini de aynı gösterirdi.

`ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır: uç
çalıştı, cevabı "bağlanamadım"dır. Ayrımı taşıyan alan `connected`'dır ve
ekranın onu OKUMASI gerekir — yalnız `ok`a bakan bir panel geçit düştüğünde
"kayıt yok" der ve K7'nin engellemek için var olduğu yalanı söyler.

ANAHTAR ADLARI. Yanıtlar ve gövdeler K-21'in snake_case sözlüğünü korur; TEK
İSTİSNA gövdedeki `dryRun` alanıdır (panel→Kontrol Merkezi sınırında camelCase,
`store_orders` deseni). Çeviri tek yerde yapılır: `dryRun` → geçidin `dry_run`
argümanı. İki adı birden kabul etmek, yanlış yazılmış bir çağrının kuru prova
sanılıp GERÇEK YAZMA yapmasına yol açardı.

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe denetimi (min 10 — arayüzde zorunlu göstermek yetmez, K9)
    2. TAZE OKUMA (cihaz/sipariş aradan değişmiş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı
    5. yerel iz: `ok` / `dry_run` / `hata`

Üçüncü adım kritiktir: "ne yapmaya çalıştık" kaydı, çağrı yarıda kaldığında
tek kanıttır. `restart` komutu gönderilirken ağ koparsa komutun kuyruğa girip
girmediği bilinmez; iz olmasa kimin denediği de bilinmezdi.

KURU PROVA VARSAYILAN KAPALIDIR (K-22 §4). Şalter arayüzden kaldırıldı ve panel
`dryRun` alanını artık hiç göndermiyor; varsayılan açık kalsaydı panelden yapılan
her yazma sessizce bir provaya döner, ekran "yazıldı" der ve kasada hiçbir şey
değişmezdi. Uç ve geçit `dryRun: true` KABUL ETMEYE DEVAM EDER — sözleşme §4
additive'dir ve kaldırmak alanı açıkça gönderen eski çağrıları kırardı.

GEREKÇE ZORUNLULUĞU DURUYOR (min 10). Kuru prova bir güvenlik ağıydı, gerekçe
ise denetim kaydıdır; ikisi ayrı şeylerdir ve biri kalktı diye öteki kalkmaz.

Kuru provada OLAY YAYINLANMAZ: BLD'de hiçbir şey değişmedi, dinleyicileri
"cihaz iptal edildi" diye uyandırmak yalan olurdu.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from . import devices as dev

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Cihaz iptal edildiğinde yayınlanan olay (manifest). Kuru provada YAYINLANMAZ.
REVOKED_EVENT = "bld_kds.device_revoked"

#: Sipariş durumu bu ekrandan değiştirildiğinde yayınlanan olay (manifest).
STATUS_EVENT = "bld_kds.order_status_changed"

#: Olay yükleri camelCase'tir: olay yolu Kontrol Merkezi'nin KENDİ iç yüzeyidir
#: ve `store.order.status_changed` bu biçimi kurdu. HTTP yanıtları ise K-21
#: sözleşmesinin snake_case sözlüğünü korur (`devices.py` başlığı).

#: Ayar önizlemesinin varsayılan ömrü (dakika).
PREVIEW_TTL_MINUTES = 30


class KdsService:
    """KDS Yönetimi ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish

        self._audit = store.table("audit")
        self._preview = store.table("settings_preview")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """Kuru prova varsayılanı. İstemci bayrağı HİÇ göndermezse bu geçerlidir.

        VARSAYILAN KAPALI (K-22 §4). Panel şalteri kaldırıldı ve `dryRun` alanı
        artık hiç gönderilmiyor; açık bırakmak, panelden yapılan HER yazmayı
        sessizce provaya çevirmek olurdu. Yedek değer de `False`: ayar dosyası
        okunamadığında ekranın "yazıldı" deyip hiçbir şey yazmaması, açık bir
        hatadan çok daha pahalıdır.
        """
        return bool(self._config.get("dry_run_default", False))

    @property
    def _command_limit(self) -> int:
        return max(1, min(50, dev.as_int(self._config.get("command_limit"), 50)))

    @property
    def _print_job_limit(self) -> int:
        """İstek limit vermezse kullanılacak VARSAYILAN — tavan değil.

        Tavan sayılsaydı 200 satır isteyen panel sessizce 100 alırdı ve
        eksikliği yalnız listenin dibine bakan biri fark ederdi. Gerçek tavan
        uç noktanın şema kapısında (`le=500`) durur.
        """
        return max(1, min(500, dev.as_int(self._config.get("print_job_limit"), 100)))

    @property
    def _stale_command_minutes(self) -> int:
        return max(1, dev.as_int(self._config.get("stale_command_minutes"),
                                 dev.STALE_AFTER_MINUTES))

    @property
    def _preview_ttl(self) -> int:
        return max(1, dev.as_int(self._config.get("settings_preview_ttl_minutes"),
                                 PREVIEW_TTL_MINUTES))

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------ yerel tablolar

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler BLD'yi ETKİLEMEZ; yalnız bu ekranın ne gösterdiğini belirler.
        """
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))
        return {
            "command_limit": dev.as_int(stored.get("command_limit"), self._command_limit),
            "print_job_limit": dev.as_int(stored.get("print_job_limit"),
                                          self._print_job_limit),
            "include_completed": dev.as_bool(stored.get("include_completed")) or False,
        }

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_type: str = "device", target_id: int = 0,
                      detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor (K-21 §3);
        bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN istekleri bilir. Ağ
        koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim neyi denedi"
        sorusunun cevabı yalnız burada kalır.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, int(target_id or 0), action, dev.text(reason), actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), dev.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde zorunlu göstermek,
        istemcinin gövdeyi elle kurmasını engellemez."""
        return dev.reason_error(reason)

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: cihaz BLD'de iptal edilmiştir,
        dinleyicinin patlaması onu geri getirmez (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    async def _fresh_device(self, device_id: int) -> tuple[dict[str, Any] | None, str]:
        """Cihazın TAZE hâli. `(satır, hata)` döner.

        Sözleşmede tek cihaz okuyan bir uç YOK (K-21 §2.1); taze okuma listeden
        yapılır. Tek istek eder ve karşılığında yazmadan hemen önceki gerçek
        durumu verir — cihaz aradan iptal edilmiş, adı değişmiş ya da ayarı
        başka biri tarafından yazılmış olabilir.
        """
        try:
            payload = await self._api.devices()
        except Exception as failure:  # noqa: BLE001 — K7
            return None, self._fail(failure)
        for raw in self._items(payload):
            if dev.as_int(raw.get("id")) == int(device_id):
                return dev.device_row(raw), ""
        return None, "Cihaz bulunamadı."

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit zarfı ZATEN AÇIYOR (`BldApi._items`) ve düz liste veriyor; bu
        yüzden buradaki iş normalde tek satırlık. `{"data": …}` ve
        `{"items": …}` biçimleri de kabul edilir, çünkü geçidin bir metodu
        (`menu` gibi) zarfı açmadan geçebilir ve tek bir ada bağlanmak, ad
        tutmadığında ekranı SESSİZCE boş gösterirdi.
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
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Geçidin tekil yanıtından kaydı çıkarır (`{"data": {...}}` ya da düz)."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Panel açılışı: cihaz sayıları, sipariş sayıları, fiş kuyruğu."""
        empty = dev.overview_view({})
        try:
            payload = await self._api.overview()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("özet okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    **empty, "printer_available": self._printer is not None}
        return {"ok": True, "connected": True, "error": "",
                **dev.overview_view(self._record_of(payload, "data", "overview")),
                # Yazıcı yeteneği İSTEĞE BAĞLIDIR (K7): yoksa ekran çalışmaya
                # devam eder, yalnız baskı sunan bölümler kapanır.
                "printer_available": self._printer is not None}

    async def devices(self, *, can_settings: bool = False) -> dict[str, Any]:
        """Cihaz listesi + panelin ayar formunu çizmek için okuduğu sözleşme.

        `can_settings` OTURUMUN `bld_kds.settings` İZNİDİR ve uçtan gelir.
        Yetkilendirme DEĞİLDİR — kapı `PATCH .../settings` ucundadır (K9); bu
        değer panelin ayar formunu salt okunur çizmesi ve çalışmayacak bir
        düğme bırakmaması içindir.

        VARSAYILAN `False`, yani "yazamaz". Değeri geçirmeyi unutan bir çağrı
        formu gereksiz yere kilitler; tersi (varsayılan `True`) yetkisiz
        kullanıcıya çalışan sanılan bir düğme gösterir ve K9'un arayüz yarısını
        sessizce açık bırakırdı. İkisinden ucuz olan seçildi.
        """
        spec = dev.settings_spec()
        # Sözleşme YEREL: geçit düşse bile form ve düğmeler çizilebilir (K7).
        # `sound_events` ayrıca veriliyor çünkü panel beş onay kutusunu bu
        # listeden çiziyor ve olay adlarını kendi elinde tutmamalı.
        contract: dict[str, Any] = {"settings_spec": spec,
                                    "commands": self._command_spec(),
                                    "sound_events": self.sound_events_spec(),
                                    "can": {"settings": bool(can_settings)}}
        try:
            payload = await self._api.devices()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("cihaz listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], **contract}
        rows = [dev.device_row(raw) for raw in self._items(payload)]
        return {"ok": True, "connected": True, "error": "", "items": rows, **contract}

    async def menu(self) -> dict[str, Any]:
        """Revizyon ekranının "ürün ekle" seçicisi için ürün listesi.

        GEÇİTTE KARŞILIĞI OLMAYABİLİR. `bld.api` bu turda `menu()` metodunu
        taşımıyor; taşımadığında modül YÜKLENMEYE ve ekran ÇALIŞMAYA devam
        eder (K7), yalnız bu uç nedenini yazarak boş döner. `getattr` ile
        bakılmasının sebebi budur: metot eklendiği gün burada değişiklik
        gerekmez, eksik olduğu gün de ekran çökmez.
        """
        fetch = getattr(self._api, "menu", None)
        if fetch is None:
            return {"ok": False, "connected": False, "data": [],
                    "error": "Ürün listesi geçitte henüz yok; ürün kimliğini elle girin."}
        try:
            payload = await fetch()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("ürün listesi okunamadı", error=str(failure))
            return {"ok": False, "connected": False, "data": [],
                    "error": self._fail(failure)}
        # Satırlar OLDUĞU GİBİ geçer: `menu_id · name · price_kurus · options`
        # BLD'nin sözlüğüdür ve seçenek ağacı büyüyor.
        return {"ok": True, "connected": True, "error": "", "data": self._items(payload)}

    def _command_spec(self) -> list[dict[str, Any]]:
        """Sekiz komut, etiketleri ve hangisinin ayrı izin istediği.

        Panel yıkıcı komutları ayrı gösterir ve gerekçe kutusunu ZORUNLU kılar;
        listeyi panelde tekrar yazmak, sunucudaki küme değiştiğinde ekranın
        yanlış rozet göstermesi olurdu.
        """
        return [{"command": name, "label": dev.COMMAND_LABELS[name],
                 "destructive": dev.is_destructive(name),
                 "permission": "bld_kds.devices" if dev.is_destructive(name)
                 else "bld_kds.manage",
                 "needs_order": name == dev.REPRINT,
                 "reprint_types": list(dev.REPRINT_TYPES) if name == dev.REPRINT else []}
                for name in dev.COMMANDS]

    def sound_events_spec(self) -> list[dict[str, str]]:
        """Kasada tek tek kapatılabilen sesli uyarılar (K-22 §1).

        `connectionLost` BU LİSTEDE YOKTUR ve olamaz: kapatılabilseydi mutfak,
        sunucuyla bağlantının koptuğunu — yani siparişlerin hiç gelmediğini —
        duyacak son uyarıyı da kaybederdi.
        """
        return [{"key": key, "label": label} for key, label in dev.SOUND_EVENTS]

    async def device_commands(self, device_id: int, *, limit: int = 0) -> dict[str, Any]:
        """Cihazın son komutları — üç damgasıyla.

        SINIRLAMA BURADA YAPILIR: geçidin `device_commands` metodu limit almıyor
        ve sunucu zaten son 50'yi tutuyor (K-21 §2.1). Geçide olmayan bir
        argüman göndermek `TypeError` ile ekranı düşürürdü.
        """
        count = max(1, min(self._command_limit, int(limit or self._command_limit)))
        try:
            payload = await self._api.device_commands(int(device_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("komut listesi okunamadı", deviceId=device_id,
                              error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "device_id": int(device_id)}
        rows = [dev.command_row(raw, stale_after=self._stale_command_minutes)
                for raw in self._items(payload)][:count]
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "device_id": int(device_id)}

    async def print_jobs(self, *, device_id: int = 0, order_id: int = 0,
                         limit: int = 0) -> dict[str, Any]:
        """BASILMIŞ fişlerin DENETİM kaydı.

        BEKLEYEN İŞ LİSTESİ DEĞİLDİR. KDS'in kendi disk kuyruğu sunucuda yok;
        bu tablo yalnız basılanı kaydeder. Bekleyen/başarısız iş sayısı cihaz
        SAĞLIĞINDAN okunur (`health.print_queue_pending` / `print_queue_failed`)
        ve ekran bu ayrımı yazmak zorundadır — aksi hâlde boş bir liste "kuyruk
        temiz" diye okunur, oysa kuyruğa hiç bakılmamıştır.
        """
        count = max(1, min(500, int(limit or self._print_job_limit)))
        try:
            payload = await self._api.print_jobs(
                device_id=int(device_id) or None, order_id=int(order_id) or None,
                limit=count)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("fiş kaydı okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "audit_only": True}
        return {"ok": True, "connected": True, "error": "",
                "items": [dev.print_job_row(raw) for raw in self._items(payload)],
                # Panel bu bayrağı görünce başlığın altına kuyruk olmadığını
                # yazar; "0 kayıt" ile "kuyruk boş" aynı şey değildir.
                "audit_only": True}

    async def orders(self, *, include_completed: bool = False,
                     since: str = "") -> dict[str, Any]:
        """Aktif siparişler. Tamamlananlar İSTENİRSE eklenir."""
        try:
            payload = await self._api.orders(include_completed=bool(include_completed),
                                             since=dev.text(since))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "statuses": self._status_spec()}
        return {"ok": True, "connected": True, "error": "",
                "items": [dev.order_row(raw) for raw in self._items(payload)],
                "statuses": self._status_spec()}

    @staticmethod
    def _status_spec() -> list[dict[str, Any]]:
        """Yedi durum kodu, adı ve tonu — panel süzgeç şeridini bununla çizer."""
        return [{"code": code, "label": dev.STATUS_LABELS[code],
                 "tone": dev.STATUS_TONES[code],
                 "next": list(dev.STATUS_NEXT.get(code, ()))}
                for code in dev.STATUS_CODES]

    async def order(self, order_id: int) -> dict[str, Any]:
        """Tek sipariş — düzenlenebilir görünüm (revizyon ekranının girdisi)."""
        try:
            payload = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş okunamadı", orderId=order_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure),
                    "order": {}}
        raw = self._record_of(payload, "order", "data")
        if not raw:
            return {"ok": False, "connected": True, "error": "Sipariş kaydı boş döndü.",
                    "order": {}}
        return {"ok": True, "connected": True, "error": "", "order": dev.order_row(raw),
                "statuses": self._status_spec()}

    async def order_revisions(self, order_id: int) -> dict[str, Any]:
        """Revizyon geçmişi — "ne oldu" sorusunun cevabı."""
        try:
            payload = await self._api.order_revisions(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("revizyon geçmişi okunamadı", orderId=order_id,
                              error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "order_id": int(order_id)}
        return {"ok": True, "connected": True, "error": "",
                "items": [dev.revision_row(raw) for raw in self._items(payload)],
                "order_id": int(order_id)}

    # ================================================================= yazma

    async def create_device(self, *, name: str, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni cihaz açar ve ilk eşleme kodunu getirir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = dev.text(name)
        problem = dev.device_error(clean)
        if problem:
            return {"ok": False, "error": problem}
        kuru = self._dry(dry_run)

        # TAZE OKUMA: aynı adlı ikinci bir cihaz, iptal düğmesine basan kişiyi
        # yazı tura atmaya bırakır. Sunucu ad tekliği aramıyor; kapı burada.
        try:
            payload = await self._api.devices()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        for raw in self._items(payload):
            if dev.text(raw.get("name")).casefold() == clean.casefold():
                await self._record(action="create_device", reason=reason, actor=actor,
                                   result=BLOCKED, detail={"name": clean})
                return {"ok": False,
                        "error": f"'{clean}' adında bir cihaz zaten var. "
                                 "Aynı adlı iki kasa hangisinin iptal edildiğini "
                                 "belirsiz bırakır."}

        await self._record(action="create_device", reason=reason, actor=actor,
                           result=TRIED, detail={"name": clean, "dry_run": kuru})
        if kuru:
            await self._record(action="create_device", reason=reason, actor=actor,
                               result=DRY, detail={"name": clean})
            return {"ok": True, "error": "", "dry_run": True,
                    "would": {"action": "create_device", "name": clean}, "device": {}}

        try:
            result = await self._api.create_device(name=clean, reason=reason,
                                                       actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="create_device", reason=reason, actor=actor,
                               result=FAILED, detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        row = self._record_of(result, "device", "data")
        device_id = dev.as_int(row.get("id"))
        await self._record(action="create_device", reason=reason, actor=actor,
                           result=DONE, target_id=device_id, detail={"name": clean})
        return {"ok": True, "error": "", "dry_run": False,
                "device": dev.device_row(row) if row else {}}

    async def rename_device(self, device_id: int, *, name: str, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Cihaz adını değiştirir. YALNIZ ad — ayar bu uçtan geçmez."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = dev.text(name)
        problem = dev.device_error(clean)
        if problem:
            return {"ok": False, "error": problem}
        kuru = self._dry(dry_run)

        current, problem = await self._fresh_device(device_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["name"] == clean:
            return {"ok": True, "error": "", "dry_run": kuru, "changed": False,
                    "device": current,
                    "note": "Ad zaten bu; sunucuya istek gönderilmedi."}

        await self._record(action="rename_device", reason=reason, actor=actor,
                           result=TRIED, target_id=device_id,
                           detail={"from": current["name"], "to": clean, "dry_run": kuru})
        if kuru:
            await self._record(action="rename_device", reason=reason, actor=actor,
                               result=DRY, target_id=device_id,
                               detail={"from": current["name"], "to": clean})
            return {"ok": True, "error": "", "dry_run": True, "changed": True,
                    "would": {"action": "rename_device", "from": current["name"],
                              "to": clean},
                    "device": current}

        try:
            result = await self._api.rename_device(int(device_id), name=clean,
                                                       reason=reason, actor=actor,
                                                       dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="rename_device", reason=reason, actor=actor,
                               result=FAILED, target_id=device_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="rename_device", reason=reason, actor=actor, result=DONE,
                           target_id=device_id,
                           detail={"from": current["name"], "to": clean})
        row = self._record_of(result, "device", "data")
        return {"ok": True, "error": "", "dry_run": False, "changed": True,
                "device": dev.device_row(row) if row else {**current, "name": clean}}

    async def pairing_code(self, device_id: int, *, reason: str, actor: str,
                           dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni eşleme kodu üretir (10 dk geçerli).

        İPTAL EDİLMİŞ CİHAZA KOD ÜRETİLMEZ. Nedeni yetki, kolaylık değil:
        iptal `bld_kds.devices` iznine bağlı, kod üretimi `bld_kds.manage`'e.
        İptal edilmiş bir kasaya kod üretilebilseydi, yalnız `manage` taşıyan
        biri `devices` iznini taşıyan kişinin kararını geri alır ve iptal edilen
        kasa mutfağa geri dönerdi.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        kuru = self._dry(dry_run)

        current, problem = await self._fresh_device(device_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["revoked"]:
            await self._record(action="pairing_code", reason=reason, actor=actor,
                               result=BLOCKED, target_id=device_id,
                               detail={"revoked_at": current["revoked_at"]})
            return {"ok": False,
                    "error": "Bu cihaz iptal edilmiş; yeniden eşleştirilemez. "
                             "Mutfağa yeni bir cihaz kaydı açın."}

        await self._record(action="pairing_code", reason=reason, actor=actor, result=TRIED,
                           target_id=device_id, detail={"dry_run": kuru})
        if kuru:
            await self._record(action="pairing_code", reason=reason, actor=actor,
                               result=DRY, target_id=device_id)
            return {"ok": True, "error": "", "dry_run": True,
                    "would": {"action": "pairing_code", "device_id": int(device_id),
                              "name": current["name"]},
                    "pairing": {}}

        try:
            result = await self._api.new_pairing_code(int(device_id), reason=reason,
                                                      actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="pairing_code", reason=reason, actor=actor,
                               result=FAILED, target_id=device_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="pairing_code", reason=reason, actor=actor, result=DONE,
                           target_id=device_id)
        row = self._record_of(result, "pairing", "device", "data")
        # KOD DENETİM İZİNE YAZILMAZ. Eşleme kodu bir sırdır ve iz satırı
        # silinmez; koda yazsaydık, kodun ömrü 10 dakika yerine sonsuz olurdu.
        return {"ok": True, "error": "", "dry_run": False,
                "pairing": {"code": dev.text(row.get("code")) or None,
                            "expires_at": dev.text(row.get("expires_at")) or None,
                            "usable": True}}

    async def revoke_device(self, device_id: int, *, reason: str, actor: str,
                            dry_run: bool | None = None,
                            allow_destructive: bool = False) -> dict[str, Any]:
        """Cihazı iptal eder. SATIR SİLİNMEZ, cihaz bir daha bağlanamaz.

        AYRI İZİN İSTER (`bld_kds.devices`). Uç noktada da denetlenir; burada
        TEKRAR denetlenmesi K9'un çift kapısıdır: izin kararı servisin kendi
        birim testinde sınanabilir olmalı, yoksa kuralın doğru olduğu ancak
        HTTP katmanı ayağa kaldırılarak gösterilebilirdi.
        """
        if not allow_destructive:
            await self._record(action="revoke_device", reason=reason, actor=actor,
                               result=BLOCKED, target_id=device_id,
                               detail={"missing": "bld_kds.devices"})
            return {"ok": False,
                    "error": "Cihaz iptali ayrı yetki ister: bld_kds.devices."}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        kuru = self._dry(dry_run)

        current, problem = await self._fresh_device(device_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["revoked"]:
            await self._record(action="revoke_device", reason=reason, actor=actor,
                               result=BLOCKED, target_id=device_id,
                               detail={"revoked_at": current["revoked_at"]})
            return {"ok": False, "error": "Bu cihaz zaten iptal edilmiş."}

        await self._record(action="revoke_device", reason=reason, actor=actor, result=TRIED,
                           target_id=device_id,
                           detail={"name": current["name"], "dry_run": kuru})
        if kuru:
            await self._record(action="revoke_device", reason=reason, actor=actor,
                               result=DRY, target_id=device_id,
                               detail={"name": current["name"]})
            return {"ok": True, "error": "", "dry_run": True, "announced": False,
                    "would": {"action": "revoke_device", "device_id": int(device_id),
                              "name": current["name"],
                              "effect": "Cihaz bir daha bağlanamaz; kaydı silinmez."}}

        try:
            await self._api.revoke_device(int(device_id), reason=reason, actor=actor,
                                              dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="revoke_device", reason=reason, actor=actor,
                               result=FAILED, target_id=device_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="revoke_device", reason=reason, actor=actor, result=DONE,
                           target_id=device_id, detail={"name": current["name"]})
        await self._announce(REVOKED_EVENT, {"deviceId": int(device_id),
                                             "name": current["name"], "reason": reason,
                                             "actor": actor})
        return {"ok": True, "error": "", "dry_run": False, "announced": True}

    async def push_settings(self, device_id: int, *, settings: dict[str, Any],
                            reason: str, actor: str, dry_run: bool | None = None,
                            token: str = "") -> dict[str, Any]:
        """Yönetilen ayarları KISMİ olarak yazar.

        AYRI İZİN İSTER (`bld_kds.settings`) ve kapı UÇ NOKTADADIR. Burada
        `allow_destructive` benzeri ikinci bir bayrak YOKTUR ve olmamalı:
        `revoke_device`/`send_command` uçları iki izni birden kabul ettiği için
        (`requires(MANAGE, DEVICES)`) kararı gövdeye bakarak serviste veriyor.
        Bu uç tek bir izne bağlıdır; `requires(SETTINGS)` geçilmeden gövdeye
        hiç girilmez. İkinci bir bayrak eklemek, hiçbir zaman `False`
        olmayacak bir dal ve onunla birlikte yanlış bir güven yaratırdı.

        Kuru provada önizleme yerel tabloya kaydedilir ve bir jeton döner.
        Jeton İSTEĞE BAĞLIDIR (sözleşmede jeton isteyen bir uç yok), ama
        verilirse denetlenir: önizlemeden bu yana cihazın ayarı başka biri
        tarafından değiştirildiyse istek REDDEDİLİR. Onaylanan tablo ile
        uygulanan tablonun aynı olduğu böyle kanıtlanır.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean, errors = dev.clean_settings(settings)
        if errors:
            return {"ok": False, "error": " ".join(errors), "errors": errors}
        if not clean:
            return {"ok": False, "error": "Yazılacak ayar seçilmedi."}
        kuru = self._dry(dry_run)

        current, problem = await self._fresh_device(device_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["revoked"]:
            return {"ok": False, "error": "İptal edilmiş cihazın ayarı yazılamaz."}

        baseline = current["settings"]
        diff = dev.settings_diff(baseline, clean)
        if not diff:
            # Aynı değeri yeniden yazmak `settings_updated_at` damgasını ileri
            # atardı ve "en son ne zaman dokunuldu" sorusunun cevabını bozardı.
            return {"ok": True, "error": "", "dry_run": kuru, "changed": False,
                    "diff": [], "warnings": [], "device": current,
                    "note": "Hiçbir ayar değişmiyor; sunucuya istek gönderilmedi."}

        warnings = [uyari for uyari in (dev.threshold_warning(baseline, clean),) if uyari]
        kilitler = [row["label"] for row in diff if row["lock"] and row["to"] is False]
        if kilitler:
            warnings.append("Kasada şu düğmeler kapanacak: " + ", ".join(kilitler)
                            + ". Düğme görünür kalır ama pasifleşir.")

        await self._record(action="push_settings", reason=reason, actor=actor, result=TRIED,
                           target_id=device_id, detail={"diff": diff, "dry_run": kuru})

        if kuru:
            fis = await self._save_preview(device_id, proposed=clean, baseline=baseline,
                                           diff=diff, actor=actor, reason=reason)
            await self._record(action="push_settings", reason=reason, actor=actor,
                               result=DRY, target_id=device_id, detail={"token": fis})
            return {"ok": True, "error": "", "dry_run": True, "changed": True,
                    "token": fis, "diff": diff, "warnings": warnings,
                    "would": {"action": "push_settings", "device_id": int(device_id),
                              "settings": clean},
                    "device": current}

        if token:
            problem = await self._check_preview(token, device_id=device_id,
                                                proposed=clean, baseline=baseline)
            if problem:
                await self._record(action="push_settings", reason=reason, actor=actor,
                                   result=BLOCKED, target_id=device_id,
                                   detail={"token": token, "why": problem})
                return {"ok": False, "error": problem}

        try:
            result = await self._api.update_device_settings(int(device_id), settings=clean,
                                                         reason=reason, actor=actor,
                                                         dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="push_settings", reason=reason, actor=actor,
                               result=FAILED, target_id=device_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="push_settings", reason=reason, actor=actor, result=DONE,
                           target_id=device_id, detail={"diff": diff})
        if token:
            await self._close_preview(token)
        row = self._record_of(result, "device", "data")
        return {"ok": True, "error": "", "dry_run": False, "changed": True,
                "diff": diff, "warnings": warnings,
                "device": dev.device_row(row) if row else current}

    async def send_command(self, device_id: int, *, command: str,
                           payload: dict[str, Any] | None = None, reason: str, actor: str,
                           dry_run: bool | None = None,
                           allow_destructive: bool = False) -> dict[str, Any]:
        """Komutu kuyruğa atar. Teslimat sağlık yanıtıyla olur.

        BEŞ KOMUT AYRI İZİN ister (`bld_kds.devices`): `restart` mutfağın
        ekranını kapatır, `clear_failed` basılamamış fişleri kuyruktan düşürür
        ve o fişler bir daha basılmaz, `clear_queue` üstelik BEKLEYEN işleri de
        düşürür, `update` kurulum boyunca ekranı alır, `unpair` kasanın
        token'ını siler ve kasa sahada elle eşleştirilene kadar HİÇ sipariş
        göremez (bkz. `devices.DESTRUCTIVE_COMMANDS`).
        """
        name = dev.text(command).lower()
        problem = dev.command_error(name, payload)
        if problem:
            return {"ok": False, "error": problem}
        if dev.is_destructive(name) and not allow_destructive:
            await self._record(action=f"command:{name}", reason=reason, actor=actor,
                               result=BLOCKED, target_id=device_id,
                               detail={"missing": "bld_kds.devices"})
            return {"ok": False,
                    "error": f"'{dev.COMMAND_LABELS[name]}' ayrı yetki ister: "
                             "bld_kds.devices."}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        kuru = self._dry(dry_run)
        body = dev.command_payload(name, payload)

        current, problem = await self._fresh_device(device_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["revoked"]:
            await self._record(action=f"command:{name}", reason=reason, actor=actor,
                               result=BLOCKED, target_id=device_id,
                               detail={"revoked_at": current["revoked_at"]})
            return {"ok": False, "error": "İptal edilmiş cihaz komut almaz."}

        # ÇEVRİMDIŞI CİHAZ ENGEL DEĞİLDİR: komut kuyrukta bekler ve kasa geri
        # döndüğünde teslim edilir. Engellemek, kapanmış bir kasaya "aç"
        # diyememek olurdu. Ama ekran bunu SÖYLER.
        beklet = not current["online"]

        await self._record(action=f"command:{name}", reason=reason, actor=actor,
                           result=TRIED, target_id=device_id,
                           detail={"payload": body, "dry_run": kuru, "queued": beklet})
        if kuru:
            await self._record(action=f"command:{name}", reason=reason, actor=actor,
                               result=DRY, target_id=device_id, detail={"payload": body})
            return {"ok": True, "error": "", "dry_run": True, "queued_offline": beklet,
                    "would": {"action": "command", "command": name,
                              "label": dev.COMMAND_LABELS[name], "payload": body,
                              "device_id": int(device_id), "name": current["name"]},
                    "command": {}}

        try:
            # YÜKSÜZ KOMUTA BOŞ YÜK GÖNDERİLMEZ: geçit `payload is not None`
            # gördüğünde gövdeye `"payload": {}` koyar ve sunucuya sözleşmede
            # olmayan bir alan taşınmış olur.
            result = await self._api.send_command(int(device_id), command=name,
                                                  payload=body or None, reason=reason,
                                                  actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action=f"command:{name}", reason=reason, actor=actor,
                               result=FAILED, target_id=device_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action=f"command:{name}", reason=reason, actor=actor,
                           result=DONE, target_id=device_id, detail={"payload": body})
        row = self._record_of(result, "command", "data")
        return {"ok": True, "error": "", "dry_run": False, "queued_offline": beklet,
                "command": dev.command_row(row, stale_after=self._stale_command_minutes)
                if row else {}}

    async def create_revision(self, order_id: int, *, items: Any, reason: str, actor: str,
                              note: str = "", requested_at: str = "",
                              customer_note: str = "",
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Sipariş revizyonu yazar. TAM KALEM LİSTESİ gönderilir.

        `created_by_device_id` NULL kalır ve `created_by_staff` "Kontrol Merkezi"
        + aktör olur (K-21 §2.5): denetim izinde işin kasadan mı merkezden mi
        yapıldığı ayrılabilmeli.
        """
        problem = dev.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean, problem = dev.revision_items(items)
        if problem:
            return {"ok": False, "error": problem}
        kuru = self._dry(dry_run)

        # TAZE OKUMA: revizyon siparişin YENİ hâlini yazar. Aradan mutfak
        # siparişi teslim etmiş ya da iptal etmiş olabilir; ekranda 10 dakika
        # önce açılmış bir sepetle yazmak, o değişikliği geri alırdı.
        fresh = await self.order(order_id)
        if not fresh.get("ok"):
            return {"ok": False, "error": fresh.get("error") or "Sipariş okunamadı."}
        current = fresh["order"]

        # GÖVDEYİ GEÇİT KURAR. `create_order_revision` kalem listesini, notu ve
        # gerekçeyi kendi biçimine yerleştiriyor; burada ikinci bir gövde
        # kurmak, iki yerde ayrışabilen bir sözleşme demek olurdu.
        await self._record(action="create_revision", reason=reason, actor=actor,
                           result=TRIED, target_type="order", target_id=order_id,
                           detail={"items": len(clean), "dry_run": kuru})
        if kuru:
            await self._record(action="create_revision", reason=reason, actor=actor,
                               result=DRY, target_type="order", target_id=order_id,
                               detail={"items": len(clean)})
            return {"ok": True, "error": "", "dry_run": True,
                    "would": {"action": "create_revision", "order_id": int(order_id),
                              "status": current.get("status"), "items": clean},
                    "revision": {}}

        try:
            result = await self._api.create_order_revision(
                int(order_id), items=clean, reason=dev.text(reason), actor=actor,
                note=dev.text(note)[:1000], requested_at=dev.text(requested_at),
                customer_note=dev.text(customer_note)[:1000], dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="create_revision", reason=reason, actor=actor,
                               result=FAILED, target_type="order", target_id=order_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="create_revision", reason=reason, actor=actor,
                           result=DONE, target_type="order", target_id=order_id,
                           detail={"items": len(clean)})
        revision = self._record_of(result, "revision")
        order = self._record_of(result, "order")
        return {"ok": True, "error": "", "dry_run": False,
                "revision": dev.revision_row(revision) if revision else {},
                "order": dev.order_row(order) if order else current}

    async def set_order_status(self, order_id: int, *, status: str, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Sipariş durumunu değiştirir. Geçiş matrisi SUNUCUDA uygulanır."""
        problem = dev.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        target = dev.text(status).lower()
        kuru = self._dry(dry_run)

        fresh = await self.order(order_id)
        if not fresh.get("ok"):
            return {"ok": False, "error": fresh.get("error") or "Sipariş okunamadı."}
        current = fresh["order"]
        before = dev.text(current.get("status"))

        problem = dev.status_error(before, target)
        if problem:
            await self._record(action="set_status", reason=reason, actor=actor,
                               result=BLOCKED, target_type="order", target_id=order_id,
                               detail={"from": before, "to": target})
            return {"ok": False, "error": problem}

        await self._record(action="set_status", reason=reason, actor=actor, result=TRIED,
                           target_type="order", target_id=order_id,
                           detail={"from": before, "to": target, "dry_run": kuru})
        if kuru:
            await self._record(action="set_status", reason=reason, actor=actor, result=DRY,
                               target_type="order", target_id=order_id,
                               detail={"from": before, "to": target})
            return {"ok": True, "error": "", "dry_run": True, "announced": False,
                    "would": {"action": "set_status", "order_id": int(order_id),
                              "from": before, "to": target,
                              "label": dev.STATUS_LABELS.get(target, target)}}

        try:
            result = await self._api.set_order_status(int(order_id), status=target,
                                                          reason=dev.text(reason),
                                                          actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="set_status", reason=reason, actor=actor,
                               result=FAILED, target_type="order", target_id=order_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="set_status", reason=reason, actor=actor, result=DONE,
                           target_type="order", target_id=order_id,
                           detail={"from": before, "to": target})
        await self._announce(STATUS_EVENT, {"orderId": int(order_id), "from": before,
                                            "to": target, "reason": dev.text(reason)})
        order = self._record_of(result, "order", "data")
        return {"ok": True, "error": "", "dry_run": False, "announced": True,
                "order": dev.order_row(order) if order else {**current, "status": target}}

    # ====================================================== ayar önizlemesi

    async def _save_preview(self, device_id: int, *, proposed: dict[str, Any],
                            baseline: dict[str, Any], diff: list[dict[str, Any]],
                            actor: str, reason: str) -> str:
        """Kuru provanın çıktısını saklar ve jetonunu döner.

        `baseline` de saklanır: gerçek uygulama geldiğinde cihazın ayarı hâlâ
        önizleme anındaki hâlde mi diye bakılır. Aksi hâlde iki yönetici aynı
        anda ayar yazdığında ikincisi birincinin değişikliğini görmeden ezerdi.
        """
        token = secrets.token_urlsafe(16)
        try:
            await self._store.execute(
                f"INSERT INTO {self._preview} "
                "(token, device_id, proposed, baseline, diff, status, actor, reason, "
                "created_at) VALUES (?, ?, ?, ?, ?, 'dry_run', ?, ?, ?)",
                (token, int(device_id), json.dumps(proposed, ensure_ascii=False),
                 json.dumps(baseline, ensure_ascii=False),
                 json.dumps(diff, ensure_ascii=False), actor, dev.text(reason),
                 dev.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — önizleme saklanamadı, iş sürsün
            self._log.warning("ayar önizlemesi saklanamadı", error=str(failure))
            return ""
        return token

    async def _check_preview(self, token: str, *, device_id: int,
                             proposed: dict[str, Any],
                             baseline: dict[str, Any]) -> str:
        """Jeton geçerli mi — değilse kullanıcıya gösterilecek metin."""
        try:
            row = await self._store.fetch_one(
                f"SELECT token, device_id, proposed, baseline, status, created_at "
                f"FROM {self._preview} WHERE token = ?", (dev.text(token),))
        except Exception as failure:  # noqa: BLE001 — okunamadı; jeton doğrulanamaz
            self._log.warning("önizleme okunamadı", error=str(failure))
            return "Önizleme kaydı okunamadı; kuru provayı tekrarlayın."
        if not row:
            return "Önizleme bulunamadı; kuru provayı tekrarlayın."
        if dev.as_int(row["device_id"]) != int(device_id):
            return "Bu önizleme başka bir cihaza ait."
        if dev.text(row["status"]) != "dry_run":
            return "Bu önizleme zaten uygulandı."
        gecen = dev.minutes_since(row["created_at"])
        if gecen is not None and gecen > self._preview_ttl:
            return (f"Önizlemenin üzerinden {self._preview_ttl} dakikadan fazla geçti; "
                    "kuru provayı tekrarlayın.")
        try:
            onceki = json.loads(row["proposed"] or "{}")
            eski_taban = json.loads(row["baseline"] or "{}")
        except ValueError:
            return "Önizleme kaydı bozuk; kuru provayı tekrarlayın."
        if onceki != proposed:
            return "İstek önizlemeden farklı; onaylanan tablo neyse o uygulanır."
        if eski_taban != baseline:
            return ("Önizlemeden bu yana cihazın ayarları değişti; "
                    "kuru provayı tekrarlayın.")
        return ""

    async def _close_preview(self, token: str) -> None:
        try:
            await self._store.execute(
                f"UPDATE {self._preview} SET status = 'applied', applied_at = ? "
                "WHERE token = ?", (dev.now_iso(), dev.text(token)))
        except Exception as failure:  # noqa: BLE001 — kapatılamadı, iş bitti
            self._log.warning("önizleme kapatılamadı", error=str(failure))
