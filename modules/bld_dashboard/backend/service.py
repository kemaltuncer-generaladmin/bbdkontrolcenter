"""Kontrol Paneli — iş kuralları.

SAYI SUNUCUDA HESAPLANIR, EKRAN BURADADIR. `active`, `revenue_today_kurus`,
`fill_rate`, `seconds_to_next_cutoff` ve `pending_tasks`'in tamamı BLD'de
üretilir (`BLD/docs/control/dashboard.md`) ve buraya `bld.api` geçidinden gelir
(K4). Bu modül ham `httpx` kullanmaz, UZAK VERİNİN KOPYASINI TUTMAZ ve tek bir
sayacı bile kendi toplamaz.

Neden kendi toplamıyor: "kaç sipariş aktif" sorusunun tek bir cevabı olmalı.
İstemcide toplansaydı cevap panel sürümüne göre değişirdi; sunucudaki
`OverviewController` ile ekran arasında bir gün fark oluştuğunda hangisinin
doğru olduğunu kimse bilemezdi. Ekranın işi hesap yapmak değil, gelen sayıyı
okunur biçimde ÇİZMEK.

İKİ GEÇİT ÇAĞRISI, TEK PANEL İSTEĞİ. Panel bir kez `/summary` çağırır; servis
onun arkasında en çok iki geçit çağrısı yapar:

    1. `dashboard_overview()`  — yedi blok, sözleşmenin tek ucu
    2. `order_list(per_page=N)` — CANLI AKIŞ kutusunun satırları

İkincisi neden var: sözleşmenin gösterge ucu SAYAÇ döndürüyor, SATIR değil
(`dashboard.md` gövdesinde tek bir sipariş numarası yok). Canlı akış kutusu
satır ister ve bu satırları uydurmanın yolu yok. Alternatif, sayaç farkından
"3 sipariş geldi" cümlesi kurmaktı — hangi siparişler olduğunu söyleyemeyen,
yani hiçbir işe yaramayan bir akış. Yük hesabı da tutuyor: 30 saniyede iki
istek saatte 240 çağrıdır ve paylaşılan `bld-control-panel` kovası 3000/saat
(`00-genel.md` §2).

İKİ ÇAĞRI BİRBİRİNİ DÜŞÜRMEZ (K7). Akış çağrısı patlarsa özet YİNE DÖNER ve
yalnız akış kutusu boş kalır; tersi de doğrudur. Tek bir `try` bloğunda
olsalardı, sipariş listesinin 500 vermesi bütün gösterge panelini karartırdı.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır:
ayrımı `connected` taşır ve panelin onu OKUMASI gerekir — yalnız `ok`a bakan
bir ekran, geçit düştüğünde "bugün sipariş yok" der ve K7'nin engellemek için
var olduğu yalanı söyler.

`dry_run` GEÇEN TEK BİR ÇAĞRI YOK ve olmayacak: bu alanda yazma ucu yoktur
(`dashboard.md` → "Bu alanda yazma ucu yoktur ve okumalar denetlenmez").
Bayrağın "her yazmada açıkça geçilir" kuralı burada boşta kalır çünkü yazma
yok; `bld_api/README.md` kuralına aykırılık değil, kuralın konusuz kalmasıdır.
"""

from __future__ import annotations

from typing import Any

from . import dashboard as db


class DashboardService:
    """Kontrol Paneli ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    #: Yazılabilen tercihler. Kapalı liste: tanınmayan bir anahtarı kabul etmek,
    #: yazım hatasını sessizce diske yazıp hiçbir yerde kullanmamak olurdu.
    PREF_KEYS = ("poll_seconds", "location_id", "flow_limit", "flow_enabled")

    def __init__(self, *, api: Any, store: Any, log: Any,
                 config: dict[str, Any]) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _poll_seconds(self) -> int:
        """Yoklama aralığı. Sözleşme bu ekran için 30 saniye diyor."""
        return max(10, min(300, db.as_int(self._config.get("poll_seconds"), 30)))

    @property
    def _location_id(self) -> int:
        """0 = sunucunun varsayılan işletmesi; sorguya hiç eklenmez."""
        return max(0, db.as_int(self._config.get("location_id"), 0))

    @property
    def _flow_enabled(self) -> bool:
        """Canlı akış kutusu ikinci bir geçit çağrısı yapar; kapatılabilir."""
        value = db.as_bool(self._config.get("flow_enabled"))
        return True if value is None else value

    @property
    def _flow_limit(self) -> int:
        return max(3, min(50, db.as_int(self._config.get("flow_limit"), 10)))

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
    def _data(payload: Any) -> dict[str, Any]:
        """`{"data": {...}}` zarfını açar; geçit çoğu uçta zaten açıyor.

        İki adı da denemek bilinçli: tek bir ada bağlanmak, ad tutmadığında
        ekranı SESSİZCE boş gösterirdi ve "sunucu düştü" ile ayırt edilemezdi.
        """
        if not isinstance(payload, dict):
            return {}
        inner = payload.get("data")
        return dict(inner) if isinstance(inner, dict) else dict(payload)

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _server_time(payload: Any) -> str:
        """Yanıttaki `server_time`. GERİ SAYIMIN TABANI BUDUR.

        Panel kalan süreyi `seconds_to_next_cutoff` ile bu anın üzerine kurar;
        istemcinin kendi saatini kullanması, saati kaymış bir makinede yanlış
        bir aciliyet yaratırdı (`00-genel.md` §6).
        """
        if isinstance(payload, dict):
            return db.text(payload.get("server_time"))
        return ""

    @staticmethod
    def _meta(payload: Any) -> dict[str, Any]:
        """`meta.cached_at` — sunucu 60 saniyelik önbellek açtıysa taşınır.

        Sözleşme önbelleği İSTEĞE BAĞLI bıraktı. Ekran bu alanı görürse
        "34 saniye önceki veri" diyebilir; görmezse hiçbir şey demez. Kendi
        tahminini yürütmek, olmayan bir gecikmeyi ekranda var göstermekti.
        """
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return dict(payload["meta"])
        return {}

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Ekranın sözleşmesi ve kullanıcının tercihleri. AĞA ÇIKMAZ (K7).

        Ayrı bir uç olması bilinçli: `/summary` 30 saniyede bir yoklanıyor ve
        her yoklamada tercih tablosunu okumanın anlamı yok. Ayrıca geçit
        düşükken bile bu uç dolu döner — rozetler, seviye adları ve durum
        etiketleri sunucuya hiç sorulmadan çizilebilir.
        """
        return {
            "ok": True,
            "connected": None,   # bu uç ağa çıkmaz; bağlantı `/summary`den anlaşılır
            "error": "",
            "contract": db.screen_contract(),
            "prefs": await self.prefs(),
            "limits": {
                "poll_seconds": self._poll_seconds,
                "flow_limit": self._flow_limit,
                "flow_enabled": self._flow_enabled,
                "location_id": self._location_id,
            },
        }

    async def summary(self, *, date: str = "", location_id: int = 0) -> dict[str, Any]:
        """Gösterge panelinin CANLI gövdesi — tek panel isteği, iki geçit çağrısı.

        `date` boşsa sunucu bugünü (servis gününü) kullanır ve buraya hiçbir
        şey yazılmaz: bugünün ne olduğuna işletme takvimi karar verir
        (Europe/Istanbul iş günü sınırı), istemcinin saati değil.
        """
        problem = db.date_error(date, field="Gün")
        if problem:
            # Süzgeç hatası bir BAĞLANTI sorunu değildir: `connected` bilinmiyor.
            # Ekran ikisini karıştırmasın diye `ok: False` ve `connected: None`.
            return {"ok": False, "connected": None, "error": problem, **self._blank()}

        chosen = max(0, int(location_id or 0)) or self._location_id
        try:
            payload = await self._api.dashboard_overview(
                location_id=chosen or None, date=db.text(date))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("gösterge özeti okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), **self._blank()}

        data = self._data(payload)
        return {
            "ok": True,
            "connected": True,
            "error": "",
            "code": "",
            "date": db.text(data.get("date")) or db.text(date),
            "location_id": db.as_int(data.get("location_id"), chosen),
            "server_time": self._server_time(payload),
            "meta": self._meta(payload),
            "sales": db.sales_block(data.get("sales")),
            "orders": db.orders_block(data.get("orders")),
            "capacity": db.capacity_block(data.get("capacity")),
            "subscriptions": db.subscriptions_block(data.get("subscriptions")),
            "devices": db.devices_block(data.get("devices")),
            "monitor": db.monitor_block(data.get("monitor")),
            "pending_tasks": db.pending_tasks(data.get("pending_tasks")),
            "flow": await self._flow(),
        }

    def _blank(self) -> dict[str, Any]:
        """Bağlantı yokken de AYNI ŞEKİLLİ gövde.

        Boş sözlük döndürmek panele "alan var mı" savunması yazdırırdı ve o
        savunmanın unutulduğu tek satır, geçit düştüğünde ekranı çökertirdi.
        Bloklar boş sözlükten üretilir: hepsi `None` taşır, yani "bilinmiyor".
        """
        return {
            "date": "",
            "location_id": self._location_id,
            "server_time": "",
            "meta": {},
            "sales": db.sales_block(None),
            "orders": db.orders_block(None),
            "capacity": db.capacity_block(None),
            "subscriptions": db.subscriptions_block(None),
            "devices": db.devices_block(None),
            "monitor": db.monitor_block(None),
            "pending_tasks": [],
            "flow": {"connected": False, "error": "", "code": "", "items": []},
        }

    async def _flow(self) -> dict[str, Any]:
        """Canlı sipariş akışı — KENDİ BAŞINA DÜŞER, özeti düşürmez (K7).

        Süzgeç GÖNDERİLMEZ. Sunucu süzgeçsiz istekte son 7 günü, en yenisi
        başta olmak üzere döndürüyor; akış kutusunun istediği tam olarak bu.
        `service_date=bugün` göndermek, gece verilen yarının siparişlerini
        akıştan düşürürdü ve catering'de gece siparişi olağandır.
        """
        if not self._flow_enabled:
            # Kapalı kutu BOŞ KUTU DEĞİLDİR: panel ayrımı `enabled` ile yapar
            # ve "akış kapatıldı" der, "sipariş yok" demez.
            return {"connected": None, "enabled": False, "error": "", "code": "",
                    "items": []}

        try:
            payload = await self._api.order_list(page=1, per_page=self._flow_limit)
        except Exception as failure:  # noqa: BLE001 — K7, özet ayakta kalır
            self._log.warning("canlı akış okunamadı", error=str(failure))
            return {"connected": False, "enabled": True, "error": self._fail(failure),
                    "code": self._code(failure), "items": []}

        rows = [db.flow_row(raw) for raw in self._items(payload)]
        return {"connected": True, "enabled": True, "error": "", "code": "",
                "items": rows[:self._flow_limit]}

    # ------------------------------------------------------ ekran tercihi

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler BLD'yi ETKİLEMEZ; yalnız bu ekranın ne gösterdiğini belirler.
        Okuma patlarsa varsayılan yeter — tercih okunamadı diye gösterge
        panelinin açılmaması, sorunun kendisini görünmez yapardı (K7).
        """
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))

        flow = db.as_bool(stored.get("flow_enabled"))
        return {
            # ALT SINIR 10 SANİYE. Sözleşme 30 diyor; kullanıcıya daha hızlısını
            # açmak paylaşılan kovayı (3000/saat/IP) yakar ve ikinci bir
            # yöneticinin ekranını 429'a düşürür. Tavan 300: beş dakikada bir
            # tazelenen bir "canlı" ekran, canlı olduğunu iddia etmemeli.
            "poll_seconds": max(10, min(300, db.as_int(stored.get("poll_seconds"),
                                                       self._poll_seconds))),
            "location_id": max(0, db.as_int(stored.get("location_id"),
                                            self._location_id)),
            "flow_limit": max(3, min(50, db.as_int(stored.get("flow_limit"),
                                                   self._flow_limit))),
            "flow_enabled": self._flow_enabled if flow is None else flow,
        }

    async def save_prefs(self, values: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Görüntüleme tercihini yazar. BLD'YE HİÇBİR ŞEY GİTMEZ.

        Gerekçe İSTENMEZ: kimse "yoklama aralığını 60 saniye yaptım"
        gerekçesini denetim izinde aramaz ve gerekçeyi burada da zorunlu
        kılmak, gerçek yazmalardaki gerekçe alışkanlığını törenselleştirirdi.
        Denetim satırı da yazılmaz — bu alanda okumalar denetlenmiyor
        (`dashboard.md`) ve tek yerel yazma kullanıcının kendi ekran ayarı.
        """
        unknown = sorted(set(values or {}) - set(self.PREF_KEYS))
        if unknown:
            return {"ok": False, "error": f"Tanınmayan tercih: {', '.join(unknown)}."}

        stamp = db.now_iso()
        for key, value in (values or {}).items():
            raw = "true" if value is True else "false" if value is False else db.text(value)
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
