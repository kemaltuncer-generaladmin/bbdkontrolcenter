"""33 raporun hesabı — saf fonksiyonlar. Ağa çıkmaz, durum tutmaz.

Her yaprak `build(anahtar, veri, parametre)` ile çağrılır ve TEK BİÇİM döner:

    {"kpis": [...], "chart": {...}, "table": {...}, "notes": [...]}

Bu tek biçim üç yeri birden besliyor — ekran, PDF ve CSV. Üç ayrı üretici
yazılsaydı ekranda görülen sayı ile PDF'teki sayı zamanla ayrışırdı; rapor
ekranında bu, güvenin tamamen kaybolması demektir.

YÖNTEM SAKLANMAZ. Bir hesap tahmine dayanıyorsa (iade kalem kırılımı,
"yeni müşteri" penceresi) `notes` içinde açıkça yazar. Kullanıcı rakamın
nereden geldiğini okuyamıyorsa rakam işe yaramaz.
"""

from __future__ import annotations

from typing import Any

from km_sdk import money, number, percent

from . import analytics as an

#: Tablolarda gösterilen en çok satır. PDF'te 400'den fazlası okunmuyor ve
#: reportlab tek tabloyu sayfalara bölerken yavaşlıyor.
ROW_LIMIT = 400


def _kpi(label: str, value: str, *, tone: str = "",
         change: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"label": label, "value": value, "tone": tone, "delta": change}


def _table(headers: list[str], rows: list[list[Any]], *, align: str = "",
           widths: list[float] | None = None, title: str = "") -> dict[str, Any]:
    return {"title": title, "headers": headers, "align": align or "L" * len(headers),
            "widths": widths or [1.0] * len(headers), "rows": rows[:ROW_LIMIT],
            "truncated": len(rows) > ROW_LIMIT}


def _chart(kind: str, data: list[Any], *, title: str = "",
           series: list[str] | None = None) -> dict[str, Any]:
    return {"kind": kind, "title": title, "data": data, "series": series or []}


def _result(kpis: list[dict[str, Any]], *, chart: dict[str, Any] | None = None,
            table: dict[str, Any] | None = None,
            notes: list[str] | None = None) -> dict[str, Any]:
    return {"kpis": kpis, "chart": chart or _chart("", []),
            "table": table or _table([], []), "notes": notes or []}


def _avg(total: int, count: int) -> int:
    return int(total / count) if count else 0


def _shallow_note(rows: list[dict[str, Any]], field: str, what: str) -> list[str]:
    """Sıfır mı, bilinmiyor mu? Ayrımı kaybolursa rapor SESSİZCE yanlış olur.

    CANLIDA DOĞRULANDI: sipariş LİSTESİ ara toplamı, kargo bedelini, indirimi
    ve KDV'yi hiç taşımıyor — bu alanlar yalnız `GET /orders/{id}` detayında
    var. Sığ satırdan gelen 0'ı "kargo tahsil edilmemiş" diye okumak, tahsil
    edilmiş her kuruşu yok saymak olurdu. Rapor bunu sıfır gibi göstermez,
    kaynağın eksikliğini YAZAR.
    """
    if not rows or any(row.get(field) for row in rows):
        return []
    blind = [row for row in rows if row.get("shallow")]
    if not blind:
        return []
    return [(f"{what} sipariş listesinde gelmiyor ({len(blind)} satır sipariş "
             "ayrıntısı olmadan okundu); gösterilen sıfır “yok” değil "
             "“bilinmiyor” demektir.")]


def _bars(rows: list[dict[str, Any]], *, top: int = 12,
          money_display: bool = True) -> list[dict[str, Any]]:
    return [{"label": row["label"], "value": row["total"],
             "display": money(row["total"]) if money_display else number(row["total"])}
            for row in rows[:top]]


def _period(data: dict[str, Any], params: dict[str, Any], name: str = "orders",
            *, only_live: bool = True) -> list[dict[str, Any]]:
    """Dönem satırları — kanal süzgeci ve iptal ayıklaması tek yerde."""
    rows = an.within(data.get(name) or [], params["start"], params["end"])
    channel = an.text(params.get("channel"))
    if channel:
        rows = [row for row in rows if row.get("channel") == channel]
    group = an.text(params.get("customerGroup"))
    if group:
        rows = [row for row in rows if row.get("customerGroup") == group]
    return an.live(rows) if only_live else rows


def _previous(data: dict[str, Any], params: dict[str, Any],
              name: str = "orders") -> list[dict[str, Any]]:
    if not params.get("prevStart"):
        return []
    shadow = {**params, "start": params["prevStart"], "end": params["prevEnd"]}
    return _period(data, shadow, name)


def _lines_of(data: dict[str, Any], params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = an.within(data.get("lines") or [], params["start"], params["end"])
    channel = an.text(params.get("channel"))
    if channel:
        rows = [row for row in rows if row.get("channel") == channel]
    return [row for row in rows if not row.get("void")]


def _product_index(data: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {row["id"]: row for row in (data.get("products") or []) if row.get("id")}


def _category_filter(params: dict[str, Any], index: dict[int, dict[str, Any]],
                     lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kategori süzgeci ürün künyesinden çözülür; kalemde kategori yok."""
    wanted = an.text(params.get("category"))
    if not wanted:
        return lines
    return [row for row in lines
            if wanted in (index.get(row["productId"], {}).get("categories") or [])]


def _group_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kalemleri ürüne toplar: [{productId, sku, label, qty, total}]."""
    bucket: dict[str, dict[str, Any]] = {}
    for row in lines:
        key = str(row.get("productId") or row.get("sku") or row.get("name"))
        slot = bucket.setdefault(key, {
            "productId": row.get("productId", 0), "sku": row.get("sku", ""),
            "label": row.get("name") or row.get("sku") or "—", "qty": 0, "total": 0,
        })
        slot["qty"] += an.as_int(row.get("qty"))
        slot["total"] += an.as_int(row.get("total"))
    return sorted(bucket.values(), key=lambda item: -item["total"])


# ==================================================================== Satış

def _sales_summary(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    raw = _period(data, p, only_live=False)
    before = _previous(data, p)
    revenue = sum(row["grandTotal"] for row in rows)
    prev_revenue = sum(row["grandTotal"] for row in before)
    voided = [row for row in raw if row["void"]]
    refunded = sum(row["refunded"] for row in raw)

    points = an.series(rows, start=p["start"], end=p["end"],
                       granularity=p["granularity"])
    return _result(
        [
            _kpi("Ciro", money(revenue), change=an.delta(revenue, prev_revenue)),
            _kpi("Sipariş", number(len(rows)),
                 change=an.delta(len(rows), len(before))),
            _kpi("Ortalama sepet", money(_avg(revenue, len(rows))),
                 change=an.delta(_avg(revenue, len(rows)),
                                 _avg(prev_revenue, len(before)))),
            _kpi("Kalem", number(sum(row["itemCount"] for row in rows))),
            _kpi("İptal/şüpheli", number(len(voided)), tone="warn" if voided else ""),
            _kpi("İade edilen", money(refunded), tone="bad" if refunded else ""),
        ],
        chart=_chart("line", [{"label": item["label"], "value": item["value"]}
                              for item in points], title="Dönem cirosu"),
        table=_table(["Dönem", "Sipariş", "Ciro", "Ortalama sepet"],
                     [[item["label"], number(item["count"]), money(item["value"]),
                       money(_avg(item["value"], item["count"]))] for item in points],
                     align="LRRR", widths=[1.4, 1, 1.4, 1.4]),
        notes=["İptal, kapanmış ve şüpheli siparişler ciroya dahil edilmez.",
               *_shallow_note(raw, "refunded", "İade edilen tutar")],
    )


def _sales_channel(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    parts = an.breakdown(rows, lambda row: row["channel"], empty="Kanalsız")
    before = {item["label"]: item["total"]
              for item in an.breakdown(_previous(data, p), lambda row: row["channel"])}
    return _result(
        [_kpi("Kanal", number(len(parts))),
         _kpi("Toplam ciro", money(sum(item["total"] for item in parts))),
         _kpi("Sipariş", number(len(rows)))],
        chart=_chart("bar", _bars(parts), title="Kanal bazında ciro"),
        table=_table(["Kanal", "Sipariş", "Ciro", "Pay", "Önceki dönem"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(item["share"]),
                       money(before.get(item["label"], 0)) if before else "—"]
                      for item in parts],
                     align="LRRRR", widths=[2, 1, 1.4, 0.9, 1.4]),
    )


def _sales_payment(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    parts = an.breakdown(rows, lambda row: row["payment"], empty="Yöntemsiz")
    return _result(
        [_kpi("Ödeme yöntemi", number(len(parts))),
         _kpi("Toplam ciro", money(sum(item["total"] for item in parts))),
         _kpi("En çok kullanılan", parts[0]["label"] if parts else "—")],
        chart=_chart("bar", _bars(parts), title="Ödeme yöntemi bazında ciro"),
        table=_table(["Yöntem", "Sipariş", "Ciro", "Pay", "Ortalama sepet"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(item["share"]), money(_avg(item["total"], item["count"]))]
                      for item in parts],
                     align="LRRRR", widths=[2, 1, 1.4, 0.9, 1.4]),
    )


def _sales_hourly(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    hours = [{"hour": hour, "count": 0, "total": 0} for hour in range(24)]
    days = [{"label": name, "count": 0, "total": 0} for name in an.TR_DAYS]
    for row in rows:
        if 0 <= row["hour"] < 24:
            hours[row["hour"]]["count"] += 1
            hours[row["hour"]]["total"] += row["grandTotal"]
        if 0 <= row["weekday"] < 7:
            days[row["weekday"]]["count"] += 1
            days[row["weekday"]]["total"] += row["grandTotal"]

    peak = max(hours, key=lambda item: item["count"]) if rows else {"hour": -1, "count": 0}
    unknown = len([row for row in rows if row["hour"] < 0])
    notes = ["Saati okunamayan sipariş saat şeridine girmez; gün dağılımı etkilenmez."] \
        if unknown else []
    return _result(
        [_kpi("Sipariş", number(len(rows))),
         _kpi("En yoğun saat",
              f"{peak['hour']:02d}:00" if peak["count"] else "—"),
         _kpi("Saati okunamayan", number(unknown), tone="warn" if unknown else "")],
        chart=_chart("hours", [{"hour": item["hour"], "count": item["count"]}
                               for item in hours], title="Saat dağılımı"),
        table=_table(["Gün", "Sipariş", "Ciro", "Ortalama sepet"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       money(_avg(item["total"], item["count"]))] for item in days],
                     align="LRRR", widths=[1.6, 1, 1.4, 1.4]),
        notes=notes,
    )


#: Sepet büyüklüğü kovaları. Sınırlar iş kararıdır ve tabloda görünür.
_BASKET_BUCKETS = ((1, 1, "1 kalem"), (2, 2, "2 kalem"), (3, 5, "3–5 kalem"),
                   (6, 10, "6–10 kalem"), (11, 10_000, "11+ kalem"))


def _sales_basket(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    lines = _lines_of(data, p)
    per_order: dict[int, int] = {}
    for line in lines:
        per_order[line["orderId"]] = per_order.get(line["orderId"], 0) \
            + an.as_int(line.get("qty"))

    buckets = []
    for low, high, label in _BASKET_BUCKETS:
        subset = [row for row in rows
                  if low <= (per_order.get(row["id"]) or row["itemCount"]) <= high]
        buckets.append({"label": label, "count": len(subset),
                        "total": sum(row["grandTotal"] for row in subset)})
    total_orders = len(rows) or 1
    revenue = sum(row["grandTotal"] for row in rows)
    pieces = sum(per_order.values()) or sum(row["itemCount"] for row in rows)

    return _result(
        [_kpi("Sipariş", number(len(rows))),
         _kpi("Ortalama sepet", money(_avg(revenue, len(rows)))),
         _kpi("Sipariş başına kalem",
              number(pieces / len(rows) if rows else 0, 1)),
         _kpi("Kalem başına ciro", money(_avg(revenue, pieces) if pieces else 0))],
        chart=_chart("bar", [{"label": item["label"], "value": item["count"],
                              "display": number(item["count"])} for item in buckets],
                     title="Sepet büyüklüğü dağılımı"),
        table=_table(["Sepet", "Sipariş", "Pay", "Ciro", "Ortalama sepet"],
                     [[item["label"], number(item["count"]),
                       percent(round(item["count"] * 100 / total_orders, 1)),
                       money(item["total"]), money(_avg(item["total"], item["count"]))]
                      for item in buckets],
                     align="LRRRR", widths=[1.6, 1, 0.9, 1.4, 1.4]),
        notes=[("Kalem sayısı sipariş ayrıntısından gelir; ayrıntısı okunamayan "
               "siparişte liste üzerindeki kalem sayacı kullanılır.")],
    )


# ===================================================================== Ürün

def _product_top(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    index = _product_index(data)
    lines = _category_filter(p, index, _lines_of(data, p))
    grouped = _group_lines(lines)
    top = grouped[:20]
    # KUYRUK ÜST LİSTEYLE ÖRTÜŞMEZ. `grouped[-20:]` 25 ürünlük bir dönemde ilk
    # 20'nin 15'ini geri getiriyordu: tablo aynı ürünü İKİ KEZ, iki ayrı sıra
    # numarasıyla yazıyor (25 ürün → 40 satır) ve "en çok satan" ile "en az
    # satan" listeleri birbirine karışıyordu. Kesme noktası 20'nin altına inmez.
    tail = list(reversed(grouped[max(20, len(grouped) - 20):]))
    return _result(
        [_kpi("Satılan ürün çeşidi", number(len(grouped))),
         _kpi("Toplam adet", number(sum(item["qty"] for item in grouped))),
         _kpi("Toplam ciro", money(sum(item["total"] for item in grouped))),
         _kpi("En çok satan", top[0]["label"] if top else "—")],
        chart=_chart("bar", [{"label": item["label"], "value": item["total"],
                              "display": money(item["total"])} for item in top[:12]],
                     title="En çok satan 12 ürün"),
        table=_table(["#", "SKU", "Ürün", "Adet", "Ciro"],
                     [[str(index_no + 1), item["sku"], item["label"],
                       number(item["qty"]), money(item["total"])]
                      for index_no, item in enumerate(top + tail)],
                     align="RLLRR", widths=[0.4, 1.1, 3, 0.8, 1.3]),
        notes=["Tabloda önce ilk 20, sonra son 20 ürün listelenir."] if tail else [],
    )


def _product_pareto(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    grouped = _group_lines(_lines_of(data, p))
    ranked = an.abc(grouped)
    classes = {"A": 0, "B": 0, "C": 0}
    for item in ranked:
        classes[item["abc"]] += 1
    return _result(
        [_kpi("Ürün", number(len(ranked))),
         _kpi("A sınıfı (ciro %80)", number(classes["A"]), tone="good"),
         _kpi("B sınıfı", number(classes["B"])),
         _kpi("C sınıfı", number(classes["C"]), tone="dim")],
        chart=_chart("pareto", [{"name": item["label"], "total": item["total"],
                                 "qty": item["qty"], "share": item["share"],
                                 "cumulativeShare": item["cumulativeShare"],
                                 "abc": item["abc"]} for item in ranked[:15]],
                     title="Pareto — ciro payı"),
        table=_table(["#", "Ürün", "Adet", "Ciro", "Pay", "Kümülatif", "Sınıf"],
                     [[str(no + 1), item["label"], number(item["qty"]),
                       money(item["total"]), percent(item["share"]),
                       percent(item["cumulativeShare"]), item["abc"]]
                      for no, item in enumerate(ranked)],
                     align="RLRRRRC", widths=[0.4, 2.6, 0.7, 1.2, 0.7, 0.9, 0.6]),
        notes=["A sınıfı cironun ilk %80'ini, B %95'e kadarını, C gerisini yapar."],
    )


def _product_turnover(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    index = _product_index(data)
    lines = _category_filter(p, index, _lines_of(data, p))
    sold: dict[int, int] = {}
    for row in lines:
        sold[row["productId"]] = sold.get(row["productId"], 0) + an.as_int(row.get("qty"))

    # SIRALAMA BİÇİMLENDİRMEDEN ÖNCE yapılır: "1.234,5" metnini geri
    # ayrıştırıp sıralamak, ayraç değişince sessizce yanlış sıra üretir.
    scored: list[tuple[float, dict[str, Any]]] = []
    for product in index.values():
        stock = product["stock"]
        qty = sold.get(product["id"], 0)
        if stock <= 0 and qty == 0:
            continue
        ratio = qty / stock if stock > 0 else float(qty)
        scored.append((ratio, {"product": product, "qty": qty, "stock": stock}))
    scored.sort(key=lambda item: -item[0])
    rows = [[item["product"]["sku"], item["product"]["name"], number(item["qty"]),
             number(item["stock"]), number(round(ratio, 2), 2)]
            for ratio, item in scored]
    average = sum(ratio for ratio, _ in scored) / len(scored) if scored else 0
    return _result(
        [_kpi("Ürün", number(len(rows))),
         _kpi("Ortalama devir", number(round(average, 2), 2)),
         _kpi("Dönem", f"{an.days_between(p['start'], p['end'])} gün")],
        table=_table(["SKU", "Ürün", "Satılan", "Stok", "Devir"], rows,
                     align="LLRRR", widths=[1.1, 3, 0.8, 0.8, 0.8]),
        notes=[("Devir = dönemde satılan adet / şu anki stok. Stoku sıfır olan "
               "üründe satılan adet devir yerine geçer; oran sonsuza gitmez.")],
    )


def _product_stockout(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    index = _product_index(data)
    lines = _category_filter(p, index, _lines_of(data, p))
    span = max(1, an.days_between(p["start"], p["end"]))
    horizon = an.as_int(p.get("stockoutDays"), 30)
    sold: dict[int, int] = {}
    for row in lines:
        sold[row["productId"]] = sold.get(row["productId"], 0) + an.as_int(row.get("qty"))

    scored: list[tuple[float, dict[str, Any], float]] = []
    for product in index.values():
        qty = sold.get(product["id"], 0)
        if qty <= 0:
            continue
        rate = qty / span
        left = product["stock"] / rate if rate > 0 else 0
        if left > horizon:
            continue
        scored.append((left, product, rate))
    scored.sort(key=lambda item: item[0])
    critical = len(scored)
    rows = [[product["sku"], product["name"], number(product["stock"]),
             number(round(rate, 2), 2), number(int(left))]
            for left, product, rate in scored]
    return _result(
        [_kpi("Tükenme ufku", f"{horizon} gün"),
         _kpi("Uyarı veren ürün", number(critical), tone="warn" if critical else "good"),
         _kpi("Ölçüm dönemi", f"{span} gün")],
        table=_table(["SKU", "Ürün", "Stok", "Günlük satış", "Kalan gün"], rows,
                     align="LLRRR", widths=[1.1, 3, 0.7, 1, 0.9]),
        notes=[("Tahmin dönemin ORTALAMA hızına dayanır; kampanya ya da sezon "
               "etkisi hesaba katılmaz.")],
    )


def _product_dead(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    index = _product_index(data)
    lines = _category_filter(p, index, _lines_of(data, p))
    sold = {row["productId"] for row in lines}
    span = an.days_between(p["start"], p["end"])

    scored: list[tuple[int, dict[str, Any]]] = []
    locked = 0
    for product in index.values():
        if product["id"] in sold or product["stock"] <= 0:
            continue
        value = product["stock"] * product["price"]
        locked += value
        scored.append((value, product))
    scored.sort(key=lambda item: -item[0])
    rows = [[product["sku"], product["name"], number(product["stock"]),
             money(product["price"]), money(value),
             "Aktif" if product["status"] else "Pasif"] for value, product in scored]
    return _result(
        [_kpi("Ölü stok ürünü", number(len(rows)), tone="warn" if rows else "good"),
         _kpi("Bağlı sermaye", money(locked)),
         _kpi("Ölçüm dönemi", f"{span} gün")],
        table=_table(["SKU", "Ürün", "Stok", "Fiyat", "Bağlı tutar", "Durum"], rows,
                     align="LLRRRC", widths=[1.1, 3, 0.7, 1, 1.2, 0.7]),
        notes=[(f"Ölü stok = bu {span} günlük dönemde HİÇ satılmamış ve stoku olan "
               "ürün. Aralığı genişletmek listeyi kısaltır.")],
    )


def _product_category(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    index = _product_index(data)
    lines = _lines_of(data, p)
    totals: dict[str, dict[str, int]] = {}
    unknown = 0
    for row in lines:
        names = index.get(row["productId"], {}).get("categories") or []
        if not names:
            unknown += an.as_int(row.get("total"))
            names = ["Kategorisiz"]
        # Ürün birden çok kategorideyse ciro BÖLÜNMEZ, her kategoriye tam
        # yazılır: bölmek "kategori cirosu" sorusunun cevabını değiştirirdi.
        for name in names:
            slot = totals.setdefault(name, {"total": 0, "qty": 0})
            slot["total"] += an.as_int(row.get("total"))
            slot["qty"] += an.as_int(row.get("qty"))

    parts = sorted(({"label": name, "total": value["total"], "count": value["qty"]}
                    for name, value in totals.items()),
                   key=lambda item: -item["total"])
    grand = sum(item["total"] for item in parts) or 1
    return _result(
        [_kpi("Kategori", number(len(parts))),
         _kpi("Toplam ciro", money(sum(item["total"] for item in parts))),
         _kpi("Kategorisiz ciro", money(unknown), tone="warn" if unknown else "")],
        chart=_chart("bar", _bars(parts), title="Kategori bazında ciro"),
        table=_table(["Kategori", "Adet", "Ciro", "Pay"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(round(item["total"] * 100 / grand, 1))] for item in parts],
                     align="LRRR", widths=[2.4, 0.8, 1.4, 0.8]),
        notes=[("Bir ürün birden çok kategorideyse cirosu her kategoriye TAM "
               "yazılır; sütun toplamı genel cirodan büyük olabilir.")],
    )


def _product_returns(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    lines = _lines_of(data, p)
    refunds = an.within(data.get("refunds") or [], p["start"], p["end"])
    sold: dict[str, dict[str, Any]] = {}
    for row in lines:
        key = row["sku"] or str(row["productId"])
        slot = sold.setdefault(key, {"label": row["name"] or key, "qty": 0, "returned": 0})
        slot["qty"] += an.as_int(row.get("qty"))

    by_order: dict[int, list[dict[str, Any]]] = {}
    for row in lines:
        by_order.setdefault(row["orderId"], []).append(row)

    estimated = False
    for refund in refunds:
        if refund["skus"]:
            for sku in refund["skus"]:
                slot = sold.setdefault(sku, {"label": sku, "qty": 0, "returned": 0})
                slot["returned"] += 1
            continue
        # İade kalem kırılımı listede yok: siparişin kalemleri iade edilmiş
        # sayılır. TAHMİNDİR ve notta yazar — sessizce kesin rakam gibi
        # göstermek yanlış olurdu.
        estimated = True
        for row in by_order.get(refund["orderId"], []):
            key = row["sku"] or str(row["productId"])
            slot = sold.setdefault(key, {"label": row["name"] or key, "qty": 0,
                                         "returned": 0})
            slot["returned"] += an.as_int(row.get("qty"))

    scored = sorted(
        ((round(item["returned"] * 100 / item["qty"], 1) if item["qty"] else 0.0, key, item)
         for key, item in sold.items() if item["returned"]),
        key=lambda entry: -entry[0])
    rows = [[key, item["label"], number(item["qty"]), number(item["returned"]),
             percent(ratio)] for ratio, key, item in scored]
    notes = ["İade oranı = iade edilen adet / satılan adet."]
    if estimated:
        notes.append("Bazı iadelerde kalem kırılımı yoktu; o iadelerde siparişin "
                     "TÜM kalemleri iade edilmiş sayıldı — oran olduğundan yüksek "
                     "çıkabilir.")
    return _result(
        [_kpi("İade", number(len(refunds))),
         _kpi("İade tutarı", money(sum(row["grandTotal"] for row in refunds))),
         _kpi("Etkilenen ürün", number(len(rows)), tone="warn" if rows else "good")],
        table=_table(["SKU", "Ürün", "Satılan", "İade", "Oran"], rows,
                     align="LLRRR", widths=[1.1, 3, 0.8, 0.8, 0.8]),
        notes=notes,
    )


# ================================================================== Müşteri

def _first_orders(history: list[dict[str, Any]]) -> dict[str, str]:
    first: dict[str, str] = {}
    for row in history:
        key = _customer_key(row)
        if not key:
            continue
        if key not in first or row["day"] < first[key]:
            first[key] = row["day"]
    return first


def _customer_key(row: dict[str, Any]) -> str:
    return str(row.get("customerId") or row.get("customerEmail") or "")


def _customer_new_returning(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    history = an.live(data.get("history") or data.get("orders") or [])
    first = _first_orders(history)
    rows = _period(data, p)

    fresh, repeat = [], []
    for row in rows:
        key = _customer_key(row)
        (fresh if first.get(key) == row["day"] else repeat).append(row)

    buckets = an.fill_buckets(p["start"], p["end"], p["granularity"])
    counts = {key: [0, 0] for key, _ in buckets}
    for row in fresh:
        key, _ = an.bucket(row["day"], p["granularity"])
        if key in counts:
            counts[key][0] += 1
    for row in repeat:
        key, _ = an.bucket(row["day"], p["granularity"])
        if key in counts:
            counts[key][1] += 1

    total = len(rows) or 1
    return _result(
        [_kpi("Sipariş", number(len(rows))),
         _kpi("Yeni müşteri siparişi", number(len(fresh))),
         _kpi("Tekrar eden sipariş", number(len(repeat))),
         _kpi("Tekrar oranı", percent(round(len(repeat) * 100 / total, 1)))],
        chart=_chart("grouped", [{"label": label, "values": counts[key]}
                                 for key, label in buckets],
                     title="Yeni vs tekrar eden", series=["Yeni", "Tekrar eden"]),
        table=_table(["Dönem", "Yeni", "Tekrar eden", "Toplam"],
                     [[label, number(counts[key][0]), number(counts[key][1]),
                       number(counts[key][0] + counts[key][1])] for key, label in buckets],
                     align="LRRR", widths=[1.6, 1, 1, 1]),
        notes=[("“Yeni” ölçüsü çekilen sipariş geçmişine göredir. Geçmiş tavana "
               "dayanmışsa daha eski bir müşteri yeni görünebilir.")],
    )


def _customer_rfm(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    history = an.live(data.get("history") or data.get("orders") or [])
    people: dict[str, dict[str, Any]] = {}
    for row in history:
        key = _customer_key(row)
        if not key:
            continue
        slot = people.setdefault(key, {"name": row["customerName"] or key, "last": "",
                                       "count": 0, "total": 0})
        slot["count"] += 1
        slot["total"] += row["grandTotal"]
        if row["day"] > slot["last"]:
            slot["last"] = row["day"]
            slot["name"] = row["customerName"] or slot["name"]

    segments: dict[str, int] = {}
    scored: list[tuple[int, dict[str, Any], int, str]] = []
    for slot in people.values():
        recency = max(0, an.days_between(slot["last"], p["end"]) - 1)
        segment = an.rfm_segment(recency, slot["count"], slot["total"])
        segments[segment] = segments.get(segment, 0) + 1
        scored.append((slot["total"], slot, recency, segment))
    scored.sort(key=lambda item: -item[0])
    rows = [[slot["name"], slot["last"] or "—", number(recency), number(slot["count"]),
             money(total), segment] for total, slot, recency, segment in scored]
    parts = sorted(({"label": name, "total": count, "count": count}
                    for name, count in segments.items()), key=lambda item: -item["total"])
    return _result(
        [_kpi("Müşteri", number(len(people))),
         _kpi("Şampiyon", number(segments.get("Şampiyon", 0)), tone="good"),
         _kpi("Riskli", number(segments.get("Riskli", 0)), tone="warn"),
         _kpi("Kayıp", number(segments.get("Kayıp", 0)), tone="bad")],
        chart=_chart("bar", [{"label": item["label"], "value": item["total"],
                              "display": number(item["total"])} for item in parts],
                     title="Segment dağılımı"),
        table=_table(["Müşteri", "Son sipariş", "Gün", "Sipariş", "Tutar", "Segment"],
                     rows, align="LLRRRL", widths=[2.4, 1.1, 0.6, 0.7, 1.2, 1]),
        notes=[("Segment eşikleri: Şampiyon ≥3 sipariş · ≤60 gün · ≥1.000 ₺ · "
               "Sadık ≥2 sipariş · ≤90 gün · Yeni ≤30 gün · Riskli ≤180 gün · "
               "gerisi Kayıp.")],
    )


def _customer_top(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _customer_key(row)
        if not key:
            continue
        slot = people.setdefault(key, {"name": row["customerName"] or key,
                                       "group": row["customerGroup"], "count": 0,
                                       "total": 0})
        slot["count"] += 1
        slot["total"] += row["grandTotal"]
    ordered = sorted(people.values(), key=lambda item: -item["total"])[:30]
    revenue = sum(row["grandTotal"] for row in rows) or 1
    top_share = sum(item["total"] for item in ordered) * 100 / revenue
    return _result(
        [_kpi("Müşteri", number(len(people))),
         _kpi("İlk 30'un payı", percent(round(top_share, 1))),
         _kpi("Ortalama müşteri cirosu", money(_avg(revenue, len(people) or 1)))],
        chart=_chart("bar", [{"label": item["name"], "value": item["total"],
                              "display": money(item["total"])} for item in ordered[:12]],
                     title="En çok harcayan 12 müşteri"),
        table=_table(["#", "Müşteri", "Grup", "Sipariş", "Tutar", "Ortalama"],
                     [[str(no + 1), item["name"], item["group"] or "—",
                       number(item["count"]), money(item["total"]),
                       money(_avg(item["total"], item["count"]))]
                      for no, item in enumerate(ordered)],
                     align="RLLRRR", widths=[0.4, 2.4, 1, 0.7, 1.2, 1.2]),
    )


def _customer_city(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    parts = an.breakdown(rows, lambda row: row["city"], empty="Şehirsiz")
    return _result(
        [_kpi("Şehir", number(len(parts))),
         _kpi("En çok sipariş", parts[0]["label"] if parts else "—"),
         _kpi("Toplam ciro", money(sum(item["total"] for item in parts)))],
        chart=_chart("bar", _bars(parts, top=15), title="Şehir bazında ciro"),
        table=_table(["Şehir", "Sipariş", "Ciro", "Pay", "Ortalama sepet"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(item["share"]), money(_avg(item["total"], item["count"]))]
                      for item in parts],
                     align="LRRRR", widths=[2, 1, 1.4, 0.8, 1.3]),
    )


def _customer_group(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    parts = an.breakdown(rows, lambda row: row["customerGroup"], empty="Grupsuz")
    return _result(
        [_kpi("Grup", number(len(parts))),
         _kpi("Toplam ciro", money(sum(item["total"] for item in parts))),
         _kpi("Sipariş", number(len(rows)))],
        chart=_chart("stacked", [{"label": item["label"], "value": item["count"]}
                                 for item in parts], title="Grup bazında sipariş"),
        table=_table(["Grup", "Sipariş", "Ciro", "Pay", "Ortalama sepet"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(item["share"]), money(_avg(item["total"], item["count"]))]
                      for item in parts],
                     align="LRRRR", widths=[2, 1, 1.4, 0.8, 1.3]),
    )


def _customer_churn(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    history = an.live(data.get("history") or data.get("orders") or [])
    limit = an.as_int(p.get("churnDays"), 180)
    people: dict[str, dict[str, Any]] = {}
    for row in history:
        key = _customer_key(row)
        if not key:
            continue
        slot = people.setdefault(key, {"name": row["customerName"] or key, "last": "",
                                       "count": 0, "total": 0})
        slot["count"] += 1
        slot["total"] += row["grandTotal"]
        if row["day"] > slot["last"]:
            slot["last"] = row["day"]
            slot["name"] = row["customerName"] or slot["name"]

    scored: list[tuple[int, dict[str, Any], int]] = []
    value = 0
    for slot in people.values():
        gap = max(0, an.days_between(slot["last"], p["end"]) - 1)
        if gap < limit:
            continue
        value += slot["total"]
        scored.append((slot["total"], slot, gap))
    scored.sort(key=lambda item: -item[0])
    lost = [[slot["name"], slot["last"] or "—", number(gap), number(slot["count"]),
             money(total)] for total, slot, gap in scored]
    return _result(
        [_kpi("Eşik", f"{limit} gün"),
         _kpi("Kayıp müşteri", number(len(lost)), tone="warn" if lost else "good"),
         _kpi("Geçmiş cirosu", money(value)),
         _kpi("Toplam müşteri", number(len(people)))],
        table=_table(["Müşteri", "Son sipariş", "Geçen gün", "Sipariş", "Geçmiş ciro"],
                     lost, align="LLRRR", widths=[2.4, 1.1, 0.9, 0.8, 1.3]),
        notes=[(f"Kayıp = son siparişinin üzerinden {limit} günden fazla geçmiş "
               "müşteri. Eşik modül ayarından değiştirilir.")],
    )


# ==================================================================== Kargo

def _shipments_of(data: dict[str, Any], p: dict[str, Any]) -> list[dict[str, Any]]:
    rows = an.within(data.get("shipments") or [], p["start"], p["end"])
    carrier = an.text(p.get("carrier"))
    return [row for row in rows if row["carrier"] == carrier] if carrier else rows


def _shipping_carrier(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _shipments_of(data, p)
    carriers: dict[str, dict[str, Any]] = {}
    for row in rows:
        slot = carriers.setdefault(row["carrier"] or "Belirtilmemiş",
                                   {"count": 0, "delivered": 0, "days": 0, "cost": 0})
        slot["count"] += 1
        slot["cost"] += row["cost"]
        if row["delivered"]:
            slot["delivered"] += 1
            slot["days"] += row["deliveryDays"]

    table = [[name, number(slot["count"]), number(slot["delivered"]),
              percent(round(slot["delivered"] * 100 / slot["count"], 1)),
              number(round(slot["days"] / slot["delivered"], 1), 1)
              if slot["delivered"] else "—", money(slot["cost"])]
             for name, slot in sorted(carriers.items(), key=lambda item: -item[1]["count"])]
    delivered = sum(slot["delivered"] for slot in carriers.values())
    days = sum(slot["days"] for slot in carriers.values())
    return _result(
        [_kpi("Gönderi", number(len(rows))),
         _kpi("Teslim edilen", number(delivered)),
         _kpi("Teslim oranı",
              percent(round(delivered * 100 / len(rows), 1) if rows else 0)),
         _kpi("Ortalama teslim",
              f"{round(days / delivered, 1)} gün" if delivered else "—")],
        chart=_chart("bar", [{"label": name, "value": slot["count"],
                              "display": number(slot["count"])}
                             for name, slot in carriers.items()],
                     title="Taşıyıcı bazında gönderi"),
        table=_table(["Taşıyıcı", "Gönderi", "Teslim", "Oran", "Ort. gün", "Ücret"],
                     table, align="LRRRRR", widths=[1.8, 0.9, 0.9, 0.8, 0.9, 1.2]),
    )


def _shipping_undelivered(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in _shipments_of(data, p) if not row["delivered"]]
    waiting = an.breakdown(rows, lambda row: row["status"] or "Durumsuz",
                           value="cost")
    table = [[row["day"], str(row["orderId"] or "—"), row["carrier"] or "—",
              row["tracking"] or "—", row["status"] or "—",
              number(max(0, an.days_between(row["day"], p["end"]) - 1))]
             for row in sorted(rows, key=lambda item: item["day"])]
    return _result(
        [_kpi("Teslim edilmeyen", number(len(rows)), tone="warn" if rows else "good"),
         _kpi("Durum çeşidi", number(len(waiting))),
         _kpi("Bağlı kargo ücreti", money(sum(row["cost"] for row in rows)))],
        chart=_chart("bar", [{"label": item["label"], "value": item["count"],
                              "display": number(item["count"])} for item in waiting],
                     title="Durum dağılımı"),
        table=_table(["Tarih", "Sipariş", "Taşıyıcı", "Takip no", "Durum", "Bekleyen gün"],
                     table, align="LLLLLR", widths=[1, 0.9, 1.2, 1.6, 1.2, 1]),
    )


def _shipping_pnl(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    orders = _period(data, p)
    shipments = _shipments_of(data, p)
    collected = sum(row["shipping"] for row in orders)
    paid = sum(row["cost"] for row in shipments)
    result = collected - paid
    carriers: dict[str, int] = {}
    for row in shipments:
        carriers[row["carrier"] or "Belirtilmemiş"] = \
            carriers.get(row["carrier"] or "Belirtilmemiş", 0) + row["cost"]
    return _result(
        [_kpi("Tahsil edilen kargo", money(collected)),
         _kpi("Taşıyıcıya ödenen", money(paid)),
         _kpi("Fark", money(result), tone="good" if result >= 0 else "bad"),
         _kpi("Gönderi", number(len(shipments)))],
        chart=_chart("bar", [{"label": name, "value": value, "display": money(value)}
                             for name, value in sorted(carriers.items(),
                                                       key=lambda item: -item[1])],
                     title="Taşıyıcıya ödenen"),
        table=_table(["Kalem", "Tutar"],
                     [["Müşteriden tahsil edilen kargo", money(collected)],
                      ["Taşıyıcıya ödenen ücret", money(paid)],
                      ["Fark (kâr/zarar)", money(result)]],
                     align="LR", widths=[3, 1.2]),
        notes=[("Ücretsiz kargo kampanyası tahsilatı sıfırlar; fark eksiye "
               "düşüyorsa kampanyanın maliyeti budur."),
               *_shallow_note(orders, "shipping", "Müşteriden tahsil edilen kargo bedeli")],
    )


def _shipping_region(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    parts = an.breakdown(rows, lambda row: row["city"], value="shipping",
                         empty="Şehirsiz")
    counts = an.breakdown(rows, lambda row: row["city"], empty="Şehirsiz")
    order_count = {item["label"]: item["count"] for item in counts}
    return _result(
        [_kpi("Şehir", number(len(parts))),
         _kpi("Toplam kargo ücreti", money(sum(item["total"] for item in parts))),
         _kpi("Gönderilen sipariş", number(len(rows)))],
        chart=_chart("bar", _bars(parts, top=15), title="Şehir bazında kargo ücreti"),
        table=_table(["Şehir", "Sipariş", "Kargo ücreti", "Sipariş başına"],
                     [[item["label"], number(order_count.get(item["label"], 0)),
                       money(item["total"]),
                       money(_avg(item["total"], order_count.get(item["label"], 0)))]
                      for item in parts],
                     align="LRRR", widths=[2, 1, 1.4, 1.4]),
        notes=_shallow_note(rows, "shipping", "Kargo ücreti"),
    )


# ===================================================================== Mali

def _finance_vat(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    lines = _lines_of(data, p)
    rates: dict[str, dict[str, int]] = {}
    for row in lines:
        key = f"%{row['taxPercent']:g}" if row["taxPercent"] else "KDV'siz"
        slot = rates.setdefault(key, {"base": 0, "tax": 0, "count": 0})
        slot["base"] += an.as_int(row.get("total"))
        slot["tax"] += an.as_int(row.get("tax"))
        slot["count"] += 1
    orders = _period(data, p)
    order_tax = sum(row["tax"] for row in orders)
    line_tax = sum(slot["tax"] for slot in rates.values())
    notes = [("Matrah kalem toplamıdır; kargo ve sipariş geneli indirimi ayrı satır "
             "olarak gelmiyorsa oran kırılımına girmez.")]
    if orders and abs(order_tax - line_tax) > 100:
        notes.append(f"Sipariş başlıklarındaki KDV toplamı {money(order_tax)}, kalem "
                     f"toplamı {money(line_tax)}. Fark kargo/indirim KDV'sinden "
                     "gelir; icmalde kalem toplamı esas alınmıştır.")
    return _result(
        [_kpi("Matrah", money(sum(slot["base"] for slot in rates.values()))),
         _kpi("Hesaplanan KDV", money(line_tax)),
         _kpi("Oran çeşidi", number(len(rates))),
         _kpi("Kalem", number(len(lines)))],
        chart=_chart("bar", [{"label": key, "value": slot["tax"],
                              "display": money(slot["tax"])}
                             for key, slot in sorted(rates.items(),
                                                     key=lambda item: -item[1]["tax"])],
                     title="Oran bazında KDV"),
        table=_table(["Oran", "Kalem", "Matrah", "KDV"],
                     [[key, number(slot["count"]), money(slot["base"]), money(slot["tax"])]
                      for key, slot in sorted(rates.items(),
                                              key=lambda item: -item[1]["tax"])],
                     align="LRRR", widths=[1, 1, 1.6, 1.6]),
        notes=notes,
    )


def _finance_invoices(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = an.within(data.get("invoices") or [], p["start"], p["end"])
    parts = an.breakdown(rows, lambda row: row["state"] or "Durumsuz")
    return _result(
        [_kpi("Fatura", number(len(rows))),
         _kpi("Toplam tutar", money(sum(row["grandTotal"] for row in rows))),
         _kpi("KDV", money(sum(row["tax"] for row in rows))),
         _kpi("Ortalama", money(_avg(sum(row["grandTotal"] for row in rows), len(rows))))],
        chart=_chart("bar", [{"label": item["label"], "value": item["count"],
                              "display": number(item["count"])} for item in parts],
                     title="Durum dağılımı"),
        table=_table(["Tarih", "Fatura no", "Sipariş", "Durum", "Matrah", "KDV", "Toplam"],
                     [[row["day"], row["number"], str(row["orderId"] or "—"),
                       row["state"] or "—", money(row["subTotal"]), money(row["tax"]),
                       money(row["grandTotal"])]
                      for row in sorted(rows, key=lambda item: item["day"])],
                     align="LLLLRRR", widths=[1, 1.2, 0.9, 1, 1.2, 1, 1.2]),
    )


def _finance_refunds(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    refunds = an.within(data.get("refunds") or [], p["start"], p["end"])
    raw = _period(data, p, only_live=False)
    canceled = [row for row in raw if row["void"]]
    revenue = sum(row["grandTotal"] for row in an.live(raw)) or 1
    refunded = sum(row["grandTotal"] for row in refunds)
    return _result(
        [_kpi("İade", number(len(refunds))),
         _kpi("İade tutarı", money(refunded), tone="bad" if refunded else ""),
         _kpi("İptal edilen sipariş", number(len(canceled))),
         _kpi("İade oranı", percent(round(refunded * 100 / revenue, 1)))],
        chart=_chart("bar", [{"label": "İade", "value": refunded, "display": money(refunded)},
                             {"label": "İptal",
                              "value": sum(row["grandTotal"] for row in canceled),
                              "display": money(sum(row["grandTotal"] for row in canceled))}],
                     title="İade ve iptal tutarı"),
        table=_table(["Tarih", "Tür", "Sipariş", "Tutar"],
                     [[row["day"], "İade", str(row["orderId"] or "—"),
                       money(row["grandTotal"])] for row in refunds]
                     + [[row["day"], row["statusLabel"], row["number"],
                         money(row["grandTotal"])] for row in canceled],
                     align="LLLR", widths=[1, 1, 1.2, 1.4]),
        notes=[("İade oranı, iade tutarının aynı dönemin (iptaller hariç) cirosuna "
               "oranıdır.")],
    )


def _finance_pos(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("reconciliation") or {}
    if not isinstance(payload, dict):
        payload = {}
    rows = payload.get("rows") or payload.get("items") or []
    kpis: list[dict[str, Any]] = []
    for label, key in (("Mutabık", "matched"), ("Farklı", "mismatched"),
                       ("Eksik", "missing"), ("Fazla", "extra")):
        if key in payload:
            kpis.append(_kpi(label, number(an.as_int(payload.get(key))),
                             tone="bad" if key != "matched" and payload.get(key) else ""))
    if "total" in payload or "amount" in payload:
        kpis.append(_kpi("Toplam tutar",
                         money(an.to_kurus(an.pick(payload, "total", "amount", default=0)))))
    if not kpis:
        kpis = [_kpi("Mutabakat satırı", number(len(rows) if isinstance(rows, list) else 0))]

    table_rows: list[list[Any]] = []
    if isinstance(rows, list):
        for row in rows[:ROW_LIMIT]:
            if not isinstance(row, dict):
                continue
            table_rows.append([
                an.day_of(an.pick(row, "date", "created_at", "day", default="")) or "—",
                an.text(an.pick(row, "reference", "order_id", "orderId", default="")) or "—",
                an.text(an.pick(row, "status", "state", default="")) or "—",
                money(an.to_kurus(an.pick(row, "store_amount", "amount", default=0))),
                money(an.to_kurus(an.pick(row, "bank_amount", "bankAmount", default=0))),
                money(an.to_kurus(an.pick(row, "difference", "diff", default=0))),
            ])
    return _result(
        kpis,
        table=_table(["Tarih", "Referans", "Durum", "Mağaza", "Banka", "Fark"],
                     table_rows, align="LLLRRR", widths=[1, 1.4, 1, 1.2, 1.2, 1.2]),
        notes=[("Mutabakat rakamları mağaza tarafındaki uçtan OLDUĞU GİBİ alınır; "
               "burada yeniden hesaplanmaz.")],
    )


def _finance_collections(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = an.within(data.get("transactions") or [], p["start"], p["end"])
    parts = an.breakdown(rows, lambda row: row["method"] or "Yöntemsiz")
    return _result(
        [_kpi("İşlem", number(len(rows))),
         _kpi("Tahsil edilen", money(sum(row["grandTotal"] for row in rows))),
         _kpi("Yöntem", number(len(parts)))],
        chart=_chart("bar", _bars(parts), title="Yöntem bazında tahsilat"),
        table=_table(["Yöntem", "İşlem", "Tutar", "Pay", "Ortalama"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(item["share"]), money(_avg(item["total"], item["count"]))]
                      for item in parts],
                     align="LRRRR", widths=[2, 1, 1.4, 0.8, 1.3]),
    )


# =============================================================== Pazarlama

def _marketing_coupons(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in _period(data, p) if row["coupon"]]
    parts = an.breakdown(rows, lambda row: row["coupon"])
    discount = sum(row["discount"] for row in rows)
    revenue = sum(row["grandTotal"] for row in rows)
    return _result(
        [_kpi("Kuponlu sipariş", number(len(rows))),
         _kpi("Kupon çeşidi", number(len(parts))),
         _kpi("Kuponlu ciro", money(revenue)),
         _kpi("Verilen indirim", money(discount), tone="warn" if discount else "")],
        chart=_chart("bar", _bars(parts), title="Kupon bazında ciro"),
        table=_table(["Kupon", "Sipariş", "Ciro", "Pay", "Ortalama sepet"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(item["share"]), money(_avg(item["total"], item["count"]))]
                      for item in parts],
                     align="LRRRR", widths=[1.6, 1, 1.4, 0.8, 1.3]),
    )


def _marketing_discount(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = _period(data, p)
    revenue = sum(row["grandTotal"] for row in rows)
    discount = sum(row["discount"] for row in rows)
    gross = revenue + discount or 1
    points = an.series(rows, start=p["start"], end=p["end"],
                       granularity=p["granularity"], value="discount")
    sales = {item["key"]: item["value"]
             for item in an.series(rows, start=p["start"], end=p["end"],
                                   granularity=p["granularity"])}
    return _result(
        [_kpi("Verilen indirim", money(discount)),
         _kpi("Net ciro", money(revenue)),
         _kpi("İndirim oranı", percent(round(discount * 100 / gross, 1)),
              tone="warn" if discount * 100 / gross > 20 else ""),
         _kpi("İndirimli sipariş",
              number(len([row for row in rows if row["discount"]])))],
        chart=_chart("line", [{"label": item["label"], "value": item["value"]}
                              for item in points], title="Dönem indirimi"),
        table=_table(["Dönem", "Ciro", "İndirim", "Oran"],
                     [[item["label"], money(sales.get(item["key"], 0)),
                       money(item["value"]),
                       percent(round(item["value"] * 100
                                     / (sales.get(item["key"], 0) + item["value"]), 1)
                               if sales.get(item["key"], 0) + item["value"] else 0)]
                      for item in points],
                     align="LRRR", widths=[1.4, 1.4, 1.4, 0.9]),
        notes=["İndirim oranı, indirimin BRÜT ciroya (net ciro + indirim) oranıdır.",
               *_shallow_note(rows, "discount", "Sipariş indirimi")],
    )


def _marketing_trial(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    members = data.get("trial") or []
    rows = _period(data, p)
    by_id = {str(row["customerId"]) for row in rows if row["customerId"]}
    by_mail = {row["customerEmail"].lower() for row in rows if row["customerEmail"]}

    converted = [member for member in members
                 if (member["customerId"] and str(member["customerId"]) in by_id)
                 or (member["email"] and member["email"] in by_mail)]
    spend = 0
    for row in rows:
        key_id = str(row["customerId"])
        if any((member["customerId"] and str(member["customerId"]) == key_id)
               or (member["email"] and member["email"] == row["customerEmail"].lower())
               for member in members):
            spend += row["grandTotal"]
    total = len(members) or 1
    return _result(
        [_kpi("Kulüp üyesi", number(len(members))),
         _kpi("Sipariş veren", number(len(converted))),
         _kpi("Dönüşüm", percent(round(len(converted) * 100 / total, 1))),
         _kpi("Üye cirosu", money(spend))],
        chart=_chart("stacked",
                     [{"label": "Sipariş veren", "value": len(converted)},
                      {"label": "Vermeyen", "value": max(0, len(members) - len(converted))}],
                     title="Üye dönüşümü"),
        table=_table(["Ölçü", "Değer"],
                     [["Kulüp üyesi", number(len(members))],
                      ["Dönemde sipariş veren üye", number(len(converted))],
                      ["Dönüşüm oranı", percent(round(len(converted) * 100 / total, 1))],
                      ["Üyelerin dönem cirosu", money(spend)]],
                     align="LR", widths=[3, 1.2]),
        notes=[("Eşleştirme müşteri numarası, olmazsa e-posta ile yapılır; ikisi de "
               "yoksa üye dönüşmemiş sayılır.")],
    )


# ================================================================== Sistem

def _system_udit(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = an.within(data.get("audit") or [], p["start"], p["end"])
    by_user = an.breakdown(rows, lambda row: row["user"])
    by_action = an.breakdown(rows, lambda row: row["action"])
    failed = [row for row in rows if row["result"].lower() in ("failed", "error", "denied")]
    return _result(
        [_kpi("Kayıt", number(len(rows))),
         _kpi("Kullanıcı", number(len(by_user))),
         _kpi("İşlem tipi", number(len(by_action))),
         _kpi("Başarısız", number(len(failed)), tone="warn" if failed else "good")],
        chart=_chart("bar", [{"label": item["label"], "value": item["count"],
                              "display": number(item["count"])} for item in by_action],
                     title="İşlem tipi dağılımı"),
        table=_table(["Kullanıcı", "İşlem sayısı", "Pay"],
                     [[item["label"], number(item["count"]),
                       percent(round(item["count"] * 100 / (len(rows) or 1), 1))]
                      for item in by_user],
                     align="LRR", widths=[2.4, 1, 0.9]),
        notes=[("Bu ekran UDİT kaydını ÖZETLER; kayıt ayrıntısı ve öncesi/sonrası "
               "farkı UDİT İşlem Kayıtları ekranındadır.")],
    )


def _system_backups(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = sorted(data.get("backups") or [], key=lambda item: item["day"], reverse=True)
    newest = rows[0] if rows else None
    age = an.days_between(newest["day"], p["end"]) - 1 if newest and newest["day"] else -1
    verified = len([row for row in rows if row["verified"]])
    return _result(
        [_kpi("Yedek", number(len(rows))),
         _kpi("En yeni yedek", newest["day"] if newest else "—",
              tone="bad" if age < 0 or age > 2 else "good"),
         _kpi("Yaş", f"{age} gün" if age >= 0 else "—",
              tone="warn" if age > 2 else ""),
         _kpi("Doğrulanmış", number(verified))],
        table=_table(["Tarih", "Ad", "Kapsam", "Boyut", "Doğrulama"],
                     [[row["day"] or "—", row["name"], row["scope"] or "—",
                       number(round(row["bytes"] / 1_048_576, 1), 1) + " MB",
                       "Evet" if row["verified"] else "Hayır"] for row in rows],
                     align="LLLRC", widths=[1, 2.4, 1.2, 1, 1]),
        notes=[("Yedek envanteri mağaza tarafındaki uçtan gelir; Kontrol "
               "Merkezi'nin kendi yedekleri bu listede yoktur.")],
    )


def _system_notifications(data: dict[str, Any], p: dict[str, Any]) -> dict[str, Any]:
    rows = an.within(data.get("notifications") or [], p["start"], p["end"])
    by_channel = an.breakdown(rows, lambda row: row["channel"])
    failed = [row for row in rows
              if row["status"].lower() in ("failed", "error", "rejected", "bounced")]
    cost = sum(row["grandTotal"] for row in rows)
    return _result(
        [_kpi("Gönderim", number(len(rows))),
         _kpi("Başarısız", number(len(failed)), tone="warn" if failed else "good"),
         _kpi("Kanal", number(len(by_channel))),
         _kpi("Maliyet", money(cost))],
        chart=_chart("bar", [{"label": item["label"], "value": item["count"],
                              "display": number(item["count"])} for item in by_channel],
                     title="Kanal bazında gönderim"),
        table=_table(["Kanal", "Gönderim", "Maliyet", "Pay"],
                     [[item["label"], number(item["count"]), money(item["total"]),
                       percent(round(item["count"] * 100 / (len(rows) or 1), 1))]
                      for item in by_channel],
                     align="LRRR", widths=[1.6, 1, 1.3, 0.9]),
    )


#: Yaprak → hesap. Ağaçtaki her `available` yaprağın burada karşılığı OLMAK
#: ZORUNDA; `service.run` eşleşme bulamazsa yaprağı hataya düşürür ve
#: diğerleri çalışmaya devam eder.
BUILDERS = {
    "sales_summary": _sales_summary,
    "sales_channel": _sales_channel,
    "sales_payment": _sales_payment,
    "sales_hourly": _sales_hourly,
    "sales_basket": _sales_basket,
    "product_top": _product_top,
    "product_pareto": _product_pareto,
    "product_turnover": _product_turnover,
    "product_stockout": _product_stockout,
    "product_dead": _product_dead,
    "product_category": _product_category,
    "product_returns": _product_returns,
    "customer_new_returning": _customer_new_returning,
    "customer_rfm": _customer_rfm,
    "customer_top": _customer_top,
    "customer_city": _customer_city,
    "customer_group": _customer_group,
    "customer_churn": _customer_churn,
    "shipping_carrier": _shipping_carrier,
    "shipping_undelivered": _shipping_undelivered,
    "shipping_pnl": _shipping_pnl,
    "shipping_region": _shipping_region,
    "finance_vat": _finance_vat,
    "finance_invoices": _finance_invoices,
    "finance_refunds": _finance_refunds,
    "finance_pos": _finance_pos,
    "finance_collections": _finance_collections,
    "marketing_coupons": _marketing_coupons,
    "marketing_discount": _marketing_discount,
    "marketing_trial": _marketing_trial,
    "system_udit": _system_udit,
    "system_backups": _system_backups,
    "system_notifications": _system_notifications,
}


def build(key: str, data: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Tek yaprağın hesabı. Bilinmeyen anahtar `KeyError` yerine hata döndürür."""
    builder = BUILDERS.get(str(key or ""))
    if builder is None:
        return _result([], notes=[f"Bu rapor için hesap tanımlı değil: {key}"])
    return builder(data, params)


def pdf_sections(result: dict[str, Any], *, title: str = "") -> list[dict[str, Any]]:
    """Ekran sonucunu `build_pdf` bölümlerine çevirir — TEK KAYNAK.

    Ekranda görülen sayı ile kâğıttaki sayı burada aynı yerden üretilir.
    """
    sections: list[dict[str, Any]] = []
    if result.get("kpis"):
        sections.append({
            "kind": "tiles", "title": title or "Özet",
            "tiles": [(item["label"], item["value"]) for item in result["kpis"]],
        })
    chart = result.get("chart") or {}
    if chart.get("kind") in ("bar", "stacked") and chart.get("data"):
        sections.append({
            "kind": "bars", "title": chart.get("title") or "",
            "bars": [(item.get("label", ""), an.as_int(item.get("value")),
                      item.get("display") or number(item.get("value")))
                     for item in chart["data"]],
        })
    table = result.get("table") or {}
    if table.get("headers") and table.get("rows"):
        sections.append({
            "kind": "table", "title": table.get("title") or "Ayrıntı",
            "headers": table["headers"], "rows": table["rows"],
            "align": table.get("align") or "", "widths": table.get("widths") or [],
        })
    for note in result.get("notes") or []:
        sections.append({"kind": "note", "text": note})
    return sections
