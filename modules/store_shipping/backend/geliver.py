"""Geliver kurulum ekranının saf kuralları — ağa çıkmaz, durum tutmaz.

NEDEN AYRI DOSYA. Bu ekranın işi tek bir sırrı (API tokenı) ve mağazanın
müşteriye açık olup olmadığını yöneten üç bayrağı düzenlemek. Kuralların
tamamı girdi→çıktı fonksiyonu olarak burada durur; testler ağ, veritabanı ve
mağaza olmadan çalışır — bir sırra dokunan kodun doğruluğu, uzak bir sistemin
ayakta olmasına bağlı olamaz.

BEŞ KURAL — hepsinin karşılığı burada bir fonksiyondur:

 1. TOKEN GERİ OKUNAMAZ. Mağaza tokenı hiçbir gövdede döndürmüyor; ekran
    yalnız `hasToken` bayrağını ve son dört karakterlik maskeyi görür
    (`token_state`). Maskenin kendisi TOKEN DEĞİLDİR ve geri gönderilemez —
    `token_error` maske biçimli değeri açıkça reddeder.
 2. BOŞ TOKEN "DOKUNMA" DEMEKTİR, "SİL" DEMEZ. `settings_patch` boş token
    alanını gövdeye HİÇ koymaz; koysaydı "test modunu kapat" isteği
    mağazanın kargo kimliğini silerdi ve belirtisi ancak ilk gerçek siparişte
    görünürdü.
 3. CANLIYA GEÇİŞ ENGELİ UYARI DEĞİLDİR. `go_live` açılırken sunucunun ön
    denetimi reddederse `blocker_lines` nedenleri OLDUĞU GİBİ gösterir;
    ekran kendi tahminini sunucunun cevabının yerine koymaz.
 4. DEĞİŞEN ALAN YOKSA İSTEK ÇIKMAZ. `settings_patch` yalnız GERÇEKTEN
    değişen alanları döndürür: her kaydetmede beş alanı birden yazmak,
    denetim defterini anlamsız satırlarla doldurur ve iki ekranın aynı anda
    yazdığı değeri sessizce ezer.
 5. "AÇIK" TEK BİR BAYRAK DEĞİLDİR. Müşterinin Geliver fiyatını görmesi için
    token + `active` + `go_live` üçünün birden açık olması gerekir
    (`visibility_line`); "panelde açık ama vitrinde yok" arızasının teşhisi
    budur.
"""

from __future__ import annotations

from typing import Any

#: Mağazanın YAZILABİLİR saydığı alanlar. Listede olmayan bir ad gönderilirse
#: mağaza 422 döner (sessizce yok saymaz); ekran o hataya hiç düşmesin diye
#: aynı liste burada da tutulur.
WRITABLE = ("api_token", "active", "go_live", "test_mode", "sender_address_id")

#: Tokenın kabul edilen uzunluk aralığı — mağaza tarafıyla AYNI
#: (`GeliverConfig::TOKEN_MIN_LENGTH` / `TOKEN_MAX_LENGTH`). Geliver tokenı 36
#: karakterdir; aralık daha geniş tutuldu ki sağlayıcı biçim değiştirdiğinde
#: ekran doğru tokenı reddetmesin.
TOKEN_MIN = 16
TOKEN_MAX = 512

#: Gönderici adres kimliğinin üst sınırı (Geliver UUID kullanıyor).
SENDER_ID_MAX = 64

#: Şifreli değerin öneki. Ekran bunu ASLA üretmez; kullanıcı bir yerden
#: kopyalayıp yapıştırırsa yakalanır.
ENCRYPTED_PREFIX = "enc:v1:"

#: Maskenin sabit öneki (`GeliverConfig::MASK_PREFIX`). Maske TOKEN DEĞİLDİR.
MASK_PREFIX = "****"

#: Ön denetim engel kodları → ekranda ne yapılacağını söyleyen metin.
#:
#: MESAJIN KENDİSİ SUNUCUDAN GELİR ve olduğu gibi gösterilir; buradaki metin
#: ONA EK olarak "sıradaki adım" söyler. İkisini karıştırmamak önemli:
#: sunucunun cevabını kendi cümlemizle değiştirmek, gerçek sebebi gizler.
BLOCKER_ACTIONS = {
    "TOKEN_MISSING": "Geliver panelinden API tokenını kopyalayıp yukarıdaki kutuya yapıştırın.",
    "NOT_ACTIVE": "Önce “Entegrasyon kurulu” anahtarını açın; ikisi birlikte çalışır.",
    "API_UNREACHABLE": "“Bağlantıyı sına” ile yeniden deneyin. Sürerse token yanlış ya da "
                       "hesap kapalı olabilir.",
    "SENDER_ADDRESS_UNRESOLVED": "Geliver panelinden bir çıkış (gönderici) adresi tanımlayın "
                                 "ya da adres kimliğini aşağıya yazın.",
}


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def flag(value: Any) -> bool:
    """Mağaza bayrakları true/false, 0/1 ya da "0"/"1" olarak gelebiliyor."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return text(value).lower() in ("1", "true", "yes", "on")


# ============================================================== token kuralı

def token_error(value: str) -> str:
    """Token kaydedilebilir mi — kullanıcıya gösterilecek metin ("" = kabul).

    BOŞ DEĞER HATA DEĞİLDİR: "dokunma" demektir ve `settings_patch` onu
    gövdeye hiç koymaz. Bu ayrım burada yapılmasaydı, boş kutuyla kaydedilen
    her form tokenı silmeye çalışırdı.
    """
    token = text(value)
    if not token:
        return ""
    if token.startswith(MASK_PREFIX):
        return ("Bu bir MASKE, token değil: ekran tokenın yalnız son dört karakterini "
                "gösterir. Değiştirmek için Geliver panelindeki tokenın TAMAMINI yapıştırın; "
                "değiştirmeyecekseniz kutuyu boş bırakın.")
    if token.startswith(ENCRYPTED_PREFIX):
        return ("Şifreli değer gönderilemez; kutuya Geliver panelindeki DÜZ METİN tokenı "
                "yazın.")
    if any(char.isspace() for char in token):
        return "Token boşluk içeremez; tek satırlık bir metindir. Kopyalarken satır sonu almış olabilirsiniz."
    if not TOKEN_MIN <= len(token) <= TOKEN_MAX:
        return f"Token {TOKEN_MIN}–{TOKEN_MAX} karakter olmalı; girilen {len(token)} karakter."
    return ""


def sender_error(value: str) -> str:
    """Gönderici adres kimliği kabul edilebilir mi.

    BOŞ MEŞRUDUR ve tokenın aksine YAZILIR: boş `sender_address_id`
    "hesabın varsayılan gönderici adresini kullan" demektir — bir sırrın
    silinmesi değil, geçerli bir seçimdir.
    """
    value = text(value)
    if not value:
        return ""
    if len(value) > SENDER_ID_MAX:
        return f"Adres kimliği en fazla {SENDER_ID_MAX} karakter olabilir."
    if not all(char.isalnum() or char in "-_" for char in value):
        return ("Adres kimliği yalnız harf, rakam, `-` ve `_` içerebilir. Buraya adresin "
                "kendisi değil, Geliver'daki KİMLİĞİ yazılır.")
    return ""


def token_state(payload: Any) -> dict[str, Any]:
    """Tokenın ekranda gösterilecek hâli. DEĞER YOK, yalnız varlık ve maske."""
    row = payload if isinstance(payload, dict) else {}
    has = flag(row.get("hasToken"))
    mask = text(row.get("tokenMask"))
    return {
        "hasToken": has,
        "mask": mask,
        "label": (mask or "girilmiş") if has else "girilmemiş",
    }


# ============================================================== yazma gövdesi

def settings_patch(current: Any, draft: Any) -> dict[str, Any]:
    """Yalnız GERÇEKTEN değişen alanların gövdesi (KURAL 2 ve 4).

    `current` mağazadan okunan durum, `draft` ekrandaki form. Token AYRI ele
    alınır: `draft` içinde dolu geldiyse yazılır, boşsa gövdeye HİÇ KONMAZ.
    Diğer alanlar mevcut değerden farklıysa konur.

    Mağaza `core_config` satırlarını metin olarak tutuyor ama bu uç JSON
    `true`/`false` kabul ediyor ve kendi normalleştirmesini yapıyor; burada
    Python `bool` gönderilir, `"1"`/`"0"` değil — iki tarafta iki ayrı biçim
    kuralı tutmak, birinin değişip diğerinin unutulmasıyla biterdi.
    """
    now = current if isinstance(current, dict) else {}
    form = draft if isinstance(draft, dict) else {}
    patch: dict[str, Any] = {}

    token = text(form.get("apiToken"))
    if token:
        patch["api_token"] = token

    for key, field in (("active", "active"), ("goLive", "go_live"),
                       ("testMode", "test_mode")):
        if key not in form:
            continue
        wanted = flag(form.get(key))
        if wanted != flag(now.get(field)):
            patch[field] = wanted

    if "senderAddressId" in form:
        wanted = text(form.get("senderAddressId"))
        if wanted != text(now.get("sender_address_id")):
            patch["sender_address_id"] = wanted

    return patch


def patch_labels(patch: Any) -> list[str]:
    """Gövdedeki alanların insan diliyle karşılığı — onay kutusunda bu yazar."""
    names = {
        "api_token": "API tokenı",
        "active": "entegrasyon kurulu",
        "go_live": "müşteriye açık (canlı)",
        "test_mode": "test modu",
        "sender_address_id": "gönderici adres kimliği",
    }
    out: list[str] = []
    for key, value in (patch or {}).items():
        label = names.get(key, key)
        if key == "api_token":
            # DEĞER YAZILMAZ. Onay metni ekranda durur, ekran görüntüsü
            # alınabilir ve destek kaydına yapıştırılabilir.
            out.append(f"{label} (yeni değer)")
        elif isinstance(value, bool):
            out.append(f"{label}: {'açık' if value else 'kapalı'}")
        else:
            out.append(f"{label}: {text(value) or '(boş — hesabın varsayılanı)'}")
    return out


# ================================================================ durum


def visibility_line(payload: Any) -> dict[str, Any]:
    """"Müşteri şu an ne görüyor" — tek cümle + ton (KURAL 5).

    Cümlenin kendisi mağazadan gelir (`statusLabel`): kapalı olmanın üç ayrı
    nedeni var ve her birinde yapılacak iş farklı. Burada yalnız TON eklenir;
    metni yeniden yazmak, iki yerde iki farklı gerçek anlatılması demekti.
    """
    row = payload if isinstance(payload, dict) else {}
    visible = flag(row.get("visibleToCustomer"))
    test_mode = flag(row.get("testMode") if "testMode" in row else row.get("test_mode"))
    label = text(row.get("statusLabel")) or (
        "Durum okunamadı; mağazaya ulaşılamıyor olabilir.")
    if not visible:
        tone = "dim"
    elif test_mode:
        tone = "warn"
    else:
        tone = "good"
    return {"visible": visible, "testMode": test_mode, "label": label, "tone": tone}


def check_lines(payload: Any) -> list[dict[str, Any]]:
    """Geliver'a sorulan iki sorunun cevabı, ekranın anlayacağı biçimde.

    `None` = DENETLENMEDİ ve "geçti" SAYILMAZ. Denetlenmemiş bir koşulu yeşil
    göstermek, operatöre olmayan bir güvence verirdi; mağaza tarafı da aynı
    ayrımı yapıyor (`apiReachable !== true`).
    """
    row = payload if isinstance(payload, dict) else {}
    checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    address = checks.get("senderAddress") if isinstance(checks.get("senderAddress"), dict) else {}

    def state(value: Any) -> str:
        if value is True:
            return "ok"
        if value is False:
            return "fail"
        return "unknown"

    api = state(checks.get("apiReachable"))
    sender = state(checks.get("senderResolved"))

    lines = [
        {"key": "api", "label": "Geliver API erişimi", "state": api,
         "detail": text(checks.get("apiError")) or {
             "ok": "Token geçerli, il listesi okundu.",
             "fail": "Okuma çağrısı başarısız.",
             "unknown": "Denetlenmedi — token yoksa ağa hiç çıkılmaz.",
         }[api]},
        {"key": "sender", "label": "Gönderici (çıkış) adresi", "state": sender,
         "detail": _sender_detail(sender, address)},
    ]
    return lines


def _sender_detail(state: str, address: dict[str, Any]) -> str:
    if state != "ok":
        return {
            "fail": "Çözülemedi: ayarda adres kimliği yok ve hesapta varsayılan gönderici "
                    "adres bulunamadı. Adres olmadan gönderi oluşturulamaz.",
            "unknown": "Denetlenmedi.",
        }[state]
    parts = [text(address.get("name")), text(address.get("cityName")),
             text(address.get("districtName"))]
    who = " · ".join([part for part in parts if part]) or text(address.get("id")) or "adres bulundu"
    # POSTA KODU VE TELEFON AYRI SÖYLENİR: gönderi oluşturma ikisi eksikken
    # reddedilebiliyor ve bu, "adres var" cevabıyla gizlenmemeli.
    missing = []
    if not flag(address.get("hasPostcode")):
        missing.append("posta kodu")
    if not flag(address.get("hasPhone")):
        missing.append("telefon")
    if missing:
        return f"{who} — eksik: {', '.join(missing)}. Gönderi oluşturma reddedilebilir."
    return who


def blocker_lines(payload: Any) -> list[dict[str, Any]]:
    """Canlıya geçişi engelleyen nedenler + sıradaki adım (KURAL 3).

    Mağazanın cevabı OLDUĞU GİBİ taşınır; `action` yalnız ek bir cümledir.
    Bilinmeyen bir kod gelirse eylem boş kalır ve mesaj yine gösterilir —
    tanımadığımız bir engeli listeden düşürmek, ekranı "her şey hazır" der
    hâle getirirdi.
    """
    row = payload if isinstance(payload, dict) else {}
    items = row.get("goLiveBlockers")
    if not isinstance(items, list):
        items = row.get("blockers") if isinstance(row.get("blockers"), list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = text(item.get("code"))
        out.append({"code": code, "message": text(item.get("message")),
                    "action": BLOCKER_ACTIONS.get(code, "")})
    return out


def webhook_line(payload: Any) -> dict[str, Any]:
    """Webhook kayıtlı mı — kayıtlı değilse NE OLDUĞU söylenir.

    Kayıtsız webhook sessiz bir arızadır: gönderi oluşur, kargo yola çıkar,
    ama durum hiç güncellenmez ve ekranda her şey "etiket oluşturuldu"da
    donar. "Elle senkron" düğmesi bunu kapatır ama kimse basmayı hatırlamaz.
    """
    row = payload if isinstance(payload, dict) else {}
    hook = row.get("webhook") if isinstance(row.get("webhook"), dict) else {}
    if not flag(hook.get("checked")):
        return {"state": "unknown", "url": "",
                "message": "Webhook durumu denetlenmedi.", "urls": []}
    error = text(hook.get("error"))
    if error:
        return {"state": "unknown", "url": text(hook.get("defaultUrl")),
                "message": f"Webhook listesi okunamadı: {error}", "urls": []}
    urls = [text(item) for item in (hook.get("registered") or []) if text(item)]
    default = text(hook.get("defaultUrl"))
    registered = flag(hook.get("defaultRegistered")) or (default and default in urls)
    if registered:
        return {"state": "ok", "url": default, "urls": urls,
                "message": "Webhook kayıtlı; gönderi durumu kendiliğinden güncellenir."}
    return {"state": "missing", "url": default, "urls": urls,
            "message": "Webhook Geliver hesabına KAYITLI DEĞİL. Gönderi durumu "
                       "kendiliğinden güncellenmez; her gönderi “etiket oluşturuldu” "
                       "durumunda kalır ve ancak elle senkronla ilerler."}


def draft_problems(draft: Any) -> list[str]:
    """Form kaydedilmeden önce YEREL doğrulama.

    Sunucu aynı kuralları tekrar uygular (K9 — arayüzde engellemek
    yetkilendirme değildir); buradaki denetim bir ağ turunu ve hız kovasından
    bir payı boşa harcamamak içindir.
    """
    form = draft if isinstance(draft, dict) else {}
    problems = []
    for value in (token_error(text(form.get("apiToken"))),
                  sender_error(text(form.get("senderAddressId")))):
        if value:
            problems.append(value)
    # `active` GÖNDERİLMEDİYSE karışılmaz: gövdede olmayan alan "dokunma"
    # demektir ve mağazadaki mevcut değeri bilmiyoruz. Yoksaydığımızı kapalı
    # saymak, zaten kurulu bir mağazada canlıya geçişi yanlışlıkla engellerdi;
    # gerçek denetim zaten sunucuda, Geliver'a sorularak yapılıyor.
    if flag(form.get("goLive")) and "active" in form and not flag(form.get("active")):
        problems.append("“Müşteriye açık” tek başına hiçbir şey göstermez: “Entegrasyon "
                        "kurulu” anahtarı da açık olmalı.")
    return problems
