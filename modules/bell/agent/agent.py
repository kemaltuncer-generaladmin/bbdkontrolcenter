"""BBD Zil Ajanı — okulun zil sistemine bağlı Windows bilgisayarında çalışır.

ARAYÜZÜ YOKTUR. Tek işi: köprüyü sorgulamak, sesleri yerelinde taze tutmak ve
söylendiği anda hoparlörden çalmak. Her karar Kontrol Merkezi'nde verilir.

─────────────────────────────────────────────────────────────────────────────
SESLER ÖNCEDEN İNER

Komut geldiğinde indirme YAPILMAZ; ses zaten diskte olur ve yalnız açılıp
çalınır. Her sorgulama yanıtı, komutların yanında "sende bulunması gereken
sesler" listesini taşır. Ajan arka planda eksikleri indirir, listede olmayanı
siler. Zil sesi değiştiğinde ya da bir grubun adı değişip anonsu yeniden
üretildiğinde özet değişir; ajan bir sonraki turda yenisini alır. `.exe`
yeniden kurulmaz, buraya elle dokunulmaz.

─────────────────────────────────────────────────────────────────────────────
SANİYE HASSASİYETİ

Sorgulama üç saniyede bir. Tek başına bu, zilin üç saniyeye kadar geç çalması
demek olurdu. Komut `playAt` damgası taşır ve köprüye zil saatinden bir dakika
ÖNCE yazılır: ajan komutu erkenden alır, kendi saatinde bekler, tam vaktinde
çalar.

Ajanın kendi PLANI YOKTUR. Elindeki komut bir dakikadan fazla yaşamaz; ağ
giderse o süre içindeki zil çalmaz. Bu bilinçli bir karardır.

─────────────────────────────────────────────────────────────────────────────
GEÇ KALAN ZİL ÇALINMAZ

Bilgisayar uykudan geç uyanırsa ya da ağ bir süre gitmişse, zamanı geçmiş
komut ATLANIR. Sabahki bütün zillerin öğlen arka arkaya çalması, hiç
çalmamaktan kötüdür.

─────────────────────────────────────────────────────────────────────────────
BAĞIMLILIK YOK

Yalnız standart kütüphane: `urllib` (istek), `winsound` (çalma), `wave`
(ses düzeyi). Kurulum ortamı olmayan bir makinede tek dosya `.exe` olarak
çalışması gerekiyor; her ek paket hem dosyayı büyütür hem kırılma yüzeyi ekler.
"""

from __future__ import annotations

import array
import contextlib
import hashlib
import json
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "0.2.0"

#: Özet biçimi — Kontrol Merkezi ve köprü ile aynı.
HASH = re.compile(r"^[0-9a-f]{16}$")

#: Zamanı bu kadar geçmiş komut atlanır.
LATE_TOLERANCE = timedelta(seconds=20)

#: `playAt` bu kadar ilerideyse damga güvenilmez sayılır ve hemen çalınır.
MAX_WAIT = timedelta(minutes=5)

#: Ağ hatasında beklenen süreler (saniye). Sonuncusu tekrarlanır.
BACKOFF = (2, 5, 10, 20, 30)

LOG_BYTES = 1_000_000
LOG_KEEP = 3

log = logging.getLogger("bbdzil")


# ============================================================== yapılandırma


def data_dir() -> Path:
    """Ajanın çalışma dizini.

    `%PROGRAMDATA%` seçildi, `%APPDATA%` değil: ajan makineye aittir, oturum
    açan kullanıcıya değil. Kullanıcı değişince zil susmamalı.
    """
    if platform.system() == "Windows":
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return Path(base) / "BBDZil"
    # Windows dışında yalnız geliştirme/deneme için.
    return Path(os.environ.get("BBDZIL_HOME") or Path.home() / ".bbdzil")


class Config:
    """`config.json` — kurulumda `install.ps1` yazar."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "config.json"
        raw: dict[str, object] = {}
        if self.path.is_file():
            try:
                # utf-8-sig: BOM varsa yutar, yoksa düz utf-8 gibi davranır.
                # CANLIDA YAŞANDI: Windows PowerShell 5.1'in
                # `Set-Content -Encoding UTF8` komutu dosyayı BOM İLE yazıyor
                # ve `json.loads` ilk karakterde patlıyordu. Ajan her açılışta
                # ölüyordu. Yazan tarafı da düzelttik ama okuyan taraf her
                # ihtimale karşı dayanıklı olmalı — bu dosyayı elle düzenleyen
                # bir editör de BOM ekleyebilir.
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except PermissionError:
                # CANLIDA YAŞANDI: kurulum betiği dosyayı fazla sıkı
                # izinlerle yazıyordu ve görev farklı bir kimlikle koşunca
                # ajan kendi ayarını okuyamıyordu. Hata mesajı çözümü de
                # söylesin; yığın izi kimseye bir şey anlatmıyor.
                raise SystemExit(
                    f"config.json OKUNAMIYOR (izin yok): {self.path}\n"
                    f"Yönetici PowerShell'de şunu çalıştırın:\n"
                    f'  icacls "{self.path}" /reset'
                ) from None
            except (OSError, json.JSONDecodeError) as failure:
                raise SystemExit(f"config.json okunamadı: {failure}") from None

        self.base_url = str(raw.get("baseUrl") or "https://bbdstore.com.tr").rstrip("/")
        self.token = str(raw.get("token") or "")
        self.poll_seconds = max(1, int(raw.get("pollSeconds") or 3))
        self.timeout = max(5, int(raw.get("timeoutSeconds") or 15))
        self.sounds = root / "sounds"

        if not self.token:
            raise SystemExit(
                f"Cihaz belirteci yok. {self.path} içine "
                '{"baseUrl": "...", "token": "..."} yazılmalı.'
            )


def setup_logging(root: Path) -> None:
    """Tek görünen iz: `agent.log`. Boyutu sınırlı, üç kopya saklanır."""
    root.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        root / "agent.log", maxBytes=LOG_BYTES, backupCount=LOG_KEEP, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    # Konsol varsa oraya da yaz (elle çalıştırıp izlemek için). `.exe`
    # penceresiz derlendiğinde `sys.stdout` None olur; o zaman atlanır.
    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(console)


# ==================================================================== köprü


class Bridge:
    """Köprü istemcisi. Yalnız `urllib` kullanır."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def _request(self, method: str, path: str, *, query: list[tuple[str, str]] | None = None,
                 body: dict[str, object] | None = None) -> object:
        url = f"{self._config.base_url}/api/bell{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._config.token}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", f"BBDZil/{VERSION}")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(request, timeout=self._config.timeout) as response:
            payload = response.read()
        return json.loads(payload.decode("utf-8")) if payload else {}

    def poll(self, since: int, have: list[str]) -> dict[str, object]:
        query: list[tuple[str, str]] = [("since", str(since))]
        query += [("have[]", item) for item in have]
        result = self._request("GET", "/poll", query=query)
        return result if isinstance(result, dict) else {}

    def ack(self, command_id: int, ok: bool, detail: str, have: list[str]) -> None:
        self._request("POST", "/ack", body={
            "id": command_id, "ok": ok, "detail": detail[:400], "have": have,
        })

    def download(self, sound_id: str) -> bytes:
        url = f"{self._config.base_url}/api/bell/sound/{sound_id}"
        request = urllib.request.Request(url)
        request.add_header("Authorization", f"Bearer {self._config.token}")
        request.add_header("User-Agent", f"BBDZil/{VERSION}")
        with urllib.request.urlopen(request, timeout=max(30, self._config.timeout)) as response:
            return bytes(response.read())


# ============================================================== ses kitaplığı


class Library:
    """Yereldeki ses dosyaları. İçerik adresli, kendini onarır."""

    def __init__(self, root: Path, bridge: Bridge) -> None:
        self.root = root
        self._bridge = bridge
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def have(self) -> list[str]:
        found = []
        for path in self.root.glob("*.wav"):
            if HASH.match(path.stem):
                found.append(path.stem)
        return sorted(found)

    def path(self, sound_id: str) -> Path | None:
        if not HASH.match(sound_id):
            return None
        candidate = self.root / f"{sound_id}.wav"
        return candidate if candidate.is_file() else None

    def sync(self, wanted: list[str]) -> None:
        """Eksikleri indirir, listede olmayanı siler.

        SİLME DE GEREKLİ: grup adı değişince eski anons artık istenmez. Yalnız
        indirseydik disk yıllar içinde ölü seslerle dolardı ve hangisinin canlı
        olduğu belirsizleşirdi.
        """
        wanted_set = {item for item in wanted if HASH.match(item)}
        if not wanted_set:
            return                      # köprü liste vermediyse silme yapma

        with self._lock:
            present = set(self.have())

            for sound_id in sorted(wanted_set - present):
                self._fetch(sound_id)

            for stale in sorted(present - wanted_set):
                with contextlib.suppress(OSError):
                    (self.root / f"{stale}.wav").unlink()
                    log.info("eski ses silindi: %s", stale)

    def ensure(self, sound_id: str) -> Path | None:
        """Çalmadan hemen önceki son kapı.

        Normalde buraya hiç girilmez: ses zaten inmiştir. İlk kurulumda ya da
        senkron henüz bitmemişken komut gelirse, o an indirilir ve `ack`
        içinde bildirilir — sessizce geç kalmaz.
        """
        found = self.path(sound_id)
        if found is not None:
            return found
        with self._lock:
            return self._fetch(sound_id)

    def _fetch(self, sound_id: str) -> Path | None:
        try:
            data = self._bridge.download(sound_id)
        except (urllib.error.URLError, OSError, TimeoutError) as failure:
            log.warning("ses inmedi %s: %s", sound_id, failure)
            return None

        # İÇERİK DOĞRULANIR. Yarım inen ya da bozulmuş dosya diske yazılmaz;
        # yazılsaydı "ses var" görünüp çalma anında patlardı.
        actual = hashlib.sha256(data).hexdigest()[:16]
        if actual != sound_id:
            log.error("ses özeti tutmadı: beklenen %s, gelen %s", sound_id, actual)
            return None

        target = self.root / f"{sound_id}.wav"
        staging = target.with_suffix(".part")
        try:
            staging.write_bytes(data)
            staging.replace(target)
        except OSError as failure:
            log.error("ses yazılamadı %s: %s", sound_id, failure)
            with contextlib.suppress(OSError):
                staging.unlink()
            return None

        log.info("ses indi: %s (%d bayt)", sound_id, len(data))
        return target


# ==================================================================== çalma


class Player:
    """Hoparlörden çalma.

    `winsound.PlaySound` BLOKLAR — komutdaki sesler sırayla, üst üste binmeden
    çalar. Zil bitmeden anonsun başlaması, ikisinin de anlaşılmaması demekti.
    """

    def __init__(self, cache: Path) -> None:
        self._cache = cache
        self._cache.mkdir(parents=True, exist_ok=True)
        self._winsound = None
        if platform.system() == "Windows":
            import winsound

            self._winsound = winsound

    def play(self, path: Path, volume: int) -> None:
        target = self._at_volume(path, volume)
        if self._winsound is None:
            log.info("(Windows değil) çalınacaktı: %s", target.name)
            time.sleep(0.05)
            return
        self._winsound.PlaySound(str(target), self._winsound.SND_FILENAME)

    def _at_volume(self, path: Path, volume: int) -> Path:
        """Sesi istenen düzeye ölçekler ve önbelleğe alır.

        `winsound` ses düzeyi bilmez ve Windows'un ana ses ayarını değiştirmek
        makinenin geri kalanını da etkilerdi. Bu yüzden örnekler dosyada
        ölçeklenir. Sonuç önbelleklenir: aynı ses her çalışta yeniden
        hesaplanmaz.
        """
        level = max(0, min(100, int(volume)))
        if level >= 100:
            return path

        cached = self._cache / f"{path.stem}-{level}.wav"
        if cached.is_file():
            return cached

        try:
            with wave.open(str(path), "rb") as source:
                if source.getsampwidth() != 2:
                    return path        # yalnız 16 bit ölçeklenir
                params = source.getparams()
                frames = source.readframes(source.getnframes())

            samples = array.array("h")
            samples.frombytes(frames)
            # Kulak logaritmik duyar: %50 doğrusal kazanç "yarı yüksek"
            # duyulmaz. 1.6 üssü, Kontrol Merkezi'nin önizlemesiyle aynı eğri.
            gain = (level / 100) ** 1.6
            for index, value in enumerate(samples):
                samples[index] = int(max(-32768, min(32767, value * gain)))

            staging = cached.with_suffix(".part")
            with wave.open(str(staging), "wb") as target:
                target.setparams(params)
                target.writeframes(samples.tobytes())
            staging.replace(cached)
            return cached
        except (wave.Error, OSError, ValueError) as failure:
            # Ölçekleme başarısızsa TAM SESLE çalmak, hiç çalmamaktan iyidir.
            log.warning("ses düzeyi uygulanamadı (%s): %s", path.name, failure)
            return path


# ==================================================================== döngü


def parse_at(raw: str) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.astimezone()


def wait_for(target: datetime | None) -> str:
    """`playAt` anına kadar bekler. Dönüş: atlanacaksa nedeni, yoksa boş."""
    if target is None:
        return ""

    now = datetime.now(timezone.utc).astimezone()
    delta = target - now

    if delta < -LATE_TOLERANCE:
        # Geç kalan zil çalınmaz: uykudan geç uyanan makine sabahki bütün
        # zilleri arka arkaya çalmamalı.
        return f"zamanı {abs(delta).seconds} sn geçmiş, atlandı"
    if delta > MAX_WAIT:
        log.warning("playAt çok ileride (%s), hemen çalınıyor", target.isoformat())
        return ""
    if delta.total_seconds() > 0:
        time.sleep(delta.total_seconds())
    return ""


class Agent:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._bridge = Bridge(config)
        self._library = Library(config.sounds, self._bridge)
        self._player = Player(config.root / "cache")
        self._since = 0
        self._syncing = threading.Lock()

    def run(self) -> None:
        log.info("zil ajanı başladı · sürüm %s · köprü %s",
                 VERSION, self._config.base_url)
        failures = 0

        while True:
            try:
                self._tick()
                failures = 0
                time.sleep(self._config.poll_seconds)
            except KeyboardInterrupt:
                log.info("zil ajanı durduruldu")
                return
            except (urllib.error.URLError, OSError, TimeoutError) as failure:
                wait = BACKOFF[min(failures, len(BACKOFF) - 1)]
                failures += 1
                # Ağ hatası GÜRÜLTÜ YAPMASIN: okulda internet dalgalanınca
                # log dosyası dakikada yirmi satırla dolmamalı.
                if failures in (1, 5, 20) or failures % 100 == 0:
                    log.warning("köprüye ulaşılamıyor (%d. deneme): %s", failures, failure)
                time.sleep(wait)
            except Exception:
                log.exception("tur patladı")
                time.sleep(5)

    def _tick(self) -> None:
        have = self._library.have()
        payload = self._bridge.poll(self._since, have)

        wanted = [str(item) for item in (payload.get("sounds") or [])]
        if set(wanted) != set(have):
            # Senkron ayrı iş parçacığında: indirme, bekleyen bir zili
            # geciktirmemeli.
            self._sync_async(wanted)

        for command in payload.get("commands") or []:
            self._handle(command)

    def _sync_async(self, wanted: list[str]) -> None:
        if not self._syncing.acquire(blocking=False):
            return                      # önceki senkron sürüyor
        def work() -> None:
            try:
                self._library.sync(wanted)
            except Exception:
                log.exception("ses senkronu patladı")
            finally:
                self._syncing.release()

        threading.Thread(target=work, name="bbdzil-sync", daemon=True).start()

    def _handle(self, command: object) -> None:
        if not isinstance(command, dict):
            return
        command_id = int(command.get("id") or 0)
        if command_id <= self._since:
            return
        self._since = command_id

        items = [item for item in (command.get("items") or []) if isinstance(item, dict)]
        skipped = wait_for(parse_at(str(command.get("playAt") or "")))
        if skipped:
            log.warning("komut #%d atlandı: %s", command_id, skipped)
            self._report(command_id, False, skipped)
            return

        notes: list[str] = []
        ok = True
        for item in items:
            sound_id = str(item.get("hash") or "")
            path = self._library.path(sound_id)
            if path is None:
                # Buraya normalde girilmez; girildiyse ekran bunu görmeli.
                path = self._library.ensure(sound_id)
                notes.append("late_download")
            if path is None:
                ok = False
                notes.append(f"ses yok: {sound_id}")
                continue
            try:
                self._player.play(path, int(item.get("volume") or 100))
            except Exception as failure:  # noqa: BLE001 — ses aygıtı dışarısı
                ok = False
                notes.append(f"çalınamadı: {failure}")

        detail = " · ".join(notes) if notes else f"{len(items)} ses çaldı"
        log.info("komut #%d: %s", command_id, detail)
        self._report(command_id, ok, detail)

    def _report(self, command_id: int, ok: bool, detail: str) -> None:
        try:
            self._bridge.ack(command_id, ok, detail, self._library.have())
        except (urllib.error.URLError, OSError, TimeoutError) as failure:
            # Bildirim gitmese de zil çaldı; bir sonraki sorgulama durumu tazeler.
            log.warning("ack gönderilemedi: %s", failure)


def main() -> int:
    root = data_dir()
    setup_logging(root)
    try:
        config = Config(root)
    except SystemExit as failure:
        log.error("%s", failure)
        return 2

    Agent(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
