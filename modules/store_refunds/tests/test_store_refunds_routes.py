"""Uç noktaların izin kapısı — kaynak metinden okunur, sunucu ayağa kalkmaz.

`requires(...)` bir `Depends` nesnesi döndürüyor; anahtar kapanışın içinde
kalıyor ve çalışma anında okunamıyor. Bu yüzden dosya `ast` ile ayrıştırılır.
Amaç tek bir soruyu her koşuda yeniden sormak: **izinsiz ya da manifestte
ilan edilmemiş bir uç kaldı mı?** (K9 · ADR 0012)
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

MODULE = Path(__file__).resolve().parents[1]
ROUTES = MODULE / "backend" / "api" / "routes.py"

#: Para harcayan / geri alınamaz uçlar ve TAŞIMASI GEREKEN anahtar. `manage`
#: burada yeterli değildir: yıkıcılık `<id>.manage` içine gizlenmez (ADR 0012).
MONEY_ROUTES = {
    "/approve": "store_refunds.approve",
    "/pos-refund": "store_refunds.approve",
    "/orders/{order_id}/return-shipment": "store_refunds.ship_return",
}


def _endpoints() -> dict[str, set[str]]:
    """`{yol: {izin anahtarları}}` — kaynak metinden."""
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        path = ""
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "router"
                    and decorator.args):
                path = ast.literal_eval(decorator.args[0])
        if not path:
            continue
        keys: set[str] = set()
        for default in node.args.defaults:
            if (isinstance(default, ast.Call) and isinstance(default.func, ast.Name)
                    and default.func.id == "requires"):
                keys.update(ast.literal_eval(argument) for argument in default.args)
        found[path] = keys
    return found


def test_her_ucta_izin_kapisi_vardir() -> None:
    endpoints = _endpoints()
    assert endpoints, "Uç bulunamadı — ayrıştırma bozulmuş olabilir."
    izinsiz = [path for path, keys in endpoints.items() if not keys]
    assert izinsiz == [], f"İzin ilan etmeyen uç: {izinsiz}"


def test_kullanilan_her_anahtar_manifestte_ilan_edilir() -> None:
    manifest = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))
    declared = {item["key"] for item in manifest["permissions"]}
    used = {key for keys in _endpoints().values() for key in keys}
    assert used <= declared, f"Manifestte olmayan anahtar: {sorted(used - declared)}"


def test_para_harcayan_uclar_ayri_anahtar_ister() -> None:
    endpoints = _endpoints()
    for path, key in MONEY_ROUTES.items():
        assert endpoints.get(path) == {key}, f"{path} yanlış anahtarla korunuyor."


def test_magaza_izinleri_yikici_bayragi_tasimaz() -> None:
    # `destructive: true` çekirdekte PIN kapısına bağlanacak; mağaza PIN istemez.
    manifest = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))
    assert all("destructive" not in item for item in manifest["permissions"])
