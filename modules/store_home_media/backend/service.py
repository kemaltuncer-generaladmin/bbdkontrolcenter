"""Ana Ekran Görselleri — iş kuralları.

EKRANIN TEK BİR İŞİ VAR: siteye ilk girişte dönen ~10 görseli değiştirmek,
sıralarını belirlemek ve tıklanınca nereye gideceklerini seçmek.

18.08.2026'DA ÜÇ SEKME KALDIRILDI. Ekran dört şeridi (kayan görseller,
tanıtım görselleri, öne çıkan ürün grupları, üst duyuru yazısı) birlikte
yönetiyordu. Üçünün mağazada karşılığı ya yoktu ya da bu uçtan yazılamıyordu;
ekranın yarısı "bu bölüm şu an düzenlenemiyor" demeye çalışıyordu. Kullanıcı
kararı üçünün de kaldırılması oldu. Onunla birlikte yayın tarihleri, cihaz
seçimi, yayına alma/kaldırma, süzgeçler, durum rozetleri, yerleşim raporu ve
CSV de kalktı: hiçbiri bu tek işe hizmet etmiyordu.

VERİ MAĞAZADADIR, KARAR BURADADIR. Slaytlar `store.api` geçidinden gelir (K4);
bu modül onların kopyasını tutmaz. Yerel tablolar yalnız mağazada KARŞILIĞI
OLMAYAN iki şeyi saklar: yazma gerekçesi (denetim izi, ADR 0012) ve yüklenen
görselin ölçü kararı ("önerilen 1920x640, yüklenen 1200x400 — mobilde
bulanık").

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): `connected: False` + `error`
döner ve panel nedenini yazar; beyaz sayfa göstermez.

MAĞAZA UÇLARI (2026-08-18'de yazıldı, `packages/BBD/ControlApi`):
    GET  /api/admin/bbd/storefront/home-slides         → sıralı liste
    PUT  /api/admin/bbd/storefront/home-slides         → TAM listeyi yazar
    POST /api/admin/bbd/storefront/home-slides/image   → tek görsel yükler

SIRA AYRI BİR UÇ DEĞİLDİR. Vitrin `options.images` dizisini olduğu gibi
çiziyor, yani sıra dizinin kendi sırası. "Sırayı değiştir" ile "içeriği
değiştir" bu yüzden aynı yazma işlemidir ve uç TAM listeyi alır — kısmi
güncelleme, sırayı iki isteğe böler ve arada vitrin yarım listeyle çizilirdi.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from . import slots

#: Ana ekran kayan görselinin önerilen ölçüsü — ayar dosyası ezer.
#: Değer mağazanın şu anki temasından alınmıştır (vitrin 2.743:1 oranında
#: çiziyor; 1920x640 = 3:1 ona en yakın yuvarlak ölçü).
DEFAULT_RECOMMENDED = "1920x640"

#: Vitrine yansımanın gecikebileceğini SÖYLERİZ; sessiz kalıp "neden değişmedi"
#: sorusunu doğurmayız.
#:
#: METİN 18.08.2026'DA DEĞİŞTİ. Eskiden "birkaç dakika sürebilir, sonra
#: yenileyin" diyordu ve bu doğru ama çaresiz bir cümleydi. Mağaza ucu artık
#: yazma sonrası `responsecache:clear` çağırıyor; gecikme kalan tek yerde
#: (tarayıcının kendi önbelleği ve varsa ara katman) olabilir.
CACHE_NOTICE = ("Kaydedildi ve sitenin sayfa önbelleği temizlendi. Kendi tarayıcınızda "
                "hemen görmezseniz sayfayı yenileyin (Ctrl+F5).")


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class HomeMediaService:
    """Ana Ekran Görselleri ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 publish: Any = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._publish = publish

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

    @property
    def _wanted(self) -> tuple[int, int]:
        """Önerilen ölçü. Ayar bozuksa temanın varsayılanına düşülür."""
        size = slots.parse_size(str(self._config.get("recommended_slider") or ""))
        return size if size != (0, 0) else slots.parse_size(DEFAULT_RECOMMENDED)

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      slot_id: int = 0, detail: Any = None) -> None:
        """Yerel denetim izi (ADR 0012). Bagisto denetim tutuyor ama GEREKÇEYİ
        tutmuyor; ayrıca ağ koparsa "ne yapmaya çalıştık" kaydı burada kalır.

        `area` sütunu ŞEMADA KALDI ama artık tek değer yazılıyor (`slider`):
        eski satırlar diğer şeritleri taşıyor ve tablo silinmiyor (BBD veri
        silme yasağı). Sütunu düşürmek, geçmiş kaydı okunamaz hâle getirirdi.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(slot_id, area, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (int(slot_id or 0), "slider", action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _record_asset(self, *, image: dict[str, Any], verdict: dict[str, Any],
                            actor: str) -> None:
        """Yüklenen görselin ölçü kararını dondurur.

        Uyarıya rağmen yüklenen bulanık görselin izi burada kalır; aksi hâlde
        "bu görsel neden bulanık" sorusunun cevabı hiçbir yerde olmazdı.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._assets} "
                "(slot_id, area, sha256, mime, width, height, bytes, verdict, note, actor, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (0, "slider", image.get("sha256", ""), image.get("mime", ""),
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

    # ================================================================ okuma

    async def slides(self) -> dict[str, Any]:
        """Ana ekran kayan görselleri — SIRALI liste. HATA FIRLATMAZ (K7)."""
        wanted = self._wanted
        base: dict[str, Any] = {
            "ok": True,
            "recommended": f"{wanted[0]}x{wanted[1]}",
            "maxImageBytes": self._max_bytes,
            "allowedTypes": list(self._allowed),
            "maxSlides": slots.MAX_SLIDES,
            "notice": CACHE_NOTICE,
        }
        try:
            payload = await self._api.bbd_home_slides(
                {"channel": self._channel, "locale": self._locale})
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            self._log.warning("ana ekran görselleri okunamadı", error=str(failure))
            return {**base, "items": [], "connected": False, "error": self._fail(failure)}

        rows = [
            slots.slide_row(item, index=index, wanted=wanted, sharp_ratio=self._sharp,
                            tolerance=self._tolerance)
            for index, item in enumerate(payload.get("items") or [])
        ]
        meta = payload.get("meta") or {}
        return {**base, "items": rows, "connected": True, "error": "",
                "themeId": slots.as_int(slots.pick(meta, "theme_id")),
                "issues": sum(1 for row in rows if row["issues"])}

    async def link_search(self, *, q: str) -> dict[str, Any]:
        """Hedef seçici için ürün arama. Boş sorguda mağazaya HİÇ gidilmez.

        NEDEN YALNIZ ÜRÜN. Ekranda dört hedef türü vardı (serbest adres, ürün,
        kategori, bilgi sayfası) ve üçü ayrı bir referans isteği gerektiriyordu.
        Bugün hedef tek bir adres kutusudur; ürün araması onu doldurmaya
        yarayan tek yardımcıdır, kategori/sayfa adresi zaten elle yazılabilir.
        """
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
            url_key = slots.text(slots.pick(item, "url_key", "slug"))
            items.append({"id": slots.as_int(item.get("id")),
                          "name": slots.text(slots.pick(item, "name", "title")),
                          "sku": slots.text(item.get("sku")),
                          "url": f"/{url_key}" if url_key else ""})
        return {"ok": True, "items": items, "connected": True, "error": ""}

    async def audit(self, *, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazmaların YEREL izi (gerekçeleriyle)."""
        sql = (f"SELECT slot_id, area, action, reason, actor, result, created_at "
               f"FROM {self._audit} ORDER BY id DESC LIMIT ?")
        try:
            rows = await self._store.fetch_all(sql, (max(1, min(500, int(limit))),))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            return {"ok": True, "items": [], "error": self._fail(failure)}
        return {"ok": True, "error": "", "items": [
            {"action": row["action"], "reason": row["reason"], "actor": row["actor"],
             "result": row["result"], "createdAt": row["created_at"]}
            for row in rows
        ]}

    # ========================================================= görsel denetim

    def _image_report(self, image: dict[str, Any]) -> dict[str, Any]:
        """Ölçü kararı + kırpma planı + gerçek oranlı önizleme kutusu — TEK yerde.

        Denetim ve yükleme aynı cümleleri söylemek ZORUNDA: kullanıcı seçim
        anında "kenarlardan %25 kırpılacak" okuyup yükleme anında başka bir
        metin görürse hangisine güveneceğini bilemez.
        """
        wanted = self._wanted
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
            # sığdırmak kırpmayı gizlerdi (TUZAK 4).
            "previewBox": slots.preview_box(image["width"], image["height"]),
            # Oran denetimi ZORUNLU: uyarı görülmeden yükleme geçmez.
            "needsConfirm": verdict["state"] in slots.WARN_STATES,
        }

    def check_image(self, *, data: str) -> dict[str, Any]:
        """Görseli YAZMADAN ÖNCE ölçer. Panel dosya seçilince bunu çağırır.

        Ağa çıkmaz: karar tamamen yereldir ve kullanıcı "Kaydet"e basmadan
        önce "mobilde bulanık / kenarlardan kırpılacak" uyarısını görür.
        """
        image = slots.decode_image(data, max_bytes=self._max_bytes, allowed=self._allowed)
        if not image["ok"]:
            return {"ok": False, "error": image["error"]}
        return {"ok": True, "error": "", **self._image_report(image)}

    async def upload_image(self, *, data: str, filename: str = "", acknowledged: bool = False,
                           reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Görseli mağazanın yükleme ucuna gönderir ve SAKLANAN YOLU döndürür.

        YÜKLEME İLE LİSTE YAZMA AYRI İKİ ADIMDIR — mağaza ucu da öyle kurgulandı.
        On slaytlık bir kaydetmede görselleri gövdeye gömmek 30 MB'lık tek bir
        istek üretir ve hangi dosyanın reddedildiği ancak tüm liste
        reddedildikten sonra öğrenilirdi.

        SIRALAMA: gerekçe → çöz → ölçü kararı → ONAY → ağ. Ağa çıkıp sonra
        "oranı uygun değil" demek, mağazada gereksiz bir dosya bırakırdı.
        """
        problem = slots.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        image = slots.decode_image(data, max_bytes=self._max_bytes, allowed=self._allowed)
        if not image["ok"]:
            return {"ok": False, "error": image["error"]}

        report = self._image_report(image)
        if report["needsConfirm"] and not acknowledged:
            # K9: panelde onay kutusu göstermek yetkilendirme değil. Onay
            # gelmediyse dosya ağa HİÇ çıkmaz.
            return {"ok": False, "needsConfirm": True, "error": report["sizeNote"], **report}

        name = slots.safe_filename(filename, image["mime"])
        await self._record(action="upload_image", reason=reason, actor=actor, result="denendi",
                           detail={"file": name, "bytes": image["bytes"],
                                   "size": report["sizeState"],
                                   "acknowledged": bool(acknowledged)})
        try:
            result = await self._api.upload_media(
                content=image["base64"], filename=name, mime=image["mime"],
                reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — ekran ayakta kalmalı (K7)
            pending = slots.is_endpoint_pending(getattr(failure, "code", ""), str(failure))
            await self._record(action="upload_image", reason=reason, actor=actor,
                               result="beklemede" if pending else "hata",
                               detail={"error": str(failure)})
            return {"ok": False, "pending": pending, "error": self._fail(failure), **report}

        await self._record(action="upload_image", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"file": name, "size": report["sizeState"]})
        # Ölçü kararı uyarılı olsa da kaydedilir: "bu görsel neden bulanık"
        # sorusunun cevabı yalnız burada durur.
        await self._record_asset(image=image,
                                 verdict={"state": report["sizeState"],
                                          "note": report["sizeNote"]}, actor=actor)
        return {"ok": True, "error": "", "pending": False, "file": name,
                "dryRun": bool(result.get("dryRun", dry_run)),
                # Liste yazması BU YOLU taşır. Mağaza serbest yol kabul etmiyor;
                # yalnız kendi yüklediği klasördeki dosyayı kabul ediyor.
                "image": slots.text(slots.pick(result, "image", "path")),
                "url": slots.text(slots.pick(result, "url", "image_url")),
                **report}

    # =============================================================== yazma

    async def save_slides(self, *, slides: list[dict[str, Any]], reason: str, actor: str,
                          dry_run: bool = True) -> dict[str, Any]:
        """Slayt listesinin TAMAMINI yazar — sıra, hedef ve ad birlikte.

        SIRALAMA ÖNEMLİ: önce gerekçe, sonra iş kuralı, en son ağ. Ağa çıkıp
        sonra "3. görselin adı boş" demek, mağazada yarım bir liste bırakırdı.
        """
        problem = slots.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        clean = slots.normalize_slides(slides)
        rule = slots.slides_error(clean)
        if rule:
            return {"ok": False, "error": rule}

        await self._record(action="save_slides", reason=reason, actor=actor, result="denendi",
                           detail={"count": len(clean)})
        try:
            result = await self._api.bbd_save_home_slides(
                slides=clean, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="save_slides", reason=reason, actor=actor, result="hata",
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="save_slides", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"count": len(clean),
                                   "order": [slide["title"] for slide in clean]})
        if not dry_run:
            await self._announce("save_slides", {"count": len(clean)})

        return {"ok": True, "error": "", "count": len(clean),
                "dryRun": bool(result.get("dryRun", dry_run)),
                "notice": CACHE_NOTICE}
