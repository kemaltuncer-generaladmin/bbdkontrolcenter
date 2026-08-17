"""Çıktı kaydı — dosyayı YAZAN fonksiyonda doğar (ADR 0019).

NEDEN BURADA. Yirmiden fazla modül diske PDF ve CSV yazıyor ve hepsi
`write_private` üzerinden geçiyor. Kaydı her modülün ayrıca bildirmesi yirmi
ayrı çağrı noktası demekti; biri unutulduğunda hiçbir test kırılmaz, çıktı
yalnızca listede görünmez. Bu yüzden kayıt tek yerde, dosya diske yazılırken
düşer — **yirmi modülün hiçbirinde tek satır değişmez.**

K1 KORUNUR. `source` alanı üreten modülün kimliğidir ve çekirdek ona göre
DALLANMAZ: yığından okunur, veri olarak saklanır, ekranda gösterilir. Burada
hiçbir modül adı geçmez; `modules/` silinse bu dosya yine çalışır ve her
çıktıyı `core` kaynağıyla kaydeder.

K7 KORUNUR. Kayıt yazılamazsa dosya üretimi DURMAZ. `record_write` hiçbir
istisna sızdırmaz; sorunu günlüğe yazar ve kullanıcı raporunu yine alır.
Kaydın kaybı can sıkıcıdır, raporun kaybı işi durdurur.

NEDEN AYRI BİR SQLite BAĞLANTISI. `write_private` SENKRON bir fonksiyondur ve
çekirdeğin `aiosqlite` bağlantısı istek bağlamında yaşar; buraya bir `await`
sokmak yirmi modülün çağrı biçimini değiştirirdi — tam da ADR'nin engellemek
istediği şey. Kısa ömürlü `sqlite3` bağlantısı aynı dosyayı WAL kipinde açar
(kilit çakışması `timeout` ile beklenir, çözülmezse kayıt düşer, dosya yazılır).

Veritabanı dosyası YOKSA kayıt sessizce atlanır: burada boş bir veritabanı
yaratmak, açılışta `Store.open()` tarafından kurulan şemanın yerine yarım bir
dosya bırakırdı.

DEPO TAHMİN EDİLMEZ, BİLDİRİLİR. Bu modül hangi veritabanına yazacağını ayardan
okumaz; yalnız `use_database()` ile AÇIKÇA gösterilen depoya yazar. Bağı
uygulama açılışta kurar (`km_core/http/app.py` lifespan) ve kapanışta çözer.
Ayardan okumak bir kez denendi ve bedeli ölçüldü: uygulama ayakta değilken —
testlerde, betiklerde, elle çağrılan yardımcılarda — kayıt yine geliştiricinin
GERÇEK deposuna düşüyordu. Bir tek takım koşusu `data/kontrol-merkezi.sqlite`
içine 390 çöp satır bıraktı; yolların çoğu `/tmp/…` altındaydı ve ilk açılışta
"dosya bulunamadı" diye görünecekti.

Ayırt edici soru "pytest koşuyor mu" DEĞİLDİR — o yalnız bir belirtiyi kapatır,
betikleri ve gömülü kullanımı açıkta bırakırdı. Soru şudur: yazan taraf bir depo
gösterdi mi? Göstermediyse kayıt düşmez ve bu SESSİZ kalmaz (aşağıda loglanır).
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from types import FrameType

import structlog

log = structlog.get_logger("km.files.outputs")

#: Modülü olmayan çağrılar (çekirdeğin kendi yazdıkları, testler) bu kaynakla
#: kaydedilir. Satır GİZLENMEZ; nereden geldiği belirsiz bir çıktı listede
#: "çekirdek" diye durur.
CORE_SOURCE = "core"

#: Kilit beklemesi. Sidecar tek süreçtir; çakışma ancak açılış göçleriyle aynı
#: ana denk gelirse olur ve o da saniyeler sürmez.
BUSY_TIMEOUT = 3.0

#: Uzantısı olmayan ya da tanınmayan dosyanın türü. Tür bir SÜZGEÇ alanıdır;
#: boş bırakmak "türü olmayan" satırları süzgecin dışına düşürürdü.
UNKNOWN_KIND = "dosya"

#: PDF sayfa sayısı: sayfa nesneleri sözlüğü sıkıştırılmadan yazılır.
#: `/Type/Pages` (kapsayıcı) sayılmasın diye 's' hariç tutulur.
_PDF_PAGE = re.compile(rb"/Type\s*/Page[^s]")

#: Kernel her modülü `km_mod_<id>` paketiyle yükler (bkz. kernel `_import_module`).
_MODULE_PACKAGE = "km_mod_"

#: Modül klasörü adı — `modules/<id>/…` yolundaki ikinci parça.
_MODULE_ID = re.compile(r"^[a-z][a-z0-9_]*$")

#: Çıktıyı üreten kişi. Çekirdeğin HTTP katmanı oturumu çözdüğü yerde
#: `use_actor(user.id)` ile doldurulur; doldurulmadığında kayıt yine düşer,
#: yalnız `user_id` boş kalır. "Kim aldı" sorusunun cevabı yoksa satırı hiç
#: yazmamak, cevabı büsbütün yok etmek olurdu.
_actor: ContextVar[str | None] = ContextVar("km_output_actor", default=None)

#: Bildirilen depo. `None` ise kayıt tutulmaz — tahmin edilmez.
_database: Path | None = None

#: "Depo bildirilmedi" uyarısı bağ çözülü kaldığı sürece BİR KEZ verilir.
#: Her yazmada bağırmak takım koşusunu okunmaz eder; hiç bağırmamak ise
#: kaydın sessizce durduğu bir kurulumu fark edilmez kılardı.
_unbound_warned = False


# ------------------------------------------------------------------ ayarlar


def use_database(path: Path | None) -> None:
    """Kaydın yazılacağı depoyu bildirir; `None` bağı çözer.

    Bağı uygulama kurar (`km_core/http/app.py` lifespan). Testler ve gömülü
    kullanım kendi deposunu aynı kapıdan gösterir.
    """
    global _database, _unbound_warned
    _database = path
    if path is not None:
        _unbound_warned = False


def database_path() -> Path | None:
    """Kayıt veritabanı — YALNIZ bildirilen depo, yoksa `None`.

    Ayara bakılmaz; gerekçesi modül başlığında.
    """
    return _database


@contextmanager
def use_actor(user_id: str | None) -> Iterator[None]:
    """Bu blok içinde yazılan çıktılar `user_id` adına kaydedilir."""
    token = _actor.set(user_id or None)
    try:
        yield
    finally:
        _actor.reset(token)


def current_actor() -> str | None:
    return _actor.get()


# -------------------------------------------------------------------- kayıt


def record_write(path: Path, content: bytes) -> str | None:
    """Diske yazılan çıktıyı `outputs` tablosuna işler.

    Dönüş: kayıt kimliği; kayıt düşmediyse `None`. Çağıran bu dönüşe BAKMAK
    ZORUNDA DEĞİLDİR — dosya zaten yazılmıştır (K7).
    """
    try:
        return _insert(
            path=path,
            size=len(content),
            digest=hashlib.sha256(content).hexdigest()[:16],
            pages=_pdf_pages(path, content),
            source=_caller_source(),
            user_id=_actor.get(),
        )
    except Exception as failure:  # noqa: BLE001 — kayıt kaybı dosyayı götürmez (K7)
        log.warning("çıktı kaydı düşürülemedi", path=str(path), error=str(failure))
        return None


def _insert(*, path: Path, size: int, digest: str, pages: int | None,
            source: str, user_id: str | None) -> str | None:
    database = database_path()
    if database is None:
        _warn_unbound(path)
        return None
    if not database.is_file():
        # Açılışta `Store.open()` dosyayı kurar. Yoksa uygulama henüz ayakta
        # değildir; burada yeni bir dosya yaratmak şemasız bir depo bırakırdı.
        log.debug("çıktı kaydı atlandı — depo dosyası yok",
                  path=str(path), database=str(database))
        return None

    output_id = uuid.uuid4().hex
    row = (
        output_id,
        datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        user_id,
        source,
        _kind_of(path),
        path.name,
        str(path),
        size,
        pages,
        digest,
    )
    connection = sqlite3.connect(database, timeout=BUSY_TIMEOUT)
    try:
        connection.execute(
            "INSERT INTO outputs (id, created_at, user_id, source, kind, title, path, "
            "bytes, pages, params_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
        connection.commit()
    finally:
        connection.close()
    return output_id


def _warn_unbound(path: Path) -> None:
    """Depo bildirilmemişken çıktı yazıldı — kayıt düşmez ama SESSİZ kalmaz.

    Kayıt olmadan üretilen çıktı ekranda hiç görünmez ve hiçbir test kırılır;
    ADR 0019'un baştan beri korktuğu sessiz kayıp tam budur. Uyarı bağ çözülü
    kaldığı sürece bir kez verilir: takım koşusunda tek satır, kaydın gerçekten
    durduğu bir kurulumda ise ilk çıktıda görünen bir işaret.
    """
    global _unbound_warned
    if _unbound_warned:
        log.debug("çıktı kaydı atlandı — depo bildirilmedi", path=str(path))
        return
    _unbound_warned = True
    log.warning(
        "çıktı kaydı tutulmuyor — depo bildirilmedi (use_database çağrılmadı)",
        path=str(path),
    )


# ------------------------------------------------------------------ okuma


def _kind_of(path: Path) -> str:
    """Dosya türü — süzgecin "tür" alanı. Uzantıdan okunur, tahmin edilmez."""
    suffix = path.suffix.lower().lstrip(".")
    return suffix if suffix.isalnum() else UNKNOWN_KIND


def _pdf_pages(path: Path, content: bytes) -> int | None:
    """PDF sayfa sayısı; sayılamıyorsa `None`.

    UYDURULMAZ: PDF'in sayfa sözlükleri sıkıştırılmış bir nesne akışındaysa
    desen tutmaz ve alan boş kalır. Ekranda "—" görünür; yanlış bir sayı
    göstermek, kaç kâğıt çıkacağını sanan kullanıcıyı yanıltırdı.
    """
    if path.suffix.lower() != ".pdf":
        return None
    count = len(_PDF_PAGE.findall(content))
    return count or None


def _caller_source() -> str:
    """Çıktıyı üreten modülün kimliği; modül değilse `core`.

    Yığın YUKARI doğru gezilir ve bu paketin (`km_core/files`) dışındaki ilk
    modül karesi kazanır. Kimlik iki bağımsız işaretten okunur: kernel'in
    yüklerken verdiği paket adı (`km_mod_<id>`) ve dosyanın `modules/<id>/`
    yolu. İkisi de veriye bakar; modül adı bu dosyada geçmez (K1).
    """
    here = str(Path(__file__).resolve().parent)
    frame: FrameType | None = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if not filename.startswith(here):
            found = _source_of(frame.f_globals.get("__name__", ""), filename)
            if found:
                return found
        frame = frame.f_back
    return CORE_SOURCE


def _source_of(module_name: str, filename: str) -> str:
    if module_name.startswith(_MODULE_PACKAGE):
        candidate = module_name[len(_MODULE_PACKAGE):].split(".")[0]
        if _MODULE_ID.match(candidate):
            return candidate
    return _source_of_file(filename)


@lru_cache(maxsize=512)
def _source_of_file(filename: str) -> str:
    """`…/modules/<id>/backend/service.py` → `<id>`. Değilse boş dizge.

    Yol tek başına yetmez: `modules` adında bir klasör başka bir ağaçta da
    olabilir. Bu yüzden aday klasörde `module.yaml` aranır — manifesti olan
    klasör modüldür. Sonuç dosya başına önbelleklenir; her yazmada disk
    yoklanmaz.
    """
    parts = Path(filename).parts
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] != "modules":
            continue
        candidate = parts[index + 1]
        if not _MODULE_ID.match(candidate):
            continue
        if Path(*parts[: index + 2], "module.yaml").is_file():
            return candidate
    return ""
