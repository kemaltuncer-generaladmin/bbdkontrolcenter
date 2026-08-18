"""Kantin Cihazları — iş kuralları.

VERİ KANTİNDEDİR, KARAR BURADADIR. Kiosk kaydı, eşleme kodu, token ve iptal
damgası kantin sunucusunda durur ve buraya `canteen.api` geçidinden gelir (K4);
bu modül ham httpx kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel tablolar
yalnız kantinde KARŞILIĞI OLMAYAN iki şeyi saklar: yazma denemesinin izi ve
bir önceki okumada görülen eşleme durumu.

MEVCUT CİHAZ AKIŞINA DOKUNULMAZ. Sahadaki kasa tableti kantinde `devices`
tablosunda, paylaşılan `enrollment_secret` ile çalışıyor. Bu servis o tabloyu
ne okur ne yazar; kullandığı bütün uçlar `/api/kiosks*` altındadır.

KURU PROVA YOKTUR — ve bilerek eklenmedi. `bld_kds` bir `dryRun` bayrağı
taşıyor çünkü BLD geçidi onu destekliyor ve sunucu tarafında karşılığı var.
Kantinde böyle bir parametre YOK: buraya bir bayrak koymak, "prova yapıldı"
diyip aslında hiçbir şey yapmayan ya da daha kötüsü GERÇEKTEN yazan bir kip
uydurmak olurdu. Yazmanın emniyeti gerekçe kutusu, ayrı izin ve yıkıcı
işlemdeki PIN teyididir.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
`ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır; ayrımı
taşıyan alan `connected`'dır ve panelin onu OKUMASI gerekir — yalnız `ok`a
bakan bir ekran, geçit düştüğünde "hiç kiosk yok" der.

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe denetimi (min 10 — arayüzde zorunlu göstermek yetmez, K9)
    2. TAZE OKUMA (kiosk aradan iptal edilmiş ya da adı değişmiş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı
    5. yerel iz: `ok` / `hata`

Üçüncü adım kritiktir: eşleme kodu üretilirken bağlantı koparsa kodun üretilip
üretilmediği bilinmez; iz olmasa kimin denediği de bilinmezdi.

EŞLEME KODU İZE YAZILMAZ. Kod bir sırdır ve iz satırı silinmez; yazsaydık
kodun ömrü 10 dakika yerine sonsuz olurdu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from km_sdk import build_pdf, report_dir, write_private

from . import kiosks as kio

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
BLOCKED = "engellendi"
FAILED = "hata"

#: Bir kiosk eşleme kodunu kullanıp kantine kaydolduğunda yayınlanır. Olay adı
#: `bbd_canteen_api` manifestinde ilan edilmişti ama HİÇ yayınlanmıyordu; ilk
#: gerçek yayıncı burasıdır.
ENROLLED_EVENT = "canteen.device_enrolled"

#: Eşleme bu ekrandan iptal edildiğinde yayınlanır.
REVOKED_EVENT = "canteen.kiosk_revoked"

#: Masaüstü çıktı hiyerarşisinde bu modülün rafı — kantin raporlarıyla aynı.
PRINT_CATEGORY = "Kantin"


class CanteenDeviceService:
    """Kantin Cihazları ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin, PIN ve şema kapısından
    çıkar.
    """

    def __init__(self, *, canteen: Any, store: Any, log: Any, config: dict[str, Any],
                 printer: Any = None, publish: Any = None,
                 fallback_dir: Path | None = None) -> None:
        self._canteen = canteen
        self._store = store
        self._log = log
        self._config = config or {}
        self._printer = printer
        self._publish = publish
        self._fallback_dir = fallback_dir or Path("data/exports")

        self._audit = store.table("audit")
        self._seen = store.table("seen")

    # ------------------------------------------------------------- ayarlar

    @property
    def _online_after(self) -> int:
        return max(1, kio.as_int(self._config.get("online_after_minutes"),
                                 kio.ONLINE_AFTER_MINUTES))

    @property
    def _ttl_minutes(self) -> int:
        return max(1, min(120, kio.as_int(self._config.get("pairing_ttl_minutes"), 10)))

    @property
    def _audit_page(self) -> int:
        return max(10, min(1000, kio.as_int(self._config.get("audit_page_size"), 200)))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Kantin sunucusuna ulaşılamadı."

    @staticmethod
    def _guard(reason: str) -> str:
        """Gerekçe backend'de DE doğrulanır (K9): arayüzde alanı zorunlu
        göstermek, istemcinin gövdeyi elle kurmasını engellemez."""
        return kio.reason_error(reason)

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      kiosk_id: int = 0, detail: Any = None) -> None:
        """Yerel denetim izi. Kantin yalnız SONUCU tutuyor; bu satır ondan
        ÖNCE yazılır ve ağ koparsa geriye kalan tek kanıttır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(kiosk_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(kiosk_id or 0), action, kio.text(reason), actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), kio.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: kiosk kantinde iptal
        edilmiştir, dinleyicinin patlaması onu geri getirmez (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    async def _rows(self) -> tuple[list[dict[str, Any]], str]:
        """Kiosk listesinin TAZE hâli. `(satırlar, hata)` döner."""
        try:
            payload = await self._canteen.kiosks()
        except Exception as failure:  # noqa: BLE001 — K7
            return [], self._fail(failure)
        items = payload if isinstance(payload, list) else []
        return [kio.kiosk_row(raw, online_after=self._online_after)
                for raw in items if isinstance(raw, dict)], ""

    async def _fresh(self, kiosk_id: int) -> tuple[dict[str, Any] | None, str]:
        """Tek kioskun TAZE hâli.

        Kantinde tek kiosk okuyan bir uç YOK; taze okuma listeden yapılır. Tek
        istek eder ve karşılığında yazmadan hemen önceki gerçek durumu verir —
        kiosk aradan iptal edilmiş ya da adı değişmiş olabilir.
        """
        rows, problem = await self._rows()
        if problem:
            return None, problem
        for row in rows:
            if row["id"] == int(kiosk_id):
                return row, ""
        return None, "Kiosk bulunamadı."

    @staticmethod
    def _pairing_of(payload: Any) -> dict[str, Any]:
        """Geçidin yazma yanıtından üretilen kodu çıkarır.

        KOD YALNIZ BURADA GÖRÜNÜR ve doğrudan panele gider; hiçbir tabloya
        yazılmaz.
        """
        block = (payload or {}).get("pairing") if isinstance(payload, dict) else None
        source = block if isinstance(block, dict) else {}
        return {"code": kio.text(source.get("code")) or None,
                "expires_at": kio.text(source.get("expiresAt")) or None,
                "usable": bool(kio.text(source.get("code")))}

    @staticmethod
    def _record_of(payload: Any) -> dict[str, Any]:
        """Geçidin tekil yanıtından kiosk kaydını çıkarır."""
        if not isinstance(payload, dict):
            return {}
        value = payload.get("data")
        return value if isinstance(value, dict) else {}

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Panel açılışı: kiosk listesi, özet ve yetenek bayrakları."""
        rows, problem = await self._rows()
        if problem:
            self._log.warning("kiosk listesi okunamadı", error=problem)
            return {"ok": True, "connected": False, "error": problem, "items": [],
                    "summary": kio.summary([]), **self._contract()}

        # Yeni eşlenen kiosk VARSA olay burada doğar. Okuma ucunda yayın yapmak
        # ilk bakışta tuhaf: eşlemeyi Kontrol Merkezi başlatmıyor, kodu cihaz
        # giriyor ve kantin bize haber vermiyor. Öğrenmenin tek yolu listeye
        # bakmak; "bir önceki okumada eşli değildi" karşılaştırması da bu
        # yüzden yerel tabloda tutuluyor.
        await self._sync_seen(rows)

        return {"ok": True, "connected": True, "error": "", "items": rows,
                "summary": kio.summary(rows), **self._contract()}

    def _contract(self) -> dict[str, Any]:
        """Panelin düğmeleri çizmek için okuduğu sözleşme. YEREL: geçit düşse
        bile ekran ne yapabileceğini bilir (K7)."""
        return {
            # Yazıcı yeteneği İSTEĞE BAĞLIDIR (K7): yoksa ekran çalışmaya devam
            # eder, yalnız "kodu bas" düğmesi hiç açılmaz.
            "printer_available": self._printer is not None,
            "pairing_ttl_minutes": self._ttl_minutes,
            "online_after_minutes": self._online_after,
            "min_reason": kio.MIN_REASON,
            "max_reason": kio.MAX_REASON,
        }

    async def audit_log(self, *, kiosk_id: int = 0, limit: int = 0) -> dict[str, Any]:
        """Yerel işlem izi — kim, ne zaman, neyi denedi ve ne oldu."""
        count = max(1, min(1000, int(limit or self._audit_page)))
        try:
            if kiosk_id:
                rows = await self._store.fetch_all(
                    f"SELECT * FROM {self._audit} WHERE kiosk_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (int(kiosk_id), count))
            else:
                rows = await self._store.fetch_all(
                    f"SELECT * FROM {self._audit} "
                    "ORDER BY created_at DESC, id DESC LIMIT ?", (count,))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun mu? Hayır (K7)
            self._log.warning("işlem izi okunamadı", error=str(failure))
            return {"ok": True, "error": self._fail(failure), "items": []}

        return {"ok": True, "error": "", "items": [
            {"id": kio.as_int(row["id"]), "kiosk_id": kio.as_int(row["kiosk_id"]),
             "action": kio.text(row["action"]), "reason": kio.text(row["reason"]),
             "actor": kio.text(row["actor"]), "result": kio.text(row["result"]),
             "created_at": kio.text(row["created_at"]),
             "detail": _loads(row["detail"])}
            for row in rows]}

    # ============================================================ olay izi

    async def _sync_seen(self, rows: list[dict[str, Any]]) -> None:
        """Eşleme durumundaki değişimi yakalar ve olayı BİR KEZ yayınlar.

        HATA YUTULUR (K7): hatıra tablosu yazılamazsa olay bir sonraki okumada
        yayınlanır; ekranın açılmaması, kaçan bir bildirimden pahalıdır.
        """
        try:
            stored = await self._store.fetch_all(
                f"SELECT kiosk_id, paired_at FROM {self._seen}")
            seen = {kio.as_int(row["kiosk_id"]): kio.text(row["paired_at"])
                    for row in stored}
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("eşleme hatırası okunamadı", error=str(failure))
            return

        for row in kio.newly_paired(rows, seen):
            await self._announce(ENROLLED_EVENT, {
                "kioskId": row["id"], "name": row["name"],
                "platform": row["platform"], "appVersion": row["app_version"],
                "pairedAt": row["paired_at"],
            })
            self._log.info("kiosk eşlendi", kioskId=row["id"], name=row["name"])

        for row in rows:
            try:
                # `INSERT OR REPLACE`: tek satır tek kiosku anlatır ve geçmişi
                # yoktur — hatıra, denetim izi DEĞİLDİR. Denetim izi ayrı
                # tabloda ve orada satır silinmez.
                await self._store.execute(
                    f"INSERT OR REPLACE INTO {self._seen} "
                    "(kiosk_id, paired_at, revoked_at, updated_at) VALUES (?, ?, ?, ?)",
                    (row["id"], row["paired_at"] or "", row["revoked_at"] or "",
                     kio.now_iso()))
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("eşleme hatırası yazılamadı", kioskId=row["id"],
                                  error=str(failure))

    # ================================================================= yazma

    async def create_kiosk(self, *, name: str, reason: str, actor: str) -> dict[str, Any]:
        """Yeni kiosk açar ve İLK eşleme kodunu getirir."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = kio.text(name)
        problem = kio.name_error(clean)
        if problem:
            return {"ok": False, "error": problem}

        # TAZE OKUMA: aynı adlı ikinci bir kiosk, iptal düğmesine basan kişiyi
        # yazı tura atmaya bırakır. Kantin ad tekliği aramıyor; kapı burada.
        rows, problem = await self._rows()
        if problem:
            return {"ok": False, "error": problem}
        for row in rows:
            if row["name"].casefold() == clean.casefold() and not row["revoked"]:
                await self._record(action="create_kiosk", reason=reason, actor=actor,
                                   result=BLOCKED, detail={"name": clean})
                return {"ok": False,
                        "error": f"'{clean}' adında bir kiosk zaten var. Aynı adlı iki "
                                 "cihaz, hangisinin iptal edildiğini belirsiz bırakır."}

        await self._record(action="create_kiosk", reason=reason, actor=actor,
                           result=TRIED, detail={"name": clean})
        try:
            payload = await self._canteen.create_kiosk(name=clean)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="create_kiosk", reason=reason, actor=actor,
                               result=FAILED, detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        row = self._record_of(payload)
        kiosk_id = kio.as_int(row.get("id"))
        await self._record(action="create_kiosk", reason=reason, actor=actor,
                           result=DONE, kiosk_id=kiosk_id, detail={"name": clean})
        return {"ok": True, "error": "",
                "kiosk": kio.kiosk_row(row, online_after=self._online_after) if row else {},
                "pairing": self._pairing_of(payload)}

    async def rename_kiosk(self, kiosk_id: int, *, name: str, reason: str,
                           actor: str) -> dict[str, Any]:
        """Kiosk adını değiştirir. YALNIZ ad — eşleme bu uçtan değişmez."""
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean = kio.text(name)
        problem = kio.name_error(clean)
        if problem:
            return {"ok": False, "error": problem}

        current, problem = await self._fresh(kiosk_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["name"] == clean:
            return {"ok": True, "error": "", "changed": False, "kiosk": current,
                    "note": "Ad zaten bu; kantine istek gönderilmedi."}

        await self._record(action="rename_kiosk", reason=reason, actor=actor,
                           result=TRIED, kiosk_id=kiosk_id,
                           detail={"from": current["name"], "to": clean})
        try:
            payload = await self._canteen.rename_kiosk(int(kiosk_id), name=clean)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="rename_kiosk", reason=reason, actor=actor,
                               result=FAILED, kiosk_id=kiosk_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="rename_kiosk", reason=reason, actor=actor,
                           result=DONE, kiosk_id=kiosk_id,
                           detail={"from": current["name"], "to": clean})
        row = self._record_of(payload)
        return {"ok": True, "error": "", "changed": True,
                "kiosk": kio.kiosk_row(row, online_after=self._online_after)
                if row else {**current, "name": clean}}

    async def pairing_code(self, kiosk_id: int, *, reason: str, actor: str,
                           print_slip: bool = False) -> dict[str, Any]:
        """Yeni eşleme kodu üretir; kioskun bekleyen ESKİ kodunu geçersiz kılar.

        İPTAL EDİLMİŞ KIOSKA KOD ÜRETİLMEZ. Nedeni yetki, kolaylık değil: iptal
        `bbd_canteen_devices.devices` iznine bağlı, kod üretimi `.manage`e.
        İptal edilmiş bir kioska kod üretilebilseydi, yalnız `manage` taşıyan
        biri `devices` iznini taşıyan kişinin kararını geri alır ve iptal edilen
        cihaz kantine geri dönerdi. Kantin de aynı kapıyı kuruyor (422
        `kiosk_revoked`) — çift kapı bilinçlidir (K9).
        """
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        current, problem = await self._fresh(kiosk_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["revoked"]:
            await self._record(action="pairing_code", reason=reason, actor=actor,
                               result=BLOCKED, kiosk_id=kiosk_id,
                               detail={"revoked_at": current["revoked_at"]})
            return {"ok": False,
                    "error": "Bu kiosk iptal edilmiş; yeniden eşleştirilemez. "
                             "Kantine yeni bir kiosk kaydı açın."}

        await self._record(action="pairing_code", reason=reason, actor=actor,
                           result=TRIED, kiosk_id=kiosk_id,
                           detail={"ttl_minutes": self._ttl_minutes})
        try:
            payload = await self._canteen.new_kiosk_pairing_code(
                int(kiosk_id), ttl_minutes=self._ttl_minutes)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="pairing_code", reason=reason, actor=actor,
                               result=FAILED, kiosk_id=kiosk_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        # KOD DENETİM İZİNE YAZILMAZ — yalnız üretildiği bilgisi yazılır.
        await self._record(action="pairing_code", reason=reason, actor=actor,
                           result=DONE, kiosk_id=kiosk_id)

        pairing = self._pairing_of(payload)
        slip = await self._print_slip(current, pairing) if print_slip else {}
        return {"ok": True, "error": "", "kiosk": current, "pairing": pairing,
                "print": slip}

    async def revoke_kiosk(self, kiosk_id: int, *, reason: str, actor: str,
                           allow_destructive: bool = False) -> dict[str, Any]:
        """Eşlemeyi iptal eder: token SİLİNİR, kayıt SİLİNMEZ.

        AYRI İZİN İSTER (`bbd_canteen_devices.devices`) ve uçta ayrıca PIN
        teyidi vardır. İzin burada TEKRAR denetlenir; bu K9'un çift kapısıdır:
        izin kararı servisin kendi birim testinde sınanabilir olmalı, yoksa
        kuralın doğru olduğu ancak HTTP katmanı ayağa kaldırılarak
        gösterilebilirdi.
        """
        if not allow_destructive:
            await self._record(action="revoke_kiosk", reason=reason, actor=actor,
                               result=BLOCKED, kiosk_id=kiosk_id,
                               detail={"missing": "bbd_canteen_devices.devices"})
            return {"ok": False,
                    "error": "Kiosk iptali ayrı yetki ister: bbd_canteen_devices.devices."}
        problem = self._guard(reason)
        if problem:
            return {"ok": False, "error": problem}

        current, problem = await self._fresh(kiosk_id)
        if current is None:
            return {"ok": False, "error": problem}
        if current["revoked"]:
            await self._record(action="revoke_kiosk", reason=reason, actor=actor,
                               result=BLOCKED, kiosk_id=kiosk_id,
                               detail={"revoked_at": current["revoked_at"]})
            return {"ok": False, "error": "Bu kiosk zaten iptal edilmiş."}

        await self._record(action="revoke_kiosk", reason=reason, actor=actor,
                           result=TRIED, kiosk_id=kiosk_id,
                           detail={"name": current["name"]})
        try:
            payload = await self._canteen.revoke_kiosk(int(kiosk_id), reason=reason)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="revoke_kiosk", reason=reason, actor=actor,
                               result=FAILED, kiosk_id=kiosk_id,
                               detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        await self._record(action="revoke_kiosk", reason=reason, actor=actor,
                           result=DONE, kiosk_id=kiosk_id,
                           detail={"name": current["name"]})
        await self._announce(REVOKED_EVENT, {"kioskId": int(kiosk_id),
                                             "name": current["name"],
                                             "reason": kio.text(reason), "actor": actor})
        row = self._record_of(payload)
        return {"ok": True, "error": "", "announced": True,
                "kiosk": kio.kiosk_row(row, online_after=self._online_after)
                if row else {**current, "revoked": True}}

    # ================================================================ baskı

    async def _print_slip(self, kiosk: dict[str, Any],
                          pairing: dict[str, Any]) -> dict[str, Any]:
        """Eşleme kodunu kâğıda basar.

        NEDEN AYNI ÇAĞRIDA. Kod tek kullanımlıktır ve hiçbir yere yazılmaz;
        "sonra bas" diyen ayrı bir uç, kodu ya bir tabloda ya da bellekte
        tutmayı gerektirirdi. Basım, kodun düz görüldüğü TEK ANDA yapılır.

        DOSYA ÖZEL KLASÖRE, 0600 İLE yazılır. Kâğıt ya da dosya, kodun ömrü
        yine 10 dakikadır; süre dolduktan sonra fişte yazan sayı hiçbir kapıyı
        açmaz.

        HATA İŞİ DURDURMAZ (K7): kod zaten üretildi ve yanıtta dönüyor;
        yazıcının patlaması yöneticinin kodu ekrandan okumasını engellemez.
        """
        if self._printer is None:
            return {"printed": False,
                    "error": "Yazıcı yeteneği bu kurulumda yok; kodu ekrandan okuyun."}
        code = kio.text(pairing.get("code"))
        if not code:
            return {"printed": False, "error": "Basılacak kod yok."}

        try:
            content = build_pdf(
                title="Kantin kiosk eşleme fişi",
                subtitle=f"{kiosk.get('name') or 'Kiosk'} · kod {self._ttl_minutes} dakika geçerli",
                sections=[
                    {"kind": "tiles", "title": "Eşleme",
                     "tiles": [("Kiosk", str(kiosk.get("name") or "—")),
                               ("Kod", f"{code[:4]} {code[4:]}"),
                               ("Son geçerlilik", str(pairing.get("expires_at") or "—"))]},
                    {"kind": "note",
                     "text": "Kod TEK KULLANIMLIKTIR. Cihazın eşleme ekranına girildiğinde "
                             "yanar; süresi dolarsa yenisi üretilir. Bu fişi eşleme "
                             "tamamlandıktan sonra saklamayın."},
                ],
                footer="Kontrol Merkezi · Kantin Cihazları",
            )
            folder = report_dir(PRINT_CATEGORY, fallback=self._fallback_dir)
            path = write_private(
                folder / f"kiosk-eslesme-{kio.as_int(kiosk.get('id'))}-"
                         f"{kio.now_iso().replace(':', '')}.pdf",
                content)
            result = await self._printer.print_file(path, title=path.name, copies=1)
        except Exception as failure:  # noqa: BLE001 — yazıcı ve dosya sistemi dışarısı
            self._log.warning("eşleme fişi basılamadı", error=str(failure))
            return {"printed": False, "error": str(failure)}

        return {"printed": True, "error": "", **(result if isinstance(result, dict) else {})}

    async def printer_status(self) -> dict[str, Any]:
        """Panelin "kodu bas" düğmesini doğru anlatabilmesi için yazıcı durumu."""
        if self._printer is None:
            return {"ready": False,
                    "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            return await self._printer.status()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ready": False, "error": self._fail(failure)}


def _loads(value: Any) -> dict[str, Any]:
    """İz satırındaki JSON ayrıntısını okur. Bozuksa boş sözlük — eski bir
    satır yüzünden bütün liste patlamamalı."""
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
