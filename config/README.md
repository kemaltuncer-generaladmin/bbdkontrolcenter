# config/

Ayar öncelik sırası (sonraki öncekini ezer):

```
default.yaml → environments/<env>.yaml → local.yaml → ortam değişkenleri
```

- `default.yaml` — depoya girer, sır içermez
- `environments/` — ortama özel farklar (dev, prod)
- `local.yaml` — makineye özel, **git dışı**, sırlar burada veya
  `km_platform/secrets` kasasında

Modül ayarı modülün kendi `config/default.yaml` dosyasındadır; buradaki
`modules.<id>.*` bloğuyla ezilir. Şemasız ayar kabul edilmez (K8).
