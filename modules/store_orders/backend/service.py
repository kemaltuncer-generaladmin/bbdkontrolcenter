"""Siparişler — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Sipariş `store.api` geçidinden gelir (K4);
bu modül Bagisto verisinin kopyasını tutmaz. Yerel tablolar yalnız mağazada
KARŞILIĞI OLMAYAN üç şeyi saklar: yazma gerekçesi (denetim izi), toplu işlem
önizlemesi (uygulanan şeyin önizlenen şey olduğunu kanıtlar) ve bu ekrana özel
tercihler (durum adları, sipariş no biçimi, iptal süresi).

İKİ OKUMA KİPİ. Mağazanın sipariş listesi ucu yalnız birkaç süzgeç uyguluyor
(durum, kanal, tarih, tutar). O süzgeçlerle yetiniliyorsa liste SUNUCU
TARAFINDA sayfalanır. Ödeme yöntemi, kargo firması, şehir, kupon, arama, çip
ve anahtar süzgeçler için ORTAK TARAMA okunur ve süzme burada yapılır; ekran
kaç kayıt tarandığını, taramanın yaşını ve tavan yakalandıysa bunu SÖYLER.
Yarım listeyi tam göstermek, hiç göstermemekten kötüdür.

ORTAK TARAMA — İSTEĞİN İÇİNDE KOŞMAZ. Geçit mağazaya dakikada 55 istekte
tutuyor ve bu pay BÜTÜN mağaza ekranlarının ortak payı. Eskiden sayaç şeridi
her yenilemede siparişlerin tamamını tarıyor, ardından en çok 300 siparişi
TEK TEK (`GET /orders/{id}`) okuyordu; sayfa değiştirmek bile taramayı
tekrarlıyordu ve o sırada öteki ekranlar sırada bekliyordu. Şimdi tarama
`store.scan` tazeleyicisinde koşar (`_load_scan`): sipariş, fatura, gönderi,
iade ve seçilen kargo LİSTELERİ — her biri 50'şerlik sayfalarla, sipariş
başına istek YOK. Liste, sayaç şeridi, CSV ve liste raporu aynı sonucu okur;
sonuç `scan_ttl_seconds` boyunca tazedir, bu ekrandan yapılan her yazmadan
sonra bayatlatılır ve ekran yaşını yazar.

DETAY YALNIZ İKİ SÜZGEÇ İÇİN. Not ve müşteri grubunun LİSTE karşılığı yok
(`ord_.DETAIL_FILTERS`). Bu ikisi seçilirse detay okuması da arka planda,
istekler arasında `DETAIL_PACE` bekleyerek koşar (geçidin payını tek başına
yemesin); `detail_cap` ve `updatedAt` anahtarlı bellek önbelleği frendir.
Detayı henüz okunmamış aday varsa yanıt `partial: True` döner ve ekran
"bazı süzgeçler eksik uygulandı" der — sessiz yanlış liste ÜRETİLMEZ.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner. İstisna dışarı sızmaz.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import orders as ord_
from . import stages

#: Tarama tavanı. Canlıda birkaç düzine sipariş var; tavan büyümeye yer bırakır
#: ve bozuk `meta` yüzünden sonsuz sayfalamayı engeller. Tavan yakalanırsa
#: ekran "liste eksik olabilir" der — sessiz kırpma yapılmaz.
SCAN_CAP = 2_000

#: Tek taramada en çok kaç sipariş DETAYLANDIRILIR — yalnız not/müşteri grubu
#: süzgeci ve CSV'nin tutar dökümü için. Her detay bir HTTP isteği ve geçit
#: dakikada 55 istekte tutuyor; önbellekle ikinci okuma bedavaya gelir.
DETAIL_CAP = 300

#: Ortak taramanın tazelik süresi (saniye). `scan_ttl_seconds` ayarıyla
#: değişir. Kısa tutmak yeni siparişi çabuk gösterir ama her tazeleme sipariş,
#: fatura, gönderi, iade ve seçilen kargo listelerinin tamamını okur.
SCAN_TTL = 180

#: İstek taramayı en çok bu kadar bekler (saniye). Küçük mağazada tarama bu
#: sürede biter ve ilk açılış dolu gelir; bitmezse ekran "tarama sürüyor" der
#: ve bir kez daha sorar. `store.scan` beklemeyi zaten 5 saniyede keser.
SCAN_WAIT = 5.0

#: `store.scan` içindeki anahtarlar. Modülün kendi köşesinde (`scoped`) durur.
SCAN_KEY = "orders"
DETAIL_KEY = "details"

#: Arka plan detay okumasında iki istek arası bekleme (saniye). Geçidin payı
#: bütün ekranlarındır; detay okuması dakikada ~25 isteği geçmez, kalanı
#: öteki ekranlara kalır.
DETAIL_PACE = 2.0

#: Yan listelerin ekranda görünen adları (`_side`).
SIDE_LABELS = {"invoices": "fatura listesi", "shipments": "gönderi listesi",
               "refunds": "iade listesi"}

#: Ortak tarama henüz bitmemişken CSV/rapor isteğine verilen cevap. Ekranda
#: görünür; "tarama" denmez (docs/tasarim-sade-magaza.md §3).
SCAN_RUNNING = "Siparişler hazırlanıyor; birkaç saniye sonra yeniden deneyin."

# Toplu işlem türleri. "ship" KALDIRILDI: toplu kargoya verme Kargo
# Yönetimi sihirbazına devredildi ve orada sipariş sipariş yürür. Yanlış desi
# doğrudan yanlış faturadır ve tek onaylı toplu iş onu gizler.
BATCH_KINDS = ("invoice",)

#: Ödeme kanıtı önbelleğinin ömrü (saniye). Liste ve sayaç uçları arka arkaya
#: çağrılıyor; ikisinin de aynı iki listeyi çekmesi geçidin dakikalık payını
#: boşuna harcardı. Kısa tutulur ve HER YAZMADAN SONRA düşürülür: fatura kesen
#: kişi ekranı yenilediğinde "Ödenmedi" görmeye devam etmemeli.
EVIDENCE_TTL = 15.0

#: Kanıt listeleri YENİDEN ESKİYE istenir. Pencere ölçütü buna dayanır ve
#: `orders.window_floor` sırayı VERİDEN doğrular: Laravel tanımadığı parametreyi
#: sessizce yok sayar, isteği göndermek uygulandığını KANITLAMAZ.
EVIDENCE_SORT = {"sort": "id", "order": "desc"}

#: Tek sayfa. Uç 50'de kırpıyor; daha büyük istemek sessizce yarım sayfa
#: getirirdi. Sipariş başına istek atmamak (N+1) için tavan burada duruyor.
EVIDENCE_PAGE = 50

#: Sipariş durumu bu ekrandan değiştirildiğinde yayınlanan olay (manifest).
STATUS_EVENT = "store.order.status_changed"

#: Tek taramada en çok kaç siparişe aşama SMS'i denenir. Tarama zamanlanmış
#: iştir ve kimse başında durmaz; tavan olmadan bozuk bir pencere ayarı bütün
#: geçmişi tek koşuda müşterilere duyururdu. Tavan yakalanırsa sonuç
#: `truncated: True` döner ve ekran bunu YAZAR.
SWEEP_CAP = 200


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _rma_status(item: dict[str, Any]) -> str:
    """İade talebinin durum ADI — nesne de metin de gelebilir.

    Mağazanın RMA ucu durumu ilişkili kayıt olarak açıyor
    (`{id, title, color}`); `str()` ile metne çevirmek ekrana sözlüğün Python
    yazımını basardı. `title` yoksa `name` denenir, o da yoksa boş dönülür:
    uydurma bir durum adı, "Durum okunamadı" boşluğundan daha yanıltıcıdır.
    """
    status = ord_.pick(item, "status")
    if isinstance(status, dict):
        return ord_.text(ord_.pick(status, "title", "name", "label"))
    return ord_.text(status)


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class OrdersService:
    """Siparişler ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, stage_notify: Any = None,
                 scan: Any = None, category: str = "Mağaza", subcategory: str = "Satış",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish
        # `store.scan` — ağır taramanın arka plan tazeleyicisi (modülün kendi
        # köşesi). Yoksa (testte servis elle kurulunca) tarama isteğin içinde
        # ve önbelleksiz koşar: doğru ama yavaş yol.
        self._scan = scan
        self._detail_pace = DETAIL_PACE
        # `store.notify.stage` — müşteriye giden aşama SMS'i. İSTEĞE BAĞLI:
        # Bildirimler ekranı kapalıysa sipariş akışı aynen çalışır, yalnız
        # müşteri bilgilendirilmez ve bu ekran nedenini söyler (K7).
        self._stage_notify = stage_notify
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._batch = store.table("batch")
        self._prefs = store.table("prefs")

        #: Detay önbelleği: siparişId → (updatedAt, kayıt). `updatedAt`
        #: değişince kayıt bayat sayılır, yani BAYAT VERİ GÖSTERİLMEZ; amaç
        #: aynı taramanın aynı siparişi ikinci kez çekmesini önlemek.
        self._details: dict[int, tuple[str, dict[str, Any]]] = {}

        #: Ödeme kanıtı (fatura + POS haritası) ve alındığı an.
        self._evidence_index: dict[str, Any] | None = None
        self._evidence_at = 0.0

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        """Kanal KODU — yalnız `configuration()` çağrısında kullanılır."""
        return str(self._config.get("channel") or "default")

    @property
    def _channel_id(self) -> int:
        """Sipariş listesi süzgecine giden kanal KİMLİĞİ. 0 = süzme yok.

        TUZAK — CANLIDA DOĞRULANDI: `/orders?channel=` kimlik bekliyor.
        `channel=default` sıfır kayıt döndürüyor ve Laravel hata vermiyor;
        kanal kodunu göndermek listeyi sessizce boşaltırdı. Kimlik verilmezse
        süzgeç HİÇ gönderilmez — tek kanallı mağazada doğru davranış budur.
        """
        return max(0, ord_.as_int(self._config.get("channel_id"), 0))

    @property
    def _detail_cap(self) -> int:
        return max(0, min(2_000, ord_.as_int(self._config.get("detail_cap"), DETAIL_CAP)))

    @property
    def _stage_dry_run(self) -> bool:
        """Aşama SMS'i freninin ÜÇÜNCÜ katmanı — tetikleyicinin kendi bayrağı.

        Diğer ikisi Bildirimler tarafındadır (`platform.notify.sms.dry_run` ve
        `lifecycle_sms_dry_run`). Üçü de kapanmadan tek bir gerçek SMS çıkmaz.
        Varsayılan AÇIK: bir siparişi kargoya vermek, müşteriye mesaj göndermeyi
        de kapsamamalı — o karar ayrıca verilir.
        """
        return bool(self._config.get("stage_sms_dry_run", True))

    @property
    def _stage_lookback(self) -> int:
        """"Siparişiniz alındı" taramasının gün penceresi. 0 = pencere yok.

        Pencere olmasaydı aşama SMS'i ilk açıldığında tarama geçmişteki bütün
        siparişleri "yeni" görürdü; canlıda 1.800 müşteriye "siparişiniz alındı"
        gitmesi geri alınamaz.
        """
        return max(0, ord_.as_int(self._config.get("stage_sms_lookback_days"), 3))

    def _base_filters(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Mağazaya GERÇEKTEN gönderilecek süzgeçler."""
        base = dict(ord_.store_filters(filters))
        if self._channel_id:
            base["channel"] = self._channel_id
        return base

    @property
    def _page_size(self) -> int:
        return max(10, min(200, ord_.as_int(self._config.get("page_size"), 50)))

    @property
    def _scan_cap(self) -> int:
        return max(100, min(SCAN_CAP, ord_.as_int(self._config.get("scan_cap"), SCAN_CAP)))

    @property
    def _scan_ttl(self) -> int:
        return max(30, min(900, ord_.as_int(self._config.get("scan_ttl_seconds"), SCAN_TTL)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    async def _prefs_view(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler mağazayı ETKİLEMEZ. Durum adı yalnız bu ekranda görünen
        yazıyı, sipariş no biçimi yalnız gösterimi, iptal süresi yalnız bu
        ekrandan yapılan iptali sınırlar.
        """
        stored = await self._all_prefs()
        names = stored.get("status_names")
        try:
            parsed = json.loads(names) if names else {}
        except ValueError:
            parsed = {}
        return {
            "statusNames": ord_.clean_status_names(parsed or self._config.get("status_names")),
            "orderNoFormat": stored.get("order_no_format")
            or str(self._config.get("order_no_format") or "#{no}"),
            "cancelWindowHours": ord_.as_int(
                stored.get("cancel_window_hours",
                           self._config.get("cancel_window_hours")), 0),
            "lateDays": max(1, ord_.as_int(
                stored.get("late_days", self._config.get("late_days")), 3)),
        }

    # ------------------------------------------------------ yerel tablolar

    async def _all_prefs(self) -> dict[str, str]:
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))
            return {}
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def _set_pref(self, key: str, value: str, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (key, value, actor, _now()),
        )

    async def _record(self, *, order_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(order_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(order_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde gizlemek yetmez."""
        return ord_.reason_error(reason)

    def _rows(self, raw: list[Any], prefs: dict[str, Any],
              sides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Ham kayıtlar → satırlar. `sides` verilirse sığ satırın fatura/gönderi/
        iade eksiği yan listelerden tamamlanır (`ord_.sides_of`)."""
        today = ord_.today_iso()
        return [ord_.order_row(item, status_names=prefs["statusNames"],
                               no_format=prefs["orderNoFormat"], today=today,
                               late_days=prefs["lateDays"],
                               sides=ord_.sides_of(item, sides) if sides is not None else None)
                for item in raw if isinstance(item, dict)]

    async def _chosen_carriers(self, rows: list[dict[str, Any]]) -> dict[str, str]:
        """Sipariş no → müşterinin checkout'ta seçtiği kargo firması. EN ÇOK TEK İSTEK.

        Sipariş listesi ucu bu alanı VERMİYOR; `bbd/orders` veriyor. Sipariş
        başına detay okumak 50 satırlık sayfada 50 istek ederdi.

        Ortak tarama `bbd/orders`u zaten okuduysa ve sayfadaki her sipariş o
        okumada GÖRÜLDÜYSE istek atılmaz: checkout'ta seçilen firma sonradan
        değişmez. Görülmemiş (taramadan yeni) bir sipariş varsa ilk sayfa çekilir.

        Patlarsa liste AYAKTA kalır ve taşıyıcı sütunu bugünkü gibi (yalnız
        gönderiden) doldurulur — eksik sütun, açılmayan listeden iyidir (K7).
        """
        if not rows:
            return {}
        if self._scan is not None:
            value = self._scan.peek(SCAN_KEY).get("value") or {}
            seen = value.get("carrierSeen") or set()
            if all(ord_.text(row.get("incrementId")) in seen or ord_.text(row.get("id")) in seen
                   for row in rows):
                return dict(value.get("carriers") or {})
        try:
            payload = await self._api.bbd_orders(page=1, per_page=self._page_size)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("seçilen kargo firmaları okunamadı", error=str(failure))
            return {}
        return ord_.chosen_carriers(payload.get("items") or [])

    async def _announce(self, payload: dict[str, Any]) -> None:
        """Durum değişikliğini olay yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: sipariş mağazada iptal
        edilmiştir, dinleyicinin patlaması onu geri getirmez.
        """
        if self._publish is None:
            return
        try:
            await self._publish(STATUS_EVENT, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=STATUS_EVENT, error=str(failure))

    async def _detail(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Sığ liste satırını detay kaydına çevirir; önbellek `updatedAt` ile.

        Detay okunamazsa SIĞ SATIR geri döner: bir siparişin okunamaması bütün
        listeyi düşürmemeli (K7). O satır `detailed: False` kalır ve detay
        isteyen süzgeçlerin dışında bırakılır.
        """
        order_id = ord_.as_int(ord_.pick(raw, "id"))
        if not order_id:
            return raw
        stamp = ord_.text(ord_.pick(raw, "updated_at"))
        cached = self._details.get(order_id)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            full = await self._api.order(order_id)
        except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün (K7)
            self._log.warning("sipariş detayı okunamadı", orderId=order_id, error=str(failure))
            return raw
        if not isinstance(full, dict) or not ord_.has_detail(full):
            return raw
        if len(self._details) > 4_000:      # sınırsız büyümesin
            self._details.clear()
        self._details[order_id] = (stamp, full)
        return full

    def _cached_detail(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Önbellekte AYNI `updatedAt` ile duran detay; yoksa kaydın kendisi. İSTEK YOK."""
        if ord_.has_detail(raw):
            return raw
        cached = self._details.get(ord_.as_int(ord_.pick(raw, "id")))
        if cached and cached[0] == ord_.text(ord_.pick(raw, "updated_at")):
            return cached[1]
        return raw

    async def _fill_details(self, raws: list[dict[str, Any]], *,
                            pace: float = 0.0) -> dict[str, int]:
        """Detayı önbellekte olmayan kayıtları SIRAYLA okur; okunanı önbelleğe yazar.

        `pace` iki istek arasındaki beklemedir: arka plan okuması geçidin
        payını tek başına yemesin. İstisna fırlatmaz — okunamayan kayıt sığ
        kalır ve sayılır (K7).
        """
        fetched = failed = 0
        for raw in raws:
            if self._cached_detail(raw) is not raw:
                continue
            if (fetched or failed) and pace > 0:
                await asyncio.sleep(pace)
            if await self._detail(raw) is raw:
                failed += 1
            else:
                fetched += 1
        return {"fetched": fetched, "failed": failed}

    async def _load_details(self) -> dict[str, int]:
        """Arka plan detay okumasının GÖVDESİ — `store.scan` görevinde koşar.

        Ortak taramanın son değerindeki siparişlerin (yeniden eskiye, en çok
        `detail_cap`) detayı önbellekte yoksa okunur. Önbellek `updatedAt`
        anahtarlı olduğu için ikinci koşu yalnız DEĞİŞEN siparişleri okur.
        """
        report = self._scan.peek(SCAN_KEY) if self._scan is not None else {}
        value = report.get("value") or {}
        raws = [raw for raw in (value.get("orders") or []) if isinstance(raw, dict)]
        return await self._fill_details(raws[: self._detail_cap], pace=self._detail_pace)

    async def _ensure_details(self, raws: list[dict[str, Any]], *, refresh: bool) -> bool:
        """Not/müşteri grubu süzgeci için detayları hazırlatır. Döner: hâlâ sürüyor mu.

        `store.scan` varsa okuma ARKA PLANDA koşar ve istek en çok `SCAN_WAIT`
        bekler; bitmeyen kısım için yanıt `partial` döner, ekran bir kez daha
        sorar. Yoksa (servis elle kurulmuş) okuma burada, beklemesiz yapılır.
        """
        if self._scan is None:
            await self._fill_details(raws[: self._detail_cap])
            return False
        try:
            report = await self._scan.read(DETAIL_KEY, self._load_details, ttl=self._scan_ttl,
                                           refresh=refresh, wait=SCAN_WAIT)
        except Exception as failure:  # noqa: BLE001 — tazeleyici bizi düşürmez (K7)
            self._log.warning("detay okuması başlatılamadı", error=str(failure))
            return False
        return bool(report.get("running"))

    # ------------------------------------------------------- ödeme kanıtı

    def _drop_evidence(self) -> None:
        """Yazmadan sonra kanıt bayattır: fatura kesildi, POS durumu değişti."""
        self._evidence_index = None
        self._evidence_at = 0.0

    def _after_write(self, *order_ids: int) -> None:
        """Bu ekrandan GERÇEK bir yazma yapıldı: kanıt, tarama ve detay bayattır.

        Tarama silinmez, BAYAT işaretlenir (`store.scan.invalidate`): ekran eski
        değeri göstermeye devam eder, yenisi bir sonraki okumada gelir. Yazma
        isteği yeni taramayı BEKLEMEZ — kaçtığımız arıza tam olarak buydu.
        """
        self._drop_evidence()
        for order_id in order_ids:
            self._details.pop(ord_.as_int(order_id), None)
        if self._scan is not None:
            self._scan.invalidate(SCAN_KEY)
            self._scan.invalidate(DETAIL_KEY)

    @staticmethod
    def _complete(payload: dict[str, Any], items: list[Any]) -> bool:
        """Bu tek sayfa kaynağın TAMAMI mı? Değilse "kayıt yok" sonucu çıkarılamaz."""
        meta = payload.get("meta") or {}
        total = ord_.as_int(meta.get("total"), -1)
        if total < 0:
            # `meta` gelmediyse dolu sayfa "devamı var" demektir.
            return len(items) < EVIDENCE_PAGE
        return not payload.get("truncated") and total <= len(items)

    async def _evidence(self) -> dict[str, Any]:
        """Ödeme kanıtı — İKİ LİSTE İSTEĞİ, sipariş başına istek YOK (N+1 yasak).

        Bagisto'nun sipariş listesi ucu ödeme DURUMU taşımıyor, yalnız ödeme
        YÖNTEMİ. Bilgi başka yerde duruyor ve ikisi birbirini doğruluyor:
        faturanın `state` alanı ile POS denemesinin `state` alanı.

        BİR KAYNAK PATLARSA DİĞERİ ÇALIŞIR (K7): eksik kaynak `…Ok: False`
        olarak işaretlenir ve o eksiklik ekranda yazılır. Kanıt hiç
        toplanamazsa satırlar bugünkü davranışta kalır ("Bilinmiyor"), sessizce
        "Ödenmedi" yazılmaz.
        """
        now = time.monotonic()
        if self._evidence_index is not None and now - self._evidence_at < EVIDENCE_TTL:
            return self._evidence_index

        invoices: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        warnings: list[str] = []
        invoice_ok = attempt_ok = True
        invoice_complete = attempt_complete = True

        # İKİ LİSTE AYNI ANDA istenir: birbirinden bağımsızlar ve ikisi de tek
        # sayfa. Sırayla beklemek ekranın açılışına bir istek süresi ekliyordu.
        invoice_payload, attempt_payload = await asyncio.gather(
            self._api.invoices(dict(EVIDENCE_SORT), per_page=EVIDENCE_PAGE),
            self._api.bbd_payment_attempts({}, per_page=EVIDENCE_PAGE),
            return_exceptions=True)

        if isinstance(invoice_payload, Exception):
            # Kanıt eksik kalır, ekran kalkar (K7).
            invoice_ok = False
            warnings.append(f"fatura listesi okunamadı ({self._fail(invoice_payload)})")
            self._log.warning("ödeme kanıtı: fatura listesi okunamadı",
                              error=str(invoice_payload))
        elif isinstance(invoice_payload, dict):
            invoices = [item for item in (invoice_payload.get("items") or [])
                        if isinstance(item, dict)]
            invoice_complete = self._complete(invoice_payload, invoices)

        if isinstance(attempt_payload, Exception):
            # POS ucu yayında olmayabilir.
            attempt_ok = False
            warnings.append(f"sanal POS denemeleri okunamadı ({self._fail(attempt_payload)})")
            self._log.info("ödeme kanıtı: POS denemeleri okunamadı",
                           error=str(attempt_payload))
        elif isinstance(attempt_payload, dict):
            attempts = [item for item in (attempt_payload.get("items") or [])
                        if isinstance(item, dict)]
            attempt_complete = self._complete(attempt_payload, attempts)

        index = ord_.payment_index(invoices, attempts)
        index.update({
            "invoiceOk": invoice_ok, "attemptOk": attempt_ok,
            "invoiceComplete": invoice_complete, "attemptComplete": attempt_complete,
            "invoiceRead": len(invoices), "attemptRead": len(attempts),
            "warnings": warnings,
        })
        if index["unboundInvoices"]:
            # Fatura kaydında `orderId` NULL geliyor ve bağ `orderIncrementId`
            # üzerinden kuruluyor. İkisi de yoksa fatura hiçbir siparişe
            # bağlanamaz; SESSİZ KALMAK herkesi "Ödenmedi" gösterirdi.
            index["warnings"].append(
                f"{index['unboundInvoices']} fatura siparişe bağlanamadı "
                "(`orderIncrementId` ve `orderId` boş geldi)")
        self._evidence_index = index
        self._evidence_at = now
        return index

    async def _paid_rows(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                                   dict[str, Any]]:
        """Satırların ödeme durumunu kanıtla yeniden çözer."""
        index = await self._evidence()
        return [ord_.with_payment(row, index) for row in rows], index

    @staticmethod
    def _evidence_view(index: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Ekranın kanıt şeridi: hangi kaynak okundu, kaç satır bankaya sorulmalı."""
        return {
            "invoiceOk": bool(index.get("invoiceOk")),
            "posOk": bool(index.get("attemptOk")),
            "complete": bool(index.get("invoiceComplete") and index.get("attemptComplete")),
            "invoiceRead": ord_.as_int(index.get("invoiceRead")),
            "posRead": ord_.as_int(index.get("attemptRead")),
            "invoiceFloorDay": ord_.text(index.get("invoiceFloorDay")),
            "posFloorDay": ord_.text(index.get("attemptFloorDay")),
            "warnings": list(index.get("warnings") or []),
            "uncertain": sum(1 for row in rows if row.get("paymentAttention")),
        }

    # ======================================================= ortak tarama

    def _side(self, name: str, payload: Any) -> tuple[dict[str, Any] | None, str]:
        """Yan listeyi siparişe göre gruplar — ya da neden BİLİNMİYOR olduğunu söyler."""
        label = SIDE_LABELS.get(name, name)
        if isinstance(payload, Exception):
            self._log.warning("yan liste okunamadı", side=name, error=str(payload))
            return None, f"{label} okunamadı ({self._fail(payload)})"
        if not isinstance(payload, dict):
            return None, f"{label} okunamadı"
        if payload.get("truncated"):
            return None, f"{label} tavana dayandı"
        index = ord_.side_index(payload.get("items") or [])
        if index["unbound"]:
            return None, f"{label}: {index['unbound']} kayıt siparişe bağlanamadı"
        return index, ""

    async def _load_scan(self) -> dict[str, Any]:
        """Ortak taramanın GÖVDESİ — `store.scan` arka plan görevinde koşar.

        BEŞ LİSTE, SİPARİŞ BAŞINA İSTEK YOK: sipariş (yeniden eskiye — tavan
        yakalanırsa düşen EN ESKİLER olsun), fatura, gönderi, iade ve
        `bbd/orders` (checkout'ta seçilen kargo). Birbirinden bağımsızlar,
        aynı anda istenir; her biri kendi içinde sayfa sayfa ve sırayla yürür.

        Süzgeç mağazaya GÖNDERİLMEZ (yalnız ayardaki kanal kimliği): tek
        tarama her süzgece, her çipe ve iki uca birden yeter. Süzme `matches`
        ile burada yapılır.

        Sipariş listesi okunamazsa HATA FIRLATIR: `store.scan` yakalar, zarfın
        `error` alanına koyar ve bir dakika yeniden denemez. Yan liste
        patlarsa tarama yine başarılıdır; o yan `None` kalır, satırların ilgili
        sütunu "Bilinmiyor" olur ve neden `warnings` içinde yazar (K7).

        Değer yalnız BELLEKTEDİR, diske yazılmaz (kişisel verinin ikinci
        kopyası olmasın — KVKK). Satırlar burada KURULMAZ: tercih (durum adı,
        gecikme eşiği) ve "bugün" her okumada değişebilir.
        """
        query = {**self._base_filters({}), **EVIDENCE_SORT}
        orders, invoices, shipments, refunds, chosen = await asyncio.gather(
            self._api.orders(query, all_pages=True),
            self._api.invoices(dict(EVIDENCE_SORT), all_pages=True),
            self._api.shipments({}, all_pages=True),
            self._api.refunds({}, all_pages=True),
            self._api.bbd_orders({}, all_pages=True),
            return_exceptions=True)
        if isinstance(orders, Exception):
            raise orders
        if not isinstance(orders, dict):
            raise TypeError("Sipariş listesi okunamadı: beklenmeyen yanıt biçimi.")
        raw = [item for item in (orders.get("items") or []) if isinstance(item, dict)]
        truncated = bool(orders.get("truncated")) or len(raw) > self._scan_cap

        sides: dict[str, Any] = {}
        warnings: list[str] = []
        for name, payload in zip(ord_.SIDES, (invoices, shipments, refunds), strict=True):
            sides[name], problem = self._side(name, payload)
            if problem:
                warnings.append(problem)

        carriers: dict[str, str] = {}
        seen: set[str] = set()
        if isinstance(chosen, dict):
            items = [item for item in (chosen.get("items") or []) if isinstance(item, dict)]
            carriers = ord_.chosen_carriers(items)
            seen = {ord_.text(key) for item in items
                    for key in (ord_.pick(item, "increment_id"), item.get("id"))
                    if ord_.text(key)}
        elif isinstance(chosen, Exception):
            self._log.warning("seçilen kargo firmaları okunamadı", error=str(chosen))
        return {"orders": raw[: self._scan_cap], "truncated": truncated, "sides": sides,
                "carriers": carriers, "carrierSeen": seen, "warnings": warnings}

    async def _scan_report(self, *, refresh: bool = False) -> dict[str, Any]:
        """Ortak taramanın zarfı. İstek en çok `SCAN_WAIT` bekler; İSTİSNA FIRLATMAZ."""
        if self._scan is None:
            try:
                value = await self._load_scan()
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("sipariş taraması yapılamadı", error=str(failure))
                return {"state": "error", "value": None, "running": False, "at": "",
                        "ageSeconds": 0, "stale": True, "error": self._fail(failure)}
            return {"state": "ready", "value": value, "running": False, "at": _now(),
                    "ageSeconds": 0, "stale": False, "error": ""}
        try:
            return await self._scan.read(SCAN_KEY, self._load_scan, ttl=self._scan_ttl,
                                         refresh=refresh, wait=SCAN_WAIT)
        except Exception as failure:  # noqa: BLE001 — tazeleyici bizi düşürmez (K7)
            self._log.warning("sipariş taraması okunamadı", error=str(failure))
            return {"state": "error", "value": None, "running": False, "at": "",
                    "ageSeconds": 0, "stale": True, "error": self._fail(failure)}

    @staticmethod
    def _scan_view(report: dict[str, Any] | None) -> dict[str, Any]:
        """Ekranın "bu sayılar ne zamandan" sorusuna verdiği cevap.

        Panel yaşı yazar ("2 dakika önce") ve tarama sürüyorsa BİR KEZ daha
        sorar. Sessiz bayat sayı yok.
        """
        report = report or {}
        return {"state": ord_.text(report.get("state")) or "empty",
                "running": bool(report.get("running")),
                "at": ord_.text(report.get("at")),
                "ageSeconds": ord_.as_int(report.get("ageSeconds")),
                "stale": bool(report.get("stale")),
                "error": ord_.text(report.get("error"))}

    async def _scanned(self, wanted: dict[str, Any], prefs: dict[str, Any], *,
                       refresh: bool = False) -> dict[str, Any]:
        """Ortak taramadan, süzgece hazır satırlar — liste, sayaç, CSV ve rapor ORTAK.

        Satırlar her okumada YENİDEN kurulur (tercih ve "bugün" değişebilir) ve
        ödeme kanıtıyla çözülür: kanıt iki liste isteğidir, kaç sipariş
        taranırsa taransın. Süzme (`matches`) kanıtla çözülmüş satır üzerinde
        yapılır — yoksa "ödeme durumu" süzgeci sığ satırlarda hep boş dönerdi.

        Not/müşteri grubu süzgecinde detay arka planda okunur; detayı henüz
        okunmamış aday varsa `partial` döner (`pending` = okuma sürüyor).
        """
        report = await self._scan_report(refresh=refresh)
        view = self._scan_view(report)
        value = report.get("value")
        if not isinstance(value, dict):
            error = view["error"]
            return {"ready": False, "connected": not error, "error": error, "scan": view,
                    "rows": [], "raws": [], "truncated": False, "partial": False,
                    "pending": False, "index": ord_.empty_index(), "warnings": []}

        raws = [raw for raw in (value.get("orders") or []) if isinstance(raw, dict)]
        detail = ord_.needs_detail(wanted)
        pending = await self._ensure_details(raws, refresh=refresh) if detail else False
        rows = self._rows([self._cached_detail(raw) for raw in raws], prefs,
                          sides=value.get("sides") or {})
        rows, index = await self._paid_rows(rows)
        ord_.apply_chosen_carrier(rows, value.get("carriers") or {})
        partial = False
        if detail:
            loose = ord_.without_detail(wanted)
            partial = any(not row["detailed"] and ord_.matches(row, loose) for row in rows)
        return {"ready": True, "connected": True, "error": "", "scan": view, "rows": rows,
                "raws": raws, "truncated": bool(value.get("truncated")), "partial": partial,
                "pending": pending and partial, "index": index,
                "warnings": list(value.get("warnings") or [])}

    # ================================================================ liste

    async def orders(self, *, filters: dict[str, Any] | None = None, page: int = 1,
                     size: int = 0, refresh: bool = False) -> dict[str, Any]:
        """Sipariş listesi. Süzgeç kümesine göre sunucu sayfalar ya da ortak tarama okunur.

        `refresh` ("Yenile" düğmesi) taramayı tazelik süresini beklemeden yeniler.
        """
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        per_page = size or self._page_size
        scanned = ord_.needs_scan(wanted)

        empty = {"ok": True, "items": [], "total": 0, "page": page, "size": per_page,
                 "pages": 0, "scanned": scanned, "truncated": False, "partial": False,
                 "pending": False, "ready": True, "warnings": [],
                 "scan": self._scan_view(None),
                 "summary": ord_.summary([]), "prefs": prefs,
                 "evidence": self._evidence_view(ord_.empty_index(), [])}

        try:
            if scanned:
                found = await self._scanned(wanted, prefs, refresh=refresh)
                if not found["ready"]:
                    # "Tarama boş döndü" ile "tarama daha bitmedi" AYRI şeylerdir;
                    # ikincisinde boş liste "sipariş yok" diye okunmamalı.
                    return {**empty, "connected": found["connected"],
                            "error": found["error"], "ready": False, "scan": found["scan"],
                            "scannedCount": 0}
                rows = found["rows"]
                hits = [row for row in rows if ord_.matches(row, wanted)]
                start = max(0, (page - 1) * per_page)
                window = hits[start:start + per_page]
                return {**empty, "connected": True, "error": "", "items": window,
                        "total": len(hits), "pages": max(1, -(-len(hits) // per_page)),
                        "truncated": found["truncated"], "partial": found["partial"],
                        "pending": found["pending"], "scan": found["scan"],
                        "warnings": found["warnings"], "scannedCount": len(rows),
                        "summary": ord_.summary(hits),
                        "evidence": self._evidence_view(found["index"], hits)}

            payload = await self._api.orders(self._base_filters(wanted), page=page,
                                             per_page=per_page)
            rows, index = await self._paid_rows(self._rows(payload.get("items") or [], prefs))
            ord_.apply_chosen_carrier(rows, await self._chosen_carriers(rows))
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("sipariş listesi okunamadı", error=str(failure))
            return {**empty, "connected": False, "error": self._fail(failure),
                    "scannedCount": 0}

        meta = payload.get("meta") or {}
        return {
            **empty, "connected": True, "error": "", "items": rows,
            "evidence": self._evidence_view(index, rows),
            "total": ord_.as_int(meta.get("total"), len(rows)),
            "page": ord_.as_int(meta.get("currentPage"), page),
            "size": ord_.as_int(meta.get("perPage"), per_page),
            "pages": ord_.as_int(meta.get("lastPage"), 1),
            "scannedCount": len(rows),
            # Sayfa toplamı SAYFANIN toplamıdır; süzgecin tamamının cirosu
            # `/summary` ucundan gelir. İkisini karıştırmak, 29 sayfalık bir
            # süzgecin cirosunu ilk sayfanınki sanmak olurdu.
            "summary": ord_.summary(rows),
        }

    async def overview(self, *, filters: dict[str, Any] | None = None,
                       refresh: bool = False) -> dict[str, Any]:
        """Çip sayaçları + süzgecin TAMAMININ mini toplamı — ORTAK taramadan.

        Sipariş başına istek YOK. "Kargoda" ve "Geciken" çipleri gönderi
        durumuna bakıyor; o bilgi artık gönderi LİSTESİNDEN geliyor (eskiden
        en çok 300 sipariş tek tek okunuyordu). Liste ucuyla AYNI taramayı okur:
        ekran ikisini art arda istese de mağazaya tek tarama gider.
        """
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        found = await self._scanned(wanted, prefs, refresh=refresh)
        if not found["ready"]:
            # Sayaç UYDURULMAZ: tarama bitmeden "0" yazmak "bekleyen iş yok" demekti.
            return {"ok": True, "connected": found["connected"], "error": found["error"],
                    "ready": False, "counts": {}, "summary": ord_.summary([]),
                    "truncated": False, "partial": False, "pending": False, "warnings": [],
                    "scannedCount": 0, "scan": found["scan"],
                    "evidence": self._evidence_view(ord_.empty_index(), [])}

        rows = found["rows"]
        # Çip sayaçları çipin KENDİSİ hariç süzgeçlere göre hesaplanır: bir çipe
        # basınca diğer çiplerin sayacının sıfırlanması, kullanıcıyı seçimini
        # kaldırmadan başka çipe geçemez hâle getirirdi.
        without_chip = {**wanted, "chip": ""}
        base = [row for row in rows if ord_.matches(row, without_chip)]
        counts: dict[str, int | None] = dict(ord_.chip_counts(base, wanted.get("today")))
        if any(not row.get("shipKnown") for row in base):
            # Gönderi listesi okunamadıysa "Kargoda 0" bir yalandır: sayaç BOŞ kalır.
            counts["shipped"] = None
            counts["late"] = None
        return {
            "ok": True, "connected": True, "error": "", "ready": True,
            "counts": counts,
            "summary": ord_.summary([row for row in base if ord_.matches(row, wanted)]),
            "truncated": found["truncated"], "partial": found["partial"],
            "pending": found["pending"], "warnings": found["warnings"],
            "scannedCount": len(rows), "scan": found["scan"],
            "evidence": self._evidence_view(found["index"], base),
        }

    # ================================================================ künye

    async def card(self, order_id: int) -> dict[str, Any]:
        """Sipariş künyesi: özet, kalemler, ödeme, gönderi, fatura, iade.

        `store.order.card` yeteneğinin de gövdesidir — Müşteriler ve Talepler
        ekranları bu yanıtı okur.
        """
        prefs = await self._prefs_view()
        oid = int(order_id)
        # DÖRT BAĞIMSIZ OKUMA AYNI ANDA: sipariş, ödeme işlemleri, iade talepleri
        # ve ödeme kanıtı (15 sn önbellekli). Sırayla beklemek çekmecenin
        # açılışına üç istek süresi ekliyordu; istek SAYISI değişmez.
        raw, tx_payload, rma_payload, _ = await asyncio.gather(
            self._api.order(oid),
            self._api.transactions({"order_id": oid}),
            self._api.bbd_return_requests({"order_id": oid}),
            self._evidence(),
            return_exceptions=True)
        if isinstance(raw, Exception) or not isinstance(raw, dict):
            failure = raw if isinstance(raw, Exception) else RuntimeError("Sipariş okunamadı.")
            self._log.warning("sipariş okunamadı", orderId=order_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure)}

        # Çekmece de kanıtla çözülmüş durumu gösterir: listede "Belirsiz" görüp
        # açınca "Ödenmedi" okumak, iki ekranın birbirini yalanlaması olurdu.
        rows, index = await self._paid_rows(self._rows([raw], prefs))
        if not rows:
            return {"ok": False, "connected": True, "error": "Sipariş kaydı boş döndü."}
        row = rows[0]

        warnings: list[str] = []
        transactions: list[dict[str, Any]] = []
        if isinstance(tx_payload, Exception):
            warnings.append(f"ödeme işlemleri: {self._fail(tx_payload)}")  # parça parça (K7)
        elif isinstance(tx_payload, dict):
            transactions = [item for item in (tx_payload.get("items") or [])
                            if isinstance(item, dict)]

        # CANLIDA DOĞRULANDI (2026-08-16): `GET /api/admin/bbd/return-requests`
        # 200 dönüyor ve gerçek RMA kayıtları geliyor. Bir dönem bu uç yayında
        # değildi ve ekran bölümü "uç yayınlanmadı" diye kapatıyordu; artık öyle
        # değil. Aşağıdaki hata dalı YİNE DE DURUYOR (K7): uç bir gün geri
        # çekilirse künye ayakta kalmalı, sipariş kartı iade yüzünden çökmemeli.
        #
        # TUZAK — UÇ `order_id` SÜZGECİNİ UYGULAMIYOR. Denetleyici
        # (`ReturnRequestController::applyFilters`) yalnız `status`, `from`, `to`
        # okur; `order_id` sessizce yok sayılır ve uç BÜTÜN talepleri döndürür.
        # Süzmeyi yerelde yapmayan kod, 20 numaralı siparişin künyesinde 9 ve 11
        # numaralı siparişlerin taleplerini gösterirdi — hata da vermeden.
        # Parametre yine gönderiliyor: uç bir gün süzgeci öğrenirse istek
        # kendiliğinden ucuzlar, yerel süzgeç de zararsız kalır.
        returns: list[dict[str, Any]] = []
        returns_available = True
        if isinstance(rma_payload, Exception):
            returns_available = False                       # uç geri çekilebilir (K7)
            warnings.append(f"iade talepleri: {self._fail(rma_payload)}")
        elif isinstance(rma_payload, dict):
            returns = [item for item in (rma_payload.get("items") or [])
                       if isinstance(item, dict)
                       and ord_.as_int(ord_.pick(item, "order_id")) == oid]

        return {
            "ok": True, "connected": True, "error": "", "warnings": warnings,
            "order": row,
            "items": ord_.item_rows(raw),
            "money": ord_.money_view(raw),
            "billing": ord_.address_view(ord_.address_of(raw, "billing")),
            "shipping": ord_.address_view(ord_.address_of(raw, "shipping")),
            "shipments": ord_.shipment_rows(raw),
            "invoices": ord_.invoice_rows(raw),
            "refunds": ord_.refund_rows(raw),
            "transactions": [{
                "id": ord_.as_int(ord_.pick(item, "id")),
                # Canlıda `type` ödeme sağlayıcısının kodu (`kuveytturk`);
                # `paymentTitle` insan okuyabilir olanıdır.
                "type": ord_.text(ord_.pick(item, "payment_title", "type")),
                "status": ord_.text(ord_.pick(item, "status")),
                "amount": ord_.kurus(ord_.pick(item, "amount")),
                "createdAt": ord_.text(ord_.pick(item, "created_at"))[:19],
            } for item in transactions],
            "returns": [{
                "id": ord_.as_int(ord_.pick(item, "id")),
                # TUZAK — CANLIDA DOĞRULANDI (2026-08-16): `status` DÜZ METİN
                # DEĞİL, `{id, title, color}` nesnesi geliyor. Doğrudan metne
                # çeviren kod ekrana `{'id': 5, 'title': 'İade Edildi', ...}`
                # yazardı: hata yok, sadece okunamayan bir hücre. Metin de
                # gelebilir (uç sözleşmesi değişirse) — iki biçim de çözülür.
                "status": _rma_status(item),
                "reason": ord_.text(ord_.pick(item, "reason")),
                "createdAt": ord_.text(ord_.pick(item, "created_at"))[:19],
            } for item in returns],
            "returnsAvailable": returns_available,
            "cancelBlock": ord_.cancel_block(row, window_hours=prefs["cancelWindowHours"]),
            # GEÇİŞ LİSTESİ SUNUCUDAN GELİR, ekranda kopyalanmaz: matris tek
            # yerde durur ve ikisi ayrıştığında ekran olmayan bir seçeneği
            # sunup 422 aldırırdı.
            "statusTargets": [
                {"value": code, "label": ord_.status_label(code, prefs.get("statusNames"))}
                for code in ord_.status_targets(row)
            ],
            "evidence": self._evidence_view(index, rows),
            "prefs": prefs,
        }

    async def comments(self, order_id: int) -> dict[str, Any]:
        """Sipariş notları ve müşteri yazışması."""
        try:
            payload = await self._api.order_comments(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure), "items": []}
        return {"ok": True, "connected": True, "error": "", "items": [{
            "id": ord_.as_int(ord_.pick(item, "id")),
            "comment": ord_.text(ord_.pick(item, "comment")),
            "notified": bool(ord_.as_int(ord_.pick(item, "customer_notified"), 0)),
            "createdAt": ord_.text(ord_.pick(item, "created_at"))[:19],
        } for item in (payload.get("items") or []) if isinstance(item, dict)]}

    async def reference(self) -> dict[str, Any]:
        """Süzgeçlerin beslendiği referans listeler. Geçit 15/30 dk önbellekli."""
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "statuses": [{"value": code, "label": label}
                         for code, label in ord_.STATUS_LABELS.items()],
            "paymentStates": [{"value": code, "label": label}
                              for code, label in ord_.PAYMENT_LABELS.items()],
            "dateFields": [{"value": code, "label": label}
                           for code, label in ord_.DATE_FIELDS.items()],
            "channels": [], "customerGroups": [], "carriers": [], "stale": False,
        }
        # İki bağımsız okuma AYNI ANDA (ikisi de geçitte önbellekli olabilir).
        snapshot, carriers = await asyncio.gather(self._api.snapshot(),
                                                  self._api.bbd_carriers(),
                                                  return_exceptions=True)
        if isinstance(snapshot, Exception) or not isinstance(snapshot, dict):  # K7
            out["connected"] = False
            out["error"] = (self._fail(snapshot) if isinstance(snapshot, Exception)
                            else "Mağazaya ulaşılamadı.")
            return out

        parts = snapshot.get("parts") or {}
        out["channels"] = [{"code": ord_.text(ord_.pick(item, "code")),
                            "name": ord_.text(ord_.pick(item, "name"))}
                           for item in parts.get("channels") or [] if isinstance(item, dict)]
        out["customerGroups"] = [{"id": ord_.as_int(ord_.pick(item, "id")),
                                  "name": ord_.text(ord_.pick(item, "name"))}
                                 for item in parts.get("customer_groups") or []
                                 if isinstance(item, dict)]
        out["stale"] = bool(snapshot.get("stale"))
        out["storedAt"] = ord_.text(snapshot.get("storedAt"))

        if isinstance(carriers, dict):
            out["carriers"] = [{"code": ord_.text(ord_.pick(item, "code")),
                                "name": ord_.text(ord_.pick(item, "name", "title"))}
                               for item in (carriers.get("items") or [])
                               if isinstance(item, dict)]
        elif isinstance(carriers, Exception):
            # BBD ucu henüz yayında olmayabilir. Taşıyıcı listesi gelmezse kargo
            # firması süzgeci SERBEST METİN olarak çalışmaya devam eder; ekran
            # boş bir açılır kutu göstermez.
            self._log.info("taşıyıcı listesi alınamadı", error=str(carriers))
        return out

    async def audit(self, *, order_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT order_id, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if order_id:
            sql += "WHERE order_id = ? "
            params = (int(order_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"orderId": row["order_id"], "action": row["action"], "reason": row["reason"],
             "actor": row["actor"], "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # =============================================================== yazma

    async def add_comment(self, order_id: int, *, comment: str, notify: bool, reason: str,
                          actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Sipariş notu. `notify=True` MÜŞTERİYE E-POSTA GÖNDERİR — iç yazışma
        değildir; ekran bunu her seferinde yazar."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        body = ord_.text(comment)
        if len(body) < 2:
            return {"ok": False, "error": "Not boş olamaz."}

        await self._record(order_id=order_id, action="add_comment", reason=reason, actor=actor,
                           result="denendi", detail={"notify": bool(notify)})
        try:
            result = await self._api.add_order_comment(int(order_id), comment=body,
                                                       notify=bool(notify), reason=reason,
                                                       actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="add_comment", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="add_comment", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail={"notify": bool(notify)})
        if not dry_run:
            # Not eklemek siparişin `updatedAt`ini değiştirmeyebilir; önbellekteki
            # detay "not yok" demeye devam etmesin. Kanıt ve tarama etkilenmez.
            self._details.pop(ord_.as_int(order_id), None)
            if self._scan is not None:
                self._scan.invalidate(DETAIL_KEY)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "notified": bool(notify)}

    async def cancel(self, order_id: int, *, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Siparişi iptal eder. GERİ ALINAMAZ (TUZAK 8).

        Sipariş yazmadan ÖNCE taze okunur: aradan geçen sürede kargolanmış ya da
        zaten iptal edilmiş olabilir. Pencere ve durum denetimi burada da yapılır
        — arayüzde düğmeyi gizlemek yetkilendirme değildir (K9).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        prefs = await self._prefs_view()
        try:
            raw = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        rows = self._rows([raw], prefs)
        if not rows:
            return {"ok": False, "error": "Sipariş okunamadı."}
        block = ord_.cancel_block(rows[0], window_hours=prefs["cancelWindowHours"])
        if block:
            await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                               result="engellendi", detail={"block": block})
            return {"ok": False, "error": block}

        await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                           result="denendi", detail={"orderNo": rows[0]["orderNo"]})
        try:
            result = await self._api.cancel_order(int(order_id), reason=reason, actor=actor,
                                                  dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="cancel", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok")
        if not dry_run:
            self._after_write(order_id)
        applied = bool(result.get("sent", not dry_run)) and not dry_run
        if applied:
            # Kuru provada olay YAYINLANMAZ: mağazada hiçbir şey değişmedi,
            # dinleyicileri "sipariş iptal edildi" diye uyandırmak yalan olurdu.
            await self._announce({"orderId": int(order_id), "from": rows[0]["status"],
                                  "to": "canceled", "reason": reason})
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "orderNo": rows[0]["orderNo"], "announced": applied}

    async def set_status(self, order_id: int, *, status: str, reason: str, actor: str,
                         dry_run: bool = True) -> dict[str, Any]:
        """Sipariş durumunu ELLE değiştirir.

        NORMALDE DURUM TÜRETİLİR: fatura kesilince `processing`, kargolanınca
        `completed`. Bu uç o türetmenin üstüne bilinçli bir istisna koyar ve
        yalnız istisnalar için vardır — mağaza dışında halledilmiş bir iş,
        yanlış kalmış bir kayıt, elle kapatılan bir sipariş.

        İKİ ŞEY AÇIKÇA SÖYLENİR ve ekranda da yazar:
          · Geçiş matrisi (`ord_.status_block`) burada da uygulanır; sunucu
            aynı denetimi tekrar yapar (K9).
          · SONRAKİ FATURA/GÖNDERİ BU DEĞERİ EZEBİLİR. Bagisto durumu o
            olaylarda yeniden hesaplıyor; elle konan değer kalıcı bir mühür
            değildir. Kullanıcı "kaydettim ama geri döndü" ile karşılaşmasın
            diye bu, yanıtın kendisinde `mayBeOverwritten` ile taşınır.

        Sipariş yazmadan ÖNCE TAZE OKUNUR: aradan geçen sürede kargolanmış ya
        da iptal edilmiş olabilir ve o hâlde geçiş artık geçerli değildir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        hedef = ord_.fold(status)
        prefs = await self._prefs_view()
        try:
            raw = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        rows = self._rows([raw], prefs)
        if not rows:
            return {"ok": False, "error": "Sipariş okunamadı."}
        row = rows[0]

        block = ord_.status_block(row, hedef)
        if block:
            await self._record(order_id=order_id, action="set_status", reason=reason,
                               actor=actor, result="engellendi",
                               detail={"from": row["status"], "to": hedef, "block": block})
            return {"ok": False, "error": block}

        detail = {"orderNo": row["orderNo"], "from": row["status"], "to": hedef}
        await self._record(order_id=order_id, action="set_status", reason=reason,
                           actor=actor, result="denendi", detail=detail)
        try:
            result = await self._api.bbd_set_order_status(int(order_id), status=hedef,
                                                          reason=reason, actor=actor,
                                                          dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="set_status", reason=reason,
                               actor=actor, result="hata",
                               detail={**detail, "error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="set_status", reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok", detail=detail)
        applied = bool(result.get("sent", not dry_run)) and not dry_run
        if not dry_run:
            self._after_write(order_id)
        if applied:
            # Kuru provada olay YAYINLANMAZ: mağazada hiçbir şey değişmedi.
            await self._announce({"orderId": int(order_id), "from": row["status"],
                                  "to": hedef, "reason": reason})
        # EZİLEBİLİRLİĞİ SUNUCU HESAPLAR, BİZ TAHMİN ETMEYİZ. Cevabı vendor'ın
        # kendi türetmesini çalıştırarak buluyor: kısmen faturalanmış bir
        # siparişte `completed` KALICI, tamamı faturalanmış ama kargolanmamışta
        # ilk olayda `processing`e dönüyor. Bu ezberlenebilir bir kural değil;
        # burada tahmin etmek kullanıcıya yanlış söz vermek olurdu.
        gövde = result if isinstance(result, dict) else {}
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "orderNo": row["orderNo"], "from": row["status"], "to": hedef,
                "statusLabel": ord_.status_label(hedef, prefs.get("statusNames")),
                "announced": applied,
                "mayBeOverwritten": bool(gövde.get("revertsOnNextInvoiceOrShipment")),
                # Sunucunun yazdığı serbest metin cümleler: stok, müşteri
                # bildirimi, "kargoya verilmedi" alarmı, kalıcılık, ciro.
                "sideEffects": [ord_.text(line) for line in (gövde.get("sideEffects") or [])
                                if ord_.text(line)]}

    async def invoice(self, order_id: int, *, items: dict[str, int] | None, reason: str,
                      actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Fatura keser. `items` boşsa siparişin TAMAMI faturalanır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = {str(key): max(0, ord_.as_int(value))
                 for key, value in (items or {}).items() if ord_.as_int(key)}

        await self._record(order_id=order_id, action="invoice", reason=reason, actor=actor,
                           result="denendi", detail={"items": clean})
        try:
            result = await self._api.create_invoice(int(order_id), items=clean or None,
                                                    reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(order_id=order_id, action="invoice", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(order_id=order_id, action="invoice", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok", detail={"items": clean})
        if not dry_run:
            # Fatura kesildi: ödeme kanıtı bayat. Yenilenen ekran "Ödenmedi"
            # görmeye devam ederse kullanıcı işini yapmadığını sanır.
            self._after_write(order_id)
        return {"ok": True, "error": "", "dryRun": bool(result.get("dryRun", dry_run)),
                "partial": bool(clean)}

    # `ship()` KALDIRILDI — kargoya verme bu modülün işi DEĞİLDİR.
    #
    # KULLANICININ KURALI: "her şey — mesela sipariş kargoya mı verilecek —
    # farklı yerde 'kargoya ver' olmasın."
    #
    # Metot Bagisto'nun KENDİ gönderi kaydını açıyordu: etiket satın
    # alınmıyor, taşıyıcıya hiç gidilmiyor, paket yola çıkmıyordu. Üstelik
    # gövdeyi `{"shipment": {…}}` sarmalıyla gönderdiği için canlı işlemci
    # `source` ve `items`ı KÖKTEN okuyup boş buluyor ve isteği reddediyordu —
    # yani muhtemelen tek satır bile yazmıyordu. Buna karşılık müşteriye
    # "kargoya verildi" SMS'ini tetikliyordu.
    #
    # Gerçek zincir (gönderi aç → teklif al → müşterinin ödediği firmayı
    # yeğle → etiket SATIN AL → takip numarasını yaz → etiket ve faturayı bas
    # → müşteriye SMS) Kargo Yönetimi'nde koşar. Aşama SMS'inin sahibi de
    # oraya geçti: gerçek takip numarası yalnız orada üretiliyor.
    #
    # `_after_ship` de bu yüzden kaldırıldı; "sipariş alındı" ve "teslim
    # edildi" taramaları (`stage_sweep`) BU MODÜLDE KALIR — onların kaynağı
    # bir tıklama değil, mağazanın durumudur.

    # ========================================================= toplu işlem

    async def batch_preview(self, *, kind: str, order_ids: list[int]) -> dict[str, Any]:
        """FARK TABLOSU. Toplu iş bu tablo gösterilmeden UYGULANMAZ.

        Önizleme yerel tabloya yazılır ve bir jeton döner; uygulama o jetonla
        gelir. Böylece uygulanan şeyin önizlenen şey olduğu kanıtlanır.
        """
        if kind not in BATCH_KINDS:
            return {"ok": False, "error": f"Bilinmeyen toplu işlem: {kind}"}
        ids = [int(item) for item in (order_ids or []) if ord_.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        if len(ids) > 200:
            return {"ok": False,
                    "error": "Tek seferde en çok 200 sipariş. Daha büyük iş için süzgeci daraltın."}

        prefs = await self._prefs_view()
        fresh, missing = await self._selected_rows(ids, prefs)

        # Önizleme de kanıtla çözülmüş ödeme durumuna bakar: listede "Belirsiz"
        # diye seçtirilmeyen sipariş, önizlemede sessizce "uygun" görünmemeli.
        rows, _ = await self._paid_rows(fresh)
        if not rows:
            return {"ok": False, "error": "Seçilen siparişlerin hiçbiri okunamadı."}

        diff = ord_.batch_rows(rows, kind)
        token = uuid.uuid4().hex
        try:
            await self._store.execute(
                f"INSERT INTO {self._batch} (token, kind, params, rows, status, created_at) "
                "VALUES (?, ?, ?, ?, 'preview', ?)",
                (token, kind, json.dumps({"ids": ids}, ensure_ascii=False),
                 json.dumps(diff, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — jeton yazılamazsa uygula reddedilir
            self._log.warning("önizleme kaydedilemedi", error=str(failure))
            return {"ok": False, "error": "Önizleme kaydedilemedi; uygulama açılmadı."}

        return {"ok": True, "error": "", "token": token, "kind": kind, "rows": diff,
                "summary": ord_.batch_summary(diff), "missing": missing}

    async def batch_apply(self, *, token: str, reason: str, actor: str,
                          dry_run: bool = True, carrier: str = "") -> dict[str, Any]:
        """Önizlenen listeyi uygular. Jeton yoksa ya da tüketilmişse reddedilir.

        KURU PROVA ÖNCE ÇALIŞIR — ARAYÜZDE DEĞİL, BURADA. Jeton üç durumdan
        geçer: `preview` → `dry_run` → `applied`. Gerçek uygulama yalnız
        `dry_run` durumundaki jetonu kabul eder; kullanıcı ne olacağını
        GÖRMEDEN mağazaya tek satır yazılmaz. Kuralı yalnız panele koymak, K9'un
        tam olarak yasakladığı şey olurdu: arayüzde gizlemek yetkilendirme
        değildir.

        TAŞIYICI PROVADA SEÇİLİR VE ORADA KALIR. Gerçek uygulama provadakinden
        farklı bir taşıyıcıyla gelirse reddedilir — onaylanan şey neyse o
        uygulanır.

        SIRAYLA yazılır ve YİNELENMEZ: geçit yazma isteklerini tekrarlamıyor
        (zaman aşımına uğrayan fatura uzakta kesilmiş olabilir). Biri patlarsa
        gerisi sürer; sonuç satır satır döner ve KISMİ BAŞARI başarısızlık
        sayılmaz.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        try:
            row = await self._store.fetch_one(
                f"SELECT token, kind, rows, status, params FROM {self._batch} WHERE token = ?",
                (ord_.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not row:
            return {"ok": False,
                    "error": "Önizleme bulunamadı. Liste görülmeden toplu işlem uygulanmaz; "
                             "önizlemeyi yeniden alın."}
        if row["status"] == "applied":
            return {"ok": False, "error": "Bu önizleme zaten uygulandı."}
        if row["status"] not in ("preview", "dry_run"):
            return {"ok": False, "error": f"Önizleme durumu uygun değil: {row['status']}"}
        if not dry_run and row["status"] != "dry_run":
            return {"ok": False,
                    "error": "Önce kuru prova çalıştırılmalı: ne olacağı görülmeden gerçek "
                             "işlem yapılmaz."}

        kind = row["kind"]
        params = self._batch_params(row)
        wanted_carrier = ord_.text(carrier)
        if dry_run:
            params["carrier"] = wanted_carrier
        elif ord_.text(params.get("carrier")) != wanted_carrier:
            return {"ok": False,
                    "error": f"Kuru provada taşıyıcı “{params.get('carrier') or '—'}” idi, "
                             f"şimdi “{wanted_carrier or '—'}” geldi. Onaylanan liste neyse o "
                             "uygulanır; önizlemeyi yeniden alın."}

        diff = json.loads(row["rows"])
        targets = [item for item in diff if not item.get("skipped")]
        if not targets:
            return {"ok": False, "error": "Önizlemede uygulanacak satır yok."}

        results: list[dict[str, Any]] = []
        applied = 0
        for item in targets:
            order_id = int(item["id"])
            # TEK TÜR KALDI (`BATCH_KINDS`), dallanma yok. Kargo dalı
            # buradan çıktı: toplu kargoya verme Kargo Yönetimi'nin işi.
            outcome = await self.invoice(order_id, items=None, reason=reason, actor=actor,
                                         dry_run=dry_run)
            applied += 1 if outcome.get("ok") else 0
            # SMS SONUCU SATIR SATIR TAŞINIR. "Yedi sipariş kargolandı" demek
            # yetmez: hangisinin müşterisine haber verilemediği ve NEDEN
            # verilemediği (numara yok, takip kodu yok) aynı tabloda görünmeli.
            sms = outcome.get("sms") or {}
            results.append({"id": order_id, "orderNo": item.get("orderNo"),
                            "customer": item.get("customer", ""),
                            "ok": bool(outcome.get("ok")), "error": outcome.get("error", ""),
                            "smsSent": bool(sms.get("sent")),
                            "smsNote": ord_.text(sms.get("note"))})

        await self._store.execute(
            f"UPDATE {self._batch} SET status = ?, actor = ?, reason = ?, applied_at = ?, "
            "params = ? WHERE token = ?",
            ("dry_run" if dry_run else "applied", actor, reason, _now(),
             json.dumps(params, ensure_ascii=False), row["token"]),
        )
        if not dry_run:
            self._after_write(*(int(item["id"]) for item in targets))
        failed = len(targets) - applied
        await self._record(order_id=0, action=f"batch_{kind}", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"applied": applied, "failed": failed, "carrier": wanted_carrier,
                                   "token": row["token"]})
        # KISMİ BAŞARI BAŞARISIZLIK DEĞİLDİR. On siparişin üçü patladıysa yedisi
        # gerçekten kargolanmıştır; `ok: False` demek o yediyi ekrandan silmek ve
        # kullanıcıya hepsini yeniden denetmek olurdu (ikinci gönderi = ikinci
        # kargo). Başarısızlar `results` içinde NEDENİYLE durur.
        return {"ok": applied > 0, "error": "", "applied": applied, "failed": failed,
                "partial": failed > 0 and applied > 0, "carrier": wanted_carrier,
                "results": results, "dryRun": dry_run}

    async def _selected_rows(self, ids: list[int], prefs: dict[str, Any]) -> tuple[
            list[dict[str, Any]], list[int]]:
        """Seçilen siparişlerin TAZE satırları — mümkünse sipariş başına istek ATMADAN.

        Önizleme ve manifesto "şu an"ı ister: arada fatura kesilmiş, gönderi
        açılmış olabilir ve ortak tarama bunu henüz bilmeyebilir. Eskiden seçilen
        her sipariş tek tek okunuyordu: 200 seçim 200 istek, geçitte ~4 dakika.

        Şimdi seçilenlerin EN ESKİSİNİN gününden bir gün önce başlayan bir
        PENCERE dört liste isteğiyle TAZE okunur (`_window_rows`). Pencerede
        bulunamayan ya da fatura/gönderi bilgisi kesinleşmeyen sipariş eski
        yoldan, tek tek okunur. Döner: (seçim sırasıyla satırlar, okunamayanlar).
        """
        wanted = list(dict.fromkeys(int(item) for item in ids))
        found, leftovers = await self._window_rows(wanted, prefs)
        missing: list[int] = []
        for order_id in leftovers:
            try:
                raw = await self._api.order(order_id)
            except Exception as failure:  # noqa: BLE001 — K7
                missing.append(order_id)
                self._log.warning("seçilen sipariş okunamadı", orderId=order_id,
                                  error=str(failure))
                continue
            built = self._rows([raw], prefs)
            if built:
                found[order_id] = built[0]
        return [found[order_id] for order_id in wanted if order_id in found], missing

    async def _window_rows(self, ids: list[int], prefs: dict[str, Any]) -> tuple[
            dict[int, dict[str, Any]], list[int]]:
        """Seçimin tarih PENCERESİNİ dört liste isteğiyle taze okur.

        Fatura, gönderi ve iade siparişten SONRA oluşur; seçilenlerin en eskisinin
        gününden (saat dilimi payı için bir gün önceden) başlayan pencere hepsini
        kapsar. Siparişlerin gününü ortak taramanın SON DEĞERİ söyler — yalnız
        bakılır (`peek`), bunun için yeni tarama başlatılmaz.

        Pencere pahalıysa (tahmini liste isteği ≥ seçim sayısı) hiç açılmaz ve
        hepsi tek tek okunur: üç siparişi okumak için dört liste çekmek kazanç
        değildir. Döner: (bulunan satırlar, tek tek okunacaklar).
        """
        if self._scan is None or not ids:
            return {}, list(ids)
        value = self._scan.peek(SCAN_KEY).get("value") or {}
        days = {ord_.as_int(ord_.pick(raw, "id")): ord_.day_of(ord_.pick(raw, "created_at"))
                for raw in (value.get("orders") or []) if isinstance(raw, dict)}
        known = [days[order_id] for order_id in ids if days.get(order_id)]
        if not known:
            return {}, list(ids)
        try:
            since = (date.fromisoformat(min(known)) - timedelta(days=1)).isoformat()
        except ValueError:
            return {}, list(ids)
        in_window = sum(1 for day in days.values() if day and day >= since)
        if 4 * max(1, -(-in_window // EVIDENCE_PAGE)) >= len(ids):
            return {}, list(ids)

        window = {"date_from": since}
        orders, invoices, shipments, refunds = await asyncio.gather(
            self._api.orders({**self._base_filters({}), **window}, all_pages=True),
            self._api.invoices(dict(window), all_pages=True),
            self._api.shipments(dict(window), all_pages=True),
            self._api.refunds(dict(window), all_pages=True),
            return_exceptions=True)
        if not isinstance(orders, dict):
            self._log.warning("seçim penceresi okunamadı", error=str(orders))
            return {}, list(ids)
        sides = {name: self._side(name, payload)[0]
                 for name, payload in zip(ord_.SIDES, (invoices, shipments, refunds),
                                          strict=True)}
        raws = {ord_.as_int(ord_.pick(raw, "id")): raw
                for raw in (orders.get("items") or []) if isinstance(raw, dict)}
        found: dict[int, dict[str, Any]] = {}
        leftovers: list[int] = []
        for order_id in ids:
            raw = raws.get(order_id)
            row = self._rows([raw], prefs, sides=sides)[0] if raw else None
            # Fatura ve gönderi KESİN değilse önizleme "uygun" diyemez: o sipariş
            # eski yoldan okunur.
            if row is None or not (row["invoiceKnown"] and row["shipKnown"]):
                leftovers.append(order_id)
                continue
            found[order_id] = row
        return found, leftovers

    @staticmethod
    def _batch_params(row: Any) -> dict[str, Any]:
        try:
            params = json.loads(row["params"] or "{}")
        except (KeyError, TypeError, ValueError):
            return {}
        return params if isinstance(params, dict) else {}

    # ====================================================== müşteri aşama SMS'i
    #
    # METİN, FREN VE TEKRAR ENGELİ BURADA DEĞİL. Onlar Bildirimler modülünün
    # işidir ve o modül buradan IMPORT EDİLMEZ (K3); aradaki tek bağ
    # `store.notify.stage` yeteneği ile `stages.stage_order()` künyesidir.
    # Bu ekranın sorumluluğu tek cümledir: "hangi sipariş hangi aşamaya geçti".

    async def stage_state(self) -> dict[str, Any]:
        """Aşama SMS'i kurulu mu, hangi aşamalar açık.

        Ekran ve tarama bunu ÖNDEN sorar: kapalı bir aşama için sipariş detayı
        çekmek, on dakikada bir boşuna istek üretmek olurdu.
        """
        if self._stage_notify is None:
            return {"available": False, "enabled": [], "dryRun": self._stage_dry_run,
                    "lookbackDays": self._stage_lookback,
                    "error": "Bildirimler ekranı kapalı; müşteriye aşama SMS'i gönderilemez."}
        try:
            state = await self._stage_notify.state()
        except Exception as failure:  # noqa: BLE001 — bildirim tarafı bizi düşürmez (K7)
            self._log.warning("aşama SMS durumu okunamadı", error=str(failure))
            return {"available": False, "enabled": [], "dryRun": self._stage_dry_run,
                    "lookbackDays": self._stage_lookback, "error": self._fail(failure)}
        return {
            "available": bool(state.get("available")),
            "enabled": [item for item in (state.get("enabled") or [])
                        if item in stages.STAGES],
            "dryRun": self._stage_dry_run,
            "lookbackDays": self._stage_lookback,
            "error": "",
        }

    async def _stage_open(self, stage: str) -> tuple[bool, str]:
        """Bu aşama gönderime açık mı — ve DEĞİLSE nedeni.

        Neden döndürmek şart: "SMS gitmedi" diye sessiz kalmak, personelin
        müşteri aradığında ne diyeceğini bilememesi demektir.
        """
        state = await self.stage_state()
        if not state["available"]:
            return False, state["error"] or "Aşama SMS'i bu kurulumda kapalı."
        if stage not in state["enabled"]:
            return False, (f"{stages.STAGE_LABELS.get(stage, stage)} aşaması kapalı "
                           "(Bildirimler → Müşteri SMS'i).")
        return True, ""

    async def _notify_stage(self, stage: str, row: dict[str, Any], *,
                            shipment: dict[str, Any] | None = None, carrier: str = "",
                            track: str = "", actor: str = "",
                            dry_run: bool = True) -> dict[str, Any]:
        """Tek siparişin tek aşaması için SMS isteği. İSTİSNA FIRLATMAZ.

        Bildirim tarafının patlaması sipariş işini düşürmez (K7): kargo kaydı
        açılmıştır, mesajın gitmemesi onu geri almaz. Sonuç satır olarak döner
        ve çağıran ekranda gösterir.
        """
        if self._stage_notify is None:
            return {"ok": False, "sent": False, "stage": stage,
                    "note": "Bildirimler ekranı kapalı; müşteriye SMS gönderilemedi."}
        payload = stages.stage_order(row, shipment=shipment, carrier=carrier, track=track)
        try:
            result = await self._stage_notify.notify(stage=stage, order=payload, actor=actor,
                                                     dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("aşama SMS'i gönderilemedi", stage=stage,
                              orderId=payload["orderId"], error=str(failure))
            return {"ok": False, "sent": False, "stage": stage,
                    "orderId": payload["orderId"], "orderNo": payload["orderNo"],
                    "note": f"SMS katmanı hata verdi: {self._fail(failure)}"}
        return {"ok": bool(result.get("ok", True)), "sent": bool(result.get("sent")),
                "stage": stage, "orderId": payload["orderId"], "orderNo": payload["orderNo"],
                "customer": payload["customer"], "result": ord_.text(result.get("result")),
                "duplicate": bool(result.get("duplicate")),
                "note": ord_.text(result.get("note")) or ord_.text(result.get("error"))}

    async def stage_sweep(self, *, stage: str, actor: str = "",
                          dry_run: bool = True) -> dict[str, Any]:
        """Tarama tetikleyicisi: "sipariş alındı" ve "teslim edildi".

        NEDEN TARAMA. Bu iki aşamanın kaynağı Kontrol Merkezi'nde bir tıklama
        değil: sipariş müşterinin kendi eylemi, teslim ise Geliver'ın webhook /
        senkron ile mağazaya yazdığı durumdur. İkisini de görmenin tek yolu
        mağazaya bakmaktır.

        TEKRAR ENGELİ BİZDE DEĞİL, BİLDİRİMLER TARAFINDADIR: tarama on dakikada
        bir koşsa da aynı siparişe ikinci SMS gitmez. Burada yapılan yalnız
        boşuna istek atmamaktır — zaten gönderilmiş siparişler künye
        çıkarılmadan elenir.
        """
        problem = stages.sweep_stage_error(stage)
        if problem:
            return {"ok": False, "error": problem}
        open_, why = await self._stage_open(stage)
        if not open_:
            return {"ok": True, "error": "", "stage": stage, "skipped": True, "note": why,
                    "considered": 0, "results": []}

        if stage == stages.STAGE_PLACED:
            candidates, note, truncated = await self._placed_candidates()
        else:
            candidates, note, truncated = await self._delivered_candidates()
        if not candidates:
            return {"ok": True, "error": "", "stage": stage, "skipped": False, "note": note,
                    "considered": 0, "truncated": truncated, "results": []}

        results = [await self._notify_stage(stage, item["row"], shipment=item.get("shipment"),
                                           actor=actor,
                                           dry_run=dry_run or self._stage_dry_run)
                   for item in candidates]
        sent = len([item for item in results if item["sent"]])
        await self._record(order_id=0, action=f"stage_sweep_{stage}",
                           reason=f"Aşama taraması: {stages.STAGE_LABELS[stage]}",
                           actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"considered": len(candidates), "tried": len(results),
                                   "sent": sent})
        return {"ok": True, "error": "", "stage": stage, "skipped": False, "note": note,
                "considered": len(candidates), "tried": len(results), "sent": sent,
                "truncated": truncated, "results": results}

    # `_placed_candidates` / `_delivered_candidates` ADAYLARI ZATEN ELENMİŞ
    # döndürür (`_pending`). Eleme sonrası yapılmasaydı teslim taraması her
    # koşuda, zaten haber verilmiş her sipariş için bir detay isteği daha
    # atardı; on dakikada bir koşan bir iş için bu, geçidin payını boşuna
    # yemek olurdu.

    async def _pending(self, stage: str, order_ids: list[int]) -> set[int]:
        """Bu aşama için HENÜZ gönderilmemiş sipariş kimlikleri.

        Bildirimler tarafı zaten ikinci gönderimi engelliyor; bu sorgu yalnız
        boşuna sipariş detayı çekmemek içindir. Sorgu patlarsa HEPSİ denenir:
        eleme bir hız iyileştirmesidir, engel değil — engeli sessizce
        devralmak, gerçek korumanın yerini alan sahte bir koruma olurdu.
        """
        wanted = {int(item) for item in order_ids if ord_.as_int(item)}
        done = getattr(self._stage_notify, "done", None)
        if not wanted or done is None:
            return wanted
        try:
            payload = await done(stage=stage, order_ids=sorted(wanted))
        except Exception as failure:  # noqa: BLE001 — eleme yapılamadı, hepsi denensin (K7)
            self._log.info("gönderilmiş aşama listesi okunamadı", stage=stage,
                           error=str(failure))
            return wanted
        return wanted - {ord_.as_int(item) for item in (payload.get("ids") or [])}

    async def _placed_candidates(self) -> tuple[list[dict[str, Any]], str, bool]:
        """"Siparişiniz alındı" adayları — YENİDEN ESKİYE, gün penceresiyle."""
        prefs = await self._prefs_view()
        filters = {**self._base_filters({}), **EVIDENCE_SORT}
        try:
            payload = await self._api.orders(filters, page=1, per_page=self._page_size)
        except Exception as failure:  # noqa: BLE001 — K7
            return [], f"Sipariş listesi okunamadı: {self._fail(failure)}", False
        rows = self._rows(payload.get("items") or [], prefs)
        window = self._stage_lookback
        out: list[dict[str, Any]] = []
        skipped = 0
        for row in rows:
            if not stages.within_window(row["createdDay"], days=window):
                continue
            if stages.placed_block(row):
                skipped += 1
                continue
            out.append({"row": row})
        pending = await self._pending(stages.STAGE_PLACED, [item["row"]["id"] for item in out])
        out = [item for item in out if item["row"]["id"] in pending]
        note = f"Son {window} günün siparişleri tarandı." if window else "Tüm sayfa tarandı."
        if skipped:
            note += f" {skipped} sipariş iptal/kapalı olduğu için atlandı."
        return out[:SWEEP_CAP], note, len(out) > SWEEP_CAP

    async def _delivered_candidates(self) -> tuple[list[dict[str, Any]], str, bool]:
        """"Teslim edildi" adayları — taşıyıcı kayıtlarından.

        DURUM SÜZGECİ MAĞAZAYA GÖNDERİLMEZ. Laravel tanımadığı parametreyi
        sessizce yok sayar; tanıyıp da başka bir yazım beklerse listeyi sessizce
        BOŞALTIR ve hiçbir müşteri teslim SMS'i almaz — hata da vermez. En yeni
        sayfa çekilir, teslim ayıklaması `stages.is_delivered` ile BURADA
        yapılır ve tanınmayan durum teslim SAYILMAZ.

        GÜN PENCERESİ BURADA UYGULANMAZ — "sipariş alındı"dan farklı olarak.
        Gerekçe: teslim tarihini her uç aynı adla vermiyor ve tarihi
        okunamayan kaydı pencere dışı saymak, teslim SMS'inin HİÇ gitmemesi
        demek olurdu. Aşırı gönderime karşı iki gerçek koruma zaten var: liste
        en yeni sayfayla sınırlı ve tekrar engeli sipariş başına çalışıyor.
        """
        try:
            payload = await self._api.bbd_shipments({}, page=1, per_page=self._page_size)
        except Exception as failure:  # noqa: BLE001 — BBD ucu yayında olmayabilir (K7)
            return [], f"Gönderi listesi okunamadı: {self._fail(failure)}", False
        views = [stages.shipment_view(item) for item in (payload.get("items") or [])]
        delivered = [view for view in views if view["delivered"] and view["orderId"]]
        if not delivered:
            return [], f"{len(views)} gönderi okundu; teslim edilmiş kayıt yok.", False

        prefs = await self._prefs_view()
        pending = await self._pending(stages.STAGE_DELIVERED,
                                      [view["orderId"] for view in delivered])
        out: list[dict[str, Any]] = []
        unread = 0
        for view in delivered[:SWEEP_CAP]:
            if view["orderId"] not in pending:
                continue
            try:
                raw = await self._api.order(view["orderId"])
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün (K7)
                unread += 1
                self._log.warning("teslim edilen siparişin künyesi okunamadı",
                                  orderId=view["orderId"], error=str(failure))
                continue
            rows = self._rows([raw], prefs)
            if rows:
                out.append({"row": rows[0], "shipment": view})
        note = f"{len(delivered)} teslim edilmiş gönderi bulundu."
        if unread:
            note += f" {unread} siparişin künyesi okunamadı; bir sonraki taramada yeniden denenir."
        return out, note, len(delivered) > SWEEP_CAP

    # ============================================================== etiket

    async def labels(self, order_ids: list[int]) -> dict[str, Any]:
        """Kargo etiketlerini rapor klasörüne indirir.

        PDF'LER BİRLEŞTİRİLMEZ: elimizde PDF birleştirici yok ve sahte bir
        "birleşik etiket" üretmek, yazıcıdan bozuk kâğıt çıkarırdı. Her etiket
        ayrı dosyadır; ekran hepsini listeler ve tek tek yazdırır.

        GÖNDERİ SİPARİŞİN KENDİSİNDEN DOĞRULANIR (`ord_.label_shipment`). Liste
        sipariş süzgeciyle istenir ama "ilk satır" kör seçilmez: süzgeç bir
        dönem sessizce yok sayıldığında her siparişe mağazanın en yeni etiketi
        iniyordu — başka müşterinin barkodu. İptal gönderi atlanır, etiketi
        alınmış olan yeğlenir.

        SIRAYLA: etiket başına iki istek (gönderi + PDF) ve geçidin dakikalık
        payı bütün ekranlarındır; en çok 50 sipariş.
        """
        ids = [int(item) for item in (order_ids or []) if ord_.as_int(item)]
        if not ids:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        if len(ids) > 50:
            return {"ok": False, "error": "Tek seferde en çok 50 etiket."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        files: list[dict[str, Any]] = []
        for order_id in ids:
            try:
                shipments = await self._api.bbd_shipments({"order_id": order_id})
                chosen, why = ord_.label_shipment(shipments.get("items") or [], order_id)
                if chosen is None:
                    raise RuntimeError(why)
                content = await self._api.bbd_shipment_label(ord_.as_int(ord_.pick(chosen, "id")))
                name = f"magaza-kargo-etiketi-{order_id}-{stamp}.pdf"
                path = write_private(self._export_dir / name, content)
            except Exception as failure:  # noqa: BLE001 — biri patlarsa gerisi sürsün
                files.append({"orderId": order_id, "name": "", "path": "",
                              "error": self._fail(failure)})
                continue
            files.append({"orderId": order_id, "name": name, "path": str(path), "error": ""})
        return {"ok": any(not item["error"] for item in files), "error": "", "files": files}

    # ============================================================== ayarlar

    async def settings(self) -> dict[str, Any]:
        """`store_settings` ekranı yok; sipariş akışı ayarları doğal sahibinde.

        İki farklı şey aynı sekmede durur ve AYRI gösterilir:
        · Bu ekranın tercihleri (durum adları, sipariş no biçimi, iptal süresi)
          — yalnız Kontrol Merkezi'ni etkiler.
        · Mağazanın sipariş ayarları — SALT OKUNUR gösterilir; gerekçesi
          aşağıda.
        """
        prefs = await self._prefs_view()
        slug = str(self._config.get("order_config_slug") or "sales.order_settings")
        out: dict[str, Any] = {
            "ok": True, "error": "", "local": prefs, "storeSlug": slug,
            "statuses": [{"value": code, "label": label}
                         for code, label in ord_.STATUS_LABELS.items()],
            "store": [], "storeAvailable": True,
        }
        try:
            payload = await self._api.configuration(slug, channel=self._channel)
        except Exception as failure:  # noqa: BLE001 — K7
            out["storeAvailable"] = False
            out["error"] = self._fail(failure)
            return out

        # CANLIDA DOĞRULANDI: `GET /api/admin/configuration?slug=…` TEK ELEMANLI
        # DİZİ döndürüyor — `[{slug, channel, locale, values:{…}}]`. Zarfı geçit
        # açıyor; bize `{slug, channel, locale, values}` geliyor. `values`
        # gelmezse tabloyu boş göstermek "bu bölümde ayar yok" demek olurdu;
        # okunamadığı SÖYLENİR.
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict) or not values:
            out["storeAvailable"] = False
            out["error"] = (
                f"Mağazanın “{slug}” ayarları okunamadı: yanıt beklenen "
                "{slug, channel, locale, values} zarfını taşımıyor. Aşağıdaki yerel "
                "tercihler bundan etkilenmez.")
            return out
        out["store"] = [{"key": str(key), "value": "" if value is None else str(value)}
                        for key, value in sorted(values.items())][:60]
        return out

    async def save_settings(self, *, status_names: dict[str, str] | None = None,
                            order_no_format: str | None = None,
                            cancel_window_hours: int | None = None, late_days: int | None = None,
                            reason: str, actor: str) -> dict[str, Any]:
        """Yalnız YEREL tercihleri yazar; mağazaya hiçbir şey gönderilmez."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        changed: list[str] = []
        if order_no_format is not None:
            trouble = ord_.format_error(order_no_format)
            if trouble:
                return {"ok": False, "error": trouble}
            await self._set_pref("order_no_format", ord_.text(order_no_format) or "#{no}", actor)
            changed.append("sipariş no biçimi")
        if status_names is not None:
            clean = ord_.clean_status_names(status_names)
            await self._set_pref("status_names", json.dumps(clean, ensure_ascii=False), actor)
            changed.append("durum adları")
        if cancel_window_hours is not None:
            await self._set_pref("cancel_window_hours",
                                 str(max(0, min(8_760, int(cancel_window_hours)))), actor)
            changed.append("iptal süresi")
        if late_days is not None:
            await self._set_pref("late_days", str(max(1, min(90, int(late_days)))), actor)
            changed.append("gecikme eşiği")

        await self._record(order_id=0, action="save_settings", reason=reason, actor=actor,
                           result="ok", detail={"changed": changed})
        return {"ok": True, "error": "", "changed": changed,
                "local": await self._prefs_view()}

    # ================================================================ rapor

    async def export_csv(self, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """TÜM kayıtların CSV'si — rapor klasörüne yazılır. Görünen sayfanın
        CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        found = await self._scanned(wanted, prefs)
        if not found["ready"]:
            return {"ok": False, "error": found["error"] or SCAN_RUNNING}

        # CSV mali kayıt yerine geçiyor: ara toplam, KDV, indirim ve kargo
        # bedeli yalnız DETAYDA var. Detay artık YALNIZ SÜZGECE UYAN siparişler
        # için okunur (eskiden taranan kümenin tamamı, en çok 300 istek); aynı
        # `updatedAt` ile önbellekte olan tekrar okunmaz.
        loose = ord_.without_detail(wanted)
        candidates = {row["id"] for row in found["rows"]
                      if not row["detailed"] and ord_.matches(row, loose)}
        partial = len(candidates) > self._detail_cap
        if candidates:
            todo = [raw for raw in found["raws"]
                    if ord_.as_int(ord_.pick(raw, "id")) in candidates]
            await self._fill_details(todo[: self._detail_cap])
            found = await self._scanned(wanted, prefs)
            if not found["ready"]:
                return {"ok": False, "error": found["error"] or SCAN_RUNNING}
        hits = [row for row in found["rows"] if ord_.matches(row, wanted)]
        partial = partial or found["partial"] or any(not row["detailed"] for row in hits)
        truncated = found["truncated"]

        def amount(value: Any) -> str:
            # Detayı okunamayan satırda tutar BİLİNMİYOR: boş hücre, "0,00 ₺" değil.
            return "" if value is None else money(value)

        headers = ["Sipariş no", "Tarih", "Müşteri", "E-posta", "Kanal", "Durum", "Ödeme",
                   "Kalem", "Ara toplam", "Kargo", "İndirim", "KDV", "Toplam", "Kargo firması",
                   "Takip no"]
        table = [[row["orderNo"], row["createdAt"], row["customer"], row["email"], row["channel"],
                  row["statusLabel"], row["paymentLabel"], row["itemCount"],
                  amount(row["subTotal"]), amount(row["shipping"]), amount(row["discount"]),
                  amount(row["tax"]), money(row["grandTotal"]), row["carrier"], row["track"]]
                 for row in hits]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-siparis-listesi-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated, "partial": partial}

    async def preview(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Raporu üretir ve sayfalarını görüntü olarak döner (kit `reportChain`)."""
        produced = await self.build_report(kind, params or {})
        if not produced.get("ok"):
            return produced
        try:
            pages = await self._render_pages(Path(produced["path"]))
        except PreviewError as failure:
            return {**produced, "pages": [], "previewError": str(failure)}
        return {**produced, "pages": pages, "previewError": ""}

    async def build_report(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if kind not in ("list", "slip", "manifest"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")

        if kind == "slip":
            produced = await self._slip_pdf(ord_.as_int(params.get("orderId")))
            name = f"magaza-siparis-fisi-{ord_.as_int(params.get('orderId'))}-{stamp}.pdf"
        elif kind == "manifest":
            produced = await self._manifest_pdf(
                [ord_.as_int(item) for item in (params.get("orderIds") or [])])
            name = f"magaza-kargo-manifestosu-{stamp}.pdf"
        else:
            produced = await self._list_pdf(params.get("filters") or {})
            name = f"magaza-siparis-listesi-{stamp}.pdf"

        if not produced.get("ok"):
            return produced
        content = produced["content"]
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("sipariş raporu üretildi", kind=kind, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": produced.get("rows", 0),
                "truncated": produced.get("truncated", False)}

    async def _list_pdf(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Süzgecin liste raporu — ORTAK taramadan, sipariş başına istek YOK.

        Raporun sütunları (no, tarih, müşteri, durum, ödeme, toplam) ve özet
        kutuları liste satırında zaten var; eskiden yine de taranan kümenin
        tamamı tek tek detaylandırılıyordu.
        """
        prefs = await self._prefs_view()
        wanted = dict(filters or {})
        wanted.setdefault("today", ord_.today_iso())
        found = await self._scanned(wanted, prefs)
        if not found["ready"]:
            return {"ok": False, "error": found["error"] or SCAN_RUNNING}
        truncated, partial = found["truncated"], found["partial"]
        hits = [row for row in found["rows"] if ord_.matches(row, wanted)]
        if not hits:
            return {"ok": False, "error": "Bu süzgeçte rapora girecek sipariş yok."}

        totals = ord_.summary(hits)
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Sipariş", number(totals["count"])),
                      ("Ciro", money(totals["revenue"])),
                      ("Ortalama sepet", money(totals["average"])),
                      ("İptal", number(totals["canceled"]))],
        }, {
            "kind": "table", "title": "Siparişler",
            "headers": ["Sipariş no", "Tarih", "Müşteri", "Durum", "Ödeme", "Toplam"],
            "align": "LLLLLR", "widths": [1.1, 1.2, 2.2, 1, 1, 1],
            "rows": [[row["orderNo"], row["createdAt"][:16], row["customer"],
                      row["statusLabel"], row["paymentLabel"], money(row["grandTotal"])]
                     for row in hits[:600]],
        }]
        if truncated or partial or len(hits) > 600:
            sections.append({"kind": "note",
                             "text": "Liste tavana dayandı; rapor eksik olabilir."})
        return {"ok": True, "rows": len(hits), "truncated": truncated or partial,
                "content": await self._pdf(title="Sipariş listesi",
                                           subtitle=f"{totals['count']} sipariş · "
                                                    f"ciro {money(totals['revenue'])}",
                                           sections=sections)}

    @staticmethod
    async def _pdf(*, title: str, subtitle: str, sections: list[dict[str, Any]]) -> bytes:
        """PDF'i AYRI İŞ PARÇACIĞINDA üretir.

        Çekirdeğin `build_pdf`i (ReportLab) eşzamanlıdır ve yüzlerce satırlık
        raporda saniyeler sürer. Olay döngüsünde çağrılırsa tek uvicorn
        işçisinde o sürece BÜTÜN istekler — başka ekranlarınkiler de — bekler.
        """
        return await asyncio.to_thread(build_pdf, title=title, subtitle=subtitle,
                                       sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _slip_pdf(self, order_id: int) -> dict[str, Any]:
        if not order_id:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        card = await self.card(order_id)
        if not card.get("ok"):
            return {"ok": False, "error": card.get("error") or "Sipariş okunamadı."}
        row = card["order"]
        amounts = card["money"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Sipariş",
            "tiles": [("Sipariş no", row["orderNo"]), ("Tarih", row["createdAt"][:16]),
                      ("Durum", row["statusLabel"]), ("Ödeme", row["paymentLabel"])],
        }, {
            "kind": "table", "title": "Kalemler",
            "headers": ["SKU", "Ürün", "Adet", "Birim", "İndirim", "KDV", "Tutar"],
            "align": "LLRRRRR", "widths": [1.1, 2.6, 0.6, 1, 1, 1, 1],
            "rows": [[item["sku"], item["name"], number(item["quantity"]),
                      money(item["unitPrice"]), money(item["discount"]), money(item["tax"]),
                      money(item["total"])] for item in card["items"]],
        }, {
            "kind": "table", "title": "Toplam",
            "headers": ["Kalem", "Tutar"], "align": "LR", "widths": [3, 1],
            "rows": [["Ara toplam", money(amounts["subTotal"])],
                     ["Kargo", money(amounts["shipping"])],
                     ["İndirim", money(amounts["discount"])],
                     ["KDV", money(amounts["tax"])],
                     ["Genel toplam", money(amounts["grandTotal"])]],
        }, {
            "kind": "table", "title": "Adresler",
            "headers": ["Tür", "Ad", "Adres"], "align": "LLL", "widths": [0.8, 1.4, 3.4],
            "rows": [["Fatura", card["billing"]["name"], card["billing"]["line"]],
                     ["Teslimat", card["shipping"]["name"], card["shipping"]["line"]]],
        }]
        return {"ok": True, "rows": len(card["items"]), "truncated": False,
                "content": await self._pdf(title="Sipariş fişi",
                                           subtitle=f"{row['orderNo']} · {row['customer']}",
                                           sections=sections)}

    async def _manifest_pdf(self, order_ids: list[int]) -> dict[str, Any]:
        ids = [item for item in order_ids if item]
        if not ids:
            return {"ok": False, "error": "Sipariş seçilmedi."}
        prefs = await self._prefs_view()
        # Manifesto basılı teslim belgesidir; takip no TAZE olmalı. Seçim
        # penceresi taze okunur, yalnız pencerede kesinleşmeyen sipariş tek tek
        # okunur (eskiden seçilen her sipariş: 200 seçimde 200 istek).
        rows, _ = await self._selected_rows(ids[:200], prefs)
        if not rows:
            return {"ok": False, "error": "Seçilen siparişlerin hiçbiri okunamadı."}

        sections = [{
            "kind": "table", "title": "Kargo manifestosu",
            "headers": ["Sipariş no", "Müşteri", "Şehir", "Kalem", "Kargo firması", "Takip no"],
            "align": "LLLRLL", "widths": [1.1, 2, 1.2, 0.6, 1.3, 1.5],
            "rows": [[row["orderNo"], row["customer"], row["city"] or "—",
                      number(row["quantity"]), row["carrier"] or "—", row["track"] or "—"]
                     for row in rows],
        }, {
            "kind": "note",
            "text": f"{len(rows)} gönderi · teslim alan imzası: ______________________",
        }]
        return {"ok": True, "rows": len(rows), "truncated": len(ids) > 200,
                "content": await self._pdf(title="Kargo manifestosu",
                                           subtitle=f"{len(rows)} sipariş",
                                           sections=sections)}

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-siparis-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                # Süre dolunca süreç ÖLDÜRÜLMELİ: `wait_for` yalnız beklemeyi
                # bırakır, `pdftoppm` arkada çalışmaya ve geçici klasöre yazmaya
                # devam ederdi — klasör silinince yarım dosyalarla boğuşurdu.
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                raise PreviewError("Önizleme üretilemedi: süre aşıldı.") from None
            except OSError as failure:
                raise PreviewError(f"Önizleme üretilemedi: {failure}") from failure
            if process.returncode != 0:
                raise PreviewError(
                    f"Önizleme üretilemedi: {err.decode(errors='replace').strip()}")
            return ["data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                    for item in sorted(Path(folder).glob("sayfa*.png"))]

    async def print_report(self, path: str, *, copies: int = 1) -> dict[str, Any]:
        """Üretilmiş raporu yazıcıya gönderir.

        GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir. Serbest
        yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
        döktürmeye açık kapı bırakırdı.
        """
        if self._printer is None:
            return {"ok": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError:
            return {"ok": False, "error": "Basılacak rapor bulunamadı."}
        allowed = self._export_dir.resolve()
        if not str(resolved).startswith(str(allowed) + os.sep):
            return {"ok": False,
                    "error": "Bu dosya rapor klasöründe değil; güvenlik gereği basılmaz."}
        try:
            result = await self._printer.print_file(resolved, title=resolved.name,
                                                    copies=max(1, min(20, int(copies))))
        except Exception as failure:  # noqa: BLE001 — yazıcı dışarısı
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, **result, "name": resolved.name}

    async def printer_status(self) -> dict[str, Any]:
        if self._printer is None:
            return {"ready": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            return await self._printer.status()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ready": False, "error": self._fail(failure)}
