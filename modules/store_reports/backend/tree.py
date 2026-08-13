"""Rapor ağacı — 7 dal, 35 yaprak. Saf veri; ağa çıkmaz, durum tutmaz.

NEDEN AYRI DOSYA. Ağaç hem panelin sol sütununu hem servisin dağıtım
tablosunu hem de rapor paketinin içindekiler sayfasını besliyor. Üç yerde
ayrı listeler tutulsaydı biri güncellenip diğeri unutulduğunda ekran var
olmayan bir raporu çizerdi. Tek kaynak burasıdır.

YAPRAK BAZLI HATA. Her yaprak hangi VERİ KÜMELERİNE ihtiyaç duyduğunu
`needs` ile söyler. Servis kümeleri tek tek toplar; biri gelmezse yalnız ona
dayanan yapraklar düşer, ağacın gerisi çalışır.

UCU OLMAYAN YAPRAK GİZLENMEZ. `available: False` olan yaprak ağaçta durur ama
"uç hazır olunca açılacak" der ve çalıştırılamaz. Gizlemek, kullanıcıya
raporun hiç düşünülmediğini düşündürürdü; sessizce boş dönmek ise yanlış
rakam göstermekten beterdir.
"""

from __future__ import annotations

from typing import Any

#: Dal sırası ekranda da bu sırayla çizilir: satıştan sisteme, iş akışına göre.
BRANCHES: tuple[tuple[str, str], ...] = (
    ("sales", "Satış"),
    ("product", "Ürün"),
    ("customer", "Müşteri"),
    ("shipping", "Kargo"),
    ("finance", "Mali"),
    ("marketing", "Pazarlama"),
    ("system", "Sistem"),
)

#: Parametre şeridinin sözlüğü. Yaprak yalnız kendisine anlamlı olanları
#: ister; ekran gereksiz alanı çizmez (Kargo raporunda "kategori" sormak
#: kullanıcıyı yanıltır).
PARAMS: tuple[tuple[str, str], ...] = (
    ("range", "Tarih aralığı"),
    ("granularity", "Kırılım"),
    ("channel", "Kanal"),
    ("customerGroup", "Müşteri grubu"),
    ("category", "Kategori"),
    ("carrier", "Taşıyıcı"),
    ("compare", "Karşılaştırma"),
)

#: Geçitte KARŞILIĞI OLMAYAN yapraklar. Metin doğrudan ekranda görünür.
_MISSING_CART = (
    "Terk edilmiş sepetleri LİSTELEYEN bir uç geçitte yok: "
    "`customer_cart_items` yalnız tek müşterinin sepetini veriyor ve 1.400 "
    "müşteriyi tek tek taramak dakikada 60 istek sınırına takılır. "
    "`store.api.abandoned_carts(filters)` yayınlanınca bu rapor kendiliğinden açılacak."
)
_MISSING_ROI = (
    "Kampanya maliyeti ve sipariş eşlemesi mağazada tutulmuyor; ROI ancak "
    "uydurma bir maliyetle hesaplanabilirdi. "
    "`store.api.bbd_campaign_stats(filters)` yayınlanınca açılacak."
)


def _leaf(key: str, branch: str, label: str, hint: str, needs: tuple[str, ...],
          params: tuple[str, ...], *, available: bool = True,
          reason: str = "") -> dict[str, Any]:
    return {
        "key": key, "branch": branch, "label": label, "hint": hint,
        "needs": needs, "params": params, "available": available, "reason": reason,
    }


_RANGE = ("range",)
_SERIES = ("range", "granularity", "channel", "compare")
_SCOPED = ("range", "channel")

LEAVES: tuple[dict[str, Any], ...] = (
    # ------------------------------------------------------------- Satış
    _leaf("sales_summary", "sales", "Genel özet",
          "Ciro, sipariş, ortalama sepet ve dönem eğrisi.",
          ("orders",), _SERIES),
    _leaf("sales_channel", "sales", "Kanal bazlı",
          "Hangi satış kanalı ne kadar ciro getirdi.",
          ("orders",), ("range", "compare")),
    _leaf("sales_payment", "sales", "Ödeme yöntemi",
          "Ödeme yöntemi kırılımı ve ortalama sepet.",
          ("orders",), _SCOPED),
    _leaf("sales_hourly", "sales", "Saat/gün dağılımı",
          "Siparişin hangi saatte ve hangi günde geldiği.",
          ("orders",), _SCOPED),
    _leaf("sales_basket", "sales", "Sepet analizi",
          "Sepet büyüklüğü dağılımı ve kalem başına ortalama.",
          ("orders", "lines"), _SCOPED),
    _leaf("sales_abandoned", "sales", "Terk edilen sepet",
          "Ödemeye gitmeyen sepetler.", (), _RANGE,
          available=False, reason=_MISSING_CART),

    # -------------------------------------------------------------- Ürün
    _leaf("product_top", "product", "En çok/az satan",
          "Adet ve ciroya göre ilk 20 ve son 20 ürün.",
          ("orders", "lines"), ("range", "channel", "category")),
    _leaf("product_pareto", "product", "Pareto (ABC)",
          "Cironun %80'ini getiren ürünler — A/B/C sınıfı.",
          ("orders", "lines"), ("range", "channel")),
    _leaf("product_turnover", "product", "Stok devir hızı",
          "Dönem satışının ortalama stoka oranı.",
          ("orders", "lines", "products"), ("range", "category")),
    _leaf("product_stockout", "product", "Tükenme tahmini",
          "Günlük satış hızına göre stok kaç gün yeter.",
          ("orders", "lines", "products"), ("range", "category")),
    _leaf("product_dead", "product", "Ölü stok",
          "Dönem boyunca hiç satılmamış, stoku bağlı ürünler.",
          ("orders", "lines", "products"), ("range", "category")),
    _leaf("product_category", "product", "Kategori kırılımı",
          "Ciro ve adetin kategoriye dağılımı.",
          ("orders", "lines", "products"), ("range", "channel")),
    _leaf("product_returns", "product", "İade oranı yüksek ürünler",
          "Satışa göre iade oranı — kalite uyarısı.",
          ("orders", "lines", "refunds"), _RANGE),

    # ----------------------------------------------------------- Müşteri
    # "Yeni müşteri" ve "kayıp müşteri" DÖNEMDEN DEĞİL sipariş geçmişinin
    # tamamından okunur: yalnız seçilen aralığa bakmak, üç yıllık müşteriyi
    # "yeni" gösterirdi. `history` bu yüzden ayrı bir veri kümesidir.
    _leaf("customer_new_returning", "customer", "Yeni vs tekrar eden",
          "Dönemde ilk siparişini veren müşteri ile geri gelen müşteri.",
          ("orders", "history"), ("range", "granularity", "channel")),
    _leaf("customer_rfm", "customer", "RFM segmentleri",
          "Yakınlık · sıklık · tutar üçlüsüne göre segment.",
          ("history",), _RANGE),
    # Müşteri GRUBU sipariş listesinde hiç gelmiyor (canlıda doğrulandı);
    # `customers` kümesi olmadan bu iki rapor her satırı "Grupsuz" gösterirdi
    # ve grup süzgeci raporu sessizce boşaltırdı.
    _leaf("customer_top", "customer", "En çok harcayan",
          "Dönemin ilk 30 müşterisi.",
          ("orders", "customers"), ("range", "customerGroup")),
    _leaf("customer_city", "customer", "Şehir dağılımı",
          "Teslimat şehrine göre sipariş ve ciro.",
          ("orders",), _RANGE),
    _leaf("customer_group", "customer", "Müşteri grubu kırılımı",
          "Grup bazında ciro, sipariş ve ortalama sepet.",
          ("orders", "customers"), _RANGE),
    _leaf("customer_churn", "customer", "Kayıp müşteri",
          "Uzun süredir sipariş vermeyen eski müşteriler.",
          ("history",), _RANGE),

    # ------------------------------------------------------------- Kargo
    _leaf("shipping_carrier", "shipping", "Taşıyıcı performansı",
          "Gönderi sayısı, teslim oranı ve ortalama teslim süresi.",
          ("shipments",), ("range", "carrier")),
    _leaf("shipping_undelivered", "shipping", "Teslim edilemeyen",
          "Yolda takılan, iade dönen ve teslim edilemeyen gönderiler.",
          ("shipments",), ("range", "carrier")),
    _leaf("shipping_pnl", "shipping", "Kargo kâr/zarar",
          "Müşteriden tahsil edilen kargo ile taşıyıcıya ödenen ücret.",
          ("orders", "shipments"), ("range", "carrier")),
    _leaf("shipping_region", "shipping", "Bölge dağılımı",
          "Gönderinin hangi şehre gittiği.",
          ("orders",), _RANGE),

    # -------------------------------------------------------------- Mali
    _leaf("finance_vat", "finance", "KDV icmali",
          "Oran bazında matrah ve hesaplanan KDV.",
          ("orders", "lines"), _RANGE),
    _leaf("finance_invoices", "finance", "Fatura listesi",
          "Dönemde kesilen faturalar ve durumları.",
          ("invoices",), _RANGE),
    _leaf("finance_refunds", "finance", "İade/iptal icmali",
          "İade edilen ve iptal edilen tutarlar.",
          ("orders", "refunds"), _RANGE),
    _leaf("finance_pos", "finance", "POS mutabakatı",
          "Banka mutabakat özeti ve fark satırları.",
          ("reconciliation",), _RANGE),
    _leaf("finance_collections", "finance", "Tahsilat özeti",
          "Ödeme işlemleri: yöntem bazında tahsil edilen tutar.",
          ("transactions",), _RANGE),

    # --------------------------------------------------------- Pazarlama
    _leaf("marketing_coupons", "marketing", "Kupon kullanımı",
          "Hangi kupon kaç kez kullanıldı, ne kadar ciro getirdi.",
          ("orders",), _RANGE),
    _leaf("marketing_discount", "marketing", "İndirim maliyeti",
          "Verilen indirimin ciroya oranı.",
          ("orders",), ("range", "granularity")),
    _leaf("marketing_roi", "marketing", "Kampanya ROI",
          "Kampanya maliyeti ve getirisi.", (), _RANGE,
          available=False, reason=_MISSING_ROI),
    _leaf("marketing_trial", "marketing", "Deneme Kulübü dönüşümü",
          "Kulüp üyesinin siparişe dönüşme oranı.",
          ("orders", "trial"), _RANGE),

    # ------------------------------------------------------------ Sistem
    _leaf("system_udit", "system", "UDİT özeti",
          "İşlem kayıtlarının kullanıcı ve işlem tipine dağılımı.",
          ("audit",), _RANGE),
    _leaf("system_backups", "system", "Yedek durumu",
          "Yedek envanteri, boyut ve tazelik.",
          ("backups",), ()),
    _leaf("system_notifications", "system", "Bildirim gönderim özeti",
          "Kanal bazında gönderim, başarısızlık ve maliyet.",
          ("notifications",), _RANGE),
)

BY_KEY: dict[str, dict[str, Any]] = {leaf["key"]: leaf for leaf in LEAVES}

# Veri ihtiyacını `datasets_for()` çözer. Buradaki `NEEDS` eşlemesi hiçbir
# yerden okunmuyordu ve yorumu "servisin ön yükleme kararı" diyerek olmayan
# bir kullanımı anlatıyordu; yanlış yorum, ölü koddan daha pahalıdır.


def leaf(key: str) -> dict[str, Any] | None:
    return BY_KEY.get(str(key or ""))


def label_of(key: str) -> str:
    found = BY_KEY.get(str(key or ""))
    return found["label"] if found else str(key or "")


def branch_label(branch: str) -> str:
    for code, label in BRANCHES:
        if code == branch:
            return label
    return branch


def full_label(key: str) -> str:
    """`Satış · Genel özet` — rapor paketi içindekiler sayfası bunu yazar."""
    found = BY_KEY.get(str(key or ""))
    if not found:
        return str(key or "")
    return f"{branch_label(found['branch'])} · {found['label']}"


def datasets_for(keys: list[str]) -> list[str]:
    """Seçilen yaprakların BİRLEŞİK veri ihtiyacı — her küme bir kez çekilir.

    Rapor paketinde on yaprak seçildiğinde her biri kendi siparişlerini
    çekseydi aynı liste on kez inerdi ve dakikada 60 istek sınırı ilk pakette
    dolardı.
    """
    wanted: list[str] = []
    for key in keys:
        found = BY_KEY.get(str(key or ""))
        if not found:
            continue
        for name in found["needs"]:
            if name not in wanted:
                wanted.append(name)
    return wanted


def tree() -> list[dict[str, Any]]:
    """Panelin çizdiği ağaç: dal → yaprak listesi."""
    out: list[dict[str, Any]] = []
    for code, label in BRANCHES:
        children = [
            {"key": item["key"], "label": item["label"], "hint": item["hint"],
             "params": list(item["params"]), "available": item["available"],
             "reason": item["reason"]}
            for item in LEAVES if item["branch"] == code
        ]
        out.append({"key": code, "label": label, "children": children,
                    "count": len(children)})
    return out


def searchable() -> list[dict[str, str]]:
    """Ağaç aramasının düz listesi — panel `foldText` ile eşler."""
    return [{"key": item["key"], "branch": item["branch"],
             "text": f"{branch_label(item['branch'])} {item['label']} {item['hint']}"}
            for item in LEAVES]
