"""Dahili AI Ekranları — iş kuralları.

İKİ İŞ, İKİ BAĞIMLILIK SEVİYESİ:

  · KATALOG SAĞLIĞI yalnız ÇEKİRDEK Bagisto uçlarına (ürün listesi, kategori
    ağacı) dayanır ve bulguyu burada hesaplar. Mağazadaki BBD/AI uçları hiç
    yayınlanmasa bile bu yarı bugün çalışır.
  · ARAÇLAR `/api/admin/bbd/ai/*` uçlarına dayanır. Uç yoksa geçit
    `bbd_endpoint_missing` koduyla anlaşılır bir hata verir; ekran araç
    kartlarını "uç hazır olunca açılacak" notuyla kapatır ve ÇÖKMEZ (K7).

ÜÇ SERT KURAL:

  1. AI HİÇBİR ŞEYİ DOĞRUDAN YAZMAZ. `run_tool` yalnız öneri üretir ve fark
     tablosunu yerel tabloya yazar. Yazma yalnız `apply` ile, yalnız
     kullanıcının seçtiği satırlar için ve yalnız TABLODA DURAN değerlerle
     olur — öneri yeniden üretilmez.
  2. PARA HARCAYAN İŞ ÖLÇÜLMEDEN YAPILMAZ. Aylık bütçe kapısı her
     çalıştırmadan önce sorulur; sayaç okunamazsa davranış `budget_verdict`
     içinde tek yerde yazılıdır.
  3. GEREKÇE BACKEND'DE DE DOĞRULANIR (K9). Arayüzde gizlemek yetkilendirme
     değildir; istemci şemayı atlatabilir.

HTTP hatası FIRLATILMAZ: her metot `{"ok": ..., "error": ...}` döner.
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import ExportError, build_pdf, csv_bytes, money, number, report_dir, write_private

from . import analytics, rules

#: Gerekçenin en az uzunluğu. Geçit de 10 istiyor; burada tekrar doğrulanır.
MIN_REASON = 10

#: Tek çalıştırmaya girebilecek en çok hedef. AI çağrısının kendisi tek
#: istektir ama "önce" değerlerini okumak hedef başına bir istek eder ve
#: mağaza dakikada 60 kabul eder. 25, bir raf dolusu ürün için yeter; daha
#: büyük iş süzgeç daraltılarak parçalanır.
MAX_TARGETS = 25

#: Ekran tercihi olarak saklanabilen anahtarlar. Listede olmayan anahtar
#: yazılmaz: ayar adını serbest bırakmak, yazım hatasını sessiz bir "kaydettim
#: ama hiçbir şey değişmedi" durumuna çevirir.
PREF_KEYS = (
    "monthly_budget", "budget_behavior", "budget_estimate_when_offline",
    "min_description_chars", "meta_title_max", "meta_description_min",
    "meta_description_max", "price_outlier_factor", "include_passive", "disabled_tools",
)

RULE_OPTION_KEYS = ("min_description_chars", "meta_title_max", "meta_description_min",
                    "meta_description_max", "price_outlier_factor")


def _now() -> str:
    """Yerel defterin zaman damgası — YEREL saat, kayması belli olsun diye offsetli.

    UTC YAZILMAZ. Bu değeri okuyan üç yer de onu YEREL sanıyor:

      · `analytics.ledger_spend` ayı `created_at[:7]` ile kesiyor ve
        `rules.today_iso()`den gelen YEREL ayla karşılaştırıyor. UTC yazılırsa
        +03:00'ta ayın ilk üç saatinde yapılan çalıştırma bir ÖNCEKİ ayın
        bütçesine işlenir — para kapısı yanlış ayı sayar.
      · `analytics.usage_view` günlük maliyeti `created_at[:10]` ile kovalıyor;
        gece yarısından sonraki üç saatlik harcama bir önceki güne düşerdi.
      · Panel `createdAt` metnini olduğu gibi yazıyor (çevirmiyor, çeviremez de:
        mağazadan gelen satırlar saat dilimsiz ve YEREL). UTC yazan sürüm denetim
        listesinde her çalıştırmayı üç saat erken gösteriyordu.

    Offset korunur (`+03:00`): bilgi kaybı yok, ama tüm dilim kesmeleri
    (`[:7]`, `[:10]`, `[:19]`) yerel takvimi verir — `rules.today_iso()` ile
    aynı dili konuşur.
    """
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class AiService:
    """Dahili AI ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None, category: str = "Mağaza",
                 subcategory: str = "Ürün", fallback_dir: Path | None = None) -> None:
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
        self._runs = store.table("run")
        self._ignored = store.table("ignored")
        self._prefs = store.table("prefs")

        # Katalog taraması 29 sayfa eder; sonuç bellekte tutulur ve ekran
        # tarama zamanını yazar. Kalıcı tabloya yazılmaz: bu, mağaza verisinin
        # kopyası olurdu ve kopya sessizce eskir.
        self._scan: dict[str, Any] | None = None
        self._scan_at = 0.0

    # ------------------------------------------------------------- ayarlar

    @property
    def _channel(self) -> str:
        return str(self._config.get("channel") or "default")

    @property
    def _locale(self) -> str:
        return str(self._config.get("locale") or "tr")

    @property
    def _page_size(self) -> int:
        return max(10, min(200, rules.as_int(self._config.get("page_size"), 50)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    async def _pref(self, key: str) -> str:
        try:
            row = await self._store.fetch_one(
                f"SELECT value FROM {self._prefs} WHERE key = ?", (key,))
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", key=key, error=str(failure))
            return ""
        return str(row["value"]) if row else ""

    async def _set_pref(self, key: str, value: str, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (key, value, actor, _now()),
        )

    async def _setting(self, key: str, default: Any = "") -> Any:
        """Ekran tercihi > config varsayılanı. Tek okuma noktası."""
        stored = await self._pref(key)
        if stored != "":
            return stored
        value = self._config.get(key)
        return default if value is None else value

    async def _options(self) -> dict[str, Any]:
        """Kural eşikleri — `rules.scan_products` bunu okur."""
        out: dict[str, Any] = {}
        for key in RULE_OPTION_KEYS:
            out[key] = rules.as_int(await self._setting(key), 0)
        return out

    @staticmethod
    def _truthy(value: Any) -> bool:
        return str(value).strip().lower() in ("1", "true", "evet", "yes", "on")

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, target_id: int, action: str, reason: str, actor: str,
                      result: str, detail: Any = None) -> None:
        """Yerel denetim izi. Mağazanın AI kaydı GEREKÇE tutmuyor; ayrıca ağ
        koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(target_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _ledger(self, *, limit: int = 500) -> list[dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT token, run_id, tool, status, actor, reason, tokens, cost, targets, "
                f"applied, duration_ms, error, created_at, applied_at FROM {self._runs} "
                "ORDER BY id DESC LIMIT ?", (max(1, min(2000, int(limit))),))
        except Exception as failure:  # noqa: BLE001 — defter okunamadı, ekran dursun
            self._log.warning("çalışma defteri okunamadı", error=str(failure))
            return []
        return [dict(row) for row in rows]

    async def _ignored_keys(self) -> dict[str, dict[str, Any]]:
        """Susturulmuş bulgular. `released_at` dolu olan satır susturmayı
        KALDIRIR: susturma satır silinerek değil, satır eklenerek geri alınır."""
        try:
            rows = await self._store.fetch_all(
                f"SELECT finding_key, reason, actor, created_at, released_at "
                f"FROM {self._ignored} ORDER BY id ASC", ())
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("susturma listesi okunamadı", error=str(failure))
            return {}
        state: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row["finding_key"])
            if str(row["released_at"] or ""):
                state.pop(key, None)
            else:
                state[key] = {"reason": row["reason"], "actor": row["actor"],
                              "createdAt": row["created_at"]}
        return state

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    @staticmethod
    def _missing_endpoint(failure: Exception) -> bool:
        """Geçit "uç henüz yayında değil" mi diyor? Kod alanı `StoreApiError`
        üstünde durur; metne bakarak tahmin etmek çeviri değişince kırılır."""
        return getattr(failure, "code", "") == "bbd_endpoint_missing"

    def _guard(self, reason: str) -> str:
        if len(rules.text(reason)) < MIN_REASON:
            return (f"Gerekçe en az {MIN_REASON} karakter olmalı; "
                    "denetim kaydına bu metin yazılır.")
        return ""

    # ================================================================ araçlar

    async def tools(self) -> dict[str, Any]:
        """Araç kartları + bütçe durumu + uç durumu.

        Mağazanın sunduğu araç listesiyle bu ekranın TANIDIĞI araç listesi
        kesiştirilir. Tanınmayan araç listelenir ama çalıştırılamaz: çıktısının
        hangi alana yazılacağı bilinmeden fark tablosu çizilemez ve "onaysız
        uygulama yok" kuralı boşa düşer.
        """
        disabled = {item for item in str(await self._setting("disabled_tools", "")).split(",")
                    if item}
        remote: dict[str, dict[str, Any]] = {}
        connected, ready, error = True, True, ""
        try:
            payload = await self._api.bbd_ai_tools()
            for item in payload.get("items") or []:
                if isinstance(item, dict):
                    remote[rules.text(item.get("key") or item.get("code") or item.get("name"))] = item
        except Exception as failure:  # noqa: BLE001 — K7: ekran ayakta kalır
            connected = False
            ready = not self._missing_endpoint(failure)
            error = self._fail(failure)
            self._log.info("AI araç listesi alınamadı", error=error)

        cards: list[dict[str, Any]] = []
        for card in analytics.tool_labels():
            offered = card["key"] in remote
            card["offered"] = offered
            card["enabled"] = card["key"] not in disabled
            card["known"] = True
            if not connected:
                card["blockReason"] = (
                    "Mağazadaki AI ucu henüz yayında değil; uç hazır olunca bu araç açılacak."
                    if not ready else f"Mağazaya ulaşılamadı — {error}")
            elif not offered:
                card["blockReason"] = "Mağaza bu aracı sunmuyor (MagicAI tarafında kapalı)."
            elif not card["enabled"]:
                card["blockReason"] = "Bu araç Ayarlar sekmesinden kapatılmış."
            else:
                card["blockReason"] = ""
            card["runnable"] = not card["blockReason"]
            cards.append(card)

        for key, item in remote.items():
            if analytics.known_tool(key):
                continue
            cards.append({
                "key": key, "label": rules.text(item.get("label")) or key,
                "summary": rules.text(item.get("description")),
                "target": "", "writes": [], "readOnly": True, "note": "",
                "offered": True, "enabled": False, "known": False, "runnable": False,
                "blockReason": "Bu ekran bu aracı tanımıyor; çıktısının hangi alana "
                               "yazılacağı bilinmediği için fark tablosu çizilemez.",
            })

        budget = await self.budget_state()
        return {"ok": True, "connected": connected, "endpointReady": ready, "error": error,
                "tools": cards, "budget": budget, "maxTargets": MAX_TARGETS}

    # ================================================================= bütçe

    async def budget_state(self) -> dict[str, Any]:
        """Bütçe kapısının o anki kararı. Çalıştırmadan ÖNCE de bu sorulur."""
        budget = rules.as_int(await self._setting("monthly_budget"), 0)
        behavior = str(await self._setting("budget_behavior", "block")) or "block"
        estimate = self._truthy(await self._setting("budget_estimate_when_offline", True))
        month = analytics.month_key(rules.today_iso())
        ledger = await self._ledger()
        local_spend = analytics.ledger_spend(ledger, month=month)

        measured = False
        remote_spend = 0
        try:
            usage = await self._api.bbd_ai_usage({"month": month})
            view = analytics.usage_view(usage, ledger, month=month)
            # Uç YANIT VERDİ ama sayaç taşımıyor olabilir. O yanıtı "ölçüldü"
            # saymak, aslında yerel defterden gelen rakamı kesin gibi
            # gösterirdi; `usage_view` bunu zaten ayırt ediyor.
            measured = view["measured"]
            remote_spend = view["spent"] if measured else 0
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.info("AI kullanım sayacı okunamadı", error=str(failure))

        # İKİ SAYAÇ VARSA BÜYÜĞÜ: yerel defter Kontrol Merkezi dışından yapılan
        # çalıştırmaları görmez, mağaza sayacı ise ağ koptuğunda geride kalır.
        # Küçüğünü seçmek bütçeyi delerdi.
        spent = max(local_spend, remote_spend)
        verdict = analytics.budget_verdict(spent=spent, budget=budget, behavior=behavior,
                                           measured=measured, estimate_allowed=estimate)
        verdict["behavior"] = behavior
        verdict["month"] = month
        return verdict

    async def usage(self, *, days: int = 30) -> dict[str, Any]:
        month = analytics.month_key(rules.today_iso())
        ledger = await self._ledger()
        connected, error = True, ""
        remote: Any = None
        try:
            remote = await self._api.bbd_ai_usage({"month": month, "days": int(days)})
        except Exception as failure:  # noqa: BLE001 — K7
            connected = False
            error = self._fail(failure)

        view = analytics.usage_view(remote, ledger, month=month, days=days)
        return {"ok": True, "connected": connected, "error": error,
                "budget": await self.budget_state(), **view}

    # ======================================================== katalog sağlığı

    async def catalog(self, *, refresh: bool = False) -> dict[str, Any]:
        """Kural tabanlı katalog denetimi. AI YOK, para harcamaz.

        Tarama pahalıdır (1.419 ürün ≈ 29 sayfa) ve bellekte `scan_ttl_seconds`
        boyunca taze sayılır. Ekran tarama zamanını YAZAR: kullanıcı bayat
        rakama bakıp bakmadığını bilmeli.
        """
        ttl = rules.as_int(self._config.get("scan_ttl_seconds"), 900)
        fresh = self._scan is not None and (time.monotonic() - self._scan_at) < ttl
        if refresh or not fresh:
            self._scan = await self._rescan()
            self._scan_at = time.monotonic()

        scan = dict(self._scan or {})
        ignored = await self._ignored_keys()
        findings = rules.sort_findings(rules.drop_ignored(scan.get("findings") or [],
                                                          set(ignored)))
        summary = rules.summarize(findings, scanned=scan.get("scanned", 0),
                                  disabled=scan.get("disabledRules") or [])
        summary["muted"] = len(ignored)
        return {"ok": True, "connected": scan.get("connected", False),
                "error": scan.get("error", ""), "warnings": scan.get("warnings") or [],
                "truncated": scan.get("truncated", False), "scannedAt": scan.get("scannedAt", ""),
                "ttlSeconds": ttl, "findings": findings, "summary": summary,
                "muted": [{"key": key, **value} for key, value in ignored.items()]}

    async def _rescan(self) -> dict[str, Any]:
        """Tek tarama: ürünler + kategori ağacı → bulgular. İstisna sızmaz."""
        cap = rules.as_int(self._config.get("scan_row_cap"), 3000)
        # KANAL SÜZGECİ — canlıda ölçüldü (2026-08-13). Bu uç kanal KODUNU
        # ister: `channel=default` → 1.421 kayıt, `channel=1` (kimlik) → 0
        # kayıt, hata yok. Sipariş ucunun tersidir (orası kimlik ister); iki
        # ucun aynı davrandığını varsaymak listeyi sessizce boşaltırdı.
        filters = {"channel": self._channel, "locale": self._locale}
        warnings: list[str] = []

        try:
            payload = await self._api.products(filters, all_pages=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("katalog taranamadı", error=str(failure))
            return {"connected": False, "error": self._fail(failure), "findings": [],
                    "scanned": 0, "truncated": False, "warnings": [],
                    "scannedAt": _now(), "disabledRules": []}

        raw = (payload.get("items") or [])[:cap]
        truncated = bool(payload.get("truncated")) or len(payload.get("items") or []) > cap
        facts = [rules.product_facts(item) for item in raw if isinstance(item, dict)]

        include_passive = self._truthy(await self._setting("include_passive", False))
        options = await self._options()
        findings = rules.scan_products(facts, options=options, include_passive=include_passive)

        disabled: list[str] = []
        if not rules.barcode_defined(facts):
            # TUZAK: barkod alanı taranan kayıtlarda hiç gelmiyorsa "barkodu
            # eksik" bulgusu 1.421 satırlık anlamsız bir duvar üretir.
            # Katalogda `isbn` özniteliği TANIMLI (öznitelik ailesinde
            # görünüyor) ama liste ucu değerini döndürmüyor — "yok" diyemeyiz,
            # "göremiyoruz" deriz.
            disabled.append(rules.MISSING_BARCODE)
            warnings.append("Barkod/ISBN değeri ürün listesi ucunda gelmiyor; barkod "
                            "denetimi kapatıldı. Kapatılmasaydı her ürün 'barkodu yok' "
                            "görünürdü.")

        # BOŞ KATEGORİ DENETİMİ — kategori bilgisi eksikse HİÇ ÇALIŞTIRILMAZ.
        # Liste ucu ürünün yalnız BİRİNCİL kategorisini veriyor (canlıda
        # doğrulandı: 1418 numaralı ürün dört kategoride, listede bir tanesi
        # görünüyor). Eksik veriyle sayınca ikincil kategorilerin hepsi "boş"
        # çıkar; barkod kuralında olduğu gibi susmak, yanlış söylemekten iyidir.
        if rules.category_data_partial(facts):
            disabled.append(rules.EMPTY_CATEGORY)
            warnings.append("Ürün listesi ucu yalnız birincil kategoriyi veriyor; boş "
                            "kategori denetimi kapatıldı. Açık kalsaydı ürünü olan "
                            "kategoriler de boş görünürdü.")
        else:
            try:
                tree = await self._api.category_tree()
                categories = rules.flatten_categories(tree.get("items") or [])
                findings.extend(rules.empty_categories(facts, categories,
                                                       include_passive=include_passive))
            except Exception as failure:  # noqa: BLE001 — K7
                disabled.append(rules.EMPTY_CATEGORY)
                warnings.append(f"Kategori ağacı okunamadı; boş kategori denetimi çalışmadı "
                                f"({self._fail(failure)}).")

        if truncated:
            warnings.append(f"Katalog {cap} üründe kırpıldı; bulgular eksik olabilir ve "
                            "boş kategori sayısı olduğundan fazla görünebilir.")

        return {"connected": True, "error": "", "findings": findings, "scanned": len(facts),
                "truncated": truncated, "warnings": warnings, "scannedAt": _now(),
                "disabledRules": disabled}

    async def mute(self, *, finding_key: str, kind: str, target_id: int, reason: str,
                   actor: str) -> dict[str, Any]:
        """Bulguyu sustur. SİLME DEĞİL: satır eklenir, geçmiş kalır."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        key = rules.text(finding_key)
        if not key or ":" not in key:
            return {"ok": False, "error": "Bulgu anahtarı geçersiz."}
        try:
            await self._store.execute(
                f"INSERT INTO {self._ignored} "
                "(finding_key, kind, target_id, reason, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (key, rules.text(kind), int(target_id or 0), reason, actor, _now()))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(target_id=target_id, action="mute_finding", reason=reason,
                           actor=actor, result="ok", detail={"key": key})
        return {"ok": True, "error": "", "key": key}

    async def unmute(self, *, finding_key: str, reason: str, actor: str) -> dict[str, Any]:
        """Susturmayı kaldırır — satır SİLİNMEZ, serbest bırakma satırı eklenir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        key = rules.text(finding_key)
        try:
            await self._store.execute(
                f"INSERT INTO {self._ignored} "
                "(finding_key, kind, target_id, reason, actor, created_at, released_at) "
                "VALUES (?, '', 0, ?, ?, ?, ?)",
                (key, reason, actor, _now(), _now()))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        await self._record(target_id=0, action="unmute_finding", reason=reason, actor=actor,
                           result="ok", detail={"key": key})
        return {"ok": True, "error": "", "key": key}

    # ================================================================ çalışma

    async def run_tool(self, tool: str, *, target_ids: list[int], params: dict[str, Any],
                       reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """AI aracını çalıştırır — ÖNERİ ÜRETİR, HİÇBİR ŞEY YAZMAZ.

        Sıra bilinçlidir: gerekçe → araç tanınıyor mu → araç açık mı → BÜTÇE →
        hedefler → "önce" değerleri → çağrı. Bütçe kapısı hedefleri okumadan
        önce sorulur; bütçe doluysa hedef okumak için bile istek harcamayız.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        key = rules.text(tool)
        if not analytics.known_tool(key):
            return {"ok": False, "error": "Bu ekran bu aracı tanımıyor; çalıştırılamaz."}

        disabled = {item for item in str(await self._setting("disabled_tools", "")).split(",")
                    if item}
        if key in disabled:
            return {"ok": False, "error": "Bu araç Ayarlar sekmesinden kapatılmış."}

        budget = await self.budget_state()
        if not budget["allowed"]:
            await self._record(target_id=0, action=f"run:{key}", reason=reason, actor=actor,
                               result="reddedildi", detail={"budget": budget["message"]})
            return {"ok": False, "error": budget["message"], "budget": budget}

        spec = analytics.TOOLS[key]
        ids = [int(item) for item in (target_ids or []) if rules.as_int(item)]
        if spec["target"] == "product" and not ids:
            return {"ok": False, "error": "Hedef ürün seçilmedi."}
        if len(ids) > MAX_TARGETS:
            return {"ok": False,
                    "error": f"Tek çalıştırmada en çok {MAX_TARGETS} hedef. "
                             "Süzgeci daraltıp parça parça çalıştırın."}

        before = await self._before_map(key, ids) if spec["target"] == "product" else {}
        body = {"targets": ids, "params": dict(params or {}),
                "channel": self._channel, "locale": self._locale}

        token = uuid.uuid4().hex
        started = time.monotonic()
        await self._record(target_id=ids[0] if len(ids) == 1 else 0, action=f"run:{key}",
                           reason=reason, actor=actor, result="denendi",
                           detail={"targets": len(ids), "dryRun": dry_run})
        try:
            payload = await self._api.bbd_ai_run(key, payload=body, reason=reason, actor=actor,
                                                 dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            note = ("Mağazadaki AI ucu henüz yayında değil; uç hazır olunca bu araç açılacak."
                    if self._missing_endpoint(failure) else self._fail(failure))
            await self._save_run(token=token, run_id=0, tool=key, params=body, rows=[],
                                 status="failed", actor=actor, reason=reason, tokens=0, cost=0,
                                 targets=len(ids), duration_ms=self._elapsed(started),
                                 error=note)
            await self._record(target_id=0, action=f"run:{key}", reason=reason, actor=actor,
                               result="hata", detail={"error": note})
            return {"ok": False, "error": note, "endpointReady": not self._missing_endpoint(failure)}

        sent = bool(payload.get("sent", not dry_run))
        run_id = self._run_number(payload)
        rows = analytics.suggestion_rows(key, payload, before=before)
        tokens = rules.as_int(payload.get("tokens") or payload.get("usedTokens"), 0)
        cost = analytics.cost_kurus(payload)

        status = "readonly" if not spec["writes"] else ("preview" if sent else "dry_run")
        # ÖZET METİN DE SAKLANIR. Yorum duygu özeti gibi yazmayan araçlarda
        # çıktının TAMAMI bu metindir; saklanmazsa geçmişten açılan çalışma
        # bomboş görünür (fark tablosu da yok, metin de yok).
        summary_text = rules.text(payload.get("summary") or payload.get("text"))
        await self._save_run(token=token, run_id=run_id, tool=key, params=body, rows=rows,
                             status=status, actor=actor, reason=reason, tokens=tokens,
                             cost=cost, targets=len(ids), summary_text=summary_text,
                             duration_ms=self._elapsed(started), error="")
        await self._record(target_id=0, action=f"run:{key}", reason=reason, actor=actor,
                           result="dry_run" if not sent else "ok",
                           detail={"token": token, "rows": len(rows), "cost": cost})

        summary = analytics.diff_summary(rows)
        return {
            "ok": True, "error": "", "token": token, "runId": run_id, "tool": key,
            "toolLabel": spec["label"], "readOnly": not spec["writes"],
            "rows": rows, "summary": summary, "tokens": tokens, "cost": cost,
            "dryRun": not sent, "sent": sent,
            "text": summary_text,
            "applicable": bool(spec["writes"]) and sent and run_id > 0,
            "note": self._apply_note(spec, sent=sent, run_id=run_id),
            "budget": await self.budget_state(),
        }

    @staticmethod
    def _run_number(payload: Any) -> int:
        """Mağazanın çalışma numarası — zarflı ya da düz gelebilir.

        ÖNCEKİ SÜRÜMÜN HATASI: koşullu ifade `data` bir sözlükse YALNIZ
        `data.id` bakıyor, o anahtar yoksa `runId`'ye hiç düşmüyordu.
        `{"data": {"runId": 42}}` yanıtı 0 olarak okunuyor ve "mağaza çalışma
        numarası vermedi" diyerek uygulamayı kapatıyordu. Numara olmadan
        uygulama açılmadığı için hata sessizdi: ekran öneriyi gösterir, düğme
        hiç açılmazdı.
        """
        if not isinstance(payload, dict):
            return 0
        envelopes: list[dict[str, Any]] = [payload]
        data = payload.get("data")
        if isinstance(data, dict):
            envelopes.insert(0, data)
        for source in envelopes:
            for key in ("id", "runId", "run_id"):
                found = rules.as_int(source.get(key))
                if found:
                    return found
        return 0

    @staticmethod
    def _apply_note(spec: dict[str, Any], *, sent: bool, run_id: int) -> str:
        if not spec["writes"]:
            return ("Bu araç mağazaya hiçbir şey yazmaz; çıktı okunur bir özettir "
                    "ve rapora alınabilir.")
        if not sent:
            return ("Kuru prova: model çağrılmadı, jeton harcanmadı. Gerçek öneri için "
                    "kuru provayı kapatın.")
        if run_id <= 0:
            return ("Mağaza çalışma numarası döndürmedi; uygulama ucu bu öneriyi "
                    "eşleştiremez. Fark tablosunu CSV alıp elle uygulayabilirsiniz.")
        return ("Seçtiğiniz satırlar uygulanır. Uygulama bu tabloyu okur ve öneriyi "
                "yeniden üretmez — onayladığınız metnin aynısı gider.")

    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    async def _before_map(self, tool: str, ids: list[int]) -> dict[int, dict[str, str]]:
        """Fark tablosunun "önce" sütunu. Okunamayan ürün ATLANIR ve o satır
        `beforeKnown: False` ile görünür — uydurma eski değer yazılmaz."""
        fields = [field for field, _ in analytics.TOOLS[tool]["writes"]]
        out: dict[int, dict[str, str]] = {}
        for target in ids:
            try:
                raw = await self._api.product(int(target))
            except Exception as failure:  # noqa: BLE001 — K7: biri okunamazsa gerisi sürsün
                self._log.info("önce değeri okunamadı", productId=target, error=str(failure))
                continue
            known: dict[str, str] = {}
            for field in fields:
                if rules.has_key(raw, field):
                    known[field] = rules.text(rules.attribute(raw, field))
            if known:
                out[int(target)] = known
        return out

    async def _save_run(self, *, token: str, run_id: int, tool: str, params: dict[str, Any],
                        rows: list[dict[str, Any]], status: str, actor: str, reason: str,
                        tokens: int, cost: int, targets: int, duration_ms: int,
                        error: str, summary_text: str = "") -> None:
        try:
            await self._store.execute(
                f"INSERT INTO {self._runs} (token, run_id, tool, params, rows, summary_text, "
                "status, actor, reason, tokens, cost, targets, applied, duration_ms, error, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (token, int(run_id), tool, json.dumps(params, ensure_ascii=False),
                 json.dumps(rows, ensure_ascii=False), summary_text, status, actor, reason,
                 int(tokens), int(cost), int(targets), int(duration_ms), error, _now()))
        except Exception as failure:  # noqa: BLE001 — defter yazılamazsa uygulama açılmaz
            self._log.warning("çalışma kaydedilemedi", token=token, error=str(failure))

    async def run_detail(self, token: str) -> dict[str, Any]:
        row = await self._run_row(token)
        if row is None:
            return {"ok": False, "error": "Çalışma bulunamadı."}
        rows = json.loads(row["rows"] or "[]")
        spec = analytics.TOOLS.get(str(row["tool"]), {})
        return {"ok": True, "error": "", "token": row["token"], "runId": row["run_id"],
                "tool": row["tool"], "toolLabel": spec.get("label") or row["tool"],
                "readOnly": not spec.get("writes"), "status": row["status"],
                # `text` OLMADAN yazmayan araçların geçmiş kaydı boş görünürdü:
                # fark tablosu zaten yok, okunacak tek şey bu özet.
                "text": rules.text(row.get("summary_text")),
                "rows": rows, "summary": analytics.diff_summary(rows),
                "tokens": row["tokens"], "cost": row["cost"], "reason": row["reason"],
                "actor": row["actor"], "createdAt": row["created_at"],
                "applicable": bool(spec.get("writes")) and rules.as_int(row["run_id"]) > 0
                and row["status"] == "preview"}

    async def _run_row(self, token: str) -> dict[str, Any] | None:
        try:
            row = await self._store.fetch_one(
                f"SELECT token, run_id, tool, params, rows, summary_text, status, actor, "
                f"reason, tokens, cost, targets, applied, created_at FROM {self._runs} "
                "WHERE token = ?",
                (rules.text(token),))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("çalışma okunamadı", error=str(failure))
            return None
        return dict(row) if row else None

    # ============================================================== uygulama

    async def apply(self, *, token: str, selections: list[str], reason: str, actor: str,
                    dry_run: bool = True) -> dict[str, Any]:
        """ONAYLANAN önerileri uygular. EKRANIN KİMLİĞİ BURADADIR.

        Üç şey burada garanti edilir:
          · Jeton yoksa uygulama yok — fark tablosu görülmeden yazılmaz.
          · Boş seçim "hepsi" DEĞİLDİR; hiçbir şey demektir.
          · Giden değer, tabloda duran değerdir. Öneri yeniden üretilmez;
            uygulanan şey kullanıcının okuduğu şeydir.
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        row = await self._run_row(token)
        if row is None:
            return {"ok": False,
                    "error": "Öneri bulunamadı. Fark tablosu görülmeden uygulama yapılmaz; "
                             "aracı yeniden çalıştırın."}

        spec = analytics.TOOLS.get(str(row["tool"]))
        if spec is None:
            return {"ok": False, "error": "Bu ekran bu aracı tanımıyor; uygulanamaz."}
        if not spec["writes"]:
            return {"ok": False,
                    "error": "Bu araç mağazaya yazmaz; uygulanacak bir şey yok."}
        if rules.as_int(row["run_id"]) <= 0:
            return {"ok": False,
                    "error": "Mağaza çalışma numarası yok; uygulama ucu bu öneriyi "
                             "eşleştiremez. Fark tablosunu CSV alıp elle uygulayın."}

        rows = json.loads(row["rows"] or "[]")
        chosen = analytics.selected_rows(rows, selections)
        if not chosen:
            return {"ok": False,
                    "error": "Onaylanan satır yok. Uygulanacak önerileri tek tek ya da "
                             "toplu seçmeniz gerekir — boş seçim 'hepsi' demek değildir."}

        payload = analytics.apply_selection_payload(chosen)
        await self._record(target_id=0, action=f"apply:{row['tool']}", reason=reason,
                           actor=actor, result="denendi",
                           detail={"token": row["token"], "rows": len(payload)})
        try:
            result = await self._api.bbd_ai_apply(int(row["run_id"]), selections=payload,
                                                  reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            note = ("Mağazadaki AI uygulama ucu henüz yayında değil; uç hazır olunca "
                    "bu düğme açılacak." if self._missing_endpoint(failure)
                    else self._fail(failure))
            await self._record(target_id=0, action=f"apply:{row['tool']}", reason=reason,
                               actor=actor, result="hata", detail={"error": note})
            return {"ok": False, "error": note}

        sent = bool(result.get("sent", not dry_run))
        applied_ids = [item["id"] for item in chosen] if sent else []
        updated = analytics.mark_applied(rows, applied_ids)
        await self._finish_run(row["token"], rows=updated, applied=len(applied_ids),
                               actor=actor, reason=reason, sent=sent)
        await self._record(target_id=0, action=f"apply:{row['tool']}", reason=reason,
                           actor=actor, result="ok" if sent else "dry_run",
                           detail={"token": row["token"], "applied": len(applied_ids)})

        if sent and applied_ids:
            await self._emit("store_ai.suggestions_applied", {
                "tool": row["tool"], "runId": rules.as_int(row["run_id"]),
                "applied": len(applied_ids), "actor": actor})

        return {"ok": True, "error": "", "applied": len(applied_ids), "requested": len(chosen),
                "dryRun": not sent, "rows": updated,
                "summary": analytics.diff_summary(updated),
                "notice": "Vitrine yansıması birkaç dakika sürebilir (arama dizini yenilenir)."}

    async def _finish_run(self, token: str, *, rows: list[dict[str, Any]], applied: int,
                          actor: str, reason: str, sent: bool) -> None:
        pending = [row for row in rows
                   if row.get("state") != analytics.APPLIED and not row.get("skipped")
                   and row.get("changed")]
        status = "preview" if pending or not sent else "applied"
        try:
            await self._store.execute(
                f"UPDATE {self._runs} SET rows = ?, status = ?, applied = applied + ?, "
                "actor = ?, reason = ?, applied_at = ? WHERE token = ?",
                (json.dumps(rows, ensure_ascii=False), status, int(applied), actor, reason,
                 _now(), token))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("çalışma güncellenemedi", token=token, error=str(failure))

    async def _emit(self, name: str, payload: dict[str, Any]) -> None:
        """Olay veri yolu henüz her kurulumda çalışmıyor olabilir; yayın
        başarısızlığı uygulanan işi geri almaz ve ekranı düşürmez (K7)."""
        if self._publish is None:
            return
        try:
            await self._publish(name, payload)
        except Exception as failure:  # noqa: BLE001 — olay yayını iş kritik değil
            self._log.info("olay yayınlanamadı", event=name, error=str(failure))

    # ============================================================== geçmiş

    async def history(self, *, tool: str = "", status: str = "", page: int = 1,
                      size: int = 0) -> dict[str, Any]:
        """Çalışma geçmişi: yerel defter + (varsa) mağaza geçmişi.

        Yerel defter GEREKÇEYİ taşıdığı için önceliklidir. Mağazada olup
        yerelde olmayan çalışmalar `local: false` ile eklenir; gizlenirse
        "bu ay neden bu kadar harcanmış" sorusu cevapsız kalır.
        """
        per_page = size or self._page_size
        ledger = await self._ledger(limit=1000)
        local = [{
            "token": row["token"], "runId": row["run_id"], "tool": row["tool"],
            "toolLabel": analytics.TOOLS.get(str(row["tool"]), {}).get("label")
            or str(row["tool"]),
            "actor": row["actor"], "reason": row["reason"], "status": row["status"],
            "tokens": row["tokens"], "cost": row["cost"], "targets": row["targets"],
            "applied": row["applied"], "durationMs": row["duration_ms"],
            "error": row["error"], "createdAt": str(row["created_at"])[:19],
        } for row in ledger]

        connected, error = True, ""
        remote: Any = []
        try:
            payload = await self._api.bbd_ai_runs({}, page=1, per_page=per_page)
            remote = payload.get("items") or []
        except Exception as failure:  # noqa: BLE001 — K7
            connected = False
            error = self._fail(failure)

        merged = analytics.merge_runs(local, remote)
        if tool:
            merged = [row for row in merged if row["tool"] == tool]
        if status:
            merged = [row for row in merged if row["status"] == status]

        start = max(0, (max(1, int(page)) - 1) * per_page)
        window = merged[start:start + per_page]
        return {"ok": True, "connected": connected, "error": error, "items": window,
                "total": len(merged), "page": max(1, int(page)), "size": per_page,
                "pages": max(1, -(-len(merged) // per_page))}

    # ============================================================== ayarlar

    async def settings(self) -> dict[str, Any]:
        """Ekran tercihleri + hangi değerin nereden geldiği.

        Her alan `source` taşır: `varsayılan` (config) ya da `ekran` (prefs).
        Kullanıcı neyi değiştirdiğini görmeden "varsayılana dön" diyemez.
        """
        out: dict[str, Any] = {"ok": True, "error": "", "values": {}, "sources": {}}
        for key in PREF_KEYS:
            stored = await self._pref(key)
            out["sources"][key] = "ekran" if stored != "" else "varsayılan"
            raw = stored if stored != "" else self._config.get(key)
            out["values"][key] = raw if raw is not None else ""
        out["values"]["monthly_budget"] = rules.as_int(out["values"].get("monthly_budget"), 0)
        out["values"]["include_passive"] = self._truthy(out["values"].get("include_passive"))
        out["values"]["budget_estimate_when_offline"] = self._truthy(
            out["values"].get("budget_estimate_when_offline"))
        for key in RULE_OPTION_KEYS:
            out["values"][key] = rules.as_int(out["values"].get(key), 0)
        out["values"]["disabled_tools"] = [item for item in
                                           str(out["values"].get("disabled_tools") or "").split(",")
                                           if item]
        out["budget"] = await self.budget_state()
        out["tools"] = analytics.tool_labels()
        # SUSTURULMUŞ BULGULAR AYARLARIN PARÇASIDIR, taramanın değil. Ayarlar
        # sekmesindeki "geri al" düğmesi bu listeyi okuyor; liste yalnız
        # `catalog()` yanıtında dursaydı iki şey birden bozulurdu:
        #   · Katalog Sağlığı sekmesi hiç açılmadıysa liste boş görünür ve
        #     susturulmuş bulgular geri alınamazdı;
        #   · geri alma sonrası ekran yalnız ayarları tazelediği için satır
        #     listede KALIRDI — düğme çalışmıyor sanılırdı.
        # Okuma yerel tablodandır, tarama gerektirmez (29 istek etmez).
        ignored = await self._ignored_keys()
        out["muted"] = [{"key": key, **value} for key, value in ignored.items()]
        return out

    async def save_settings(self, *, values: dict[str, Any], reason: str,
                            actor: str) -> dict[str, Any]:
        """Ayarları yazar. Tanınmayan anahtar SESSİZCE düşmez, `skipped` döner."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        changed: list[str] = []
        skipped: list[str] = []
        for key, value in (values or {}).items():
            if key not in PREF_KEYS:
                skipped.append(str(key))
                continue
            if key == "disabled_tools":
                wanted = value if isinstance(value, list) else str(value).split(",")
                stored = ",".join(sorted({rules.text(item) for item in wanted
                                          if analytics.known_tool(rules.text(item))}))
            elif key == "budget_behavior":
                stored = "warn" if rules.text(value) == "warn" else "block"
            elif key in ("include_passive", "budget_estimate_when_offline"):
                stored = "1" if bool(value) else "0"
            else:
                stored = str(max(0, rules.as_int(value, 0)))
            try:
                await self._set_pref(key, stored, actor)
            except Exception as failure:  # noqa: BLE001 — K7
                return {"ok": False, "error": self._fail(failure), "changed": changed}
            changed.append(key)

        # Eşikler değiştiyse önceki tarama artık YANLIŞ bulgu gösterir.
        self._scan = None
        self._scan_at = 0.0

        await self._record(target_id=0, action="save_settings", reason=reason, actor=actor,
                           result="ok", detail={"changed": changed, "skipped": skipped})
        return {"ok": True, "error": "", "changed": changed, "skipped": skipped,
                "budget": await self.budget_state()}

    # ================================================================ rapor

    async def export_csv(self) -> dict[str, Any]:
        """Katalog sağlığı bulgularının tamamı — rapor klasörüne CSV."""
        catalog = await self.catalog()
        rows = catalog["findings"]
        if not rows:
            return {"ok": False, "error": "Dışa aktarılacak bulgu yok."}
        headers = ["Bulgu", "Önem", "Hedef", "Numara", "SKU", "Ad", "Ayrıntı"]
        table = [[row["kindLabel"], rules.SEVERITY_LABELS.get(row["severity"], row["severity"]),
                  "Kategori" if row["targetType"] == "category" else "Ürün",
                  row["targetId"], row["sku"], row["name"], row["detail"]] for row in rows]
        name = f"magaza-katalog-sagligi-{_stamp()}.csv"
        try:
            # `csv_bytes` de `ExportError` atabilir (biçimlenemeyen hücre);
            # yalnız OSError yakalamak o hatayı 500'e çevirirdi — servis HTTP
            # hatası fırlatmaz (K7).
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name, "rows": len(table)}

    async def preview(self, kind: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        produced = await self.build_report(kind, params or {})
        if not produced.get("ok"):
            return produced
        try:
            pages = await self._render_pages(Path(produced["path"]))
        except PreviewError as failure:
            return {**produced, "pages": [], "previewError": str(failure)}
        return {**produced, "pages": pages, "previewError": ""}

    async def build_report(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if kind not in ("health", "usage"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}

        if kind == "health":
            catalog = await self.catalog()
            if not catalog["findings"] and not catalog["connected"]:
                return {"ok": False, "error": catalog["error"] or "Katalog okunamadı."}
            content = self._health_pdf(catalog)
            name = f"magaza-katalog-sagligi-{_stamp()}.pdf"
            rows = len(catalog["findings"])
        else:
            usage = await self.usage(days=rules.as_int(params.get("days"), 30))
            content = self._usage_pdf(usage)
            name = f"magaza-ai-maliyet-{_stamp()}.pdf"
            rows = len(usage["byTool"])

        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("AI raporu üretildi", kind=kind, rows=rows, path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": rows}

    def _health_pdf(self, catalog: dict[str, Any]) -> bytes:
        summary = catalog["summary"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Taranan ürün", number(summary["scanned"])),
                      ("Bulgu", number(summary["total"])),
                      ("Etkilenen kayıt", number(summary["affected"])),
                      ("Ağır bulgu", number(summary["bySeverity"]["high"]))],
        }, {
            "kind": "table", "title": "Bulgu türleri",
            "headers": ["Bulgu", "Adet"], "align": "LR", "widths": [3, 1],
            "rows": [[item["label"], number(item["count"])] for item in summary["byKind"]],
        }]

        for severity, title in (("high", "Ağır bulgular"), ("medium", "Orta bulgular"),
                                ("low", "Hafif bulgular")):
            subset = [row for row in catalog["findings"] if row["severity"] == severity]
            if not subset:
                continue
            sections.append({
                "kind": "table", "title": f"{title} ({len(subset)})",
                "headers": ["Bulgu", "SKU", "Kayıt", "Ayrıntı"],
                "align": "LLLL", "widths": [1.2, 1, 2, 3],
                "rows": [[row["kindLabel"], row["sku"] or "—", row["name"], row["detail"]]
                         for row in subset[:300]],
            })

        for warning in catalog.get("warnings") or []:
            sections.append({"kind": "note", "text": warning})
        sections.append({"kind": "note",
                         "text": "Bu rapor KURAL TABANLIDIR: yapay zekâ kullanılmamıştır. "
                                 f"Tarama zamanı: {catalog.get('scannedAt', '')}."})
        return build_pdf(title="Katalog sağlığı",
                         subtitle=f"{summary['scanned']} ürün tarandı · "
                                  f"{summary['total']} bulgu",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    def _usage_pdf(self, usage: dict[str, Any]) -> bytes:
        budget = usage["budget"]
        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": f"{usage['month']} dönemi",
            "tiles": [("Harcanan", money(usage["spent"])),
                      ("Aylık bütçe", money(budget["budget"]) if budget["budget"] else "sınırsız"),
                      ("Kalan", money(budget["remaining"]) if budget["budget"] else "—"),
                      ("Jeton", number(usage["tokens"]))],
        }, {
            "kind": "table", "title": "Araç kırılımı",
            "headers": ["Araç", "Çalışma", "Maliyet"], "align": "LRR", "widths": [3, 1, 1.2],
            "rows": [[row["label"], number(row["runs"]), money(row["cost"])]
                     for row in usage["byTool"]],
        }]
        if not usage["measured"]:
            sections.append({"kind": "note",
                             "text": "Mağaza sayacı okunamadı; rakamlar Kontrol Merkezi'nin "
                                     "yerel defterinden TAHMİN edilmiştir ve başka araçlardan "
                                     "yapılan çalıştırmaları içermez."})
        sections.append({"kind": "note", "text": budget["message"]})
        return build_pdf(title="AI kullanım ve maliyet",
                         subtitle=f"{usage['month']} · {money(usage['spent'])}",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-ai-onizleme-") as folder:
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
        """GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir.
        Serbest yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı
        kâğıda döktürmeye açık kapı bırakırdı."""
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
