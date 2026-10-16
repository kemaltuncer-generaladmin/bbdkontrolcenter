# Güvenlik modeli — Kontrol Merkezi ve BBD Akademi

Bağlayıcı kurallar: [CLAUDE.md](../../CLAUDE.md) (K8 sır, K9 çift kapı, K10 rol adı sorulmaz),
[permissions.md](../permissions.md) (izin kataloğu ve kural 3 — PIN teyidi), ADR 0007 (kimlik),
0012 (gerekçeli onay), 0026 (sunucu merkezli), 0028 (servis anahtarı), 0029 (outbox imzası),
0030 (gerçek istemci adresi). Son denetim: [bulgular-2026-10-16.md](bulgular-2026-10-16.md).
Uç başına durum (üretilir): [endpoint-envanteri.md](endpoint-envanteri.md).

## 1. Varlıklar

| Varlık | Nerede | Sınıf |
|---|---|---|
| Öğrenci/veli kimliği, telefon, sınıf | KM `bbd_students` (ana kayıt), Akademi aynası | kişisel (KVKK) |
| Kantin bakiyesi, harcama, ödeme talebi | kantin backend'i, KM `bbd_*` | finansal + kişisel |
| Kurum carisi (taksit, tahsilat) | Akademi `finance` | finansal + kişisel (kantinle KARIŞMAZ — A12) |
| Değerlendirme, koçluk, mesaj, hata defteri, ders kaydı | Akademi | kişisel (çocuk verisi) |
| Mağaza siparişi/müşteri | BBD Store (KM vekil) | kişisel + finansal |
| PIN, oturum belirteci, servis/entegrasyon anahtarı, pepper, kasa | KM `users`/`sessions`/kasa | sır |

## 2. Güven sınırları

```
 Tauri kabuğu (masaüstü)  ──HTTPS + Bearer(oturum)──▶  KM sunucu (FastAPI, Coolify/Traefik/Cloudflare)
   CSP: script/style 'self'                              │  izin kapısı (router taban izni ∧ uç requires)
   core_request yalnız server_base'e                     │  PIN step-up (kural 3) / gerekçeli onay (ADR 0012)
                                                         ├──entegrasyon anahtarı (aksk_)──▶ BBD Akademi API
                                                         │     vekil: allowlist.py ∧ permission_map.py ∧ step-up
                                                         ├──servis anahtarı (kmsk_) ◀── kantin backend'i (olay itme)
                                                         ├──imzalı outbox webhook (X-KM-Signature t=,v1=) ──▶ Akademi
                                                         └──Bagisto/BLD API, SSH/DB platform yetenekleri
 Kurum tableti ──cihaz belirteci──▶ KM `/api/tablet/v1` (http.device_router; izin kapısı dışında, kendi kimliği)
 Mobil/web (öğrenci, veli, öğretmen) ──OTP/öğrenci kodu → JWT──▶ Akademi (Postgres RLS + rol kapsamı)
 Ödeme sitesi ──X-Bbdstore-Signature + event_id tekilliği + yaş sınırı──▶ Akademi webhook
```

Her sınırda kimlik **o sınırın kendi sırrıyla** doğrulanır; hiçbir başlık (`X-KM-On-Behalf-Of`,
`X-Akademi-On-Behalf-Of`, `X-Request-Id`) yetki vermez, yalnız denetim izine yazılır.

## 3. Tehdit modeli (özet)

| Tehdit | Karşı önlem |
|---|---|
| İnternetten PIN kaba kuvveti | IP + genel hız sınırı; `/auth/login` ve `/auth/set-password` AYNI kovada (KM-01) |
| Ele geçmiş/açık bırakılmış oturum | 30 dk boşta + 12 sa mutlak ömür (KM-04); yetki veren ve yıkıcı işlemde PIN teyidi, art arda 5 yanlışta 15 dk kapanır (KM-02, KM-06) |
| PIN sıfırlandıktan sonra eski belirteç | PIN değişince kişinin oturumları silinir (KM-03) |
| Yetkisiz uç / unutulmuş izin | K9: izin ilan etmeyen router monte edilmez; envanter testi kimliksiz uçları gerekçeli listeye bağlar |
| KM üzerinden Akademi'de yetki yükseltme | vekil: liste dışı yol 403, haritasız izin 403 (fail-closed), yazma görüntüleme iznine eşlenmez |
| Kiracılar arası sızıntı (Akademi) | Postgres FORCE RLS `tenant_id = app.tenant_id`; uygulama rolü BYPASSRLS değil, tablo sahibi değil |
| Aynı kurumda başka öğrencinin verisi (IDOR) | rol kapsamı (`self`/`children`/`taught`/`coached`/`tenant`) tek yerde: `akademi/auth/scopes.py` + `auth/deps.student_for` / `accessible_student_ids` |
| Webhook sahteleme / yeniden oynatma | HMAC + zaman toleransı (KM olayları), `event_id` tekilliği + `paid_at` yaş sınırı (ödeme) |
| SSRF | Dış URL çeken yollar sabit kökenlidir: YouTube (oEmbed/Data API), vekil indirme yalnız Akademi kökeni + `download_hosts` |
| Tıklama kaçırma / içerik koklama | KM yanıtlarında nosniff, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer` (KM-08) |

## 4. İzin modeli

- **KM**: rol = izin kümesi, kişi birden çok rol taşır. Router'ın taban izni `module.yaml → http.requires`,
  uç `requires(...)` ile daraltır (gruplar arası VE, grup içi VEYA). Yıkıcı izinler manifestte
  `destructive: true`; uçta PIN (`km_sdk.confirm_pin`) ya da mağazada gerekçeli onay (ADR 0012).
  Yetki veren çekirdek işlemler (kullanıcı açma, rol değiştirme, başkasının PIN'ini sıfırlama)
  gövdede `confirmPin` ister (KM-06; acil şalter `auth.admin_step_up: false`).
- **KM → Akademi**: Akademi KM'yi tek entegrasyon anahtarıyla görür; kişi başına yetkinin TAMAMI
  KM'dedir: `allowlist.py` (sözleşmeden üretilir) ∧ `permission_map.py` (Akademi izni → tek KM izni)
  ∧ step-up (`x-step-up` + `KM_STEP_UP_OPERATIONS`, ör. KVKK kararı).
- **Akademi**: izin + ilişki kapsamı (`izin:kapsam`), rol adıyla dal yok (A2). Öğretmen/koç kapsamı
  kurum ayarı `teacher.scope` (`related` varsayılan) ile genişler.

## 5. RLS ve kapsam modeli (Akademi)

- Kiracıya ait her tablo `tenant_id` taşır; göç `akademi.db.rls.enable_rls` ile ENABLE + FORCE +
  `tenant_isolation` politikası (USING + WITH CHECK) kurar. İstisnalar yalnız `GLOBAL_TABLES`
  (kiracı çözümünden önce okunanlar) ve küresel okuma kataloğu.
- Mantıksal RLS: öğrenci yalnız kendisi (`self`), veli yalnız çocukları (`children`), öğretmen ders
  verdiği (`taught` — program satırı, sınıf sorumluluğu, son 30/gelecek 14 gün oturumları, etüt),
  koç yalnız bugün etkin eşlemesi olan öğrenciler (`coached`). Kapsam dışı öğrenci `404`.

## 6. Denetim araçları (CI'da koşar)

| Test | Ne denetler | Komut |
|---|---|---|
| KM `tests/core/test_guvenlik_envanteri.py` | kimliksiz uç yalnız gerekçeli listede; izinsiz yazma yok; her izin ilan edilmiş ve `permissions.md`'de; yıkıcı/yetki veren yazma step-up ister; envanter belgeleri güncel | `.venv/bin/python -m pytest tests/core/test_guvenlik_envanteri.py` |
| KM `tests/core/test_guvenlik_kimlik.py` | KM-01…KM-08 regresyonları | aynı biçim |
| KM `modules/bbd_akademi/tests/test_akademi_vekil_guvenlik.py` (+ `test_akademi_allowlist.py`) | vekil izin listesi ↔ harita tutarlılığı, fail-closed | aynı biçim |
| Akademi `tests/integration/test_rls_kapsami.py` | `pg_catalog`'dan: her `tenant_id`'li tablo ENABLE+FORCE+politika; kiracısız tablolar gerekçeli | `uv run pytest tests/integration/test_rls_kapsami.py` |
| Akademi `tests/integration/test_idor_matrisi.py` | bütün tohumlarla gerçek kayıt kimlikleri; veli/öğrenci/koç/başka kurum × şemadaki her parametreli uç | `uv run pytest tests/integration/test_idor_matrisi.py` (~1–2 dk) |
| Akademi `tests/integration/test_rol_kapsam_taramasi.py` | `{studentId}`'li her uç ilişkisiz kişiye kapalı | — |

Envanter belgesini yenilemek (uç ekleyen her değişiklikten sonra; test eskiyse kırılır):
`.venv/bin/python tools/security/endpoint_inventory.py` (`--check` yalnız denetler).

## 7. Canlı yapılandırma önerileri (lider uygular)

- Cloudflare/Traefik: `Strict-Transport-Security: max-age=31536000; includeSubDomains` (uygulama TLS'i görmez).
- `auth.session_absolute_hours` (varsayılan 12), `auth.step_up_max_failures` (5) /
  `auth.step_up_window_minutes` (15), `auth.admin_step_up` (true) — `config/default.yaml`.
- Bağımlılık taraması CI'a: `pip-audit` (KM, Akademi `uv export | pip-audit -r -`), `npm audit --omit=dev`
  (Akademi web) — bu denetimde çevrimdışıydı.
