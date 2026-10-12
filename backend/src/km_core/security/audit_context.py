"""Denetim izinin istek bağlamı — `X-KM-On-Behalf-Of` ve `X-Request-Id` (ADR 0028).

NEDEN BAĞLAM DEĞİŞKENİ. `Identity.audit` istek nesnesini görmez; çağrıldığı
yerler (izin reddi, PIN teyidi, kullanıcı yönetimi) zaten kalabalık ve her
birine iki argüman daha eklemek, birinin unutulup izin yarısının boş kalması
demekti. Kimlik kapısı (`km_core/http/deps.py`) değerleri istek boyunca buraya
koyar; `audit` buradan okur. `outputs_log.use_actor` ile aynı desen.

İKİ DEĞER DE YETKİ VERMEZ. Yalnız denetim izine yazılır:

  · `on_behalf_of` — servis anahtarıyla gelen istekte işlemi dış uygulamada
    başlatan kişi (örn. `akademi:staff:42:Ayşe Yılmaz`). YALNIZ servis
    sorumlusunda doldurulur; kişi oturumunda başlık yok sayılır, çünkü orada
    "kimin adına" sorusunun cevabı zaten oturumun sahibidir.
  · `request_id` — uçtan uca iz sürmek için istemcinin verdiği kimlik.

Başlıklar istemciden gelir, temizlenmeden yazılmaz: yazdırılamayan karakter
atılır, uzunluk sınırlanır. Kayıt satırına kaçış dizisi ya da satır sonu
sokulamaz.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import unquote

#: `X-KM-On-Behalf-Of` için üst sınır (karakter).
ON_BEHALF_OF_MAX = 160

#: `X-Request-Id` için üst sınır (karakter).
REQUEST_ID_MAX = 128


@dataclass(frozen=True, slots=True)
class AuditContext:
    on_behalf_of: str | None = None
    request_id: str | None = None

    @property
    def empty(self) -> bool:
        return self.on_behalf_of is None and self.request_id is None


_CONTEXT: ContextVar[AuditContext | None] = ContextVar("km_audit_context", default=None)


def decode_header_text(value: str | None) -> str | None:
    """HTTP başlığındaki Türkçe metni çözer.

    Başlıklar ASCII/latin-1 taşır; "Ayşe Yılmaz" doğrudan yazılamaz. İstemci
    iki biçimden birini kullanır ve ikisi de kabul edilir:

      · **yüzde kodlama** (önerilen): `Ay%C5%9Fe%20Y%C4%B1lmaz`
      · **ham UTF-8 bayt**: sunucu baytları latin-1 diye okur; burada geri
        çevrilir. Geçerli UTF-8 değilse metin olduğu gibi kalır.
    """
    if value is None:
        return None
    text = str(value)
    try:
        text = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    if "%" in text:
        try:
            text = unquote(text, errors="strict")
        except UnicodeDecodeError:
            pass
    return text


def clean_header(value: str | None, limit: int) -> str | None:
    """Yazdırılabilir karakterlerle sınırlı, kırpılmış metin; boşsa `None`."""
    if not value:
        return None
    text = "".join(char for char in str(decode_header_text(value)) if char.isprintable()).strip()
    return text[:limit] or None


def current() -> AuditContext | None:
    return _CONTEXT.get()


@contextmanager
def use(on_behalf_of: str | None, request_id: str | None) -> Iterator[None]:
    """İstek boyunca denetim bağlamını kurar; çıkışta eskisini geri koyar.

    Değer geri alınmazsa aynı olay döngüsünde sırayı devralan bir sonraki
    istek başkasının "kimin adına"sıyla denetim satırı yazardı.
    """
    token = _CONTEXT.set(AuditContext(on_behalf_of=on_behalf_of, request_id=request_id))
    try:
        yield
    finally:
        _CONTEXT.reset(token)
