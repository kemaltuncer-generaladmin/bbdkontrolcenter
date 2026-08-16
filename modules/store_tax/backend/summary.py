"""KDV icmali hesabı — saf mantık, ağa çıkmaz, testin hedefi.

`accountant` rolünün ANA EKRANINI besleyen dosya budur: rakamlar buradan
çıkıyor ve mali müşavire gidiyor. Bu yüzden burada üç kural var ve hiçbiri
esnetilmez:

 1. PARA HER YERDE KURUŞ (int). Bölme yalnız oran türetirken yapılır ve
    sonucu tutara dönmez.
 2. KAYNAK BELGE FATURADIR, sipariş değil. Sipariş "satış vaadi", fatura
    "vergi doğuran olay"dır. Faturasız sipariş beyana girmez; girseydi henüz
    doğmamış vergiyi beyan etmiş olurduk.
 3. TUTMAYAN KURUŞ SAKLANMAZ. Kalem toplamları belgenin KDV'sini tutmuyorsa
    fark `unresolved` kovasına yazılır ve ekranda görünür. Sessizce en yakın
    orana eklemek, tutmayan bir beyanı tutuyor gibi göstermektir.

BAGISTO ALAN ADLARI. Tutarlar hem mağaza para biriminde (`subTotal`) hem de
taban para biriminde (`baseSubTotal`) geliyor. Beyan TABAN para birimine göre
yapılır; `base` öneki tercih edilir, yoksa öneksiz alan kullanılır.

ALAN ADLARI camelCase'TİR — canlıda doğrulandı (2026-08-13):

    GET /api/admin/invoices → {"createdAt":"2026-08-13 18:27:20",
                               "baseSubTotal":2, "baseTaxAmount":0,
                               "baseGrandTotal":2, "channelName":"…",
                               "incrementId":"18", "items":[]}

snake_case okuyan kod hiçbir alanı bulamaz: tarih boş kalır, belge aralık
dışı sayılır ve KDV icmali SESSİZCE BOŞ çıkar. Bu yüzden okuma iki adı da
dener (`taxes.pick`). `items` LİSTE ucunda boş gelir; kalem kırılımı yalnız
tekil uçta vardır, bu yüzden oran belge toplamından türetilir.
"""

from __future__ import annotations

from typing import Any

from .taxes import as_int, camel, pick, rate_key, rate_label, rate_value, text, to_kurus

#: Türetilen efektif oranın bilinen bir orana "aynı" sayılması için izin
#: verilen sapma (yüzde puanı). 0,05 puan bir belge boyunca en çok birkaç
#: kuruşluk yuvarlama farkına karşılık gelir; daha geniş bir tolerans %8 ile
#: %8,5'i birbirine karıştırırdı.
SNAP_TOLERANCE = 0.05

#: Kalem kırılımı gelmeyen belgede oran türetmek için gereken en küçük matrah.
#: Çok küçük matrahlarda kuruş yuvarlaması efektif oranı sallar.
MIN_DERIVE_BASE = 100


def _keys(name: str) -> tuple[str, ...]:
    """Bir tutarın aranacağı dört ad: taban/normal × snake/camel."""
    return (f"base_{name}", camel(f"base_{name}"), name, camel(name))


def money_of(raw: dict[str, Any], *names: str) -> int:
    """Tutarı kuruş olarak okur; taban para birimi alanı öncelikli.

    Bulunamayan alan 0 döner — burada `None` taşımanın anlamı yok: toplama
    girmeyen tutar sıfırdır ve eksik alanın kendisi `money_known` üzerinden
    ayrıca sorulabilir.
    """
    if not isinstance(raw, dict):
        return 0
    for name in names:
        for key in _keys(name):
            if key in raw and raw[key] not in (None, ""):
                value = to_kurus(raw[key])
                if value is not None:
                    return value
    return 0


def money_known(raw: dict[str, Any], *names: str) -> bool:
    """Tutar alanı yanıtta VAR MI?

    "KDV alanı yok" ile "KDV sıfır" ayrı şeylerdir: ilki okunamadı demektir ve
    `unresolved` kovasına düşer, ikincisi mağazanın beyanıdır ve %0 satırına
    yazılır. İkisini birleştirmek ya muaf satışı şüpheli gösterir ya da
    okunamayan belgeyi «vergisiz» diye beyana sokar.
    """
    if not isinstance(raw, dict):
        return False
    return any(key in raw and raw[key] is not None for name in names for key in _keys(name))


def day_of(raw: dict[str, Any]) -> str:
    """Belgenin takvim günü (YYYY-MM-DD)."""
    return text(pick(raw, "created_at"))[:10]


def channel_of(raw: dict[str, Any]) -> str:
    """Belgenin satış kanalı. Fatura kanalı taşımıyorsa siparişinkine bakılır."""
    direct = text(pick(raw, "channel_name", "channel"))
    if direct:
        return direct
    order = raw.get("order")
    if isinstance(order, dict):
        found = text(pick(order, "channel_name", "channel"))
        if found:
            return found
    return "Belirtilmemiş"


def line_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Belgenin kalemleri → {percent, base, tax}.

    Matrah = kalem toplamı − kalem indirimi. Bagisto indirimi kalem üzerinde
    tutuyor ve KDV'yi indirimden SONRAKİ tutar üzerinden hesaplıyor; indirimi
    düşmemek matrahı şişirir.
    """
    items = raw.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        percent = rate_value(pick(item, "tax_percent", "tax_rate"))
        base = money_of(item, "total") - money_of(item, "discount_amount")
        out.append({
            "percent": percent,
            "base": base,
            "tax": money_of(item, "tax_amount"),
            "taxKnown": money_known(item, "tax_amount"),
        })
    return out


def doc_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Fatura ya da iade kaydı → icmalin çalıştığı düz belge."""
    lines = line_rows(raw)
    tax = money_of(raw, "tax_amount")
    base = money_of(raw, "sub_total") - money_of(raw, "discount_amount")
    return {
        "id": as_int(raw.get("id")),
        "number": text(pick(raw, "increment_id") or raw.get("id")),
        "orderId": as_int(pick(raw, "order_id")),
        "day": day_of(raw),
        "channel": channel_of(raw),
        "base": base,
        "tax": tax,
        "taxKnown": money_known(raw, "tax_amount"),
        "shipping": money_of(raw, "shipping_amount"),
        "total": money_of(raw, "grand_total"),
        "lines": lines,
        "hasLines": bool(lines),
    }


def resolve_percent(base: int, tax: int, known: list[float], *,
                    tax_known: bool) -> float | None:
    """Belgenin/kalemin oranı. Sıfır KDV ile okunamayan KDV'yi AYIRIR.

    Mağaza KDV alanını dolu ve sıfır gönderiyorsa (2026-08-16 ölçümü: 17
    faturanın hepsi böyle; bir dönem "16 fatura" yazıyordu, sayı büyüyor ama
    durum değişmedi) bu bir ölçüm değil BEYANDIR: satış vergisizdir ve %0
    satırına yazılır.
    Alan hiç yoksa oran türetilemez ve belge `unresolved` kovasına düşer.
    """
    if tax_known and tax == 0:
        return 0.0
    return derive_percent(base, tax, known)


def derive_percent(base: int, tax: int, known: list[float]) -> float | None:
    """Belgenin efektif oranını bilinen oranlardan birine oturtmaya çalışır.

    NEDEN GEREKLİ: fatura listesi ucu kalemleri her zaman taşımıyor. Kalemsiz
    belgeyi doğrudan "oran bilinmiyor" kovasına atmak, tek oranlı bir mağazada
    icmali tamamen boş bırakırdı.

    NEDEN TOLERANSLI: kuruş yuvarlaması yüzünden efektif oran hiçbir zaman
    tam 20,0000 çıkmaz.

    Oturmuyorsa `None` döner — karışık oranlı sepeti tek orana yazmaktansa
    "çözülemedi" demek doğrudur.
    """
    if base < MIN_DERIVE_BASE or tax <= 0 or not known:
        return None
    effective = (tax * 100) / base
    best = min(known, key=lambda item: abs(item - effective))
    return best if abs(best - effective) <= SNAP_TOLERANCE else None


def _bucket(store: dict[str, dict[str, Any]], percent: float | None) -> dict[str, Any]:
    key = rate_key(percent)
    return store.setdefault(key, {
        "key": key, "percent": percent, "label": rate_label(percent),
        "base": 0, "tax": 0, "documents": 0, "derived": 0,
        "refundBase": 0, "refundTax": 0,
    })


def _spread(doc: dict[str, Any], store: dict[str, dict[str, Any]], known: list[float],
            *, sign: int, unresolved: dict[str, Any]) -> None:
    """Bir belgenin matrah/KDV'sini oran kovalarına dağıtır.

    Kalem varsa kalem kırılımı kullanılır ve kalemlerin toplamı belgenin
    KDV'sini tutmuyorsa FARK `unresolved` kovasına yazılır — dengeleme
    yapılmaz. Kalem yoksa efektif orandan türetilir; o da oturmazsa belge
    bütünüyle `unresolved` olur.
    """
    sale = sign > 0
    base_field, tax_field = ("base", "tax") if sale else ("refundBase", "refundTax")
    lost_base, lost_tax = ("base", "tax") if sale else ("refundBase", "refundTax")

    if doc["hasLines"]:
        line_tax = 0
        touched: set[str] = set()
        for line in doc["lines"]:
            # Kalem oranı gelmiyorsa (canlı fatura kalemleri `taxPercent`
            # taşımıyor) kalemin kendi matrah/KDV'sinden çözülür.
            percent = line["percent"]
            if percent is None:
                percent = resolve_percent(line["base"], line["tax"], known,
                                          tax_known=line.get("taxKnown", False))
            bucket = _bucket(store, percent)
            bucket[base_field] += line["base"]
            bucket[tax_field] += line["tax"]
            line_tax += line["tax"]
            touched.add(bucket["key"])
        if sale:
            # Belge birden çok oran içeriyorsa HER oranın sayacına girer:
            # "bu oranda kaç belge var" sorusunun cevabı budur, belgeyi tek bir
            # orana yazmak karışık sepetleri görünmez kılardı.
            for key in touched:
                store[key]["documents"] += 1
        residual = doc["tax"] - line_tax
        if residual:
            # Kargo KDV'si ve belge düzeyi düzeltmeler kalemlerde görünmüyor.
            unresolved[lost_tax] += residual
            unresolved["documents"] += 1
            unresolved["reasons"].add("kalem toplamı belge KDV'sini tutmuyor")
        return

    derived = resolve_percent(doc["base"], doc["tax"], known, tax_known=doc["taxKnown"])
    if derived is None:
        unresolved[lost_base] += doc["base"]
        unresolved[lost_tax] += doc["tax"]
        unresolved["documents"] += 1
        unresolved["reasons"].add("kalem kırılımı yok ve efektif oran bilinen "
                                  "oranlardan hiçbirine oturmadı")
        return

    bucket = _bucket(store, derived)
    bucket[base_field] += doc["base"]
    bucket[tax_field] += doc["tax"]
    if sale:
        bucket["documents"] += 1
        if doc["tax"]:
            # Sıfır KDV TÜRETİLMİŞ sayılmaz: oran tahmin edilmedi, belge zaten
            # "vergi yok" diyor. Tahmin rozetini oraya koymak, kesin olanı
            # şüpheli göstermek olurdu.
            bucket["derived"] += 1


def summarize(*, invoices: list[dict[str, Any]], refunds: list[dict[str, Any]],
              canceled: list[dict[str, Any]], start: str, end: str,
              known_percents: list[float] | None = None,
              channel: str = "") -> dict[str, Any]:
    """Dönem KDV icmali.

    `canceled` beyandan DÜŞÜLMEZ, yalnız rapor edilir: iptal edilen siparişin
    faturası kesilmişse düşüm zaten iade (credit memo) kaydıyla gelir; iptali
    ayrıca düşmek aynı tutarı iki kez indirmek olurdu. Faturası kesilmemiş
    iptalin ise beyanla ilgisi yoktur.

    KANAL SÜZGECİ BURADA UYGULANIR, mağazada değil: 2026-08-16 ölçümünde
    `invoices?channel=zzzz` de `invoices?channel=1` de 17 faturanın hepsini,
    `refunds?channel=zzzz` 3 iadenin hepsini döndürüyor — Laravel tanımadığı
    parametreyi sessizce yok sayıyor. Süzgeci sunucuya gönderip "süzüldü"
    sanmak, bir kanalın matrahını hepsinin matrahı diye beyan etmek demekti.

    SİPARİŞ UCU AYRIKTIR ve bu not bir dönem onu da "yok sayıyor" diye
    yazıyordu — artık böyle değil: 2026-08-16'da `orders?channel=1` → 18,
    `orders?channel=default` → 0, `orders?channel=zzzz` → 0. Yani sipariş ucu
    kanalı gerçekten uyguluyor ve KİMLİK bekliyor. İcmalin davranışı yine de
    doğrudur: iptal listesine kanal parametresi HİÇ gönderilmez, ayıklama
    aşağıda kanal ADIYLA yapılır. Gönderilseydi `channel=default` iptal
    listesini sessizce boşaltır ve "bu dönemde iptal yok" derdik.
    """
    known = sorted({item for item in (known_percents or []) if item is not None})
    store: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, Any] = {"base": 0, "tax": 0, "refundBase": 0, "refundTax": 0,
                                  "documents": 0, "reasons": set()}
    channels: dict[str, dict[str, Any]] = {}

    def channel_bucket(name: str) -> dict[str, Any]:
        return channels.setdefault(name, {
            "channel": name, "base": 0, "tax": 0, "documents": 0,
            "refundBase": 0, "refundTax": 0, "refundDocuments": 0,
        })

    wanted = text(channel)

    def keep(doc: dict[str, Any]) -> bool:
        if not in_range(doc["day"], start, end):
            return False
        return not wanted or doc["channel"] == wanted

    sold = [doc for doc in (doc_row(item) for item in invoices) if keep(doc)]
    returned = [doc for doc in (doc_row(item) for item in refunds) if keep(doc)]

    for doc in sold:
        _spread(doc, store, known, sign=1, unresolved=unresolved)
        bucket = channel_bucket(doc["channel"])
        bucket["base"] += doc["base"]
        bucket["tax"] += doc["tax"]
        bucket["documents"] += 1

    for doc in returned:
        _spread(doc, store, known, sign=-1, unresolved=unresolved)
        bucket = channel_bucket(doc["channel"])
        bucket["refundBase"] += doc["base"]
        bucket["refundTax"] += doc["tax"]
        bucket["refundDocuments"] += 1

    rates = []
    for bucket in store.values():
        bucket["total"] = bucket["base"] + bucket["tax"]
        bucket["refundTotal"] = bucket["refundBase"] + bucket["refundTax"]
        bucket["netBase"] = bucket["base"] - bucket["refundBase"]
        bucket["netTax"] = bucket["tax"] - bucket["refundTax"]
        bucket["netTotal"] = bucket["netBase"] + bucket["netTax"]
        rates.append(bucket)
    # Yüksek oran üstte: beyanname de böyle okunur. Çözülemeyen kova (`?`)
    # en alta iner ve sayısal sıralamaya karışmaz.
    rates.sort(key=lambda item: (item["percent"] is None, -(item["percent"] or 0)))

    channel_rows = []
    for bucket in channels.values():
        bucket["total"] = bucket["base"] + bucket["tax"]
        bucket["netBase"] = bucket["base"] - bucket["refundBase"]
        bucket["netTax"] = bucket["tax"] - bucket["refundTax"]
        bucket["netTotal"] = bucket["netBase"] + bucket["netTax"]
        channel_rows.append(bucket)
    channel_rows.sort(key=lambda item: -item["netTotal"])

    totals = {
        "base": sum(item["base"] for item in rates) + unresolved["base"],
        "tax": sum(item["tax"] for item in rates) + unresolved["tax"],
        "refundBase": sum(item["refundBase"] for item in rates) + unresolved["refundBase"],
        "refundTax": sum(item["refundTax"] for item in rates) + unresolved["refundTax"],
    }
    totals["total"] = totals["base"] + totals["tax"]
    totals["refundTotal"] = totals["refundBase"] + totals["refundTax"]
    totals["netBase"] = totals["base"] - totals["refundBase"]
    totals["netTax"] = totals["tax"] - totals["refundTax"]
    totals["netTotal"] = totals["netBase"] + totals["netTax"]

    canceled_rows = [doc for doc in (doc_row(item) for item in canceled) if keep(doc)]

    warnings: list[str] = []
    if wanted:
        warnings.append(
            f"Kanal süzgeci («{wanted}») Kontrol Merkezi'nde uygulandı: mağaza bu süzgeci "
            "listeleme ucunda kabul etmiyor, belgeler çekildikten sonra ayıklandı. Belge "
            "tavanı süzgeçten ÖNCEKİ sayıya bakar.")
    if unresolved["documents"]:
        warnings.append(
            f"{unresolved['documents']} belgenin oranı çözülemedi "
            f"({', '.join(sorted(unresolved['reasons']))}). Tutarları toplamlara "
            "GİRDİ ama oran kırılımında ayrı satırda duruyor; beyan öncesi "
            "elle bakılmalı.")
    if canceled_rows:
        warnings.append(
            f"Bu dönemde {len(canceled_rows)} sipariş iptal edildi. İptaller icmalden "
            "DÜŞÜLMEZ: faturası kesilmiş bir iptalin düşümü iade kaydıyla zaten gelir, "
            "iki kez düşmek beyanı eksiltirdi.")
    if any(item["derived"] for item in rates):
        warnings.append(
            "Bazı faturaların kalem kırılımı gelmedi; oranları belge toplamından "
            "türetildi (matrah ve KDV doğru, kırılım tahmini).")

    return {
        "range": {"start": start, "end": end},
        "channel": channel,
        "rates": rates,
        "channels": channel_rows,
        "totals": totals,
        "unresolved": {
            "base": unresolved["base"], "tax": unresolved["tax"],
            "refundBase": unresolved["refundBase"], "refundTax": unresolved["refundTax"],
            "documents": unresolved["documents"],
            "reasons": sorted(unresolved["reasons"]),
        },
        "documents": {
            "invoices": len(sold), "refunds": len(returned), "canceled": len(canceled_rows),
        },
        "canceled": {
            "count": len(canceled_rows),
            "total": sum(item["total"] for item in canceled_rows),
        },
        "warnings": warnings,
    }


def in_range(day: str, start: str, end: str) -> bool:
    """Belge aralıkta mı — ISO gün metinleri sözlük sırasıyla karşılaştırılır.

    NEDEN YEREL SÜZME DE VAR: süzgeç sunucuya gönderiliyor ama Laravel
    tanımadığı sorgu parametresini SESSİZCE yok sayar. Yok sayılmışsa aralık
    dışı belgeler icmale girer ve beyan şişer. Servis ayrıca "süzgeç uygulandı
    mı" denetimi yapar; burası ikinci emniyet kemeridir.
    """
    if not day:
        return False
    if start and day < start:
        return False
    return not (end and day > end)


def range_honored(docs: list[dict[str, Any]], start: str, end: str) -> bool | None:
    """Sunucu tarih süzgecini gerçekten uyguladı mı?

    `None` = anlaşılamadı (hiç belge yok ya da tarih alanı boş geldi).
    Bilinmeyeni "evet" saymak, eksik veriyle üretilmiş bir KDV icmalini doğru
    gibi göstermek olurdu.
    """
    days = [text(pick(item, "created_at"))[:10] for item in docs]
    days = [day for day in days if day]
    if not days:
        return None
    return all(in_range(day, start, end) for day in days)
