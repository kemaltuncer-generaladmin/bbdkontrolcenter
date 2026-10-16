"""Akademi belgeleri — kurum logolu PDF, önizleme, kapı ve Akademi PDF'i.

Akademi taklittir (ağa çıkılmaz). PDF gerçekten reportlab ile çizilir;
`pdftotext` varsa metni okunup başlık/altbilgi/sayfa numarası denetlenir.
Çıktı geçici klasöre yazılır — kullanıcının masaüstüne değil.
"""

from __future__ import annotations

import io
import shutil
import stat
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from modules.bbd_akademi.backend import document_pdf
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.api import routes
from modules.bbd_akademi.backend.core.gateway import AkademiGateway
from modules.bbd_akademi.backend.core.operations import Allowlist
from modules.bbd_akademi.backend.core.proxy import AkademiProxy
from modules.bbd_akademi.backend.core.stepup import StepUpStore
from modules.bbd_akademi.backend.document_pdf import DocumentError, Letterhead, Stamp
from modules.bbd_akademi.backend.documents import (
    AKADEMI_PDFS,
    KINDS,
    DocumentDenied,
    DocumentService,
    Letterheads,
    slug,
)
from modules.bbd_akademi.backend.staff import StaffService
from modules.bbd_akademi.tests.akademi_fakes import (
    BASE,
    Akademi,
    CheckedStore,
    Identity,
    Log,
    auth,
    core_envelope,
    user,
)

IDS = {operation.operation_id for operation in OPERATIONS}


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (120, 40), (8, 145, 178)).save(buffer, format="PNG")
    return buffer.getvalue()


LOGO = _png()


def model(parts: int = 1, **extra: Any) -> dict[str, Any]:
    part = {
        "title": "Öğrenci Karnesi",
        "subtitle": "2025–2026 · 1. Dönem",
        "meta": [["Öğrenci", "Ayşe Yılmaz"], ["Sınıf", "8-A"], ["Veli", "0532 123 45 67"]],
        "sections": [
            {"kind": "tiles", "items": [["Ortalama", "4,2 / 5"], ["Devam", "%96"]]},
            {"kind": "table", "title": "Kriterler", "headers": ["Kriter", "Puan"],
             "align": "LC", "rows": [["Katılım <b>&", "5"], {"cells": ["Ödev", {"text": "2",
                                                                              "tone": "bad"}]}],
             "footer": ["Ortalama", "3,5"]},
            {"kind": "bars", "items": [{"label": "Türkçe", "value": 16.5}]},
            {"kind": "list", "title": "Güçlü yönler", "items": ["Düzenli"]},
            {"kind": "text", "text": "Görüş\nikinci satır"},
            {"kind": "note", "text": "Not"},
            {"kind": "signatures", "items": ["Öğretmen", "Veli"]},
        ],
    }
    return {"title": "Öğrenci Karnesi", "parts": [part] * parts, **extra}


def pdf_text(content: bytes, tmp_path: Path) -> str | None:
    binary = shutil.which("pdftotext")
    if not binary:
        return None
    source = tmp_path / "metin.pdf"
    source.write_bytes(content)
    result = subprocess.run([binary, "-layout", str(source), "-"], capture_output=True,
                            text=True, check=False, timeout=60)
    return result.stdout


# ------------------------------------------------------------- çizici (saf)


def test_maske_telefonu_gizler_son_iki_hane_kalir() -> None:
    masked = document_pdf.mask_phones("Veli 0532 123 45 67 · +90 532 1234567 · no 12345678901")
    assert masked == "Veli 0*** *** ** 67 · 0*** *** ** 67 · no 12345678901"  # 11 hane: no
    assert document_pdf.mask_phones("0*** *** ** 67") == "0*** *** ** 67"


def test_turkce_sayi_ve_para() -> None:
    assert document_pdf.tr_number(1419.5, 1) == "1.419,5"
    assert document_pdf.tr_money(1234567) == "12.345,67 ₺"
    assert document_pdf.tr_money(None) == "—"


@pytest.mark.parametrize(("raw", "message"), [
    ({"title": "", "parts": [{}]}, "Belge adı"),
    ({"title": "X", "orientation": "yan", "parts": [{}]}, "Sayfa yönü"),
    ({"title": "X", "parts": []}, "en az bir"),
    ({"title": "X", "parts": [{"sections": [{"kind": "video"}]}]}, "Bilinmeyen"),
    ({"title": "X", "parts": [{"sections": [{"kind": "table", "headers": []}]}]}, "sütun"),
    ({"title": "X", "parts": [{}] * (document_pdf.LIMITS["parts"] + 1)}, "en çok"),
])
def test_gecersiz_model_anlasilir_hata(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(DocumentError, match=message):
        document_pdf.parse(raw)


def test_cok_buyuk_tablo_reddedilir() -> None:
    rows = [["a"]] * (document_pdf.LIMITS["rows_total"] + 1)
    with pytest.raises(DocumentError, match="çok büyük"):
        document_pdf.parse({"title": "X", "parts": [{"sections": [
            {"kind": "table", "headers": ["A"], "rows": rows}]}]})


def test_pdf_basligi_altbilgisi_ve_parca_sayfa_numarasi(tmp_path: Path) -> None:
    document = document_pdf.parse(model(parts=2))
    content = document_pdf.render(
        document, Letterhead(name="BBD Akademi Kadıköy", detail="Moda Cad. 1", logo=LOGO,
                             accent="#0891B2"),
        Stamp(number="AK-KRN-261013-2045-AB12", issued="13.10.2026 20:45", author="Kemal Tuncer"))
    assert content.startswith(b"%PDF")
    text = pdf_text(content, tmp_path)
    if text is None:
        pytest.skip("pdftotext yok")
    assert "BBD Akademi Kadıköy" in text
    assert "AK-KRN-261013-2045-AB12-001" in text and "AK-KRN-261013-2045-AB12-002" in text
    assert text.count("Sayfa 1 / 1") == 2               # her parça kendi içinde numaralı
    assert "Düzenleyen: Kemal Tuncer" in text
    assert "KVKK" in text
    assert "0532" not in text and "0*** *** ** 67" in text
    assert "Katılım <b>&" in text                        # işaret dili kaçışlandı


def test_yatay_sayfa_ve_logosuz_baslik(tmp_path: Path) -> None:
    raw = model()
    raw["orientation"] = "landscape"
    raw["parts"][0]["watermark"] = "Taslak"
    content = document_pdf.render(document_pdf.parse(raw), Letterhead(logo=b"bozuk"),
                                  Stamp(number="N", issued="x", author="y"))
    assert content.startswith(b"%PDF")
    if shutil.which("pdfinfo"):
        source = tmp_path / "yatay.pdf"
        source.write_bytes(content)
        info = subprocess.run(["pdfinfo", str(source)], capture_output=True, text=True,
                              check=False).stdout
        assert "841.89 x 595.276" in info


SVG_LOGO = (b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" '
            b'viewBox="0 0 120 40"><rect width="120" height="40" rx="6" fill="#0891B2"/>'
            b'<circle cx="20" cy="20" r="12" fill="#ffffff"/></svg>')


def test_svg_logo_vektor_cizilir(tmp_path: Path) -> None:
    """SVG kurum logosu başlığa vektör olarak çizilir (eskiden monograma düşüyordu); PNG yolu aynı."""
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.utils import ImageReader

    assert document_pdf.is_svg(SVG_LOGO) and not document_pdf.is_svg(LOGO)
    assert isinstance(document_pdf.logo_drawable(SVG_LOGO), Drawing)
    assert isinstance(document_pdf.logo_drawable(LOGO), ImageReader)
    assert document_pdf.logo_drawable(b"bozuk") is None
    content = document_pdf.render(document_pdf.parse(model()),
                                  Letterhead(name="BBD Akademi Kadıköy", logo=SVG_LOGO),
                                  Stamp(number="N", issued="x", author="y"))
    assert content.startswith(b"%PDF")
    text = pdf_text(content, tmp_path)
    if text is not None:
        assert "BBD Akademi Kadıköy" in text and "\nBA\n" not in text   # monogram (baş harfler) yok


def test_svg_logo_dis_kaynak_ve_varlik_okumaz(tmp_path: Path) -> None:
    """Güvenlik: DOCTYPE/varlık taşıyan SVG ayrıştırılmaz; dış dosyaya bağlanan <image> çözülmez."""
    secret = tmp_path / "gizli.png"
    secret.write_bytes(LOGO)
    xxe = (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
           b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><text>&x;</text></svg>')
    assert document_pdf.logo_drawable(xxe) is None
    external = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                b'width="40" height="40"><image xlink:href="' + str(secret).encode() + b'" width="40" '
                b'height="40"/></svg>')
    drawing = document_pdf.logo_drawable(external)
    content = document_pdf.render(document_pdf.parse(model()), Letterhead(logo=external),
                                  Stamp(number="N", issued="x", author="y"))
    assert content.startswith(b"%PDF")
    assert drawing is None or not drawing.contents or all(
        type(item).__name__ != "Image" for item in drawing.getContents())


def test_tur_islemleri_izin_listesinde() -> None:
    for table in (KINDS, AKADEMI_PDFS):
        for key, kind in table.items():
            for operation_id in kind.operations:
                assert operation_id in IDS, f"{key}: {operation_id} izin listesinde yok"


def test_kalici_silme_belgesi_silme_iznini_ister(gateway: AkademiGateway, log: Log, tmp_path: Path) -> None:
    """0.5.4: KM verisi taşıyan belge (Akademi işlemi yok) kendi KM iznini ister."""
    service = DocumentService(gateway=gateway, allowlist=Allowlist(OPERATIONS), log=log, fallback_dir=tmp_path)
    kind = KINDS["student_deletion"]
    with pytest.raises(DocumentDenied):
        service.authorize(user("u-9", "Can", "Demir", "bbd_akademi.view"), kind)
    service.authorize(user("u-8", "Ayşe", "Yılmaz", "bbd_students.delete"), kind)
    # Kitap satış belgesi satışı okuyabilen herkese (books.view → bbd_akademi.view).
    service.authorize(user("u-7", "Ece", "Kaya", "bbd_akademi.view"), KINDS["book_sale"])


def test_dosya_adi_ascii() -> None:
    assert slug("Ayşe Yılmaz 8-A / Çağrı") == "ayse-yilmaz-8-a-cagri"
    assert slug("") == "belge"


# ------------------------------------------------------------------- uçlar


def branding_reply(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/admin/settings":
        return httpx.Response(200, json={"name": "BBD Akademi Moda", "address": "Moda Cad. 1",
                                         "support": {"phone": "0216 555 12 34"}})
    if path == "/v1/admin/branding":
        return httpx.Response(200, json={"published": {
            "seedColor": "#0891B2", "light": {"primary": "#0E7490"},
            "logo": {"url": f"{BASE}/files/logo.png", "markUrl": None}}, "draft": None})
    if path == "/files/logo.png":
        return httpx.Response(200, content=LOGO, headers={"content-type": "image/png"})
    if path == "/files/makbuz.pdf":
        return httpx.Response(200, content=MINIMAL_PDF, headers={"content-type": "application/pdf"})
    if path == "/files/sahte.pdf":
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
    return httpx.Response(404, json={"error": {"code": "not_found", "message": "yok"}})


MINIMAL_PDF = document_pdf.render(document_pdf.parse({"title": "M", "parts": [{}]}),
                                  Letterhead(), Stamp(number="N", issued="x", author="y"))


@pytest.fixture
async def app_client(store: CheckedStore, gateway: AkademiGateway, log: Log,
                     staff_service: StaffService, identity: Identity,
                     tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, DocumentService]]:
    allowlist = Allowlist(OPERATIONS)
    proxy = AkademiProxy(gateway=gateway, allowlist=allowlist,
                         step_up=StepUpStore(store, ttl_seconds=120), log=log)
    service = DocumentService(gateway=gateway, allowlist=allowlist, log=log,
                              fallback_dir=tmp_path, folder=lambda kind: tmp_path / kind.folder,
                              letterheads=Letterheads(gateway=gateway, log=log,
                                                      fallback_name="Yedek Kurum"))
    for kind in {*KINDS.values(), *AKADEMI_PDFS.values()}:
        (tmp_path / kind.folder).mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    core_envelope(app)
    app.state.identity = identity
    app.include_router(routes.bind(proxy, staff_service, documents=service),
                       prefix="/api/bbd_akademi")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://km.test") as http:
        yield http, service
    await gateway.close()
    routes._proxy = None
    routes._staff = None
    routes._documents = None


async def test_belge_uretilir_logolu_ve_onizlenir(app_client: Any, akademi: Akademi,
                                                  tmp_path: Path) -> None:
    http, _ = app_client
    akademi.reply(branding_reply)
    response = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("admin"),
                               json={"kind": "report_card", "document": model(
                                   filename="Ayşe Yılmaz 8-A")})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True, data
    path = Path(data["path"])
    assert path.parent == tmp_path / "Karne"
    assert path.name.startswith("karne-ayse-yilmaz-8-a-")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert data["number"].startswith("AK-KRN-")
    if shutil.which("pdftoppm"):
        assert data["pages"] and data["pages"][0].startswith("data:image/png;base64,")
        assert data["pageCount"] >= 1 and data["previewError"] == ""
    # Başlık Akademi'den: kurum adı + logo isteği; ikinci belge önbellekten.
    paths = [call.url.path for call in akademi.calls]
    assert {"/v1/admin/settings", "/v1/admin/branding", "/files/logo.png"} <= set(paths)
    logo_call = next(call for call in akademi.calls if call.url.path == "/files/logo.png")
    assert "authorization" not in logo_call.headers          # imzalı bağlantıya anahtar gitmez
    settings_call = next(call for call in akademi.calls if call.url.path == "/v1/admin/settings")
    assert settings_call.headers["x-akademi-on-behalf-of"].startswith("km:system:")
    text = pdf_text(path.read_bytes(), tmp_path)
    if text is not None:
        assert "BBD Akademi Moda" in text and "0216 555 12 34" in text
    before = len(akademi.calls)
    again = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("admin"),
                            json={"kind": "report_card", "document": model(), "preview": False})
    assert again.json()["ok"] is True and again.json()["pages"] == []
    assert len(akademi.calls) == before                       # başlık önbellekte (A14)
    # Karne değerlendirme verisidir: yalnız görüntüleme izni yetmez (K9).
    denied = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("viewer"),
                             json={"kind": "report_card", "document": model()})
    assert denied.status_code == 403
    assert denied.json()["error"]["details"]["permission"] == "bbd_akademi.evaluations"


async def test_akademi_yokken_belge_yine_cikar(app_client: Any, akademi: Akademi,
                                               tmp_path: Path) -> None:
    http, _ = app_client
    akademi.down = httpx.ConnectError("kapalı")
    response = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("viewer"),
                               json={"kind": "attendance_day", "document": model(),
                                     "preview": False})
    data = response.json()
    assert data["ok"] is True
    text = pdf_text(Path(data["path"]).read_bytes(), tmp_path)
    if text is not None:
        assert "Yedek Kurum" in text


async def test_kurum_carisi_belgesi_finans_izni_ister(app_client: Any, akademi: Akademi) -> None:
    http, _ = app_client
    akademi.reply(branding_reply)
    denied = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("viewer"),
                             json={"kind": "finance_statement", "document": model()})
    assert denied.status_code == 403
    body = denied.json()["error"]
    assert body["code"] == "forbidden" and body["details"]["permission"] == "bbd_akademi.finance"
    allowed = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("cashier"),
                              json={"kind": "finance_statement", "document": model(),
                                    "preview": False})
    assert allowed.json()["ok"] is True


async def test_bilinmeyen_tur_ve_bozuk_model(app_client: Any, akademi: Akademi) -> None:
    http, _ = app_client
    unknown = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("admin"),
                              json={"kind": "gizli_rapor", "document": model()})
    assert unknown.json() == {"ok": False, "error": "Bilinmeyen belge türü: gizli_rapor"}
    broken = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("admin"),
                             json={"kind": "report_card", "document": {"title": "X"}})
    assert broken.json()["ok"] is False and "en az bir" in broken.json()["error"]
    no_session = await http.post("/api/bbd_akademi/km/documents/preview",
                                 json={"kind": "report_card", "document": model()})
    assert no_session.status_code == 401


async def test_akademi_pdfi_alinir_ve_onizlenir(app_client: Any, akademi: Akademi,
                                                tmp_path: Path) -> None:
    http, _ = app_client
    akademi.reply(branding_reply)
    response = await http.post("/api/bbd_akademi/km/documents/akademi", headers=auth("cashier"),
                               json={"kind": "finance_receipt", "url": f"{BASE}/files/makbuz.pdf",
                                     "name": "BBD-000123"})
    data = response.json()
    assert data["ok"] is True, data
    path = Path(data["path"])
    assert path.parent == tmp_path / "Kurum cari" and path.read_bytes().startswith(b"%PDF")
    assert path.name.startswith("makbuz-bbd-000123-")
    # PDF olmayan yanıt ve izinsiz köken reddedilir.
    fake = await http.post("/api/bbd_akademi/km/documents/akademi", headers=auth("cashier"),
                           json={"kind": "finance_receipt", "url": f"{BASE}/files/sahte.pdf"})
    assert fake.json() == {"ok": False, "error": "Akademi'den gelen dosya PDF değil."}
    foreign = await http.post("/api/bbd_akademi/km/documents/akademi", headers=auth("cashier"),
                              json={"kind": "finance_receipt", "url": "https://kotu.example/x.pdf"})
    assert foreign.json()["ok"] is False
    assert not any(call.url.host == "kotu.example" for call in akademi.calls)
    # Makbuz kurum carisidir: yalnız görüntüleme izni yetmez.
    denied = await http.post("/api/bbd_akademi/km/documents/akademi", headers=auth("viewer"),
                             json={"kind": "finance_receipt", "url": f"{BASE}/files/makbuz.pdf"})
    assert denied.status_code == 403
