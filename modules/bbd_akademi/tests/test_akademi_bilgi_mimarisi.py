"""Panel sadeleştirme (13.10.2026): bilgi mimarisi ve "yalnız açık olan görünür".

İki parça:

* `ui/panel/sections.js` — menü (en fazla altı üst bölüm), öğrenci kartı
  (beş sekme, katlanır bölümler) ve özellik bayrağına göre görünürlük. Dosya
  bilerek import'suz ve DOM'suzdur: `.mjs` kopyası Node'da yüklenir, işlevler
  çağrılır, sonuç JSON olarak buraya döner. `node` yoksa bu parça ATLANIR.
* `/km/status` → `features` (`backend/features.py`): kurumun özellik bayrakları
  sunucu tarafında, modülün kendi adına okunur, 60 sn önbellekte durur;
  Akademi'ye ulaşılamazsa `null` döner ve açılış düşmez. `operations=1` ile
  işlem listesi de aynı yanıtta gelir (açılış tek çağrı).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from modules.bbd_akademi.backend.features import TenantFeatures, parse
from modules.bbd_akademi.tests.akademi_fakes import Akademi, Log, auth

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
STATUS = "/api/bbd_akademi/km/status"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")

SCRIPT = r"""
import * as s from './sections.mjs';
const all = () => true;
const only = (...keys) => (permission) => keys.includes(permission);
const keys = (nav) => nav.map((area) => [area.key, area.sections.map((x) => x.key)]);
const card = (tabs) => tabs.map((tab) => [tab.key, tab.parts.map((p) => p.key)]);
const off = { early_warning: false, flashcards: false, finance: false, canteen: false, books: false,
  coaching: false, self_review: false, coach_questions: false };
const navOff = s.buildNav({ has: all, features: off });
const out = {
  areas: s.AREAS.map((a) => a.key),
  sectionKeys: s.SECTIONS.map((x) => x.key),
  navAll: keys(s.buildNav({ has: all, features: null })),
  navOff: keys(navOff),
  navUnknownFlag: keys(s.buildNav({ has: all, features: { some_new_flag: false } })),
  navViewer: keys(s.buildNav({ has: only('view'), features: null })),
  navNothing: keys(s.buildNav({ has: () => false, features: null })),
  featureOn: [s.featureOn(null, 'finance'), s.featureOn({}, 'finance'), s.featureOn({ finance: true }, 'finance'),
    s.featureOn({ finance: false }, 'finance'), s.featureOn({ finance: false }, undefined)],
  goArea: s.resolveTarget(navOff, 'progress'),
  goSection: s.resolveTarget(navOff, 'exams'),
  goAlias: s.resolveTarget(navOff, 'panel'),
  goSettings: s.resolveTarget(navOff, 'settings'),
  goHidden: s.resolveTarget(navOff, 'insights'),
  goUnknown: s.resolveTarget(navOff, 'yok'),
  cardAll: card(s.studentCard({ has: all, features: null, capabilities: { record: true } })),
  cardNoRecord: card(s.studentCard({ has: all, features: null })),
  cardOff: card(s.studentCard({ has: only('view'), features: off })),
  cardMoneyNoPerm: card(s.studentCard({ has: only('view'), features: null })),
  locTab: s.locatePart(s.studentCard({ has: all }), 'progress'),
  locRecord: s.locatePart(s.studentCard({ has: all, capabilities: { record: true } }), 'record'),
  locPart: s.locatePart(s.studentCard({ has: all }), 'coaching'),
  locHidden: s.locatePart(s.studentCard({ has: only('view'), features: off }), 'canteen'),
  locEmpty: s.locatePart([], 'overview'),
  navKmOnly: keys(s.buildNav({ has: () => false, features: off, kmOnly: true })),
  goKmOnly: s.resolveTarget(s.buildNav({ has: () => false, kmOnly: true }), 'students'),
  goKmOnlyHidden: s.resolveTarget(s.buildNav({ has: () => false, kmOnly: true }), 'exams'),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    folder = tmp_path_factory.mktemp("sections")
    (folder / "sections.mjs").write_bytes((PANEL / "sections.js").read_bytes())
    (folder / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    done = subprocess.run(["node", str(folder / "run.mjs")], capture_output=True, text=True,
                          check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    data: dict[str, Any] = json.loads(done.stdout)
    return data


# --------------------------------------------------------------- menü (Node)


@needs_node
def test_en_fazla_alti_ust_bolum(result: dict[str, Any]) -> None:
    assert result["areas"] == ["today", "students", "lessons", "progress", "money", "admin"]
    nav = dict(result["navAll"])
    assert list(nav) == result["areas"]
    # Hoca soruları (Akademi §14 madde 63): öğrencinin hocaya sorusu, yanıt metin + görsel + ses.
    assert nav["today"] == ["dashboard", "requests", "announcements", "questions", "messages"]
    # Koçluk (KM-4): öğretmen = koç; kuyruk, kart, haftalık program, koçlar, kütüphane, görüşmeler.
    assert nav["students"] == ["students", "coaching"]
    # Şablonlar (0.5.4): ödev şablonu, atama paketi, plan şablonu tek yerde.
    assert nav["lessons"] == ["timetable", "attendance", "homework", "plan", "templates", "calendar"]
    assert nav["money"] == ["finance", "canteen", "bookSales", "books"]   # kitap satışı 0.5.4
    assert nav["admin"] == ["settings", "staff", "privacy"]


@needs_node
def test_hicbir_ekran_kaybolmadi(result: dict[str, Any]) -> None:
    """Sadeleştirme yalnız yerleşimi değiştirir: eski 21 ekranın hepsi menüde (+ Kantin)."""
    old = {"dashboard", "students", "curriculum", "resources", "timetable", "calendar", "attendance",
           "homework", "exams", "plan", "reading", "decks", "evaluations", "insights", "finance",
           "books", "announcements", "requests", "settings", "staff", "privacy"}
    shown = {key for _, sections in result["navAll"] for key in sections}
    assert old <= shown
    assert shown == set(result["sectionKeys"])


@needs_node
def test_kapali_ozellik_menude_yok(result: dict[str, Any]) -> None:
    nav = dict(result["navOff"])
    assert "insights" not in nav["progress"] and "decks" not in nav["progress"]
    assert "questions" not in nav["today"]             # soru-cevap kapalı kurumda menüde yok
    assert "messages" not in nav["today"]              # koçluk kapalı: mesaj denetimi menüde yok
    assert "coaching" not in nav["students"]           # koçluk kapalı: Koçluk ekranı menüde yok
    # Kantin ve Finans'ın üç ekranı da kapalı → üst bölüm hiç görünmez.
    assert "money" not in nav
    # Bilinmeyen / listede olmayan bayrak bir şey gizlemez.
    assert result["navUnknownFlag"] == result["navAll"]
    # "Bilmiyorum" gizle demek değildir.
    assert result["featureOn"] == [True, True, True, False, True]


@needs_node
def test_yetkiye_gore_menu(result: dict[str, Any]) -> None:
    nav = dict(result["navViewer"])
    assert nav["today"] == ["dashboard"]               # talepler/duyurular izni yok
    assert "admin" not in nav                          # ayar, personel, KVKK izni yok
    assert nav["money"] == ["canteen", "bookSales", "books"]   # kurum cari `finance` izni ister
    assert result["navNothing"] == []


@needs_node
def test_go_hedefi(result: dict[str, Any]) -> None:
    assert result["goArea"] == {"area": "progress", "section": "exams"}
    assert result["goSection"] == {"area": "progress", "section": "exams"}
    assert result["goAlias"] == {"area": "today", "section": "dashboard"}
    assert result["goSettings"] == {"area": "admin", "section": "settings"}
    assert result["goHidden"] is None and result["goUnknown"] is None


# ---------------------------------------------------------- öğrenci kartı (Node)


@needs_node
def test_ogrenci_karti_en_cok_alti_sekme(result: dict[str, Any]) -> None:
    card = dict(result["cardAll"])
    assert list(card) == ["summary", "record", "program", "work", "progress", "money"]
    old_tabs = {"overview", "schedule", "curriculum", "resources", "homework", "exams", "attendance",
                "study", "learning", "goals", "coaching", "flashcards", "requests", "finance", "canteen"}
    parts = [key for keys in card.values() for key in keys]
    assert sorted(parts) == sorted(old_tabs | {"record", "books"}), (
        "eski 15 sekmenin her biri tam bir kez bölüm olmalı (+ KM kaydı, + kurumdan alınan kitaplar 0.5.4)")
    # Öğrenci yönetimi yeteneği yoksa (modül kapalı / izin yok) "Kayıt" sekmesi yok.
    assert [key for key, _ in result["cardNoRecord"]] == ["summary", "program", "work", "progress",
                                                         "money"]
    assert result["locRecord"] == {"tab": "record", "part": "record"}


@needs_node
def test_ogrenci_karti_kapali_ozellik_ve_yetki(result: dict[str, Any]) -> None:
    card = dict(result["cardOff"])
    assert "flashcards" not in card["work"]
    assert "money" not in card                         # kurum cari + kantin + kitap kapalı → sekme yok
    money = dict(result["cardMoneyNoPerm"])["money"]
    assert money == ["canteen", "books"]                # kurum cari `finance` izni ister


@needs_node
def test_ogrenci_karti_derin_baglanti(result: dict[str, Any]) -> None:
    assert result["locTab"] == {"tab": "progress", "part": "exams"}
    assert result["locPart"] == {"tab": "progress", "part": "coaching"}
    assert result["locHidden"] == {"tab": "summary", "part": "overview"}
    assert result["locEmpty"] is None


# ------------------------------------------------------ /km/status → features


FLAGS = {"items": [
    {"key": "finance", "enabled": True, "config": None, "editable": True},
    {"key": "early_warning", "enabled": False, "config": None, "editable": True},
    {"key": "canteen", "enabled": False, "config": {"source": "km"}, "editable": False},
    {"key": "bozuk", "enabled": "evet"},
    "çöp",
]}


def _flags_reply(akademi: Akademi, payload: Any = None, status: int = 200) -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/admin/features":
            return httpx.Response(status, json=FLAGS if payload is None else payload)
        return httpx.Response(200, json={"ok": True})
    akademi.reply(reply)


def _feature_calls(akademi: Akademi) -> list[httpx.Request]:
    return [call for call in akademi.calls if call.url.path == "/v1/admin/features"]


async def test_durum_ucu_ozellikleri_verir_ve_onbellekler(client: httpx.AsyncClient,
                                                          akademi: Akademi) -> None:
    _flags_reply(akademi)
    data = (await client.get(STATUS, headers=auth("viewer"))).json()
    assert data["features"] == {"finance": True, "early_warning": False, "canteen": False}
    sent = _feature_calls(akademi)[0]
    assert sent.method == "GET"
    # Modülün kendi adına (sistem aktörü) okunur; KM kullanıcısının ayar izni gerekmez.
    assert sent.headers["x-akademi-on-behalf-of"].startswith("km:system:")
    assert "operationList" not in data

    await client.get(STATUS, headers=auth("viewer"))
    assert len(_feature_calls(akademi)) == 1, "60 sn içinde ikinci okuma Akademi'ye gitmemeli"
    await client.get(STATUS, headers=auth("viewer"), params={"fresh": 1})
    assert len(_feature_calls(akademi)) == 2, "fresh=1 önbelleği atlamalı"


@pytest.mark.parametrize("status,payload", [(500, {"error": {"code": "internal"}}),
                                            (403, {"error": {"code": "forbidden"}}),
                                            (200, {"ok": True})])
async def test_ozellikler_okunamazsa_null(client: httpx.AsyncClient, akademi: Akademi,
                                          status: int, payload: Any) -> None:
    _flags_reply(akademi, payload, status)
    response = await client.get(STATUS, headers=auth("viewer"))
    assert response.status_code == 200
    assert response.json()["features"] is None


async def test_akademiye_ulasilamazsa_acilis_dusmez(client: httpx.AsyncClient,
                                                    akademi: Akademi) -> None:
    akademi.down = httpx.ConnectError("yok")
    response = await client.get(STATUS, headers=auth("viewer"))
    assert response.status_code == 200
    assert response.json()["features"] is None


async def test_acilis_tek_cagri_islem_listesiyle(client: httpx.AsyncClient,
                                                 akademi: Akademi) -> None:
    _flags_reply(akademi)
    data = (await client.get(STATUS, headers=auth("viewer"), params={"operations": 1})).json()
    separate = (await client.get("/api/bbd_akademi/km/operations",
                                 headers=auth("viewer"))).json()["items"]
    assert data["operationList"] == separate
    assert isinstance(data["operations"], int)          # eski alan (işlem sayısı) korunur
    by_id = {item["operationId"]: item for item in data["operationList"]}
    assert by_id["admin_listEarlyWarnings"]["featureFlag"] == "early_warning"


async def test_yavas_akademi_acilisi_bekletmez(akademi: Akademi) -> None:
    import asyncio

    class SlowGateway:
        async def send(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            await asyncio.sleep(1)
            return httpx.Response(200, json=FLAGS)

    features = TenantFeatures(gateway=SlowGateway(), log=Log(),  # type: ignore[arg-type]
                              wait_seconds=0.05)
    assert await features.snapshot() is None            # beklemez, bilinmiyor der
    await asyncio.sleep(1.1)                            # okuma arkada tamamlanır
    assert await features.snapshot() == {"finance": True, "early_warning": False,
                                         "canteen": False}


def test_ayristirici_bicim_disini_reddeder() -> None:
    assert parse(b"") is None
    assert parse(b"{bozuk") is None
    assert parse(b'{"items": "x"}') is None
    assert parse(b'[]') is None
    assert parse(json.dumps(FLAGS).encode()) == {"finance": True, "early_warning": False,
                                                  "canteen": False}


@needs_node
def test_yalniz_ogrenci_yonetimi_izni_olan_yalniz_ogrencileri_gorur(result: dict[str, Any]) -> None:
    """0.5.3 (ADR 0031 — tek giriş noktası): menüdeki eski "Öğrenci Yönetimi"
    kalktı; Akademi izni olmayan ama `bbd_students.view` taşıyan roller (muhasebe,
    org_staff) BBD Akademi panelini YALNIZ "Öğrenciler" bölümüyle görür."""
    assert result["navKmOnly"] == [["students", ["students"]]]
    assert result["goKmOnly"] == {"area": "students", "section": "students"}
    assert result["goKmOnlyHidden"] is None
