"""Veri dizini — yazılabilir dosyaların TEK adres çözümü (ADR 0023).

NEDEN AYRI BİR DOSYA. Bugüne dek `data/` deponun içindeydi ve bu doğruydu:
tek makine, tek klasör, elle yedeklenebilir. Kurulu uygulamada aynı yer
YAZILAMAZ — program dosyaları klasörü (Windows'ta `Program Files`, Linux'ta
`/usr/lib/...`) kullanıcıya kapalıdır. Veritabanı, kasa anahtarı, ses
kitaplığı ve günlük oraya yazılamaz; uygulama ilk açılışta ölür.

Çözüm zinciri — üstteki alttakini ezer:

    1. `KM_DATA_DIR` ortam değişkeni      (elle taşıma / test / acil müdahale)
    2. depo kökünde `.git` varsa `<kök>/data`   (GELİŞTİRME — bugünkü davranış)
    3. platformun kullanıcı veri klasörü        (KURULU uygulama)

İkinci basamağın ölçütü bilerek `.git`tir: paketlenmiş uygulamada `.git`
bulunmaz, dolayısıyla ölçüt kendiliğinden doğru tarafa düşer. Ayrı bir
"geliştirme kipi" bayrağı olsaydı, o bayrağı paketlemede kapatmayı unutmak
kurulu uygulamayı sessizce depo klasörüne yazdırırdı.

BU DOSYA TEK KAPIDIR. Yol çözümü başka yerde tekrarlanmaz; `Config.path`
buradan geçer ve `data/` ile başlayan her göreli ayar değeri kendiliğinden
doğru diske düşer.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from km_core.files.private import ensure_private_dir

#: Veri dizinini elle söylemenin yolu. Boşsa yok sayılır.
DATA_DIR_ENV = "KM_DATA_DIR"

#: Ayar değerlerinde veri dizinini gösteren ön ek (`data/sounds` → `<veri>/sounds`).
DATA_SEGMENT = "data"

#: Linux/XDG adı — küçük harf, boşluksuz. Kabuk yollarında tırnak gerektirmez.
APP_SLUG = "kontrol-merkezi"

#: Windows ve macOS adı — o platformlarda kullanıcıya görünen klasör adıdır.
APP_NAME = "Kontrol Merkezi"


def is_development(root: Path) -> bool:
    """Depo klasöründen mi çalışıyoruz?

    `.git` bir klasör (normal kopya) ya da dosya (worktree) olabilir; ikisi de
    "burası depodur" der.
    """
    return (root / ".git").exists()


def system_data_dir() -> Path:
    """Platformun kullanıcı veri klasörü. Klasörü OLUŞTURMAZ, adresi söyler."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        roaming = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return roaming / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux ve diğerleri: XDG. Değişken tanımsızsa XDG'nin kendi varsayılanı.
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    share = Path(base) if base else Path.home() / ".local" / "share"
    return share / APP_SLUG


def data_dir(root: Path) -> Path:
    """Yazılabilir veri dizini. Yoksa OLUŞTURULUR ve 0700'e çekilir.

    Oluşturma burada yapılır çünkü kurulu uygulamada bunu yapacak başka kimse
    yok: `%APPDATA%\\Kontrol Merkezi` ilk açılışta mevcut değildir ve SQLite
    olmayan klasöre dosya açamaz.

    Klasör 0700'dür — içinde öğrenci/veli telefonu taşıyan veritabanı ve kasa
    anahtarı durur (`km_core/files/private.py` başlığındaki denetim bulgusu).
    Oluşturulamazsa açılış düşürülmez (K7): adres yine döner, hatayı onu
    kullanan katman kendi bağlamıyla söyler.
    """
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        target = Path(override).expanduser()
    elif is_development(root):
        target = root / DATA_SEGMENT
    else:
        target = system_data_dir()

    try:
        ensure_private_dir(target)
    except OSError:
        pass
    return target


def resolve_path(value: str | Path, root: Path) -> Path:
    """Ayardaki yolu diskteki gerçek yola çevirir.

    - Mutlak yol olduğu gibi kullanılır (kullanıcının açık tercihi).
    - `data/...` ile başlayan göreli yol VERİ DİZİNİNE düşer.
    - Başka bir göreli yol depo/kaynak köküne göre çözülür; oradakiler
      okunan dosyalardır (ayar, manifest), yazılan değil.
    """
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate

    parts = candidate.parts
    if parts and parts[0] == DATA_SEGMENT:
        return data_dir(root).joinpath(*parts[1:])
    return root / candidate
