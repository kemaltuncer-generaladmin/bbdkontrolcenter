"""ClamAV motoru — `clamdscan` birincil, `clamscan` yedek (ADR 0009 §2).

DESEN `km_platform/audio/player.py` İLE AYNI: hızlı yol birincil, bağımsız yol
yedek, ikisi de yoksa yetenek "hazır değil" der ve çağıran bunu GÖRÜR. Sessizce
başarısız olmaz — çalışmayan bir virüs taraması, fark edilmeyen bir arızadır.

  · Birincil: `clamdscan --fdpass` — clamd imzaları bellekte tutar, tarama
    hızlıdır. `--fdpass` dosyayı istemci açıp betimleyiciyi daemon'a geçirir;
    böylece okuma yetkisi clamd'in değil, uygulamayı çalıştıran kullanıcınındır
    (ADR 0009 §4).
  · Yedek: `clamscan` — daemon yoksa çalışır, imzaları her taramada diskten
    yükler, belirgin biçimde yavaştır.

YEDEĞE YALNIZ DAEMON YOKKEN DÜŞÜLÜR. "Çıkış kodu 2" tek başına yeterli değil:
o kod izin hatası da demek olabilir ve aynı taramayı yavaş yoldan tekrarlamak
saatler yakardı. Bağlantı hatası çıktıdan tanınır.

AYRI SÜREÇ + ZAMAN AŞIMI (K7): takılan bir tarama çekirdeği kilitleyemez.
Çıktı satır satır AKITILARAK okunur; tam tarama yüz binlerce `OK` satırı
üretir ve hepsini bellekte biriktirmek kabul edilemez. Bellekte yalnızca
sayaçlar ile sınırlı sayıda tehdit/atlanan yol kaydı durur.

ATLANAN YOL "TEMİZ"İ ENGELLER. Erişilemeyen yol raporda listelenir ve tarama
"temiz" olarak raporlanamaz (ADR 0009 §4 — bağlayıcı). Bilerek hariç tutulan
yol ayrı tutulur: o, yöneticinin ilan ettiği bir karardır, taramanın eksiği
değildir.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ sabitler

#: Motor durumu. Ekran bu değere bakarak "kurulu değil" ile "hazırlanıyor"u
#: ayırır; ikisi bambaşka cümlelerdir ve kullanıcının yapacağı iş farklıdır.
STATE_MISSING = "missing"
STATE_PREPARING = "preparing"
STATE_READY = "ready"
STATE_UNAVAILABLE = "unavailable"

#: Tarama sonucu. `clean` YALNIZ hiçbir engelleyici eksik yokken verilir.
VERDICT_CLEAN = "clean"
VERDICT_INCOMPLETE = "incomplete"
VERDICT_INFECTED = "infected"
VERDICT_FAILED = "failed"

#: Atlanan yolun nedeni. `blocking` olanlar taramanın "temiz" sayılmasını
#: engeller; olmayanlar yalnızca raporda görünür.
REASON_UNREADABLE = "erişilemedi"
REASON_ERROR = "tarayıcı hatası"
REASON_MISSING = "yol yok"
REASON_EXCLUDED = "hariç tutuldu"

#: Raporda tutulan en fazla tehdit / atlanan yol kaydı. Binlerce bulaşmalı bir
#: makinede listeyi sınırsız büyütmek belleği de ekranı da boğardı; sayaç
#: gerçeği söylemeye devam eder.
MAX_RECORDS = 500

#: Tanınmayan satırlardan saklanan kuyruk — hata mesajının gövdesi buradan
#: kurulur ve daemon bağlantı hatası burada aranır.
MAX_NOTES = 20

#: clamd'e ping süresi. Ekran durumu için; taramayı bağlamaz.
PING_TIMEOUT = 5.0

#: Okuma tamponu ve satır sonu görmeden büyüyebilecek en fazla yığın.
CHUNK = 65536
MAX_LINE = 1 << 20

#: Öldürülen sürecin kapanması için tanınan mühlet. Aşılırsa beklenmez:
#: kapanmayan bir süreç yüzünden ekranın "tarama sürüyor" demeye devam etmesi,
#: taramanın kendisinden büyük bir arıza olurdu.
KILL_GRACE = 10.0

#: freshclam'in indirdiği ana imza dosyaları. `.cvd` tam paket, `.cld`
#: artımlı güncellemeyle oluşan yerel biçim; ikisinden biri yeterlidir.
MAIN_FILES = ("main.cvd", "main.cld")
DAILY_FILES = ("daily.cvd", "daily.cld")

#: clamdscan daemon'a ulaşamadığında çıktısında görünen imzalar. Yedeğe
#: düşmenin TEK koşulu budur.
#:
#: Liste bilerek DAR. "no such file or directory" gibi genel bir parça da
#: bağlantı hatasında geçer ama sıradan bir dosya hatasında da geçer; onu
#: eklemek, izin sorunu yüzünden başarısız olan bir taramayı saatler süren
#: yavaş yoldan tekrarlatırdı.
DAEMON_DOWN = (
    "could not connect to clamd",
    "can't connect to clamd",
    "could not lookup",
    "clamd is not running",
    "connection refused",
)

#: Özet bloğundan okunan başlıklar. Beyaz liste bilerek dar: her iki nokta
#: üst üste içeren satırı özet sanmak, `ERROR: ...` satırını özet alanına
#: yazardı ve hata metni kaybolurdu.
SUMMARY_KEYS = frozenset({
    "known viruses", "engine version", "scanned directories", "scanned files",
    "infected files", "data scanned", "data read", "time", "start date", "end date",
})


def now_utc() -> str:
    """Zaman damgası — saniye hassasiyetinde, saat dilimi bilgisiyle."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class EngineNotReady(RuntimeError):
    """Motor tarama yapabilecek durumda değil. Mesaj kullanıcıya gösterilir."""


# ------------------------------------------------------------------- sonuç


def skip_entry(path: str, reason: str, *, blocking: bool) -> dict[str, Any]:
    return {"path": path, "reason": reason, "blocking": blocking}


def decide_verdict(*, threats: list[dict[str, str]], skipped: list[dict[str, Any]],
                   error: str, returncode: int | None) -> str:
    """Taramanın sonucu.

    SIRA ÖNEMLİ. `clean` en sonda ve en dar koşulla verilir:

      · hata varsa → başarısız,
      · tehdit varsa → bulaşma (atlanan yol olsa da bulaşma daha ağırdır),
      · tarayıcı 0/1 dışında bir kodla döndüyse → eksik. Kısmi sonuç
        gelmiş olsa bile "temiz" DENMEZ; ClamAV 2 ile "bir şeyler okunamadı"
        demektedir ve neyin okunamadığını bilmiyoruz.
      · engelleyici atlanan yol varsa → eksik (ADR 0009 §4).
    """
    if error:
        return VERDICT_FAILED
    if threats:
        return VERDICT_INFECTED
    if returncode not in (0, 1):
        return VERDICT_INCOMPLETE
    if any(entry.get("blocking") for entry in skipped):
        return VERDICT_INCOMPLETE
    return VERDICT_CLEAN


@dataclass(slots=True)
class ScanOutcome:
    """Tek bir tarama koşusunun sonucu."""

    engine: str = ""
    verdict: str = VERDICT_FAILED
    files: int = 0
    seconds: float = 0.0
    returncode: int | None = None
    error: str = ""
    engine_version: str = ""
    threats: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def blocking(self) -> list[dict[str, Any]]:
        """"Temiz" denmesini engelleyen atlanan yollar."""
        return [entry for entry in self.skipped if entry.get("blocking")]

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "verdict": self.verdict,
            "files": self.files,
            "seconds": round(self.seconds, 2),
            "returncode": self.returncode,
            "error": self.error,
            "engineVersion": self.engine_version,
            "threats": self.threats,
            "threatCount": len(self.threats),
            "skipped": self.skipped,
            # Ekranda görünen "atlanan yol sayısı" ENGELLEYİCİ olanları sayar;
            # hariç tutulan yol ayrı satırda anlatılır.
            "skippedCount": len(self.blocking),
            "excludedCount": len(self.skipped) - len(self.blocking),
        }


# ---------------------------------------------------------------- toplayıcı


class Collector:
    """Tarayıcı çıktısını satır satır okur ve sayar.

    Bellekte yalnız sayaçlar ile SINIRLI listeler durur: tam tarama yüz
    binlerce satır üretir, hepsini biriktirmek gereksiz ve tehlikelidir.
    """

    __slots__ = (
        "_dropped_skipped",
        "_dropped_threats",
        "_ticks",
        "files",
        "notes",
        "on_progress",
        "skipped",
        "summary",
        "threats",
    )

    def __init__(self, on_progress: Any = None) -> None:
        self.files = 0
        self.threats: list[dict[str, str]] = []
        self.skipped: list[dict[str, Any]] = []
        self.summary: dict[str, str] = {}
        self.notes: list[str] = []
        self.on_progress = on_progress
        self._dropped_threats = 0
        self._dropped_skipped = 0
        self._ticks = 0

    # ------------------------------------------------------------ ayrıştırma

    def line(self, text: str) -> None:
        raw = text.strip()
        if not raw or raw.startswith("-----"):
            return

        if raw.endswith(" FOUND"):
            path, _, name = raw[: -len(" FOUND")].rpartition(": ")
            self._threat(path or raw, name or "bilinmeyen")
            self.files += 1
        elif raw.endswith(" ERROR"):
            # `/yol: Access denied. ERROR` — yol ": " içerse bile ayraç
            # SONUNCUSUDUR; bu yüzden rpartition.
            path, _, reason = raw[: -len(" ERROR")].rpartition(": ")
            self._skip(path or raw, reason.strip(" .") or REASON_ERROR)
        elif raw.endswith((": OK", ": Empty file")):
            self.files += 1
        else:
            key, separator, value = raw.partition(":")
            folded = key.strip().lower()
            if separator and folded in SUMMARY_KEYS:
                self.summary[folded] = value.strip()
            elif len(self.notes) < MAX_NOTES:
                self.notes.append(raw)
        self._tick()

    def _threat(self, path: str, name: str) -> None:
        if len(self.threats) < MAX_RECORDS:
            self.threats.append({"path": path, "name": name})
        else:
            self._dropped_threats += 1

    def _skip(self, path: str, reason: str) -> None:
        # Tarayıcının bildirdiği her ERROR satırı ENGELLEYİCİDİR: o dosya ya da
        # dizin taranamadı, içinde ne olduğunu bilmiyoruz.
        if len(self.skipped) < MAX_RECORDS:
            self.skipped.append(skip_entry(path, reason or REASON_ERROR, blocking=True))
        else:
            self._dropped_skipped += 1

    def _tick(self) -> None:
        """İlerlemeyi seyrek bildir: her satırda çağırmak boşuna iş olurdu."""
        self._ticks += 1
        if self.on_progress is not None and self._ticks % 50 == 0:
            self.on_progress(self.files)

    # ----------------------------------------------------------------- rapor

    @property
    def scanned(self) -> int:
        """Taranan dosya sayısı — özet varsa ona güvenilir.

        clamdscan'in özeti bazı sürümlerde `Scanned files` satırını hiç
        yazmaz; o zaman kendi saydığımız `OK`/`FOUND` satırları kalır. İkisi
        de varsa büyüğü alınır: ikisinden hangisinin eksik kaldığı sürüme
        göre değişir, ama ikisi de fazla saymaz.
        """
        reported = 0
        try:
            reported = int(str(self.summary.get("scanned files", "0")).split()[0])
        except (ValueError, IndexError):
            reported = 0
        return max(self.files, reported)

    @property
    def dropped(self) -> tuple[int, int]:
        return self._dropped_threats, self._dropped_skipped

    def tail(self) -> str:
        return " · ".join(self.notes[-3:])

    def daemon_down(self) -> bool:
        """Çıktı "clamd'e ulaşamadım" diyor mu?"""
        blob = " ".join(self.notes).lower()
        return any(marker in blob for marker in DAEMON_DOWN)


# -------------------------------------------------------------------- motor


class ClamAvEngine:
    """ClamAV ikililerinin sarmalayıcısı.

    Aynı anda TEK tarama koşar; sırayı servis tutar. Süreç tutamağı burada
    saklanır ki `stop()` çalışan taramayı öldürebilsin.
    """

    def __init__(self, *, config: dict[str, Any], log: Any) -> None:
        self._log = log
        self._primary = str(config.get("clamdscan") or "clamdscan")
        self._fallback = str(config.get("clamscan") or "clamscan")
        self._database = Path(str(config.get("database_path") or "/var/lib/clamav")).expanduser()
        self._process: asyncio.subprocess.Process | None = None
        # Durdurma isteği süreçten ÖNCE gelebilir: kullanıcı "Tara"dan hemen
        # sonra "Durdur"a basarsa tarayıcı henüz açılmamış olur. Bayrak
        # olmasaydı istek düşer ve tarama sonuna kadar koşardı.
        self._running = False
        self._stop_requested = False

    # ------------------------------------------------------------- keşif

    def binaries(self) -> dict[str, str]:
        """Hangi ikili bulundu. Boş dize "yok" demektir."""
        return {
            "primary": shutil.which(self._primary) or "",
            "fallback": shutil.which(self._fallback) or "",
        }

    def database(self) -> dict[str, Any]:
        """İmza veritabanının durumu ve yaşı.

        YAŞ DOSYA ZAMANINDAN OKUNUR. İmza paketinin kendi başlığındaki tarih
        `sigtool --info` ile okunabilirdi ama bu, tarama yolunda olmayan bir
        ikiliye daha bağımlılık demek olurdu (K11: her bağımlılık ilan edilir).
        freshclam güncellediği dosyaya dokunduğu için mtime yeterince
        doğrudur; ölçtüğümüz şey "en son ne zaman güncellendi"dir.
        """
        path = str(self._database)
        try:
            present = self._database.is_dir()
        except OSError as failure:
            return {"path": path, "ready": False, "ageHours": None, "updatedAt": "",
                    "reason": f"imza dizini okunamadı: {failure}"}

        if not present:
            return {"path": path, "ready": False, "ageHours": None, "updatedAt": "",
                    "reason": "imza dizini henüz yok"}

        main = self._pick(MAIN_FILES)
        daily = self._pick(DAILY_FILES)
        if main is None or daily is None:
            missing = "ana imza" if main is None else "günlük imza"
            return {"path": path, "ready": False, "ageHours": None, "updatedAt": "",
                    "reason": f"{missing} dosyası indirilmemiş"}

        try:
            modified = daily.stat().st_mtime
        except OSError as failure:
            return {"path": path, "ready": True, "ageHours": None, "updatedAt": "",
                    "reason": f"imza yaşı okunamadı: {failure}"}

        age = max(0.0, (time.time() - modified) / 3600.0)
        stamp = datetime.fromtimestamp(modified, UTC).isoformat(timespec="seconds")
        return {"path": path, "ready": True, "ageHours": round(age, 2),
                "updatedAt": stamp, "reason": ""}

    def _pick(self, names: tuple[str, ...]) -> Path | None:
        for name in names:
            candidate = self._database / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    async def ping(self) -> bool:
        """clamd ayakta mı? Yalnız EKRAN için; tarama yolu buna bakmaz.

        Tarama kararı gerçek denemeye dayanır (aşağıda): `--ping` seçeneğini
        tanımayan eski bir clamdscan yüzünden hızlı yolu terk etmek yanlış
        olurdu. Burada yanlış "hayır", ekranda bir not; tarama yolunda ise
        saatlerce yavaş tarama demek olurdu.
        """
        binary = shutil.which(self._primary)
        if not binary:
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                binary, "--ping", "1",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            return False
        try:
            await asyncio.wait_for(process.wait(), timeout=PING_TIMEOUT)
        except TimeoutError:
            self._kill(process)
            await self._settle(process)
            return False
        return process.returncode == 0

    async def status(self) -> dict[str, Any]:
        """Ekranın "motor ne durumda" sorusuna tek yanıtı."""
        binaries = self.binaries()
        database = self.database()
        daemon = await self.ping() if binaries["primary"] else False
        installed = bool(binaries["primary"] or binaries["fallback"])

        if not installed:
            state, engine, note = STATE_MISSING, "", (
                "ClamAV kurulu değil. `scripts/install-deps.sh` çalıştırıldığında "
                "clamav, clamav-daemon ve clamav-freshclam paketleri kurulur."
            )
        elif not database["ready"]:
            # İLK KURULUM HATA DEĞİL. freshclam ~300 MB imza indirir ve bu
            # bitmeden clamd başlamaz (ADR 0009 sonuçları).
            state, engine, note = STATE_PREPARING, "", (
                "İmzalar hazırlanıyor: freshclam ilk kurulumda ~300 MB indirir ve "
                "bu bitmeden clamd başlamaz. Bu bir arıza değildir; indirme "
                f"bittiğinde tarama kendiliğinden açılır ({database['reason']})."
            )
        elif daemon:
            state, engine, note = STATE_READY, self._primary, ""
        elif binaries["fallback"]:
            state, engine, note = STATE_READY, self._fallback, (
                "clamd çalışmıyor; yedek yol kullanılacak. clamscan imzaları her "
                "taramada diskten yükler, tarama belirgin biçimde yavaştır."
            )
        else:
            state, engine, note = STATE_UNAVAILABLE, "", (
                "clamd çalışmıyor ve yedek clamscan kurulu değil. `clamav` paketi "
                "kurulduğunda yedek yol açılır."
            )

        return {
            "state": state,
            "installed": installed,
            "ready": state == STATE_READY,
            "engine": engine,
            "daemon": daemon,
            "primary": binaries["primary"],
            "fallback": binaries["fallback"],
            "note": note,
            "database": database,
        }

    # -------------------------------------------------------------- kapsam

    @staticmethod
    def expand(raw: Any) -> Path | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            return Path(text).expanduser()
        except (RuntimeError, ValueError):
            # `~bilinmeyen_kullanici` gibi çözülemeyen yol.
            return None

    def resolve(self, paths: list[str], exclude: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
        """Verilen yolları taranabilir hedeflere ve atlananlara ayırır.

        ÜÇ AYRI DURUM, ÜÇ AYRI ANLAM:
          · hariç tutuldu — yönetici öyle istedi, eksiklik değil,
          · yol yok — o yolda dosya da yok, kaçırılan bir şey yok,
          · erişilemedi — yol VAR ama okunamıyor. İçinde ne olduğu
            bilinmiyor; bu tarama "temiz" olamaz.
        """
        excluded = [item for item in (self.expand(entry) for entry in exclude) if item is not None]
        targets: list[str] = []
        skipped: list[dict[str, Any]] = []
        seen: set[str] = set()

        for raw in paths:
            path = self.expand(raw)
            if path is None:
                continue
            text = str(path)
            if text in seen:
                continue
            seen.add(text)

            if any(path == item or item in path.parents for item in excluded):
                skipped.append(skip_entry(text, REASON_EXCLUDED, blocking=False))
                continue
            try:
                exists = path.exists()
            except OSError:
                exists = False
            if not exists:
                skipped.append(skip_entry(text, REASON_MISSING, blocking=False))
                continue
            if not os.access(text, os.R_OK):
                skipped.append(skip_entry(text, REASON_UNREADABLE, blocking=True))
                continue
            targets.append(text)

        return targets, skipped

    # ------------------------------------------------------------- tarama

    async def scan(self, paths: list[str], *, timeout: float,
                   exclude: list[str] | None = None,
                   on_progress: Any = None) -> ScanOutcome:
        """Verilen yolları tarar. İstisna fırlatmaz; sonuç nesnesi döner."""
        started = time.monotonic()
        self._running = True
        self._stop_requested = False
        try:
            return await self._scan(paths, started, timeout=timeout,
                                    exclude=exclude or [], on_progress=on_progress)
        finally:
            self._running = False
            self._stop_requested = False
            self._process = None

    async def _scan(self, paths: list[str], started: float, *, timeout: float,
                    exclude: list[str], on_progress: Any) -> ScanOutcome:
        targets, skipped = self.resolve(paths, exclude)
        if not targets:
            return ScanOutcome(
                verdict=VERDICT_FAILED, skipped=skipped, seconds=time.monotonic() - started,
                error="Taranacak yol bulunamadı — ayardaki yolları denetleyin.",
            )

        attempts = self._commands(targets, exclude)
        if not attempts:
            return ScanOutcome(
                verdict=VERDICT_FAILED, skipped=skipped, seconds=time.monotonic() - started,
                error=("ClamAV kurulu değil (ne " + self._primary + " ne " + self._fallback
                       + " bulundu). `scripts/install-deps.sh` ile kurulur."),
            )

        outcome = ScanOutcome(verdict=VERDICT_FAILED, skipped=skipped)
        for index, (name, command) in enumerate(attempts):
            outcome, retry = await self._attempt(name, command, timeout=timeout,
                                                 on_progress=on_progress, base=skipped)
            # Kullanıcı durdurduysa yedek yol denenmez: "durdur" demek
            # "aynı işi baştan, yavaş yoldan yap" demek değildir.
            if self._stop_requested or not retry or index == len(attempts) - 1:
                return outcome
            self._log.warning("clamd'e ulaşılamadı, yedek yola geçiliyor",
                              engine=name, detail=outcome.error[:120])
        return outcome

    def _commands(self, targets: list[str], exclude: list[str]) -> list[tuple[str, list[str]]]:
        """Denenecek komutlar — sırayla.

        `--exclude-dir` YALNIZ clamscan'e verilir: clamdscan bu seçeneği
        tanımaz, hariç tutma clamd.conf'a aittir. Bu yüzden hariç tutma asıl
        olarak hedef listesi kurulurken (`resolve`) uygulanır; buradaki bayrak
        yedek yolda ağacın içine dalmayı da engelleyen İKİNCİ kapıdır.
        """
        attempts: list[tuple[str, list[str]]] = []

        primary = shutil.which(self._primary)
        if primary:
            attempts.append((self._primary, [primary, "--fdpass", *targets]))

        fallback = shutil.which(self._fallback)
        if fallback:
            command = [fallback, "--recursive", "--stdout"]
            for entry in exclude:
                path = self.expand(entry)
                if path is not None:
                    command.append(f"--exclude-dir=^{re.escape(str(path))}")
            command += targets
            attempts.append((self._fallback, command))

        return attempts

    async def _attempt(self, name: str, command: list[str], *, timeout: float,
                       on_progress: Any, base: list[dict[str, Any]]) -> tuple[ScanOutcome, bool]:
        """Tek bir tarayıcıyı çalıştırır.

        Dönüş: (sonuç, yedeğe_düşülsün_mü).
        """
        collector = Collector(on_progress=on_progress)
        started = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # KENDİ SÜREÇ GRUBU. Zaman aşımında yalnız tarayıcıyı öldürmek
                # yetmiyor: tarayıcının başlattığı bir alt süreç boruları açık
                # tutarsa `wait()` dönmez (asyncio süreç bitişini borular
                # kapanınca haber verir) ve "zaman aşımı" hiçbir şeyi
                # kısaltmamış olur. Grup ayrıldığında tüm ağaç birlikte
                # öldürülebilir.
                start_new_session=True,
            )
        except OSError as failure:
            outcome = ScanOutcome(engine=name, verdict=VERDICT_FAILED, skipped=list(base),
                                  seconds=time.monotonic() - started,
                                  error=f"{name} çalıştırılamadı: {failure}")
            return outcome, True

        self._process = process
        # Durdurma isteği süreç açılmadan ÖNCE gelmiş olabilir; öyleyse
        # tarayıcı daha ilk dosyaya bakmadan kapatılır.
        if self._stop_requested:
            self._kill(process)

        timed_out = False
        try:
            await asyncio.wait_for(self._pump(process, collector), timeout=timeout)
        except TimeoutError:
            timed_out = True
            self._kill(process)
        finally:
            await self._settle(process)
            self._process = None

        seconds = time.monotonic() - started
        returncode = process.returncode
        skipped = list(base) + collector.skipped
        error = ""

        if timed_out:
            limit = f"{timeout / 60:.0f} dk" if timeout >= 60 else f"{timeout:.0f} sn"
            error = (f"Zaman aşımı ({limit}) — tarama süreci durduruldu. "
                     "O ana dek bulunanlar aşağıdadır; tarama tamamlanmadı.")
        elif returncode is not None and returncode < 0:
            # Negatif kod = sinyalle öldürüldü. Tek kaynağı `stop()`tur.
            error = "Tarama durduruldu."
        elif returncode not in (0, 1):
            if collector.daemon_down():
                outcome = ScanOutcome(
                    engine=name, verdict=VERDICT_FAILED, skipped=skipped, seconds=seconds,
                    returncode=returncode,
                    error=f"{name}: clamd'e bağlanılamadı — {collector.tail()}",
                )
                return outcome, True
            if not (collector.scanned or collector.threats or collector.skipped):
                error = (f"{name} hata verdi (çıkış {returncode})."
                         + (f" {collector.tail()}" if collector.notes else ""))
            elif not any(entry.get("blocking") for entry in skipped):
                # Kısmi sonuç var, tarayıcı hata bildirdi ama NEYİN atlandığını
                # söylemedi. Sonuç zaten "temiz" olamaz (bkz. `decide_verdict`);
                # buradaki kayıt, ekranın nedenini yazabilmesi içindir.
                # Zaten engelleyici bir kayıt varsa ikincisi eklenmez: aynı
                # gerçeği iki satırla saymak sayacı şişirirdi.
                skipped.append(skip_entry(
                    f"({name})", f"tarayıcı hata bildirdi (çıkış {returncode})", blocking=True))

        dropped_threats, dropped_skipped = collector.dropped
        if dropped_threats:
            skipped.append(skip_entry("(rapor)",
                                      f"{dropped_threats} tehdit kaydı listeye sığmadı",
                                      blocking=True))
        if dropped_skipped:
            skipped.append(skip_entry("(rapor)",
                                      f"{dropped_skipped} atlanan yol listeye sığmadı",
                                      blocking=True))

        outcome = ScanOutcome(
            engine=name,
            verdict=decide_verdict(threats=collector.threats, skipped=skipped,
                                   error=error, returncode=returncode),
            files=collector.scanned,
            seconds=seconds,
            returncode=returncode,
            error=error,
            engine_version=collector.summary.get("engine version", ""),
            threats=collector.threats,
            skipped=skipped,
        )
        return outcome, False

    # -------------------------------------------------------------- süreç

    async def _pump(self, process: asyncio.subprocess.Process, collector: Collector) -> None:
        streams = [stream for stream in (process.stdout, process.stderr) if stream is not None]
        await asyncio.gather(*(self._read(stream, collector) for stream in streams))
        await process.wait()

    @staticmethod
    async def _read(stream: asyncio.StreamReader, collector: Collector) -> None:
        """Akışı parça parça okur ve satırlara böler.

        `readline()` KULLANILMAZ: akış sınırını aşan tek bir uzun satır
        `ValueError` fırlatır ve tampon dolu kaldığı için döngü sonsuza girer.
        Parça okumak sınırı bizim koymamızı sağlar.
        """
        buffer = b""
        while True:
            chunk = await stream.read(CHUNK)
            if not chunk:
                break
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                collector.line(line.decode("utf-8", errors="replace").rstrip("\r"))
            if len(buffer) > MAX_LINE:
                collector.line(buffer.decode("utf-8", errors="replace"))
                buffer = b""
        if buffer:
            collector.line(buffer.decode("utf-8", errors="replace").rstrip("\r"))

    @staticmethod
    def _kill(process: asyncio.subprocess.Process) -> bool:
        """Tarayıcıyı ve başlattığı her şeyi öldürür.

        Önce SÜREÇ GRUBU denenir: `clamscan` kabuk sarmalayıcısı ya da alt
        süreç kullanırsa yalnız başı öldürmek geride boruları açık tutan bir
        torun bırakır. Grup öldürülemiyorsa tek süreçle yetinilir.
        """
        if process.returncode is not None:
            return False
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            return False
        return True

    @staticmethod
    async def _settle(process: asyncio.subprocess.Process) -> None:
        """Sürecin kapanmasını bekler — ama sonsuza dek değil.

        `wait()` süreç öldüğünde DEĞİL, boruları da kapandığında döner. Bir
        torun boruyu tutmayı sürdürürse burası asılırdı; kısa mühletten sonra
        vazgeçilir ve sonuç elde olanla yazılır.
        """
        try:
            await asyncio.wait_for(process.wait(), timeout=KILL_GRACE)
        except TimeoutError:
            return

    def stop(self) -> bool:
        """Süren taramayı durdurur. Dönüş: durdurma isteği kabul edildi mi.

        Süreç HENÜZ AÇILMAMIŞ olabilir (kullanıcı "Tara"dan hemen sonra
        "Durdur"a bastı). O durumda bayrak bırakılır ve tarayıcı açılır
        açılmaz kapatılır; istek düşmez.
        """
        if not self._running:
            return False
        self._stop_requested = True
        process = self._process
        if process is not None:
            self._kill(process)
        return True
