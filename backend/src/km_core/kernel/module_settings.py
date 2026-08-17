"""`module.yaml` → `settings` bloğunun doğrulanması (ADR 0018 §1).

Modül, Sistem Ayarları ekranındaki kendi sekmesini manifestinde ilan eder.
Çekirdek sekme başlıklarını sabit bir listede tutmaz; blok **veri** olarak
okunur (K1).

İki kural bu dosyanın biçimini belirler:

* **Geçersiz blok modülü düşürmez** (K7). Bir alan başlığındaki hata yüzünden
  modülün tamamının yüklenmemesi orantısız olurdu: modül sekmesiz yüklenir,
  hata loglanır. Bu yüzden yapı `module.schema.json` içinde manifest
  doğrulamasının dışında, `$defs/settings` altında durur ve burada AYRI bir
  kapıda uygulanır.
* **İzin ilan etmeyen blok reddedilir** (K9). Varsayılan kapalıdır; sekmeyi
  görmek ve yazmak için gereken izin `requires` ile ilan edilir. Ekranda
  gizlemek yetkilendirme değildir — ayar yazan uç nokta aynı izni bağımsız
  olarak sorar.

Şemanın tek kaynağı `docs/schemas/module.schema.json`. Yapı burada yeniden
yazılmaz; okunur ve uygulanır.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Tip saplaması `types-jsonschema` ile gelir ve `backend/pyproject.toml`
# içinde ilan edilir (K11); susturma gerekmez.
import jsonschema

#: Şema dosyasındaki alt şemanın adı: `#/$defs/settings`.
DEFINITION = "settings"


class SettingsSchema:
    """`$defs/settings` doğrulayıcısı.

    Şema dosyası okunamazsa ya da alt şema yoksa doğrulayıcı **boş** kalır ve
    her blok reddedilir. Sessizce "geçti" demek, kapıyı sahte yeşile boyardı.
    """

    __slots__ = ("_validator",)

    def __init__(self, validator: Any) -> None:
        self._validator = validator

    @classmethod
    def load(cls, schema_path: Path) -> SettingsSchema:
        try:
            document: object = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(None)
        if not isinstance(document, dict):
            return cls(None)

        definitions = document.get("$defs")
        if not isinstance(definitions, dict) or DEFINITION not in definitions:
            return cls(None)

        # Alt şema, kendi `$defs` sözlüğüyle birlikte ayrı bir belge gibi
        # kurulur; iç göndermeler (`#/$defs/settings_group`) böylece çözülür.
        subschema: dict[str, Any] = {
            "$schema": document.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
            "$ref": f"#/$defs/{DEFINITION}",
            "$defs": definitions,
        }
        try:
            return cls(jsonschema.Draft202012Validator(subschema))
        except jsonschema.exceptions.SchemaError:
            return cls(None)

    def validate(self, manifest_data: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """Bloğu doğrular.

        Dönüş: (blok, hata). Blok yoksa (None, "") — ayar sekmesi ilan etmemek
        hata değildir. Blok varsa ve geçersizse (None, neden): modül yüklenmeye
        devam eder, yalnız sekmesi düşer.
        """
        block = manifest_data.get("settings")
        if block is None:
            return None, ""
        if not isinstance(block, dict):
            return None, "settings bloğu nesne değil"

        # Önce izin kapısı: neden "şemaya uymuyor" değil, "izin ilan etmemiş"
        # olarak okunmalı — yönetici eksiği bir bakışta görür (K9).
        if not block.get("requires"):
            return None, "settings bloğu izin ilan etmiyor — reddedildi (K9, varsayılan kapalı)"

        if self._validator is None:
            return None, "settings şeması okunamadı"

        errors = sorted(self._validator.iter_errors(block), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            where = "/".join(str(part) for part in first.path) or "kök"
            return None, f"settings bloğu şemaya uymuyor ({where}): {first.message}"

        return block, ""
