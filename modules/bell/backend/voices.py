"""Anons sesi önbelleği ve üretim kuyruğu.

TEK KURAL: zil çalarken buluta çıkılmaz. Ses, içerik DEĞİŞTİĞİNDE üretilir —
grup eklendi, adı değişti, metin şablonu düzenlendi, ses/model değişti. Çalma
anında yalnız diskteki dosya açılır.

ÖNBELLEK ANAHTARI metnin kendisi değil, `sha256(metin | model | ses)` üçlüsüdür.
Aynı metni farklı sesle istemek yeni bir kayıttır; aynı metni ikinci kez aynı
sesle istemek hiç istek doğurmaz. Kaldırılan bir grup aynı adla geri açılırsa
özet aynı çıkar ve ses yeniden üretilmez.

429 DİSİPLİNİ burada durur, `speech.py`de değil: istemci tek çağrı yapar,
sırayı, beklemeyi ve geri çekilmeyi kuyruk yönetir.
  · aynı anda tek üretim (tek işçi)
  · çağrılar arası en az `min_interval_seconds`
  · 429/5xx → 2, 4, 8, 16, 32 sn (Retry-After varsa o yeğlenir), en çok 5 deneme
  · kalıcı hata (400/401/403/404) hiç yeniden denenmez — kotayı boşa harcar

BAŞARISIZLIK SAKLANMAZ. Denemeler bitince hata `mod_bell_voice.error` alanına
yazılır; ekran o sesi kırmızı gösterir ve onu kullanan düğmeyi kapatır. Sesi
olmadığı halde basılabilen bir "Çağır" düğmesi, sessizlikle biten bir çağrıdır.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .speech import SpeechError, VertexSpeech

#: Üretilen dosyaların adı. Zil sesi seçicisi bu öneki süzerek gizler —
#: anonslar zil sesi değildir, listede yan yana durmaları kafa karıştırır.
PREFIX = "anons-"

#: Dosya adında kullanılan özet uzunluğu. Tam sha256 gereksiz uzun; 16 hane
#: çakışma için fazlasıyla yeterli ve klasör okunur kalıyor.
STEM_LENGTH = 16

#: Grup adının anons metnine girmeden önceki üst sınırı.
NAME_MAX = 60

_UNSAFE = re.compile(r"[^0-9a-f]")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def number(value: Any, fallback: float) -> float:
    """Ayardan sayı okur. Yalnız YOKLUK varsayılana döner, sıfır dönmez.

    `float(section.get(ad) or 2.0)` yazmak yaygın ama yanlış: ayarda 0 yazan
    kullanıcı "bekleme" demek isterken sessizce 2 saniye bekletilirdi.
    """
    if value is None or value == "":
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def digest(text: str, model: str, voice: str) -> str:
    """Önbellek anahtarı. Metin boşluk normalize edilerek özetlenir."""
    clean = " ".join(str(text or "").split())
    raw = f"{clean}|{model}|{voice}".encode()
    return hashlib.sha256(raw).hexdigest()


def file_name(hash_: str) -> str:
    """Özetten dosya adı. Özet dışarıdan gelirse ad uydurulamasın diye süzülür."""
    safe = _UNSAFE.sub("", hash_.lower())[:STEM_LENGTH]
    return f"{PREFIX}{safe}.wav"


def render(template: str, group_name: str) -> str:
    """`{grup}` yerine grup adını koyar.

    Şablonda yer tutucu yoksa metin olduğu gibi kullanılır — kullanıcı sabit bir
    cümle yazmak isteyebilir ve bunu bir hata sayıp reddetmek gereksiz engeldir.
    """
    name = " ".join(str(group_name or "").split())[:NAME_MAX]
    return " ".join(str(template or "").replace("{grup}", name).split())


class VoiceLibrary:
    """Ses önbelleği (`mod_bell_voice`) + arka plan üretim kuyruğu."""

    def __init__(self, *, store: Any, log: Any, speech: VertexSpeech,
                 sounds_path: Path, config: dict[str, Any]) -> None:
        self._store = store
        self._log = log
        self._speech = speech
        self._sounds = Path(sounds_path)
        self._table = store.table("voice")

        section = config if isinstance(config, dict) else {}
        # `or` KULLANILMAZ: bu alanlarda 0 geçerli bir değerdir ("hiç bekleme")
        # ve `x or 2.0` yazmak onu sessizce varsayılana çevirirdi.
        self._min_interval = number(section.get("min_interval_seconds"), 1.0)
        self._max_attempts = max(1, int(number(section.get("max_attempts"), 5)))
        self._backoff = number(section.get("backoff_seconds"), 2.0)
        self._backoff_cap = number(section.get("backoff_cap_seconds"), 32.0)

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._pending: set[str] = set()
        self._worker: asyncio.Task[None] | None = None
        self._last_call: float = 0.0
        # Test edilebilirlik: kuyruk gerçek zamanı beklemesin diye değiştirilebilir.
        self._sleep = asyncio.sleep

    # ---------------------------------------------------------------- yaşam

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="km-bell-voices")

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    # ---------------------------------------------------------------- okuma

    def key(self, text: str) -> str:
        """Metnin önbellek anahtarı — o anki model ve sese göre.

        Çağıranın `digest()`i kendi başına çağırıp model/ses adını tekrar
        yazması gerekmesin: ikisi burada tek yerde duruyor.
        """
        return digest(text, self._speech.model, self._speech.voice)

    async def entry(self, hash_: str) -> dict[str, Any] | None:
        return await self._store.fetch_one(
            f"SELECT * FROM {self._table} WHERE hash = ?", (hash_,)
        )

    async def entries(self) -> dict[str, dict[str, Any]]:
        rows = await self._store.fetch_all(f"SELECT * FROM {self._table}")
        return {str(row["hash"]): dict(row) for row in rows}

    async def ready_file(self, hash_: str) -> str:
        """Çalınabilir dosya adı; hazır değilse boş metin.

        Kayıt "hazır" der ama dosya diskten silinmişse hazır DEĞİLDİR. Bu kontrol
        ucuz ve tek gerçek koruma: veri tabanı ile klasör ayrı ayrı yaşıyor.
        """
        row = await self.entry(hash_)
        if row is None or not row["file"] or row["error"]:
            return ""
        if not (self._sounds / str(row["file"])).is_file():
            return ""
        return str(row["file"])

    def state(self) -> dict[str, Any]:
        return {"queued": len(self._pending),
                "running": self._worker is not None and not self._worker.done()}

    # ---------------------------------------------------------------- yazma

    async def require(self, text: str, *, force: bool = False) -> str:
        """Metnin sesini ister ve özetini döner. Gerekirse kuyruğa alır.

        `force` yalnız "yeniden üret" düğmesi içindir: var olan kaydı siler ve
        yeniden sıraya koyar.
        """
        clean = " ".join(str(text or "").split())
        if not clean:
            return ""

        hash_ = digest(clean, self._speech.model, self._speech.voice)
        existing = await self.entry(hash_)

        if existing is not None and not force:
            fresh = bool(existing["file"]) and not existing["error"] \
                and (self._sounds / str(existing["file"])).is_file()
            if fresh:
                return hash_
            # Kayıt var ama kullanılamaz (hata almış ya da dosyası kaybolmuş):
            # yeniden denemek için sıraya alınır.

        await self._store.execute(
            f"INSERT INTO {self._table} (hash, text, model, voice, created_at) "
            f"VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT(hash) DO UPDATE SET text = excluded.text, error = ''",
            (hash_, clean, self._speech.model, self._speech.voice, now()),
        )
        self._enqueue(hash_)
        return hash_

    def _enqueue(self, hash_: str) -> None:
        if hash_ in self._pending:
            return
        self._pending.add(hash_)
        self._queue.put_nowait(hash_)
        self.start()

    async def sweep(self) -> int:
        """Eksik ya da hatalı kayıtları yeniden sıraya alır. Kaç tane olduğunu döner."""
        rows = await self._store.fetch_all(f"SELECT hash, file, error FROM {self._table}")
        count = 0
        for row in rows:
            usable = bool(row["file"]) and not row["error"] \
                and (self._sounds / str(row["file"])).is_file()
            if not usable:
                await self._store.execute(
                    f"UPDATE {self._table} SET error = '' WHERE hash = ?", (row["hash"],)
                )
                self._enqueue(str(row["hash"]))
                count += 1
        return count

    async def forget(self, hash_: str) -> None:
        """Kaydı ve dosyasını siler. Yalnız ses/model değişince çağrılır."""
        row = await self.entry(hash_)
        if row is not None and row["file"]:
            try:
                (self._sounds / str(row["file"])).unlink(missing_ok=True)
            except OSError as failure:      # dosya gitmezse kayıt da kalsın
                self._log.warning("anons dosyası silinemedi", error=str(failure))
                return
        await self._store.execute(f"DELETE FROM {self._table} WHERE hash = ?", (hash_,))

    # ---------------------------------------------------------------- kuyruk

    async def _run(self) -> None:
        while True:
            hash_ = await self._queue.get()
            try:
                await self._produce(hash_)
            except asyncio.CancelledError:
                self._pending.discard(hash_)
                raise
            except Exception as failure:  # noqa: BLE001 — kuyruk ASLA ölmemeli (K7)
                self._log.error("ses üretim turu patladı", hash=hash_, error=str(failure))
            finally:
                self._pending.discard(hash_)
                self._queue.task_done()

    async def _produce(self, hash_: str) -> None:
        row = await self.entry(hash_)
        if row is None:
            return          # aradan silinmiş
        text = str(row["text"])

        delay = self._backoff
        last = ""

        for attempt in range(1, self._max_attempts + 1):
            await self._throttle()
            try:
                speech = await self._speech.synthesize(text)
            except SpeechError as failure:
                last = str(failure)
                if not failure.retryable or attempt == self._max_attempts:
                    break
                wait = failure.retry_after or delay
                self._log.warning("ses üretimi yeniden denenecek",
                                  attempt=attempt, wait=round(wait, 1),
                                  status=failure.status)
                await self._sleep(wait)
                delay = min(delay * 2, self._backoff_cap)
                continue

            await self._write(hash_, speech)
            return

        await self._store.execute(
            f"UPDATE {self._table} SET error = ?, file = '' WHERE hash = ?",
            (last[:500] or "Bilinmeyen hata.", hash_),
        )
        self._log.error("ses üretilemedi", hash=hash_, detail=last[:200])

    async def _throttle(self) -> None:
        """Çağrılar arası en az `min_interval_seconds` bırakır."""
        loop = asyncio.get_running_loop()
        gap = self._min_interval - (loop.time() - self._last_call)
        if gap > 0:
            await self._sleep(gap)
        self._last_call = loop.time()

    async def _write(self, hash_: str, speech: Any) -> None:
        """Dosyayı diske yazar ve kaydı hazır işaretler.

        Önce geçici ada yazılıp sonra taşınır: `audio` yeteneği klasörü listelerken
        yarım bir dosyaya rastlamamalı, yoksa "ses var" görünüp çalmaz.
        """
        self._sounds.mkdir(parents=True, exist_ok=True)
        name = file_name(hash_)
        target = self._sounds / name
        staging = target.with_suffix(".part")

        staging.write_bytes(speech.data)
        staging.replace(target)

        await self._store.execute(
            f"UPDATE {self._table} SET file = ?, bytes = ?, seconds = ?, "
            f"model = ?, voice = ?, created_at = ?, error = '' WHERE hash = ?",
            (name, len(speech.data), speech.seconds, speech.model, speech.voice,
             now(), hash_),
        )
        self._log.info("anons sesi üretildi", file=name, seconds=speech.seconds)
