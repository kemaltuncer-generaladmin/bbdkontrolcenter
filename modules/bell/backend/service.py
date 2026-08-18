"""Zil Sistemi — iş kuralları.

ÜÇ İŞ VARDIR, ÜÇÜ DE BİRBİRİNDEN AYRI:

  1. OTOMASYON — haftalık zil saatleri. Saati gelince YALNIZ teneffüs zili
     çalar. Başka hiçbir karar içermez: grup yerleştirme, hoca, salon
     otomasyona GİRMEZ.
  2. GRUP ÇAĞRISI — elle. Kullanıcı grubu seçip "Çağır" der; o grubun anonsu
     çalar ("İlayda, Hüseyin hoca ile dersiniz başlıyor."). Zil çalmaz.
  3. ELLE ZİL — yalnız zil sesi.

ZİLDEN SONRA ANONS GEÇMEZ (18.08.2026, kullanıcı kararı). Otomatik zil bir
zamanlar arkasından "Lütfen derse geçiniz." çalıyordu; o yol tümüyle kaldırıldı.
Kapatılabilir bir seçenek de BIRAKILMADI: istenen "şimdilik sessiz" değil, hiç
çalmaması — ekranda duran bir onay kutusu yanlışlıkla geri açılabilirdi.
Anons yalnız elle basılan grup çağrısında duyulur.

SES ÖNCEDEN ÜRETİLİR. Çalma anında Vertex'e gidilmez; `voices.py` metin
değiştiğinde üretip diske yazar. Sesi hazır olmayan bir çağrı YAPILMAZ ve
nedeni günlüğe geçer — basılabilen ama sessizlikle biten bir düğme, arızanın
en kötü biçimidir.

ASIL HOPARLÖR WINDOWS AJANINDADIR. Yerel `audio` yeteneği ikinci hedeftir;
ayarla kapatılabilir. İkisinin sonucu da ayrı ayrı günlüğe yazılır.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import Trigger

from .bridge import BellBridge
from .voices import VoiceLibrary, number, render

#: Gün anahtarları — `km_platform.scheduler` ile aynı sıra (pazartesi = 0).
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

#: Önizlemede gövdeye gömülebilecek en büyük ses. Zil birkaç saniyedir;
#: bundan büyüğü yanlış dosyadır ve base64 ile üçte bir daha şişer.
MAX_PREVIEW_BYTES = 8 * 1024 * 1024

#: Uzantı → MIME. Kabuk `new Audio(dataUri)` derken türü buradan öğrenir.
MIME_BY_SUFFIX = {
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
}

DEFAULT_SOUND = "classic_electric"
DEFAULT_VOLUME = 90

#: Grup çağrısı şablonu — TOPLU dersler (Mezun, TYT, AYT, LGS, 7. sınıf).
#: `{grup}` yerine grubun adı yazılır. Çoğul hitap: "dersiniz", "geçiniz".
DEFAULT_CALL_TEXT = (
    "{grup}, Hüseyin hoca ile dersiniz başlıyor. Lütfen hocanın yanına geçiniz."
)

#: ÖZEL ders şablonu — tek öğrenci (İlayda, Hidayet, Zehra).
#: Tekil hitap: "dersin", "geç". Aynı cümleyi kullanmak yanlış olurdu; tek
#: kişiye "dersiniz başlıyor" demek de "grubu" demek kadar tuhaf.
DEFAULT_SOLO_TEXT = (
    "{grup}, Hüseyin hoca ile özel dersin başlıyor. Lütfen hocanın yanına geç."
)

#: Grup türleri. Ad'dan tahmin edilmez, açıkça saklanır (bkz. 003 göçü).
KINDS = ("grup", "ozel")

TEXT_MAX = 300
LABEL_MAX = 40
NAME_MAX = 60

#: Bir güne en çok bu kadar zil saati. Elle bozulmuş veri ekranı boğmasın.
TIME_MAX_PER_DAY = 40
GROUP_MAX = 200

#: Köprüye gönderilen ses kimliği: dosya içeriğinin özeti. İçerik adresli
#: olduğu için ajan indirdiğini doğrulayabiliyor ve aynı dosya iki kez inmiyor.
CONTENT_ID_LENGTH = 16

_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clock(value: Any) -> str | None:
    """'HH:MM' doğrular ve iki haneye tamamlar. Geçersizse `None`."""
    match = _CLOCK.match(str(value or "").strip())
    if match is None:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def content_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:CONTENT_ID_LENGTH]


#: Türkçede noktalı/noktasız i, `casefold()` ile eşleşmez: "İlayda".casefold()
#: birleşik noktalı bir i üretir, "ilayda" ise düz i. Bu yüzden karşılaştırmadan
#: önce iki büyük harf elle eşlenir. Yoksa kullanıcı "İlayda" ve "ilayda" diye
#: iki ayrı grup açabilir ve ikisi ekranda aynı görünür.
_FOLD = str.maketrans({"I": "ı", "İ": "i"})


def fold(text: str) -> str:
    """Türkçe duyarlı küçültme — yalnız ad karşılaştırmak için."""
    return str(text or "").translate(_FOLD).casefold()


def call_text(settings: dict[str, Any], name: str, kind: str) -> str:
    """Bir grubun çağrı cümlesi. Türe göre şablon seçilir.

    TEK YER: "hangi metin çalınacak" sorusunun cevabı burada. Beş ayrı yerde
    `settings["texts"]["call" if ... else "solo"]` yazılsaydı, biri
    unutulduğunda özel ders öğrencisine çoğul hitap eden bir anons çıkardı.
    """
    key = "solo" if kind == "ozel" else "call"
    return render(settings["texts"][key], name)


class BellService:
    def __init__(self, *, store: Any, log: Any, audio: Any, scheduler: Any,
                 voices: VoiceLibrary, bridge: BellBridge, notify: Any = None,
                 play_locally: bool = True) -> None:
        self._store = store
        self._log = log
        self._audio = audio
        self._scheduler = scheduler
        self._voices = voices
        self._bridge = bridge
        self._notify = notify
        self._default_play_locally = play_locally

        self._settings_table = store.table("settings")
        self._time_table = store.table("time")
        self._group_table = store.table("group")
        self._log_table = store.table("log")

        self._sync_task: asyncio.Task[None] | None = None

    # ================================================================= ayar

    def _defaults(self) -> dict[str, Any]:
        return {
            "version": 3,
            "enabled": True,
            "bellSound": DEFAULT_SOUND,
            "volume": DEFAULT_VOLUME,
            "playLocally": self._default_play_locally,
            "texts": {
                "call": DEFAULT_CALL_TEXT,
                "solo": DEFAULT_SOLO_TEXT,
            },
        }

    def normalize(self, state: Any) -> dict[str, Any]:
        """Eksik/bozuk ayarı tamamlar.

        0.1 sürümünün `groups` bloğu (ders takvimi grup kimliklerine bağlıydı)
        sessizce düşer: o kimlikler artık yok, taşınabilecek bir anlamı da yok.

        0.2'nin `texts.lesson` alanı da aynı biçimde düşer — zilden sonra anons
        çalınmıyor. Kayıtlı metin taşınmaz: taşınsaydı ekranda görünmeyen ama
        veride duran bir cümle olurdu ve ilk okuyan onun çaldığını sanardı.
        """
        base = self._defaults()
        if not isinstance(state, dict):
            return base

        raw_texts = state.get("texts")
        texts: dict[str, Any] = raw_texts if isinstance(raw_texts, dict) else {}

        # Ses düzeyi `number()` ile okunur: eski kod `str(v).isdigit()` deneyip
        # 85.0 gibi bir değeri sessizce varsayılana çeviriyordu ve `int(None)`
        # ile patlamaya açıktı.
        volume = int(number(state.get("volume"), float(base["volume"])))

        return {
            "version": 3,
            "enabled": state.get("enabled") is not False,
            "bellSound": str(state.get("bellSound") or base["bellSound"])[:120],
            "volume": max(0, min(100, volume)),
            "playLocally": bool(state.get("playLocally", base["playLocally"])),
            "texts": {
                "call": str(texts.get("call") or DEFAULT_CALL_TEXT)[:TEXT_MAX].strip(),
                "solo": str(texts.get("solo") or DEFAULT_SOLO_TEXT)[:TEXT_MAX].strip(),
            },
        }

    async def settings(self) -> dict[str, Any]:
        row = await self._store.fetch_one(
            f"SELECT payload FROM {self._settings_table} WHERE id = 1"
        )
        if row is None:
            return self._defaults()
        try:
            return self.normalize(json.loads(str(row["payload"])))
        except json.JSONDecodeError:
            self._log.warning("zil ayarı çözülemedi, varsayılana dönülüyor")
            return self._defaults()

    async def save_settings(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        before = await self.settings()
        clean = self.normalize(payload)

        await self._store.execute(
            f"INSERT INTO {self._settings_table} (id, payload, updated_at, updated_by) "
            f"VALUES (1, ?, ?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
            f"updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (json.dumps(clean, ensure_ascii=False), _now(), actor),
        )

        # Metin değiştiyse o metnin sesi yeniden üretilmeli. Çağrı şablonu
        # HER GRUP için ayrı ses demektir.
        if (clean["texts"]["call"] != before["texts"]["call"]
                or clean["texts"]["solo"] != before["texts"]["solo"]):
            await self._ensure_group_voices(clean)

        await self.reschedule()
        return {"ok": True, "settings": clean}

    # ================================================================ saatler

    async def times(self) -> dict[str, list[dict[str, Any]]]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._time_table} ORDER BY time"
        )
        week: dict[str, list[dict[str, Any]]] = {day: [] for day in DAYS}
        for row in rows:
            day = str(row["day"])
            if day in week:
                week[day].append({"id": str(row["id"]), "time": str(row["time"]),
                                  "label": str(row["label"])})
        return week

    async def save_times(self, week: Any, *, actor: str) -> dict[str, Any]:
        """Haftalık saatleri TÜMÜYLE değiştirir.

        Kısmi güncelleme yok: ekran zaten tabloyu bir bütün olarak gönderiyor ve
        satır satır fark almak, iki makinede açık panelin birbirini yarım ezmesi
        demek olurdu.
        """
        clean: list[tuple[str, str, str, str]] = []
        seen: set[tuple[str, str]] = set()

        source = week if isinstance(week, dict) else {}
        for day in DAYS:
            entries = source.get(day)
            if not isinstance(entries, list):
                continue
            for entry in entries[:TIME_MAX_PER_DAY]:
                if not isinstance(entry, dict):
                    continue
                value = clock(entry.get("time"))
                if value is None or (day, value) in seen:
                    continue          # geçersiz saat ve tekrar sessizce düşer
                seen.add((day, value))
                label = str(entry.get("label") or "")[:LABEL_MAX].strip()
                clean.append((f"{day}-{value.replace(':', '')}", day, value, label))

        await self._store.execute(f"DELETE FROM {self._time_table}")
        for row in clean:
            await self._store.execute(
                f"INSERT INTO {self._time_table} (id, day, time, label) VALUES (?, ?, ?, ?)",
                row,
            )

        self._log.info("zil saatleri güncellendi", count=len(clean), actor=actor)
        await self.reschedule()
        return {"ok": True, "times": await self.times()}

    # ================================================================ gruplar

    async def groups(self) -> list[dict[str, Any]]:
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._group_table} WHERE deleted_at = '' ORDER BY sort, name"
        )
        return [{"id": str(row["id"]), "name": str(row["name"]),
                 "kind": str(row["kind"] or "grup"), "sort": int(row["sort"])}
                for row in rows]

    async def add_group(self, name: str, *, kind: str = "grup",
                        actor: str) -> dict[str, Any]:
        clean = " ".join(str(name or "").split())[:NAME_MAX]
        if not clean:
            return {"ok": False, "detail": "Grup adı boş olamaz."}
        kind = kind if kind in KINDS else "grup"

        current = await self.groups()
        if len(current) >= GROUP_MAX:
            return {"ok": False, "detail": f"En çok {GROUP_MAX} grup olabilir."}
        if any(fold(item["name"]) == fold(clean) for item in current):
            return {"ok": False, "detail": f"'{clean}' adında bir grup zaten var."}

        # Aynı adla daha önce kaldırılmış bir grup varsa onu geri getiriyoruz:
        # yeni satır açmak, aynı adın iki kimlikle dolaşması demek olurdu.
        revived = await self._store.fetch_one(
            f"SELECT id FROM {self._group_table} "
            f"WHERE deleted_at != '' AND name = ? LIMIT 1", (clean,)
        )
        if revived is not None:
            group_id = str(revived["id"])
            await self._store.execute(
                f"UPDATE {self._group_table} SET deleted_at = '', kind = ? WHERE id = ?",
                (kind, group_id),
            )
        else:
            group_id = _slug(clean, {item["id"] for item in current})
            await self._store.execute(
                f"INSERT INTO {self._group_table} (id, name, kind, sort, created_at) "
                f"VALUES (?, ?, ?, ?, ?)",
                (group_id, clean, kind, len(current), _now()),
            )

        settings = await self.settings()
        await self._voices.require(call_text(settings, clean, kind))
        self._log.info("grup eklendi", group=clean, kind=kind, actor=actor)
        return {"ok": True, "id": group_id, "groups": await self.groups()}

    async def rename_group(self, group_id: str, name: str, *,
                           kind: str = "", actor: str) -> dict[str, Any]:
        clean = " ".join(str(name or "").split())[:NAME_MAX]
        if not clean:
            return {"ok": False, "detail": "Grup adı boş olamaz."}

        current = await self.groups()
        mevcut = next((item for item in current if item["id"] == group_id), None)
        if mevcut is None:
            return {"ok": False, "detail": "Grup bulunamadı."}
        # Tür verilmediyse dokunulmaz: ad değiştirmek türü sıfırlamamalı.
        kind = kind if kind in KINDS else str(mevcut["kind"])
        if any(item["id"] != group_id and fold(item["name"]) == fold(clean)
               for item in current):
            return {"ok": False, "detail": f"'{clean}' adında başka bir grup var."}

        await self._store.execute(
            f"UPDATE {self._group_table} SET name = ?, kind = ? WHERE id = ?",
            (clean, kind, group_id),
        )
        # ESKİ SES SİLİNMEZ: aynı ada dönülürse yeniden üretilmesin. Birkaç yüz
        # kilobayt, gereksiz bir bulut çağrısından ucuzdur.
        settings = await self.settings()
        await self._voices.require(call_text(settings, clean, kind))
        self._log.info("grup adı değişti", group=clean, kind=kind, actor=actor)
        return {"ok": True, "groups": await self.groups()}

    async def remove_group(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """Grubu kaldırır. SATIR SİLİNMEZ — `deleted_at` yazılır."""
        row = await self._store.fetch_one(
            f"SELECT name FROM {self._group_table} WHERE id = ? AND deleted_at = ''",
            (group_id,),
        )
        if row is None:
            return {"ok": False, "detail": "Grup bulunamadı."}

        await self._store.execute(
            f"UPDATE {self._group_table} SET deleted_at = ? WHERE id = ?", (_now(), group_id)
        )
        self._log.info("grup kaldırıldı", group=str(row["name"]), actor=actor)
        return {"ok": True, "groups": await self.groups()}

    async def _ensure_group_voices(self, settings: dict[str, Any]) -> None:
        for group in await self.groups():
            await self._voices.require(
                call_text(settings, group["name"], group["kind"])
            )

    async def rebuild_voices(self) -> int:
        """Eksik/hatalı sesleri yeniden sıraya alır; kaç tane olduğunu döner.

        Önce bugün gerekli olan metinler istenir (arada eklenmiş bir grup
        hiç kayıt açmamış olabilir), sonra kayıtlı ama kullanılamaz durumdakiler
        taranır.
        """
        settings = await self.settings()
        await self._ensure_group_voices(settings)
        return await self._voices.sweep()

    # ============================================================== yetenek

    async def week(self) -> dict[str, Any]:
        """`bell.week` — Ders Takvimi ekranının okuduğu salt okunur özet."""
        return {"times": await self.times(), "groups": await self.groups()}

    # ================================================================ durum

    async def state(self) -> dict[str, Any]:
        settings = await self.settings()
        times = await self.times()
        groups = await self.groups()
        entries = await self._voices.entries()

        for group in groups:
            text = call_text(settings, group["name"], group["kind"])
            group["text"] = text
            group["hash"] = self._voices.key(text)
            group["voice"] = _voice_state(entries, group["hash"])

        audio_state = self._audio.available() if self._audio else {"ready": False}
        scheduler_state = self._scheduler.state() if self._scheduler else {"running": False}
        agent = await self._bridge.try_status()

        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._log_table} ORDER BY id DESC LIMIT 60"
        )

        total_times = sum(len(items) for items in times.values())
        missing = [group["name"] for group in groups if group["voice"]["state"] != "ready"]

        return {
            "settings": settings,
            "times": times,
            "groups": groups,
            "sounds": [item for item in (self._audio.sounds() if self._audio else [])
                       if not str(item["name"]).startswith("anons-")],
            "audio": audio_state,
            "scheduler": scheduler_state,
            "agent": agent,
            "queue": self._voices.state(),
            "log": [dict(row) for row in rows],
            # Zil çalabilir mi? Ajan, saat ve ana şalter — üçü de gerekli.
            # Yerel çalma açıksa ajan olmadan da ses çıkar, o da sayılır.
            "ready": bool(settings["enabled"]) and total_times > 0
            and (bool(agent.get("online")) or bool(settings["playLocally"])),
            "missingVoices": missing,
        }

    # ============================================================ zamanlama

    async def reschedule(self) -> dict[str, Any]:
        """Haftalık saatlerden tetikleyici tablosu kurar.

        Her zil saati İKİ tetikleyici doğurur:
          · `dispatch` — zilden bir dakika önce; komutu köprüye yazar. Komut
            `playAt` damgası taşır, ajan o ana kadar bekler. Zil bu yüzden
            saniye hassasiyetiyle çalar, sorgulama gecikmesine takılmaz.
          · `local`    — tam saatinde; yerel hoparlörden çalar (açıksa).
        """
        if self._scheduler is None:
            return {"ok": False, "detail": "Zamanlayıcı yeteneği yok."}

        settings = await self.settings()
        if not settings["enabled"]:
            self._scheduler.clear("bell")
            return {"ok": True, "triggers": 0, "detail": "ana şalter kapalı"}

        lead = max(1, (self._bridge.lead_seconds + 59) // 60)   # dakikaya yuvarla
        triggers: list[Trigger] = []

        for day, entries in (await self.times()).items():
            for entry in entries:
                early_day, early_time = _shift(day, entry["time"], lead)
                label = f"{entry['label'] or 'zil'} {entry['time']}"
                triggers.append(Trigger(
                    day=early_day, time=early_time, label=f"{label} · gönderim",
                    payload={"phase": "dispatch", "at": entry["time"]},
                ))
                if settings["playLocally"]:
                    triggers.append(Trigger(
                        day=day, time=entry["time"], label=f"{label} · yerel",
                        payload={"phase": "local", "at": entry["time"]},
                    ))

        count = self._scheduler.set_plan("bell", triggers, self._on_trigger)
        return {"ok": True, "triggers": count}

    async def _on_trigger(self, trigger: Any) -> None:
        payload = getattr(trigger, "payload", {}) or {}
        settings = await self.settings()
        items = await self._bell_items(settings)
        if not items:
            return

        if str(payload.get("phase")) == "local":
            await self._play_locally(items, settings, kind="zil", edge="plan")
            return

        play_at = _next_occurrence(str(payload.get("at") or ""))
        result = await self._bridge.try_send(items, play_at=play_at)
        await self._record("zil", "plan", ", ".join(item["name"] for item in items),
                           result["ok"], f"ajan · {result['detail']}")

    async def _bell_items(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        """Otomatik zilin çalacağı adımlar — YALNIZ zil sesi.

        Buraya bir zamanlar zilin arkasına "derse geçiniz" anonsu ekleniyordu;
        artık eklenmiyor. Liste dönmesi korunuyor: köprü komutu her hâlükârda
        adım dizisi bekliyor ve zil sesi bulunamazsa boş dönmek gerekiyor.
        """
        bell = self._resolve_sound(settings["bellSound"])
        if bell is None:
            await self._record("zil", "plan", settings["bellSound"], False,
                               "Zil sesi dosyası bulunamadı.")
            return []
        return [_item("zil", bell, settings["volume"])]

    # ================================================================= çalma

    async def ring_now(self, *, actor: str) -> dict[str, Any]:
        """Yalnız zil. Anons yok."""
        settings = await self.settings()
        path = self._resolve_sound(settings["bellSound"])
        if path is None:
            return await self._fail("zil", "manuel", settings["bellSound"],
                                    "Zil sesi dosyası bulunamadı.")
        items = [_item("zil", path, settings["volume"])]
        return await self._dispatch(items, settings, kind="zil", edge="manuel",
                                    name=path.name, actor=actor)

    async def call_group(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """Grup çağrısı: yalnız anons, zil YOK (kullanıcı kararı)."""
        settings = await self.settings()
        groups = await self.groups()
        group = next((item for item in groups if item["id"] == group_id), None)
        if group is None:
            return await self._fail("cagri", "manuel", group_id, "Grup bulunamadı.")

        text = call_text(settings, group["name"], group["kind"])
        name = await self._voices.ready_file(self._voices.key(text))
        if not name:
            entry = await self._voices.entry(self._voices.key(text))
            detail = str(entry["error"]) if entry and entry["error"] \
                else "Anons sesi henüz üretilmedi."
            return await self._fail("cagri", "manuel", group["name"], detail)

        items = [_item("anons", self._sounds_path() / name, settings["volume"])]
        return await self._dispatch(items, settings, kind="cagri", edge="manuel",
                                    name=group["name"], actor=actor)

    async def preview_source(self, sound: str) -> dict[str, Any]:
        """Önizleme için sesin ADRESİNİ verir; ÇALMAZ.

        Ekrandaki "dinle" düğmesi budur ve okulun hoparlörüne gitmez.

        ÇALMA İŞİ KABUĞA GEÇTİ (ADR 0026). Eskiden burada `audio.play`
        çağrılıyordu ve bu, backend kullanıcının makinesinde koştuğu sürece
        doğruydu. Backend sunucuya taşınınca aynı çağrı sesi VERİ MERKEZİNDE
        çalmaya kalkardı — kimsenin duymadığı bir hoparlörde, üstelik "çaldı"
        diye başarı dönerek. Uç artık adresi veriyor, sesi kabuk çalıyor.
        """
        path = self.sound_path(sound)
        if path is None:
            return {"ok": False, "detail": "Ses bulunamadı."}
        try:
            raw = path.read_bytes()
        except OSError as failure:
            return {"ok": False, "detail": f"Ses okunamadı: {failure}"}
        if len(raw) > MAX_PREVIEW_BYTES:
            return {"ok": False, "detail": "Ses önizleme için fazla büyük."}

        # VERİ URI'Sİ, AYRI BİR İNDİRME UCU DEĞİL. Kabuğun istek katmanı
        # (`core_request`) gövdeyi METİN olarak taşıyor; ikili veri oradan
        # sağlam geçmez. Base64 gövdeye gömmek hem tek istek hem de kabukta
        # `new Audio(dataUri)` ile doğrudan çalınabilir bir sonuç verir —
        # tarayıcıda ve Tauri'de aynı yol.
        mime = MIME_BY_SUFFIX.get(path.suffix.lower(), "audio/wav")
        return {
            "ok": True,
            "name": path.name,
            "dataUri": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
        }

    def sound_path(self, name: str) -> Path | None:
        """Ses dosyasının diskteki yolu — yoksa `None`.

        Ad ÇÖZÜLÜR, birleştirilmez: `data/sounds` ile birleştirmek `../` ile
        klasör dışına çıkmaya izin verirdi.
        """
        return self._resolve_sound(name)

    async def _dispatch(self, items: list[dict[str, Any]], settings: dict[str, Any],
                        *, kind: str, edge: str, name: str, actor: str) -> dict[str, Any]:
        """Komutu ajana yollar, gerekirse yerelden de çalar, ikisini de günlüğe yazar."""
        result = await self._bridge.try_send(items, play_at="")
        detail = f"ajan · {result['detail']}"

        if settings["playLocally"]:
            local = await self._play_locally(items, settings, kind=kind, edge=edge,
                                             record=False)
            detail = f"{detail} · yerel · {local['detail']}"
            ok = result["ok"] or local["ok"]
        else:
            ok = result["ok"]

        await self._record(kind, edge, name, ok, detail, actor=actor)
        return {"ok": ok, "detail": detail}

    async def _play_locally(self, items: list[dict[str, Any]], settings: dict[str, Any],
                            *, kind: str, edge: str, record: bool = True) -> dict[str, Any]:
        """Sesleri sırayla yerel aygıttan çalar."""
        if self._audio is None:
            return {"ok": False, "detail": "ses yeteneği yok"}

        played: list[str] = []
        for item in items:
            try:
                await self._audio.play(item["name"], volume=item["volume"])
            except Exception as failure:  # noqa: BLE001 — ses dışarısı
                detail = f"{item['name']}: {failure}"
                if record:
                    await self._record(kind, edge, item["name"], False, detail)
                return {"ok": False, "detail": detail}
            played.append(item["name"])

        detail = " + ".join(played) or "çalınacak ses yok"
        if record:
            await self._record(kind, edge, detail, True, "yerel hoparlör")
        return {"ok": True, "detail": detail}

    async def _fail(self, kind: str, edge: str, name: str, detail: str) -> dict[str, Any]:
        await self._record(kind, edge, name, False, detail)
        return {"ok": False, "detail": detail}

    async def _record(self, kind: str, edge: str, name: str, ok: bool,
                      detail: str, *, actor: str = "") -> None:
        await self._store.execute(
            f"INSERT INTO {self._log_table} "
            f"(at, group_id, group_name, edge, sound, ok, detail, kind) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), actor, name, edge, name, 1 if ok else 0, detail[:500], kind),
        )
        if not ok:
            self._log.warning("zil çalınamadı", kind=kind, detail=detail[:200])

    # ============================================================ ses dosyası

    def _sounds_path(self) -> Path:
        state = self._audio.available() if self._audio else {}
        return Path(str(state.get("soundsPath") or "data/sounds"))

    def _resolve_sound(self, name: str) -> Path | None:
        if self._audio is None:
            return None
        return self._audio.resolve(name)

    async def upload_sound(self, name: str, data: bytes, *, actor: str) -> dict[str, Any]:
        """Kullanıcının yüklediği teneffüs zilini `data/sounds` altına yazar."""
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._")
        if not safe.lower().endswith((".wav", ".ogg", ".mp3", ".flac", ".oga")):
            return {"ok": False, "detail": "Yalnız wav, ogg, mp3, flac dosyaları."}
        if safe.startswith("anons-"):
            return {"ok": False, "detail": "'anons-' önekli ad ayrılmıştır."}
        if not data:
            return {"ok": False, "detail": "Dosya boş."}

        target = self._sounds_path() / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(target.suffix + ".part")
        staging.write_bytes(data)
        staging.replace(target)

        self._log.info("zil sesi yüklendi", file=safe, bytes=len(data), actor=actor)
        return {"ok": True, "name": safe, "stem": target.stem}

    # ============================================ ajan ses kitaplığı senkronu

    def start_sync(self, interval: float = 60.0) -> None:
        """Ajanın ses kitaplığını arka planda eşitler.

        Ayrı bir döngü olmasının nedeni: ses üretimi, dosya yükleme ve grup
        değişikliği birbirinden bağımsız anlarda oluyor. Her birine ayrı ayrı
        "şimdi yükle" çağrısı asmak, biri unutulduğunda ajanda eksik ses
        bırakırdı. Tek döngü kendini onarır.
        """
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = asyncio.create_task(self._sync_loop(interval),
                                                  name="km-bell-sync")

    async def stop_sync(self) -> None:
        if self._sync_task is None:
            return
        self._sync_task.cancel()
        try:
            await self._sync_task
        except asyncio.CancelledError:
            pass
        self._sync_task = None

    async def _sync_loop(self, interval: float) -> None:
        while True:
            try:
                await self.sync_sounds()
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 — döngü ölmemeli (K7)
                self._log.warning("ses eşitlemesi patladı", error=str(failure))
            await asyncio.sleep(interval)

    async def needed_sounds(self) -> dict[str, Path]:
        """Ajanın yerelinde bulunması gereken dosyalar: içerik özeti → yol."""
        settings = await self.settings()
        wanted: dict[str, Path] = {}

        bell = self._resolve_sound(settings["bellSound"])
        if bell is not None:
            wanted[content_id(bell.read_bytes())] = bell

        # Yalnız grup çağrılarının anonsu. Zilden sonraki anons kalktığı için
        # o ses ajanın yerelinde de durmaz; `sync_sounds` onu köprüden siler.
        texts = [call_text(settings, group["name"], group["kind"])
                 for group in await self.groups()]

        root = self._sounds_path()
        for text in texts:
            name = await self._voices.ready_file(self._voices.key(text))
            if not name:
                continue
            path = root / name
            try:
                wanted[content_id(path.read_bytes())] = path
            except OSError:
                continue          # arada silinmiş; `sweep` yeniden üretir
        return wanted

    async def sync_sounds(self) -> dict[str, Any]:
        """Köprüde eksik olan sesleri yükler, gereksizleri sildirir.

        KARŞILAŞTIRMA KÖPRÜYLE YAPILIR, ajanla değil. Ajanın yerelindeki liste
        onun kendi işidir — köprüden eksiklerini kendisi indirir. Kontrol
        Merkezi'nin sorumluluğu köprünün tam olması. İkisi karıştırılırsa ajan
        ilk kez bağlanana dek her turda bütün sesler yeniden yüklenir.
        """
        wanted = await self.needed_sounds()
        status = await self._bridge.try_status()
        if status.get("error"):
            return {"ok": False, "detail": status["error"]}

        present = set(status.get("bridgeSounds") or [])
        missing = [(key, path) for key, path in wanted.items() if key not in present]

        sent = 0
        for key, path in missing:
            try:
                await self._bridge.upload(path.name, path.read_bytes())
                sent += 1
            except Exception as failure:  # noqa: BLE001 — köprü dışarısı
                self._log.warning("ses köprüye yüklenemedi", file=path.name,
                                  error=str(failure))

        # Grup adı değiştikçe eski anonslar birikir; köprüde artık gerekmeyeni
        # bırakmak, ajanın da onu indirip saklaması demek olurdu.
        stale = present - set(wanted)
        removed = 0
        if stale and not wanted:
            # BOŞ `keep` İLE TEMİZLİK YAPILMAZ. Köprüye "hiçbirini tutma"
            # demek, oradaki BÜTÜN sesleri sildirmek olurdu — ve buraya
            # düşmenin normal yolu tam da tehlikeli olanı: sesler henüz
            # üretilmemiştir (taze kurulum, kalıcı disk yeni bağlanmış,
            # Vertex sırası daha bitmemiş). O anda silmek, okulun zilini
            # sessizleştirirdi.
            #
            # Köprü zaten reddediyordu (Laravel `required` boş diziyi eksik
            # sayar, 422) ama bu bir kaza sonucu koruma; niyeti burada yazıp
            # çağrıyı hiç yapmıyoruz.
            self._log.info("köprü temizliği atlandı — tutulacak ses listesi boş",
                           stale=len(stale))
        elif stale:
            try:
                result = await self._bridge.prune(sorted(wanted))
                removed = int(result.get("removed") or 0)
            except Exception as failure:  # noqa: BLE001 — köprü dışarısı
                self._log.warning("eski sesler silinemedi", error=str(failure))

        if sent or removed:
            self._bridge.forget_status()    # sayılar taze okunsun
            self._log.info("köprü ses kitaplığı eşitlendi", uploaded=sent,
                           removed=removed, total=len(wanted))
        return {"ok": True, "uploaded": sent, "removed": removed, "total": len(wanted)}

    # ================================================================ açılış

    async def bootstrap(self) -> dict[str, Any]:
        """Açılışta: eksik sesleri sıraya al ve tetikleyicileri kur.

        Eşitleme döngüsünü BAŞLATMAZ. Hazırlık ile uzun ömürlü döngü ayrı
        durur: çağıran taraf (modül girişi) döngüyü açıkça başlatır, testler
        de arka planda dönen bir görevle yarışmak zorunda kalmaz.
        """
        settings = await self.settings()
        await self._ensure_group_voices(settings)
        return await self.reschedule()


# ------------------------------------------------------------------- yardım


def _item(kind: str, path: Path, volume: int) -> dict[str, Any]:
    """Köprüye giden tek çalma adımı."""
    data = path.read_bytes()
    return {"kind": kind, "hash": content_id(data), "name": path.name,
            "volume": int(volume)}


def _voice_state(entries: dict[str, dict[str, Any]], hash_: str) -> dict[str, Any]:
    """Bir sesin durumu: hazır / üretiliyor / hata / hiç istenmemiş.

    Ekran bu dört durumu ayırt edebilmeli: "üretiliyor" beklemek demektir,
    "hata" müdahale demektir, ikisini tek renge indirmek kullanıcıyı boşuna
    bekletir.
    """
    row = entries.get(hash_)
    if row is None:
        return {"state": "missing", "detail": "henüz istenmedi", "seconds": 0}
    if row["error"]:
        return {"state": "error", "detail": str(row["error"]), "seconds": 0}
    if row["file"]:
        return {"state": "ready", "detail": str(row["file"]),
                "seconds": float(row["seconds"] or 0)}
    return {"state": "pending", "detail": "üretiliyor", "seconds": 0}


def _slug(name: str, taken: set[str]) -> str:
    """Ad'dan okunur bir kimlik. Çakışırsa sayı eklenir."""
    table = str.maketrans("çğıöşüÇĞÖŞÜ", "cgiosucgosu")
    base = re.sub(r"[^a-z0-9]+", "-", fold(name).translate(table)).strip("-")
    base = base[:40] or "grup"
    candidate = base
    index = 2
    while candidate in taken:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _shift(day: str, time: str, minutes: int) -> tuple[str, str]:
    """Saati `minutes` kadar geri alır; gün sınırını aşarsa günü de kaydırır."""
    hour, minute = (int(part) for part in time.split(":"))
    total = hour * 60 + minute - minutes
    index = DAYS.index(day)
    if total < 0:
        total += 24 * 60
        index = (index - 1) % 7
    return DAYS[index], f"{total // 60:02d}:{total % 60:02d}"


def _next_occurrence(time: str) -> str:
    """'HH:MM' → ajanın bekleyeceği yerel ISO damgası.

    Tetikleyici zil saatinden bir dakika önce çalıştığı için hedef HER ZAMAN
    ileridedir; yine de geçmişte kalmışsa (uykudan geç uyanma) ertesi güne
    atlanmaz — komut düşer, çünkü geç çalan zil çalmayan zilden kötüdür.
    """
    parsed = clock(time)
    if parsed is None:
        return ""
    hour, minute = (int(part) for part in parsed.split(":"))
    now = datetime.now().astimezone()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now - timedelta(minutes=5):
        return ""          # çok geç kalınmış: ajan hemen çalsın, kaydırma yok
    return target.isoformat(timespec="seconds")
