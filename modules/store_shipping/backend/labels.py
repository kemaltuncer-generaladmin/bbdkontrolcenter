"""Toplu kargo etiketi — sayfa yerleşimi ve birleştirme.

NEDEN ETİKETİ BİZ ÇİZMİYORUZ. Etiket üzerindeki barkod taşıyıcının kendi
numaralandırmasıdır; şubede o barkod okutulur. Kendi çizdiğimiz bir barkod
okunmazsa gönderi elde kalır. Bu yüzden etiketin KENDİSİ her zaman
taşıyıcıdan gelir (`store.api.bbd_shipment_label`); bu dosya yalnızca gelen
PDF'leri istenen kâğıda YERLEŞTİRİR.

İKİ BİÇİM:
  · `thermal-100x150` — termal etiket yazıcısı. Her etiket kendi 100×150 mm
    sayfasında, kenara yapışmasın diye 2 mm boşlukla.
  · `a4-4up` — normal lazer yazıcı. A4'e 2×2 dört etiket; her göz A6
    (105×148,5 mm) ve 5 mm iç boşluk bırakır ki makasla kesim payı kalsın.

YERLEŞİM SAF, BİRLEŞTİRME DEĞİL. `plan()` ve `fit()` yalnız aritmetiktir ve
`pypdf` olmadan test edilir; `compose()` ise PDF yazar ve kütüphaneyi TEMBEL
import eder. Paket kurulu değilse modül yine yüklenir, yalnız bu uç anlaşılır
bir hata döner (K7).
"""

from __future__ import annotations

from typing import Any

#: 1 mm kaç PDF puanı. PDF birimi 1/72 inç'tir.
MM = 72.0 / 25.4

THERMAL = "thermal-100x150"
A4_4UP = "a4-4up"
FORMATS = (THERMAL, A4_4UP)

FORMAT_LABELS = {
    THERMAL: "Termal 100×150 mm",
    A4_4UP: "A4 · sayfada 4 etiket",
}

#: `pypdf` yoksa gösterilecek metin. Kullanıcıya ne yapacağı söylenir; "modül
#: hazır değil" gibi kapalı bir cümle bırakılmaz.
MISSING_LIBRARY = (
    "Toplu etiket sayfası üretilemedi: `pypdf` kurulu değil. "
    "Kurulum: scripts/install-deps.sh (modül bağımlılığı olarak ilan edilmiştir). "
    "Tek gönderinin etiketi bu paket olmadan da indirilebilir."
)


class LabelError(RuntimeError):
    """Etiket sayfası üretilemedi. Ekran mesajı gösterir, çökmez."""


def page_size(fmt: str) -> tuple[float, float]:
    """Sayfa ölçüsü (puan)."""
    if fmt == THERMAL:
        return (100 * MM, 150 * MM)
    if fmt == A4_4UP:
        return (210 * MM, 297 * MM)
    raise LabelError(f"Bilinmeyen etiket biçimi: {fmt}")


def slots(fmt: str) -> list[tuple[float, float, float, float]]:
    """Bir sayfadaki göz kutuları: (x, y, genişlik, yükseklik) puan.

    PDF'te başlangıç noktası SOL ALTTIR. A4 gözleri okuma sırasına göre
    döner (sol üst → sağ üst → sol alt → sağ alt); yazdırıp kesen kişi
    etiketleri gönderi listesiyle aynı sırada bulur.
    """
    if fmt == THERMAL:
        pad = 2 * MM
        width, height = page_size(THERMAL)
        return [(pad, pad, width - 2 * pad, height - 2 * pad)]
    if fmt == A4_4UP:
        pad = 5 * MM
        width, height = page_size(A4_4UP)
        half_w, half_h = width / 2, height / 2
        cells = [(0, half_h), (half_w, half_h), (0, 0), (half_w, 0)]
        return [(x + pad, y + pad, half_w - 2 * pad, half_h - 2 * pad) for x, y in cells]
    raise LabelError(f"Bilinmeyen etiket biçimi: {fmt}")


def plan(count: int, fmt: str) -> list[list[dict[str, Any]]]:
    """`count` etiketin sayfa sayfa yerleşimi.

    Dönüş: her sayfa için `[{"index": kaçıncı etiket, "box": (x, y, w, h)}, …]`.
    Son sayfa eksik kalabilir; boş gözler BOŞ BIRAKILIR — tekrar eden etiket
    basmak, aynı gönderiyi iki kez kargoya vermek demektir.
    """
    total = max(0, int(count))
    boxes = slots(fmt)
    pages: list[list[dict[str, Any]]] = []
    for start in range(0, total, len(boxes)):
        page = [{"index": start + offset, "box": box}
                for offset, box in enumerate(boxes)
                if start + offset < total]
        pages.append(page)
    return pages


def fit(source_width: float, source_height: float,
        box: tuple[float, float, float, float]) -> tuple[float, float, float]:
    """Kaynağı kutuya ORANI BOZMADAN sığdırır: (ölçek, dx, dy).

    Kutudan küçük etiket de BÜYÜTÜLÜR: taşıyıcıların bir kısmı etiketi A4
    sayfanın köşesine basıyor ve olduğu gibi yerleştirilirse termal kâğıtta
    okunamayacak kadar küçük kalıyor. Oran korunduğu için barkod bozulmaz.
    """
    x, y, width, height = box
    if source_width <= 0 or source_height <= 0:
        raise LabelError("Etiket PDF'inin sayfa ölçüsü okunamadı.")
    scale = min(width / source_width, height / source_height)
    dx = x + (width - source_width * scale) / 2
    dy = y + (height - source_height * scale) / 2
    return (scale, dx, dy)


def compose(labels: list[bytes], fmt: str) -> bytes:
    """Taşıyıcıdan gelen etiket PDF'lerini tek dosyada birleştirir.

    Çok sayfalı bir etiket PDF'i geldiğinde SAYFALARIN HEPSİ yerleştirilir:
    bazı taşıyıcılar ikinci sayfaya iade barkodunu koyuyor ve onu atmak
    iadeyi imkânsız kılardı.
    """
    if fmt not in FORMATS:
        raise LabelError(f"Bilinmeyen etiket biçimi: {fmt}")
    if not labels:
        raise LabelError("Birleştirilecek etiket yok.")

    try:
        from pypdf import PageObject, PdfReader, PdfWriter, Transformation
    except ImportError as failure:  # pragma: no cover - paket yoksa
        raise LabelError(MISSING_LIBRARY) from failure

    import io

    sources: list[Any] = []
    for order, content in enumerate(labels, start=1):
        if not content:
            raise LabelError(f"{order}. etiket boş geldi; taşıyıcı dosyayı vermedi.")
        try:
            reader = PdfReader(io.BytesIO(content))
            sources.extend(reader.pages)
        except Exception as failure:      # bozuk PDF dışarıdan gelir, ad ile söylenir
            raise LabelError(f"{order}. etiket PDF olarak okunamadı: {failure}") from failure

    width, height = page_size(fmt)
    writer = PdfWriter()
    for page_plan in plan(len(sources), fmt):
        sheet = PageObject.create_blank_page(width=width, height=height)
        for cell in page_plan:
            source = sources[cell["index"]]
            scale, dx, dy = fit(float(source.mediabox.width), float(source.mediabox.height),
                                cell["box"])
            sheet.merge_transformed_page(
                source, Transformation().scale(scale).translate(dx, dy))
        writer.add_page(sheet)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def concat(parts: list[bytes]) -> bytes:
    """PDF'leri SAYFA SAYFA arka arkaya ekler. Yerleşim yapmaz.

    `compose` etiketleri sayfaya YERLEŞTİRİR (4'lü A4, termal ölçek). Burada
    öyle bir şey yok: teslim fişi zaten A4 ve fatura da A4; ikisini ölçekleyip
    bir sayfaya sıkıştırmak ikisini de okunmaz yapardı. Kullanıcı çıktıyı
    katlayıp kargo cebine koyacak, küçültüp değil.

    Boş parça ATLANIR ama sessizce değil: fatura alınamadıysa fiş yine basılır
    ve çağıran bunu ekranda söyler — fişsiz paket, faturasız pakete yeğdir.
    """
    dolu = [item for item in parts if item]
    if not dolu:
        raise LabelError("Birleştirilecek belge yok.")

    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as failure:  # pragma: no cover - paket yoksa
        raise LabelError(MISSING_LIBRARY) from failure

    import io

    writer = PdfWriter()
    for sira, content in enumerate(dolu, start=1):
        try:
            for page in PdfReader(io.BytesIO(content)).pages:
                writer.add_page(page)
        except Exception as failure:
            raise LabelError(f"{sira}. belge PDF olarak okunamadı: {failure}") from failure

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
