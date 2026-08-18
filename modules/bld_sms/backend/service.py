"""SMS Paneli — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Şablon metni (`veykemtu_sms_templates`),
gönderim kaydı (`veykemtu_sms_log`) ve duyuru taslağı (`location_options`) BLD
sunucusundadır ve buraya `bld.api` geçidinden gelir (K4). Bu modül ham httpx
kullanmaz, Netgsm istemcisi yazmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel
tablolar yalnız BLD'de KARŞILIĞI OLMAYAN üç şeyi saklar: yazma denemesinin izi,
yazdığımız anın temel çizgisi ve toplu duyurunun kuru prova jetonu.

BU EKRAN SMS GÖNDERMEZ — GÖNDERİLMESİNİ İSTER. Gönderimi BLD sunucusu kendi
sağlayıcısıyla yapar (`Services\\Sms\\SmsSender`). Kontrol Merkezi'nin kendi
Netgsm şeridi (`notify`) bu yolun parçası DEĞİLDİR ve panel ikisini ayrı
satırda yazar; aksi hâlde BLD'nin sırrı eksikken yönetici buradaki Netgsm
ayarını düzeltmeye çalışır ve hiçbir şey değişmez.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır;
ayrımı taşıyan alan `connected`'dır ve ekranın onu OKUMASI gerekir.

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe denetimi (min 10 — arayüzde zorunlu göstermek yetmez, K9)
    2. TAZE OKUMA (şablon/duyuru aradan değişmiş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı (`dry_run=` HER ZAMAN AÇIK GEÇİLİR)
    5. yerel iz: `ok` / `dry_run` / `hata`

Üçüncü adım burada ayrıca ağır basıyor: toplu duyuru KUYRUĞA ALINMAZ, akış
hâlinde gönderilir. İstek yarıda kesilirse bazı müşteriler mesajı almış olabilir
ve bunu bilen tek satır o izdir.

`dry_run=` HİÇBİR ÇAĞRIDA ATLANMAZ. Geçidin varsayılanı `config/local.yaml`
ile değiştirilebiliyor ve o dosya git dışında; bayrağı atlayan bir çağrı hiçbir
şey yazmadan `{"ok": true}` alır ve ekran "kaydedildi" der. "Kaydedildi" ile
"sessizce atıldı" ayırt edilemez hâle gelir.

TOPLU DUYURUDA KURU PROVA ZORUNLU İLK ADIMDIR ve BURADA KISA DEVRE YAPTIRILMAZ:
prova isteği gerçekten sunucuya gider (`dry_run=True`), sunucu ön denetimleri
koşar ve `would` döner. Yerelde uydurulmuş bir "tahmin" ile onay almak,
sunucunun reddedeceği bir gönderimi onaylatmak olurdu.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from km_sdk import normalize_msisdn

from . import text as txt

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Toplu duyuru gerçekten gönderildiğinde yayınlanan olay (manifest).
#: Kuru provada YAYINLANMAZ.
SENT_EVENT = "bld_sms.announcement_sent"

#: Kuru prova jetonunun varsayılan ömrü (dakika).
DRY_RUN_TTL_MINUTES = 15


class SmsService:
    """SMS Paneli'nin tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 notify: Any = None, publish: Any = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._notify = notify
        self._publish = publish

        self._audit = store.table("audit")
        self._baseline = store.table("templates")
        self._triggers = store.table("triggers")
        self._dry_runs = store.table("announcement_dry")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """Kuru prova varsayılanı. İstemci bayrağı HİÇ göndermezse bu geçerlidir.

        VARSAYILAN KAPALI ve yedek değeri de `False`: ayar dosyası okunamadığında
        ekranın "kaydedildi" deyip hiçbir şey yazmaması, açık bir hatadan çok
        daha pahalıdır. Panel zaten her yazmada bayrağı açıkça gönderiyor.
        """
        return bool(self._config.get("dry_run_default", False))

    @property
    def _log_page_size(self) -> int:
        return max(1, min(200, txt.as_int(self._config.get("log_page_size"), 25)))

    @property
    def _dry_run_ttl(self) -> int:
        return max(1, txt.as_int(self._config.get("announcement_dry_run_ttl_minutes"),
                                 DRY_RUN_TTL_MINUTES))

    @property
    def _segment_warn(self) -> int:
        return max(1, txt.as_int(self._config.get("segment_warn_threshold"), 2))

    @property
    def _confirm_threshold(self) -> int:
        return max(1, txt.as_int(self._config.get("announcement_confirm_threshold"), 100))

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit zarfı `{"items": [...], "meta": {...}}` biçiminde açıyor; `data`
        ve düz dizi de kabul edilir çünkü tek bir ada bağlanmak, ad
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
    def _meta(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return dict(payload["meta"])
        return {}

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Yazma yanıtından kaydı çıkarır. `would` DA OKUNUR: kuru provada
        sunucu `data` değil `would` döndürüyor (00-genel.md §3.1) ve ekranın
        göstereceği sayı orada."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _was_dry(payload: Any, asked: bool) -> bool:
        """Sunucu gerçekten prova mı yaptı.

        SORDUĞUMUZ DEĞİL, CEVAP OKUNUR: bir kurulum geçidin varsayılanını geri
        açarsa `dry_run=False` istenmiş bir çağrı yine provaya düşebilir ve
        ekran "gönderildi" DEMEMELİDİR.
        """
        if isinstance(payload, dict) and "dry_run" in payload:
            return bool(payload["dry_run"])
        return bool(asked)

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_type: str = "sms_template", target_key: str = "",
                      detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor
        (00-genel.md §3); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        `detail` içine METNİN TAMAMI ve AÇIK NUMARA YAZILMAZ — sözleşmenin
        denetim kuralı ile aynı: gönderilen cümlenin arşivi `veykemtu_sms_log`
        tarafındadır, burada yalnız ölçüsü durur.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_key, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target_type, target_key, action, txt.text_of(reason), actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), txt.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: SMS'ler gitti, dinleyicinin
        patlaması onları geri getirmez (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    def _platform_lane(self) -> dict[str, Any]:
        """Kontrol Merkezi'nin KENDİ SMS şeridi — bilgi amaçlı, gönderim yolu DEĞİL.

        Panel bunu BLD'nin sağlayıcı durumundan ayrı bir satırda yazar. Yetenek
        yoksa `available: False` döner ve ekran çalışmaya devam eder (K7).
        """
        return {
            "available": self._notify is not None,
            "note": "Kontrol Merkezi'nin kendi Netgsm şeridi. BLD SMS'leri "
                    "BURADAN GEÇMEZ; gönderimi BLD sunucusu kendi sağlayıcısıyla "
                    "yapar.",
        }

    # ------------------------------------------------- yerel temel çizgi

    async def _baselines(self) -> dict[str, dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT template_key, body_hash, length, segments, enabled, actor, "
                f"reason, updated_at FROM {self._baseline}"
            )
        except Exception as failure:  # noqa: BLE001 — temel çizgi yoksa sapma bilinmez, ekran durmaz
            self._log.warning("temel çizgi okunamadı", error=str(failure))
            return {}
        return {str(row["template_key"]): dict(row) for row in rows}

    async def _trigger_states(self) -> dict[str, dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT template_key, confirmed, last_state, changed_by, reason, "
                f"changed_at FROM {self._triggers}"
            )
        except Exception as failure:  # noqa: BLE001
            self._log.warning("tetikleyici politikası okunamadı", error=str(failure))
            return {}
        return {str(row["template_key"]): dict(row) for row in rows}

    async def _write_baseline(self, key: str, *, body: str, length: int, segments: int,
                              enabled: bool, actor: str, reason: str) -> None:
        try:
            await self._store.execute(
                f"INSERT INTO {self._baseline} "
                "(template_key, body_hash, length, segments, enabled, actor, reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(template_key) DO UPDATE SET "
                "body_hash=excluded.body_hash, length=excluded.length, "
                "segments=excluded.segments, enabled=excluded.enabled, "
                "actor=excluded.actor, reason=excluded.reason, updated_at=excluded.updated_at",
                (key, txt.body_hash(body), int(length), int(segments), 1 if enabled else 0,
                 actor, txt.text_of(reason), txt.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — temel çizgi yazılamadı, iş durmasın (K7)
            self._log.warning("temel çizgi yazılamadı", key=key, error=str(failure))

    async def _write_trigger(self, key: str, *, enabled: bool, actor: str,
                             reason: str) -> None:
        """Tetikleyici politikası.

        `confirmed` BİR KEZ 1 OLUR ve geri dönmez: "bu bildirim Kontrol
        Merkezi'nden en az bir kez bilinçli olarak açıldı" bilgisi kapatınca
        yalan olmaz. Kapatma `last_state` ile ayrıca duruyor.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._triggers} "
                "(template_key, confirmed, last_state, changed_by, reason, changed_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(template_key) DO UPDATE SET "
                "confirmed=MAX(mod_bld_sms_triggers.confirmed, excluded.confirmed), "
                "last_state=excluded.last_state, changed_by=excluded.changed_by, "
                "reason=excluded.reason, changed_at=excluded.changed_at",
                (key, 1 if enabled else 0, 1 if enabled else 0, actor,
                 txt.text_of(reason), txt.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001
            self._log.warning("tetikleyici politikası yazılamadı", key=key,
                              error=str(failure))

    # ================================================================ okuma

    async def templates(self) -> dict[str, Any]:
        """Şablon listesi + öbekler + yerel politika + sağlayıcı durumu.

        Panelin AÇILIŞ okumasıdır: Şablonlar ve Tetikleyiciler sekmelerinin
        ikisi de bu tek yanıtla çizilir. İki sekme için iki istek atmak,
        paylaşılan 3000/saat bütçesini iki katına çıkarırdı.
        """
        try:
            payload = await self._api.sms_templates()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "data": [], "groups": txt.GROUPS + [txt.OTHER_GROUP],
                    "sender": {}, "platform_lane": self._platform_lane(),
                    "segment_warn": self._segment_warn}

        baselines = await self._baselines()
        triggers = await self._trigger_states()
        rows: list[dict[str, Any]] = []

        for raw in self._items(payload):
            key = txt.text_of(raw.get("key"))
            if not key:
                continue
            body = str(raw.get("body") or "")
            catalog = txt.CATALOG.get(key, {})
            base = baselines.get(key) or {}
            trigger = triggers.get(key) or {}
            enabled = bool(raw.get("enabled"))

            rows.append({
                # Sunucunun sözlüğü olduğu gibi taşınır (snake_case).
                "key": key,
                "title": str(raw.get("title") or key),
                "body": body,
                "enabled": enabled,
                "variables": [str(name) for name in (raw.get("variables") or [])],
                "length": txt.as_int(raw.get("length"), len(body)),
                "segments": txt.as_int(raw.get("segments")),
                "has_turkish_chars": bool(raw.get("has_turkish_chars")),
                "updated_at": raw.get("updated_at"),
                # Ekranın kendi kataloğu — sunucuda karşılığı yok.
                "group": str(catalog.get("group") or txt.OTHER_GROUP["key"]),
                "about": str(catalog.get("about") or ""),
                # O METNİ GERÇEKTEN GÖNDEREN KOD. Boşsa şablon sunucuda
                # tanımlı ama hiçbir yerden gönderilmiyor — ekran bunu
                # söylemeli, yoksa yönetici çalışmayan bir metni özenle
                # yazıp açıyor (bkz. `txt.CATALOG` başlığı).
                "sender_code": str(catalog.get("sender") or ""),
                # Katalogda YOKSA bilinmiyor demektir; "gönderilmiyor" DEĞİL.
                # İkisini karıştırmak, sunucuya yeni eklenmiş bir şablonu
                # haksız yere ölü göstermek olurdu.
                "dispatch": ("unknown" if "sender" not in catalog
                             else ("live" if catalog.get("sender") else "dead")),
                "sample": txt.sample_for(key),
                # Yerel politika ve temel çizgi.
                "local": {
                    # BU EKRANDAN AÇILDI MI. Sunucudaki `enabled` "bugün açık"
                    # der, "bilinçli açıldı" demez; ikisi ayrı sorulardır ve
                    # ikincisinin cevabı yalnız burada.
                    "confirmed": bool(txt.as_int(trigger.get("confirmed"))),
                    "changed_by": str(trigger.get("changed_by") or ""),
                    "changed_at": str(trigger.get("changed_at") or ""),
                    "reason": str(trigger.get("reason") or ""),
                    # METİN SAPTI MI: en son biz yazdıktan sonra metin başka
                    # bir yerden değişmiş olabilir. Temel çizgi yoksa sapma
                    # bilinmez (False), "değişmiş" DENMEZ.
                    "drifted": bool(base) and str(base.get("body_hash") or "")
                    != txt.body_hash(body),
                    "written_at": str(base.get("updated_at") or ""),
                },
                # AÇIK AMA ONAYLANMAMIŞ: sunucuda açık görünüyor ama bu ekrandan
                # hiç açılmadı. Şablonun açık doğması tek dağıtımı binlerce
                # SMS'e çevirir; panel bunu uyarı olarak gösterir.
                "unconfirmed_enabled": enabled
                and not bool(txt.as_int(trigger.get("confirmed"))),
            })

        meta = self._meta(payload)
        return {
            "ok": True,
            "connected": True,
            "error": "",
            "data": rows,
            "groups": txt.GROUPS + [txt.OTHER_GROUP],
            "audiences": txt.AUDIENCES,
            "sender": {
                "driver": str(meta.get("sender_driver") or ""),
                # SIRRI OLMAYAN SAĞLAYICI HİÇBİR ŞEY GÖNDERMEZ, yalnız günlüğe
                # yazar (`LogSmsSender`). Panel bunu açıkça göstermeli; aksi
                # hâlde "SMS gitti" diyen bir ekran hiçbir şey göndermemiş olur.
                "configured": bool(meta.get("sender_configured")),
                # BAŞLIK VE EKSİK ALANLAR AÇILIŞ OKUMASINDA DA GELİR.
                # `configured: False` "bir şey eksik" diyor ama HANGİSİ
                # olduğunu söylemiyordu; yönetici de eksik olan parolayken
                # başlığı düzeltmeye çalışıyordu.
                "header": str(meta.get("sender_header") or ""),
                "header_source": str(meta.get("sender_header_source") or ""),
                "missing": [str(name) for name in (meta.get("sender_missing") or [])],
            },
            "platform_lane": self._platform_lane(),
            "segment_warn": self._segment_warn,
            "server_time": (payload or {}).get("server_time")
            if isinstance(payload, dict) else None,
        }

    def measure(self, *, body: str, key: str = "",
                sample: dict[str, Any] | None = None,
                allowed: list[str] | None = None) -> dict[str, Any]:
        """YEREL ölçüm ve önizleme. AĞA ÇIKMAZ, denetim satırı yazmaz.

        Panelin canlı sayacı buradan beslenir. Sunucudaki `preview` ucu her
        çağrıda denetim satırı yazıyor ve geçidin dakikada 18 istekle sınırlı
        tek kovasından geçiyor; her tuş vuruşunda oraya gitmek hem kovayı
        boşaltır hem denetim izini kullanılamaz hâle getirirdi.

        Sunucu önizlemesi KALDIRILMADI — `preview()` ile ayrı bir eylem olarak
        duruyor ve gerçeği o söyler. Buradaki sayı bir TAHMİNDİR ve panel bunu
        yazar.
        """
        body = str(body or "")
        ornek = txt.sample_for(key, sample)
        rendered, unresolved = txt.render(body, ornek)
        izinli = allowed if allowed is not None else list(ornek)

        return {
            "ok": True,
            "data": {
                "key": key,
                "rendered": rendered,
                "unresolved_variables": unresolved,
                # SUNUCU BUNU 422 İLE REDDEDER; ekran kaydetmeden önce söyler.
                "unknown_variables": txt.unknown_variables(body, izinli),
                "variables": txt.variables(body),
                # Ölçüm HAM metnin değil, gönderilecek metnin ölçüsüdür:
                # değişkenler yerine konunca uzunluk değişir ve fatura da öyle.
                "raw": txt.measure(body),
                "rendered_measure": txt.measure(rendered),
                "warn_at": self._segment_warn,
                "limits": {"body_min": txt.BODY_MIN, "body_max": txt.BODY_MAX},
            },
        }

    async def log(self, *, phone: str = "", template_key: str = "", status: str = "",
                  context: str = "", customer_id: int = 0, date_from: str = "",
                  date_to: str = "", page: int = 1,
                  per_page: int = 0) -> dict[str, Any]:
        """Gönderim kaydı (sayfalı). Kayıt SİLİNEMEZ; silme ucu yoktur.

        Telefon sunucuda maskeleniyor ve gövde 120 karakterde kırpılıyor.
        Burada ikisi de GERİ AÇILMAZ: gönderim kaydı bir iletişim defterine
        dönüşmemeli.
        """
        try:
            payload = await self._api.sms_log(
                phone=txt.text_of(phone), template_key=txt.text_of(template_key),
                status=txt.text_of(status), context=txt.text_of(context),
                customer_id=int(customer_id) or None,
                date_from=txt.text_of(date_from), date_to=txt.text_of(date_to),
                page=max(1, int(page or 1)),
                per_page=int(per_page) or self._log_page_size,
            )
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "data": [], "meta": {}}

        return {
            "ok": True, "connected": True, "error": "",
            "data": self._items(payload),
            # `meta.segment_total` süzgeçlenmiş kümenin segment toplamıdır —
            # maliyet sorusunun cevabı ve ekranın göstermesi gereken sayı.
            "meta": self._meta(payload),
            "contexts": list(txt.LOG_CONTEXTS),
            "statuses": list(txt.LOG_STATUSES),
        }

    async def announcement(self) -> dict[str, Any]:
        """Duyuru taslağı + ALICI TAHMİNİ.

        `estimate` her okumada sunucuda yeniden hesaplanıyor: alıcı sayısı
        sürekli değişiyor ve donmuş bir tahmin, yöneticinin sandığından fazla
        SMS göndermesi demekti. Bu yüzden ekran da bu değeri saklamaz, her
        açılışta yeniden okur.
        """
        try:
            payload = await self._api.sms_announcement()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "data": {}, "audiences": txt.AUDIENCES, "dry_run": None}

        data = dict(payload) if isinstance(payload, dict) else {}
        body = str(data.get("body") or "")
        estimate = data.get("estimate") if isinstance(data.get("estimate"), dict) else {}

        return {
            "ok": True, "connected": True, "error": "",
            "data": {
                "body": body,
                "audience": str(data.get("audience") or ""),
                "length": txt.as_int(data.get("length"), len(body)),
                "segments": txt.as_int(data.get("segments")),
                "encoding": str(data.get("encoding") or ""),
                "last_run_at": data.get("last_run_at"),
                "estimate": {
                    "recipients": txt.as_int((estimate or {}).get("recipients")),
                    "segments": txt.as_int((estimate or {}).get("segments")),
                },
                # Yerel ölçüm: sunucununkiyle yan yana gösterilir.
                "measure": txt.measure(body),
            },
            "audiences": txt.AUDIENCES,
            # BEKLEYEN KURU PROVA. Gerçek gönderim ancak taze bir provanın
            # ardından yapılabilir; ekran hangi provanın açık olduğunu bilmeli.
            "dry_run": await self._pending_dry_run(),
            "confirm_threshold": self._confirm_threshold,
            "ttl_minutes": self._dry_run_ttl,
        }

    async def netgsm(self) -> dict[str, Any]:
        """Netgsm gönderici ayarı — başlık, kaynağı ve eksik alanlar.

        PAROLA BURAYA HİÇ GELMEZ ve gelmeyecek: BLD onu ortam değişkeninde
        tutuyor ve hiçbir uçtan geri vermiyor (K8 ile aynı gerekçe — sır
        yedeklerde ve istemci günlüklerinde dolaşmamalı). Ekranın bilmesi
        gereken tek şey "dolu mu", o da `missing` listesinde.

        BAŞLIK İSE AÇIK GELİR çünkü düzeltilecek olan odur: Netgsm'in `40`
        hatası ancak gönderilen ad ile panelde onaylı ad yan yana görülünce
        anlaşılır.
        """
        try:
            payload = await self._api.sms_netgsm()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "data": {}, "header_max": txt.HEADER_MAX}

        data = dict(payload) if isinstance(payload, dict) else {}
        return {
            "ok": True, "connected": True, "error": "",
            "data": {
                "header": str(data.get("header") or ""),
                # AYRIM ÖNEMLİ: ayar boş ama ortam dolu olabilir. İkisini tek
                # alana indirmek, yöneticinin "demek ki başlık yok" deyip
                # ÇALIŞAN bir yapılandırmayı bozmasına yol açardı.
                "stored_header": str(data.get("stored_header") or ""),
                "env_header": str(data.get("env_header") or ""),
                "source": str(data.get("source") or ""),
                "username_configured": bool(data.get("username_configured")),
                "password_configured": bool(data.get("password_configured")),
                "missing": [str(name) for name in (data.get("missing") or [])],
                "driver": str(data.get("driver") or ""),
            },
            "header_max": txt.as_int(data.get("header_max"), txt.HEADER_MAX),
            "expected_header": txt.EXPECTED_HEADER,
        }

    async def history(self, *, limit: int = 50) -> dict[str, Any]:
        """Bu ekrandan yapılan yazma DENEMELERİ — yerel iz.

        BLD'nin denetim izinden ayrıdır ve onu tekrar etmez: burada
        sunucuya ULAŞMAYAN denemeler de var. İkisini birlikte okumak, "istek
        gitti mi" sorusunun tek cevabıdır.
        """
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_type, target_key, action, reason, actor, result, "
                f"detail, created_at FROM {self._audit} ORDER BY id DESC LIMIT ?",
                (max(1, min(500, int(limit or 50))),),
            )
        except Exception as failure:  # noqa: BLE001
            return {"ok": True, "error": str(failure), "data": []}

        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail") or "{}")
            except (TypeError, ValueError):
                item["detail"] = {}
            out.append(item)
        return {"ok": True, "error": "", "data": out}

    # ============================================================== yazma

    async def preview(self, key: str, *, body: str = "", sample: dict[str, Any] | None,
                      reason: str, actor: str, dry_run: bool | None) -> dict[str, Any]:
        """SUNUCU önizlemesi — hiçbir şey göndermez ama yazma kabuğundan geçer.

        Okuma gibi görünür, `POST`'tur: örnek veri gövdeyle taşınıyor ve
        gövdesiz bir GET onu sorgu dizesine, yani imzanın DIŞINA koyardı.
        Gerekçe istemesinin sebebi de bu: sunucu bunu bir yazma sayıyor ve
        denetim satırı düşüyor.

        Yerel sayaç (`measure`) bunun yerine geçmez; bu uç gerçeği söyler.
        """
        hata = txt.reason_error(reason)
        if hata:
            await self._record(action="template_preview", reason=reason, actor=actor,
                               result=BLOCKED, target_key=key, detail={"error": hata})
            return {"ok": False, "error": hata}

        istek_dry = self._dry(dry_run)
        await self._record(action="template_preview", reason=reason, actor=actor,
                           result=TRIED, target_key=key,
                           detail={"key": key, "draft": bool(body)})
        try:
            payload = await self._api.preview_sms_template(
                key, body=str(body or ""), sample=sample,
                reason=reason, actor=actor, dry_run=istek_dry,
            )
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="template_preview", reason=reason, actor=actor,
                               result=FAILED, target_key=key, detail={"error": message})
            return {"ok": False, "error": message}

        data = self._record_of(payload, "data", "would")
        await self._record(action="template_preview", reason=reason, actor=actor,
                           result=DRY if self._was_dry(payload, istek_dry) else DONE,
                           target_key=key,
                           detail={"key": key, "segments": data.get("segments")})
        return {"ok": True, "dry_run": self._was_dry(payload, istek_dry),
                "audit_id": (payload or {}).get("audit_id"), "data": data}

    async def update_template(self, key: str, *, body: str | None, enabled: bool | None,
                              reason: str, actor: str,
                              dry_run: bool | None) -> dict[str, Any]:
        """Şablon metnini ve/veya durumunu yazar. KISMİ: yalnız verilen alanlar.

        `title` YAZILAMAZ ve bu uçtan geçmez — şablonun adı sistemin kendi
        sözlüğüdür, yöneticinin değiştireceği bir şey değil.

        BİR BİLDİRİMİ AÇMAK BİLİNÇLİ BİR HAREKETTİR: `enabled: True` gelen bir
        çağrı, arayüzde gerekçeli onaydan geçmiş olmalıdır ve burada da ayrıca
        kaydedilir (K9 — çift kapı). Açık doğan ya da yanlışlıkla açılan bir
        şablon, tek dağıtımı binlerce SMS'e çevirir.
        """
        hata = txt.reason_error(reason)
        if not hata and body is None and enabled is None:
            hata = "Değişecek bir şey yok: metin ya da durum verilmeli."
        if not hata and body is not None:
            temiz = str(body)
            if not temiz.strip():
                hata = "Şablon metni boş olamaz."
            elif len(temiz) > txt.BODY_MAX:
                hata = f"Şablon metni en çok {txt.BODY_MAX} karakter olabilir."

        if hata:
            await self._record(action="template_update", reason=reason, actor=actor,
                               result=BLOCKED, target_key=key, detail={"error": hata})
            return {"ok": False, "error": hata}

        # TAZE OKUMA: metin aradan başkası tarafından değişmiş olabilir ve
        # uzunluk farkını denetim satırına yazabilmek için önceki hâli lazım.
        onceki, okuma_hatasi = await self._fresh_template(key)
        if onceki is None:
            await self._record(action="template_update", reason=reason, actor=actor,
                               result=FAILED, target_key=key,
                               detail={"error": okuma_hatasi})
            return {"ok": False, "error": okuma_hatasi}

        # TANINMAYAN DEĞİŞKEN: sunucu 422 veriyor. Burada da denetlenir (K9)
        # çünkü hatayı erken ve anlaşılır söylemek, 422'yi ekrana basmaktan
        # iyidir — ve istek hiç çıkmadığı için hız kovası da yanmaz.
        if body is not None:
            bilinmeyen = txt.unknown_variables(str(body), onceki.get("variables") or [])
            if bilinmeyen:
                mesaj = ("Şablonun tanımadığı değişken var: "
                         + ", ".join(f"{{{ad}}}" for ad in bilinmeyen)
                         + ". Tanınan değişkenler: "
                         + ", ".join(f"{{{ad}}}" for ad in (onceki.get("variables") or [])))
                await self._record(action="template_update", reason=reason, actor=actor,
                                   result=BLOCKED, target_key=key,
                                   detail={"unknown_variables": bilinmeyen})
                return {"ok": False, "error": mesaj}

        istek_dry = self._dry(dry_run)
        eylem = "template_update"
        if enabled is True and not onceki.get("enabled"):
            eylem = "template_enable"
        elif enabled is False and onceki.get("enabled"):
            eylem = "template_disable"

        # METNİN TAMAMI YAZILMAZ — yalnız ölçüsü (sözleşmenin denetim kuralı).
        iz = {"key": key,
              "length_from": txt.as_int(onceki.get("length")),
              "length_to": len(str(body)) if body is not None else None,
              "enabled": enabled}
        await self._record(action=eylem, reason=reason, actor=actor, result=TRIED,
                           target_key=key, detail=iz)

        # `UNSET` geçidin "bu alanı gönderme" işareti; `None` göndermek alanı
        # açıkça boşaltmak olurdu. Verilmeyen alanı hiç iletmemek için
        # anahtar sözlüğü koşullu kurulur.
        cagri: dict[str, Any] = {}
        if body is not None:
            cagri["body"] = str(body)
        if enabled is not None:
            cagri["enabled"] = bool(enabled)

        try:
            payload = await self._api.update_sms_template(
                key, reason=reason, actor=actor, dry_run=istek_dry, **cagri)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action=eylem, reason=reason, actor=actor, result=FAILED,
                               target_key=key, detail={"error": message})
            return {"ok": False, "error": message}

        prova = self._was_dry(payload, istek_dry)
        data = self._record_of(payload, "data", "would")
        await self._record(action=eylem, reason=reason, actor=actor,
                           result=DRY if prova else DONE, target_key=key,
                           detail={**iz, "segments": data.get("segments")})

        # TEMEL ÇİZGİ VE POLİTİKA YALNIZ GERÇEK YAZMADA GÜNCELLENİR: kuru
        # provada BLD'de hiçbir şey değişmedi ve yereli ilerletmek, ekranı
        # olmayan bir yazmaya inandırmak olurdu.
        if not prova:
            yeni_body = str(body) if body is not None else str(onceki.get("body") or "")
            yeni_durum = bool(enabled) if enabled is not None else bool(onceki.get("enabled"))
            await self._write_baseline(
                key, body=yeni_body,
                length=txt.as_int(data.get("length"), len(yeni_body)),
                segments=txt.as_int(data.get("segments")),
                enabled=yeni_durum, actor=actor, reason=reason)
            if enabled is not None:
                await self._write_trigger(key, enabled=bool(enabled), actor=actor,
                                          reason=reason)

        return {"ok": True, "dry_run": prova, "audit_id": (payload or {}).get("audit_id"),
                "data": data}

    async def send_test(self, *, phone: str, template_key: str = "", body: str = "",
                        sample: dict[str, Any] | None, reason: str, actor: str,
                        dry_run: bool | None) -> dict[str, Any]:
        """Tek numaraya `[DENEME]` SMS'i.

        Ön eki SUNUCU koyuyor ve kaldırılamıyor: deneme SMS'inin gerçek bir
        bildirimden ayırt edilememesi, yanlış numaraya giden bir mesajın
        müşteride panik yaratması demekti. Panel de bunu metnin başında gösterir.

        Şablon VEYA serbest metin; ikisi birden `422`. Geçit bunu istek çıkmadan
        kesiyor, burada erken kesilmesinin sebebi anlaşılır bir cümle vermek.
        """
        hata = txt.reason_error(reason)
        if not hata and bool(template_key) == bool(body):
            hata = ("Deneme SMS'i ya bir şablonla ya da serbest metinle gönderilir; "
                    "ikisi birden ya da hiçbiri olmaz.")

        numara = ""
        if not hata:
            try:
                numara = normalize_msisdn(phone)
            except ValueError:
                hata = ("Geçersiz cep numarası. Beklenen biçim 5XXXXXXXXX "
                        "(10 hane, 5 ile başlar).")

        if hata:
            await self._record(action="send_test", reason=reason, actor=actor,
                               result=BLOCKED, target_type="test",
                               target_key=txt.text_of(template_key),
                               detail={"error": hata})
            return {"ok": False, "error": hata}

        istek_dry = self._dry(dry_run)
        # DENETİM SATIRINA AÇIK NUMARA YAZILMAZ (sözleşme "Denetim eylemleri").
        iz = {"phone": txt.mask_phone(numara),
              "template_key": txt.text_of(template_key) or None,
              "free_text": bool(body)}
        await self._record(action="send_test", reason=reason, actor=actor, result=TRIED,
                           target_type="test", target_key=txt.text_of(template_key),
                           detail=iz)

        try:
            payload = await self._api.send_test_sms(
                phone=numara, template_key=txt.text_of(template_key), body=str(body or ""),
                sample=sample, reason=reason, actor=actor, dry_run=istek_dry)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="send_test", reason=reason, actor=actor,
                               result=FAILED, target_type="test",
                               target_key=txt.text_of(template_key),
                               detail={**iz, "error": message})
            return {"ok": False, "error": message}

        prova = self._was_dry(payload, istek_dry)
        data = self._record_of(payload, "data", "would")
        # SAĞLAYICI HATASI İSTEK HATASI DEĞİLDİR: sunucu `502` değil,
        # `ok: true` + `data.status: "failed"` döndürüyor. Gönderim denemesi
        # kayda geçti; ekran "istek patladı" DEMEMELİ, "gönderilemedi" demeli.
        durum = str(data.get("status") or ("dry_run" if prova else ""))
        await self._record(action="send_test", reason=reason, actor=actor,
                           result=DRY if prova else (DONE if durum == "sent" else FAILED),
                           target_type="test", target_key=txt.text_of(template_key),
                           detail={**iz, "status": durum,
                                   "segments": data.get("segments")})
        return {"ok": True, "dry_run": prova, "audit_id": (payload or {}).get("audit_id"),
                "data": data}

    async def set_netgsm(self, *, header: str, reason: str, actor: str,
                         dry_run: bool | None) -> dict[str, Any]:
        """Netgsm gönderici başlığını yazar.

        BLD'DEN GİDEN HER SMS BU ADLA GİDER. Yanlış bir başlık sessiz bir
        arızadır: sağlayıcı `40` döner, istek 200 kalır, kayıt `failed` olur ve
        müşteriye hiçbir şey ulaşmaz. Bu yüzden yazma yolu diğerleriyle aynı
        beş adımı izler (gerekçe → iz → geçit → iz) ve `dry_run` atlanmaz.

        BOŞ DİZE AYARI SİLER ve BLD ortam değişkenine geri döner. Engellemek
        yerine geçirmemizin sebebi şu: bir "geri al" yolu olmasaydı, panelden
        bir kez yazılan yanlış başlık oradaki doğru değeri kalıcı olarak
        gölgelerdi.
        """
        hata = txt.reason_error(reason)
        temiz = str(header or "").strip()

        if not hata and len(temiz) > txt.HEADER_MAX:
            hata = (f"Gönderici başlığı en çok {txt.HEADER_MAX} karakter olabilir; "
                    f"Netgsm daha uzununu reddeder ({len(temiz)} karakter girildi).")
        # SUNUCU BUNU DA 422 İLE REDDEDER (`regex`). Burada erken kesmek hem
        # anlaşılır bir cümle verir hem de hız kovasını yakmaz (K9 — çift kapı).
        if not hata and temiz and not txt.HEADER_ALLOWED.fullmatch(temiz):
            hata = ("Gönderici başlığı yalnız harf, rakam ve boşluk içerebilir. "
                    "Türkçe karakterli bir başlık Netgsm'de zaten onaylanamaz ve "
                    "gönderim `40` hatasıyla düşerdi.")

        if hata:
            await self._record(action="netgsm_update", reason=reason, actor=actor,
                               result=BLOCKED, target_type="netgsm",
                               detail={"error": hata})
            return {"ok": False, "error": hata}

        istek_dry = self._dry(dry_run)
        # BAŞLIK BİR SIR DEĞİL — müşterinin telefonunda görünen ad. İzde açık
        # yazılması, "hangi başlıkla gidiyordu" sorusunun tek cevabıdır.
        iz = {"header": temiz, "cleared": not temiz}
        await self._record(action="netgsm_update", reason=reason, actor=actor,
                           result=TRIED, target_type="netgsm", detail=iz)

        try:
            payload = await self._api.set_sms_netgsm(header=temiz, reason=reason,
                                                     actor=actor, dry_run=istek_dry)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="netgsm_update", reason=reason, actor=actor,
                               result=FAILED, target_type="netgsm",
                               detail={**iz, "error": message})
            return {"ok": False, "error": message}

        prova = self._was_dry(payload, istek_dry)
        await self._record(action="netgsm_update", reason=reason, actor=actor,
                           result=DRY if prova else DONE, target_type="netgsm",
                           detail=iz)

        return {
            "ok": True, "dry_run": prova, "audit_id": (payload or {}).get("audit_id"),
            "data": self._record_of(payload, "data", "would"),
            # YENİ BAŞLIK BİR SONRAKİ İSTEKTE YÜRÜRLÜĞE GİRER: gönderici BLD
            # tarafında istek başına çözülen bir tekil. Ekranın bunu yazması
            # gerekiyor, aksi hâlde yönetici hemen deneme SMS'i gönderip eski
            # başlıkla `40` alır ve kaydın işlemediğini sanır.
            "warnings": [str(code) for code in ((payload or {}).get("warnings") or [])],
        }

    async def set_announcement(self, *, body: str, audience: str, reason: str, actor: str,
                               dry_run: bool | None) -> dict[str, Any]:
        """Duyuru TASLAĞINI yazar. GÖNDERMEZ.

        Gönderme ayrı bir eylem, ayrı bir izin ve ayrı bir gerekçedir; metni
        yazmakla göndermeyi tek tuşta birleştirmek, yazım hatasını yüzlerce
        kişiye ulaştırırdı.

        TASLAK DEĞİŞİNCE BEKLEYEN KURU PROVA DÜŞER: prova "şu metin, şu kadar
        kişiye" diyordu; metin değiştiyse o onay artık başka bir mesaja aitti.
        """
        hata = txt.reason_error(reason)
        if not hata and not str(body or "").strip():
            hata = "Duyuru metni boş olamaz."
        if not hata and len(str(body)) > txt.BODY_MAX:
            hata = f"Duyuru metni en çok {txt.BODY_MAX} karakter olabilir."
        if not hata and audience not in txt.AUDIENCE_KEYS:
            hata = ("Bilinmeyen kitle. Seçenekler: "
                    + ", ".join(sorted(txt.AUDIENCE_KEYS)) + ".")

        if hata:
            await self._record(action="announcement_update", reason=reason, actor=actor,
                               result=BLOCKED, target_type="announcement",
                               detail={"error": hata})
            return {"ok": False, "error": hata}

        istek_dry = self._dry(dry_run)
        olcum = txt.measure(str(body))
        iz = {"audience": audience, "length": len(str(body)),
              "segments": olcum["billed"]["segments"]}
        await self._record(action="announcement_update", reason=reason, actor=actor,
                           result=TRIED, target_type="announcement", detail=iz)

        try:
            payload = await self._api.set_sms_announcement(
                body=str(body), audience=audience, reason=reason, actor=actor,
                dry_run=istek_dry)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="announcement_update", reason=reason, actor=actor,
                               result=FAILED, target_type="announcement",
                               detail={**iz, "error": message})
            return {"ok": False, "error": message}

        prova = self._was_dry(payload, istek_dry)
        await self._record(action="announcement_update", reason=reason, actor=actor,
                           result=DRY if prova else DONE, target_type="announcement",
                           detail=iz)
        if not prova:
            await self._drop_dry_runs("taslak değişti")

        return {"ok": True, "dry_run": prova, "audit_id": (payload or {}).get("audit_id"),
                "data": self._record_of(payload, "data", "would")}

    async def run_announcement(self, *, confirm_recipients: int, reason: str, actor: str,
                               dry_run: bool | None, token: str = "",
                               allow_send: bool = False) -> dict[str, Any]:
        """Toplu duyuruyu çalıştırır. KURU PROVA ZORUNLU İLK ADIMDIR.

        İki ayrı yol tek uçta:

        * `dryRun: true` → istek GERÇEKTEN sunucuya gider, sunucu ön denetimleri
          koşar ve `would` döner (kaç alıcı, işlenmiş gövde). Yanıt bir JETON
          üretir ve yerele yazılır.
        * `dryRun: false` → jeton ŞARTTIR. Jeton yoksa, kullanılmışsa, süresi
          dolmuşsa ya da provadaki alıcı sayısı ile `confirm_recipients`
          uyuşmuyorsa gönderim BURADA durur ve istek hiç çıkmaz.

        Jeton kapısı sunucudaki `confirm_recipients` kapısının yerine geçmez,
        ONDAN ÖNCE durur: sunucu "sayı değişti" diyerek 409 verir, buradaki kapı
        ise "hiç prova yapılmadı" durumunu yakalar — sunucu onu göremez, çünkü
        onun için ilk istek de geçerli bir istektir.

        `allow_send` izin kapısının servisteki AYNASIDIR (K9 — çift kapı).
        """
        hata = txt.reason_error(reason)
        istek_dry = self._dry(dry_run)

        if not hata and not istek_dry and not allow_send:
            hata = ("Toplu duyuru göndermek için `bld_sms.announce` yetkisi gerekir. "
                    "Kuru prova bu yetkiyi istemez.")
        if not hata and int(confirm_recipients or 0) < 0:
            hata = "Alıcı sayısı eksi olamaz."

        if hata:
            await self._record(action="announcement_run", reason=reason, actor=actor,
                               result=BLOCKED, target_type="announcement",
                               detail={"error": hata, "dry_run": istek_dry})
            return {"ok": False, "error": hata}

        if istek_dry:
            return await self._announcement_dry_run(reason=reason, actor=actor)
        return await self._announcement_send(
            confirm_recipients=int(confirm_recipients or 0), reason=reason, actor=actor,
            token=txt.text_of(token))

    # -------------------------------------------------------- duyuru içi

    async def _announcement_dry_run(self, *, reason: str, actor: str) -> dict[str, Any]:
        """Kuru prova: istek sunucuya GİDER, hiçbir SMS gitmez.

        `confirm_recipients` provada da gönderiliyor çünkü gövdenin şekli
        sabittir; sunucu prova dalında sayıyı kendisi hesaplayıp `would`
        içinde döndürüyor. Buradan gönderilen `0` bir iddia değil, yer
        tutucudur — gerçek sayı yanıttan okunur.
        """
        await self._record(action="announcement_dry_run", reason=reason, actor=actor,
                           result=TRIED, target_type="announcement", detail={})

        taslak = await self.announcement()
        if not taslak.get("connected"):
            mesaj = str(taslak.get("error") or "Duyuru taslağı okunamadı.")
            await self._record(action="announcement_dry_run", reason=reason, actor=actor,
                               result=FAILED, target_type="announcement",
                               detail={"error": mesaj})
            return {"ok": False, "error": mesaj}

        body_text = str((taslak.get("data") or {}).get("body") or "")
        if not body_text.strip():
            mesaj = "Duyuru metni boş. Önce taslağı yazın."
            await self._record(action="announcement_dry_run", reason=reason, actor=actor,
                               result=BLOCKED, target_type="announcement",
                               detail={"error": mesaj})
            return {"ok": False, "error": mesaj}

        try:
            payload = await self._api.run_sms_announcement(
                confirm_recipients=txt.as_int(
                    ((taslak.get("data") or {}).get("estimate") or {}).get("recipients")),
                reason=reason, actor=actor, dry_run=True)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            await self._record(action="announcement_dry_run", reason=reason, actor=actor,
                               result=FAILED, target_type="announcement",
                               detail={"error": message})
            return {"ok": False, "error": message}

        prova = self._was_dry(payload, True)
        would = self._record_of(payload, "would", "data")
        alicilar = txt.as_int(would.get("recipients"))
        segmentler = txt.as_int(would.get("segments"))
        kitle = str(would.get("audience")
                    or (taslak.get("data") or {}).get("audience") or "")

        if not prova:
            # GEÇİT PROVAYI GERÇEK YAZMAYA ÇEVİRDİ. Bu bir yapılandırma
            # hatasıdır ve SESSİZ GEÇİLEMEZ: SMS'ler gitmiş olabilir.
            await self._record(action="announcement_run", reason=reason, actor=actor,
                               result=DONE, target_type="announcement",
                               detail={"warning": "prova istendi, sunucu gerçek "
                                                  "gönderim bildirdi",
                                       "recipients": alicilar})
            return {"ok": False, "dry_run": False,
                    "error": "Kuru prova istendi ama sunucu gerçek gönderim bildirdi. "
                             "Gönderim yapılmış olabilir; gönderim kaydını denetleyin.",
                    "data": would}

        token = secrets.token_urlsafe(18)
        await self._store_dry_run(token, audience=kitle, recipients=alicilar,
                                  segments=segmentler, body=body_text, actor=actor,
                                  reason=reason)
        await self._record(action="announcement_dry_run", reason=reason, actor=actor,
                           result=DRY, target_type="announcement",
                           detail={"audience": kitle, "recipients": alicilar,
                                   "segments": segmentler})

        return {
            "ok": True, "dry_run": True, "audit_id": (payload or {}).get("audit_id"),
            "data": {
                "token": token,
                "audience": kitle,
                "recipients": alicilar,
                "segments": segmentler,
                # GERÇEK İŞLENMİŞ GÖVDE — sunucunun döndürdüğü örnek. Ekran
                # bunu göstermeden gönderim düğmesini açmaz.
                "sample_rendered": str(would.get("sample_rendered") or ""),
                "ttl_minutes": self._dry_run_ttl,
                "confirm_threshold": self._confirm_threshold,
            },
        }

    async def _announcement_send(self, *, confirm_recipients: int, reason: str, actor: str,
                                 token: str) -> dict[str, Any]:
        """Gerçek toplu gönderim. Jetonsuz YAPILMAZ."""
        kayit, hata = await self._use_dry_run(token, confirm_recipients)
        if hata:
            await self._record(action="announcement_run", reason=reason, actor=actor,
                               result=BLOCKED, target_type="announcement",
                               detail={"error": hata, "recipients": confirm_recipients})
            return {"ok": False, "error": hata}

        iz = {"audience": str((kayit or {}).get("audience") or ""),
              "recipients": confirm_recipients,
              "segments": txt.as_int((kayit or {}).get("segments"))}
        await self._record(action="announcement_run", reason=reason, actor=actor,
                           result=TRIED, target_type="announcement", detail=iz)

        try:
            payload = await self._api.run_sms_announcement(
                confirm_recipients=confirm_recipients, reason=reason, actor=actor,
                dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            message = self._fail(failure)
            # JETON YANDI. Ağ koparsa gönderimin yapılıp yapılmadığı bilinmez;
            # aynı jetonla ikinci kez denemek, aynı duyuruyu iki kez göndermek
            # olabilirdi. Yeniden prova yapılır.
            await self._record(action="announcement_run", reason=reason, actor=actor,
                               result=FAILED, target_type="announcement",
                               detail={**iz, "error": message})
            return {"ok": False, "error": message}

        prova = self._was_dry(payload, False)
        data = self._record_of(payload, "data", "would")
        gonderilen = txt.as_int(data.get("sent"))
        basarisiz = txt.as_int(data.get("failed"))

        await self._record(action="announcement_run", reason=reason, actor=actor,
                           result=DRY if prova else DONE, target_type="announcement",
                           detail={**iz, "sent": gonderilen, "failed": basarisiz,
                                   "segments": txt.as_int(data.get("segments"))})

        # OLAY YALNIZ GERÇEK GÖNDERİMDE YAYINLANIR.
        if not prova:
            await self._announce(SENT_EVENT, {
                "recipients": txt.as_int(data.get("recipients"), confirm_recipients),
                "sent": gonderilen, "failed": basarisiz,
                "segments": txt.as_int(data.get("segments")),
                "audience": iz["audience"], "actor": actor,
            })

        return {"ok": True, "dry_run": prova, "audit_id": (payload or {}).get("audit_id"),
                "data": data}

    # ------------------------------------------------------- jeton defteri

    async def _store_dry_run(self, token: str, *, audience: str, recipients: int,
                             segments: int, body: str, actor: str, reason: str) -> None:
        try:
            await self._store.execute(
                f"INSERT INTO {self._dry_runs} "
                "(token, audience, recipients, segments, body_hash, actor, reason, "
                "created_at, used_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')",
                (token, audience, int(recipients), int(segments), txt.body_hash(body),
                 actor, txt.text_of(reason), txt.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001
            self._log.warning("kuru prova jetonu yazılamadı", error=str(failure))

    async def _pending_dry_run(self) -> dict[str, Any] | None:
        """Kullanılmamış ve süresi dolmamış EN SON prova."""
        try:
            rows = await self._store.fetch_all(
                f"SELECT token, audience, recipients, segments, actor, reason, created_at "
                f"FROM {self._dry_runs} WHERE used_at = '' ORDER BY created_at DESC LIMIT 5"
            )
        except Exception as failure:  # noqa: BLE001
            self._log.warning("kuru prova jetonu okunamadı", error=str(failure))
            return None
        for row in rows:
            if not txt.older_than(str(row.get("created_at")), self._dry_run_ttl):
                return dict(row)
        return None

    async def _use_dry_run(self, token: str,
                           recipients: int) -> tuple[dict[str, Any] | None, str]:
        """Jetonu harcar. `(kayıt, hata)` döner.

        Jeton BİR KEZ kullanılır: çift tıklama ile aynı duyuruyu iki kez almak,
        müşterinin gördüğü tek şeydir. Sunucunun 10 dakikalık soğuma penceresi
        de var ama o ikinci savunma hattıdır; ilki burada.
        """
        if not token:
            return None, ("Önce kuru prova çalıştırın. Toplu gönderim, kaç kişiye "
                          "gideceği ve metnin işlenmiş hâli görülmeden yapılmaz.")
        try:
            row = await self._store.fetch_one(
                f"SELECT token, audience, recipients, segments, actor, reason, "
                f"created_at, used_at FROM {self._dry_runs} WHERE token = ?", (token,))
        except Exception as failure:  # noqa: BLE001
            return None, f"Kuru prova kaydı okunamadı: {failure}"

        if row is None:
            return None, "Kuru prova kaydı bulunamadı. Provayı yeniden çalıştırın."
        kayit = dict(row)
        if txt.text_of(kayit.get("used_at")):
            return None, ("Bu kuru prova zaten kullanıldı. Yeni bir gönderim için "
                          "provayı yeniden çalıştırın.")
        if txt.older_than(str(kayit.get("created_at")), self._dry_run_ttl):
            return None, (f"Kuru provanın üzerinden {self._dry_run_ttl} dakikadan fazla "
                          "geçti; alıcı sayısı değişmiş olabilir. Provayı yenileyin.")
        if txt.as_int(kayit.get("recipients")) != int(recipients):
            return None, (f"Onaylanan alıcı sayısı provadakiyle aynı değil "
                          f"(prova: {txt.as_int(kayit.get('recipients'))}, "
                          f"onay: {int(recipients)}). Provayı yenileyin.")

        try:
            await self._store.execute(
                f"UPDATE {self._dry_runs} SET used_at = ? WHERE token = ?",
                (txt.now_iso(), token))
        except Exception as failure:  # noqa: BLE001
            # JETON HARCANAMADIYSA GÖNDERİM YAPILMAZ: harcanmamış bir jeton
            # ikinci bir gönderime kapı açar ve müşteri duyuruyu iki kez alır.
            return None, f"Kuru prova jetonu kapatılamadı, gönderim yapılmadı: {failure}"
        return kayit, ""

    async def _drop_dry_runs(self, why: str) -> None:
        """Bekleyen provaları KULLANILMIŞ işaretler — silmez (kayıt silinmez).

        Taslak değiştiğinde çağrılır: prova "şu metin, şu kadar kişiye" diyordu
        ve metin artık başka.
        """
        try:
            await self._store.execute(
                f"UPDATE {self._dry_runs} SET used_at = ? WHERE used_at = ''",
                (f"{txt.now_iso()} ({why})",))
        except Exception as failure:  # noqa: BLE001
            self._log.warning("bekleyen provalar kapatılamadı", error=str(failure))

    # ---------------------------------------------------------- taze okuma

    async def _fresh_template(self, key: str) -> tuple[dict[str, Any] | None, str]:
        """Şablonun TAZE hâli. `(satır, hata)` döner.

        Sözleşmede tek şablon okuyan bir uç YOK; taze okuma listeden yapılır.
        Tek istek eder ve karşılığında yazmadan hemen önceki gerçek metni verir
        — başkası aradan değiştirmiş olabilir.
        """
        try:
            payload = await self._api.sms_templates()
        except Exception as failure:  # noqa: BLE001 — K7
            return None, self._fail(failure)
        for raw in self._items(payload):
            if txt.text_of(raw.get("key")) == txt.text_of(key):
                body = str(raw.get("body") or "")
                return {
                    "key": key,
                    "body": body,
                    "enabled": bool(raw.get("enabled")),
                    "length": txt.as_int(raw.get("length"), len(body)),
                    "segments": txt.as_int(raw.get("segments")),
                    "variables": [str(name) for name in (raw.get("variables") or [])],
                }, ""
        return None, (f"'{key}' diye bir şablon yok. Anahtarlar sabittir; "
                      "listede olmayan bir anahtara yazılamaz.")
