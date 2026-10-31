"""Sözleşmeye sonradan giren alanların şalterleri — TEK YER.

Sözleşme `km-ogrenci-gruplari-ve-zil.md` §7.D/§8.1 iki alanı Akademi API'sine
ekleyici olarak getirdi; bbd-akademi `main`'deki commit'lenmiş
`docs/api/openapi.yaml` (1.0.0-draft.3) bunları 2d91b96 ile TAŞIYOR, iki şalter
de bu yüzden AÇIK. Vekil gövdeyi doğrulamadan geçirdiği için (proxy.py)
sözleşmede olmayan bir alanı göndermek Akademi'de `422 validation_error`
riski demektir; şalter kapatılırsa alan hiç gönderilmez.

``STAFF_ANNOUNCE_NAME``
    `PUT /v1/admin/staff/sync` (`admin_syncStaff`) yükünde her satıra
    `announceName` eklenir mi (`StaffSyncRequest.items[].announceName`).
    KAPALIYKEN anons adı KM'de saklanır ve Personel ekranında
    görünür/düzenlenir, yalnız itilmez.

``CALENDAR_EVENT_BELL``
    Kurum takvimi etkinliğinin oluştur/güncelle gövdesine `bell`
    (`{mode: normal|off|profile, profileId}`) ve `cancelLessons` eklenir mi
    (`InstitutionEventInput`). KAPALIYKEN formdaki "Zil" alanı görünür ama
    devre dışıdır ve gerekçesi yazar; gövdeye hiçbir şey eklenmez. Alan adları
    değişirse yalnız `ui/panel/lessons/bell-plan.js` → `bellPayload` / `withBell`
    uyarlanır.

``STAFF_FINANCE_MANAGER``
    `admin_syncStaff` yükünde her satıra `financeManager` (kurum finansı onay kodlarını alan personel, iş 83)
    eklenir mi. KAPALIYKEN bayrak KM'de saklanır ve Personel ekranında görünür/düzenlenir, yalnız itilmez
    (Akademi'de atama CLI ile yapılır). Alan `wp/83-finans` sözleşmesinde (b0b38536, İSTEĞE BAĞLI; gönderilmezse
    Akademi kayıtlı değeri korur) — şalter AÇIK; KM ile Akademi 83 birlikte canlıya çıkar.

Akademi'nin `admin_getBellPlanStatus` işlemi şalter İSTEMEZ: işlem izin
listesine (`allowlist.py`, sözleşmeden üretilir) girdiği anda panel onu
kendiliğinden kullanır; o zamana dek KM Zil Sistemi'nin kendi durum ucunu
(`GET /api/bell/plan/status`) okur.

Şalteri açan commit, aynı commit'te `allowlist.py`'yi de yeniden üretmeli;
`tests/test_akademi_contract_features.py` açık bir şalterin sözleşmede
karşılığı olduğunu doğrular.
"""

from __future__ import annotations

STAFF_ANNOUNCE_NAME = True
CALENDAR_EVENT_BELL = True
STAFF_FINANCE_MANAGER = True


def as_dict() -> dict[str, bool]:
    """Panelin okuduğu biçim (`/km/status` → `contractFeatures`)."""
    return {"staffAnnounceName": STAFF_ANNOUNCE_NAME, "calendarEventBell": CALENDAR_EVENT_BELL,
            "staffFinanceManager": STAFF_FINANCE_MANAGER}
