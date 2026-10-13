# ADR 0029 — Olay outbox'u ve modül işlemi

**Durum:** Kabul edildi · 12.10.2026
**Etkilediği:** `km_core/store` (`transaction.py`, `db.py`, `postgres.py`,
`base.py`, `bootstrap.py`), `km_core/contracts/module.py` (`ModuleStore`,
`ScopedStore`), `km_platform/events/` (yeni platform yeteneği `events.outbox`),
`km_core/http/app.py` (yetenek kaydı, `GET /api/events/wait`)
**Bağlam belgesi:** bbd-akademi `docs/integrations/km-merkezi-senkron.md` §3.1, §8

## Bağlam

Kullanıcı isteği: *"Amaç merkeziyetçilik. Kontrol Merkezi'nden bir öğrenci
yönetiliyorsa bu kantini de BBD mobil uygulamayı da etkilemeli."* KM'de olan
her öğrenci / veli / grup / kantin / tablet / zil değişikliği BBD Akademi'ye
**bir saniye içinde** ve **kaybolmadan** ulaşmalı.

İki eksik vardı:

- **Modül deposunda işlem yoktu.** `ModuleStore` yalnız tek ifadelik
  `execute` sunuyordu; SQLite'ta her ifade kendi commit'iydi, PostgreSQL'de
  her çağrı havuzdan başka bir bağlantı alabiliyordu. Gruplar (`bbd_students`)
  bu yüzden bir süreç kilidine dayanıyordu ve "işlem açılamıyor" notu
  taşıyordu; öğrenci ekleme önce kantine sonra profile yazıyor, arada patlarsa
  yarım kayıt kalıyordu.
- **Olay yalnız süreç içiydi.** `bus` yayını (ör. `bbd_students.groups_changed`)
  KM içindeki dinleyiciler içindir; süreç düşerse ya da dinleyici patlarsa olay
  kaybolur. Dış dünyaya "kesin iletilen" bir yol yoktu.

## Karar

**1. Modül işlemi.** `ModuleStore.transaction()` (ve çekirdekte
`StoreLike.transaction()`) eşzamansız bağlam yöneticisidir:

- PostgreSQL: havuzdan bir bağlantı + `connection.transaction()`.
- SQLite: tek bağlantı paylaşıldığı için **depo kilidi** altında
  `BEGIN IMMEDIATE … COMMIT`. İşlem dışındaki her çağrı kilidi kısa süre alır;
  böylece başka bir görevin ifadesi açık işleme karışmaz.
- İşlem bağlantısı `contextvars` ile taşınır; işlem içindeki her `fetch_*` /
  `execute` (modülün `ScopedStore`'u ya da bir platform yeteneği) aynı
  bağlantıya gider. **İç içe çağrı dış işleme katılır** (kayıt noktası yok).
- Kancalar: `before_commit` (aynı bağlantıda, hata → geri alma),
  `after_commit`, `after_end` (her durumda bir kez).
- İşlem gövdesinde dış çağrı (ağ, başka görevi beklemek) **yapılmaz**.
  `execute_script` işlem içinde reddedilir (`executescript` örtük commit eder).
- Yalıtım PostgreSQL varsayılanıdır (READ COMMITTED). Sayaç gibi
  okuma-değiştirme-yazma adımları `x = x + 1` ile yazılır ve işlemin İLK yazması
  yapılır: satır kilidi aynı sayacı artıran işlemleri commit'e kadar sıralar,
  sonraki doğrulama okumaları öncekinin commit'ini görür. Grupların süreç
  kilidi bu kalıpla işleme çevrildi (davranış aynı).

**2. Platform yeteneği `events.outbox`.** `km_platform/events/` — silinmez
altyapı (ADR 0006 kalıbı), registry'de `events.outbox` adıyla, modüller
`consumes` ile çözer (K3).

- Tablolar `km_event_outbox(seq, event_id UNIQUE, type, subject_kind,
  subject_id, revision, fields, data, origin, causation_id, occurred_at,
  created_at)` ve `km_event_consumer(name, last_seq, updated_at)`; çekirdek
  sahipli eklemeli göçler `0012_event_outbox`, `0013_event_outbox_indexes`
  (`-- km: concurrently`). Şemayı yine `build_schema` kurar.
- `emit(type, subject, *, revision, fields, data, origin, causation_id)` açık
  işlemin içinde çağrılır ve olayı **o işleme** bağlar; işlem yoksa kendi kısa
  işlemini açar. Satır commit'ten hemen önce **outbox kilidi altında** yazılır,
  kilit commit'ten sonra bırakılır: `seq` sırası = commit sırası, tüketici
  imleci boşluk atlamaz. (Boşluk yalnız geri alınan işlemden doğar.)
- Zarf API sözleşmesindeki `KmEvent` ile birebirdir (camelCase). `data`
  kişisel veri taşımaz: ad / telefon / kod anahtarları kodla reddedilir, 2 KB
  üst sınırı vardır (A6).
- `read_after(seq, limit, types)`, `last_seq()`, `wait_after(seq, timeout)`
  (süreç içi `asyncio.Event`; veritabanı yoklanmaz), `expired(after)`.
- Tüketici imleçleri: `register_consumer`, `consumer_seq(name, seq)`.
- **Saklama.** Gece budaması: 14 günden eski **ve** bütün tüketicilerin geçtiği
  olaylar silinir ("hangisi uzunsa"). Ek sert sınır **30 gün**: takılmış ya da
  kapatılmış bir tüketici outbox'u sonsuza büyütmez; o tüketici tam uzlaştırma
  yapar (`directory.resync_required`, geçitte `410 events_expired`). En yeni
  satır budanmaz (`seq` sayacı yeniden başlatılmadan okunabilsin).
- `on_close(hook)`: kapanışta boşaltma (kantin bakiye birleştirme tamponu).
- Süreç içi olaylar (`bus`) **korunur**; dış dünyaya giden yol outbox'tır.

**3. Panel için uzun yoklama.** `GET /api/events/wait?after=&types=&timeout=25`
— oturumu olan panel kullanıcısı; olay kişisel veri taşımadığı için ayrı izin
istemez (ekran verisini yine kendi izniyle okur). `after` yoksa başlangıç imleci
döner; budanmış bölgedeki `after` → `reset: true`.

## Gerekçe

- **Aynı işlem = kaybolmayan olay.** Olay iş yazmasıyla birlikte commit edilir;
  "yazıldı ama duyurulmadı" ya da "duyuruldu ama geri alındı" durumu yoktur.
  İletim (KM → Akademi webhook'u) outbox'u imleçle okur; ağ kesilse de olay
  bekler.
- **Commit sırası imleci güvenli kılar.** Seq ekleme anında verilseydi, önce
  seq alıp sonra commit eden yavaş bir işlem, tüketici imleci onun üstünden
  geçtikten sonra görünür olur ve olay atlanırdı.
- **Kilit kısa.** Kilit yalnız commit anında (birkaç `INSERT` + `COMMIT`)
  tutulur; iş işlemleri paralel koşar.
- **Tek süreç.** KM sunucusu tek süreçtir (ADR 0026). Çok süreçli kurulumda
  süreç içi kilit yetmez: o zaman `_write` adımı aynı işlemde
  `SELECT pg_advisory_xact_lock(<sabit>)` alır (kilit commit/geri almada
  kendiliğinden düşer) ve `wait_after` için `LISTEN/NOTIFY` eklenir. Bugün
  gerekmediği için yazılmadı.

## Sonuçlar

- Modül, iş yazmasını ve olayını tek işlemde yapar:
  `async with ctx.store.transaction(): … ; await outbox.emit(…)`.
- İşlem içinde dış çağrı yasağı kod incelemesinde aranır; ihlal SQLite'ta bütün
  depoyu, PostgreSQL'de bir havuz bağlantısını işlem süresince tutar.
- SQLite'ta işlem dışı okumalar açık işlem bitene kadar bekler (kirli okuma
  yok). İşlemler kısa olduğu için fark ölçülemez.
- Testler: `tests/core/test_modul_islemi.py`, `tests/core/test_olay_outbox.py`
  — iki lehçe (PostgreSQL `KM_TEST_POSTGRES_DSN` ile).
