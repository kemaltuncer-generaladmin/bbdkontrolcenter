"""Müşteri aşama SMS'i — iş kuralları.

GÖNDERİM KONTROL MERKEZİ'NDEN ÇIKAR, mağazadan değil. Bu, modülün geri kalanına
göre bilinçli bir AYRIMDIR ve gerekçesi tek cümledir: **fren burada**.

  · Mağaza tarafında (Bagisto) kuru prova yok, beyaz liste yok, tek segment
    zorlaması yok, "aynı siparişe ikinci kez gönderme" kaydı yok.
  · Kontrol Merkezi'nde bunların hepsi var ve ödeme linki SMS'i için zaten
    kurulmuş, çalıştığı görülmüş durumda (`store_payment_gateway`).

Toplu bildirim ve e-posta yine mağazadan geçer (bkz. `service.py`); değişen
yalnız işlemsel aşama SMS'idir.

ÜÇ KATMANLI FREN — gerçek SMS yalnız üçü de izin verirse çıkar:
  1. `platform.notify.sms.dry_run`  (platform ayarı; varsayılan AÇIK)
  2. `lifecycle_sms_dry_run`        (modül ayarı; varsayılan AÇIK)
  3. isteğin/tetikleyicinin kendi `dry_run` bayrağı (varsayılan AÇIK)
Dördüncü bir daraltma olarak `lifecycle_sms_allowlist` doluyken yalnız
listedeki numaralara gerçek mesaj gider. HANGİ KATMANIN TUTTUĞU YAZILIR:
"gönderilmedi" demek yetmez, personel nereyi açacağını bilmeli.

SESSİZ SAAT UYGULANMAZ — ve bu bir unutma değildir. Aşama SMS'i işlemseldir ve
ERTELENEMEZ: bu modülde kuyruk yok, zamanlanmış yeniden deneme yok. Sessiz
saatte "gönderme" demek, o siparişin takip kodunun müşteriye HİÇ ulaşmaması
demekti. Üç aşamanın üçü de zaten uyanık saatlerde tetikleniyor: sipariş
müşterinin kendi eylemi, kargo devri personelin mesaisi, teslim kuryenin
saatleri.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): istisna dışarı sızmaz,
`{"ok": False, "error": …}` döner.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from km_sdk import SmsMessage

from . import lifecycle, messaging


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class StageNotifier:
    """`store.notify.stage` yeteneğinin uygulaması.

    Siparişler ekranı bunu çağırır; o ekran bu modülü IMPORT ETMEZ (K3). Yüzey
    bilerek dardır: aşama gönderimi ve "hangi aşama açık" sorusu. Şablon yazma
    ya da aşama açma bu yetenekten YAPILAMAZ — onlar bu modülün kendi
    uçlarından, kendi izniyle (`store_notifications.manage`) ve gerekçeyle olur.
    """

    def __init__(self, service: LifecycleService) -> None:
        self._service = service

    async def notify(self, *, stage: str, order: dict[str, Any], actor: str = "",
                     dry_run: bool = True) -> dict[str, Any]:
        """Bir siparişin bir aşaması için SMS. `dry_run` VARSAYILAN AÇIK."""
        return await self._service.notify_stage(stage=stage, order=order, actor=actor,
                                                dry_run=dry_run)

    async def state(self) -> dict[str, Any]:
        """Hangi aşamalar açık ve SMS katmanı hazır mı.

        Çağıran bunu önden sorar: kapalı bir aşama için sipariş detayı çekmek,
        her taramada boşuna istek üretmek olurdu.
        """
        return await self._service.stage_state()

    async def done(self, *, stage: str, order_ids: list[int]) -> dict[str, Any]:
        """Verilen siparişlerden bu aşama için SMS'i ZATEN GİTMİŞ olanlar.

        TEKRAR ENGELİ DEĞİLDİR, hız iyileştirmesidir: asıl engel `notify()`
        içinde ve veritabanı düzeyindedir. Çağıran bunu kullanmasa da müşteri
        ikinci kez rahatsız olmaz; kullanınca boşuna sipariş detayı çekmez.
        """
        return await self._service.already_sent(stage=stage, order_ids=order_ids)


class LifecycleService:
    """Üç aşama SMS'inin tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, store: Any, log: Any, config: dict[str, Any],
                 notify: Any = None) -> None:
        self._store = store
        self._log = log
        self._config = config or {}
        self._notify = notify

        self._audit = store.table("audit")
        self._stages = store.table("lifecycle")
        self._log_table = store.table("lifecycle_log")

        #: Aşama+sipariş başına kilit. Veritabanındaki BENZERSİZ dizin arka
        #: arkaya gelen ikinci gönderimi zaten engelliyor; bu kilit AYNI ANDA
        #: gelen ikisini engeller (webhook iki kez düşerse ikisi de aynı
        #: saniyede işlenebilir ve ikisi de "kayıt yok" görebilirdi).
        self._locks: dict[tuple[str, int], asyncio.Lock] = {}

    # ------------------------------------------------------------- ayarlar

    @property
    def _store_name(self) -> str:
        return messaging.text(self._config.get("lifecycle_store_name")) or "BBD Store"

    @property
    def _tracking_base(self) -> str:
        return messaging.text(self._config.get("lifecycle_tracking_url_base"))

    @property
    def _header(self) -> str:
        return messaging.text(self._config.get("lifecycle_sms_header"))

    @property
    def _module_dry_run(self) -> bool:
        """Modülün kendi freni. VARSAYILAN AÇIK — kapatmak bilinçli karardır."""
        return bool(self._config.get("lifecycle_sms_dry_run", True))

    @property
    def _allowlist(self) -> list[str]:
        raw = self._config.get("lifecycle_sms_allowlist") or []
        return [lifecycle.normal_phone(item) for item in raw if messaging.text(item)]

    @property
    def _price(self) -> int:
        return max(0, messaging.as_int(self._config.get("sms_price_kurus"), 0))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        return str(failure).strip() or "SMS katmanına ulaşılamadı."

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      detail: Any = None) -> None:
        """Yerel denetim izi — `service.py` ile AYNI tabloya.

        Aşama SMS'i ayrı bir defterde tutulsaydı, "bu ekrandan bugün ne
        yapıldı" sorusunun iki ayrı cevabı olurdu.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(action, reason, actor, result, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), _now()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("aşama denetim izi yazılamadı", action=action, error=str(failure))

    def _lock(self, stage: str, order_id: int) -> asyncio.Lock:
        key = (stage, int(order_id))
        lock = self._locks.get(key)
        if lock is None:
            if len(self._locks) > 5_000:      # sınırsız büyümesin
                self._locks.clear()
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    # =============================================================== şablon

    async def _rows(self) -> dict[str, dict[str, Any]]:
        try:
            rows = await self._store.fetch_all(
                f"SELECT stage, body, enabled, actor, updated_at FROM {self._stages}")
        except Exception as failure:  # noqa: BLE001 — yerel tablo okunamadı (K7)
            self._log.warning("aşama şablonları okunamadı", error=str(failure))
            return {}
        return {messaging.text(row["stage"]): dict(row) for row in rows}

    async def _stage_row(self, stage: str) -> tuple[str, bool, str]:
        """(metin, açık mı, kaynak). Kayıt yoksa FABRİKA METNİ ve KAPALI.

        Kayıt yokluğunu "açık" saymak, göç çalışır çalışmaz üç aşamanın da
        müşteriye SMS atması demekti.
        """
        rows = await self._rows()
        row = rows.get(stage)
        if not row:
            return lifecycle.DEFAULT_TEMPLATES.get(stage, ""), False, "default"
        body = messaging.text(row.get("body")) or lifecycle.DEFAULT_TEMPLATES.get(stage, "")
        return body, messaging.as_bool(row.get("enabled"), False), "local"

    async def stages(self) -> dict[str, Any]:
        """Üç aşamanın metni, ölçümü, sorunları ve SMS katmanının durumu."""
        rows = await self._rows()
        items: list[dict[str, Any]] = []
        for stage in lifecycle.STAGES:
            row = rows.get(stage)
            body = (messaging.text(row.get("body")) if row else "") \
                or lifecycle.DEFAULT_TEMPLATES.get(stage, "")
            enabled = messaging.as_bool(row.get("enabled"), False) if row else False
            view = lifecycle.template_view(stage, body, enabled=enabled,
                                           price_kurus=self._price,
                                           source="local" if row else "default")
            view["updatedAt"] = messaging.text(row.get("updated_at"))[:19] if row else ""
            view["actor"] = messaging.text(row.get("actor")) if row else ""
            items.append(view)
        return {"ok": True, "error": "", "items": items, "sms": await self.sms_state(),
                "maxParts": lifecycle.MAX_PARTS,
                "guardSample": lifecycle.guard_values()}

    async def stage_state(self) -> dict[str, Any]:
        """Çağıran modülün önden sorduğu özet: hangi aşama açık, SMS hazır mı."""
        rows = await self._rows()
        enabled = [stage for stage in lifecycle.STAGES
                   if messaging.as_bool((rows.get(stage) or {}).get("enabled"), False)]
        return {"ok": True, "stages": list(lifecycle.STAGES), "enabled": enabled,
                "available": self._notify is not None}

    async def sms_state(self) -> dict[str, Any]:
        """SMS katmanının durumu ve ÜÇ FRENİN her birinin hâli.

        Üçü ayrı ayrı gösterilir: "gönderilmiyor" demek personele hangi
        anahtarı açacağını söylemez.
        """
        state: dict[str, Any] = {
            "available": self._notify is not None,
            "moduleDryRun": self._module_dry_run,
            # Platform freni okunamazsa AÇIK VARSAYILIR: bilinmeyen bir freni
            # kapalı göstermek, ekranın "gerçek SMS gidiyor" demesi olurdu.
            "platformDryRun": True,
            "allowlist": self._allowlist,
            "header": self._header,
            "provider": "", "configured": False, "enabled": False,
            "error": "" if self._notify is not None
                     else "Bildirim (notify) yeteneği bu kurulumda yok; aşama SMS'i gönderilemez.",
        }
        if self._notify is None:
            return state
        try:
            ready = await self._notify.ready()
        except Exception as failure:  # noqa: BLE001 — durum ekranı ayakta kalmalı (K7)
            state["error"] = self._fail(failure)
            return state
        state.update({
            "platformDryRun": bool(ready.get("dryRun", True)),
            "provider": messaging.text(ready.get("provider")),
            "configured": bool(ready.get("configured")),
            "enabled": bool(ready.get("enabled")),
            "header": self._header or messaging.text(ready.get("header")),
            "error": messaging.text(ready.get("error")),
        })
        return state

    def preview_stage(self, *, stage: str, body: str) -> dict[str, Any]:
        """Örnek veriyle önizleme + segment sayacı. AĞA ÇIKMAZ, YAZMAZ.

        Ölçüm `GUARD_SAMPLE` ile yapılır (bilerek uzun ad/takip/bağlantı):
        sayaç ile kayıt kapısı aynı metni ölçmezse ekran "1 parça" derken
        kayıt "2 parça" diye reddederdi.
        """
        problem = lifecycle.stage_error(stage)
        if problem:
            return {"ok": False, "error": problem}
        view = lifecycle.template_view(stage, body, enabled=False, price_kurus=self._price)
        return {"ok": True, "error": "", **view}

    async def save_stage(self, *, stage: str, body: str, enabled: bool, reason: str,
                         actor: str) -> dict[str, Any]:
        """Aşama metnini ve Açık/Kapalı durumunu yazar.

        TEK SEGMENTİ AŞAN ŞABLON KAYDEDİLMEZ (K9: kapı burada, arayüzde değil).
        Ekran uyarıyı zaten gösteriyor ama istemci şemayı atlatabilir; kayıt
        olsaydı her siparişte sessizce iki kredi harcanırdı.
        """
        problem = messaging.reason_error(reason) or lifecycle.stage_error(stage)
        if problem:
            return {"ok": False, "error": problem}
        # BOŞ METİN = "fabrika metnine dön". Boş bir şablonu kaydetmek, açık
        # bir aşamanın müşteriye boş SMS göndermesi olurdu; aşamayı susturmanın
        # yolu metni silmek değil, Açık/Kapalı anahtarıdır.
        text_body = messaging.text(body) or lifecycle.DEFAULT_TEMPLATES.get(stage, "")
        problem = lifecycle.template_problem(stage, text_body, price_kurus=self._price)
        if problem:
            # DENETİM İZİNE DE YAZILIR: reddedilen bir kayıt denemesi, sonradan
            # "neden hâlâ eski metin" sorusunun cevabıdır.
            await self._record(action="save_stage", reason=reason, actor=actor,
                               result="engellendi", detail={"stage": stage, "problem": problem})
            return {"ok": False, "error": problem,
                    "plan": lifecycle.plan(
                        lifecycle.render(text_body, lifecycle.guard_values(stage))["text"],
                        price_kurus=self._price)}

        try:
            await self._store.execute(
                f"INSERT INTO {self._stages} (stage, body, enabled, actor, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(stage) DO UPDATE SET "
                "body = excluded.body, enabled = excluded.enabled, actor = excluded.actor, "
                "updated_at = excluded.updated_at",
                (stage, text_body, 1 if enabled else 0, actor, _now()))
        except Exception as failure:  # noqa: BLE001 — yerel yazma
            return {"ok": False, "error": f"Aşama kaydedilemedi: {failure}"}

        await self._record(action="save_stage", reason=reason, actor=actor, result="ok",
                           detail={"stage": stage, "enabled": bool(enabled)})
        view = lifecycle.template_view(stage, text_body, enabled=enabled,
                                       price_kurus=self._price)
        return {"ok": True, "error": "", **view}

    # ================================================================== iz

    async def stage_log(self, *, stage: str = "", result: str = "",
                        limit: int = 100) -> dict[str, Any]:
        """Gönderim izi — gönderilmeyenler DE burada.

        "Numara yok" diye atlanan müşteri, ekranda görünmezse hiç yaşanmamış
        sayılır; oysa o siparişin sahibi kargo kodunu bekliyordur.
        """
        sql = (f"SELECT stage, order_id, order_no, customer, phone, result, note, parts, "
               f"job_id, created_at, updated_at FROM {self._log_table}")
        clauses: list[str] = []
        params: list[Any] = []
        if messaging.text(stage) in lifecycle.STAGES:
            clauses.append("stage = ?")
            params.append(messaging.text(stage))
        if messaging.text(result) in lifecycle.RESULT_LABELS:
            clauses.append("result = ?")
            params.append(messaging.text(result))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        try:
            rows = await self._store.fetch_all(sql, tuple(params))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun (K7)
            return {"ok": True, "items": [], "error": self._fail(failure), "summary": {}}
        items = [lifecycle.log_row(dict(row)) for row in rows]
        summary: dict[str, int] = {key: 0 for key in lifecycle.RESULT_LABELS}
        for item in items:
            summary[item["result"]] = summary.get(item["result"], 0) + 1
        return {"ok": True, "error": "", "items": items, "summary": summary}

    async def already_sent(self, *, stage: str, order_ids: list[int]) -> dict[str, Any]:
        """Bu aşama için SMS'i GERÇEKTEN gitmiş sipariş kimlikleri.

        Sorgu patlarsa BOŞ liste döner ve çağıran hepsini dener: eleme bir hız
        iyileştirmesidir, engel değil. Engelin yerini alan sahte bir eleme,
        gerçek korumayı görünmez kılardı.
        """
        problem = lifecycle.stage_error(stage)
        if problem:
            return {"ok": False, "error": problem, "ids": []}
        wanted = sorted({messaging.as_int(item) for item in (order_ids or [])
                         if messaging.as_int(item)})
        if not wanted:
            return {"ok": True, "error": "", "ids": []}
        marks = ", ".join("?" for _ in wanted)
        try:
            rows = await self._store.fetch_all(
                f"SELECT order_id FROM {self._log_table} WHERE stage = ? AND result = ? "
                f"AND order_id IN ({marks})",
                (stage, lifecycle.SENT, *wanted))
        except Exception as failure:  # noqa: BLE001 — eleme yapılamadı (K7)
            self._log.warning("gönderilmiş aşama listesi okunamadı", stage=stage,
                              error=str(failure))
            return {"ok": True, "error": self._fail(failure), "ids": []}
        return {"ok": True, "error": "",
                "ids": [messaging.as_int(row["order_id"]) for row in rows]}

    async def _existing(self, stage: str, order_id: int) -> dict[str, Any] | None:
        try:
            return await self._store.fetch_one(
                f"SELECT stage, order_id, result, note, created_at FROM {self._log_table} "
                "WHERE stage = ? AND order_id = ?", (stage, int(order_id)))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı
            self._log.warning("aşama izi okunamadı", stage=stage, orderId=order_id,
                              error=str(failure))
            return None

    async def _write_log(self, *, stage: str, order: dict[str, Any], phone: str,
                         result: str, note: str, parts: int = 0,
                         job_id: str = "") -> bool:
        """İzi yazar; `(stage, order_id)` benzersizdir, varsa ÜZERİNE YAZILIR.

        TEK İSTİSNA: `result = 'sent'` olan satır EZİLMEZ. Gerçekten gitmiş bir
        mesajın kaydını "kuru prova" ile değiştirmek, tekrarı önleyen tek
        kanıtı silmek olurdu.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._log_table} "
                "(stage, order_id, order_no, customer, phone, result, note, parts, job_id, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(stage, order_id) DO UPDATE SET "
                "order_no = excluded.order_no, customer = excluded.customer, "
                "phone = excluded.phone, result = excluded.result, note = excluded.note, "
                "parts = excluded.parts, job_id = excluded.job_id, "
                f"updated_at = excluded.updated_at WHERE {self._log_table}.result <> 'sent'",
                (stage, messaging.as_int(order.get("orderId")),
                 messaging.text(order.get("orderNo")), messaging.text(order.get("customer")),
                 phone, result, note, max(0, int(parts)), job_id, _now(), _now()))
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı
            # MESAJ ZATEN GİTTİYSE bu ciddi bir uyarıdır: tekrarı önleyen kanıt
            # yok demektir. İş durdurulmaz ama kayda geçer.
            self._log.warning("aşama izi yazılamadı", stage=stage,
                              orderId=order.get("orderId"), result=result, error=str(failure))
            return False
        return True

    # ============================================================= gönderim

    def _gate(self, phone: str, dry_run: bool) -> tuple[bool, str]:
        """ÜÇ KATMANLI FREN + beyaz liste. Hangi katmanın tuttuğu YAZILIR."""
        if self._notify is None:
            return False, "Bildirim (notify) yeteneği bu kurulumda yok."
        if dry_run:
            return False, "Tetikleyicinin kendi kuru provası açık."
        if self._module_dry_run:
            return False, ("Modül ayarı: lifecycle_sms_dry_run açık "
                           "(modules.store_notifications).")
        allow = self._allowlist
        if allow and lifecycle.normal_phone(phone) not in allow:
            return False, ("Numara beyaz listede değil "
                           "(modules.store_notifications.lifecycle_sms_allowlist).")
        return True, ""

    async def notify_stage(self, *, stage: str, order: dict[str, Any], actor: str = "",
                           dry_run: bool = True) -> dict[str, Any]:
        """Bir siparişin bir aşaması için müşteriye SMS.

        SIRA ÖNEMLİDİR ve her adım kendi nedenini kaydeder:
          1. aşama tanınıyor mu · sipariş kimliği var mı
          2. aşama AÇIK mı (kapalıysa iz bile yazılmaz: bu siparişin değil,
             kurulumun kararıdır)
          3. AYNI AŞAMA DAHA ÖNCE GİTMİŞ Mİ (tekrar engeli)
          4. metin dolabiliyor mu (numara, takip kodu, bağlantı)
          5. üç katmanlı fren
          6. gönderim ve sonucun ize yazılması
        """
        problem = lifecycle.stage_error(stage)
        if problem:
            return {"ok": False, "error": problem}
        order_id = messaging.as_int(order.get("orderId"))
        if order_id <= 0:
            return {"ok": False,
                    "error": "Sipariş kimliği yok; tekrar engeli sipariş kimliğine bağlıdır."}

        body, enabled, _ = await self._stage_row(stage)
        if not enabled:
            # İZ YAZILMAZ. Kapalı aşama bir gönderim denemesi değildir; her
            # taramada üç satır yazmak, izi anlamsız kılardı.
            return {"ok": True, "sent": False, "error": "", "stage": stage,
                    "result": "", "skipped": "stage_disabled",
                    "note": f"{lifecycle.STAGE_LABELS[stage]} aşaması kapalı."}

        async with self._lock(stage, order_id):
            return await self._deliver(stage=stage, order=order, body=body, actor=actor,
                                       dry_run=dry_run)

    async def _deliver(self, *, stage: str, order: dict[str, Any], body: str, actor: str,
                       dry_run: bool) -> dict[str, Any]:
        order_id = messaging.as_int(order.get("orderId"))
        previous = await self._existing(stage, order_id)
        if previous and lifecycle.blocks_resend(messaging.text(previous.get("result"))):
            # AYNI MÜŞTERİYE AYNI AŞAMA İÇİN İKİNCİ SMS GİTMEZ. Webhook iki kez
            # düşerse, tarama iki kez koşarsa ya da personel iki kez tıklarsa
            # burada durur: müşteri iki kez rahatsız olmaz, iki kez ödenmez.
            return {"ok": True, "sent": False, "error": "", "stage": stage,
                    "result": "sent", "duplicate": True,
                    "note": (f"{lifecycle.STAGE_LABELS[stage]} SMS'i bu siparişe "
                             f"{messaging.text(previous.get('created_at'))[:19]} tarihinde "
                             "zaten gönderildi.")}

        phone = messaging.text(order.get("phone"))
        values = lifecycle.order_values(stage, order, store_name=self._store_name,
                                        tracking_base=self._tracking_base)
        rendered = lifecycle.render(body, values)
        counted = lifecycle.plan(rendered["text"], price_kurus=self._price)

        phone_problem = lifecycle.phone_error(phone)
        if phone_problem:
            result = "no_phone" if not phone else "bad_phone"
            note = f"Gönderilemedi: {phone_problem}"
            await self._write_log(stage=stage, order=order, phone=phone, result=result,
                                  note=note)
            await self._record(action="stage_sms", reason=note, actor=actor, result=result,
                               detail={"stage": stage, "orderId": order_id})
            return {"ok": True, "sent": False, "error": "", "stage": stage, "result": result,
                    "note": note, "plan": counted}

        # NUMARA SAĞLAYICI BİÇİMİNE İNDİRGENİR. Mağaza "0532 123 45 67" gibi
        # serbest biçim tutuyor; sağlayıcıya ham hâlini vermek ya reddedilir ya
        # da beyaz liste karşılaştırmasını sessizce ıskalardı. İze de bu hâli
        # yazılır: aynı müşteri iki farklı yazımla iki ayrı numara görünmesin.
        phone = lifecycle.normal_phone(phone)

        if rendered["missing"]:
            names = ", ".join(sorted(set(rendered["missing"])))
            note = (f"Gönderilemedi: bilgi eksik ({names}). Süslü parantezli metin "
                    "müşteriye gönderilmez.")
            await self._write_log(stage=stage, order=order, phone=phone, result="missing",
                                  note=note)
            await self._record(action="stage_sms", reason=note, actor=actor, result="missing",
                               detail={"stage": stage, "orderId": order_id, "missing": names})
            return {"ok": True, "sent": False, "error": "", "stage": stage, "result": "missing",
                    "note": note, "plan": counted}

        allowed, block = self._gate(phone, dry_run)
        if not allowed:
            note = f"SMS GÖNDERİLMEDİ — {block}"
            await self._write_log(stage=stage, order=order, phone=phone, result="dry_run",
                                  note=note, parts=counted["parts"])
            await self._record(action="stage_sms", reason=note, actor=actor, result="dry_run",
                               detail={"stage": stage, "orderId": order_id,
                                       "parts": counted["parts"]})
            return {"ok": True, "sent": False, "error": "", "stage": stage, "result": "dry_run",
                    "dryRun": True, "note": note, "plan": counted, "text": rendered["text"]}

        try:
            provider = await self._notify.sms()
            outcome = await provider.send([SmsMessage(to=phone, text=rendered["text"])],
                                          header=self._header or None)
        except Exception as failure:  # noqa: BLE001 — SMS katmanı dışarısı (K7)
            note = f"Gönderilemedi: {self._fail(failure)}"
            await self._write_log(stage=stage, order=order, phone=phone, result="error",
                                  note=note, parts=counted["parts"])
            await self._record(action="stage_sms", reason=note, actor=actor, result="hata",
                               detail={"stage": stage, "orderId": order_id})
            return {"ok": False, "sent": False, "error": note, "stage": stage,
                    "result": "error", "plan": counted}

        accepted = bool(getattr(outcome, "accepted", False))
        provider_dry = bool(getattr(outcome, "dry_run", False))
        parts = messaging.as_int(getattr(outcome, "parts", counted["parts"]),
                                 counted["parts"])
        job_id = messaging.text(getattr(outcome, "job_id", ""))
        if provider_dry:
            result = "dry_run"
            note = "SMS GÖNDERİLMEDİ — platform ayarı: platform.notify.sms.dry_run açık."
        elif accepted:
            result = "sent"
            note = f"Gönderildi ({parts} parça)." if parts else "Gönderildi."
        else:
            result = "error"
            note = "Gönderilemedi: sağlayıcı işi kabul etmedi."

        await self._write_log(stage=stage, order=order, phone=phone, result=result, note=note,
                              parts=parts, job_id=job_id)
        await self._record(action="stage_sms", reason=note, actor=actor, result=result,
                           detail={"stage": stage, "orderId": order_id, "parts": parts,
                                   "jobId": job_id})
        return {"ok": True, "sent": result == "sent", "error": "", "stage": stage,
                "result": result, "dryRun": provider_dry, "note": note, "parts": parts,
                "jobId": job_id, "plan": counted}
