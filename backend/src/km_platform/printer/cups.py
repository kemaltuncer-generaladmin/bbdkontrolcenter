"""Yazıcı yeteneği — CUPS üzerinden belge basma.

PLATFORM YETENEĞİDİR, MODÜL DEĞİL (ADR 0006). Rapor modülü de, ileride gelecek
başka ekranlar ve otonom (zamanlanmış) baskı da bunu tüketir; kimse `lp`
komutunu kendi çağırmaz (K4).

------------------------------------------------------------------------
PAHALIYA MAL OLAN DERS: "TAMAMLANDI" KÂĞIT ÇIKTIĞI ANLAMINA GELMEZ
------------------------------------------------------------------------

Bu makinede aynı fiziksel yazıcının İKİ kuyruğu vardı:

    HP_LaserJet_MFP_M139-M142         → hp:/usb/…      (HPLIP, klasik)
    HP_LaserJet_MFP_M141w_BE4507_USB  → implicitclass://…  (sürücüsüz)

`ipp-usb` servisi çalışırken USB aygıtını TEKELİNE ALIR. O sırada klasik
`hp:/usb/` kuyruğu yazıcıya ulaşamaz — ama CUPS işi yine kabul eder,
`job-state=completed` yazar, hatta `job-media-sheets-completed=1` der.
Yani yazılımın gördüğü her şey "başarılı"dır ve kâğıt çıkmaz. Bu sessiz
kayıp saatlerce kovalandı.

Bu yüzden hedef seçimi şuna dayanır:

  1. `ipp-usb` 127.0.0.1:60000'de yanıt veriyorsa fiziksel USB yazıcı ORADADIR.
     Aygıtın durumu, modeli ve toner seviyesi doğrudan ondan okunur.
  2. O durumda klasik `usb://` / `hp:/usb/` kuyrukları TUZAKTIR; seçilmez.
  3. Sürücüsüz kuyruklar arasından USB olanı yeğlenir (ipp-usb yayınladığı
     mDNS adına "(USB)" yazar); gerçek ağ yazıcıları böylece elenir.

`ipp-usb` yoksa klasik USB kuyruğu doğru seçimdir ve o kullanılır.

Kablo çıkmışsa ya da yazıcı devre dışıysa SESSİZ KALINMAZ: `PrinterError`
fırlar ve ekran "yazıcı bağlantısı hatası" der. Basılmadığı fark edilmeyen bir
rapor, hiç basılmamış rapordan daha kötüdür.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
from pathlib import Path
from typing import Any

#: Baskı işi bu kadar sürede kuyruğa verilemezse süreç öldürülür.
DEFAULT_TIMEOUT = 120.0

#: AYNI BELGE BU SÜRE İÇİNDE İKİNCİ KEZ GÖNDERİLMEZ (saniye).
#:
#: "Yazdır"a bir kez basıldığında bir kez basılmalı. Sahada bunun bozulduğu
#: görüldü: aynı rapor arka arkaya çıkıyordu. Tetikleyici tek bir şey değil —
#: üst üste binen önizleme katmanları, çift tıklama, ekranın yeniden çizilmesi
#: ve ağ yavaşken sabırsızlanan kullanıcı aynı sonucu veriyor. Hepsinin ortak
#: noktası TEK KAPIDAN geçmeleri (K4), o yüzden koruma da burada durur:
#: yirmi iki modülün her biri ayrı ayrı korunmak zorunda kalmaz.
#:
#: SÜRE KISA TUTULDU. Amaç kasıtlı ikinci baskıyı engellemek DEĞİL — o meşru
#: bir istek ve `copies` ile ya da birkaç saniye sonra tekrar basarak yapılır.
#: Amaç, kullanıcının TEK eyleminin birden çok işe dönüşmesini engellemek.
DUPLICATE_WINDOW = 12.0

#: Klasik USB kuyruğu: çekirdek sürücüsü doğrudan USB aygıtına yazar.
_USB_URI = re.compile(r"^(usb://|hp:/usb/)", re.IGNORECASE)

#: Sürücüsüz / ağ üzerinden görünen kuyruklar.
_NETWORK_URI = re.compile(r"^(implicitclass://|ipp://|ipps://|dnssd://|socket://|lpd://)",
                          re.IGNORECASE)

#: `ipp-usb` yayınladığı mDNS hizmet adına bu damgayı koyar:
#: "HP LaserJet MFP M141w (BE4507) (USB)._ipp._tcp.local". Sürücüsüz bir
#: kuyruğun aslında USB'de olduğunu buradan anlarız.
_USB_MARK = re.compile(r"\(usb\)|%28usb%29|_usb(?:[^a-z]|$)", re.IGNORECASE)

#: `ipp-usb` aygıt başına bir port açar; ilki 60000.
IPPUSB_PORTS = (60000, 60001, 60002, 60003)

#: Aygıtı sorgularken beklenecek en fazla süre.
PROBE_TIMEOUT = 6.0


class PrinterError(RuntimeError):
    """Yazıcıya ulaşılamadı ya da baskı kuyruğa verilemedi."""


def _identity(text: str) -> str:
    """Model adını karşılaştırılabilir tek parçaya indirger.

    "HP LaserJet MFP M139-M142" ile kuyruk adı "HP_LaserJet_MFP_M139-M142" ve
    adres "hp:/usb/HP_LaserJet_MFP_M139-M142?serial=…" aynı cihazı gösterir;
    ayraçlar ve büyük/küçük harf farkı temizlenince üçü de eşitlenir.
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _matches(identity: str, name: str, uri: str) -> bool:
    """Kuyruk, kimliği verilen cihaza mı ait."""
    if len(identity) < 6:  # anlamsız derecede kısa kimlikle eşleştirme yapılmaz
        return False
    return identity in _identity(name) or identity in _identity(uri)


def _parse_states(text: str) -> dict[str, str]:
    """`lpstat -p` çıktısını okur.

    CUPS aynı bilgiyi ÜÇ FARKLI CÜMLE KALIBIYLA yazar ve ilk yazdığımız desen
    yalnız birincisini tanıyordu:

        printer X is idle.  enabled since …
        printer X now printing X-17.  enabled since …      ← "is" YOK
        printer X disabled since … -                        ← "is" YOK

    Yazıcı baskı yaparken satır ikinci kalıba düşüyor, eşleşme olmuyor ve
    durum "bilinmiyor" sanılıp yazıcı hazır değil sayılıyordu. Kullanıcı
    normal bir belge bastıktan hemen sonra uygulamadan yazdırınca tam olarak
    bu oluyordu: "yazıcı bağlantısı hatası", oysa yazıcı gayet çalışıyor.
    """
    states: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("printer "):
            continue  # "cfFilterGhostscript: …" gibi alt satırlar
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        name, rest = parts[1], parts[2]
        low = rest.lower()
        if low.startswith("disabled"):
            states[name] = "devre dışı"
        elif low.startswith("now printing"):
            states[name] = "baskı yapıyor"
        elif low.startswith("is idle"):
            states[name] = "boşta"
        elif low.startswith("is "):
            states[name] = rest[3:].split(".")[0].strip().lower()
        else:
            states[name] = "bilinmiyor"
    return states


def _parse_accepting(text: str) -> dict[str, bool]:
    """`lpstat -a` çıktısı: kuyruk yeni iş alıyor mu.

        X accepting requests since …
        X not accepting requests since …
    """
    result: dict[str, bool] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if " accepting requests" not in line:
            continue
        name = line.split(None, 1)[0]
        result[name] = " not accepting requests" not in line
    return result


class PrinterService:
    """`printer` yeteneğinin CUPS uygulaması."""

    def __init__(self, config: Any, log: Any) -> None:
        section = config.section("platform.printer") or {}
        self._log = log
        self._timeout = float(section.get("job_timeout_seconds") or DEFAULT_TIMEOUT)
        # Yapılandırmadaki ad yalnız EŞİTLİK BOZUCUDUR: USB'de birden çok
        # LaserJet varsa hangisinin yeğleneceğini söyler. Tek başına hedef
        # belirlemez — kablo değişirse ad tutar ama cihaz yanlış olur.
        self._preferred = str(section.get("default_printer") or "").strip()
        # Önce kırp, SONRA varsayılana düş: "   " değeri `or`'u geçip boş
        # dizgeye dönüşürse eşleştirme ve kâğıt boyutu sessizce devre dışı kalır.
        self._match = str(section.get("usb_match") or "").strip().lower() or "laserjet"
        # Ürettiğimiz raporlar A4. Kuyruğun varsayılanına güvenilmez; bu
        # makinede varsayılan A6 çıktı ve yazıcı kâğıt uyuşmazlığı verdi.
        self._media = str(section.get("media") or "").strip() or "A4"
        # Aynı belgenin arka arkaya gönderilmesini engelleyen pencere.
        # 0 yazılırsa koruma kapanır; ayar bilerek bırakıldı ama varsayılanı
        # kapatmak için bir sebep yok.
        window = section.get("duplicate_window_seconds")
        self._duplicate_window = (
            DUPLICATE_WINDOW if window is None else max(0.0, float(window))
        )
        #: Son gönderilen işler: (kuyruk, dosya, boyut, değişim damgası) →
        #: (gönderim anı, sonuç). Süreç belleğinde durur ve durması yeterli:
        #: koruma "kullanıcının tek eylemi" ölçeğinde, saatler ölçeğinde değil.
        self._recent: dict[tuple[str, str, int, int], tuple[float, dict[str, Any]]] = {}

    # ------------------------------------------------------------- keşif

    def _tools(self) -> tuple[str, str]:
        """`lp` ve `lpstat` yolları. Yoksa CUPS kurulu değildir."""
        lp = shutil.which("lp")
        lpstat = shutil.which("lpstat")
        if not lp or not lpstat:
            raise PrinterError(
                "Yazıcı bağlantısı hatası: sistemde CUPS (lp/lpstat) bulunamadı. "
                "Baskı için CUPS kurulu olmalı."
            )
        return lp, lpstat

    async def _run(self, *args: str) -> tuple[int, str, str]:
        """Komutu ayrı süreçte, zaman aşımıyla çalıştırır (K7)."""
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as failure:
            raise PrinterError(f"Yazıcı bağlantısı hatası: {failure}") from failure

        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise PrinterError(
                "Yazıcı bağlantısı hatası: yazıcı yanıt vermedi "
                f"({int(self._timeout)} saniye). Kabloyu ve cihazın açık olduğunu denetleyin."
            ) from None

        return process.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def discover(self) -> list[dict[str, Any]]:
        """Tanımlı yazıcılar: adı, aygıt URI'si, bağlantı türü, durumu."""
        _, lpstat = self._tools()

        code, uris, err = await self._run(lpstat, "-v")
        if code != 0 and not uris.strip():
            raise PrinterError(
                f"Yazıcı bağlantısı hatası: yazıcı listesi okunamadı ({err.strip() or code})."
            )

        devices: dict[str, str] = {}
        for line in uris.splitlines():
            # "device for HP_LaserJet_MFP_M139-M142: hp:/usb/HP_...?serial=..."
            match = re.match(r"device for ([^:]+):\s*(.+)$", line.strip())
            if match:
                devices[match.group(1).strip()] = match.group(2).strip()

        # Durum ayrı sorgulanır: tanımlı olmak baskı alabilmek değildir.
        _, states, _ = await self._run(lpstat, "-p")
        state_of = _parse_states(states)

        # KABUL AYRI, ÇALIŞMA AYRI. `lp`nin işi alıp almayacağını belirleyen
        # şey kuyruğun "accepting requests" olmasıdır; yazıcının o an baskı
        # yapıyor olması işi engellemez, sıraya girer.
        _, accepts, _ = await self._run(lpstat, "-a")
        accepting = _parse_accepting(accepts)

        # Fiziksel USB yazıcı ipp-usb'de mi ve HANGİ cihaz? Kuyruk seçimini
        # bu belirler. Hangi cihaz olduğu önemli: ipp-usb yalnız o aygıtı
        # sahiplenir, makinedeki diğer USB yazıcılar (fiş yazıcısı gibi)
        # klasik kuyruklarıyla çalışmaya devam eder.
        device = await self.device_info()
        ippusb = device.get("port")
        owned = _identity(str(device.get("printer-make-and-model") or ""))

        printers = []
        for name, uri in devices.items():
            classic = bool(_USB_URI.match(uri))
            network = bool(_NETWORK_URI.match(uri))
            # Sürücüsüz bir kuyruk da FİZİKSEL OLARAK USB olabilir: ipp-usb
            # yayınladığı adına "(USB)" yazar. Adrese "usb" yazmıyor diye
            # elemek, tam da bizi yanlış kuyruğa götüren hataydı.
            driverless_usb = network and bool(_USB_MARK.search(uri))

            state = state_of.get(name, "bilinmiyor")
            takes = accepting.get(name, True)

            # ipp-usb aygıtı tekeline aldıysa O AYGITIN klasik kuyruğu yazıcıya
            # ULAŞAMAZ ama işi kabul edip "tamamlandı" der. Damga yalnız
            # eşleşen cihaza vurulur; cihaz kimliği okunamadıysa (ipptool yok)
            # kimse suçlanmaz — koruma yine de sürücüsüz kuyruğu yeğlemekten
            # gelir.
            trapped = bool(
                classic and ippusb is not None and owned
                and _matches(owned, name, uri)
            )

            if driverless_usb:
                connection = "usb (sürücüsüz)"
            elif classic:
                connection = "usb"
            elif network:
                connection = "ağ"
            else:
                connection = "diğer"

            printers.append({
                "name": name,
                "uri": uri,
                "connection": connection,
                "usb": classic or driverless_usb,
                "driverless": driverless_usb,
                "trapped": trapped,
                "state": state,
                "accepting": takes,
                # Devre dışı kuyruk işi alır ama BASMAZ; iş orada bekler ve
                # kimse fark etmez. Meşgul olmak ise sorun değil.
                "ready": takes and state != "devre dışı" and not trapped,
            })
        return printers

    # ------------------------------------------------------ aygıtın kendisi

    async def device_port(self) -> int | None:
        """`ipp-usb` hangi portta yanıt veriyor; hiçbiri yoksa `None`.

        Bu sorunun cevabı yalnız "sürücüsüz baskı var mı"yı değil, klasik USB
        kuyruklarının ÇALIŞIP ÇALIŞMADIĞINI da söyler: ipp-usb ayaktaysa o
        kuyruklar sessizce boşa gider.
        """
        for port in IPPUSB_PORTS:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=1.5,
                )
            except (TimeoutError, OSError):
                continue
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:  # pragma: no cover — kapanışta kopan bağlantı
                pass
            return port
        return None

    async def device_info(self) -> dict[str, Any]:
        """Yazıcının KENDİ bildirdiği durum: model, hâl, toner.

        CUPS kuyruğunun ne dediğinden bağımsızdır — kuyruk "boşta" derken
        cihazda kâğıt bitmiş olabilir. Otonom baskı için asıl bakılacak yer
        burasıdır. `ipptool` yoksa ya da aygıt yanıt vermezse boş döner.
        """
        port = await self.device_port()
        if port is None:
            return {}

        binary = shutil.which("ipptool")
        if not binary:
            return {"port": port}

        code, out, _ = await self._run(
            binary, "-tv", f"ipp://localhost:{port}/ipp/print",
            "get-printer-attributes.test",
        )
        if code != 0:
            return {"port": port}

        info: dict[str, Any] = {"port": port}
        for line in out.splitlines():
            match = re.match(
                r"\s*(printer-make-and-model|printer-info|printer-state|"
                r"printer-state-reasons|marker-levels|printer-uuid)\s*"
                r"\([^)]*\)\s*=\s*(.+)$",
                line,
            )
            if match:
                info[match.group(1)] = match.group(2).strip()

        levels = str(info.get("marker-levels") or "")
        if levels:
            try:
                # Birden çok kartuş olabilir; en düşüğü uyarılacak olandır.
                info["tonerPercent"] = min(
                    int(part) for part in re.findall(r"-?\d+", levels) if int(part) >= 0
                )
            except ValueError:
                pass
        return info

    async def target(self) -> dict[str, Any]:
        """Basılacak kuyruğu seçer. Seçemezse NEDENİNİ söyler."""
        printers = await self.discover()
        if not printers:
            raise PrinterError("Yazıcı bağlantısı hatası: sistemde tanımlı yazıcı yok.")

        # Ayarda kuyruk adı verilmişse o BAĞLAYICIDIR: yönetici bilerek
        # seçmiştir, tahmin yürütmeyiz. Ama tuzak kuyruksa yine uyarırız.
        if self._preferred:
            pinned = next((p for p in printers if p["name"] == self._preferred), None)
            if pinned is not None:
                self._assert_usable(pinned)
                return pinned
            self._log.warning("ayardaki yazıcı bulunamadı, otomatik seçime dönüldü",
                              aranan=self._preferred)

        ippusb = await self.device_port()
        usable = [p for p in printers if p["usb"] and not p["trapped"]]

        if not usable:
            trapped = [p for p in printers if p["trapped"]]
            if trapped:
                # En sinsi durum: kuyruk var, "hazır" görünüyor, iş kabul
                # ediyor, "tamamlandı" diyor — ve kâğıt çıkmıyor.
                adlar = ", ".join(p["name"] for p in trapped)
                raise PrinterError(
                    "Yazıcı bağlantısı hatası: USB yazıcı `ipp-usb` servisi "
                    f"tarafından kullanılıyor, bu yüzden “{adlar}” kuyruğu cihaza "
                    "ulaşamıyor (iş “tamamlandı” görünse bile kâğıt çıkmaz). "
                    "Sürücüsüz kuyruk bulunamadı; yazıcıyı sistem ayarlarından "
                    "yeniden ekleyin ya da `ipp-usb` servisini durdurun."
                )
            adlar = ", ".join(f"{p['name']} ({p['connection']})" for p in printers)
            raise PrinterError(
                "Yazıcı bağlantısı hatası: USB'ye bağlı yazıcı bulunamadı. "
                f"Görünen kuyruklar: {adlar}. Kabloyu ve cihazın açık olduğunu denetleyin."
            )

        # Sürücüsüz kuyruk YALNIZ ipp-usb ayaktayken yeğlenir: cihaza ulaşan
        # tek yol odur. ipp-usb kapalıysa o kuyruğun dayandığı mDNS hizmeti de
        # yok demektir; o zaman doğru seçim klasik USB kuyruğudur.
        pool = usable
        if ippusb is not None:
            driverless = [p for p in usable if p["driverless"]]
            pool = driverless or usable

        matched = [p for p in pool if self._match in p["name"].lower()]
        if matched:
            pool = matched
        else:
            self._log.warning("adı eşleşen yazıcı yok, uygun ilk kuyruk kullanılıyor",
                              aranan=self._match, bulunan=[p["name"] for p in pool])

        chosen = pool[0]
        self._assert_usable(chosen)
        return chosen

    @staticmethod
    def _assert_usable(printer: dict[str, Any]) -> None:
        """Seçilen kuyruk gerçekten basabilir mi."""
        if printer.get("trapped"):
            raise PrinterError(
                f"Yazıcı bağlantısı hatası: “{printer['name']}” kuyruğu cihaza ulaşamıyor. "
                "USB yazıcıyı `ipp-usb` servisi kullanıyor; bu kuyruğa gönderilen iş "
                "“tamamlandı” görünür ama kâğıt çıkmaz. Sürücüsüz kuyruğu seçin."
            )
        if not printer.get("accepting", True):
            raise PrinterError(
                f"Yazıcı bağlantısı hatası: “{printer['name']}” yeni iş kabul etmiyor. "
                "Kuyruk duraklatılmış olabilir."
            )
        if printer.get("state") == "devre dışı":
            raise PrinterError(
                f"Yazıcı bağlantısı hatası: “{printer['name']}” devre dışı. "
                "Yazıcıyı açıp kuyruğu serbest bırakın."
            )

    # ------------------------------------------------------------- baskı

    def _fingerprint(self, path: Path, queue: str) -> tuple[str, str, int, int] | None:
        """Belgeyi TANIMLAYAN damga: kuyruk + yol + boyut + değişim anı.

        Yol tek başına yetmez — aynı ada yeniden üretilen bir rapor BAŞKA
        belgedir ve basılabilmelidir. Boyut ve değişim damgası bunu ayırır:
        rapor yeniden üretilmişse damga değişir, koruma devreye girmez.
        """
        try:
            info = path.stat()
        except OSError:
            return None
        return (queue, str(path), int(info.st_size), int(info.st_mtime_ns))

    def _recent_job(self, key: tuple[str, str, int, int]) -> dict[str, Any] | None:
        """Bu belge az önce gönderildi mi. Süresi geçmiş kayıtlar atılır."""
        if self._duplicate_window <= 0:
            return None
        now = time.monotonic()
        for stale, (moment, _) in list(self._recent.items()):
            if now - moment > self._duplicate_window:
                del self._recent[stale]
        found = self._recent.get(key)
        if found is None:
            return None
        moment, result = found
        if now - moment > self._duplicate_window:
            del self._recent[key]
            return None
        return result

    async def print_file(self, path: Path, *, title: str = "", copies: int = 1) -> dict[str, Any]:
        """Dosyayı USB'deki LaserJet'e yollar. Onay sorulmaz, doğrudan basılır.

        BİR EYLEM BİR BASKI. Aynı belge birkaç saniye içinde ikinci kez
        istenirse İŞ TEKRAR GÖNDERİLMEZ; ilk işin sonucu `duplicate: True` ile
        geri verilir. Sahada aynı raporun arka arkaya çıktığı görüldü ve
        tetikleyici tek bir yer değildi — koruma bu yüzden çağıranlarda değil,
        hepsinin geçtiği bu kapıda duruyor (K4).

        Kopya isteyen `copies` kullanır: o TEK iştir, kuyruğa bir kez girer.
        """
        if not path.is_file():
            raise PrinterError(f"Basılacak dosya bulunamadı: {path}")

        lp, _ = self._tools()
        printer = await self.target()

        # Damga hedef SEÇİLDİKTEN sonra hesaplanır: aynı belgeyi bilerek başka
        # bir kuyruğa göndermek ikinci baskı değil, farklı bir iştir.
        key = self._fingerprint(path, str(printer["name"]))
        if key is not None:
            previous = self._recent_job(key)
            if previous is not None:
                self._log.info("aynı belge yeniden gönderilmedi",
                               printer=printer["name"], title=title,
                               window=self._duplicate_window)
                return {**previous, "duplicate": True,
                        "note": f"Bu belge {int(self._duplicate_window)} saniye içinde "
                                "zaten yazıcıya gönderildi; ikinci kez gönderilmedi."}

        args = [lp, "-d", printer["name"], "-n", str(max(1, int(copies)))]

        # KÂĞIT BOYUTU İKİ ADLA BİRDEN SÖYLENİR.
        #
        # Söylenmezse kullanıcının `~/.cups/lpoptions` dosyasındaki varsayılan
        # geçerli olur; bu makinede orada `PageSize=A6` yazıyordu ve yazıcı A4
        # belgeyi A6 sanıp hata veriyordu. İş CUPS'a sorunsuz giriyor, hatayı
        # cihaz veriyor — bu yüzden uygulama "gönderildi" derken yazıcı
        # kırmızı yanıyordu. Grafik yazdır pencereleri o dosyayı okumadığı
        # için normal belgeler düzgün çıkıyordu.
        #
        # `media` ile `PageSize` CUPS'ta AYRI iki seçenek: ilki genel IPP adı,
        # ikincisi PPD'nin kendi anahtarı. Yalnız `media` verilince PPD tabanlı
        # sürücü (hpcups) lpoptions'daki `PageSize`i kullanmaya devam
        # edebiliyor. İkisi birden verilir ki hangi yoldan okunursa okunsun
        # aynı boyut çıksın.
        if self._media:
            args += ["-o", f"media={self._media}", "-o", f"PageSize={self._media}"]

        if title:
            args += ["-t", title]
        args.append(str(path))

        code, out, err = await self._run(*args)
        if code != 0:
            raise PrinterError(
                f"Yazıcı bağlantısı hatası: baskı gönderilemedi — {err.strip() or out.strip() or code}"
            )

        # "request id is HP_LaserJet_MFP_M139-M142-42 (1 file(s))"
        job = ""
        match = re.search(r"request id is (\S+)", out)
        if match:
            job = match.group(1)

        self._log.info("baskı gönderildi", printer=printer["name"], job=job,
                       title=title, media=self._media)
        result: dict[str, Any] = {
            "ok": True, "printer": printer["name"], "label": printer["name"],
            "job": job, "copies": max(1, int(copies)), "media": self._media,
            "duplicate": False,
        }
        # Damga İŞ KUYRUĞA GİRDİKTEN SONRA yazılır: başarısız bir gönderim
        # tekrar denenebilmeli, yoksa ilk deneme yazıcıyı bulamadığında
        # kullanıcı pencere boyunca hiç basamazdı.
        if key is not None:
            self._recent[key] = (time.monotonic(), dict(result))
        return result

    async def status(self) -> dict[str, Any]:
        """Ekranın gösterebileceği durum — hata fırlatmaz, anlatır.

        Kuyruğun ne dediğiyle yetinmez: `ipp-usb` üzerinden CİHAZIN KENDİSİNE
        de sorar. Kuyruk "boşta" derken yazıcıda kâğıt bitmiş olabilir.
        """
        try:
            printers = await self.discover()
        except PrinterError as failure:
            return {"ready": False, "error": str(failure), "printers": [], "device": {}}

        device = await self.device_info()

        try:
            chosen = await self.target()
        except PrinterError as failure:
            return {"ready": False, "error": str(failure),
                    "printers": printers, "device": device}

        # Cihaz kendi derdini bildiriyorsa ekran onu göstersin.
        reasons = str(device.get("printer-state-reasons") or "").strip()
        warning = ""
        if reasons and reasons != "none":
            warning = f"Yazıcı bildiriyor: {reasons}"
        toner = device.get("tonerPercent")
        if isinstance(toner, int) and toner <= 10:
            warning = (f"{warning} · " if warning else "") + f"Toner %{toner}"

        return {"ready": True, "error": "", "warning": warning,
                "target": chosen, "printers": printers, "device": device}

    async def health(self) -> dict[str, Any]:
        """Otonom baskı öncesi tek soruluk denetim: şimdi bassak çıkar mı?

        Zamanlanmış baskı için düşünüldü. Gece çalışan bir iş, hatayı ancak
        ertesi sabah fark edilecek şekilde sessizce yutmamalı; bu yüzden
        çağıran önce buraya sorar ve `ok` değilse `reason` ile günlüğe yazar.
        """
        state = await self.status()
        device = state.get("device") or {}
        return {
            "ok": bool(state.get("ready")),
            "reason": state.get("error") or state.get("warning") or "",
            "printer": (state.get("target") or {}).get("name", ""),
            "deviceState": device.get("printer-state", ""),
            "tonerPercent": device.get("tonerPercent"),
            "ippusb": bool(device.get("port")),
        }
