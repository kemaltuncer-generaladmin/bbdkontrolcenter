"""AI araç kataloğu, öneri fark tablosu, jeton/maliyet ve bütçe hesabı.

SAF DOSYA: ağa çıkmaz, durum tutmaz, saat okumaz (gün her zaman parametre).
Bu ekranın iki tehlikeli kararı burada verilir ve burada test edilir:

  1. FARK TABLOSU. AI'nin döndürdüğü metin doğrudan uygulanmaz; hangi ürünün
     hangi ALANI, hangi ESKİ değerden hangi YENİ değere gideceği satır satır
     çıkarılır. Uygulama bu satırları okur, yeniden HESAPLAMAZ — kullanıcı
     neyi onayladıysa o gider.
  2. BÜTÇE. Para harcayan tek ekran burasıdır. Sayaç okunamadığında ne
     olacağı `budget_verdict()` içinde tek yerde yazılıdır.
"""

from __future__ import annotations

from typing import Any

from .rules import as_int, text, to_kurus

#: Bu ekranın TANIDIĞI araçlar. Mağaza (MagicAI) başka araçlar da sunabilir;
#: tanımadığımız araç ÇALIŞTIRILMAZ, çünkü çıktısının hangi alana yazılacağını
#: bilmeden anlamlı bir fark tablosu çizilemez ve "onaysız uygulama yok"
#: kuralı boşa düşer. Ekran tanımadığı aracı listeler ve nedenini söyler.
#:
#: `writes` boşsa araç MAĞAZAYA HİÇBİR ŞEY YAZMAZ — çıktısı okunur bir
#: özettir (yorum duygu analizi böyledir) ve "Uygula" düğmesi hiç çıkmaz.
TOOLS: dict[str, dict[str, Any]] = {
    "product_description": {
        "label": "Ürün açıklaması üret",
        "summary": "Seçili ürünler için tanıtım metni önerir.",
        "target": "product",
        "writes": [("description", "Açıklama")],
        "note": "Öneri metni ürünün mevcut açıklamasının YERİNE geçer, sonuna eklenmez.",
    },
    "seo_meta": {
        "label": "SEO meta üret",
        "summary": "Meta başlık ve meta açıklama önerir.",
        "target": "product",
        "writes": [("meta_title", "Meta başlık"), ("meta_description", "Meta açıklama")],
        "note": "Arama sonucunda görünen metindir; başlık 60, açıklama 160 karakterde kesilir.",
    },
    "image_alt": {
        "label": "Görsel alt metni üret",
        "summary": "Görselleri olan ürünler için erişilebilirlik alt metni önerir.",
        "target": "product",
        "writes": [("image_alt", "Görsel alt metni")],
        "note": "Ekran okuyucu bu metni okur; görseli olmayan ürün için üretilmez.",
    },
    "review_sentiment": {
        "label": "Yorum duygu özeti",
        "summary": "Seçilen dönemin yorumlarını olumlu/olumsuz başlıklarda özetler.",
        "target": "review",
        "writes": [],
        "note": "Mağazaya hiçbir şey yazmaz; çıktı okunur bir özettir ve rapora girer.",
    },
    "request_triage": {
        "label": "Talep sınıflandırma",
        "summary": "Açık taleplere konu ve öncelik önerir.",
        "target": "request",
        "writes": [("category", "Konu"), ("priority", "Öncelik")],
        "note": "Öneri talebi kapatmaz, yalnız etiketler. Yanıt metnini insan yazar.",
    },
}

#: Öneri satırının durumu. İKİ DEĞER VARDIR, üç değil: bir satır ya onay
#: bekler ya uygulanmıştır. "Reddedildi" diye bir durum YOK — reddetmek satırı
#: seçmemektir ve seçilmeyen satır zaten uygulanmaz. Yazılıp hiçbir yerde
#: kurulmayan bir `REJECTED` sabiti, ekranda karşılığı olmayan bir durum
#: varmış gibi gösteriyordu.
PENDING = "pending"
APPLIED = "applied"


def tool_labels() -> list[dict[str, Any]]:
    """Araç kartlarının statik künyesi (mağaza durumu ayrı eklenir)."""
    return [{
        "key": key,
        "label": spec["label"],
        "summary": spec["summary"],
        "target": spec["target"],
        "writes": [{"field": field, "label": label} for field, label in spec["writes"]],
        "readOnly": not spec["writes"],
        "note": spec["note"],
    } for key, spec in TOOLS.items()]


def known_tool(tool: str) -> bool:
    return text(tool) in TOOLS


# =========================================================== fark tablosu

def suggestion_rows(tool: str, payload: Any, *, before: dict[int, dict[str, Any]] | None = None,
                    ) -> list[dict[str, Any]]:
    """Mağazadan gelen ham AI yanıtını FARK TABLOSUNA çevirir.

    Beklenen yanıt biçimi (BBD ucunun sözleşmesi):
        {"suggestions": [{"targetId": 5, "sku": "...", "name": "...",
                          "fields": {"description": "yeni metin"},
                          "confidence": 0.82, "note": ""}]}

    Tanınmayan alan SESSİZCE DÜŞÜRÜLMEZ, `skipped` olarak tabloya girer:
    "modelin ürettiği ama uygulanamayan şey" gizlenirse kullanıcı neyin
    uygulanmadığını hiç öğrenemez.

    `before` ekranın elindeki mevcut değerlerdir ve ALAN ALAN sorulur: kayıtta
    hiç bulunmayan bir alanın eski değeri "boş" DEĞİL "bilinmiyor"dur. Satır
    `beforeKnown: False` taşır ve ekran `—` gösterir; uydurma eski değer
    yazmak, farkı olduğundan büyük gösterirdi.
    """
    spec = TOOLS.get(text(tool))
    if spec is None:
        return []
    allowed = {field: label for field, label in spec["writes"]}
    current = before or {}

    raw_items = payload.get("suggestions") if isinstance(payload, dict) else None
    if raw_items is None and isinstance(payload, dict):
        raw_items = payload.get("items") or payload.get("data")
    rows: list[dict[str, Any]] = []

    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        target_id = as_int(item.get("targetId") or item.get("target_id") or item.get("id"))
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        known = current.get(target_id) or {}
        for field, value in fields.items():
            label = allowed.get(str(field))
            after = text(value)
            was = text(known.get(str(field)))
            rows.append({
                "id": f"{target_id}:{field}",
                "targetId": target_id,
                "sku": text(item.get("sku")),
                "name": text(item.get("name")) or f"#{target_id}",
                "field": str(field),
                "fieldLabel": label or str(field),
                "before": was,
                "beforeKnown": str(field) in known,
                "after": after,
                "changed": bool(after) and after != was,
                "confidence": _confidence(item.get("confidence")),
                "note": text(item.get("note")),
                "skipped": label is None or not after,
                "skipReason": ("Bu araç bu alana yazmıyor; öneri uygulanamaz."
                               if label is None else
                               ("Model boş metin döndürdü." if not after else "")),
                "state": PENDING,
            })
    return rows


def _confidence(value: Any) -> int | None:
    """Güven yüzdesi 0–100 tam sayı; yoksa `None` (sıfır DEĞİL — "bilinmiyor"
    ile "hiç güvenmiyor" aynı şey değildir)."""
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number <= 1:
        number *= 100
    return max(0, min(100, round(number)))


def diff_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Onay kutusunda yazan tek satırlık özet."""
    usable = [row for row in rows if not row.get("skipped") and row.get("changed")]
    return {
        "total": len(rows),
        "applicable": len(usable),
        "skipped": len([row for row in rows if row.get("skipped")]),
        "unchanged": len([row for row in rows
                          if not row.get("skipped") and not row.get("changed")]),
        "targets": len({row["targetId"] for row in usable}),
        "applied": len([row for row in rows if row.get("state") == APPLIED]),
    }


def selected_rows(rows: list[dict[str, Any]], selections: list[str]) -> list[dict[str, Any]]:
    """Onaylanan satırlar. Seçim listesi BOŞSA hiçbir şey seçilmemiştir —
    "boş seçim = hepsi" kısayolu, yanlış tıklamayı toplu yazmaya çevirirdi."""
    wanted = {str(item) for item in (selections or [])}
    return [row for row in rows
            if str(row["id"]) in wanted and not row.get("skipped") and row.get("changed")
            and row.get("state") != APPLIED]


def mark_applied(rows: list[dict[str, Any]], applied_ids: list[str]) -> list[dict[str, Any]]:
    """Uygulanan satırları işaretler; liste yerinde DEĞİL kopyayla döner."""
    done = {str(item) for item in (applied_ids or [])}
    return [{**row, "state": APPLIED} if str(row["id"]) in done else dict(row)
            for row in rows]


def apply_selection_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mağazaya gidecek seçim yükü. Fark tablosundaki DEĞERİN AYNISI gider;
    burada yeniden üretim yoktur (uygulanan = önizlenen)."""
    return [{"targetId": row["targetId"], "field": row["field"], "value": row["after"]}
            for row in rows]


# ================================================================== bütçe

def month_key(iso: str) -> str:
    """`2026-08-13T10:00:00+03:00` → `2026-08`. Bütçe aylıktır."""
    return text(iso)[:7]


def ledger_spend(rows: list[dict[str, Any]], *, month: str) -> int:
    """Yerel defterden bu ayın harcaması (kuruş).

    Yerel defter mağazanın sayacının YERİNE geçmez, YEDEĞİdir: Kontrol
    Merkezi dışından yapılan çalıştırmaları göremez. Bu yüzden iki sayaç
    varken büyüğü kullanılır (`budget_verdict`), küçüğü değil.
    """
    total = 0
    for row in rows:
        if month_key(str(row.get("created_at") or row.get("createdAt") or "")) != month:
            continue
        total += max(0, as_int(row.get("cost"), 0))
    return total


def budget_verdict(*, spent: int, budget: int, behavior: str = "block",
                   measured: bool = True, estimate_allowed: bool = True) -> dict[str, Any]:
    """Yeni bir çalıştırmaya izin var mı — ve ekranda ne yazacak.

    Dört durum vardır ve hiçbiri sessiz değildir:
      · bütçe 0        → sınır yok, uyarı yok.
      · sayaç okunamadı ve tahmine izin yok → DURDUR. Ölçemediğimiz parayı
        harcamayız; "herhâlde azdır" varsayımı faturayı büyütür.
      · sınır aşıldı, davranış `block` → DURDUR.
      · sınır aşıldı, davranış `warn`  → izin ver, kırmızı uyarı göster.
    """
    if budget <= 0:
        return {"allowed": True, "level": "ok", "spent": spent, "budget": 0,
                "remaining": 0, "percent": 0, "measured": measured,
                "message": "Aylık bütçe sınırı tanımlı değil."}

    if not measured and not estimate_allowed:
        return {"allowed": False, "level": "block", "spent": spent, "budget": budget,
                "remaining": budget, "percent": 0, "measured": False,
                "message": "Bu ayın harcaması okunamadı ve tahmine izin verilmedi; "
                           "ölçemeden para harcanmaz. Ayarlardan tahmine izin "
                           "verebilir ya da mağaza bağlantısını düzeltebilirsiniz."}

    remaining = budget - spent
    percent = min(999, round(spent * 100 / budget))
    prefix = "" if measured else "Tahmini: "

    if remaining <= 0:
        blocked = behavior != "warn"
        return {
            "allowed": not blocked, "level": "block" if blocked else "warn",
            "spent": spent, "budget": budget, "remaining": 0, "percent": percent,
            "measured": measured,
            "message": (f"{prefix}Aylık bütçe doldu (%{percent}). "
                        + ("Yeni çalıştırma durduruldu; sınırı Ayarlar'dan yükseltin."
                           if blocked else "Ayar 'uyar' olduğu için çalıştırma sürüyor.")),
        }

    if percent >= 80:
        return {"allowed": True, "level": "warn", "spent": spent, "budget": budget,
                "remaining": remaining, "percent": percent, "measured": measured,
                "message": f"{prefix}Aylık bütçenin %{percent}'i kullanıldı."}

    return {"allowed": True, "level": "ok", "spent": spent, "budget": budget,
            "remaining": remaining, "percent": percent, "measured": measured,
            "message": f"{prefix}Aylık bütçenin %{percent}'i kullanıldı."}


# ============================================================ kullanım özeti

def usage_view(store_usage: Any, ledger: list[dict[str, Any]], *, month: str,
               days: int = 30) -> dict[str, Any]:
    """Günlük seri + araç kırılımı. Mağaza sayacı varsa o, yoksa yerel defter.

    Mağaza para birimini ONDALIK gönderir (geçidin sözleşmesi: kuruşa çevirmek
    çağıranın işi). Çeviri burada yapılır ve bir kez yapılır.
    """
    remote = store_usage if isinstance(store_usage, dict) else {}
    remote_days = remote.get("daily") or remote.get("days")
    remote_tools = remote.get("byTool") or remote.get("tools")

    if isinstance(remote_days, list) and remote_days:
        series = [{"label": text(row.get("date") or row.get("label"))[5:],
                   "value": cost_kurus(row)} for row in remote_days if isinstance(row, dict)]
        tokens = as_int(remote.get("tokens"), 0)
        spent = cost_kurus(remote) or sum(point["value"] for point in series)
        measured = True
    else:
        buckets: dict[str, int] = {}
        tokens = 0
        for row in ledger:
            day = text(row.get("created_at") or row.get("createdAt"))[:10]
            if not day:
                continue
            buckets[day] = buckets.get(day, 0) + max(0, as_int(row.get("cost"), 0))
            tokens += max(0, as_int(row.get("tokens"), 0))
        series = [{"label": day[5:], "value": value}
                  for day, value in sorted(buckets.items())][-max(1, days):]
        spent = ledger_spend(ledger, month=month)
        measured = False

    if isinstance(remote_tools, list) and remote_tools:
        by_tool = [{"key": text(row.get("tool")),
                    "label": TOOLS.get(text(row.get("tool")), {}).get("label")
                    or text(row.get("tool")) or "(bilinmeyen)",
                    "runs": as_int(row.get("runs"), 0),
                    "cost": cost_kurus(row)}
                   for row in remote_tools if isinstance(row, dict)]
    else:
        counts: dict[str, dict[str, int]] = {}
        for row in ledger:
            key = text(row.get("tool"))
            slot = counts.setdefault(key, {"runs": 0, "cost": 0})
            slot["runs"] += 1
            slot["cost"] += max(0, as_int(row.get("cost"), 0))
        by_tool = [{"key": key,
                    "label": TOOLS.get(key, {}).get("label") or key or "(bilinmeyen)",
                    "runs": slot["runs"], "cost": slot["cost"]}
                   for key, slot in counts.items()]

    by_tool.sort(key=lambda row: -row["cost"])
    return {"days": series, "byTool": by_tool, "spent": spent, "tokens": tokens,
            "measured": measured, "month": month}


def cost_kurus(row: Any) -> int:
    """Maliyeti kuruşa çevirir. `costKurus` varsa doğrudan kullanılır; yoksa
    ondalık `cost` çevrilir. İkisi de yoksa 0."""
    if not isinstance(row, dict):
        return 0
    if row.get("costKurus") is not None:
        return max(0, as_int(row["costKurus"], 0))
    value = to_kurus(row.get("cost") if row.get("cost") is not None else row.get("amount"))
    return max(0, value or 0)


# ============================================================ geçmiş birleşme

def merge_runs(local: list[dict[str, Any]], remote: Any) -> list[dict[str, Any]]:
    """Yerel defter + mağaza geçmişi.

    YEREL DEFTER ÖNCELİKLİDİR çünkü GEREKÇEYİ yalnız o taşır. Mağazada olup
    yerelde olmayan çalışmalar (başka bir araçtan yapılanlar) listeye
    `local: False` ile eklenir — gizlenirse "bu ay neden bu kadar harcanmış"
    sorusu cevapsız kalır.
    """
    rows = [{**row, "local": True} for row in local]
    seen = {as_int(row.get("runId") or row.get("run_id")) for row in rows}
    seen.discard(0)

    for item in (remote if isinstance(remote, list) else []):
        if not isinstance(item, dict):
            continue
        run_id = as_int(item.get("id") or item.get("runId"))
        if run_id and run_id in seen:
            continue
        rows.append({
            "token": "", "runId": run_id, "tool": text(item.get("tool")),
            "toolLabel": TOOLS.get(text(item.get("tool")), {}).get("label")
            or text(item.get("tool")),
            "actor": text(item.get("user") or item.get("actor")) or "(mağaza)",
            "reason": "", "status": text(item.get("status")) or "ok",
            "tokens": as_int(item.get("tokens"), 0), "cost": cost_kurus(item),
            "targets": as_int(item.get("targets"), 0), "applied": 0,
            "durationMs": as_int(item.get("duration_ms") or item.get("durationMs"), 0),
            "createdAt": text(item.get("created_at") or item.get("createdAt"))[:19],
            "local": False,
        })

    rows.sort(key=lambda row: sort_stamp(row.get("createdAt")), reverse=True)
    return rows


def sort_stamp(value: Any) -> str:
    """İki kaynaktan gelen zaman damgasını KARŞILAŞTIRILABİLİR hâle getirir.

    TUZAK: yerel defter ISO yazıyor (`2026-08-13T18:27:17`), mağaza ise
    saat dilimsiz boşluklu (`2026-08-13 18:27:17`). Ham metin sıralamasında
    onuncu karakter `T` (0x54) ile boşluk (0x20) karşılaşır ve `T` büyüktür:
    TARİHTEN BAĞIMSIZ olarak bütün yerel satırlar bütün mağaza satırlarının
    üstüne çıkıyordu. Geçen yılki bir yerel çalışma, bugün mağazada yapılan
    çalışmanın üstünde duruyordu — liste sıralı görünüyor ama değil.

    Ayırıcı boşluğa çevrilir ve damga saniyede kesilir; offset (`+03:00`)
    böylece düşer — mağaza damgası offset taşımıyor ve ikisi zaten aynı yerel
    saati anlatıyor.
    """
    return text(value).replace("T", " ")[:19].strip()
