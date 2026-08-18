# ---------------------------------------------------------------------------
# KM SUNUCU — Kontrol Merkezi'nin tamamı (çekirdek + 49 modül).
#
# DERLEME BAĞLAMI DEPO KÖKÜDÜR:
#
#     docker build -f deploy/server/Dockerfile .
#     (Coolify: "Base Directory" = /  ·  "Dockerfile Location" = deploy/server/Dockerfile)
#
# Bağlam `deploy/server` yapılırsa imaj derlenir ama `km_core` ve `modules`
# bulunamaz; konteyner açılışta `ModuleNotFoundError` ile ölür. Aynı tuzak
# `services/identity/Dockerfile` başlığında da yazılıdır — iki kez düşüldü.
#
# ADR 0026 — masaüstü uygulaması artık ince kabuktur ve BU sunucuya bakar.
# Veri PostgreSQL'dedir; kurulumların kendi veritabanı yoktur.
# ---------------------------------------------------------------------------

# SÜRÜM SABİTLENİR. `python:3-slim` bir gün 3.15'e atlar ve o gün sunucu haber
# vermeden başka bir yorumlayıcıda çalışmaya başlar.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend/src:/app \
    # PyPI'den okuma zaman aşımına düşünce derleme komple çöküyordu. Varsayılan
    # 15 sn ve tek deneme, 21 paketlik bir kurulum için fazla kırılgan.
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /app

# Sistem paketleri. Liste `deploy/packaging/system-packages.yaml` ile aynı
# gerekçelere dayanır ama SUNUCUNUN ihtiyacına indirgenmiştir:
#
#   poppler-utils   → `pdftoppm`; 22 modülün rapor ÖNİZLEMESİ buna bağlı.
#                     Yoksa rapor yine üretilir ve indirilir, yalnız önizleme
#                     çıkmaz (K7) — ama burada kurmak ucuz ve tam çalışsın.
#   fonts-dejavu-core → öğrenci kartlarındaki Türkçe karakterler (bbd_students).
#   mariadb-client  → BBD/BLD yedekleme-geri yükleme (`mysqldump`).
#   openssh-client  → `km_platform/ssh`.
#
# `cups`, `hplip`, `pipewire` ve `clamav` KURULMAZ: sunucuda yazıcı ve
# hoparlör yok, antivirüs taraması makinelerin kendi işi.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        poppler-utils \
        fonts-dejavu-core \
        mariadb-client \
        openssh-client \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# ZİLİN SAATİ. Konteyner varsayılan olarak UTC'de koşar; zamanlayıcı ise
# "pazartesi 08:40" derken OKULUN saatini kastediyor. `tzdata` olmadan
# `ZoneInfo("Europe/Istanbul")` bulunamaz ve zamanlayıcı makinenin yereline
# (UTC) düşer: 08:40'a kurulan zil İstanbul'da 11:40'ta çalıyordu ve belirti
# "zil çalmıyor"du.
#
# `TZ` ikinci bir güvencedir, tek dayanak değil: zamanlayıcı saat dilimini
# ayrıca `platform.scheduler.timezone` ayarından alıyor. Buradaki değer günlük
# satırlarının ve `date` çıktısının da ekranda görülen saatle okunmasını sağlar.
ENV TZ=Europe/Istanbul

# Uygulama. `apps/desktop` KOPYALANMAZ — kabuk kullanıcının makinesinde durur.
COPY backend /app/backend
COPY modules /app/modules
COPY config /app/config
COPY docs/schemas /app/docs/schemas

# Bağımlılıklar İLAN EDİLDİĞİ YERDEN toplanır (K11): `backend/pyproject.toml`
# ve her modülün kendi `dependencies.python` bloğu. İkinci bir liste
# tutulsaydı er geç ayrışır ve modülün biri YALNIZ ÜRETİMDE import hatasıyla
# düşerdi.
COPY deploy/server/collect-requirements.py /tmp/collect-requirements.py
RUN pip install --no-cache-dir pyyaml \
    && python /tmp/collect-requirements.py /app > /tmp/requirements.txt \
    && cat /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/collect-requirements.py /tmp/requirements.txt

# Kalıcı disk (Coolify → Persistent Storage). Bağlanmazsa üretilen PDF'ler,
# anons sesleri ve dışa aktarımlar her dağıtımda silinir.
VOLUME ["/data"]
ENV KM_DATA_DIR=/data

# ADR 0026 — depo motoru. `KM_CENTRAL_DSN` Coolify'da tanımlanır ve BU
# İMAJDA YOKTUR (K8). Tanımsızsa açılış `EngineError` ile durur; sessizce
# SQLite'a düşüp herkesin verisini geçici diske yazmaktan iyidir.
ENV KM_STORE_ENGINE=postgres

# Kök olarak çalışmaz.
RUN useradd --system --uid 10001 --create-home kmsunucu \
    && mkdir -p /data && chown -R kmsunucu:kmsunucu /data /app
USER kmsunucu

EXPOSE 8000

# GÖÇ AÇILIŞTA KOŞAR: `create_app` lifespan'i içinde `build_schema` ve
# kernel'in modül göçleri çağrılır. Ayrı bir `migrate` adımı yoktur — atlanacak
# bir adım, yarım şema demektir.
CMD ["uvicorn", "km_core.http.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
