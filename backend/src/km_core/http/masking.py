"""Tanılama çıktısında maskeleme.

DESTEK PAKETİ DIŞARI ÇIKAR. Kullanıcı onu e-postayla, WhatsApp'la ya da bir
hata kaydına ekleyerek gönderir; gittiği yerin güvenli olduğu varsayılamaz.
Oysa günlüğün içinde bu depoda gerçekten bulunan şeyler var: sunucu adresleri,
`Bearer` başlıkları, veli cep telefonları, e-posta adresleri.

Bu yüzden maskeleme İSTEĞE BAĞLI DEĞİLDİR (K8). Paket üretilirken metin bu
süzgeçten geçer; süzgeç çalışmazsa paket de üretilmez.

MASKELEME ANLAMI KORUR. Tümünü yıldıza çevirmek günlüğü okunamaz hâle
getirirdi ve destek isteyen kişi hiçbir şey anlatamazdı. Kural: BİÇİM kalır,
İÇERİK gider — `https://api.ornek.com/v1/x` → `https://<sunucu>/v1/x`,
`5321234567` → `532****567`, `1|3Eja…6X` → `1|3E…⟨gizlendi⟩`.

DÖNGÜ ADRESİ MASKELENMEZ. `127.0.0.1` ve `localhost` bir sır değil, bu
uygulamanın mimarisidir (ADR 0002); maskelemek "çekirdeğe bağlanılamadı"
satırını anlamsız kılardı.
"""

from __future__ import annotations

import re
from typing import Any

#: Maskelenmiş parçaların yerine konan işaretler — gözle ayırt edilebilir
#: olmalı ki okuyan "burada bir şey vardı" desin.
HOST_MARK = "<sunucu>"
SECRET_MARK = "⟨gizlendi⟩"

#: Maskelenmeyecek adresler. Yerel döngü ve `.local` bir sunucu kimliği vermez.
_KEEP_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}

# --- şema taşıyan adres: şema ve yol kalır, konak gider ---------------------
_URL = re.compile(r"\b([a-z][a-z0-9+.-]*)://([^\s/?#\"']+)", re.IGNORECASE)

# --- e-posta: yerel kısmın ilk harfi kalır ---------------------------------
_EMAIL = re.compile(r"\b([A-Za-z0-9])[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# --- açıkça adı konmuş sırlar: `token=…`, `"password": "…"`, `Bearer …` -----
#
# AYRAÇ ZORUNLU (`:` ya da `=`). Yalnız boşluk da kabul edilseydi "token
# bulunamadı" gibi sıradan bir günlük satırının yarısı maskelenir ve tanılama
# okunamaz hâle gelirdi. `Bearer` bu yüzden ayrı bir desendir: onda ayraç yok.
_LABELLED = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|apikey|authorization|pin|"
    r"private[_-]?key|access[_-]?token|refresh[_-]?token)"
    r"(\s*[:=]\s*)"
    r"(\"|')?([^\s\"',;)]{4,})"
)
_BEARER = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=|-]{8,})")

# --- imzasız uzun jeton: 24+ karakterlik gövde. Kelimeler ve tarihler
#     yakalanmasın diye en az bir rakam VE bir harf aranır. Depodaki gerçek
#     sırların en kısası 42 karakter; eşik onun altında ama sıradan
#     tanımlayıcıların üstünde duracak şekilde seçildi.
_TOKEN = re.compile(r"\b(?=[A-Za-z0-9+/=_|-]*\d)(?=[A-Za-z0-9+/=_|-]*[A-Za-z])"
                    r"[A-Za-z0-9+/=_|-]{24,}\b")

# --- PEM blokları: gövdesi tümüyle gider ------------------------------------
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL)

# --- telefon: 10–13 haneli diziler (araya boşluk/tire girmiş olabilir) -------
_PHONE = re.compile(r"(?<![\d.])(\+?9?0?[\s.-]?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2})(?![\d.])")

# --- alan adı: `bbdstore.com.tr` gibi şemasız konaklar ----------------------
_BARE_HOST = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|net|org|edu|gov|io|dev|app|tr|de|co|info|biz|xyz|local)"
    r"(?:\.[a-z]{2})?\b",
    re.IGNORECASE,
)

# --- IPv4: döngü dışındaki her adres ---------------------------------------
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _hide(text: str, keep: int = 2) -> str:
    """Baştan `keep` karakter kalır, gerisi tek bir işarete iner."""
    body = text.strip()
    if len(body) <= keep:
        return SECRET_MARK
    return f"{body[:keep]}…{SECRET_MARK}"


def _mask_labelled(match: re.Match[str]) -> str:
    """`token=…` kalıbı. ZATEN MASKELENMİŞ DEĞERE İKİNCİ KEZ DOKUNULMAZ.

    `private_key: -----BEGIN …` satırında blok bir üstteki adımda gitmiştir;
    kalan `-----BEGIN` işareti burada da maskelenseydi çıktı
    `private_key: --…⟨gizlendi⟩` olurdu ve okuyan "burada ne vardı" sorusunu
    yanıtlayamazdı. Maskelemenin amacı içeriği almak, satırı silmek değil.
    """
    value = match.group(4)
    if SECRET_MARK in value or HOST_MARK in value or value.startswith("-----"):
        return match.group(0)
    return f"{match.group(1)}{match.group(2)}{match.group(3) or ''}{_hide(value)}"


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(1))
    if len(digits) < 10:
        return match.group(0)
    return f"{digits[:3]}****{digits[-3:]}"


def mask_text(text: str) -> str:
    """Metni maskeler. Sıra önemlidir: en özgül desen en önce çalışır."""
    if not text:
        return text

    # 1. PEM blokları bütün hâlde gider; içindeki base64 satırları tek tek
    #    yakalanmaya bırakılırsa dosya devasa büyür ve okunmaz olur.
    masked = _PEM.sub(f"-----BEGIN … {SECRET_MARK} … END-----", text)

    # 2. Adı konmuş sırlar. `Bearer` ayrı: araya iki nokta girmez.
    masked = _BEARER.sub(lambda m: f"Bearer {_hide(m.group(1))}", masked)
    masked = _LABELLED.sub(_mask_labelled, masked)

    # 3. Adresler. Şemalı olan önce: yolu korumak istiyoruz.
    masked = _URL.sub(lambda m: _mask_url(m), masked)

    # 4. E-posta, alan adı, IP.
    masked = _EMAIL.sub(lambda m: f"{m.group(1)}***@{HOST_MARK}", masked)
    masked = _BARE_HOST.sub(HOST_MARK, masked)
    masked = _IPV4.sub(lambda m: m.group(0) if m.group(0) in _KEEP_HOSTS else HOST_MARK, masked)

    # 5. Telefon ve artakalan uzun jetonlar.
    masked = _PHONE.sub(_mask_phone, masked)
    return _TOKEN.sub(lambda m: _hide(m.group(0), keep=4), masked)


def _mask_url(match: re.Match[str]) -> str:
    scheme, host = match.group(1), match.group(2)
    bare = host.split("@")[-1].split(":")[0]
    if bare in _KEEP_HOSTS:
        # Port bilgisi kalır: "127.0.0.1:8787 yanıt vermiyor" tanılamanın ta
        # kendisidir ve bir sır taşımaz.
        return match.group(0)
    return f"{scheme}://{HOST_MARK}"


def mask_value(value: Any) -> Any:
    """Sözlük/liste ağacını olduğu gibi dolaşıp metinleri maskeler."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {key: mask_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_value(item) for item in value]
    return value
