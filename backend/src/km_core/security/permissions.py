"""Çekirdek izinleri (docs/permissions.md → "Çekirdek izinleri").

Bunları çekirdek tanımlar; modüller tanımlayamaz. Modül izinleri
`module.yaml` içinde ilan edilir ve çekirdek yalnızca uygular (K10).
"""

from __future__ import annotations

from typing import Any

CORE_PERMISSIONS: list[dict[str, Any]] = [
    # kullanıcı ve rol
    {"key": "users.view", "default_roles": ["admin"]},
    {"key": "users.manage", "default_roles": ["admin"]},
    {"key": "users.set_pin", "default_roles": ["admin"]},
    {"key": "roles.view", "default_roles": ["admin"]},
    {"key": "roles.manage", "default_roles": ["admin"]},
    # sistem
    {"key": "settings.view", "default_roles": ["admin"]},
    {"key": "settings.manage", "default_roles": ["admin"]},
    {"key": "audit.view", "default_roles": ["admin"]},
    {"key": "secrets.view", "default_roles": ["admin"]},
    {"key": "secrets.manage", "default_roles": ["admin"]},
    # sunucu ve ssh — kapsamlı
    {"key": "servers.view", "scoped": True, "default_roles": ["admin", "bld_staff", "bbd_staff"]},
    {"key": "servers.manage", "scoped": True, "default_roles": ["admin"]},
    {"key": "ssh.execute", "scoped": True, "default_roles": ["admin", "bld_staff", "bbd_staff"]},
    {"key": "ssh.transfer", "scoped": True, "default_roles": ["admin", "bld_staff", "bbd_staff"]},
    # veritabanı — kapsamlı
    {"key": "database.view", "scoped": True, "default_roles": ["admin", "bld_staff", "bbd_staff"]},
    {"key": "database.query", "scoped": True, "default_roles": ["admin", "bld_staff", "bbd_staff"]},
    {"key": "database.write", "scoped": True, "default_roles": ["admin", "bld_staff", "bbd_staff"]},
    {"key": "database.backup", "scoped": True, "default_roles": ["admin", "bld_staff", "bbd_staff"]},
    {"key": "database.restore", "scoped": True, "destructive": True, "default_roles": ["admin"]},
    # rehber
    {"key": "directory.view", "default_roles": ["admin", "bld_staff", "bbd_staff", "org_staff", "accountant"]},
    {"key": "directory.view_external", "default_roles": ["admin", "bld_staff", "bbd_staff", "org_staff", "accountant"]},
    {"key": "directory.manage", "default_roles": ["admin"]},
]

CORE_PERMISSION_KEYS = {permission["key"] for permission in CORE_PERMISSIONS}
