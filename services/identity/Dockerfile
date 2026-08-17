# ---------------------------------------------------------------------------
# DERLEME BAĞLAMI DEPO KÖKÜDÜR — `services/identity` DEĞİL.
#
#     docker build -f services/identity/Dockerfile .
#     (Coolify: "Base Directory" = /  ·  "Dockerfile Location" = services/identity/Dockerfile)
#
# Nedeni: bu servis kimlik kodunu SIFIRDAN YAZMAZ (ADR 0021 — Sonuçlar).
# `backend/src/km_core` içindeki şema, Argon2id ve izin modeli olduğu gibi
# kullanılır; onu COPY edebilmek için bağlamın depo kökü olması gerekir.
# Bağlam `services/identity` yapılırsa imaj derlenir ama `km_core` bulunamaz
# ve konteyner açılışta `ModuleNotFoundError` ile ölür.
# ---------------------------------------------------------------------------

# SÜRÜM SABİTLENİR. `python:3-slim` bir gün 3.15'e atlar ve o gün servis
# haber vermeden başka bir yorumlayıcıda çalışmaya başlar.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend/src:/app

WORKDIR /app

# argon2-cffi tekerlek (wheel) olarak gelir; derleyici gerekmez. Bu yüzden
# build-essential KURULMAZ — imajı üç katına çıkarır ve saldırı yüzeyini
# genişletirdi.
COPY services/identity/requirements.txt /app/services/identity/requirements.txt
RUN pip install --no-cache-dir -r /app/services/identity/requirements.txt

# PAYLAŞILAN ÇEKİRDEK. `km_platform` ve `modules` KOPYALANMAZ: servis onlara
# dokunmaz ve kopyalanan her klasör imajda taşınacak fazladan yüktür.
COPY backend/src/km_core /app/backend/src/km_core
COPY services/identity/app /app/services/identity/app
COPY services/identity/__init__.py /app/services/identity/__init__.py
COPY services/__init__.py /app/services/__init__.py

# Kalıcı disk buraya bağlanır (Coolify → Persistent Storage). Bağlanmazsa
# veritabanı konteynerle birlikte her dağıtımda silinir.
VOLUME ["/data"]
ENV KM_IDENTITY_DB_PATH=/data/identity.sqlite

# Kök olarak çalışmaz.
RUN useradd --system --uid 10001 --create-home identity \
    && mkdir -p /data && chown -R identity:identity /data /app
USER identity

EXPOSE 8000

# GÖÇ AÇILIŞTA KOŞAR: `create_app` lifespan'i içinde `apply_core_migrations` ve
# `apply_service_migrations` çağrılır (services/identity/app/main.py). Ayrı bir
# `migrate` adımı yoktur — unutulacak bir adım, yarım şema demektir.
CMD ["uvicorn", "services.identity.app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
