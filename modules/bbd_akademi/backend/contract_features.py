"""Sözleşmeye HENÜZ GİRMEMİŞ alanların şalterleri — TEK YER.

Sözleşme `km-ogrenci-gruplari-ve-zil.md` §7.D/§8.1 iki alanı Akademi API'sine
ekleyici olarak getirecek; bbd-akademi `main`'deki commit'lenmiş
`docs/api/openapi.yaml` (1.0.0-draft.3, f964e8f) bunları HENÜZ TAŞIMIYOR.
Vekil gövdeyi doğrulamadan geçirdiği için (proxy.py) sözleşmede olmayan bir
alanı göndermek Akademi'de `422 validation_error` riski demektir. Bu yüzden:

``STAFF_ANNOUNCE_NAME``
    `PUT /v1/admin/staff/sync` (`admin_syncStaff`) yükünde her satıra
    `announceName` eklenir mi. KAPALIYKEN anons adı KM'de saklanır ve Personel
    ekranında görünür/düzenlenir, yalnız itilmez. Açılması: sözleşmenin
    `StaffSyncRequest.items[]` şemasına `announceName` girdikten SONRA.

``CALENDAR_EVENT_BELL``
    Kurum takvimi etkinliğinin oluştur/güncelle gövdesine `bell`
    (`{mode: normal|off|profile, profileId, cancelSessions}`) eklenir mi.
    KAPALIYKEN formdaki "Zil" alanı görünür ama devre dışıdır ve gerekçesi
    yazar; gövdeye hiçbir şey eklenmez. Açılması: takvim etkinliği şemasına
    `bell` girdikten SONRA (alan adları farklı gelirse yalnız
    `ui/panel/bell-plan.js` → `bellPayload` uyarlanır).

Akademi'nin `admin_getBellPlanStatus` işlemi şalter İSTEMEZ: işlem izin
listesine (`allowlist.py`, sözleşmeden üretilir) girdiği anda panel onu
kendiliğinden kullanır; o zamana dek KM Zil Sistemi'nin kendi durum ucunu
(`GET /api/bell/plan/status`) okur.

Şalteri açan commit, aynı commit'te `allowlist.py`'yi de yeniden üretmeli;
`tests/test_akademi_contract_features.py` açık bir şalterin sözleşmede
karşılığı olduğunu doğrular.
"""

from __future__ import annotations

STAFF_ANNOUNCE_NAME = False
CALENDAR_EVENT_BELL = False


def as_dict() -> dict[str, bool]:
    """Panelin okuduğu biçim (`/km/status` → `contractFeatures`)."""
    return {"staffAnnounceName": STAFF_ANNOUNCE_NAME, "calendarEventBell": CALENDAR_EVENT_BELL}
