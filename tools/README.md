# tools/

- `module-template/` — yeni modül şablonu. `modules/<id>/` olarak kopyalanır.
- `perf/km_hiz.py` — hız ölçümü (BBD Akademi `docs/PERFORMANCE.md` §4): §1 hacmini
  SQLite'a ya da PostgreSQL'e tohumlar, gerçek uygulamayı sahte bir HTTPS kantinle
  süreç içinde açar ve sıcak yolların p50/p95 süresini, istek başına sorgu ve kantin
  çağrısı sayısını yazar. Kullanım betiğin başında.
