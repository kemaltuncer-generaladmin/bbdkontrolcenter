"""Saf dönüşümler — ağ yok, durum yok.

Bu dosyanın sınadığı şeyler ekranda tek bir rozetten ibaret görünür ama karar
değiştirirler: "çevrimdışı" yazan bir kiosk sahaya gidilmesini, "kod hazır"
yazan bir satır kantine boşuna gidilmesini sağlar.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bbd_canteen_devices_backend import kiosks as kio

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)


def wire(**degisiklik: object) -> dict:
    """Kantinin tel üzerindeki gövdesi — camelCase, eşlenmiş ve sessiz bir kiosk."""
    row = {
        "id": 3, "name": "Kantin Kiosk", "platform": "android", "appVersion": "1.4.0",
        "paired": True, "pairedAt": "2026-08-18T09:00:00+00:00",
        "lastSeenAt": "2026-08-18T11:58:00+00:00", "revokedAt": None,
        "revokedReason": None, "createdAt": "2026-08-18T08:00:00+00:00",
        "pairing": {"usable": False, "expiresAt": None, "usedAt": "2026-08-18T09:00:00+00:00"},
    }
    row.update(degisiklik)
    return row


def test_esik_icindeki_kiosk_cevrimicidir() -> None:
    satir = kio.kiosk_row(wire(), online_after=5, now=NOW)
    assert satir["state"] == "online"
    assert satir["last_seen_minutes"] == 2
    # Alan adları snake_case'e çevrilir: paneller bu sözlüğü okuyor.
    assert satir["app_version"] == "1.4.0"


def test_sessiz_kalan_kiosk_cevrimdisidir() -> None:
    satir = kio.kiosk_row(wire(lastSeenAt="2026-08-18T11:00:00+00:00"),
                          online_after=5, now=NOW)
    assert satir["state"] == "offline"
    assert satir["online"] is False


def test_iptal_edilmis_kiosk_cevrimici_gorunmez() -> None:
    # İptal edilen cihaz bir daha bağlanamaz; "az önce görüldü" damgası hâlâ
    # tazeyken çevrimiçi göstermek, ölü bir cihazı çalışıyor sanmaktır.
    satir = kio.kiosk_row(wire(revokedAt="2026-08-18T11:59:00+00:00"),
                          online_after=5, now=NOW)
    assert satir["state"] == "revoked"
    assert satir["online"] is False


def test_hic_eslesmemis_kiosk_arizali_degil_bekliyor_sayilir() -> None:
    # "Çevrimdışı" ile "kurulumu bekliyor" aynı şey değildir: ilki sahaya
    # gitmeyi, ikincisi kodu götürmeyi gerektirir.
    satir = kio.kiosk_row(wire(paired=False, pairedAt=None, lastSeenAt=None),
                          online_after=5, now=NOW)
    assert satir["awaiting_pairing"] is True
    assert satir["state"] == "offline"
    assert satir["last_seen_minutes"] is None


def test_suresi_gecmis_kod_kullanilabilir_gorunmez() -> None:
    """Liste 9 dakika önce çekilmiş olabilir.

    Kantin o an "kullanılabilir" demişti; ekrana bakılırken kod ölmüş olabilir.
    İki kaynağın da evet demesi gerekir — "kod hazır" yazan bir satır,
    yöneticiyi kantine boşuna yollar.
    """
    olu = kio.kiosk_row(wire(pairing={"usable": True,
                                      "expiresAt": (NOW - timedelta(minutes=1)).isoformat(),
                                      "usedAt": None}),
                        now=NOW)
    assert olu["pairing"]["usable"] is False
    assert olu["pairing"]["expires_in_minutes"] == 0

    canli = kio.kiosk_row(wire(pairing={"usable": True,
                                        "expiresAt": (NOW + timedelta(minutes=6)).isoformat(),
                                        "usedAt": None}),
                          now=NOW)
    assert canli["pairing"]["usable"] is True
    assert canli["pairing"]["expires_in_minutes"] == 6


def test_ozet_sayilari_satirlardan_turer() -> None:
    satirlar = [
        kio.kiosk_row(wire(id=1), online_after=5, now=NOW),
        kio.kiosk_row(wire(id=2, lastSeenAt="2026-08-18T10:00:00+00:00"),
                      online_after=5, now=NOW),
        kio.kiosk_row(wire(id=3, revokedAt="2026-08-18T11:00:00+00:00"),
                      online_after=5, now=NOW),
        kio.kiosk_row(wire(id=4, paired=False, pairedAt=None, lastSeenAt=None),
                      online_after=5, now=NOW),
    ]
    ozet = kio.summary(satirlar)
    assert ozet == {"total": 4, "online": 1, "offline": 2, "revoked": 1,
                    "awaiting": 1, "usable_codes": 0}


def test_yeni_eslesen_yalniz_onceden_gorulmus_kayittan_dogar() -> None:
    """`canteen.device_enrolled` olayının doğduğu yer.

    Hiç görülmemiş ama zaten eşli bir kiosk YENİ SAYILMAZ: modülün ilk açılışı
    sahadaki her cihazı "az önce eşlendi" diye ilan ederdi.
    """
    satir = kio.kiosk_row(wire(id=7), now=NOW)

    assert kio.newly_paired([satir], {}) == []            # ilk okuma
    assert kio.newly_paired([satir], {7: ""}) == [satir]  # önce eşsizdi
    assert kio.newly_paired([satir], {7: "2026-08-18T09:00:00+00:00"}) == []


def test_gerekce_ve_ad_sinirlari_kantinle_ayni() -> None:
    assert kio.reason_error("kısa")
    assert kio.reason_error("x" * 161)
    assert kio.reason_error("Kiosk degistirildi") == ""

    assert kio.name_error("K")
    assert kio.name_error("x" * 101)
    assert kio.name_error("Kantin Kiosk") == ""
