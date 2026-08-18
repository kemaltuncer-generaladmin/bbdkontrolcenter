"""Üretilmiş çıktının BAYTLARINI veren uç — yerel yazdırma için.

NEDEN VAR. Baskı artık kullanıcının makinesinde yapılıyor (bkz. kabuk
`src/printing.rs`): çekirdek sunucuda koşuyor ve sunucu imajında CUPS yok,
yazıcılar ise kullanıcının masasında. PDF sunucunun diskinde üretiliyor;
kabuğun onu basabilmesi için önce ELİNE ALMASI gerekiyor.

Önizleme ucu yetmiyordu: o, sayfaların PNG görüntülerini veriyor. Görüntü
basmak metni rasterleştirir, dosyayı şişirir ve seçilebilir metni yok eder —
basılan şey üretilen belgenin kendisi olmalı.

YOL SERBEST DEĞİLDİR. Yalnız rapor kökü altındaki dosyalar verilir; serbest
yol kabul etmek, oturumu olan herkese sunucudaki herhangi bir dosyayı okutmak
olurdu. Denetim modül servislerindeki `print_report` ile aynıdır (`resolve()`
sonrası önek kontrolü) ve sembolik bağ ile dışarı çıkmayı da kapatır.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from km_core.config.paths import data_dir
from km_core.files.outputs import EXPORTS_SEGMENT, output_roots
from km_core.http.deps import requires
from km_core.security.identity import CurrentUser

#: Kabuğa gövdeyle taşınabilecek en büyük belge. Rapor birkaç yüz kilobayttır;
#: bundan büyüğü ya yanlış dosyadır ya da base64 ile üçe katlanıp kabuğu boğar.
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024

#: Yalnız basılabilir belgeler. Kabuk bunları yazıcıya veriyor; başka bir tür
#: istemek "dosya oku" ucuna dönüşmek demekti.
ALLOWED_SUFFIXES = (".pdf",)


class DocumentBody(BaseModel):
    path: str = Field(default="", max_length=4096)


def _configured_paths(config: Any) -> list[str]:
    """Ayarlarda elle verilmiş çıktı klasörleri.

    İKİ YERDEN GELİR ve ikisi de kâğıda çıkabilen dosya üretir:
      · `files.output_path` — çekirdeğin çıktıları (ör. yazıcı test sayfası)
      · `modules.<id>.export_path` — o modülün raporları

    Kullanıcı çıktıyı başka bir klasöre yönlendirmişse (`report_dir` →
    `configured`) oraya yazılan rapor da yazdırılabilmeli; yoksa ayarı
    değiştiren kullanıcının bütün baskıları sessizce ölürdü.

    MODÜL ADI OKUNMAZ (K1). `modules` bloğunun altındaki HER kayıt gezilir ve
    yalnız anahtarın adına bakılır; burada hangi modüllerin var olduğu bilgisi
    yok. Modül silinse de bu kod aynı çalışır.
    """
    found: list[str] = []

    core_path = config.get("files.output_path")
    if isinstance(core_path, str) and core_path.strip():
        found.append(core_path)

    try:
        blok = config.section("modules")
    except Exception:  # noqa: BLE001 — ayar okunamazsa kapı dar kalsın, çökmesin
        return found
    if not isinstance(blok, dict):
        return found
    for entry in blok.values():
        if isinstance(entry, dict):
            value = entry.get("export_path")
            if isinstance(value, str) and value.strip():
                found.append(value)
    return found


def create_documents_router() -> APIRouter:
    router = APIRouter(tags=["documents"])

    @router.post("/outputs/document")
    async def document(
        body: DocumentBody,
        request: Request,
        # `outputs.print` = "elindeki belgeyi yazıcıya verebilir" (çekirdek
        # izni, bkz. `km_core/security/permissions.py`). Eski iki anahtar
        # LİSTEDE KALIYOR: izinleri elle düzenlenmiş kurulumlarda yalnız onlara
        # sahip kullanıcılar var ve yeni anahtar dağıtılana kadar baskıları
        # kesilmesin. Kapı en az birini ister (`requires` "herhangi biri").
        user: CurrentUser = requires("outputs.print", "print.view", "settings.view"),
    ) -> dict[str, Any]:
        """Rapor kökündeki bir PDF'i base64 olarak döndürür."""
        try:
            resolved = Path(body.path).expanduser().resolve(strict=True)
        except OSError:
            raise HTTPException(status_code=404, detail="Belge bulunamadı.") from None

        # KÖKLER İSTEK ANINDA ÇÖZÜLÜR. Router kurulurken `app.state.config`
        # henüz yok (uygulama yaşam döngüsü onu sonra bağlıyor); kurulumda
        # okumak testlerde `AttributeError` üretiyordu.
        #
        # TEK KÖK YETMİYOR: çıktıyı yazan taraf ile burası kökü ayrı ayrı
        # hesaplıyordu ve kurulu sistemde farklı yer gösteriyorlardı — gerekçe
        # `km_core/files/outputs.py` → `output_roots`. Belirti, uygulamanın
        # kendi ürettiği raporda "bu dosya rapor klasöründe değil" demekti.
        config = getattr(request.app.state, "config", None)
        if config is not None:
            roots = output_roots(
                config.root,
                data_dir(config.root) / EXPORTS_SEGMENT,
                _configured_paths(config),
            )
        else:
            roots = [(Path.home() / "km-raporlar").resolve()]

        # `is_relative_to` sembolik bağ çözüldükten SONRA uygulanır: kök içine
        # konmuş bir bağ, dışarıyı işaret etse bile buradan geçemez.
        if not any(resolved.is_relative_to(entry) for entry in roots):
            raise HTTPException(
                status_code=403,
                detail="Bu dosya çıktı klasörlerinde değil; güvenlik gereği verilmez.",
            )
        if resolved.suffix.lower() not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=415, detail="Yalnız PDF belgeler verilir.")

        size = resolved.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Belge yazdırma için fazla büyük ({size // 1024 // 1024} MB).",
            )

        raw = resolved.read_bytes()
        return {
            "ok": True,
            "name": resolved.name,
            "bytes": size,
            "data": base64.b64encode(raw).decode("ascii"),
        }

    return router
