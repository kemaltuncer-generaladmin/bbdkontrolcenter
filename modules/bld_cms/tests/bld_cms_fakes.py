"""Testlerin sahte bağlamı. AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ.

`FakeStore` SQL'i AYRIŞTIRMAZ; servisin yazdığı iki ifadeyi (denetim satırı ve
sürüm satırı) tanıyacak kadarını yapar ve okumada süzgeçleri UYGULAMAZ. Amaç
çekirdek depoyu taklit etmek değil, servisin DOĞRU ANDA DOĞRU SATIRI yazdığını
görmek — özellikle `result="denendi"` izinin geçit çağrısından ÖNCE düşmesini
ve sürüm satırının kuru provada HİÇ yazılmamasını.

`FakeApi` `bld.api` yeteneğinin testlik yüzüdür. `.calls` her çağrıyı sırasıyla
tutar: "kuru provada uzağa çağrı gitmedi" ve "her yazmada `dry_run` AÇIKÇA
geçildi" iddiaları ancak bu liste okunarak kanıtlanabilir. `.fail` kümesine bir
metot adı atılırsa o metot patlar ve K7 (geçit düşerse ekran ayakta kalır)
sınanır.

Fixtures cms.md'nin ÖRNEK gövdelerinden kopyalanmıştır. Modülün kendi
uydurduğu bir gövdeye karşı geçen test hiçbir şey kanıtlamaz.
"""

from __future__ import annotations

import json
from typing import Any

#: `GET /content` yanıtı — cms.md örneğinden. Yedi anahtarın hepsi var, üçü
#: kayıtsız (boş değer + `updated_at: null`): "kaydı olmayan anahtar da döner".
CONTENT: dict[str, Any] = {
    "data": {
        "brand": {
            "value": {"name": "BLD Catering", "tagline": "Kurumsal mutfak çözümleri"},
            "updated_at": "2026-08-02T10:00:00Z",
        },
        "contact": {
            "value": {
                "phone": "3124445566",
                "email": "info@bld.example",
                "address": "Kızılırmak Mah. 1443. Cad. No:12, Çankaya / Ankara",
                "working_hours": "Hafta içi 08:00 – 18:00",
            },
            "updated_at": "2026-08-02T10:00:00Z",
        },
        "faq": {
            "value": [{"q": "Minimum sipariş adediniz var mı?",
                       "a": "20 porsiyondan başlıyoruz."}],
            "updated_at": "2026-07-28T09:00:00Z",
        },
        "company": {"value": {}, "updated_at": None},
        "sectors": {"value": [], "updated_at": None},
        "menus": {"value": [], "updated_at": None},
        "quality": {"value": [], "updated_at": None},
    },
    "meta": {"keys": ["brand", "contact", "company", "faq", "sectors", "menus",
                      "quality"]},
    "server_time": "2026-08-16T09:00:00Z",
}

#: `GET /services` satırı — cms.md örneğinden, kısaltılmadan.
SERVICE: dict[str, Any] = {
    "id": 3, "slug": "kurumsal-catering", "title": "Kurumsal Catering",
    "summary": "Ofis ve fabrikalara günlük sıcak yemek",
    "intro": "Her sabah taze pişirilen menüler…",
    "icon": "Building2",
    "body_html": "<p>Sıcak yemek her sabah teslim edilir.</p>",
    "audience": ["Ofisler", "Fabrikalar"],
    "how_it_works": ["Menü planlanır", "Sabah teslim edilir"],
    "benefits": ["Sabit fiyat", "Tek fatura"],
    "menu_planning": "Haftalık menü birlikte belirlenir.",
    "quote_needs": ["Kişi sayısı", "Teslim adresi"],
    "sort_order": 10, "is_published": True,
    "created_at": "2026-06-01T08:00:00Z", "updated_at": "2026-08-02T10:00:00Z",
}

#: `GET /posts` satırı — cms.md örneğinden.
POST: dict[str, Any] = {
    "id": 21, "slug": "toplu-yemekte-soguk-zincir",
    "title": "Toplu yemekte soğuk zincir",
    "description": "Taşıma sırasında sıcaklık nasıl korunur?",
    "category": "gida-guvenligi",
    "body_html": "<p>Soğuk zincir taşımada kırılmamalı.</p>",
    "published_at": "2026-08-01",
    "reading_minutes": None, "reading_minutes_effective": 4,
    "is_published": True,
    "created_at": "2026-07-30T12:00:00Z", "updated_at": "2026-08-01T07:00:00Z",
}


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, message: str, **fields: Any) -> None:
        self.records.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._add("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._add("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._add("error", message, **fields)


class FakeStore:
    """`ModuleStore` yüzeyi. Satırları bellekte tutar, SQL'i ayrıştırmaz."""

    def __init__(self, module_id: str = "bld_cms") -> None:
        self.module_id = module_id
        self.audit: list[dict[str, Any]] = []
        self.revisions: list[dict[str, Any]] = []
        #: `True` ise her yazma patlar — "iz yazılamazsa iş durmasın" (K7).
        self.broken = False

    def table(self, name: str) -> str:
        return f"mod_{self.module_id}_{name}"

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise RuntimeError("depo yazılamıyor")
        text = " ".join(sql.split())
        if "_audit" in text and text.startswith("INSERT"):
            keys = ("target_type", "target_id", "target_key", "action", "reason",
                    "actor", "result", "detail", "created_at")
            self.audit.append(dict(zip(keys, params, strict=False)))
        elif "_revisions" in text and text.startswith("INSERT"):
            keys = ("target_type", "target_id", "target_key", "title", "action",
                    "before_json", "after_json", "truncated", "actor", "reason",
                    "audit_id", "created_at")
            row = dict(zip(keys, params, strict=False))
            row["id"] = len(self.revisions) + 1
            self.revisions.append(row)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        if "_revisions" in sql:
            wanted = int(params[0])
            for row in self.revisions:
                if row["id"] == wanted:
                    return dict(row)
        return None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        # SÜZGEÇ UYGULANMAZ (bkz. dosya başlığı): son parametre limittir ve
        # yalnız o onurlandırılır. Süzgeci taklit etmek, SQLite'ı yeniden
        # yazmak olurdu; sınanan şey satırın YAZILDIĞI, nasıl süzüldüğü değil.
        if "_revisions" in sql:
            limit = int(params[-1]) if params else len(self.revisions)
            return [dict(row) for row in reversed(self.revisions)][:limit]
        return []

    # ------------------------------------------------------------- kolaylık

    def actions(self) -> list[tuple[str, str]]:
        """`(action, result)` çiftleri — sıra korunur."""
        return [(row["action"], row["result"]) for row in self.audit]

    def detail(self, index: int) -> dict[str, Any]:
        return json.loads(self.audit[index]["detail"])


class FakeApi:
    """`bld.api` yeteneğinin testlik yüzü."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        #: Patlaması istenen metot adları.
        self.fail: set[str] = set()
        self.content: dict[str, Any] = json.loads(json.dumps(CONTENT))
        self.services: list[dict[str, Any]] = [json.loads(json.dumps(SERVICE))]
        self.posts: list[dict[str, Any]] = [json.loads(json.dumps(POST))]
        #: Yeniden çizdirme sonucu — `"failed"` yapılırsa uyarı dalı sınanır.
        self.revalidate_status = "ok"
        #: Sunucunun `body_html` alanını temizlediği durumu taklit eder.
        self.sanitize_body: str | None = None

    def _guard(self, name: str) -> None:
        self.calls.append((name, {}))
        if name in self.fail:
            raise RuntimeError("BLD sunucusuna ulaşılamadı.")

    def _write(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))
        if name in self.fail:
            raise RuntimeError("BLD sunucusuna ulaşılamadı.")

    def _revalidate_block(self, requested: bool) -> dict[str, Any]:
        """Yazma uçlarının tazeleme kuyruğu — cms.md `PUT /content` örneği.

        YAZMA YANITINDA `data.status` YOKTUR: `data` orada YAZILAN KAYITTIR ve
        tazelemenin sonucu ayrı bir bayrakla (`revalidated`) + uyarıyla gelir.
        Sahteyi buna uydurmak şart: `data` alanını tazeleme künyesiyle ezen bir
        sahte, servisin kaydı geri okuduğunu SANDIRIR ve gerçek sunucuda
        patlayacak bir testi yeşil gösterirdi.
        """
        if not requested:
            return {"revalidated": False, "warnings": []}
        if self.revalidate_status == "failed":
            return {"revalidated": False,
                    "warnings": [{"code": "revalidate_failed"}]}
        return {"revalidated": True, "warnings": []}

    @staticmethod
    def _merge(block: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
        """Tazeleme uyarısı ile kaydın kendi uyarılarını (slug) birleştirir."""
        return {"revalidated": block["revalidated"],
                "warnings": [*warnings, *block["warnings"]]}

    # ------------------------------------------------------------- okumalar

    async def site_content(self) -> dict[str, Any]:
        self._guard("site_content")
        return json.loads(json.dumps(self.content))

    async def site_services(self, *, published: str = "") -> dict[str, Any]:
        self._guard("site_services")
        return {"items": json.loads(json.dumps(self.services))}

    async def site_posts(self, *, q: str = "", category: str = "", published: str = "",
                         page: int = 1, per_page: int | None = None) -> dict[str, Any]:
        self._guard("site_posts")
        return {"items": json.loads(json.dumps(self.posts)),
                "meta": {"page": page, "per_page": per_page or 25,
                         "total": len(self.posts), "last_page": 1,
                         "categories": ["gida-guvenligi", "menu-planlama"]}}

    # -------------------------------------------------------------- yazmalar

    async def set_site_content(self, key: str, *, value: Any, revalidate: bool = True,
                               reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._write("set_site_content", key=key, value=value, revalidate=revalidate,
                    reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2201,
                "data": {"key": key, "updated_at": "2026-08-16T09:00:00Z"},
                **self._merge(self._revalidate_block(revalidate), [])}

    async def create_site_service(self, *, slug: str, title: str, fields: dict[str, Any],
                                  revalidate: bool = True, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._write("create_site_service", slug=slug, title=title, fields=fields,
                    revalidate=revalidate, reason=reason, actor=actor, dry_run=dry_run)
        row = {**SERVICE, **fields, "id": 44, "slug": slug, "title": title}
        if self.sanitize_body is not None:
            row["body_html"] = self.sanitize_body
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2210, "data": row,
                **self._merge(self._revalidate_block(revalidate), [])}

    async def update_site_service(self, service_id: int, *, fields: dict[str, Any],
                                  revalidate: bool = True, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._write("update_site_service", service_id=service_id, fields=fields,
                    revalidate=revalidate, reason=reason, actor=actor, dry_run=dry_run)
        row = {**self.services[0], **fields}
        warnings = []
        if "slug" in fields and fields["slug"] != self.services[0]["slug"]:
            warnings.append({"code": "slug_changed", "from": self.services[0]["slug"],
                             "to": fields["slug"],
                             "note": "Eski adrese verilen bağlantılar kırılacak."})
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2211, "data": row,
                **self._merge(self._revalidate_block(revalidate), warnings)}

    async def delete_site_service(self, service_id: int, *, revalidate: bool = True,
                                  reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        self._write("delete_site_service", service_id=service_id, revalidate=revalidate,
                    reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2212, "data": {},
                **self._merge(self._revalidate_block(revalidate), [])}

    async def create_site_post(self, *, slug: str, title: str, body_html: str,
                               fields: dict[str, Any] | None = None,
                               revalidate: bool = True, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._write("create_site_post", slug=slug, title=title, body_html=body_html,
                    fields=fields or {}, revalidate=revalidate, reason=reason,
                    actor=actor, dry_run=dry_run)
        row = {**POST, **(fields or {}), "id": 55, "slug": slug, "title": title,
               "body_html": body_html}
        if self.sanitize_body is not None:
            row["body_html"] = self.sanitize_body
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2220, "data": row,
                **self._merge(self._revalidate_block(revalidate), [])}

    async def update_site_post(self, post_id: int, *, fields: dict[str, Any],
                               revalidate: bool = True, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._write("update_site_post", post_id=post_id, fields=fields,
                    revalidate=revalidate, reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2221,
                "data": {**self.posts[0], **fields},
                **self._merge(self._revalidate_block(revalidate), [])}

    async def delete_site_post(self, post_id: int, *, revalidate: bool = True,
                               reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        self._write("delete_site_post", post_id=post_id, revalidate=revalidate,
                    reason=reason, actor=actor, dry_run=dry_run)
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2222, "data": {},
                **self._merge(self._revalidate_block(revalidate), [])}

    async def revalidate_site(self, *, paths: list[str] | None = None, reason: str,
                              actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        self._write("revalidate_site", paths=paths, reason=reason, actor=actor,
                    dry_run=dry_run)
        # TOPLU ÇİZDİRME UCU FARKLIDIR: burada `data` KAYIT DEĞİL, çizdirmenin
        # künyesidir (cms.md `POST /revalidate`). Yazma uçlarıyla aynı gövdeyi
        # kullanmak, iki ayrı sözleşmeyi tek sahtede karıştırmak olurdu.
        basarisiz = self.revalidate_status == "failed"
        data = {"requested": "all" if not paths else paths,
                "status": "failed" if basarisiz else "ok"}
        if basarisiz:
            data["error"] = "Bağlantı zaman aşımı (3 sn)"
        else:
            data["duration_ms"] = 340
        return {"ok": True, "dry_run": bool(dry_run), "audit_id": 2230, "data": data,
                "warnings": [{"code": "revalidate_failed"}] if basarisiz else []}

    # ------------------------------------------------------------- kolaylık

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def kwargs(self, name: str) -> dict[str, Any]:
        """Adı geçen SON çağrının anahtar argümanları."""
        for call_name, call_kwargs in reversed(self.calls):
            if call_name == name:
                return call_kwargs
        raise AssertionError(f"'{name}' hiç çağrılmadı")
