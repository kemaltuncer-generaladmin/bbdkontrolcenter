"""Ana Ekran Görselleri — iş kuralları.

VERİ MAĞAZADADIR, KARAR BURADADIR. Slotlar `store.api` geçidinden gelir (K4);
bu modül onların kopyasını tutmaz. Yerel tablolar yalnız mağazada KARŞILIĞI
OLMAYAN iki şeyi saklar: yazma gerekçesi (denetim izi) ve yüklenen görselin
ölçü kararı ("önerilen 1920x640, yüklenen 1200x400 — mobilde bulanık").

ÖLÇEK. Bir ana sayfada onlarca slot olur, binlerce değil. Liste TEK istekte
gelir ve süzme burada yapılır; sunucu tarafı sayfalama bu boyutta kazanç değil
gecikme olurdu.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner. Slot listesi okunamazsa ekran tema özelleştirmelerinden SALT OKUNUR
bir görünüm çizer ve NEDENİNİ söyler — sessizce boş kalmaz.

CANLIDA DOĞRULANDI (2026-08-16): okuma ucu `GET /api/admin/bbd/storefront/
carousels` 200 dönüyor ve gerçek slotlar geliyor; güncelleme ucu
`PATCH .../carousels/{id}` de rota listesinde var. Bir dönem bu ikisi için de
"uç henüz yayında değil" deniyordu, ARTIK ÖYLE DEĞİL. Salt okunur dal yine de
duruyor (K7 — uç bir gün geri çekilirse ekran ayakta kalmalı), ama artık
kullanıcıya "uç yayınlanmadı" diye değil "şu an okunamadı" diye anlatılır:
yanlış teşhis, geçici bir arızayı kalıcı bir eksiklik gibi gösteriyordu.

Hâlâ eksik olan uçlar (2026-08-16'da yeniden ölçüldü): yükleme
`POST /api/admin/bbd/home/slides` 404, slot EKLEME (POST .../carousels) ve
sıralama (PUT .../carousels/reorder) rota listesinde yok.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, number, report_dir, write_private

from . import slots

#: Alan başına önerilen ölçünün son çaresi. Ayar dosyası ezer; buradaki
#: değerler mağazanın şu anki temasından alınmıştır.
DEFAULT_RECOMMENDED = {
    "slider": "1920x640",
    "banner": "1200x400",
    "collection": "800x800",
    "announcement": "0x0",
}

#: Vitrine yansımanın gecikebileceğini SÖYLERİZ; sessiz kalıp "neden değişmedi"
#: sorusunu doğurmayız. Ana sayfa çoğu kurulumda önbelleklidir.
CACHE_NOTICE = ("Kaydedildi. Ana sayfada görünmesi birkaç dakika sürebilir — site son "
                "hâlini bir süre hafızasında tutuyor. Hemen görmezseniz birkaç dakika "
                "sonra sayfayı yenileyin.")

#: Yükleme ucu (`POST /api/admin/bbd/home/slides`) mağaza tarafında henüz
#: yayında değil — 2026-08-13 itibarıyla 404. Bunu "hata" diye göstermek
#: yanlış olurdu: kullanıcı yanlış bir şey yapmadı, paket henüz çıkmadı.
#: Ekran ne olduğunu, ne zaman açılacağını ve o zamana kadar hangi yolun
#: çalıştığını söyler (K7).
UPLOAD_PENDING = (
    "Ayrı görsel yükleme yolu mağaza tarafında henüz açılmadı — bu sizin hatanız "
    "değil, mağaza yazılımında o parça yayınlanmadı. YAPACAĞINIZ İŞLEM DEĞİŞMİYOR: "
    "görseli seçip “Kaydet” deyin, görsel kayıtla birlikte gidiyor ve ölçü denetimi "
    "orada da çalışıyor. Bu düğme, mağaza tarafı açıldığı gün kendiliğinden çalışmaya "
    "başlayacak."
)


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class HomeMediaService:
    """Ana Ekran Görselleri ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, category: str = "Mağaza",
                 subcategory: str = "Müşteri", fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._assets = store.table("assets")

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _max_bytes(self) -> int:
        return max(50_000, min(20_000_000, slots.as_int(self._config.get("max_image_bytes"),
                                                        2_000_000)))

    @property
    def _allowed(self) -> tuple[str, ...]:
        raw = self._config.get("allowed_image_types")
        if isinstance(raw, (list, tuple)) and raw:
            return tuple(str(item).strip().lower() for item in raw)
        return ("image/png", "image/jpeg", "image/webp")

    @property
    def _sharp(self) -> float:
        try:
            return max(0.5, min(1.0, float(self._config.get("sharp_ratio") or 1.0)))
        except (TypeError, ValueError):
            return 1.0

    @property
    def _tolerance(self) -> int:
        return max(0, min(50, slots.as_int(self._config.get("aspect_tolerance"), 8)))

    def _wanted(self, area: str) -> tuple[int, int]:
        """Alanın önerilen ölçüsü. Ayarda yoksa temanın varsayılanı."""
        key = f"recommended_{area if area in slots.AREAS else 'banner'}"
        raw = str(self._config.get(key) or "") or DEFAULT_RECOMMENDED.get(area, "1200x400")
        size = slots.parse_size(raw)
        return size if size != (0, 0) or area == "announcement" else (1200, 400)

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, slot_id: int, area: str, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Bagisto denetim tutuyor ama GEREKÇEYİ tutmuyor;
        ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(slot_id, area, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(slot_id or 0), area, action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _record_asset(self, *, slot_id: int, area: str, image: dict[str, Any],
                            verdict: dict[str, Any], actor: str) -> None:
        """Yüklenen görselin ölçü kararını dondurur.

        Uyarıya rağmen yüklenen bulanık görselin izi burada kalır; aksi hâlde
        "bu banner neden bulanık" sorusunun cevabı hiçbir yerde olmazdı.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._assets} "
                "(slot_id, area, sha256, mime, width, height, bytes, verdict, note, actor, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(slot_id or 0), area, image.get("sha256", ""), image.get("mime", ""),
                 int(image.get("width") or 0), int(image.get("height") or 0),
                 int(image.get("bytes") or 0), verdict.get("state", ""),
                 verdict.get("note", ""), actor, _now()),
            )
        except Exception as failure:  # noqa: BLE001 — kayıt yazılamadı, yükleme durmasın
            self._log.warning("görsel kaydı yazılamadı", error=str(failure))

    async def _announce(self, action: str, detail: dict[str, Any]) -> None:
        """`store_home_media.layout_changed` — yalnız GERÇEK değişiklikte."""
        if self._publish is None:
            return
        try:
            await self._publish("store_home_media.layout_changed",
                                {"action": action, "at": _now(), **detail})
        except Exception as failure:  # noqa: BLE001 — olay yayılamadı, iş bitti sayılır
            self._log.warning("olay yayılamadı", action=action, error=str(failure))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı — internet bağlantısını kontrol edin."

    def _row(self, raw: Any, *, today: str, read_only: bool = False) -> dict[str, Any]:
        area = slots.area_of(raw)
        return slots.slot_row(raw, today=today, wanted=self._wanted(area),
                              sharp_ratio=self._sharp, tolerance=self._tolerance,
                              read_only=read_only)

    # ================================================================ okuma

    async def _fetch(self) -> dict[str, Any]:
        """Tüm slotlar tek istekte. Başarısızsa tema özelleştirmelerine düşer."""
        today = slots.today_iso()
        filters = {"channel": self._channel, "locale": self._locale}
        try:
            payload = await self._api.bbd_carousel(filters)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("ana sayfa slotları okunamadı", error=str(failure))
            fallback = await self._fallback_rows(today)
            return {"rows": fallback["rows"], "connected": fallback["connected"],
                    "error": self._fail(failure), "source": fallback["source"],
                    "readOnly": True, "fallbackError": fallback["error"]}

        rows = [self._row(item, today=today) for item in (payload.get("items") or [])]
        return {"rows": slots.sort_rows(rows), "connected": True, "error": "",
                "source": "carousel", "readOnly": False, "fallbackError": ""}

    async def _fallback_rows(self, today: str) -> dict[str, Any]:
        """Slot listesi okunamazken tema özelleştirmelerinden SALT OKUNUR görünüm.

        Bu dal bir dönem "BBD ucu henüz yayında değil" diye anlatılıyordu;
        uç 2026-08-16'da canlıda 200 dönüyor, dolayısıyla buraya düşmek artık
        "eksik paket" değil "şu an okunamadı" demektir. Dal duruyor (K7) ama
        gerekçesi `_fetch` içinde `error` olarak taşınır ve ekranda gösterilir.
        """
        try:
            payload = await self._api.themes({"channel": self._channel})
        except Exception as failure:  # noqa: BLE001 — ikisi birden yoksa boş liste
            self._log.info("tema özelleştirmeleri de okunamadı", error=str(failure))
            return {"rows": [], "connected": False, "source": "none",
                    "error": self._fail(failure)}
        rows = [self._row(slots.theme_slot(item), today=today, read_only=True)
                for item in (payload.get("items") or [])]
        return {"rows": slots.sort_rows(rows), "connected": True, "source": "themes",
                "error": ""}

    async def overview(self, *, q: str = "", area: str = "", status: str = "", device: str = "",
                       channel: str = "", placement: str = "", start: str = "",
                       end: str = "") -> dict[str, Any]:
        """Ekranın ana yükü: önizleme, sekmeler ve liste tek yanıttan beslenir.

        Süzgeç sonucu `items`, ÖNİZLEME ise `preview` alanındadır: önizleme
        ana sayfanın temsilidir ve süzgeçle daralmaz — daralsaydı "filtre
        koydum, ana sayfam boşaldı" yanılgısı üretirdi.
        """
        data = await self._fetch()
        rows = data["rows"]
        filtered = slots.filter_rows(rows, q=q, area=area, status=status, device=device,
                                     channel=channel, placement=placement, start=start, end=end)

        preview: dict[str, list[dict[str, Any]]] = {name: [] for name in slots.AREAS}
        for row in rows:
            preview[row["area"]].append(row)

        return {
            "ok": True,
            "connected": bool(data["connected"]),
            "error": data["error"],
            "source": data["source"],
            "readOnly": bool(data["readOnly"]),
            "items": filtered,
            "preview": preview,
            "summary": slots.summary(rows),
            "counts": {name: len(preview[name]) for name in slots.AREAS},
            "today": slots.today_iso(),
            "recommended": {name: f"{self._wanted(name)[0]}x{self._wanted(name)[1]}"
                            for name in slots.AREAS},
            "maxImageBytes": self._max_bytes,
            "allowedTypes": list(self._allowed),
            "notice": CACHE_NOTICE,
        }

    async def reference(self) -> dict[str, Any]:
        """Süzgeç ve hedef bağlantı seçicisinin beslendiği listeler.

        Parça parça hata: kategoriler gelmezse CMS sayfaları yine dolar (K7).
        """
        out: dict[str, Any] = {"ok": True, "connected": True, "error": "", "warnings": [],
                               "channels": [], "locales": [], "categories": [], "pages": [],
                               # `what` = "bu bölüm ne işe yarar" tek cümlesi.
                               # Ekran adı tek başına yetmiyor: "Tanıtım
                               # görselleri" doğru bir ad ama müşterinin bunu
                               # NEREDE göreceğini söylemez.
                               "areas": [{"value": key, "label": label,
                                          "one": slots.AREA_ONE[key],
                                          "what": slots.AREA_WHAT[key]}
                                         for key, label in slots.AREA_LABELS.items()],
                               "placements": [{"value": key, "label": slots.PLACEMENT_LABELS[key]}
                                              for key in slots.PLACEMENTS],
                               "devices": [{"value": key, "label": slots.DEVICE_LABELS[key]}
                                           for key in slots.DEVICES],
                               "states": [{"value": key, "label": label}
                                          for key, label in slots.STATE_LABELS.items()]}

        async def part(label: str, call: Any) -> list[dict[str, Any]]:
            try:
                result = await call()
            except Exception as failure:  # noqa: BLE001 — uzak sistem
                out["warnings"].append(f"{label}: {self._fail(failure)}")
                return []
            return result.get("items") or []

        for item in await part("kanallar", self._api.channels):
            out["channels"].append({"code": slots.text(item.get("code")),
                                    "name": slots.text(item.get("name"))})
        for item in await part("diller", self._api.locales):
            out["locales"].append({"code": slots.text(item.get("code")),
                                   "name": slots.text(item.get("name"))})
        for item in await part("kategoriler", self._api.category_tree):
            out["categories"].extend(_flatten_categories(item))
        for item in await part("CMS sayfaları",
                               lambda: self._api.cms_pages({"channel": self._channel})):
            out["pages"].append({"id": slots.as_int(item.get("id")),
                                 "title": slots.text(item.get("page_title")
                                                     or item.get("title")),
                                 "url": "/" + slots.text(item.get("url_key")).lstrip("/")})

        out["connected"] = not out["warnings"]
        out["error"] = " · ".join(out["warnings"])
        return out

    async def link_search(self, *, q: str) -> dict[str, Any]:
        """Hedef bağlantı için ürün arama. Boş sorguda mağazaya HİÇ gidilmez."""
        needle = slots.text(q)
        if len(needle) < 2:
            return {"ok": True, "items": [], "error": "", "connected": True}
        limit = max(5, min(100, slots.as_int(self._config.get("link_search_limit"), 20)))
        try:
            payload = await self._api.product_lookup(
                {"name": needle, "channel": self._channel, "locale": self._locale},
                per_page=limit)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "items": [], "connected": False, "error": self._fail(failure)}
        items = []
        for item in (payload.get("items") or [])[:limit]:
            url_key = slots.text(slots.pick(item, "url_key", "urlKey", "slug"))
            items.append({"id": slots.as_int(item.get("id")),
                          "name": slots.text(slots.pick(item, "name", "title")),
                          "sku": slots.text(item.get("sku")),
                          "url": f"/{url_key}" if url_key else ""})
        return {"ok": True, "items": items, "connected": True, "error": ""}

    async def audit(self, *, slot_id: int = 0, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT slot_id, area, action, reason, actor, result, created_at "
               f"FROM {self._audit} ")
        params: tuple[Any, ...] = ()
        if slot_id:
            sql += "WHERE slot_id = ? "
            params = (int(slot_id),)
        sql += "ORDER BY id DESC LIMIT ?"
        params = (*params, max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, params)
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"slotId": row["slot_id"], "area": row["area"], "action": row["action"],
             "reason": row["reason"], "actor": row["actor"], "result": row["result"],
             "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ========================================================= görsel denetim

    def _image_report(self, area: str, image: dict[str, Any]) -> dict[str, Any]:
        """Ölçü kararı + kırpma planı + gerçek oranlı önizleme kutusu — TEK yerde.

        Denetim ve yükleme aynı cümleleri söylemek ZORUNDA: kullanıcı seçim
        anında "kenarlardan %25 kırpılacak" okuyup yükleme anında başka bir
        metin görürse hangisine güveneceğini bilemez.
        """
        wanted = self._wanted(area)
        verdict = slots.size_verdict(image["width"], image["height"], wanted,
                                     sharp_ratio=self._sharp, tolerance=self._tolerance)
        crop = slots.crop_plan(image["width"], image["height"], wanted,
                               tolerance=self._tolerance)
        return {
            "mime": image["mime"], "bytes": image["bytes"],
            "width": image["width"], "height": image["height"],
            "sizeState": verdict["state"], "sizeNote": verdict["note"],
            "recommended": verdict["recommended"],
            "aspect": slots.ratio_label(image["width"], image["height"]),
            "recommendedAspect": slots.ratio_label(*wanted),
            "cropAxis": crop["axis"], "cropPercent": crop["percent"],
            "cropNote": crop["note"],
            # Panel önizlemeyi BU ölçülerle çizer: sabit çerçeveye `cover` ile
            # sığdırmak kırpmayı gizlerdi (TUZAK 8).
            "previewBox": slots.preview_box(image["width"], image["height"]),
            # Oran denetimi ZORUNLU: uyarı görülmeden yükleme geçmez.
            "needsConfirm": verdict["state"] in slots.WARN_STATES,
        }

    def check_image(self, *, area: str, data: str) -> dict[str, Any]:
        """Görseli YAZMADAN ÖNCE ölçer. Panel yükleme anında bunu çağırır.

        Ağa çıkmaz: karar tamamen yereldir ve kullanıcı "Kaydet"e basmadan
        önce "mobilde bulanık / kenarlardan kırpılacak" uyarısını görür.
        """
        image = slots.decode_image(data, max_bytes=self._max_bytes, allowed=self._allowed)
        if not image["ok"]:
            return {"ok": False, "error": image["error"]}
        return {"ok": True, "error": "", **self._image_report(area, image)}

    async def upload_image(self, *, data: str, area: str = "banner", slot_id: int = 0,
                           filename: str = "", acknowledged: bool = False, reason: str,
                           actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Görseli mağazanın YÜKLEME ucuna gönderir (multipart, geçit üzerinden).

        Slot kaydından AYRI bir yoldur ve bilerek öyledir: dosyanın kendisi için
        geçidin beş politikası (acil fren, gerekçe, kuru prova, hız kovası,
        denetim izi) burada işler. Gövdeye gömülü görselde bunlar slot yazmasının
        politikasına bindirilmişti; büyük dosya ayrı bir risk ve ayrı bir izdir.

        SIRALAMA: gerekçe → alan → çöz → ölçü kararı → ONAY → ağ. Ağa çıkıp
        sonra "oranı uygun değil" demek, mağazada gereksiz bir dosya bırakırdı.

        Uç bugün 404; `pending` bayrağıyla döner ve ekran bunu hata değil
        "hazır, bekliyor" diye anlatır (K7).
        """
        problem = slots.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        if area not in slots.AREAS:
            return {"ok": False, "error": f"Tanınmayan bölüm: {area}"}
        if self._wanted(area) == (0, 0):
            return {"ok": False,
                    "error": "Üstteki duyuru yazısı yalnız metin gösterir; oraya görsel konmaz. "
                             "Görsel asmak için “Tanıtım görselleri” sekmesini kullanın."}

        image = slots.decode_image(data, max_bytes=self._max_bytes, allowed=self._allowed)
        if not image["ok"]:
            return {"ok": False, "error": image["error"]}

        report = self._image_report(area, image)
        if report["needsConfirm"] and not acknowledged:
            # K9: panelde onay kutusu göstermek yetkilendirme değil. Onay
            # gelmediyse dosya ağa HİÇ çıkmaz.
            return {"ok": False, "needsConfirm": True, "error": report["sizeNote"], **report}

        name = slots.safe_filename(filename, image["mime"])
        await self._record(slot_id=int(slot_id or 0), area=area, action="upload_image",
                           reason=reason, actor=actor, result="denendi",
                           detail={"file": name, "bytes": image["bytes"],
                                   "size": report["sizeState"],
                                   "acknowledged": bool(acknowledged)})
        try:
            result = await self._api.upload_media(
                content=image["base64"], filename=name, mime=image["mime"], slot=area,
                reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            pending = slots.is_endpoint_pending(getattr(failure, "code", ""), str(failure))
            await self._record(slot_id=int(slot_id or 0), area=area, action="upload_image",
                               reason=reason, actor=actor,
                               result="beklemede" if pending else "hata",
                               detail={"error": str(failure)})
            if pending:
                self._log.info("görsel yükleme ucu henüz yayında değil", area=area)
                return {"ok": False, "pending": True, "error": UPLOAD_PENDING, **report}
            return {"ok": False, "error": self._fail(failure), **report}

        await self._record(slot_id=int(slot_id or 0), area=area, action="upload_image",
                           reason=reason, actor=actor, result="dry_run" if dry_run else "ok",
                           detail={"file": name, "size": report["sizeState"]})
        # Ölçü kararı uyarılı olsa da kaydedilir: "bu banner neden bulanık"
        # sorusunun cevabı yalnız burada durur.
        await self._record_asset(slot_id=int(slot_id or 0), area=area, image=image,
                                 verdict={"state": report["sizeState"],
                                          "note": report["sizeNote"]}, actor=actor)
        return {"ok": True, "error": "", "pending": False, "file": name,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)),
                "url": slots.text(slots.pick(result, "url", "path", "image_url")),
                "notice": CACHE_NOTICE, **report}

    # =============================================================== yazma

    async def _current(self, slot_id: int) -> tuple[dict[str, Any] | None, str]:
        """Slotu TAZE okur — oku-değiştir-yaz için.

        Geçitte tekil slot ucu yok (`bbd_carousel` yalnız liste veriyor); taze
        kayıt listeden çekilir. Liste küçük olduğu için bu ucuz; yine de tek
        slot ucu eklenirse burası tek satırda değişir.
        """
        data = await self._fetch()
        if not data["connected"]:
            return None, data["error"] or "Mağazaya ulaşılamadı."
        for row in data["rows"]:
            if row["id"] == int(slot_id):
                return row, ""
        return None, ("Bu kayıt artık listede yok — bu arada başka biri kaldırmış olabilir. "
                      "“Yenile” deyip listeye yeniden bakın.")

    async def save(self, slot_id: int | None, *, patch: dict[str, Any], image: str = "",
                   reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Slot ekler ya da günceller. Görsel varsa base64 gövdeyle gider.

        SIRALAMA ÖNEMLİ: önce gerekçe, sonra görsel denetimi, sonra iş kuralı,
        en son ağ. Ağa çıkıp sonra "alt metni boş" demek, mağazada yarım kayıt
        bırakırdı.
        """
        problem = slots.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        current: dict[str, Any] | None = None
        if slot_id:
            current, missing = await self._current(int(slot_id))
            if missing:
                return {"ok": False, "error": missing}
            if current and current["readOnly"]:
                # Gerekçe DÜZELTİLDİ (2026-08-16): eskiden burada "BBD ucu
                # yayınlanınca açılacak" yazıyordu. Slot ucu artık canlıda;
                # bu dala düşmek uç eksikliği değil, listenin O AN okunamamış
                # olması demek. Yanlış teşhis kullanıcıya geçici arızayı
                # kalıcı eksiklik gibi gösteriyordu.
                return {"ok": False,
                        "error": "Şu an yalnız BAKABİLİRSİNİZ, değiştiremezsiniz. Neden: ana "
                                 "sayfa listesi mağazadan okunamadı; ekrandaki görüntü sitenin "
                                 "temasından çıkarılmış bir özet, düzenlenecek gerçek kayıt "
                                 "elimizde değil. Sıradaki adım: “Yenile” deyin — bağlantı "
                                 "düzelir düzelmez düzenleme kendiliğinden açılır."}

        clean = slots.normalize_patch(patch)
        body = slots.write_body(current, clean, channel=self._channel, locale=self._locale)
        area = slots.text(body.get("area")) or (current or {}).get("area") or "banner"
        body["area"] = area
        if not slot_id:
            # Yeni slot TASLAK açılır. Yayına almak ayrı izin ister
            # (`store_home_media.publish`); kaydeder kaydetmez vitrine düşen
            # bir banner, yarım kalmış bir işi müşteriye gösterirdi.
            body["status"] = 0

        verdict: dict[str, Any] = {}
        report: dict[str, Any] = {}
        decoded: dict[str, Any] = {}
        if image:
            decoded = slots.decode_image(image, max_bytes=self._max_bytes,
                                         allowed=self._allowed)
            if not decoded["ok"]:
                return {"ok": False, "error": decoded["error"]}
            # Ölçü/oran kararı yükleme ucundakiyle AYNI fonksiyondan çıkar;
            # iki yol iki farklı cümle söylerse kullanıcı hangisine güveneceğini
            # bilemez.
            report = self._image_report(area, decoded)
            verdict = {"state": report["sizeState"], "note": report["sizeNote"]}
            body["image"] = decoded["base64"]
            body["image_mime"] = decoded["mime"]
            body["image_width"] = decoded["width"]
            body["image_height"] = decoded["height"]

        rule = slots.slot_error(body, area=area, has_image=bool(image))
        if rule:
            return {"ok": False, "error": rule}

        action = "update_slot" if slot_id else "create_slot"
        await self._record(slot_id=int(slot_id or 0), area=area, action=action, reason=reason,
                           actor=actor, result="denendi",
                           detail={"fields": sorted(clean), "image": bool(image)})
        try:
            result = await self._api.bbd_save_carousel_slot(
                payload=body, slot_id=int(slot_id) if slot_id else None,
                reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(slot_id=int(slot_id or 0), area=area, action=action,
                               reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        new_id = int(slot_id or 0) or slots.as_int(
            slots.pick(result.get("data") if isinstance(result.get("data"), dict) else result,
                       "id", "slot_id"))
        await self._record(slot_id=new_id, area=area, action=action, reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"fields": sorted(clean), "size": verdict.get("state", "")})
        if decoded:
            await self._record_asset(slot_id=new_id, area=area, image=decoded, verdict=verdict,
                                     actor=actor)
        if not dry_run:
            await self._announce(action, {"slotId": new_id, "area": area})

        return {"ok": True, "error": "", "id": new_id,
                "dryRun": bool(result.get("dryRun", dry_run)),
                "sent": bool(result.get("sent", not dry_run)),
                "sizeState": verdict.get("state", ""), "sizeNote": verdict.get("note", ""),
                "cropNote": report.get("cropNote", ""), "aspect": report.get("aspect", ""),
                "notice": CACHE_NOTICE}

    async def set_status(self, slot_id: int, *, published: bool, reason: str, actor: str,
                         dry_run: bool = True) -> dict[str, Any]:
        """Yayına al / yayından kaldır — SİLME YOK (ADR 0012).

        Slot silinseydi tıklama istatistiği ve "geçen kampanyada ne asmıştık"
        bilgisi de giderdi. Yayından kaldırılan slot listede kalır, vitrinde
        görünmez.
        """
        problem = slots.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        current, missing = await self._current(int(slot_id))
        if missing:
            return {"ok": False, "error": missing}
        if current and current["readOnly"]:
            return {"ok": False,
                    "error": "Şu an yalnız bakabilirsiniz: ana sayfa listesi mağazadan "
                             "okunamadığı için yayın durumu değiştirilemiyor. Sıradaki adım: "
                             "“Yenile” deyin, bağlantı düzelince düğme çalışır."}
        if current and current["status"] == bool(published):
            return {"ok": False, "error": "Bu zaten istediğiniz durumda; değişecek bir şey yok."}

        body = slots.write_body(current, {"status": 1 if published else 0},
                                channel=self._channel, locale=self._locale)
        area = (current or {}).get("area", "banner")
        action = "publish" if published else "unpublish"
        await self._record(slot_id=int(slot_id), area=area, action=action, reason=reason,
                           actor=actor, result="denendi")
        try:
            result = await self._api.bbd_save_carousel_slot(payload=body, slot_id=int(slot_id),
                                                            reason=reason, actor=actor,
                                                            dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(slot_id=int(slot_id), area=area, action=action, reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(slot_id=int(slot_id), area=area, action=action, reason=reason,
                           actor=actor, result="dry_run" if dry_run else "ok")
        if not dry_run:
            await self._announce(action, {"slotId": int(slot_id), "area": area})
        return {"ok": True, "error": "", "published": bool(published),
                "dryRun": bool(result.get("dryRun", dry_run)), "notice": CACHE_NOTICE}

    async def reorder(self, *, area: str, order: list[int], reason: str, actor: str,
                      dry_run: bool = True) -> dict[str, Any]:
        """Şeridin sırasını yazar. Gönderilen liste GLOBAL sıraya oturtulur."""
        problem = slots.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        data = await self._fetch()
        if not data["connected"]:
            return {"ok": False, "error": data["error"] or "Mağazaya ulaşılamadı."}
        if data["readOnly"]:
            # BURADAKİ İDDİA HÂLÂ DOĞRU (2026-08-16'da rota listesinden yeniden
            # bakıldı): sıralama ucu mağazada gerçekten yok. Yalnız iki neden
            # birbirine karışmasın diye ayrıldı — liste okunamamış OLABİLİR,
            # ama uç yayınlansa bile bu görünümden sıra yazılamaz.
            return {"ok": False,
                    "error": "Sıra şu an kaydedilemiyor. İki neden var ve ikisi de sizden "
                             "kaynaklanmıyor: (1) ana sayfa listesi mağazadan okunamadı, "
                             "(2) sıralamayı kaydeden bölüm mağaza yazılımında henüz "
                             "yayınlanmadı. Sıradaki adım: “Yenile” deyip yeniden deneyin; "
                             "sürerse mağaza yazılımını güncelleyecek kişiye haber verin. "
                             "Bu arada diğer düzenlemeler (görsel, yazı, tarih, yayına alma) "
                             "çalışmaya devam ediyor."}

        merged = slots.merged_order(data["rows"], area, order)
        if not merged["ok"]:
            return {"ok": False, "error": merged["error"]}

        await self._record(slot_id=0, area=area, action="reorder", reason=reason, actor=actor,
                           result="denendi", detail={"order": merged["order"]})
        try:
            result = await self._api.bbd_reorder_carousel(order=merged["order"], reason=reason,
                                                          actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(slot_id=0, area=area, action="reorder", reason=reason,
                               actor=actor, result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(slot_id=0, area=area, action="reorder", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"order": merged["order"]})
        if not dry_run:
            await self._announce("reorder", {"area": area})
        return {"ok": True, "error": "", "order": merged["order"],
                "dryRun": bool(result.get("dryRun", dry_run)), "notice": CACHE_NOTICE}

    # ================================================================ rapor

    async def export_csv(self) -> dict[str, Any]:
        """Tüm slotların CSV'si — rapor klasörüne yazılır. Görünen listenin
        CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
        data = await self._fetch()
        if not data["connected"] and not data["rows"]:
            return {"ok": False, "error": data["error"] or "Mağazaya ulaşılamadı."}

        headers = ["Bölüm", "Başlık", "Görsel açıklaması", "Tıklayınca gideceği yer",
                   "Sayfadaki yeri", "Hangi ekranda", "Başlangıç", "Bitiş", "Durum",
                   "Sıra", "Tıklama", "Görsel ölçüsü", "Eksikler"]
        table = [[row["areaLabel"], row["title"], row["altText"], row["link"],
                  row["placementLabel"], row["deviceLabel"], row["startsAt"] or "—",
                  row["endsAt"] or "—", row["stateLabel"], row["sortOrder"],
                  row["clicks"] if row["clicksKnown"] else "—",
                  f"{row['imageWidth']}x{row['imageHeight']}" if row["imageWidth"] else "—",
                  ", ".join(row["issues"]) or "—"] for row in data["rows"]]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-ana-sayfa-slotlari-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False,
                    "error": f"Dosya kaydedilemedi: {failure}. Disk dolu olabilir."}
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(table)}

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
        if kind != "layout":
            return {"ok": False, "error": f"Böyle bir rapor yok: {kind}"}

        data = await self._fetch()
        rows = data["rows"]
        if not rows:
            return {"ok": False,
                    "error": "Ana sayfada hiç görsel ya da duyuru yok; raporlanacak bir şey "
                             "bulunamadı. Önce bir görsel ekleyin."}

        area = slots.text(params.get("area"))
        if area:
            rows = [row for row in rows if row["area"] == area]
        stats = slots.summary(rows)

        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Toplam", number(stats["total"])),
                      ("Yayında", number(stats[slots.STATE_PUBLISHED])),
                      ("Tarihi gelmedi", number(stats[slots.STATE_SCHEDULED])),
                      ("Süresi dolmuş", number(stats[slots.STATE_EXPIRED])),
                      ("Görsel açıklaması yok", number(stats["missingAlt"])),
                      ("Ölçüsü tutmuyor", number(stats["lowRes"]))],
        }]
        for name in slots.AREAS:
            subset = [row for row in rows if row["area"] == name]
            if not subset:
                continue
            sections.append({
                "kind": "table", "title": slots.AREA_LABELS[name],
                "headers": ["#", "Başlık", "Tıklayınca gideceği yer", "Yayın tarihleri",
                            "Hangi ekranda", "Durum", "Eksikler"],
                "align": "LLLLLLL", "widths": [0.4, 2.2, 2, 1.4, 0.8, 0.9, 1.6],
                "rows": [[str(index + 1), row["title"], row["link"] or "—",
                          f"{row['startsAt'] or '—'} → {row['endsAt'] or '—'}",
                          row["deviceLabel"], row["stateLabel"],
                          ", ".join(row["issues"]) or "—"]
                         for index, row in enumerate(subset)],
            })
        if data["readOnly"]:
            # Rapor kâğıda basılıp saklanır: yanlış gerekçe orada yıllarca durur.
            # 2026-08-16'da düzeltildi, bkz. modül başlığı.
            sections.append({"kind": "note",
                             "text": "DİKKAT — bu rapor eksik olabilir. Rapor alınırken ana "
                                     "sayfa listesi mağazadan okunamadı; buradaki bilgiler "
                                     "sitenin temasından çıkarılmış özettir. Tıklanınca "
                                     "gidilen yer, yayın tarihleri ve hangi ekranda görüneceği "
                                     "eksik olabilir. Bağlantı düzeldiğinde rapor yeniden "
                                     "alınmalıdır."})

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        content = build_pdf(title="Ana sayfada ne var",
                            subtitle=f"{stats['total']} görsel/duyuru · {slots.today_iso()}",
                            sections=sections, footer="Kontrol Merkezi · Mağaza")
        name = f"magaza-ana-sayfa-yerlesimi-{stamp}.pdf"
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("yerleşim raporu üretildi", rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows)}

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Raporun ekrandaki önizlemesi çizilemedi çünkü bu bilgisayarda önizleme "
                "aracı (poppler-utils) kurulu değil. RAPOR YİNE DE HAZIR: rapor klasörüne "
                "kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-yerlesim-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
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
            return {"ok": False,
                    "error": "Bu bilgisayarda yazdırma kurulu değil. Raporu rapor klasöründen "
                             "açıp elle yazdırabilirsiniz."}
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError:
            return {"ok": False,
                    "error": "Yazdırılacak rapor bulunamadı; önce raporu üretin."}
        allowed = self._export_dir.resolve()
        if not str(resolved).startswith(str(allowed) + os.sep):
            return {"ok": False,
                    "error": "Bu dosya rapor klasöründe değil; güvenlik gereği yazdırılmaz. "
                             "Raporu bu ekrandan yeniden üretin."}
        try:
            result = await self._printer.print_file(resolved, title=resolved.name,
                                                    copies=max(1, min(20, int(copies))))
        except Exception as failure:  # noqa: BLE001 — yazıcı dışarısı
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, **result, "name": resolved.name}

    async def printer_status(self) -> dict[str, Any]:
        if self._printer is None:
            return {"ready": False,
                    "error": "Bu bilgisayarda yazdırma kurulu değil."}
        try:
            return await self._printer.status()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ready": False, "error": self._fail(failure)}


def _flatten_categories(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Kategori ağacını hedef seçicinin anladığı düz listeye açar."""
    if not isinstance(node, dict):
        return []
    slug = slots.text(slots.pick(node, "slug", "url_key"))
    out = [{"id": slots.as_int(node.get("id")),
            "label": ("— " * depth) + slots.text(slots.pick(node, "name", "title")),
            "url": f"/{slug}" if slug else "", "depth": depth}]
    for child in node.get("children") or []:
        out.extend(_flatten_categories(child, depth + 1))
    return out
