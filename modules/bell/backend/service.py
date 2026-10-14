"""Zil Sistemi — iş kuralları.

ÜÇ İŞ VARDIR, ÜÇÜ DE BİRBİRİNDEN AYRI:

  1. OTOMASYON — haftalık zil saatleri. Saati gelince zil çalar, hemen
     arkasından TÜRE GÖRE anons gider. Başka hiçbir karar içermez: grup
     yerleştirme, hoca, salon otomasyona GİRMEZ.
  2. GRUP ÇAĞRISI — elle. Kullanıcı grubu seçip "Çağır" der; o grubun anonsu
     çalar ("İlayda, Hüseyin hoca ile dersiniz başlıyor."). Zil çalmaz.
  3. ELLE ZİL — yalnız zil sesi.

ZİLDEN SONRA ANONS GEÇER, TÜRE GÖRE (ADR 0027). Ders zilinin arkasından
"İyi dersler.", teneffüs zilinin arkasından "İyi teneffüsler." gider. Tür zil
saatinde AÇIKÇA saklanır (`kind`, 004 göçü); etiketten tahmin edilmez, çünkü
"mola" yazan bir satırın ders mi teneffüs mü olduğunu hiçbir kural güvenilir
biçimde bilemez.

ANONS ZİLİ SUSTURAMAZ. Sesi hazır değilse zil yalnız başına çalar ve sebep
günlüğe yazılır; okulu sessiz bırakmak eksik bir cümleden çok daha kötüdür.
Bir türün metni boşaltılırsa o zilin arkasından anons gitmez — bu geçerli bir
seçimdir ve ekranda görünür (gizli bir onay kutusu değil).

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
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from km_sdk import Trigger

from . import plan as planning
from .bridge import BellBridge
from .plan import PlanBook, PlanError, render_call, solo
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

#: ÖĞRETMENSİZ (genel) çağrı şablonları — zil planındaki oturumun öğretmeninin
#: anons adı yoksa ya da kesin metnin sesi henüz hazır değilse bunlar okunur
#: (sözleşme §3.4). Grup adı değişmedikçe ses değişmez; her grup için bir kez
#: üretilir.
DEFAULT_CALL_GENERIC_TEXT = "{grup}, dersiniz başlıyor. Lütfen sınıfınıza geçiniz."
DEFAULT_SOLO_GENERIC_TEXT = "{grup}, özel dersin başlıyor."

#: Bugünkü şablonlarda SABİT YAZILI öğretmen adı. KENDİLİĞİNDEN DEĞİŞTİRİLMEZ
#: (kullanıcı onayı gerekir); panel "şablonda {hoca} yok" uyarısı ve tek
#: tıkla aşağıdaki öneriyi gösterir.
HARDCODED_TEACHER = "Hüseyin hoca"
SUGGESTED_CALL_TEXT = "{grup}, {hoca} ile dersiniz başlıyor. Lütfen hocanın yanına geçiniz."
SUGGESTED_SOLO_TEXT = "{grup}, {hoca} ile özel dersin başlıyor. Lütfen hocanın yanına geç."

#: Oturum çağrıları (zil planı) — VARSAYILAN KAPALI. Akademi 14 günü itip
#: durum ucu `source: akademi` gösterince açılır (sözleşme §6.6).
DEFAULT_SESSION_CALLS = {"enabled": False, "gapSeconds": 1, "maxPerSlot": 12,
                         "offsetSeconds": 0}

#: Elle grup çağrısında bugünün planındaki oturum bu pencere içindeyse o
#: oturumun (öğretmen adlı) metni çalar.
MANUAL_CALL_WINDOW_MINUTES = 30

MIGRATED_DETAIL = ("Gruplar artık Öğrenci Yönetimi’nde. Grubu orada düzenleyin; "
                   "zil çağrı listesi oradan okunur.")

#: Grup türleri. Ad'dan tahmin edilmez, açıkça saklanır (bkz. 003 göçü).
KINDS = ("grup", "ozel")

#: Zil saatinin türü. Etiketten tahmin edilmez, açıkça saklanır (004 göçü).
TIME_KINDS = ("ders", "teneffus")
DEFAULT_TIME_KIND = "ders"

#: Zilin HEMEN ARKASINDAN çalan anonslar. Ders zilinden sonra derse giriliyor,
#: teneffüs zilinden sonra teneffüse çıkılıyor; iki durum aynı cümleyle
#: karşılanamaz.
DEFAULT_LESSON_AFTER_TEXT = "İyi dersler."
DEFAULT_BREAK_AFTER_TEXT = "İyi teneffüsler."

#: Öğrenci çağırma (anons sayfası) şablonu — mobil geçit sözleşmesi §5.
#: Yer tutucular: `{ad}`, `{soyad}`, `{sinif}`.
DEFAULT_PAGE_TEXT = "{ad} {soyad}, lütfen danışmaya gel."

#: Sayfa sesinin köprüde kalma süreleri (bkz. 005 göçü başlığı).
PAGE_RECENT = timedelta(hours=24)
PAGE_KEEP = timedelta(days=60)

#: Ajan köprüyü 3 saniyede bir sorar: kuyruğa giren anonsun tahmini gecikmesi.
PAGE_ETA_SECONDS = 3

#: Sayfa sonuçları. Yeteneğin dışarıya döndürdüğü `status` bunlardan biridir.
PAGE_STATUSES = ("queued", "disabled", "tts_unavailable", "failed")

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
    key = "solo" if solo(kind) else "call"
    template = str(settings["texts"][key])
    # Öğretmen bilinmiyor (elle çağrı, eski zil grubu): şablon `{hoca}` taşıyorsa
    # yer tutucu boş okunmasın — öğretmensiz GENEL şablona düşülür.
    if "{hoca}" in template:
        return generic_text(settings, name, kind)
    return render(template, name)


def generic_text(settings: dict[str, Any], name: str, kind: str) -> str:
    """Öğretmensiz genel çağrı cümlesi (`texts.callGeneric` / `texts.soloGeneric`)."""
    key = "soloGeneric" if solo(kind) else "callGeneric"
    return render_call(str(settings["texts"][key]), group=name)


def session_text(settings: dict[str, Any], call: dict[str, Any]) -> str:
    """Zil planındaki oturum çağrısının KESİN cümlesi — tek yer (§3.4).

    Öğretmenin anons adı yoksa genel şablon okunur. Şablon `{hoca}` taşımıyorsa
    (bugünkü sabit "Hüseyin hoca" şablonu) metin olduğu gibi kalır; panel bunu
    uyarır, kendiliğinden değiştirmez.
    """
    kind = str(call.get("groupKind") or call.get("group_kind") or "group")
    name = str(call.get("groupName") or call.get("group_name") or "")
    teacher = call.get("teacherName", call.get("teacher_name")) or None
    if not teacher:
        return generic_text(settings, name, kind)
    template = str(settings["texts"]["solo" if solo(kind) else "call"])
    return render_call(template, group=name, teacher=str(teacher),
                       room=(call.get("room") or None), subject=(call.get("subject") or None))


def template_warnings(settings: dict[str, Any]) -> list[dict[str, str]]:
    """Şablonda `{hoca}` yoksa ve sabit öğretmen adı varsa: uyarı + öneri."""
    out: list[dict[str, str]] = []
    for key, suggestion in (("call", SUGGESTED_CALL_TEXT), ("solo", SUGGESTED_SOLO_TEXT)):
        text = str(settings["texts"][key])
        if "{hoca}" in text:
            continue
        current = text
        if HARDCODED_TEACHER in text:
            suggestion = text.replace(HARDCODED_TEACHER, "{hoca}")
        out.append({"key": key, "current": current, "suggestion": suggestion,
                    "detail": "Şablonda {hoca} yok: oturum çağrısı hangi öğretmen girerse "
                              "girsin aynı adı okur."})
    return out


def _after_setting(texts: dict[str, Any], key: str, fallback: str) -> str:
    """Zil sonrası anons metni — BOŞ BIRAKMAK GEÇERLİ BİR SEÇİMDİR.

    `call`/`solo` şablonlarından ayrılır ve bilerek: grup çağrısının boş
    metni anlamsızdır (çağrı yapılamaz), ama zil sonrası anonsun boş metni
    "bu tür için anons istemiyorum" demektir — kullanıcı yalnız teneffüs
    zilinin arkasına anons isteyebilir.

    Bu yüzden "anahtar hiç yok" ile "anahtar var ama boş" AYRILIR. Ayrılmasaydı
    kullanıcı alanı temizleyip kaydeder, ekran varsayılanı geri koyar ve anons
    çalmaya devam ederdi — kaydettiğini sandığı şey olmazdı.
    """
    if key not in texts:
        return fallback
    return str(texts[key] or "")[:TEXT_MAX].strip()


def render_page(template: str, *, first_name: str = "", last_name: str = "",
                class_name: str = "") -> str:
    """Sayfa şablonunu doldurur: `{ad}`, `{soyad}`, `{sinif}`.

    Boşluklar sadeleşir; boş kalan yer tutucu cümlede çift boşluk bırakmaz.
    """
    text = str(template or "")
    for key, value in (("{ad}", first_name), ("{soyad}", last_name), ("{sinif}", class_name)):
        text = text.replace(key, " ".join(str(value or "").split())[:NAME_MAX])
    return " ".join(text.split())[:TEXT_MAX]


def time_kind(value: Any) -> str:
    """Zil saatinin türünü doğrular. Tanınmayan değer 'ders' sayılır."""
    text = str(value or "").strip().lower()
    return text if text in TIME_KINDS else DEFAULT_TIME_KIND


def after_text(settings: dict[str, Any], kind: str) -> str:
    """Zilin arkasından çalacak anonsun cümlesi.

    TEK YER: `call_text` grup çağrıları için ne yapıyorsa bu da zil sonrası
    anonslar için onu yapar. İki ayrı yerde
    `settings["texts"]["lessonAfter" if ... else "breakAfter"]` yazılsaydı,
    biri unutulduğunda teneffüse çıkan öğrenciye "İyi dersler" denirdi.
    """
    key = "breakAfter" if time_kind(kind) == "teneffus" else "lessonAfter"
    return str(settings["texts"][key] or "").strip()


class BellService:
    def __init__(self, *, store: Any, log: Any, audio: Any, scheduler: Any,
                 voices: VoiceLibrary, bridge: BellBridge, notify: Any = None,
                 play_locally: bool = True, page_tts_timeout: float = 20.0,
                 on_week_change: Callable[[], Awaitable[None]] | None = None,
                 student_groups: Callable[[], Any] | None = None,
                 today: Callable[[], date] = planning.today_local,
                 now: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._log = log
        self._audio = audio
        self._scheduler = scheduler
        self._voices = voices
        self._bridge = bridge
        self._notify = notify
        self._default_play_locally = play_locally
        self._page_tts_timeout = page_tts_timeout
        #: Haftalık plan (saatler ya da gruplar) değişti — `bell.week_changed`
        #: olayını yayınlar (`bell.week` tüketicilerinin önbelleği bırakılır).
        self._on_week_change = on_week_change
        #: `bbd_students.groups` yeteneğini ÇAĞRI ANINDA çözer (isteğe bağlı
        #: tüketim; modül yükleme sırası önemli olmasın). Yoksa `None` döner.
        self._student_groups = student_groups
        self._group_cache: list[dict[str, Any]] | None = None
        self._today = today
        self._clock = now or (lambda: datetime.now(planning.ISTANBUL))
        self._plan = PlanBook(store)
        #: Plan uygulama, acil kapatma ve tetik kurma birbirini ezmesin.
        self._plan_lock = asyncio.Lock()

        self._settings_table = store.table("settings")
        self._time_table = store.table("time")
        self._group_table = store.table("group")
        self._log_table = store.table("log")
        self._page_table = store.table("page")
        self._page_voice_table = store.table("page_voice")

        self._sync_task: asyncio.Task[None] | None = None
        #: Ajanın kitaplığında bulunması gereken ses kimlikleri — eşitleme
        #: döngüsünün son turundan. Ekran "zil bilgisayarında 21/21 ses" der;
        #: her ekran tazelemesinde bütün dosyaları yeniden özetlemek pahalı.
        self._wanted_ids: set[str] | None = None
        #: Günlüğe en son işlenen ajan cevabı (`lastAck.id`).
        self._acked_id = 0

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
                "lessonAfter": DEFAULT_LESSON_AFTER_TEXT,
                "breakAfter": DEFAULT_BREAK_AFTER_TEXT,
                "page": DEFAULT_PAGE_TEXT,
                "callGeneric": DEFAULT_CALL_GENERIC_TEXT,
                "soloGeneric": DEFAULT_SOLO_GENERIC_TEXT,
            },
            "sessionCalls": dict(DEFAULT_SESSION_CALLS),
            "groupsMigratedAt": "",
        }

    def normalize(self, state: Any) -> dict[str, Any]:
        """Eksik/bozuk ayarı tamamlar.

        0.1 sürümünün `groups` bloğu (ders takvimi grup kimliklerine bağlıydı)
        sessizce düşer: o kimlikler artık yok, taşınabilecek bir anlamı da yok.

        0.2'nin `texts.lesson` alanı da düşer — ADI TAŞINMAZ. Zilden sonra anons
        geri geldi (0.4) ama artık TEK bir cümle değil, türe göre iki cümle var
        (`lessonAfter` / `breakAfter`). Eski tek alanı yeni alanlardan birine
        kopyalamak, kullanıcının bir zamanlar yazdığı "derse geçiniz" cümlesini
        teneffüs zilinin arkasına da takma riski taşırdı; kaynağı belirsiz bir
        metnin hangi türe ait olduğunu bilemeyiz.
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

        raw_calls = state.get("sessionCalls")
        calls: dict[str, Any] = raw_calls if isinstance(raw_calls, dict) else {}

        return {
            "version": 5,
            "enabled": state.get("enabled") is not False,
            "bellSound": str(state.get("bellSound") or base["bellSound"])[:120],
            "volume": max(0, min(100, volume)),
            "playLocally": bool(state.get("playLocally", base["playLocally"])),
            "texts": {
                "call": str(texts.get("call") or DEFAULT_CALL_TEXT)[:TEXT_MAX].strip(),
                "solo": str(texts.get("solo") or DEFAULT_SOLO_TEXT)[:TEXT_MAX].strip(),
                "lessonAfter": _after_setting(texts, "lessonAfter",
                                              DEFAULT_LESSON_AFTER_TEXT),
                "breakAfter": _after_setting(texts, "breakAfter",
                                             DEFAULT_BREAK_AFTER_TEXT),
                # Sayfa şablonu boş bırakılamaz: boş bir çağrı anlamsızdır.
                "page": str(texts.get("page") or DEFAULT_PAGE_TEXT)[:TEXT_MAX].strip(),
                # Genel (öğretmensiz) çağrı boş bırakılamaz: kesin metnin sesi
                # hazır değilken okunacak tek cümle budur.
                "callGeneric": str(texts.get("callGeneric")
                                   or DEFAULT_CALL_GENERIC_TEXT)[:TEXT_MAX].strip(),
                "soloGeneric": str(texts.get("soloGeneric")
                                   or DEFAULT_SOLO_GENERIC_TEXT)[:TEXT_MAX].strip(),
            },
            # Oturum çağrıları — varsayılan KAPALI (§6.6).
            "sessionCalls": {
                "enabled": calls.get("enabled") is True,
                "gapSeconds": max(0, min(10, int(number(calls.get("gapSeconds"), 1)))),
                "maxPerSlot": max(1, min(40, int(number(calls.get("maxPerSlot"), 12)))),
                "offsetSeconds": max(0, min(120, int(number(calls.get("offsetSeconds"), 0)))),
            },
            # Zil grupları Öğrenci Yönetimi'ne taşındı mı (§6)? Taşındıysa grup
            # düzenleme kapalı, elle çağrı listesi `bbd_students.groups`'tan.
            "groupsMigratedAt": str(state.get("groupsMigratedAt") or "")[:40],
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
        # Taşıma damgası ayar ekranından değiştirilemez; yalnız "Grupları taşı".
        clean["groupsMigratedAt"] = before["groupsMigratedAt"]

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
                or clean["texts"]["solo"] != before["texts"]["solo"]
                or clean["texts"]["callGeneric"] != before["texts"]["callGeneric"]
                or clean["texts"]["soloGeneric"] != before["texts"]["soloGeneric"]):
            await self._ensure_group_voices(clean)
            await self._ensure_plan_voices(clean)

        # Zil sonrası anonslar gruplardan bağımsız: metin değiştiyse yalnız o
        # iki ses yeniden üretilir, yüzlerce grup sesi boşuna sıraya girmez.
        if (clean["texts"]["lessonAfter"] != before["texts"]["lessonAfter"]
                or clean["texts"]["breakAfter"] != before["texts"]["breakAfter"]):
            await self._ensure_time_voices(clean)

        await self.reschedule()
        return {"ok": True, "settings": clean}

    async def _week_changed(self) -> None:
        """`bell.week` yanıtı değişti. Yazma ANINDA (ses üretiminden önce)
        bildirilir: sonraki adım patlasa da tüketici eski planı göstermez."""
        if self._on_week_change is None:
            return
        try:
            await self._on_week_change()
        except Exception as failure:  # noqa: BLE001 — bildirim yazmayı düşürmez (K7)
            self._log.warning("zil haftası değişikliği bildirilemedi", error=str(failure))

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
                                  "label": str(row["label"]),
                                  "kind": time_kind(row["kind"])})
        return week

    async def save_times(self, week: Any, *, actor: str) -> dict[str, Any]:
        """Haftalık saatleri TÜMÜYLE değiştirir.

        Kısmi güncelleme yok: ekran zaten tabloyu bir bütün olarak gönderiyor ve
        satır satır fark almak, iki makinede açık panelin birbirini yarım ezmesi
        demek olurdu.
        """
        clean: list[tuple[str, str, str, str, str]] = []
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
                clean.append((f"{day}-{value.replace(':', '')}", day, value, label,
                              time_kind(entry.get("kind"))))

        await self._store.execute(f"DELETE FROM {self._time_table}")
        for row in clean:
            await self._store.execute(
                f"INSERT INTO {self._time_table} (id, day, time, label, kind) "
                f"VALUES (?, ?, ?, ?, ?)",
                row,
            )
        await self._week_changed()

        # Yeni bir tür ilk kez kullanılmış olabilir; o türün anons sesi henüz
        # hiç istenmemişse burada sıraya girer, zil saatinde eksik kalmasın.
        await self._ensure_time_voices(await self.settings())

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
        if (await self.settings())["groupsMigratedAt"]:
            return {"ok": False, "detail": MIGRATED_DETAIL}
        clean = " ".join(str(name or "").split())[:NAME_MAX]
        if not clean:
            return {"ok": False, "detail": "Grup adı boş olamaz."}
        kind = kind if kind in KINDS else "grup"

        current = await self.groups()
        if len(current) >= GROUP_MAX:
            return {"ok": False, "detail": f"En çok {GROUP_MAX} grup olabilir."}
        if any(fold(item["name"]) == fold(clean) for item in current):
            return {"ok": False, "detail": f"'{clean}' adında bir grup zaten var."}

        # ESKİ VERİ İÇİN: silme artık kalıcı (ADR 0027 eki), yani bu dal yalnız
        # 004 göçünden ÖNCE yumuşak silinmiş satırlara denk gelir. Onları
        # görmezden gelip yeni satır açmak, aynı adın iki kimlikle dolaşması
        # demek olurdu.
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
        await self._week_changed()

        settings = await self.settings()
        await self._voices.require(call_text(settings, clean, kind))
        self._log.info("grup eklendi", group=clean, kind=kind, actor=actor)
        return {"ok": True, "id": group_id, "groups": await self.groups()}

    async def rename_group(self, group_id: str, name: str, *,
                           kind: str = "", actor: str) -> dict[str, Any]:
        if (await self.settings())["groupsMigratedAt"]:
            return {"ok": False, "detail": MIGRATED_DETAIL}
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
        await self._week_changed()
        # ESKİ SES SİLİNMEZ: aynı ada dönülürse yeniden üretilmesin. Birkaç yüz
        # kilobayt, gereksiz bir bulut çağrısından ucuzdur.
        settings = await self.settings()
        await self._voices.require(call_text(settings, clean, kind))
        self._log.info("grup adı değişti", group=clean, kind=kind, actor=actor)
        return {"ok": True, "groups": await self.groups()}

    async def remove_group(self, group_id: str, *, actor: str) -> dict[str, Any]:
        """Grubu KALICI olarak siler: satır, ses kaydı, `.wav` ve ajandaki kopya.

        002 göçünün "SATIR SİLİNMEZ" kuralı BU TABLO İÇİN geçersizdir (ADR 0027
        eki, kullanıcı kararı). Gerekçe: yumuşak silme, silinen grubun anons
        `.wav`'ını hem `data/sounds` altında hem zil ajanının kitaplığında
        süresiz bırakıyordu. Kullanıcı ekranda "kaldırdım" görüyor, ses diskte
        duruyordu; "sildim" ile "gizledim" arasındaki fark hiçbir yerde
        yazmıyordu.

        DİKKAT — bu bir iş verisi silme DEĞİLDİR. Satış, cari, öğrenci gibi
        kayıtlarda silme yasağı yerinde duruyor. Buradaki içerik TÜRETİLMİŞtir:
        aynı ad yeniden girilirse aynı metinden aynı ses yeniden üretilir.

        SES PAYLAŞILIYORSA SİLİNMEZ. İki grup aynı cümleyi üretebilir (aynı ad
        farklı türde, ya da şablon `{grup}` taşımıyorsa hepsi aynı metin).
        Önce referans sayılır; başka canlı bir grup ya da zil sonrası anons o
        sesi kullanıyorsa dosya yerinde bırakılır.
        """
        if (await self.settings())["groupsMigratedAt"]:
            return {"ok": False, "detail": MIGRATED_DETAIL}
        row = await self._store.fetch_one(
            f"SELECT name, kind FROM {self._group_table} WHERE id = ? AND deleted_at = ''",
            (group_id,),
        )
        if row is None:
            return {"ok": False, "detail": "Grup bulunamadı."}

        settings = await self.settings()
        name = str(row["name"])
        doomed = self._voices.key(call_text(settings, name, str(row["kind"] or "grup")))

        # Satır ÖNCE gider: kalan referansları saymadan önce bu grubun kendi
        # sesini "hâlâ kullanılıyor" saymayalım.
        await self._store.execute(
            f"DELETE FROM {self._group_table} WHERE id = ?", (group_id,)
        )
        await self._week_changed()

        if doomed not in await self._live_voice_keys(settings):
            await self._voices.forget(doomed)

        # Ajandaki kopya da gitmeli: köprü temizliği `needed_sounds()` ile
        # karşılaştırır ve grup artık listede olmadığı için ses oradan düşer.
        await self.sync_sounds()

        self._log.info("grup kalıcı olarak silindi", group=name, actor=actor)
        return {"ok": True, "groups": await self.groups()}

    async def _live_voice_keys(self, settings: dict[str, Any]) -> set[str]:
        """Şu an KULLANILAN bütün anons seslerinin özetleri.

        Zil sonrası anonslar da buraya girer: bir grubun cümlesiyle aynı metne
        denk gelirlerse o ses silinmemelidir.
        """
        texts = [after_text(settings, kind) for kind in TIME_KINDS]
        texts += [call_text(settings, group["name"], group["kind"])
                  for group in await self.groups()]
        # Öğrenci gruplarının genel (öğretmensiz) çağrıları — elle çağrı ve
        # kesin ses hazır değilken plan çağrısı bunları çalar.
        texts += [generic_text(settings, group["callName"] or group["name"], group["kind"])
                  for group in await self.student_groups()]
        keys = {self._voices.key(text) for text in texts if text}
        # Zil planının önümüzdeki 14 günündeki çağrı sesleri (kesin + genel).
        # Buraya girmeselerdi köprü eşitlemesi onları silerdi (ADR 0027 §7).
        today = self._today()
        keys |= await self._plan.voice_keys(
            today, today + timedelta(days=planning.WINDOW_DAYS - 1))
        # Öğrenci çağırma sesleri de canlıdır (005 göçü): son 24 saatte
        # çalınanlar ve 60 gün içinde hazırlananlar.
        return keys | await self._page_voice_keys()

    async def _page_voice_keys(self) -> set[str]:
        """Köprüde ve diskte TUTULMASI gereken sayfa sesleri."""
        current = datetime.now(UTC)
        recent = (current - PAGE_RECENT).isoformat(timespec="seconds")
        keep = (current - PAGE_KEEP).isoformat(timespec="seconds")
        rows = await self._store.fetch_all(
            f"SELECT DISTINCT voice_key FROM {self._page_table} "
            f"WHERE at >= ? AND voice_key != ''", (recent,),
        )
        rows += await self._store.fetch_all(
            f"SELECT voice_key FROM {self._page_voice_table} "
            f"WHERE prepared_at >= ? OR last_used_at >= ?", (keep, keep),
        )
        return {str(row["voice_key"]) for row in rows if row.get("voice_key")}

    async def delete_sound(self, name: str, *, actor: str) -> dict[str, Any]:
        """Yüklenmiş zil sesini kalıcı siler. `upload_sound`un karşılığı.

        `anons-` önekli dosyalar BU UÇTAN silinemez: onların sahibi
        `mod_bell_voice` tablosudur ve dosyayı tek başına silmek, kaydı
        "hazır" derken dosyası olmayan bir duruma düşürürdü. Anons temizliği
        `prune_voices` üzerinden yapılır. Bu, `upload_sound`daki yasağın
        aynadaki karşılığıdır.
        """
        settings = await self.settings()
        if name.startswith("anons-"):
            return {"ok": False, "detail": "Anons sesleri bu uçtan silinemez."}
        if name == settings["bellSound"] or Path(name).stem == settings["bellSound"]:
            return {"ok": False, "detail": "Kullanımdaki zil sesi silinemez. "
                                           "Önce başka bir zil sesi seçin."}
        path = self._resolve_sound(name)
        if path is None or not path.is_file():
            return {"ok": False, "detail": "Ses dosyası bulunamadı."}
        try:
            path.unlink()
        except OSError as failure:
            return {"ok": False, "detail": f"Dosya silinemedi: {failure}"}

        await self.sync_sounds()
        self._log.info("zil sesi silindi", file=name, actor=actor)
        return {"ok": True, "name": name}

    async def prune_voices(self, *, actor: str) -> dict[str, Any]:
        """Hiçbir canlı gruba/metne bağlı olmayan anons seslerini kalıcı siler.

        Grup adı değiştikçe ve metin şablonu düzenlendikçe eski `.wav`'lar
        birikiyor. Eskiden hiçbir yerden temizlenmiyorlardı: `VoiceLibrary.forget()`
        yazılmıştı ama DEPODA HİÇBİR YERDEN ÇAĞRILMIYORDU.
        """
        settings = await self.settings()
        live = await self._live_voice_keys(settings)
        entries = await self._voices.entries()
        stale = [hash_ for hash_ in entries if hash_ not in live]
        for hash_ in stale:
            await self._voices.forget(hash_)
        if stale:
            await self.sync_sounds()
        self._log.info("kullanılmayan anonslar silindi", count=len(stale), actor=actor)
        return {"ok": True, "removed": len(stale)}

    async def _ensure_time_voices(self, settings: dict[str, Any]) -> None:
        """Zil sonrası iki anonsun sesini sıraya alır.

        Gruplardan bağımsızdır: hiç grup olmasa da zil çalar ve arkasından
        anons gitmesi beklenir.
        """
        for kind in TIME_KINDS:
            text = after_text(settings, kind)
            if text:
                await self._voices.require(text)

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
        await self._ensure_time_voices(settings)
        await self._ensure_group_voices(settings)
        return await self._voices.sweep()

    # ============================================================== yetenek

    async def week(self) -> dict[str, Any]:
        """`bell.week` — haftalık saatler, SAAT SETLERİ ve önümüzdeki 7 günün kipi.

        Salt okunur; Ders Takvimi ekranı ve mobil geçit (`GET /schedule/bell-week`)
        okur. Ekleyici genişledi (sözleşme §3.1):
          · `profiles` — saat setleri `[{id, name, times: [{time, label, kind}]}]`;
          · `days`     — bugünden 7 gün `[{date, mode, profileId, reason, source,
                         override}]` (gün bazında etkin kip);
          · `groups`   — KULLANIMDAN KALKTI: taşıma (§6) öncesi eski zil grupları,
                         sonrası boş liste. Akademi grupları `GET /groups`'tan okur.
        """
        settings = await self.settings()
        profiles = [{"id": item["id"], "name": item["name"], "times": item["times"]}
                    for item in await self._plan.profiles()]
        today = self._today()
        days = await self.plan_days(today, today + timedelta(days=planning.SCHEDULE_DAYS - 1))
        return {
            "times": await self.times(),
            "profiles": profiles,
            "groups": [] if settings["groupsMigratedAt"] else await self.groups(),
            "days": [{key: day[key] for key in ("date", "mode", "profileId", "reason",
                                                "source", "override")} for day in days],
        }

    # ========================================================= saat setleri

    async def profiles(self) -> list[dict[str, Any]]:
        return await self._plan.profiles()

    async def save_profile(self, profile_id: str | None, name: str, times: Any, *,
                           actor: str) -> dict[str, Any]:
        """Saat setini kurar ya da TÜMÜYLE değiştirir (`save_times` ile aynı ilke)."""
        clean = " ".join(str(name or "").split())[:planning.PROFILE_NAME_MAX]
        if not clean:
            return {"ok": False, "detail": "Saat setinin adı boş olamaz."}
        current = await self._plan.profiles()
        existing = next((item for item in current if item["id"] == profile_id), None)
        if profile_id and existing is None:
            return {"ok": False, "detail": "Saat seti bulunamadı."}
        if any(fold(item["name"]) == fold(clean) and item["id"] != profile_id
               for item in current):
            return {"ok": False, "detail": f"'{clean}' adında bir saat seti zaten var."}
        if existing is None and len(current) >= planning.PROFILE_MAX:
            return {"ok": False, "detail": f"En çok {planning.PROFILE_MAX} saat seti olabilir."}

        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for entry in (times if isinstance(times, list) else [])[:planning.PROFILE_TIMES_MAX]:
            if not isinstance(entry, dict):
                continue
            value = clock(entry.get("time"))
            if value is None or value in seen:
                continue
            seen.add(value)
            rows.append((value, str(entry.get("label") or "")[:LABEL_MAX].strip(),
                         time_kind(entry.get("kind"))))
        rows.sort()
        if not rows:
            return {"ok": False, "detail": "Saat setinde en az bir zil saati olmalı."}

        target = profile_id or _slug(clean, {item["id"] for item in current})
        await self._plan.save_profile(target, clean, fold(clean), rows,
                                      sort=existing["sort"] if existing else len(current),
                                      actor=actor, create=existing is None)
        await self._week_changed()
        # Setin kullandığı türün zil sonrası anonsu hiç istenmemiş olabilir.
        await self._ensure_time_voices(await self.settings())
        self._log.info("saat seti kaydedildi", profile=target, times=len(rows), actor=actor)
        await self.reschedule()
        return {"ok": True, "id": target, "profiles": await self._plan.profiles()}

    async def delete_profile(self, profile_id: str, *, actor: str) -> dict[str, Any]:
        """Saat setini kaldırır. Önümüzdeki bir gün kullanıyorsa REDDEDİLİR:
        o gün Akademi planında kalır ve set yoksa haftalık saatlere düşerdi."""
        if profile_id not in await self._plan.profile_ids():
            return {"ok": False, "detail": "Saat seti bulunamadı."}
        used = await self._plan.days_using_profile(profile_id, self._today())
        if used:
            return {"ok": False, "detail": "Bu saat seti zil planında kullanılıyor: "
                                           + ", ".join(used[:5])
                                           + ". Önce Akademi kurum takviminden kaldırın."}
        await self._plan.delete_profile(profile_id)
        await self._week_changed()
        self._log.info("saat seti silindi", profile=profile_id, actor=actor)
        await self.reschedule()
        return {"ok": True, "profiles": await self._plan.profiles()}

    # ============================================================ zil planı

    def _session_calls(self, settings: dict[str, Any]) -> dict[str, Any]:
        value = settings.get("sessionCalls")
        return value if isinstance(value, dict) else dict(DEFAULT_SESSION_CALLS)

    async def plan_days(self, start: date, end: date) -> list[dict[str, Any]]:
        """Aralığın her günü için ETKİN durum (acil kapatma > Akademi planı > haftalık)."""
        rows = await self._plan.days(start, end)
        out: list[dict[str, Any]] = []
        day = start
        while day <= end:
            row = rows.get(day.isoformat())
            has_plan = bool(row and row["source"] == "akademi")
            override = None
            if row and row["override_mode"]:
                override = {"mode": str(row["override_mode"]), "by": str(row["override_by"]),
                            "at": planning.local_iso(str(row["override_at"]))}
            out.append({
                "date": day.isoformat(),
                "mode": str(row["mode"]) if has_plan and row else "normal",
                "profileId": (str(row["profile_id"]) or None) if has_plan and row else None,
                "reason": (str(row["reason"]) or None) if has_plan and row else None,
                "source": "km" if override else ("akademi" if has_plan else "none"),
                "revision": int(row["revision"]) if has_plan and row else None,
                "planHash": str(row["plan_hash"]) if has_plan and row else None,
                "override": override,
                "lastResult": (str(row["last_result"]) or None) if row else None,
                "lastError": (str(row["last_error"]) or None) if row else None,
                "lastDispatchAt": planning.local_iso(str(row["last_dispatch_at"]))
                if row else None,
                "receivedAt": planning.local_iso(str(row["received_at"])) if row else None,
            })
            day += timedelta(days=1)
        return out

    async def _effective(self, day: date, settings: dict[str, Any],
                         profiles: dict[str, list[dict[str, Any]]] | None = None,
                         weekly: dict[str, list[dict[str, Any]]] | None = None,
                         ) -> tuple[str, list[dict[str, Any]], bool]:
        """Bir günün (kip, zil saatleri, çağrılar çalsın mı).

        Öncelik: KM acil kapatması → Akademi planı → haftalık saatler. Kip
        `profile` ama set artık yoksa haftalık saatlere düşülür (çatışma kuralı 2).
        """
        row = await self._plan.day(day)
        return await self._effective_row(day, row, settings, profiles, weekly)

    async def _effective_row(self, day: date, row: dict[str, Any] | None,
                             settings: dict[str, Any],
                             profiles: dict[str, list[dict[str, Any]]] | None = None,
                             weekly: dict[str, list[dict[str, Any]]] | None = None,
                             ) -> tuple[str, list[dict[str, Any]], bool]:
        if row and row["override_mode"] == "off":
            return "off", [], False
        has_plan = bool(row and row["source"] == "akademi")
        mode = str(row["mode"]) if has_plan and row else "normal"
        calls_on = has_plan and bool(self._session_calls(settings)["enabled"])
        if mode == "off":
            return "off", [], False
        if mode == "profile" and row:
            sets = profiles if profiles is not None else {
                item["id"]: item["times"] for item in await self._plan.profiles()}
            times = sets.get(str(row["profile_id"]))
            if times is not None:
                return "profile", times, calls_on
            mode = "normal"
        week = weekly if weekly is not None else await self.times()
        return mode, week[DAYS[day.weekday()]], calls_on

    async def apply_days(self, body: Any, *, actor: str) -> dict[str, Any]:
        """`bell.plan.apply_days` — Akademi'nin `PUT /bell/plan` gövdesini uygular.

        Yapı bozuksa `PlanError` (422, `details.path`). Her gün BAĞIMSIZ uygulanır;
        sonuç gün bazındadır (`plan.py` başlığındaki kural). Ses üretimi isteğin
        DIŞINDA kuyruktadır.
        """
        _start, _end, days = planning.validate_push(body)
        settings = await self.settings()
        known = await self._plan.profile_ids()
        results: list[dict[str, Any]] = []
        touched = False
        async with self._plan_lock:
            for entry in days:
                day = entry["date"]
                existing = await self._plan.day(day)
                result, error = self._decide(entry, existing, known)
                record: dict[str, Any] = {"date": day.isoformat(), "result": result,
                                          "revision": entry["revision"]}
                if error:
                    record["error"] = error
                    if error == "unknown_profile":
                        record["details"] = {"profileId": entry["profileId"]}
                if result in ("applied", "overridden"):
                    calls = [self._prepare_call(settings, call) for call in entry["calls"]]
                    await self._plan.write_day(entry, calls=calls, existing=existing,
                                               result=result)
                    touched = True
                elif result == "unchanged":
                    if existing and int(existing["revision"]) != entry["revision"]:
                        await self._plan.bump_revision(day, entry["revision"])
                else:
                    await self._plan.note_result(day, existing, result=result, error=error)
                    if error:
                        self._log.warning("zil planı günü reddedildi", date=day.isoformat(),
                                          error=error, revision=entry["revision"])
                results.append(record)

        if touched:
            await self._ensure_plan_voices(settings)
            await self.reschedule()
            await self._record("cagri-plan", "plan", "zil planı", True,
                               "; ".join(f"{item['date']}:{item['result']}"
                                         for item in results)[:500], actor=actor)
        queue = self._voices.state()
        return {"days": results, "voicesPending": int(queue.get("queued") or 0),
                "bellEnabled": bool(settings["enabled"])}

    @staticmethod
    def _decide(entry: dict[str, Any], existing: dict[str, Any] | None,
                known: set[str]) -> tuple[str, str]:
        if entry["mode"] == "profile" and entry["profileId"] not in known:
            return "rejected", "unknown_profile"
        has_plan = bool(existing and existing["source"] == "akademi")
        if has_plan and existing:
            if str(existing["plan_hash"]) == entry["planHash"]:
                return "unchanged", ""
            current = int(existing["revision"])
            if entry["revision"] < current:
                return "stale", ""
            if entry["revision"] == current:
                return "rejected", "revision_conflict"
        if existing and existing["override_mode"]:
            return "overridden", ""
        return "applied", ""

    def _prepare_call(self, settings: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
        """Çağrı satırına kesin ve genel metnin ses anahtarlarını iliştirir."""
        exact = session_text(settings, call)
        generic = generic_text(settings, call["groupName"], call["groupKind"])
        return {**call,
                "textHash": hashlib.sha256(exact.encode("utf-8")).hexdigest()[:32],
                "voiceKey": self._voices.key(exact),
                "genericVoiceKey": self._voices.key(generic)}

    async def _ensure_plan_voices(self, settings: dict[str, Any]) -> None:
        """14 günün bütün tekil çağrı metinleri ve her grubun genel metni sıraya girer.

        Ses anahtarı metne bağlıdır; şablon değişince satırlardaki anahtarlar da
        yenilenir (aynı satır, yeni metin)."""
        today = self._today()
        calls = await self._plan.calls(today, today + timedelta(days=planning.WINDOW_DAYS - 1))
        texts: dict[str, str] = {}
        for row in calls:
            call = {"groupKind": row["group_kind"], "groupName": row["group_name"],
                    "teacherName": row["teacher_name"] or None, "room": row["room"] or None,
                    "subject": row["subject"] or None}
            exact = session_text(settings, call)
            generic = generic_text(settings, str(row["group_name"]), str(row["group_kind"]))
            texts[exact] = exact
            texts[generic] = generic
            keys = (self._voices.key(exact), self._voices.key(generic))
            if (str(row["voice_key"]), str(row["generic_voice_key"])) != keys:
                await self._store.execute(
                    f"UPDATE {self._plan.call_table} SET voice_key = ?, generic_voice_key = ? "
                    f"WHERE id = ?", (*keys, str(row["id"])))
        for group in await self.student_groups():
            text = generic_text(settings, group["callName"] or group["name"], group["kind"])
            texts[text] = text
        for text in texts:
            await self._voices.require(text)

    async def plan_status(self, start: Any, end: Any) -> dict[str, Any]:
        """`bell.plan.status` — Akademi uzlaştırması ve "zil planı durumu" ekranı."""
        first = planning.parse_day(start, path="from")
        last = planning.parse_day(end, path="to")
        if last < first:
            raise PlanError("to, from'dan önce olamaz.", details={"path": "to"})
        if (last - first).days + 1 > planning.STATUS_MAX_DAYS:
            raise PlanError(f"Aralık en çok {planning.STATUS_MAX_DAYS} gün olabilir.",
                            details={"path": "to"})
        settings = await self.settings()
        days = await self.plan_days(first, last)
        calls = await self._plan.calls(first, last)
        entries = await self._voices.entries()

        def ready(key: str) -> bool:
            row = entries.get(key)
            return bool(row and row["file"] and not row["error"])

        counts: dict[str, dict[str, int]] = {}
        for row in calls:
            bucket = counts.setdefault(str(row["date"]), {"calls": 0, "ready": 0})
            bucket["calls"] += 1
            bucket["ready"] += 1 if ready(str(row["voice_key"])) else 0
        agent = await self._bridge.try_status()
        out = []
        for day in days:
            bucket = counts.get(day["date"], {"calls": 0, "ready": 0})
            out.append({
                "date": day["date"], "mode": day["mode"], "source": day["source"],
                "revision": day["revision"], "planHash": day["planHash"],
                "profileId": day["profileId"], "reason": day["reason"],
                "calls": bucket["calls"], "voicesReady": bucket["ready"],
                "voicesPending": bucket["calls"] - bucket["ready"],
                "lastDispatchAt": day["lastDispatchAt"], "override": day["override"],
                "lastResult": day["lastResult"], "lastError": day["lastError"],
            })
        return {
            "bellEnabled": bool(settings["enabled"]),
            "sessionCallsEnabled": bool(self._session_calls(settings)["enabled"]),
            "agent": {"online": bool(agent.get("online")),
                      "lastSeenAt": agent.get("lastSeen") or None},
            "days": out,
        }

    async def override_day(self, day: Any, mode: str = "off", *, actor: str) -> dict[str, Any]:
        """Acil kapatma ("Bugün zili kapat"). YALNIZ BUGÜN; Akademi takvimine yazılmaz.

        O gün sonuna dek zil, zil sonrası anons ve çağrı çalmaz; ertesi gün
        Akademi planı geçerlidir (§3.5)."""
        target = planning.parse_day(day, path="date")
        if mode != "off":
            raise PlanError("Acil kapatmanın tek kipi 'off'.", details={"path": "mode"})
        if target != self._today():
            raise PlanError("Acil kapatma yalnız bugün için yapılır.", status=409,
                            code="override_today_only", details={"path": "date"})
        async with self._plan_lock:
            await self._plan.set_override(target, mode, actor=actor)
        await self._record("zil", "acil", target.isoformat(), True,
                           "bugünün zili kapatıldı", actor=actor)
        self._log.warning("bugünün zili acil kapatıldı", date=target.isoformat(), actor=actor)
        await self.reschedule()
        return {"ok": True, "days": await self.plan_days(target, target)}

    async def clear_override(self, day: Any, *, actor: str) -> dict[str, Any]:
        target = planning.parse_day(day, path="date")
        async with self._plan_lock:
            await self._plan.clear_override(target)
        await self._record("zil", "acil", target.isoformat(), True,
                           "acil kapatma kaldırıldı", actor=actor)
        await self.reschedule()
        return {"ok": True, "days": await self.plan_days(target, target)}

    # ====================================================== öğrenci grupları

    def _groups_capability(self) -> Any:
        return self._student_groups() if self._student_groups is not None else None

    def forget_groups(self) -> None:
        """`bbd_students.groups_changed` geldi: grup önbelleği bırakılır."""
        self._group_cache = None

    async def student_groups(self) -> list[dict[str, Any]]:
        """Canlı öğrenci grupları (`bbd_students.groups`) — yalnız taşıma SONRASI.

        Taşıma öncesi boş döner: elle çağrı listesi eski zil gruplarıdır.
        Yetenek yoksa ya da patlarsa boş liste — zil bundan etkilenmez (K7).
        """
        if not (await self.settings())["groupsMigratedAt"]:
            return []
        if self._group_cache is not None:
            return self._group_cache
        capability = self._groups_capability()
        if capability is None:
            return []
        try:
            # Sınıf = grup (bbd_students göç 009): eski `className`'den dönüşen sınıflar yalnız
            # `view="classes"` görünümünde gelir; Akademi zil planı onları da `km_group:<id>`
            # ile çağırır (§2.6) — elle çağrı listesi BÜTÜN sınıflardır. Görünüm bilmeyen
            # eski öğrenci modülünde parametresiz çağrılır.
            try:
                listing = await capability.list(view="classes")
            except TypeError:
                listing = await capability.list()
        except Exception as failure:  # noqa: BLE001 — başka modül; zil ayakta kalır (K7)
            self._log.warning("öğrenci grupları okunamadı", error=str(failure))
            return []
        groups = [{"id": str(item["id"]), "name": str(item["name"]),
                   "callName": str(item.get("callName") or ""),
                   "kind": str(item.get("kind") or "group")}
                  for item in listing.get("groups") or [] if not item.get("archived")]
        groups.sort(key=lambda item: fold(item["name"]))
        self._group_cache = groups
        return groups

    async def group_deleted(self, group_id: str) -> int:
        """Öğrenci Yönetimi bir sınıfı KALICI sildi (`bbd_students.group_deleted`).

        O sınıfın bugün ve sonrasındaki planlı oturum çağrıları iptal edilir
        (çalınmaz; satır durum ekranında "iptal" görünür), elle çağrı listesi
        önbelleği bırakılır, tetikler yeniden kurulur. Dokunulan çağrı sayısı döner.
        """
        self.forget_groups()
        cancelled = await self._plan.cancel_group_calls(
            f"km_group:{group_id}", self._today(), "sınıf silindi (Öğrenci Yönetimi)")
        if cancelled:
            self._log.info("silinen sınıfın planlı çağrıları iptal edildi", group=group_id,
                           calls=cancelled)
            await self.reschedule()
        return cancelled

    async def groups_changed(self) -> None:
        """Öğrenci grupları değişti: önbellek bırakılır, yeni/adı değişen grubun
        genel çağrı sesi sıraya girer."""
        self.forget_groups()
        settings = await self.settings()
        for group in await self.student_groups():
            await self._voices.require(
                generic_text(settings, group["callName"] or group["name"], group["kind"]))

    async def migrate_groups(self, *, actor: str) -> dict[str, Any]:
        """"Grupları taşı" (§6.2): her canlı zil grubu için `import_named`.

        Katlanmış ada göre İDEMPOTENT: ikinci basış yeni grup açmaz. Zil grubu
        satırı SİLİNMEZ (K5); `student_group_id` taşıma izidir. Ad aynı kaldığı
        için genel çağrı sesi yeniden üretilmez — üretilmesi gerekirse sıraya girer.
        """
        capability = self._groups_capability()
        if capability is None:
            return {"ok": False, "detail": "Öğrenci Yönetimi modülü yok ya da kapalı; "
                                           "gruplar taşınamaz."}
        moved: list[dict[str, Any]] = []
        for group in await self.groups():
            kind = "individual" if group["kind"] == "ozel" else "group"
            try:
                result = await capability.import_named(group["name"], kind=kind, actor=actor)
            except Exception as failure:  # noqa: BLE001 — başka modül; sebebi ekrana taşınır
                return {"ok": False, "detail": f"'{group['name']}' taşınamadı: {failure}",
                        "moved": moved}
            await self._store.execute(
                f"UPDATE {self._group_table} SET student_group_id = ? WHERE id = ?",
                (str(result["id"]), group["id"]))
            moved.append({"bellGroupId": group["id"], "name": group["name"],
                          "studentGroupId": str(result["id"]),
                          "created": bool(result.get("created"))})
        settings = await self.settings()
        if not settings["groupsMigratedAt"]:
            settings["groupsMigratedAt"] = _now()
            await self._store.execute(
                f"INSERT INTO {self._settings_table} (id, payload, updated_at, updated_by) "
                f"VALUES (1, ?, ?, ?) "
                f"ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
                f"updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (json.dumps(settings, ensure_ascii=False), _now(), actor),
            )
        self.forget_groups()
        await self._week_changed()
        await self.groups_changed()
        self._log.info("zil grupları öğrenci gruplarına taşındı", count=len(moved), actor=actor)
        return {"ok": True, "moved": moved,
                "created": sum(1 for item in moved if item["created"])}

    # ======================================================= öğrenci çağırma

    async def page_template(self) -> str:
        """Ayardaki sayfa şablonu (`texts.page`)."""
        return str((await self.settings())["texts"]["page"])

    async def page(self, text: str, *, cache_key: str | None = None,
                   actor: str | None = None,
                   request_id: str | None = None) -> dict[str, Any]:
        """Serbest cümleyi okulun hoparlöründen duyurur (`bell.announce`).

        Ses önbellekte varsa hemen, yoksa Vertex ile ANLIK üretilir (gece
        kuyruğunun önüne geçer) ve mevcut çağrılarla aynı yoldan köprüye
        gider (`_dispatch`). HATA YÜKSELTMEZ — sonuç `status` ile döner:

          queued · disabled (ana şalter kapalı) · tts_unavailable (ses
          üretilemedi / metin boş) · failed (köprü ve yerel çalma reddetti)

        `request_id` tekildir: aynı kimlikle ikinci çağrı anonsu TEKRAR
        çalmaz, ilk sonucu döner (`replayed: true`).
        """
        clean = " ".join(str(text or "").split())[:TEXT_MAX]
        rid = " ".join(str(request_id or "").split())[:128] or None
        who = " ".join(str(actor or "").split())[:160]
        key_label = " ".join(str(cache_key or "").split())[:160]

        if rid is not None:
            prior = await self._store.fetch_one(
                f"SELECT result, cached FROM {self._page_table} WHERE request_id = ?", (rid,)
            )
            if prior is not None:
                status = str(prior["result"])
                return {"status": status, "voiceCached": bool(prior["cached"]),
                        "etaSeconds": PAGE_ETA_SECONDS if status == "queued" else 0,
                        "replayed": True}

        settings = await self.settings()
        if not settings["enabled"]:
            return await self._page_result("disabled", clean, "", rid, who, key_label,
                                           cached=False, detail="Zil sistemi kapalı.")
        if not clean:
            return await self._page_result("tts_unavailable", clean, "", rid, who, key_label,
                                           cached=False, detail="Metin boş.")

        voice_key = self._voices.key(clean)
        name = await self._voices.ready_file(voice_key)
        cached = bool(name)
        if not name:
            name = await self._voices.produce_now(clean, timeout=self._page_tts_timeout)
        if not name:
            entry = await self._voices.entry(voice_key)
            detail = str(entry["error"]) if entry and entry["error"] \
                else "Anons sesi zamanında üretilemedi."
            await self._record("cagri", "sayfa", clean, False, detail, actor=who)
            return await self._page_result("tts_unavailable", clean, voice_key, rid, who,
                                           key_label, cached=False, detail=detail)

        path = self._sounds_path() / name
        try:
            await self._ensure_on_bridge(path)
            items = [_item("anons", path, settings["volume"])]
        except OSError as failure:
            detail = f"Anons dosyası okunamadı: {failure}"
            await self._record("cagri", "sayfa", clean, False, detail, actor=who)
            return await self._page_result("tts_unavailable", clean, voice_key, rid, who,
                                           key_label, cached=cached, detail=detail)

        result = await self._dispatch(items, settings, kind="cagri", edge="sayfa",
                                      name=clean, actor=who)
        status = "queued" if result["ok"] else "failed"
        if key_label:
            await self._touch_page_voice(voice_key, key_label, prepared=False)
        return await self._page_result(status, clean, voice_key, rid, who, key_label,
                                       cached=cached, detail=str(result["detail"]))

    async def prepare_page(self, text: str, *, cache_key: str | None = None) -> dict[str, Any]:
        """Sayfa sesini ÖNDEN hazırlar (arka plan kuyruğu); 60 gün tutulur.

        Çalmaz. Gece hazırlığı ve "bu öğrenciyi sık çağırıyoruz" durumu için.
        Dönüş `{"status": "cached" | "queued" | "disabled"}`.
        """
        clean = " ".join(str(text or "").split())[:TEXT_MAX]
        if not clean:
            return {"status": "disabled", "detail": "Metin boş."}
        voice_key = self._voices.key(clean)
        ready = bool(await self._voices.ready_file(voice_key))
        if not ready:
            await self._voices.require(clean)
        await self._touch_page_voice(voice_key, " ".join(str(cache_key or "").split())[:160],
                                     prepared=True)
        return {"status": "cached" if ready else "queued"}

    async def prepare_students(self, directory: Any) -> dict[str, Any]:
        """Dizindeki her öğrencinin çağrı sesini hazırlar (gece görevi).

        `directory` = `bbd_students.directory` yeteneği. Kantine ulaşılamazsa
        (`complete: false`) hiçbir şey yapılmaz; ses sırası bir sonraki geceye
        kalır.
        """
        listing = await directory.list()
        if not listing.get("complete"):
            return {"ok": False, "prepared": 0, "detail": "öğrenci dizini eksik"}
        template = await self.page_template()
        queued = 0
        for student in listing.get("students") or []:
            text = render_page(template, first_name=student.get("firstName") or "",
                               last_name=student.get("lastName") or "",
                               class_name=student.get("className") or "")
            result = await self.prepare_page(text, cache_key=f"student:{student.get('id')}")
            queued += 1 if result["status"] == "queued" else 0
        self._log.info("öğrenci çağrı sesleri hazırlandı",
                       students=len(listing.get("students") or []), queued=queued)
        return {"ok": True, "prepared": len(listing.get("students") or []), "queued": queued}

    async def _ensure_on_bridge(self, path: Path) -> None:
        """Sesi köprüye koyar (yoksa). Ajan komuttaki özeti köprüden indirir.

        Grup sesleri eşitleme döngüsüyle önden gider; anlık üretilen sayfa sesi
        o döngüyü beklerse ajan bir dakika boyunca "ses yok" der.
        """
        data = path.read_bytes()
        status = await self._bridge.try_status()
        if status.get("error"):
            return              # köprü yok: gönderim zaten başarısız olacak, sebebi orada yazar
        if content_id(data) in set(status.get("bridgeSounds") or []):
            return
        try:
            await self._bridge.upload(path.name, data)
            self._bridge.forget_status()
        except Exception as failure:  # noqa: BLE001 — köprü dışarısı
            self._log.warning("sayfa sesi köprüye yüklenemedi", file=path.name,
                              error=str(failure))

    async def _touch_page_voice(self, voice_key: str, cache_key: str, *,
                                prepared: bool) -> None:
        stamp = _now()
        await self._store.execute(
            f"INSERT INTO {self._page_voice_table} "
            f"(voice_key, cache_key, prepared_at, last_used_at) VALUES (?, ?, ?, ?) "
            f"ON CONFLICT(voice_key) DO UPDATE SET cache_key = excluded.cache_key, "
            + ("prepared_at = excluded.prepared_at" if prepared
               else "last_used_at = excluded.last_used_at"),
            (voice_key, cache_key, stamp if prepared else "", "" if prepared else stamp),
        )

    async def _page_result(self, status: str, text: str, voice_key: str, request_id: str | None,
                           actor: str, cache_key: str, *, cached: bool,
                           detail: str) -> dict[str, Any]:
        """Geçmişe yazar ve yeteneğin yanıtını kurar. Metin değil özeti yazılır."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._page_table} (at, text_hash, voice_key, result, actor, "
                f"request_id, cache_key, cached, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now(), hashlib.sha256(text.encode("utf-8")).hexdigest(), voice_key, status,
                 actor, request_id, cache_key, 1 if cached else 0, detail[:500]),
            )
        except Exception as failure:  # noqa: BLE001 — geçmiş yazılamadı diye anons düşmez
            self._log.warning("sayfa geçmişi yazılamadı", error=str(failure))
        return {"status": status, "voiceCached": cached,
                "etaSeconds": PAGE_ETA_SECONDS if status == "queued" else 0,
                "detail": detail}

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

        # Zil sonrası anonsların durumu da ekranda görünür. Görünmezse
        # kullanıcı zilin arkasından ses gelmediğinde sebebi hiçbir yerde
        # okuyamaz — "üretiliyor" beklemek, "hata" müdahale demektir.
        after: dict[str, Any] = {}
        for kind in TIME_KINDS:
            text = after_text(settings, kind)
            if not text:
                after[kind] = {"text": "", "hash": "",
                               "voice": {"state": "off", "detail": "anons istenmiyor",
                                         "seconds": 0}}
                continue
            hash_ = self._voices.key(text)
            after[kind] = {"text": text, "hash": hash_,
                           "voice": _voice_state(entries, hash_)}

        audio_state = self._audio.available() if self._audio else {"ready": False}
        scheduler_state = self._scheduler.state() if self._scheduler else {"running": False}
        agent = await self._bridge.try_status()
        # Zil bilgisayarının son cevabı günlüğe işlenir (ÖNCE: aşağıdaki
        # günlük okuması onu da görsün).
        await self.note_ack(agent)

        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._log_table} ORDER BY id DESC LIMIT 60"
        )

        total_times = sum(len(items) for items in times.values())
        migrated = bool(settings["groupsMigratedAt"])
        # Taşıma sonrası elle çağrı listesi öğrenci gruplarıdır; sesleri genel metin.
        callable_groups = await self.student_groups() if migrated else []
        for group in callable_groups:
            text = generic_text(settings, group["callName"] or group["name"], group["kind"])
            group["text"] = text
            group["hash"] = self._voices.key(text)
            group["voice"] = _voice_state(entries, group["hash"])
        missing = [group["name"]
                   for group in (callable_groups if migrated else groups)
                   if group["voice"]["state"] != "ready"]
        # Kullanılan türün anonsu hazır değilse ekran bunu da söylemeli.
        used_kinds = {time_kind(entry.get("kind"))
                      for day in times.values() for entry in day}
        missing += [f"zil sonrası ({kind})" for kind in sorted(used_kinds)
                    if after[kind]["voice"]["state"] not in ("ready", "off")]

        return {
            "settings": settings,
            "times": times,
            "groups": groups,
            "sounds": [item for item in (self._audio.sounds() if self._audio else [])
                       if not str(item["name"]).startswith("anons-")],
            "audio": audio_state,
            "scheduler": scheduler_state,
            "agent": agent,
            "afterTexts": after,
            "queue": self._voices.state(),
            "log": [dict(row) for row in rows],
            # Zil çalabilir mi? Ajan, saat ve ana şalter — üçü de gerekli.
            # Yerel çalma YALNIZ bu makinede çalıcı varsa sayılır: sunucuda
            # (ADR 0026) hoparlör yok ve "yerel" ayarı ajan arızasını
            # gizliyordu — ekran "zil hazır" derken hiçbir yerden ses çıkmazdı.
            "ready": bool(settings["enabled"]) and total_times > 0
            and (bool(agent.get("online")) or self._plays_locally(settings)),
            # Bu makinede ses çalıcı var mı (masaüstü yerel kip)? Sunucuda yok;
            # ekran "bu bilgisayardan da çal" seçeneğini o zaman hiç çizmez.
            "localAvailable": self._local_ready(),
            "library": self._library_state(agent),
            "agentAck": _ack_view(agent.get("lastAck")),
            "schedule": await self.schedule_view(settings),
            "missingVoices": missing,
            # --- zil planı ve öğrenci grupları (0.5) ---
            "profiles": await self._plan.profiles(),
            "plan": await self._plan_summary(settings, entries),
            "templateWarnings": template_warnings(settings),
            "groupsMigrated": migrated,
            "studentGroups": callable_groups,
            "studentGroupsAvailable": self._groups_capability() is not None,
        }

    async def schedule_view(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Ekranın "Program" özeti — bugün ve önümüzdeki 7 günün ETKİN zilleri.

        Tetik tablosuyla AYNI kuraldan (`_effective_row`) üretilir: ekranda
        görünen saat, zil bilgisayarına giden saattir. Eskiden ekran sıradaki
        zili zamanlayıcının iç tetiğinden okuyordu ("… · gönderim", bir dakika
        erken) ve Akademi'nin tatil günlerini, saat setlerini hiç göstermiyordu.
        """
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=planning.ISTANBUL)
        now = now.astimezone(planning.ISTANBUL)
        today = self._today()
        last = today + timedelta(days=planning.SCHEDULE_DAYS - 1)
        weekly = await self.times()
        profile_list = await self._plan.profiles()
        profiles = {item["id"]: item["times"] for item in profile_list}
        names = {item["id"]: item["name"] for item in profile_list}
        rows = await self._plan.days(today, last)
        clock_now = now.strftime("%H:%M")

        days: list[dict[str, Any]] = []
        upcoming: list[dict[str, Any]] = []
        for offset in range(planning.SCHEDULE_DAYS):
            day = today + timedelta(days=offset)
            row = rows.get(day.isoformat())
            mode, times, _calls = await self._effective_row(day, row, settings,
                                                             profiles, weekly)
            has_plan = bool(row and row["source"] == "akademi")
            override = bool(row and row["override_mode"])
            bells: list[dict[str, Any]] = []
            for entry in sorted(times, key=lambda item: str(item["time"])):
                at = str(entry["time"])
                past = offset == 0 and at <= clock_now
                bell = {"time": at, "kind": time_kind(entry.get("kind")),
                        "label": str(entry.get("label") or "")}
                bells.append({**bell, "past": past})
                if not past and settings["enabled"] and len(upcoming) < 3:
                    hour, minute = (int(part) for part in at.split(":"))
                    moment = datetime(day.year, day.month, day.day, hour, minute,
                                      tzinfo=planning.ISTANBUL)
                    upcoming.append({**bell, "date": day.isoformat(),
                                     "inMinutes": max(0, int((moment - now)
                                                             .total_seconds() // 60))})
            profile_id = str(row["profile_id"]) if has_plan and row else ""
            days.append({
                "date": day.isoformat(),
                "weekday": DAYS[day.weekday()],
                "mode": mode,
                "profileName": names.get(profile_id) if mode == "profile" else None,
                "reason": (str(row["reason"]) or None) if has_plan and row else None,
                "source": "km" if override else ("akademi" if has_plan else "none"),
                "override": override,
                "bells": bells,
            })
        return {"today": today.isoformat(), "now": now.isoformat(timespec="seconds"),
                "next": upcoming, "days": days}

    async def note_ack(self, agent: Any) -> None:
        """Zil bilgisayarının son cevabını (`lastAck`) günlükteki komuta işler.

        Köprü yalnız SON cevabı tutar; durum her okunduğunda (ekran 10 sn,
        eşitleme döngüsü 60 sn) bakılır. Zil komutları dakikalar arayla
        gittiği için her cevap yakalanır. Satır henüz yazılmadıysa (elle
        çalmada ajan çok hızlı cevap verdi) işaretlenmez, bir sonraki turda
        yeniden denenir.
        """
        ack = agent.get("lastAck") if isinstance(agent, dict) else None
        if not isinstance(ack, dict):
            return
        try:
            command_id = int(ack.get("id") or 0)
        except (TypeError, ValueError):
            return
        if command_id <= 0 or command_id == self._acked_id:
            return
        try:
            row = await self._store.fetch_one(
                f"SELECT id FROM {self._log_table} WHERE command_id = ? LIMIT 1",
                (command_id,))
            if row is None:
                return
            await self._store.execute(
                f"UPDATE {self._log_table} SET agent_ok = ?, agent_detail = ? "
                f"WHERE command_id = ? AND agent_ok = -1",
                (1 if ack.get("ok") is True else 0,
                 " ".join(str(ack.get("detail") or "").split())[:300], command_id))
        except Exception as failure:  # noqa: BLE001 — günlük notu ekranı düşürmez (K7)
            self._log.warning("ajan cevabı günlüğe yazılamadı", error=str(failure))
            return
        self._acked_id = command_id

    def _library_state(self, agent: dict[str, Any]) -> dict[str, int] | None:
        """Zil bilgisayarında olması gereken seslerden kaçı orada."""
        if self._wanted_ids is None or agent.get("error"):
            return None
        present = {str(item) for item in agent.get("sounds") or []}
        return {"wanted": len(self._wanted_ids),
                "present": len(self._wanted_ids & present)}

    def _local_ready(self) -> bool:
        """Bu makinede ses çalıcı var mı? Sunucu imajında yok (ADR 0026)."""
        if self._audio is None:
            return False
        try:
            return bool(self._audio.available().get("ready"))
        except Exception:  # noqa: BLE001 — platform yeteneği dışarısı
            return False

    def _plays_locally(self, settings: dict[str, Any]) -> bool:
        """"Bu bilgisayardan da çal" GERÇEKTEN çalabilir mi: ayar açık VE çalıcı var."""
        return bool(settings["playLocally"]) and self._local_ready()

    async def _plan_summary(self, settings: dict[str, Any],
                            entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Takvim sekmesi: önümüzdeki 14 gün, gün başına çağrılar ve ses durumu."""
        today = self._today()
        last = today + timedelta(days=planning.WINDOW_DAYS - 1)
        days = await self.plan_days(today, last)
        calls = await self._plan.calls(today, last)
        by_day: dict[str, list[dict[str, Any]]] = {}
        for row in calls:
            exact = entries.get(str(row["voice_key"]))
            generic = entries.get(str(row["generic_voice_key"]))
            by_day.setdefault(str(row["date"]), []).append({
                "at": str(row["at"]), "groupName": str(row["group_name"]),
                "groupKind": str(row["group_kind"]), "teacherName": str(row["teacher_name"]),
                "room": str(row["room"]), "subject": str(row["subject"]),
                "kind": str(row["kind"]), "state": str(row["state"]),
                "detail": str(row["detail"]),
                "voice": "ready" if exact and exact["file"] and not exact["error"]
                else ("generic" if generic and generic["file"] and not generic["error"]
                      else "missing"),
            })
        received = [day["receivedAt"] for day in days if day["source"] == "akademi"
                    and day["receivedAt"]]
        for day in days:
            day["calls"] = by_day.get(day["date"], [])
        return {"days": days, "sessionCalls": self._session_calls(settings),
                "lastReceivedAt": max(received) if received else None,
                "today": today.isoformat()}

    # ============================================================ zamanlama

    async def reschedule(self) -> dict[str, Any]:
        """Önümüzdeki 7 GÜNÜN tarihli tetik tablosunu kurar (sözleşme §3.4).

        Platform zamanlayıcısı haftalıktır (gün + saat); yedi gün her hafta
        gününe TEK tarih düşürür. Her tarih D için kip:
          · `normal`  → D'nin haftalık saatleri,
          · `profile` → saat setinin saatleri,
          · `off`     → tetik yok (zil, anons ve çağrı çalmaz).
        Oturum çağrıları açıksa hiçbir zil saatine denk gelmeyen çağrı saatleri
        için YALNIZ-ÇAĞRI tetikleri de kurulur. Her tetik yükünde `date` ve
        `fireDate` taşır; ateşlendiğinde `fireDate` bugün değilse atlanır.

        Her zil saati İKİ tetikleyici doğurur:
          · `dispatch` — zilden bir dakika önce; komutu köprüye yazar. Komut
            `playAt` damgası taşır, ajan o ana kadar bekler.
          · `local`    — tam saatinde; yerel hoparlörden çalar (açıksa).

        Plan uygulanınca, saatler / saat setleri / ayarlar kaydedilince, acil
        kapatmada ve her gece 00:05'te yeniden kurulur.
        """
        if self._scheduler is None:
            return {"ok": False, "detail": "Zamanlayıcı yeteneği yok."}

        self._plan_window_roll()
        settings = await self.settings()
        if not settings["enabled"]:
            self._scheduler.clear("bell")
            return {"ok": True, "triggers": 0, "detail": "ana şalter kapalı"}

        lead = max(1, (self._bridge.lead_seconds + 59) // 60)   # dakikaya yuvarla
        today = self._today()
        last = today + timedelta(days=planning.SCHEDULE_DAYS - 1)
        weekly = await self.times()
        profiles = {item["id"]: item["times"] for item in await self._plan.profiles()}
        rows = await self._plan.days(today, last)
        calls_by_day: dict[str, set[str]] = {}
        for call in await self._plan.calls(today, last):
            if str(call.get("state")) == "cancelled":
                continue                      # silinen sınıfın çağrısı — tetik kurulmaz
            calls_by_day.setdefault(str(call["date"]), set()).add(str(call["at"]))

        triggers: list[Trigger] = []
        for offset in range(planning.SCHEDULE_DAYS):
            day = today + timedelta(days=offset)
            mode, times, calls_on = await self._effective_row(
                day, rows.get(day.isoformat()), settings, profiles, weekly)
            if mode == "off":
                continue
            slots: dict[str, str] = {entry["time"]: time_kind(entry.get("kind"))
                                     for entry in times}
            if calls_on:
                for at in calls_by_day.get(day.isoformat(), set()):
                    slots.setdefault(at, "")          # yalnız-çağrı tetiği
            for at, kind in sorted(slots.items()):
                label = f"{day.isoformat()} {at} {kind or 'çağrı'}"
                fire = datetime.combine(day, datetime.strptime(at, "%H:%M").time()) \
                    - timedelta(minutes=lead)
                base = {"date": day.isoformat(), "at": at, "kind": kind, "bell": bool(kind),
                        "mode": mode}
                triggers.append(Trigger(
                    day=DAYS[fire.weekday()], time=fire.strftime("%H:%M"),
                    label=f"{label} · gönderim",
                    payload={**base, "phase": "dispatch",
                             "fireDate": fire.date().isoformat()},
                ))
                if self._plays_locally(settings):
                    triggers.append(Trigger(
                        day=DAYS[day.weekday()], time=at, label=f"{label} · yerel",
                        payload={**base, "phase": "local", "fireDate": day.isoformat()},
                    ))

        count = self._scheduler.set_plan("bell", triggers, self._on_trigger)
        return {"ok": True, "triggers": count}

    def _plan_window_roll(self) -> None:
        """Gece 00:05: pencere bir gün kayar, eski plan günleri silinir."""
        if self._scheduler is None:
            return
        self._scheduler.set_plan("bell-plan", [
            Trigger(day=day, time="00:05", label="zil planı penceresi",
                    payload={"phase": "roll"})
            for day in DAYS
        ], self._on_roll)

    async def _on_roll(self, _trigger: Any) -> None:
        try:
            await self._plan.purge(self._today() - timedelta(days=planning.KEEP_DAYS))
            await self._ensure_plan_voices(await self.settings())
        except Exception as failure:  # noqa: BLE001 — gece işi zili düşürmez (K7)
            self._log.warning("zil planı gece bakımı patladı", error=str(failure))
        await self.reschedule()

    async def _on_trigger(self, trigger: Any) -> None:
        payload = getattr(trigger, "payload", {}) or {}
        today = self._today()
        if str(payload.get("fireDate") or "") != today.isoformat():
            # Pencere eskimiş (gece yenilemesi kaçmış): bu tetik başka bir tarihe
            # aitti. ATLANIR ve tablo yeniden kurulur.
            self._log.warning("zil tetiği atlandı — tarih bugün değil",
                              date=payload.get("date"), fire=payload.get("fireDate"))
            await self.reschedule()
            return
        try:
            target = date.fromisoformat(str(payload.get("date") or ""))
        except ValueError:
            return
        at = str(payload.get("at") or "")
        settings = await self.settings()
        if not settings["enabled"]:
            return
        # Tetik anında YEREL plan yeniden okunur: tetik kurulduktan sonra gelen
        # acil kapatma ya da iptal de geçerli olsun. Ağa çıkılmaz.
        mode, times, calls_on = await self._effective(target, settings)
        if mode == "off":
            return
        entry = next((item for item in times if item["time"] == at), None)
        items = await self._bell_items(settings, time_kind(entry.get("kind"))) \
            if entry is not None else []
        call_items: list[dict[str, Any]] = []
        outcomes: list[tuple[str, str, str]] = []
        if calls_on:
            call_items, outcomes = await self._call_items(settings, target, at,
                                                          after_bell=bool(items))
        if not items and not call_items:
            if outcomes and str(payload.get("phase")) != "local":
                await self._finish_calls(target, outcomes, ok=False, detail="")
            return

        if str(payload.get("phase")) == "local":
            await self._play_locally(items + call_items, settings, kind="zil", edge="plan")
            return

        offset = 0 if items else int(self._session_calls(settings)["offsetSeconds"])
        moment = self._play_at(target, at, offset)
        result = await self._bridge.try_send(items + call_items, play_at=moment)
        command_id = int(result.get("id") or 0)
        if items:
            await self._record("zil", "plan", ", ".join(item["name"] for item in items),
                               result["ok"], f"ajan · {result['detail']}",
                               command_id=command_id)
        if outcomes:
            await self._finish_calls(target, outcomes, ok=bool(result["ok"]),
                                     detail=str(result["detail"]), command_id=command_id)
        await self._plan.mark_dispatch(target)

    def _play_at(self, day: date, at: str, offset: int = 0) -> str:
        """Ajanın bekleyeceği MUTLAK an. Çok geç kalınmışsa (5 dk) "" — hemen çalsın.

        `_next_occurrence`ın yerine geçer: o makinenin saat dilimine bakıyordu;
        sunucu UTC'de koştuğu için damga İstanbul'a göre üç saat kayardı.
        """
        moment = planning.play_at(day, at, offset_seconds=offset)
        if datetime.fromisoformat(moment) < self._clock() - timedelta(minutes=5):
            return ""
        return moment

    async def _call_items(self, settings: dict[str, Any], day: date, at: str, *,
                          after_bell: bool) -> tuple[list[dict[str, Any]],
                                                     list[tuple[str, str, str]]]:
        """O dakikanın oturum çağrıları — grup adına göre sıralı, `maxPerSlot` sınırlı.

        Ses seçimi (ADR 0027 — ANONS ZİLİ SUSTURAMAZ): kesin metnin sesi hazırsa
        o; değilse grubun genel sesi; o da yoksa çağrı ATLANIR ve `anons` hatası
        günlüğe yazılır. Zil her durumda çalar.
        """
        config = self._session_calls(settings)
        rows = sorted(await self._plan.calls_at(day, at),
                      key=lambda row: fold(str(row["group_name"])))
        items: list[dict[str, Any]] = []
        outcomes: list[tuple[str, str, str]] = []
        root = self._sounds_path()
        for row in rows:
            if len(items) >= int(config["maxPerSlot"]):
                outcomes.append((str(row["id"]), "skipped", "atlandı: kapasite"))
                continue
            name = await self._voices.ready_file(str(row["voice_key"]))
            detail = "kesin metin"
            if not name:
                name = await self._voices.ready_file(str(row["generic_voice_key"]))
                detail = "genel çağrı (kesin ses hazır değil)"
            if not name:
                outcomes.append((str(row["id"]), "failed", "anons: ses hazır değil"))
                await self._record("anons", "plan", str(row["group_name"]), False,
                                   "Çağrı sesi hazır değil; zil yalnız çaldı.")
                continue
            try:
                item = _item("anons", root / name, settings["volume"])
            except OSError as failure:
                outcomes.append((str(row["id"]), "failed", f"anons dosyası okunamadı: {failure}"))
                continue
            pause = int(config["gapSeconds"]) if items else (
                int(config["offsetSeconds"]) if after_bell else 0)
            if pause:
                item["pauseBefore"] = pause
            items.append(item)
            outcomes.append((str(row["id"]), "dispatched", detail))
        return items, outcomes

    async def _finish_calls(self, day: date, outcomes: list[tuple[str, str, str]], *,
                            ok: bool, detail: str, command_id: int = 0) -> None:
        """Çağrı sonuçlarını `mod_bell_call.state`'e işler ve günlüğe ÖZET yazar."""
        sent = 0
        for call_id, state, note in outcomes:
            if state == "dispatched" and not ok:
                state, note = "failed", f"köprü: {detail}"
            sent += 1 if state == "dispatched" else 0
            await self._plan.mark_call(call_id, state, note)
        skipped = len(outcomes) - sent
        await self._record("cagri-plan", "plan", day.isoformat(),
                           skipped == 0 and bool(outcomes),
                           f"{sent} çağrı gönderildi · {skipped} atlandı/başarısız"
                           + (f" · ajan · {detail}" if detail else ""),
                           command_id=command_id)

    async def _bell_items(self, settings: dict[str, Any],
                          kind: str = DEFAULT_TIME_KIND) -> list[dict[str, Any]]:
        """Otomatik zilin çalacağı adımlar: ZİL, arkasından ANONS.

        Ders zilinin arkasından "İyi dersler.", teneffüs zilinin arkasından
        "İyi teneffüsler." gider. Adımlar sırayla çalınır — köprü komutu bir
        DİZİ taşır, ajan onları üst üste bindirmeden çalar ve yerel yol da
        `await` ile birini bitirmeden ötekine geçmez.

        ANONS ZİLİ SUSTURAMAZ. Sesi henüz üretilmemişse (Vertex sırası
        bitmemiş, kota dolmuş, taze kurulum) zil YALNIZ BAŞINA çalar ve sebep
        günlüğe yazılır. Tersi — anons yok diye zili de çalmamak — okulu
        sessiz bırakırdı ve bu, eksik bir cümleden çok daha büyük bir arıza.
        """
        bell = self._resolve_sound(settings["bellSound"])
        if bell is None:
            await self._record("zil", "plan", settings["bellSound"], False,
                               "Zil sesi dosyası bulunamadı.")
            return []

        items = [_item("zil", bell, settings["volume"])]
        announcement, reason = await self._after_item(settings, kind)
        if announcement is not None:
            items.append(announcement)
        elif reason:
            await self._record("anons", "plan", after_text(settings, kind), False, reason)
        return items

    async def _after_item(self, settings: dict[str, Any],
                          kind: str) -> tuple[dict[str, Any] | None, str]:
        """Zil sonrası anonsun çalma adımı. Hazır değilse `(None, sebep)`."""
        text = after_text(settings, kind)
        if not text:
            return None, ""          # kullanıcı metni boşalttı: anons istemiyor
        name = await self._voices.ready_file(self._voices.key(text))
        if not name:
            entry = await self._voices.entry(self._voices.key(text))
            detail = str(entry["error"]) if entry and entry["error"] \
                else "Anons sesi henüz üretilmedi."
            return None, detail
        path = self._sounds_path() / name
        try:
            return _item("anons", path, settings["volume"]), ""
        except OSError as failure:
            return None, f"Anons dosyası okunamadı: {failure}"

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
        if settings["groupsMigratedAt"]:
            return await self._call_student_group(group_id, settings, actor=actor)
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

    async def _call_student_group(self, group_id: str, settings: dict[str, Any], *,
                                  actor: str) -> dict[str, Any]:
        """Taşıma sonrası elle çağrı (§3.4): grup `bbd_students.groups`'tan gelir.

        Bugünün planında o grubun ±30 dk içinde oturumu varsa o oturumun
        (öğretmen adlı) metni, yoksa grubun genel metni çalar. Kesin sesin
        hazır olmadığı durumda da genel metne düşülür. Zil çalmaz.
        """
        group = next((item for item in await self.student_groups()
                      if item["id"] == group_id), None)
        if group is None:
            return await self._fail("cagri", "manuel", group_id, "Grup bulunamadı.")
        spoken = group["callName"] or group["name"]
        now = self._clock()
        today = self._today()
        minutes = now.hour * 60 + now.minute
        nearest: dict[str, Any] | None = None
        for row in await self._plan.calls(today, today):
            if str(row["group_ref"]) != f"km_group:{group_id}":
                continue
            hour, minute = (int(part) for part in str(row["at"]).split(":"))
            distance = abs(hour * 60 + minute - minutes)
            if distance <= MANUAL_CALL_WINDOW_MINUTES and (
                    nearest is None or distance < nearest["distance"]):
                nearest = {**row, "distance": distance}

        candidates: list[str] = []
        if nearest is not None:
            candidates.append(session_text(settings, nearest))
        candidates.append(generic_text(settings, spoken, group["kind"]))
        name = ""
        for text in candidates:
            name = await self._voices.ready_file(self._voices.key(text))
            if name:
                break
        if not name:
            await self._voices.require(candidates[-1])
            return await self._fail("cagri", "manuel", group["name"],
                                    "Anons sesi henüz üretilmedi.")
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

        if self._plays_locally(settings):
            local = await self._play_locally(items, settings, kind=kind, edge=edge,
                                             record=False)
            detail = f"{detail} · yerel · {local['detail']}"
            ok = result["ok"] or local["ok"]
        else:
            ok = result["ok"]

        await self._record(kind, edge, name, ok, detail, actor=actor,
                           command_id=int(result.get("id") or 0))
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
                      detail: str, *, actor: str = "", command_id: int = 0) -> None:
        await self._store.execute(
            f"INSERT INTO {self._log_table} "
            f"(at, group_id, group_name, edge, sound, ok, detail, kind, command_id) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), actor, name, edge, name, 1 if ok else 0, detail[:500], kind,
             int(command_id or 0)),
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

        # ZİL SONRASI ANONSLAR DA LİSTEDE. Buraya konmazlarsa `sync_sounds`
        # onları köprüde "gereksiz" sayıp sildirir; zil çalar, arkasından
        # sessizlik gelir ve sebebi hiçbir ekranda görünmez.
        # ÖĞRENCİ ÇAĞIRMA SESLERİ DE LİSTEDE (`_live_voice_keys` → 005 göçü).
        # Girmeselerdi her sayfa sesi bir dakika içinde köprüden silinir ve
        # aynı öğrenciyi ikinci çağırış yeniden yükleme beklerdi.
        root = self._sounds_path()
        for key in sorted(await self._live_voice_keys(settings)):
            name = await self._voices.ready_file(key)
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
        self._wanted_ids = set(wanted)
        status = await self._bridge.try_status()
        if status.get("error"):
            return {"ok": False, "detail": status["error"]}
        # Ekran kapalıyken de zil bilgisayarının cevabı günlüğe işlensin.
        await self.note_ack(status)

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
        await self._ensure_time_voices(settings)
        await self._ensure_group_voices(settings)
        await self._ensure_plan_voices(settings)
        return await self.reschedule()


# ------------------------------------------------------------------- yardım


def _item(kind: str, path: Path, volume: int) -> dict[str, Any]:
    """Köprüye giden tek çalma adımı."""
    data = path.read_bytes()
    return {"kind": kind, "hash": content_id(data), "name": path.name,
            "volume": int(volume)}


def _ack_view(ack: Any) -> dict[str, Any] | None:
    """Köprüdeki son ajan cevabı → ekran: hangi komut, çaldı mı, ne dedi, ne zaman."""
    if not isinstance(ack, dict) or not ack.get("id"):
        return None
    return {"id": ack.get("id"), "ok": ack.get("ok") is True,
            "detail": " ".join(str(ack.get("detail") or "").split())[:300],
            "at": str(ack.get("at") or "")}


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
