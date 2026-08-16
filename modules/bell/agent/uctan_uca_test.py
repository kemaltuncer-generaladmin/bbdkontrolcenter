"""Zil ajanını sahte bir köprüye bağlayıp uçtan uca sınar.

Windows'a taşımadan ÖNCE burada kırılsın: okula gidip "çalışmıyor" demek
pahalı. Sınanan zincir gerçek — `agent.py` hiç değiştirilmeden koşuyor;
taklit edilen tek şey köprünün kendisi.

Windows olmadığı için `winsound` yok; ajanın `Player` sınıfı o durumda
"çalınacaktı: …" diye günlüğe yazıyor. Çalma ÇAĞRISININ doğru dosyayla ve
doğru anda yapıldığını ölçüyoruz.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8899
TOKEN = "test-cihaz-belirteci"
EV = Path(sys.argv[1])


def wav(saniye: float, tohum: str) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        seed = hashlib.sha256(tohum.encode()).digest()
        n = int(8000 * saniye) * 2
        w.writeframes((seed * (n // len(seed) + 1))[:n])
    return buf.getvalue()


def kimlik(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


SESLER = {kimlik(wav(0.3, ad)): wav(0.3, ad) for ad in ("zil", "anons")}
ZIL, ANONS = sorted(SESLER)

DURUM: dict = {"komutlar": [], "ackler": [], "indirilen": [], "poll": 0, "have": []}


class Kopru(BaseHTTPRequestHandler):
    def log_message(self, *a):  # sessiz
        pass

    def _yetki(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def _json(self, payload):
        gövde = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(gövde)))
        self.end_headers()
        self.wfile.write(gövde)

    def do_GET(self):
        if not self._yetki():
            return
        yol = urlparse(self.path)
        if yol.path == "/api/bell/poll":
            DURUM["poll"] += 1
            DURUM.setdefault("yollar", []).append(self.path)
            q = parse_qs(yol.query)
            DURUM["have"] = q.get("have[]", [])
            DURUM.setdefault("have_hepsi", set()).update(DURUM["have"])
            since = int(q.get("since", ["0"])[0])
            self._json({
                "now": datetime.now().astimezone().isoformat(),
                "commands": [c for c in DURUM["komutlar"] if c["id"] > since],
                "sounds": sorted(SESLER),
                "pollSeconds": 1,
            })
        elif yol.path.startswith("/api/bell/sound/"):
            sid = yol.path.rsplit("/", 1)[-1]
            if sid not in SESLER:
                self.send_response(404)
                self.end_headers()
                return
            DURUM["indirilen"].append(sid)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(SESLER[sid])))
            self.end_headers()
            self.wfile.write(SESLER[sid])
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._yetki():
            return
        n = int(self.headers.get("Content-Length", 0))
        gövde = json.loads(self.rfile.read(n) or b"{}")
        if urlparse(self.path).path == "/api/bell/ack":
            gövde["_at"] = time.time()
            DURUM["ackler"].append(gövde)
            self._json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


def main() -> int:
    EV.mkdir(parents=True, exist_ok=True)
    (EV / "config.json").write_text(json.dumps({
        "baseUrl": f"http://127.0.0.1:{PORT}", "token": TOKEN, "pollSeconds": 1,
    }), encoding="utf-8")

    sunucu = ThreadingHTTPServer(("127.0.0.1", PORT), Kopru)
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()

    ajan = Path(sys.argv[2])
    süreç = subprocess.Popen(
        [sys.executable, str(ajan)],
        env={"BBDZIL_HOME": str(EV), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    sonuç = {"ok": 0, "bad": 0}

    def kontrol(k: bool, mesaj: str, ek: str = "") -> None:
        print(f"  {'✓' if k else '✗'} {mesaj}" + (f"  [{ek}]" if ek else ""))
        sonuç["ok" if k else "bad"] += 1

    try:
        # 1. Sesler KOMUT OLMADAN, kendiliğinden inmeli.
        for _ in range(30):
            time.sleep(0.5)
            if len(DURUM["indirilen"]) >= 2:
                break
        kontrol(len(DURUM["indirilen"]) >= 2,
                "sesler komut beklemeden indi", f"{len(DURUM['indirilen'])} dosya")

        diskte = sorted(p.stem for p in (EV / "sounds").glob("*.wav"))
        kontrol(diskte == sorted(SESLER),
                "dosya adları içerik özeti", ", ".join(d[:8] for d in diskte))
        kontrol(all((EV / "sounds" / f"{s}.wav").read_bytes() == SESLER[s]
                    for s in SESLER), "inen içerik birebir aynı")
        kontrol(not list((EV / "sounds").glob("*.part")), "yarım dosya kalmadı")

        # 2. Ajan yerelindekileri köprüye bildiriyor mu?
        # İLK sorgu haliyle boş: ajan henüz indirmemiştir. Listeyi taşıyan
        # bir sorgu gelene kadar bekle — yoksa test kendi yarışını ölçer.
        for _ in range(20):
            if any("have" in y for y in DURUM.get("yollar", [])):
                break
            time.sleep(0.5)
        yollar = DURUM.get("yollar", [])
        taşıyan = [y for y in yollar if "have" in y]
        kontrol(bool(taşıyan) and all(s[:8] in taşıyan[-1] for s in SESLER),
                "ajan yerel listesini bildiriyor",
                f"{len(taşıyan)}/{len(yollar)} sorgu listeyi taşıdı")

        # 3. playAt ANINDA çalmalı — ne erken ne geç.
        hedef = datetime.now().astimezone() + timedelta(seconds=4)
        DURUM["komutlar"].append({
            "id": 1, "playAt": hedef.isoformat(),
            "items": [{"kind": "zil", "hash": ZIL, "volume": 90},
                      {"kind": "anons", "hash": ANONS, "volume": 90}],
            "queuedAt": datetime.now().astimezone().isoformat(),
        })
        for _ in range(40):
            time.sleep(0.5)
            if DURUM["ackler"]:
                break

        kontrol(bool(DURUM["ackler"]), "komut işlendi ve bildirildi")
        if DURUM["ackler"]:
            ack = DURUM["ackler"][0]
            kontrol(ack["ok"] is True, "çalma başarılı bildirildi", ack.get("detail", ""))
            sapma = ack["_at"] - hedef.timestamp()
            kontrol(-0.5 < sapma < 2.5,
                    "playAt anında çaldı", f"sapma {sapma:+.2f} sn")
            kontrol("late_download" not in ack.get("detail", ""),
                    "indirme beklenmedi — ses zaten yereldeydi")

        # 4. Zamanı geçmiş komut ATLANMALI.
        geç = datetime.now().astimezone() - timedelta(seconds=120)
        DURUM["komutlar"].append({
            "id": 2, "playAt": geç.isoformat(),
            "items": [{"kind": "zil", "hash": ZIL, "volume": 90}],
            "queuedAt": datetime.now().astimezone().isoformat(),
        })
        for _ in range(20):
            time.sleep(0.5)
            if len(DURUM["ackler"]) >= 2:
                break
        if len(DURUM["ackler"]) >= 2:
            ikinci = DURUM["ackler"][1]
            kontrol(ikinci["ok"] is False and "atland" in ikinci.get("detail", ""),
                    "geç kalan zil ÇALINMADI", ikinci.get("detail", ""))
        else:
            kontrol(False, "geç kalan komut için bildirim gelmedi")

        # 5. Ses silinirse kendini onarmalı.
        önce = len(DURUM["indirilen"])
        (EV / "sounds" / f"{ZIL}.wav").unlink()
        for _ in range(20):
            time.sleep(0.5)
            if len(DURUM["indirilen"]) > önce:
                break
        kontrol((EV / "sounds" / f"{ZIL}.wav").is_file(),
                "silinen ses kendiliğinden geri indi")

        günlük = (EV / "agent.log").read_text(encoding="utf-8", errors="replace")
        kontrol("zil ajanı başladı" in günlük, "günlük yazılıyor")
        kontrol(DURUM["poll"] > 3, "sorgulama döngüsü dönüyor",
                f"{DURUM['poll']} istek")

    finally:
        süreç.terminate()
        süreç.wait(timeout=5)
        sunucu.shutdown()

    print(f"\n  {sonuç['ok']} geçti, {sonuç['bad']} kaldı")
    return 1 if sonuç["bad"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
