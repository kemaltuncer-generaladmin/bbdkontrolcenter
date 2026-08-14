"""Müşteri aşama SMS'inin saf mantığı — ağa çıkmaz, durum tutmaz, testin hedefi.

ÜÇ AŞAMA: sipariş alındı · kargoya verildi · teslim edildi. Metinler
`messaging.py` ile aynı ilkeye uyar (bilinmeyen değişken boşa çevrilmez, sayaç
maliyeti söyler) ama BURADA EK BİR ZORUNLULUK VAR: **tek segment**.

NEDEN AYRI DOSYA. Bu üç mesaj toplu bildirim değil, sipariş başına giden
işlemsel SMS'tir: canlıda günde onlarca kez, kimse bakmadan çıkar. Yanlış
hesaplanmış tek bir septet, üç ay sonra iki katı faturadır. Hesap servisin
içinde dursaydı tek satırı bile ağsız sınanamazdı.

ALTI TUZAK — hepsinin karşılığı burada bir fonksiyondur:

 1. TEK SEGMENT ZORUNLU. 161. septet ikinci SMS demektir ve kimse fark etmez;
    `template_problem` tek segmenti aşan şablonu KAYDETTİRMEZ, sadeleştirmeyi
    önerir (`messaging.sms_plan(...)["simplified"]`).
 2. ÖLÇÜ İYİMSER ÖRNEKLE YAPILMAZ. "Ayse Yilmaz" ile 1 parçaya sığan metin
    "Mehmet Emin Karaosmanoglu" ile 2 parçaya taşar. Ölçüm `GUARD_SAMPLE` ile
    yapılır: bilerek UZUN ad, UZUN takip numarası, UZUN bağlantı.
 3. `ç` GSM-7 TEMEL KÜMESİNDE YOKTUR (`km_platform/notify/text.py` —
    `_GSM7_BASIC` içinde `Ç` var, küçük `ç` yok). Tek bir `ç` mesajı UCS-2'ye
    düşürür: 160 sınırı 70'e iner ve üç katı fatura gelir. Varsayılan metinler
    bu yüzden ASCII'dir; `template_problem` sayacı bunu zaten yakalar.
 4. AŞAMADA DOLMAYAN DEĞİŞKEN. "Sipariş alındı" metnine `{{kargo_takip}}`
    koymak, gönderim anında müşteriye süslü parantez göndermektir. Aşama dışı
    değişken UYARI DEĞİL, KAYIT ENGELİDİR.
 5. "KARGOYA VERİLDİ" ÜÇ ŞEY TAŞIMAK ZORUNDA: firma, takip numarası, takip
    bağlantısı. Biri eksikse mesajın müşteriye faydası yoktur; şablon
    kaydedilmez, veri eksikse gönderim yapılmaz ve NEDENİ yazılır.
 6. NUMARA ÖNCE DENETLENİR. Sağlayıcıya geçersiz numara vermek "gönderildi"
    yanılgısı üretir; `phone_error` gönderimden önce konuşur.
"""

from __future__ import annotations

from typing import Any

from km_sdk import normalize_msisdn

from . import messaging

#: Aşama anahtarları — ekrandaki sıra da budur (siparişin kendi akışı).
STAGES = ("order_placed", "shipped", "delivered")

STAGE_LABELS = {
    "order_placed": "Sipariş alındı",
    "shipped": "Kargoya verildi",
    "delivered": "Teslim edildi",
}

STAGE_TRIGGERS = {
    "order_placed": "Mağazaya yeni sipariş düştüğünde (Siparişler ekranının taraması).",
    "shipped": "“Kargoya ver” tamamlandığında (tek sipariş ya da toplu iş).",
    "delivered": "Geliver takip durumu “teslim edildi”ye döndüğünde (webhook/senkron).",
}

#: Aşamada GERÇEKTEN dolan değişkenler. Palet buna göre daralır ve bunun
#: dışındaki bir değişken şablona girerse kayıt reddedilir (TUZAK 4).
STAGE_VARIABLES = {
    "order_placed": ("magaza_adi", "musteri_adi", "siparis_no", "siparis_tarihi", "tutar"),
    "shipped": ("magaza_adi", "musteri_adi", "siparis_no", "kargo_firma", "kargo_takip",
                "kargo_takip_linki"),
    "delivered": ("magaza_adi", "musteri_adi", "siparis_no"),
}

#: Aşamanın metninde BULUNMASI ZORUNLU değişkenler (TUZAK 5). "Kargoya verildi"
#: mesajı takip kodu, firma ve bağlantı taşımıyorsa müşteriye hiçbir şey
#: söylemiyordur; o mesajın parası da boşa gitmiş olur.
REQUIRED_VARIABLES = {
    "order_placed": ("siparis_no",),
    "shipped": ("kargo_firma", "kargo_takip", "kargo_takip_linki"),
    "delivered": ("siparis_no",),
}

#: Tek segment. `plan_text` 160 septeti aşınca 2 parçaya çıkar.
MAX_PARTS = 1

#: VARSAYILAN METİNLER — kibar, nazik, ölçülü esprili; üçü de FARKLI.
#:
#: TÜRKÇE HARF YOK. `ğ ı ş İ Ğ Ş` iki septet yiyor, küçük `ç` ise mesajı
#: tamamen UCS-2'ye düşürüyor (TUZAK 3). Kullanıcı isterse Türkçe yazar —
#: ekran maliyeti anında söyler ve tek segmenti aşarsa kaydettirmez.
#:
#: Üçü de `GUARD_SAMPLE` (bilerek uzun örnek) ile tek segmenttir; ölçüm
#: `tests/test_store_notifications_lifecycle.py` içinde sabitlenmiştir.
DEFAULT_TEMPLATES = {
    "order_placed": (
        "Sayin {{musteri_adi}}, {{siparis_no}} siparisinizi aldik. Kitaplar raftan "
        "iniyor, hazir olunca haber verecegiz. {{magaza_adi}}"
    ),
    "shipped": (
        "Sayin {{musteri_adi}}, {{siparis_no}} kitaplariniz {{kargo_firma}} ile yolda. "
        "Takip {{kargo_takip}}: {{kargo_takip_linki}}"
    ),
    "delivered": (
        "Sayin {{musteri_adi}}, {{siparis_no}} siparisiniz teslim edildi. Kitaplar "
        "sizde, gerisi size kalmis: iyi okumalar. {{magaza_adi}}"
    ),
}

#: ÖLÇÜM ÖRNEĞİ — bilerek UZUN (TUZAK 2). Önizleme de bu değerlerle çizilir:
#: sayaç ile kayıt kapısı AYNI metni ölçmezse ekran "1 parça" derken kayıt
#: "2 parça" diye reddederdi.
GUARD_SAMPLE = {
    "magaza_adi": "BBD Store",
    "musteri_adi": "Mehmet Emin Karaosmanoglu",
    "siparis_no": "SP-2026-004173",
    "siparis_tarihi": "13.08.2026",
    "tutar": "1.249,90 TL",
    "kargo_firma": "Yurtici Kargo",
    "kargo_takip": "7350041982143",
    "kargo_takip_linki": "https://bbdstore.com.tr/kargo/7350041982143",
}

#: Gönderim izinin sonuçları. `sent` DIŞINDAKİLERİN HİÇBİRİ müşteriye ulaşmaz
#: ve hiçbiri sessiz değildir: ekran satırı nedeniyle gösterir.
RESULT_LABELS = {
    "sent": "Gönderildi",
    "dry_run": "Kuru prova — gönderilmedi",
    "no_phone": "Gönderilemedi: numara yok",
    "bad_phone": "Gönderilemedi: numara geçersiz",
    "missing": "Gönderilemedi: bilgi eksik",
    "error": "Gönderilemedi: sağlayıcı hatası",
}

RESULT_TONES = {
    "sent": "good",
    "dry_run": "info",
    "no_phone": "warn",
    "bad_phone": "warn",
    "missing": "warn",
    "error": "bad",
}

#: Yalnız bu sonuç "müşteri rahatsız edildi" demektir; ikinci gönderimi
#: SADECE bu engeller. Numarası olmadığı için gidememiş bir mesaj, numara
#: düzeltilince gitmelidir (bkz. `blocks_resend`).
SENT = "sent"


def blocks_resend(result: str) -> bool:
    """Aynı aşamanın ikinci kez gönderilmesini engelleyen sonuç mu?

    YALNIZ GERÇEKTEN GİDEN MESAJ ENGELLER. Kuru prova müşteriyi rahatsız
    etmedi ve para harcamadı; onu da engel saymak, provadan sonra gerçek
    gönderimi imkânsız kılardı. "Numara yok" da engel değildir: numara
    düzeltilince mesaj gitmelidir.
    """
    return messaging.text(result) == SENT


# ===================================================================== aşama

def stage_error(stage: str) -> str:
    """Aşama tanınan üçlüden biri mi.

    Serbest metin kabul edilmez: yazım hatalı bir aşama adı, hiçbir zaman
    tetiklenmeyen bir şablon ve hiç kimseye gitmeyen bir SMS demektir — ve
    hata da vermez.
    """
    if messaging.text(stage) not in STAGES:
        names = ", ".join(STAGE_LABELS[item] for item in STAGES)
        return f"Bilinmeyen aşama: {messaging.text(stage) or '(boş)'}. Beklenen: {names}."
    return ""


def variables_for(stage: str) -> list[dict[str, Any]]:
    """Aşamanın paleti — YALNIZ bu aşamada dolan değişkenler.

    `messaging.variable_palette` gibi "ilgisizleri de göster" yapılmaz: burada
    aşama dışı bir değişken kayıt engelidir, palete koymak onu meşru gösterirdi.
    """
    wanted = STAGE_VARIABLES.get(messaging.text(stage), ())
    labels = {key: label for key, label, _ in messaging.VARIABLES}
    return [{"key": key, "token": "{{" + key + "}}",
             "label": labels.get(key, key),
             "sample": GUARD_SAMPLE.get(key, ""),
             "required": key in REQUIRED_VARIABLES.get(messaging.text(stage), ())}
            for key in wanted]


def guard_values(stage: str = "") -> dict[str, str]:
    """Ölçüm/önizleme değerleri. Aşama verilirse aşama dışı alanlar BOŞ kalır:
    metne sızmış bir `{{iade_no}}` önizlemede de doldurulmuş görünmemeli."""
    if not stage:
        return dict(GUARD_SAMPLE)
    wanted = STAGE_VARIABLES.get(messaging.text(stage), ())
    return {key: value for key, value in GUARD_SAMPLE.items() if key in wanted}


def render(body: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    """`messaging.render` ile aynı sözleşme: bilinmeyen değişken YERİNDE KALIR."""
    return messaging.render(body, values)


def plan(text_body: str, *, price_kurus: int = 0) -> dict[str, Any]:
    """Tek alıcılık sayaç. Aşama SMS'i sipariş başına gider; `recipients` 1'dir."""
    return messaging.sms_plan(text_body, recipients=1, price_kurus=price_kurus)


def off_stage(stage: str, used: list[str]) -> list[str]:
    """Metinde geçen ama BU AŞAMADA DOLMAYAN değişkenler (TUZAK 4)."""
    wanted = STAGE_VARIABLES.get(messaging.text(stage), ())
    return [key for key in used if key not in wanted]


def missing_required(stage: str, used: list[str]) -> list[str]:
    """Aşamanın zorunlu değişkenlerinden metinde OLMAYANLAR (TUZAK 5)."""
    return [key for key in REQUIRED_VARIABLES.get(messaging.text(stage), ())
            if key not in used]


def template_problem(stage: str, body: str, *, price_kurus: int = 0) -> str:
    """Şablon kaydedilebilir mi — kullanıcıya gösterilecek metin ya da boş.

    KAPI BURADADIR, arayüzde değil (K9). Sıra bilerek şudur: önce "bu metin
    ne söylüyor" (aşama, değişkenler), sonra "kaça mal oluyor" (segment).
    Segment hatasını önce söylemek, düzeltilse bile kaydı açmayan bir hatayı
    düzelttirmek olurdu.
    """
    problem = stage_error(stage)
    if problem:
        return problem
    text_body = messaging.text(body)
    if not text_body:
        return "Mesaj metni boş olamaz; aşamayı kapatmak için Açık/Kapalı anahtarını kullanın."

    filled = render(text_body, guard_values(stage))
    unknown = [key for key in filled["unknown"]]
    if unknown:
        return (f"Palette olmayan değişken: {', '.join(sorted(set(unknown)))}. Bu alanlar "
                "hiçbir zaman dolmaz ve müşteriye süslü parantez olarak gider.")

    stray = off_stage(stage, filled["used"])
    if stray:
        return (f"{STAGE_LABELS[stage]} aşamasında dolmayan değişken: "
                f"{', '.join(sorted(set(stray)))}. Gönderim anında boş kalır; "
                "metinden çıkarın.")

    absent = missing_required(stage, filled["used"])
    if absent:
        return (f"{STAGE_LABELS[stage]} mesajı şunları taşımak zorunda: "
                f"{', '.join('{{' + key + '}}' for key in absent)}. Bunlar olmadan mesaj "
                "müşteriye bir şey söylemez.")

    counted = plan(filled["text"], price_kurus=price_kurus)
    if counted["parts"] > MAX_PARTS:
        note = (f"Metin {counted['parts']} SMS parçası ({counted['units']}/"
                f"{counted['capacity']} birim). Aşama SMS'i sipariş başına gider; "
                "tek parçayı aşan şablon her siparişte iki kredi harcar.")
        if counted["simplified"] and counted["simplifiedParts"] <= MAX_PARTS:
            note += (" Türkçe harfleri sadeleştirmek yeter: "
                     f"“{counted['simplified']}”")
        elif counted["offending"]:
            note += (" Metni pahalılaştıran karakterler: "
                     f"{' '.join(counted['offending'])}")
        else:
            note += " Metni kısaltın."
        return note
    return ""


def template_view(stage: str, body: str, *, enabled: bool,
                  price_kurus: int = 0, source: str = "local") -> dict[str, Any]:
    """Ekranın bir aşama için gördüğü her şey: metin, ölçüm, sorun, palet."""
    text_body = messaging.text(body)
    filled = render(text_body, guard_values(stage))
    counted = plan(filled["text"], price_kurus=price_kurus)
    return {
        "stage": stage,
        "label": STAGE_LABELS.get(stage, stage),
        "trigger": STAGE_TRIGGERS.get(stage, ""),
        "body": text_body,
        "enabled": bool(enabled),
        # `default` = metin hâlâ fabrika ayarında mı. Ekran bunu yazar: kimse
        # "biz bunu yazmıştık" sanarak varsayılanı canlıya açmasın.
        "default": text_body == DEFAULT_TEMPLATES.get(stage, ""),
        "defaultBody": DEFAULT_TEMPLATES.get(stage, ""),
        "source": source,
        "preview": filled["text"],
        "missing": sorted(set(filled["missing"])),
        "unknown": sorted(set(filled["unknown"])),
        "offStage": sorted(set(off_stage(stage, filled["used"]))),
        "requiredMissing": missing_required(stage, filled["used"]),
        "plan": counted,
        "problem": template_problem(stage, text_body, price_kurus=price_kurus),
        "variables": variables_for(stage),
    }


# ==================================================================== numara

def phone_error(raw: Any) -> str:
    """Numara denetimi — GÖNDERİMDEN ÖNCE (TUZAK 6).

    Sağlayıcıya geçersiz numara vermek "kabul edildi" yanıtı alıp hiçbir yere
    ulaşmamak demektir; ekran gönderildi sanır ve kimse aramaz.
    """
    value = messaging.text(raw)
    if not value:
        return "Müşterinin cep numarası yok."
    try:
        normalize_msisdn(value)
    except ValueError:
        return f"Cep numarası çözülemedi ({value}); 10 hane olmalı ve 5 ile başlamalı."
    return ""


def normal_phone(raw: Any) -> str:
    """`5XXXXXXXXX` biçimine indirger; çözülemezse ham hâli döner."""
    value = messaging.text(raw)
    try:
        return normalize_msisdn(value)
    except ValueError:
        return value


# ============================================================ sipariş künyesi

def order_values(stage: str, order: dict[str, Any], *, store_name: str,
                 tracking_base: str = "") -> dict[str, str]:
    """Çağıranın verdiği sipariş künyesini şablon değişkenlerine çevirir.

    KÜNYE SÖZLEŞMESİ (Siparişler ekranı bunu doldurur): `orderId` · `orderNo` ·
    `customer` · `phone` · `total` · `date` · `carrier` · `track` · `trackUrl`.

    TAKİP BAĞLANTISI UYDURULMAZ. Taşıyıcının kendi bağlantısı geldiyse o
    kullanılır; gelmediyse ancak yapılandırılmış bir önek varsa bağlantı
    kurulur. Önek de yoksa alan BOŞ kalır ve gönderim "bilgi eksik" diye
    durur — çalışmayan bir bağlantı göndermek, hiç göndermemekten kötüdür.
    """
    track = messaging.text(order.get("track"))
    link = messaging.text(order.get("trackUrl"))
    if not link and track and messaging.text(tracking_base):
        link = messaging.text(tracking_base).rstrip("/") + "/" + track
    values = {
        "magaza_adi": messaging.text(store_name),
        "musteri_adi": messaging.text(order.get("customer")),
        "siparis_no": messaging.text(order.get("orderNo")),
        "siparis_tarihi": messaging.text(order.get("date")),
        "tutar": messaging.text(order.get("total")),
        "kargo_firma": messaging.text(order.get("carrier")),
        "kargo_takip": track,
        "kargo_takip_linki": link,
    }
    wanted = STAGE_VARIABLES.get(messaging.text(stage), ())
    return {key: value for key, value in values.items() if key in wanted}


def log_row(row: dict[str, Any]) -> dict[str, Any]:
    """Yerel gönderim izi satırı → ekranın beklediği biçim."""
    result = messaging.text(row.get("result"))
    stage = messaging.text(row.get("stage"))
    return {
        "stage": stage,
        "stageLabel": STAGE_LABELS.get(stage, stage),
        "orderId": messaging.as_int(row.get("order_id")),
        "orderNo": messaging.text(row.get("order_no")),
        "customer": messaging.text(row.get("customer")),
        # NUMARA MASKELİ. Gönderim izi ekranda ve raporda duruyor; müşterinin
        # tam numarası personelin göremesi gereken bir şey değil, "hangi
        # numaraya gitti" sorusunun cevabı son dört hanedir.
        "phone": messaging.mask_secret(row.get("phone")),
        "result": result,
        "resultLabel": RESULT_LABELS.get(result, result or "—"),
        "tone": RESULT_TONES.get(result, ""),
        "sent": result == SENT,
        "note": messaging.text(row.get("note")),
        "parts": messaging.as_int(row.get("parts")),
        "jobId": messaging.text(row.get("job_id")),
        "createdAt": messaging.text(row.get("created_at"))[:19],
        "updatedAt": messaging.text(row.get("updated_at"))[:19],
    }
