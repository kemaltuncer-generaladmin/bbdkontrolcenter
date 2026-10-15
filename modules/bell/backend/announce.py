"""`bell.announce` yeteneği — öğrenci çağırma (mobil geçit sözleşmesi §5).

    await page(text, *, cache_key=None, actor=None, request_id=None)
        → {"status": "queued" | "disabled" | "tts_unavailable" | "failed",
           "voiceCached": bool, "etaSeconds": int, "detail": str
           [, "replayed": True]}

HATA YÜKSELTMEZ: modül sınırını exception geçmez, sonuç `status` ile döner.
`failed` sözleşmedeki üç duruma EK olarak vardır: ses hazır ama köprü de yerel
çalma da reddetti. `detail` Türkçe, insan içindir.

Yardımcılar (aynı nesnede):

    await page_template() → str           ayardaki `texts.page`
    render(template, first_name=, last_name=, class_name=) → str
    await prepare(text, *, cache_key=None, bulk=False)
        → {"status": "cached"|"queued"|"disabled"}
          `bulk=True`: toplu ön hazırlık (Akademi şablon sesleri) — olağan
          seslerin arkasından üretilir, köprüye önden gitmez (010 göçü).
    await preview(text) → {"status": "ready", "voiceCached": bool, "dataUri": str}
                         | {"status": "tts_unavailable", "detail": str}
          Sesi hazırlar (yoksa anlık üretir) ve ÇALMADAN döner — serbest duyuru
          önizlemesi. Üretilen ses saklanır; ardından `page` aynı metni
          Vertex'e gitmeden çalar.

Metni ÇAĞIRAN kurar (`render` ile) — bell öğrenci kaydını bilmez (K3/K5).
"""

from __future__ import annotations

from typing import Any

from .service import BellService, render_page


class BellAnnounce:
    def __init__(self, service: BellService) -> None:
        self._service = service

    async def page(self, text: str, *, cache_key: str | None = None,
                   actor: str | None = None,
                   request_id: str | None = None) -> dict[str, Any]:
        try:
            return await self._service.page(text, cache_key=cache_key, actor=actor,
                                            request_id=request_id)
        except Exception as failure:  # noqa: BLE001 — modül sınırını hata geçmez (sözleşme)
            return {"status": "failed", "voiceCached": False, "etaSeconds": 0,
                    "detail": f"Anons yapılamadı: {failure}"}

    async def page_template(self) -> str:
        return await self._service.page_template()

    @staticmethod
    def render(template: str, *, first_name: str = "", last_name: str = "",
               class_name: str = "") -> str:
        return render_page(template, first_name=first_name, last_name=last_name,
                           class_name=class_name)

    async def prepare(self, text: str, *, cache_key: str | None = None,
                      bulk: bool = False) -> dict[str, Any]:
        try:
            return await self._service.prepare_page(text, cache_key=cache_key, bulk=bulk)
        except Exception as failure:  # noqa: BLE001 — modül sınırını hata geçmez
            return {"status": "disabled", "detail": str(failure)}

    async def preview(self, text: str) -> dict[str, Any]:
        try:
            return await self._service.preview_page(text)
        except Exception as failure:  # noqa: BLE001 — modül sınırını hata geçmez
            return {"status": "tts_unavailable", "voiceCached": False,
                    "detail": f"Ses hazırlanamadı: {failure}"}
