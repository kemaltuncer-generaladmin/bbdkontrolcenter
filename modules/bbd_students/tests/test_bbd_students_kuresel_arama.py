"""Küresel öğrenci araması (0.5.3 — "öğrenci tek yerde, hızlı güncelleme").

Kabuğun üst çubuğundaki "Ara" kutusu (`/`) her ekrandan öğrenciyi bulur:
ad (Türkçe harfsiz de), okul no, veli/öğrenci telefonu (son hanelerle de),
kantin kartı (kimlik ya da kartın QR metni). Kaynak dizinin anlık
görüntüsüdür — arama kantine gitmez (A14: tek dizin çağrısı, kayıt başına
istek yok). Telefon sonuçta maskelidir.
"""

# ruff: noqa: F811 — `world` fikstürü merkezi senkron testinden alınır (aynı gerçek depo).
from __future__ import annotations

import time

from bbd_students_backend import qr as qr_codec
from bbd_students_backend.search import fold, looks_like_card, mask_phone, search
from test_bbd_students_merkezi_senkron import World, world  # noqa: F401 — fikstür

MOTHER, FATHER = "5321112233", "5054445566"


def _rec(ident: str, name: str, **extra: object) -> dict:
    return {"id": ident, "displayName": name, "className": extra.get("className", ""),
            "schoolNo": extra.get("schoolNo", ""), "studentPhone": extra.get("studentPhone"),
            "guardians": extra.get("guardians", []), "status": extra.get("status", "active")}


RECORDS = [
    _rec("STU-AAA111", "Çağrı Öztürk", className="9-A", schoolNo="1203",
         guardians=[{"slot": 1, "name": "Ayşe Öztürk", "phone": MOTHER}]),
    _rec("STU-BBB222", "Elif Kaya", className="9-A", schoolNo="77",
         guardians=[{"slot": 1, "name": "Mehmet Kaya", "phone": FATHER}]),
    _rec("STU-CCC333", "Ali Çağrıcı", className="10-B", schoolNo="1204", status="inactive"),
    _rec("STU-DDD444", "Zeynep Ali", className="11-SAY", studentPhone="5551234567"),
]


def ids(items: list[dict]) -> list[str]:
    return [item["id"] for item in items]


def test_katlama_turkce_harfsiz_bulur() -> None:
    assert fold("  Çağrı   ÖZTÜRK ") == "cagri ozturk"
    assert ids(search(RECORDS, "ozturk")) == ["STU-AAA111"]
    assert ids(search(RECORDS, "ÇAĞRI")) == ["STU-AAA111", "STU-CCC333"]


def test_siralama_bas_eslesme_kelime_ici_ve_pasif_sonra() -> None:
    # "ali": ad başı (Ali Çağrıcı, pasif) ile soyad başı (Zeynep Ali, etkin):
    # ad başı puanı daha iyi — pasif olsa da önde; aynı puanda etkin önce gelir.
    assert ids(search(RECORDS, "ali")) == ["STU-CCC333", "STU-DDD444"]
    assert ids(search(RECORDS, "cag")) == ["STU-AAA111", "STU-CCC333"]
    assert search(RECORDS, "ozturk")[0]["match"] == "name"


def test_okul_no_ve_kart_kimligi_birebir_once() -> None:
    assert ids(search(RECORDS, "1203"))[0] == "STU-AAA111"
    assert search(RECORDS, "1203")[0]["match"] == "schoolNo"
    assert ids(search(RECORDS, "120")) == ["STU-AAA111", "STU-CCC333"]
    hit = search(RECORDS, "stu-bbb222")
    assert ids(hit) == ["STU-BBB222"] and hit[0]["match"] == "card"


def test_veli_telefonu_tam_ya_da_son_haneler() -> None:
    for text in ("0532 111 22 33", "+90 532 111 2233", "5321112233"):
        hit = search(RECORDS, text)
        assert ids(hit) == ["STU-AAA111"], text
        assert hit[0]["match"] == "guardianPhone"
    assert ids(search(RECORDS, "2233")) == ["STU-AAA111"]
    assert ids(search(RECORDS, "4567")) == ["STU-DDD444"]
    assert search(RECORDS, "4567")[0]["match"] == "studentPhone"


def test_sonuc_kisisel_veriyi_maskeler_ve_sinirlidir() -> None:
    hit = search(RECORDS, "ozturk")[0]
    assert hit["guardian"] == {"name": "Ayşe Öztürk", "phone": "•••• •• 22 33"}
    assert MOTHER not in str(hit)
    assert mask_phone("") == ""
    many = [_rec(f"STU-{i:06d}", f"Ahmet {i}") for i in range(40)]
    assert len(search(many, "ahmet")) == 8
    assert len(search(many, "ahmet", limit=100)) == 25
    assert search(RECORDS, "   ") == []
    assert search(RECORDS, "q") == []


def test_kart_qr_metni_taninir() -> None:
    key = bytes(range(32))
    text = qr_codec.encode("STU-AAA111", key)
    assert looks_like_card(text)
    assert not looks_like_card("Çağrı Öztürk")
    assert not looks_like_card("5321112233")


async def test_servis_dizinden_arar_kantine_tekrar_gitmez(world: World) -> None:
    world.canteen.add("STU-1", "Elif Kaya")
    world.canteen.add("STU-2", "Can Demir")
    await world.service.set_guardians("STU-1", [{"name": "Ayşe", "phone": MOTHER}], actor="t")
    reads: list[int] = []
    original = world.canteen.students

    async def counted() -> list[dict]:
        reads.append(1)
        return await original()

    world.canteen.students = counted  # type: ignore[method-assign]
    first = await world.service.search("kaya")
    assert first["complete"] is True and ids(first["items"]) == ["STU-1"]
    before = len(reads)
    started = time.perf_counter()
    for _ in range(50):
        again = await world.service.search("2233")
    elapsed = time.perf_counter() - started
    assert ids(again["items"]) == ["STU-1"]
    # Sıcak yol: dizinin anlık görüntüsü — kantine yeni istek yok, 50 arama < 0,5 sn.
    assert len(reads) == before
    assert elapsed < 0.5


async def test_servis_kart_qr_metnini_cozer_kantin_yoksa_eksik_der(world: World) -> None:
    key = bytes(range(32))

    async def qr_key() -> bytes:
        return key

    world.canteen.qr_key = qr_key  # type: ignore[attr-defined]
    world.canteen.add("STU-QR1", "Deniz Ak")
    result = await world.service.search(qr_codec.encode("STU-QR1", key))
    assert ids(result["items"]) == ["STU-QR1"]
    assert result["items"][0]["match"] == "card"

    world.directory.invalidate()
    world.canteen.down = True
    assert await world.service.search("deniz") == {"items": [], "complete": False}
