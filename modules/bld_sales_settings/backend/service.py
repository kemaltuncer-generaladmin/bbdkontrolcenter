"""Satış Ayarları — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Satış ayarları TastyIgniter'ın kendi
`location_options` tablosunda, kapalı günler `veykemtu_closed_days` içinde
yaşıyor ve buraya `bld.api` geçidinden geliyor (K4); bu modül ham httpx
kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel tablolar yalnız BLD'de
KARŞILIĞI OLMAYAN üç şeyi saklar: yazma denemesinin izi, formun açıldığı
andaki TABAN ÇİZGİSİ ve bu ekrana özel tercihler.

Neden kopya tutulmuyor: gövdede `ordering_enabled`, `paused_until` ve `busy`
gibi CANLI şalterler var. "Satışı durdurdum ama panel hâlâ açık gösteriyor"
cümlesi, önbelleğin en pahalı hâlidir; bayat bir satış şalteri kazandığı
istekten çok daha pahalıya patlar.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` okumanın başarısını değil UCUN SAĞLIĞINI anlatır; ayrımı
taşıyan alan `connected`'dır ve ekranın onu OKUMASI gerekir.

═══════════════════════════════════════════════════════════════════════════
KURU PROVA BU EKRANIN EN TEHLİKELİ HATASI — VE BU YÜZDEN ÜÇ KAPI VAR
═══════════════════════════════════════════════════════════════════════════

Sessizce no-op olan bir ayar yazması, başarıdan AYIRT EDİLEMEZ: ekran
"kaydedildi" der, denetim izine satır düşer, `location_options` tablosunda
hiçbir şey değişmez ve yönetici bunu ancak ertesi sabah kesim saatinin eski
değerde olduğunu görünce anlar.

  1. BAYRAK ÜÇ DEĞERLİ DEĞİL, İKİ DEĞERLİ. Gövdedeki alan `preview: bool` ve
     varsayılanı `False`. `bld_kds`'teki `dryRun: bool|None` + modül ayarı
     kalıbı burada BİLEREK KULLANILMADI: `None` dalı, ayarın (ya da git dışı
     `config/local.yaml`'ın) yanlış olduğu bir kurulumda her yazmayı sessizce
     provaya çevirirdi. Üçüncü hâl yoksa yanlış yapılandırılacak bir şey de
     yoktur.
  2. GEÇİDE HER ÇAĞRIDA AÇIK `dry_run=` GEÇİLİR. Geçidin kendi varsayılanına
     asla güvenilmez (`bld_api/README.md`): `config/local.yaml` git dışıdır ve
     bugün orada `true` yazıyor. Bayrağı atlayan bir modül, hiçbir şey
     yazmadan `{"ok": true}` alır.
  3. YANIT DOĞRULANIR. `_verify()` yanıttaki `dry_run` bayrağını istenenle
     karşılaştırır; uyuşmuyorsa işlem BAŞARISIZ sayılır ve neden yazılır.
     Üçüncü kapı ilk ikisinin yanıldığı durumu yakalar — yazmayı bizden
     bağımsız bir yerin (geçit ayarı, sunucu kabuğu) provaya çevirmesi.

ÖNİZLEME AYRI VE AÇIK BİR EYLEMDİR. Sözleşme `PUT /sales` için `would`
bloğunda eski ve yeni değeri YAN YANA veriyor, stok ucunda ise uyarıları
gerçekten hesaplıyor; ikisi de yöneticinin yazmadan önce görmesi gereken
şeyler. Tehlikeli olan kuru provanın kendisi değil, ÖRTÜK olmasıdır.

═══════════════════════════════════════════════════════════════════════════

YAZMA ZİNCİRİ — her yazma ucu bu altı adımı bu sırayla uygular:

    1. gerekçe ve gövde denetimi (arayüzde göstermek yetmez, K9)
    2. TAZE OKUMA (ayar aradan değişmiş olabilir)
    3. TABAN ÇİZGİSİ KARŞILAŞTIRMASI (yazılan alanlar için)
    4. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    5. geçit çağrısı — AÇIK `dry_run=`
    6. yanıt doğrulaması + yerel iz: `ok` / `onizleme` / `hata`

Üçüncü adım bu ekrana özeldir. `bld_busy` anahtarını MUTFAK EKRANI da
değiştiriyor: yönetici formu 09:00'da açar, mutfak 09:10'da yoğunluğu açar,
yönetici 09:30'da kaydeder ve yarım saat önceki hâli geri yazar. Sunucudaki
`SettingsRepository` bunu taban çizgisi karşılaştırmasıyla çözüyor; panel de
aynı disiplini korur — ama YALNIZ YAZILAN ALANLAR için. Dokunulmamış bir
anahtar değişti diye kesim saati kaydını reddetmek, ekranı kullanılamaz
yapardı.

Kuru provada (önizlemede) OLAY YAYINLANMAZ: BLD'de hiçbir şey değişmedi,
dinleyicileri "satış durduruldu" diye uyandırmak yalan olurdu.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from . import settings as rules

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
PREVIEWED = "onizleme"
BLOCKED = "engellendi"
FAILED = "hata"

#: Satış durdurulduğunda / açıldığında yayınlanan olaylar (manifest).
#: ÖNİZLEMEDE YAYINLANMAZ.
PAUSED_EVENT = "bld_sales_settings.ordering_paused"
RESUMED_EVENT = "bld_sales_settings.ordering_resumed"

#: Olay yükleri camelCase'tir: olay yolu Kontrol Merkezi'nin KENDİ iç
#: yüzeyidir. HTTP yanıtları ise sözleşmenin snake_case sözlüğünü korur.

#: Taban çizgisinin varsayılan ömrü (dakika).
BASELINE_TTL_MINUTES = 30


class SalesSettingsService:
    """Satış Ayarları ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

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

        self._audit = store.table("audit")
        self._baseline = store.table("baseline")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _location_id(self) -> int | None:
        """Vitrin kimliği. `0` = varsayılan vitrin, geçide hiç gönderilmez."""
        value = rules.as_int(self._config.get("location_id"), 0)
        return value if value > 0 else None

    @property
    def _baseline_ttl(self) -> int:
        return max(1, rules.as_int(self._config.get("baseline_ttl_minutes"),
                                   BASELINE_TTL_MINUTES))

    @property
    def _stock_days(self) -> int:
        """Hızlı stok şeridinde kaç gün. Varsayılan 2 (bugün + yarın)."""
        return max(1, min(7, rules.as_int(self._config.get("stock_days"), 2)))

    @property
    def _closed_day_days_ahead(self) -> int:
        return max(1, min(730, rules.as_int(self._config.get("closed_day_days_ahead"), 365)))

    @property
    def _poll_seconds(self) -> int:
        """Panelin canlı tazeleme aralığı.

        Ayar SUNUCUDAN gelir, panele gömülmez: aralığı değiştirmek istediğinde
        kurulumun paneli yeniden derlemesi gerekmesin ve iki farklı gerçek
        (ayar dosyasında bir sayı, ekranda başka bir sayı) oluşmasın.
        """
        return max(15, min(900, rules.as_int(self._config.get("poll_seconds"), 60)))

    # --------------------------------------------------------- yardımcılar

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçidin hata kodu (`control_endpoint_missing`, `not_found`, …).

        `not_found` ile `control_endpoint_missing` AYRI şeylerdir: ilki "uç
        var, kayıt yok", ikincisi "uç sunucuya henüz dağıtılmamış, bekle".
        Ekran ikisine aynı cümleyi yazsaydı, dağıtılmamış bir ucu yönetici
        "kayıt yok" diye okur ve boşuna beklerdi.
        """
        return str(getattr(failure, "code", "") or "")

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_type: str = "settings", target_id: str = "",
                      detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor
        (`00-genel.md` §8); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN istekleri bilir. Ağ
        koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim neyi denedi"
        sorusunun cevabı yalnız burada kalır. Satış şalteri için bu ayrım
        özellikle pahalı: satış açık mı kapalı mı belirsizken, en azından
        kimin ne denediği bilinmelidir.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, rules.text(target_id), action, rules.text(reason), actor,
                 result, json.dumps(detail or {}, ensure_ascii=False), rules.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: satış BLD'de durdurulmuştur,
        dinleyicinin patlaması onu geri açmaz (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    def _verify(self, result: Any, *, preview: bool) -> str:
        """Yanıtın GERÇEKTEN yazdığını doğrular. Boş dize = sorun yok.

        ÜÇÜNCÜ KAPI (dosya başlığı). İlk iki kapı — iki değerli bayrak ve
        geçide açık `dry_run=` — bizim tarafımızdadır. Bu kapı, yazmayı BİZDEN
        BAĞIMSIZ bir yerin provaya çevirmesini yakalar: geçidin `read_only`
        freni, `config/local.yaml` içindeki `dry_run_default`, ya da sunucunun
        isteği tanımadığı bir yol.

        Sessiz kalsaydı ekran "kaydedildi" derdi ve yönetici ertesi sabah
        kesim saatinin eski değerde olduğunu görene kadar hiçbir şey
        öğrenmezdi.
        """
        if not isinstance(result, dict):
            return "Sunucudan beklenen biçimde yanıt gelmedi."
        if result.get("ok") is False:
            raw = result.get("error")
            if isinstance(raw, dict):
                return rules.text(raw.get("message")) or "İşlem sunucuda başarısız oldu."
            return rules.text(raw) or "İşlem sunucuda başarısız oldu."
        # Geçit, sözleşmede kayıtlı OLMAYAN bir yola kuru prova ile yazılmak
        # istendiğinde isteği hiç göndermiyor ve bunu `sent: False` ile
        # söylüyor. Önizleme sanılan bir istek aslında hiç sorulmamış olur.
        if preview and result.get("sent") is False:
            return ("Önizleme sunucuya HİÇ GÖNDERİLMEDİ: bu yol geçidin kuru prova "
                    "defterinde kayıtlı değil. Ekrandaki değerler sunucudan gelmiyor.")
        said_dry = bool(result.get("dry_run"))
        if not preview and said_dry:
            return ("Sunucu bu isteği KURU PROVA olarak işledi; HİÇBİR AYAR YAZILMADI. "
                    "Geçidin kuru prova varsayılanı ya da acil freni açık olabilir "
                    "(bld_api ayarı). Ayarı düzeltmeden yeniden denemeyin — ekran "
                    "'kaydedildi' derken sunucuda hiçbir şey değişmez.")
        if preview and not said_dry:
            return ("Önizleme istendi ama sunucu GERÇEK YAZMA yaptı. Ayarların şu anki "
                    "hâlini ekrandan tazeleyin ve gerekiyorsa geri alın.")
        return ""

    @staticmethod
    def _changed(result: dict[str, Any]) -> list[str]:
        """Sunucunun "gerçekten değişti" dediği alanlar (`PUT /sales`)."""
        raw = result.get("changed")
        return [rules.text(item) for item in raw if rules.text(item)] if isinstance(raw, list) else []

    # -------------------------------------------------------- taban çizgisi

    async def _save_baseline(self, snapshot: dict[str, Any]) -> str:
        """Formun açıldığı andaki ayar görüntüsünü saklar ve jetonunu verir.

        Bu UZAK VERİNİN KOPYASI DEĞİLDİR ve ekrana hiç çizilmez: yalnız
        "yönetici neyin üstüne yazdığını sanıyordu" sorusunun cevabıdır.
        Jetonsuz bir yazma da kabul edilir (eski bir istemci, elle kurulmuş
        bir çağrı); o zaman yarış denetimi yapılamaz ve yanıt bunu söyler.
        """
        token = secrets.token_hex(16)
        try:
            await self._store.execute(
                f"INSERT INTO {self._baseline} "
                "(token, location_id, snapshot, created_at) VALUES (?, ?, ?, ?)",
                (token, rules.as_int(self._location_id, 0),
                 json.dumps(snapshot, ensure_ascii=False), rules.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — taban yazılamadı, okuma durmasın
            self._log.warning("taban çizgisi yazılamadı", error=str(failure))
            return ""
        return token

    async def _load_baseline(self, token: str) -> dict[str, Any] | None:
        if not rules.text(token):
            return None
        try:
            row = await self._store.fetch_one(
                f"SELECT token, snapshot, created_at FROM {self._baseline} WHERE token = ?",
                (rules.text(token),),
            )
        except Exception as failure:  # noqa: BLE001 — taban okunamadı (K7)
            self._log.warning("taban çizgisi okunamadı", error=str(failure))
            return None
        if not row:
            return None
        try:
            snapshot = json.loads(row.get("snapshot") or "{}")
        except (TypeError, ValueError):
            return None
        return snapshot if isinstance(snapshot, dict) else None

    # =================================================================== okuma

    async def sales(self, *, baseline: bool = True) -> dict[str, Any]:
        """Satış ayarlarının tamamı + panelin ipucu metinleri.

        `meta.defaults` SUNUCUDAN gelir ve ekrana gri ipucu olarak yazılır.
        İstemcinin kendi varsayılanını gömmesi, sunucu varsayılanı
        değiştiğinde iki farklı gerçek üretirdi.

        `baseline=False` YOKLAMA OKUMASIDIR: panel arka planda dakikada bir
        bakıyor ("mutfak yoğunluğu açtı mı") ve her bakışta yeni bir taban
        çizgisi üretmek iki şeyi birden bozardı. Birincisi tablo şişerdi
        (ekran açık kaldıkça günde binden çok satır). İkincisi ve asıl önemlisi:
        yeni jeton, YÖNETİCİNİN GÖRDÜĞÜ hâli değil sunucunun EN SON hâlini
        işaret eder — yarım saattir açık duran formun üstüne yazma, aradaki
        değişiklik hiç olmamış gibi kabul edilirdi. Yarış denetimi tam da bunu
        engellemek için var.
        """
        try:
            payload = await self._api.sales_settings(location_id=self._location_id)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "data": {}, "meta": {},
                    "baseline_token": ""}

        data = payload if isinstance(payload, dict) else {}
        # `settings_reference()` aynı ucun `meta` bloğunu ÖNBELLEKLİ verir;
        # canlı şalterler `data`dan, değişmeyen ipuçları oradan okunur.
        meta: dict[str, Any] = {}
        try:
            reference = await self._api.settings_reference(location_id=self._location_id)
            if isinstance(reference, dict):
                meta = reference
        except Exception as failure:  # noqa: BLE001 — ipuçları olmadan da çalışır (K7)
            self._log.warning("ayar referansı okunamadı", error=str(failure))

        # Taban çizgisi YALNIZ yazılabilir 12 alanı taşır: yarış denetimi
        # ancak yazılan bir alan için anlamlıdır. Panelin izlediği "başka biri
        # satışı durdurdu mu" sorusu ayrı bir iştir ve `data`dan okunur.
        token = ""
        if baseline:
            token = await self._save_baseline(
                {field: data.get(field) for field in rules.WRITABLE_FIELDS})

        return {
            "ok": True,
            "connected": True,
            "data": data,
            "meta": meta,
            "baseline_token": token,
            "baseline_ttl_minutes": self._baseline_ttl,
            "poll_seconds": self._poll_seconds,
            "stock_days": self._stock_days,
            "writable_fields": list(rules.WRITABLE_FIELDS),
            "read_only_fields": list(rules.READ_ONLY_FIELDS),
            "payment_methods": list(rules.PAYMENT_METHODS),
            "limits": {
                "max_lookahead_days": rules.MAX_LOOKAHEAD_DAYS,
                "minute_min": rules.MINUTE_MIN,
                "minute_max": rules.MINUTE_MAX,
                "busy_message_max": rules.BUSY_MESSAGE_MAX,
                "customer_message_max": rules.CUSTOMER_MESSAGE_MAX,
                "closed_day_description_max": rules.CLOSED_DAY_DESCRIPTION_MAX,
                "pause_max_days": rules.PAUSE_MAX_DAYS,
                "reason_min": rules.MIN_REASON,
                "reason_max": rules.MAX_REASON,
            },
        }

    async def closed_days(self, *, date_from: str = "", date_to: str = "") -> dict[str, Any]:
        """Kapalı gün listesi. **Global** — vitrin süzgeci yoktur."""
        try:
            payload = await self._api.closed_days(date_from=rules.iso_date(date_from),
                                                  date_to=rules.iso_date(date_to))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": [], "meta": {}}
        items = payload.get("items") if isinstance(payload, dict) else None
        meta = payload.get("meta") if isinstance(payload, dict) else None
        return {
            "ok": True, "connected": True,
            "items": items if isinstance(items, list) else [],
            "meta": meta if isinstance(meta, dict) else {},
            "days_ahead": self._closed_day_days_ahead,
        }

    async def stock(self, *, dates: list[str] | None = None) -> dict[str, Any]:
        """Hızlı stok şeridi: bugünün ve yarının kalemleri.

        Her gün AYRI istek eder ve bir günün patlaması ötekini düşürmez: yarın
        için menü henüz kurulmamış olabilir (`not_found`) ve bu bir hata değil,
        olağan bir durumdur. İkisini tek istekte toplayan bir uç yok; olsaydı
        da bir günün eksikliği hepsini boş gösterirdi.
        """
        wanted = [rules.iso_date(value) for value in (dates or [])]
        wanted = [value for value in wanted if value]
        if not wanted:
            wanted = [rules.today_iso(offset) for offset in range(self._stock_days)]

        days: list[dict[str, Any]] = []
        for date in wanted:
            try:
                payload = await self._api.menu_stock(date, location_id=self._location_id)
            except Exception as failure:  # noqa: BLE001 — K7, gün gün
                days.append({"date": date, "connected": False,
                             "error": self._fail(failure), "code": self._code(failure),
                             "day": {}, "items": [], "blocking": {}})
                continue
            summary = rules.stock_summary(payload if isinstance(payload, dict) else {})
            summary["date"] = summary["date"] or date
            summary["connected"] = True
            summary["error"] = ""
            summary["code"] = ""
            days.append(summary)

        connected = any(day.get("connected") for day in days)
        return {"ok": True, "connected": connected, "days": days,
                "error": "" if connected else "Stok bilgisi okunamadı."}

    async def audit(self, *, limit: int = 100) -> dict[str, Any]:
        """Bu ekrandan yapılan yazma DENEMELERİNİN yerel izi.

        Sunucunun izi ayrı bir sorudur ve `control/audit` alanının ekranından
        okunur; buradaki satırlar sunucuya ULAŞMAYAN denemeleri de taşır.
        """
        rows: list[dict[str, Any]] = []
        try:
            rows = await self._store.fetch_all(
                f"SELECT target_type, target_id, action, reason, actor, result, detail, "
                f"created_at FROM {self._audit} ORDER BY id DESC LIMIT ?",
                (max(1, min(500, int(limit or 100))),),
            )
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran ayakta (K7)
            self._log.warning("denetim izi okunamadı", error=str(failure))
            return {"ok": True, "items": [], "error": "Yerel denetim izi okunamadı."}

        items: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            try:
                entry["detail"] = json.loads(entry.get("detail") or "{}")
            except (TypeError, ValueError):
                entry["detail"] = {}
            items.append(entry)
        return {"ok": True, "items": items, "error": ""}

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler BLD'yi ETKİLEMEZ; yalnız bu ekranın ne gösterdiğini belirler.
        """
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {rules.text(row.get("key")): rules.text(row.get("value")) for row in rows}
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))
        return {
            "stock_days": rules.as_int(stored.get("stock_days"), self._stock_days),
            "tab": stored.get("tab") or "settings",
        }

    # ================================================================== yazma

    async def _fresh_settings(self) -> tuple[dict[str, Any], str]:
        """Ayarların TAZE hâli. `(veri, hata)` döner.

        Yazmadan hemen önce okunur: yönetici formu açtıktan sonra mutfak
        yoğunluğu açmış, başka bir yönetici kesim saatini değiştirmiş olabilir.
        """
        try:
            payload = await self._api.sales_settings(location_id=self._location_id)
        except Exception as failure:  # noqa: BLE001 — K7
            return {}, self._fail(failure)
        return (payload if isinstance(payload, dict) else {}), ""

    async def update_sales(self, *, settings: dict[str, Any], reason: str, actor: str,
                           preview: bool = False, token: str = "") -> dict[str, Any]:
        """Satış ayarlarını KISMİ yazar.

        Gönderilmeyen alan değişmez. `ordering_enabled`, `is_open` ve
        `daily_package_menu_id` buradan yazılamaz; ilki kendi uçlarından,
        ötekiler hiç.
        """
        problem = rules.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        body, errors = rules.clean_patch(settings or {})
        if errors:
            return {"ok": False, "error": errors[0]["message"], "errors": errors}

        fresh, link_error = await self._fresh_settings()
        if link_error:
            return {"ok": False, "connected": False, "error": link_error}

        baseline = await self._load_baseline(token)
        if baseline is not None:
            clash = rules.conflicts(baseline, fresh, list(body))
            if clash:
                await self._record(action="settings.sales", reason=reason, actor=actor,
                                   result=BLOCKED, detail={"conflicts": clash})
                names = ", ".join(row["label"] for row in clash)
                return {
                    "ok": False, "conflict": True, "conflicts": clash,
                    "data": fresh,
                    # Mutfak ekranı `busy` anahtarını değiştirebiliyor; yarım
                    # saat önceki hâli geri yazmak, mutfağın kararını sessizce
                    # iptal etmek olurdu.
                    "error": (f"Bu alanlar siz formu açtıktan sonra sunucuda değişti: "
                              f"{names}. Yazma yapılmadı. Ekranı tazeleyip yeni değerin "
                              "üstüne karar verin."),
                }

        planned = rules.diff(fresh, body, tuple(body))
        if not planned:
            # Aynı değeri yeniden yazmak sunucuda da "değişiklik yok"a düşer.
            # İsteği hiç göndermemek dürüst olanıdır: "kaydedildi" demek,
            # yöneticiye olmayan bir değişikliği bildirmek olurdu.
            await self._record(action="settings.sales", reason=reason, actor=actor,
                               result=DONE, detail={"changes": []})
            return {"ok": True, "preview": preview, "changed": [], "changes": [],
                    "data": fresh,
                    "note": "Gönderilen değerler sunucudakiyle aynı; yazma yapılmadı."}

        await self._record(action="settings.sales", reason=reason, actor=actor,
                           result=TRIED, detail={"changes": planned, "preview": preview})

        try:
            # AÇIK `dry_run=` — geçidin varsayılanına ASLA güvenilmez.
            result = await self._api.update_sales_settings(
                **body, location_id=self._location_id, reason=reason, actor=actor,
                dry_run=bool(preview))
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="settings.sales", reason=reason, actor=actor,
                               result=FAILED, detail={"error": message})
            return {"ok": False, "error": message, "code": self._code(failure)}

        broken = self._verify(result, preview=preview)
        if broken:
            await self._record(action="settings.sales", reason=reason, actor=actor,
                               result=FAILED, detail={"error": broken, "response": result})
            return {"ok": False, "error": broken}

        changed = self._changed(result)
        await self._record(action="settings.sales", reason=reason, actor=actor,
                           result=PREVIEWED if preview else DONE,
                           detail={"changes": planned, "changed": changed,
                                   "audit_id": result.get("audit_id")})

        response = {
            "ok": True, "preview": preview, "changes": planned, "changed": changed,
            "audit_id": result.get("audit_id"),
            "data": result.get("data") if isinstance(result.get("data"), dict) else {},
            "would": result.get("would") if isinstance(result.get("would"), dict) else {},
        }
        if not preview and not changed:
            # Sunucu "hiçbir alan değişmedi" diyor ama biz gerçek bir fark
            # göndermiştik. Sessiz geçmek, tam da bu ekranın en pahalı
            # hatasını görünmez yapardı.
            response["warning"] = (
                "Sunucu hiçbir alanın değişmediğini bildirdi; oysa gönderilen değerler "
                "okunan hâlden farklıydı. Ayarları tazeleyip doğrulayın.")
        return response

    async def pause(self, *, until: str | None, customer_message: str | None,
                    reason: str, actor: str, preview: bool = False) -> dict[str, Any]:
        """Satışı DURDURUR.

        `busy` ile karıştırılmamalı: `busy` yalnız uyarır, bu satışı gerçekten
        keser. `reason` MÜŞTERİYE GÖSTERİLMEZ — o denetim izi içindir;
        müşteriye gösterilen metin `customer_message`'tır ve ikisinin ayrı
        olması bilinçlidir ("buzdolabı arızası" cümlesi müşteriye söylenecek
        şey değildir).
        """
        problem = rules.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        fresh, link_error = await self._fresh_settings()
        if link_error:
            return {"ok": False, "connected": False, "error": link_error}

        server_now = rules.text(fresh.get("server_time"))
        problem = rules.pause_error(until, now=server_now)
        if problem:
            return {"ok": False, "error": problem}
        problem = rules.customer_message_error(customer_message)
        if problem:
            return {"ok": False, "error": problem}

        clean_until = rules.text(until) or None
        clean_message = rules.text(customer_message) or None
        detail = {"until": clean_until, "customer_message": clean_message,
                  "preview": preview}
        await self._record(action="settings.ordering.pause", reason=reason, actor=actor,
                           result=TRIED, detail=detail)

        try:
            result = await self._api.pause_ordering(
                until=clean_until, customer_message=clean_message,
                location_id=self._location_id, reason=reason, actor=actor,
                dry_run=bool(preview))
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="settings.ordering.pause", reason=reason, actor=actor,
                               result=FAILED, detail={"error": message})
            return {"ok": False, "error": message, "code": self._code(failure)}

        broken = self._verify(result, preview=preview)
        if broken:
            await self._record(action="settings.ordering.pause", reason=reason, actor=actor,
                               result=FAILED, detail={"error": broken})
            return {"ok": False, "error": broken}

        await self._record(action="settings.ordering.pause", reason=reason, actor=actor,
                           result=PREVIEWED if preview else DONE,
                           detail={**detail, "audit_id": result.get("audit_id")})
        if not preview:
            await self._announce(PAUSED_EVENT, {"until": clean_until, "reason": reason,
                                                "actor": actor})
        return {"ok": True, "preview": preview, "audit_id": result.get("audit_id"),
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
                "would": result.get("would") if isinstance(result.get("would"), dict) else {}}

    async def resume(self, *, reason: str, actor: str,
                     preview: bool = False) -> dict[str, Any]:
        """Satışı yeniden AÇAR; durdurma izlerini temizler.

        Zaten açıksa da `ok` döner — işlem sonuç odaklıdır ve "zaten açıktı"
        diye hata vermek, yöneticiyi satışın kapalı olduğuna inandırırdı.
        """
        problem = rules.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}

        fresh, link_error = await self._fresh_settings()
        if link_error:
            return {"ok": False, "connected": False, "error": link_error}

        await self._record(action="settings.ordering.resume", reason=reason, actor=actor,
                           result=TRIED, detail={"was_enabled": fresh.get("ordering_enabled"),
                                                 "preview": preview})
        try:
            result = await self._api.resume_ordering(
                location_id=self._location_id, reason=reason, actor=actor,
                dry_run=bool(preview))
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="settings.ordering.resume", reason=reason, actor=actor,
                               result=FAILED, detail={"error": message})
            return {"ok": False, "error": message, "code": self._code(failure)}

        broken = self._verify(result, preview=preview)
        if broken:
            await self._record(action="settings.ordering.resume", reason=reason, actor=actor,
                               result=FAILED, detail={"error": broken})
            return {"ok": False, "error": broken}

        await self._record(action="settings.ordering.resume", reason=reason, actor=actor,
                           result=PREVIEWED if preview else DONE,
                           detail={"audit_id": result.get("audit_id")})
        if not preview:
            await self._announce(RESUMED_EVENT, {"reason": reason, "actor": actor})
        return {"ok": True, "preview": preview, "audit_id": result.get("audit_id"),
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
                "already_open": fresh.get("ordering_enabled") is True}

    async def add_closed_day(self, *, date: str, description: str | None, reason: str,
                             actor: str, preview: bool = False) -> dict[str, Any]:
        """Kapalı gün ekler. **Global** — bütün vitrinler için geçerli.

        Geçmiş tarih KABUL EDİLİR (rapor tutarlılığı); o güne sipariş varsa
        sunucu `warnings` döndürür ve ekran onları AYNEN yazar — engel yoktur
        ama yönetici ne olduğunu bilmelidir.
        """
        problem = rules.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        problem = rules.closed_day_error(date, description)
        if problem:
            return {"ok": False, "error": problem}

        clean_date = rules.iso_date(date)
        clean_description = rules.text(description) or None
        await self._record(action="settings.closed_day.create", reason=reason, actor=actor,
                           result=TRIED, target_type="closed_day", target_id=clean_date,
                           detail={"description": clean_description, "preview": preview})
        try:
            result = await self._api.create_closed_day(
                date=clean_date, description=clean_description, reason=reason,
                actor=actor, dry_run=bool(preview))
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="settings.closed_day.create", reason=reason,
                               actor=actor, result=FAILED, target_type="closed_day",
                               target_id=clean_date, detail={"error": message})
            return {"ok": False, "error": message, "code": self._code(failure)}

        broken = self._verify(result, preview=preview)
        if broken:
            await self._record(action="settings.closed_day.create", reason=reason,
                               actor=actor, result=FAILED, target_type="closed_day",
                               target_id=clean_date, detail={"error": broken})
            return {"ok": False, "error": broken}

        warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
        await self._record(action="settings.closed_day.create", reason=reason, actor=actor,
                           result=PREVIEWED if preview else DONE, target_type="closed_day",
                           target_id=clean_date,
                           detail={"audit_id": result.get("audit_id"), "warnings": warnings})
        return {"ok": True, "preview": preview, "audit_id": result.get("audit_id"),
                "data": result.get("data") if isinstance(result.get("data"), dict) else {},
                "warnings": warnings}

    async def remove_closed_day(self, *, date: str, reason: str, actor: str,
                                preview: bool = False) -> dict[str, Any]:
        """Kapalı günü kaldırır.

        SİLME GERÇEK SİLMEDİR (sözleşme): kapalı gün bir belge değil bir
        kuraldır ve iptal edilmiş bir kuralın "iptal edilmiş" hâlini saklamak,
        üretim sorgularının her seferinde bir bayrak daha kontrol etmesi
        demekti. Kaydın tarihçesi denetim izindedir — hem sunucudaki hem
        buradaki.

        Kit'in "silme yok, pasifleştirme var" kuralı burada UYGULANAMAZ çünkü
        sözleşme pasifleştirme alanı tanımlamıyor; kuralın koruduğu şey
        (geri izlenebilirlik) gerekçeli onay ve iki denetim satırıyla
        karşılanır.
        """
        problem = rules.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean_date = rules.iso_date(date)
        if not clean_date:
            return {"ok": False, "error": "Kapalı gün tarihi YYYY-MM-DD biçiminde olmalı."}

        await self._record(action="settings.closed_day.delete", reason=reason, actor=actor,
                           result=TRIED, target_type="closed_day", target_id=clean_date,
                           detail={"preview": preview})
        try:
            result = await self._api.delete_closed_day(clean_date, reason=reason,
                                                       actor=actor, dry_run=bool(preview))
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            code = self._code(failure)
            if code == "not_found":
                # "Zaten öyle" hoşgörüsü UYGULANMAZ: var olmayan bir tatili
                # silmeye çalışan yönetici muhtemelen yanlış tarihe bakıyor ve
                # bunu bilmeli.
                message = (f"{clean_date} kapalı gün olarak kayıtlı değil. "
                           "Yanlış tarihe bakıyor olabilirsiniz.")
            await self._record(action="settings.closed_day.delete", reason=reason,
                               actor=actor, result=FAILED, target_type="closed_day",
                               target_id=clean_date, detail={"error": message})
            return {"ok": False, "error": message, "code": code}

        broken = self._verify(result, preview=preview)
        if broken:
            await self._record(action="settings.closed_day.delete", reason=reason,
                               actor=actor, result=FAILED, target_type="closed_day",
                               target_id=clean_date, detail={"error": broken})
            return {"ok": False, "error": broken}

        await self._record(action="settings.closed_day.delete", reason=reason, actor=actor,
                           result=PREVIEWED if preview else DONE, target_type="closed_day",
                           target_id=clean_date,
                           detail={"audit_id": result.get("audit_id")})
        return {"ok": True, "preview": preview, "audit_id": result.get("audit_id"),
                "data": result.get("data") if isinstance(result.get("data"), dict) else {}}

    async def set_stock(self, *, date: str, capacity_total: Any, items: Any, reason: str,
                        actor: str, preview: bool = False) -> dict[str, Any]:
        """Bir günün stok tavanlarını yazar. **TAM LİSTE.**

        Gönderilmeyen kalemin tavanı sunucuda `null`'a düşer. Bu yüzden panel
        ekranda GÖRDÜĞÜ bütün kalemleri gönderir ve servis taze okumadaki
        kalem kümesiyle karşılaştırır: aradan yeni bir kalem eklendiyse yazma
        REDDEDİLİR, çünkü göndermediğimiz o kalemin tavanı sessizce kalkardı.
        """
        problem = rules.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean_date = rules.iso_date(date)
        if not clean_date:
            return {"ok": False, "error": "Stok tarihi YYYY-MM-DD biçiminde olmalı."}

        body, problem = rules.clean_stock(capacity_total, items)
        if problem:
            return {"ok": False, "error": problem}

        try:
            current = await self._api.menu_stock(clean_date, location_id=self._location_id)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure)}

        snapshot = rules.stock_summary(current if isinstance(current, dict) else {})
        known = {row["item_id"] for row in snapshot["items"]}
        sending = {row["item_id"] for row in body["items"]}
        missing = sorted(known - sending)
        if missing:
            names = ", ".join(
                row["name"] or str(row["item_id"])
                for row in snapshot["items"] if row["item_id"] in missing)
            return {"ok": False, "error": (
                f"Menüye aradan kalem eklenmiş ({names}) ve ekrandaki liste eksik. "
                "Stok yazması TAM LİSTEDİR; eksik gönderim o kalemin tavanını sessizce "
                "kaldırırdı. Ekranı tazeleyip yeniden deneyin."), "stale": True}
        unknown = sorted(sending - known)
        if unknown:
            return {"ok": False, "error": (
                f"Bu günün menüsünde bulunmayan kalem gönderildi: {unknown}. "
                "Ekranı tazeleyin.")}

        planned = []
        for row in snapshot["items"]:
            new = next((item for item in body["items"]
                        if item["item_id"] == row["item_id"]), None)
            if new is not None and new["capacity"] != row["capacity"]:
                planned.append({"item_id": row["item_id"], "name": row["name"],
                                "from": row["capacity"], "to": new["capacity"]})
        if body["capacity_total"] != snapshot["day"]["capacity"]:
            planned.append({"item_id": 0, "name": "Gün toplamı",
                            "from": snapshot["day"]["capacity"],
                            "to": body["capacity_total"]})

        await self._record(action="menu.stock", reason=reason, actor=actor, result=TRIED,
                           target_type="stock", target_id=clean_date,
                           detail={"changes": planned, "preview": preview})
        try:
            result = await self._api.set_menu_stock(
                clean_date, capacity_total=body["capacity_total"], items=body["items"],
                location_id=self._location_id, reason=reason, actor=actor,
                dry_run=bool(preview))
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="menu.stock", reason=reason, actor=actor,
                               result=FAILED, target_type="stock", target_id=clean_date,
                               detail={"error": message})
            return {"ok": False, "error": message, "code": self._code(failure)}

        broken = self._verify(result, preview=preview)
        if broken:
            await self._record(action="menu.stock", reason=reason, actor=actor,
                               result=FAILED, target_type="stock", target_id=clean_date,
                               detail={"error": broken})
            return {"ok": False, "error": broken}

        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        would = result.get("would") if isinstance(result.get("would"), dict) else {}
        payload = would or data
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        await self._record(action="menu.stock", reason=reason, actor=actor,
                           result=PREVIEWED if preview else DONE, target_type="stock",
                           target_id=clean_date,
                           detail={"changes": planned, "warnings": warnings,
                                   "audit_id": result.get("audit_id")})
        return {"ok": True, "preview": preview, "date": clean_date, "changes": planned,
                "audit_id": result.get("audit_id"), "data": data, "would": would,
                "warnings": warnings}

    async def set_pref(self, *, key: str, value: str, actor: str) -> dict[str, Any]:
        """Ekran tercihi yazar. BLD'ye HİÇ GİTMEZ."""
        name = rules.text(key)
        if name not in ("stock_days", "tab"):
            return {"ok": False, "error": "Bilinmeyen ekran tercihi."}
        try:
            await self._store.execute(
                f"INSERT INTO {self._prefs} (key, value, actor, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value = excluded.value, actor = excluded.actor, "
                "updated_at = excluded.updated_at",
                (name, rules.text(value), actor, rules.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — tercih yazılamadı (K7)
            self._log.warning("tercih yazılamadı", key=name, error=str(failure))
            return {"ok": False, "error": "Ekran tercihi kaydedilemedi."}
        return {"ok": True}
