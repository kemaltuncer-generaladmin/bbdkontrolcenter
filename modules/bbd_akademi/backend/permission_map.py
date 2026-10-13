"""Akademi izni → KM izni. VEKİLİN YETKİ TABLOSU TEK YERDE.

Vekil bir isteği geçirmeden önce iki kapıdan geçirir (K9):

1. İşlem izin listesinde mi (`allowlist.py`, sözleşmeden üretilir)?
2. KM kullanıcısı o işlemin Akademi iznine karşılık gelen KM iznini taşıyor mu?

İkinci kapının bütün bilgisi bu dosyadadır; router'da, panelde ya da başka bir
yerde izin adı yazılmaz. Akademi'ye giden istek entegrasyon anahtarının
izinleriyle yetkilendirilir (Akademi'de ikinci kez denetlenir); buradaki tablo
"KM'de bu kişi bu işi yapabilir mi" sorusunun cevabıdır.

Kural: Akademi izni → TEK KM izni. Sözleşmede tek Akademi izni iki ayrı işi
kapsıyorsa (ör. `etut.manage` hem etüt dersi hem etüt talebi) ayrım işlem
kimliğiyle `OPERATION_OVERRIDES` içinde yapılır — yine bu dosyada.

Kantin parası ile kurum parası ayrıdır (A12): `canteen.*` hiçbir zaman
`bbd_akademi.finance`e eşlenmez.
"""

from __future__ import annotations

from collections.abc import Iterable

from .operations import Operation

MODULE = "bbd_akademi"

VIEW = f"{MODULE}.view"
ACADEMIC = f"{MODULE}.academic"
CURRICULUM = f"{MODULE}.curriculum"
RESOURCES = f"{MODULE}.resources"
ATTENDANCE = f"{MODULE}.attendance"
EXAMS = f"{MODULE}.exams"
FINANCE = f"{MODULE}.finance"
FINANCE_VOID = f"{MODULE}.finance_void"
BOOKS = f"{MODULE}.books"
ANNOUNCEMENTS = f"{MODULE}.announcements"
REQUESTS = f"{MODULE}.requests"
SETTINGS = f"{MODULE}.settings"
STAFF = f"{MODULE}.staff"
CAMPUS = f"{MODULE}.campus"
CANTEEN = f"{MODULE}.canteen"
LEARNING = f"{MODULE}.learning"
LEARNING_SETUP = f"{MODULE}.learning_setup"
EVALUATIONS = f"{MODULE}.evaluations"
PRIVACY = f"{MODULE}.privacy"

#: Akademi izin anahtarı (kapsamsız) → KM izni.
PERMISSION_MAP: dict[str, str] = {
    # --- görüntüleme: Akademi ekranlarını açabilen herkes
    "dashboard.view": VIEW,
    "students.view": VIEW,
    "schedule.view": VIEW,
    "attendance.view": VIEW,
    "homework.view": VIEW,
    "exams.view": VIEW,
    "books.view": VIEW,
    "curriculum.view": VIEW,
    "resources.view": VIEW,
    "tablet.view": VIEW,
    "canteen.view": VIEW,
    "progress.view": VIEW,
    "study.view": VIEW,
    "goals.view": VIEW,
    "reports.view": VIEW,
    "learning.view": VIEW,         # öğrenme verisi (plan, hata defteri, soru, okuma, ajanda)
    "reading.view": VIEW,
    "calendar.view": VIEW,
    "insights.view": VIEW,         # erken uyarı listesi, kurum içgörüleri
    # --- akademik işler: program, etüt, ödev, dönem/sınıf/grup, zil dilimi
    "academic.manage": ACADEMIC,
    "timetable.manage": ACADEMIC,
    "timetable.edit": ACADEMIC,
    "timetable_templates.manage": ACADEMIC,  # program şablonları (yalnız KM yüzeyi)
    "etut.manage": ACADEMIC,
    "homework.manage": ACADEMIC,
    "goals.suggest": ACADEMIC,
    "calendar.manage": ACADEMIC,   # kurum takvimi (tatil, veli toplantısı, deneme günü)
    # --- müfredat ve kaynak
    "curriculum.manage": CURRICULUM,
    "resources.author": RESOURCES,
    "resources.manage": RESOURCES,
    # --- yoklama (alma, düzeltme ve denetim kaydı, dışa aktarım); yoklama
    # ayarları kurum ayarıdır (yalnız KM yüzeyi)
    "attendance.manage": ATTENDANCE,
    "attendance.correct": ATTENDANCE,
    "attendance.export": ATTENDANCE,
    "attendance.settings": SETTINGS,
    # --- deneme (tanım, sonuç, toplu yükleme, cevap anahtarı, dışa aktarım)
    "exams.manage": EXAMS,
    "exams.import": EXAMS,
    "exams.export": EXAMS,
    # --- öğrenme araçları (F-A18–A24): günlük koçluk işi ve yapısal kurulum ayrı
    "plan.manage": LEARNING,
    "coach_notes.manage": LEARNING,
    "insights.manage": LEARNING,   # erken uyarı: not, görüşme, veliye bildir, çöz/yeniden aç
    "plan_templates.manage": LEARNING_SETUP,
    "reading_lists.manage": LEARNING_SETUP,
    "decks.manage": LEARNING_SETUP,
    "insights.settings": LEARNING_SETUP,
    "evaluation_templates.manage": LEARNING_SETUP,
    "evaluation_periods.manage": LEARNING_SETUP,
    # --- koçluk (draft.3, 6bbcd0b): görüntüleme herkese; günlük koçluk işi
    # (görev onayı, soru-cevap, check-in, tebrik) öğrenme işidir; yapısal
    # kurulum (koç eşlemesi, kütüphane küratörlüğü, rozet, dışa aktarım) kurulum
    # iznidir; koçluk ayarları kurum ayarıdır (yalnız KM yüzeyi).
    "coaching.dashboard": VIEW,
    "coach_assignments.view": VIEW,
    "coach_library.view": VIEW,
    "coaching.review": LEARNING,
    "coach_questions.answer": LEARNING,
    "checkins.manage": LEARNING,
    "kudos.give": LEARNING,
    "coach_assignments.manage": LEARNING_SETUP,
    "coach_library.curate": LEARNING_SETUP,
    "badges.manage": LEARNING_SETUP,
    "coaching.export": LEARNING_SETUP,
    "coaching.settings": SETTINGS,
    # --- mesajlaşma denetimi (draft.3, b973148): başkasının yazışmasını
    # okumak / şikâyete karar vermek hassastır — denetim kaydı gibi yalnız
    # kurum ayarı yetkisiyle (varsayılan admin).
    "messages.moderate": SETTINGS,
    "messages.audit": SETTINGS,
    # --- öğrenci değerlendirme (formu doldurma, yayımlama, rapor)
    "evaluations.manage": EVALUATIONS,
    "evaluations.export": EVALUATIONS,
    # --- kurum cari (kantinden AYRI — A12). Görüntüleme de hassastır.
    "finance.view": FINANCE,
    "finance.manage": FINANCE,
    "finance.record_payment": FINANCE,
    "finance.void": FINANCE_VOID,
    # --- kitap talepleri
    "books.manage": BOOKS,
    # --- duyurular
    "announcements.manage": ANNOUNCEMENTS,
    # --- talepler (izin, görüşme; etüt talebi aşağıda işlemle ayrılır)
    "leave.manage": REQUESTS,
    "meetings.manage": REQUESTS,
    "meetings.request": REQUESTS,
    # --- kurum ayarları (yalnız KM yüzeyi)
    "settings.manage": SETTINGS,
    "branding.manage": SETTINGS,
    "integrations.manage": SETTINGS,
    "canteen.settings": SETTINGS,
    "pa.manage": SETTINGS,
    "audit.view": SETTINGS,
    "audit.export": SETTINGS,
    "directory.sync": SETTINGS,
    # --- personel ve Akademi rolleri (mobil yönetici erişimi KM'dedir)
    "staff.manage": STAFF,
    "roles.manage": STAFF,
    # --- KVKK talep kuyruğu (mağaza, sözleşme §14 madde 41): hesap silme ve
    # veri indirme taleplerine karar — silme onayı kişisel veriyi geri
    # dönüşsüz siler; ayrı ve yalnız yöneticide duran izin.
    "privacy.requests": PRIVACY,
    # --- kurum içi anlık işler Akademi üzerinden (anons, tablet)
    "pa.call": CAMPUS,
    "tablet.control": CAMPUS,
    # --- kantin işlemleri Akademi üzerinden (asıl ekran: Öğrenciler modülü)
    "canteen.controls": CANTEEN,
    "canteen.collect_cash": CANTEEN,
}

#: İşlem kimliğine göre ayrım. Yalnız aynı Akademi izninin iki ayrı KM işine
#: düştüğü yerde kullanılır.
OPERATION_OVERRIDES: dict[str, str] = {
    # Etüt TALEPLERİ "Talepler" kuyruğundadır (sözleşme §2: izin / görüşme /
    # etüt talepleri `bbd_akademi.requests`); etüt DERSİ ise akademik iştir.
    "admin_listEtutRequests": REQUESTS,
    "admin_scheduleEtut": REQUESTS,
    "admin_declineEtut": REQUESTS,
}


def km_permissions(operation: Operation) -> tuple[str, ...]:
    """İşlemi açan KM izinleri — BİRİ yeterli.

    Eşlenmemiş Akademi izni `KeyError` fırlatır: sözleşmeye yeni izin
    eklendiğinde sessizce açık ya da kapalı kalmak yerine test kırılır.
    """
    override = OPERATION_OVERRIDES.get(operation.operation_id)
    if override:
        return (override,)
    keys: list[str] = []
    for permission in operation.permissions:
        mapped = PERMISSION_MAP[permission]
        if mapped not in keys:
            keys.append(mapped)
    return tuple(keys)


def unmapped(operations: Iterable[Operation]) -> list[str]:
    """Eşlemesi olmayan Akademi izinleri (test ve açılış denetimi için)."""
    missing: set[str] = set()
    for operation in operations:
        if operation.operation_id in OPERATION_OVERRIDES:
            continue
        missing.update(p for p in operation.permissions if p not in PERMISSION_MAP)
    return sorted(missing)


#: Panelin bölüm görünürlüğü de AYNI tablodan okunur (`GET km/capabilities`).
ALL_PERMISSIONS: tuple[str, ...] = (
    VIEW, ACADEMIC, CURRICULUM, RESOURCES, ATTENDANCE, EXAMS, FINANCE, FINANCE_VOID,
    BOOKS, ANNOUNCEMENTS, REQUESTS, SETTINGS, STAFF, CAMPUS, CANTEEN, LEARNING, LEARNING_SETUP,
    EVALUATIONS, PRIVACY,
)
