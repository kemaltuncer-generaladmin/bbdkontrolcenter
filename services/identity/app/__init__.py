"""Merkezî kimlik servisi (ADR 0021).

Kontrol Merkezi'nin PARÇASI DEĞİLDİR: ayrı bir Coolify uygulaması olarak, kendi
veritabanıyla, BLD sunucusunda çalışır. Adres: `kontrolmerkezi.bbdstore.com.tr`.

Kimlik kodu SIFIRDAN YAZILMADI (ADR 0021 — Sonuçlar): `km_core/security/identity.py`
ve `km_core/store/db.py` aynı şemayı, aynı Argon2id'yi ve aynı izin modelini zaten
taşıyor. Servis onları OLDUĞU GİBİ import eder; kopyalanmış tek satır yoktur.
Bu yüzden imajın derleme bağlamı depo köküdür (bkz. `Dockerfile` başlığı).
"""
