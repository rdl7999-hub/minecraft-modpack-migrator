#!/usr/bin/env python3
"""
comparar-packs.py — compara dos indices y dice que se puede portar y a que coste.

Contesta tres preguntas, en este orden:

  1. ¿Son compatibles?  version de Minecraft y loader. Si no lo son, copiar
     jars es imposible y solo se salvan datapacks, resourcepacks y configs.
  2. ¿Que tiene X que no tenga Y?  los candidatos a portar.
  3. ¿Que me falta si me llevo este mod?  cierre transitivo de dependencias,
     contando como satisfechas las que Y ya tiene, incluidas las anidadas.

Uso:
    python comparar-packs.py <indice-X.json> <indice-Y.json> [--si-porto id[,id2]]
                             [--salida plan-dependencias.json]

Los indices se generan con escanear-pack.py.
Solo biblioteca estandar. Python 3.9+.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(encoding="utf-8", errors="replace")

# Dependencias que siempre las aporta el loader o el propio juego: si las
# cuentas como "faltantes" el informe se llena de ruido.
IMPLICITAS = {
    "minecraft", "java", "fabricloader", "fabric-loader", "quilt_loader",
    "quilt_base", "forge", "neoforge", "mcp", "fml", "mixinextras",
}


def cargar(ruta: str) -> dict:
    p = Path(ruta)
    if not p.is_file():
        print(f"ERROR: no existe {p}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(p.read_text("utf-8"))


def familia(version: str) -> str:
    m = re.match(r"(1\.\d{1,2})", str(version))
    return m.group(1) if m else str(version)


def ids_disponibles(indice: dict) -> set[str]:
    """Todo lo que un pack ya ofrece: mods sueltos + jars anidados."""
    ids = {m["id"].lower() for m in indice.get("mods", []) if m.get("id")}
    ids |= {a["id"].lower() for a in indice.get("anidados", []) if a.get("id")}
    # Los modulos de la Fabric API van anidados y se declaran uno a uno.
    if any(i.startswith("fabric-api") or i == "fabric" for i in ids):
        ids.add("fabric")
    return ids


def veredicto(x: dict, y: dict) -> tuple[str, list[str]]:
    notas: list[str] = []
    mc_x, mc_y = str(x.get("minecraft", "?")), str(y.get("minecraft", "?"))
    ld_x, ld_y = str(x.get("loader", "?")).lower(), str(y.get("loader", "?")).lower()

    familias = familia(mc_x) == familia(mc_y)
    exactas = mc_x == mc_y
    # Quilt ejecuta mods de Fabric; al reves no.
    loader_ok = (ld_x == ld_y) or (ld_x == "fabric" and ld_y == "quilt")

    if loader_ok and exactas:
        nivel = "VERDE"
        notas.append("Misma version y mismo loader: se pueden portar jars, "
                     "datapacks, resourcepacks y configs.")
    elif loader_ok and familias:
        nivel = "AMARILLO"
        notas.append(f"Mismo loader pero {mc_x} contra {mc_y}. Los jars suelen ir, "
                     "pero hay riesgo de NoSuchMethodError en runtime.")
        notas.append("Los datapacks pueden necesitar ajuste: entre versiones cambian "
                     "rutas y formatos (recipes/ pasa a recipe/ en 1.21).")
    elif loader_ok:
        nivel = "ROJO"
        notas.append(f"Mismo loader pero versiones lejanas ({mc_x} contra {mc_y}): "
                     "portar jars no va a funcionar.")
        notas.append("Solo son portables datapacks y resourcepacks, y con ajustes.")
    else:
        nivel = "ROJO"
        notas.append(f"Loaders distintos: {ld_x} contra {ld_y}. Un jar de uno NO "
                     "carga en el otro, por mucho que coincida la version.")
        notas.append("Portable: datapacks (data/) y resourcepacks (assets/), que son "
                     "vanilla y no dependen del loader. Todo lo demas hay que recrearlo "
                     "o buscar el equivalente para el loader de destino.")
        if ld_y in ("fabric", "quilt") and ld_x in ("forge", "neoforge"):
            notas.append("Busca si el mod tiene version Fabric, o un equivalente "
                         "(muchos mods populares existen en los dos).")
    return nivel, notas


def faltantes(indice_x: dict, indice_y: dict, semillas: list[str]) -> dict:
    """Cierre transitivo: que hay que llevarse ademas del mod pedido."""
    por_id = {m["id"].lower(): m for m in indice_x.get("mods", []) if m.get("id")}
    anidados_x: dict[str, dict] = {}
    for a in indice_x.get("anidados", []):
        if a.get("id"):
            anidados_x.setdefault(a["id"].lower(), a)

    ya_tiene = ids_disponibles(indice_y)
    pendientes = [s.lower() for s in semillas]
    visitados: set[str] = set()
    hay_que_copiar: list[dict] = []
    ya_estaban: list[str] = []
    no_encontrados: list[dict] = []

    while pendientes:
        actual = pendientes.pop(0)
        if actual in visitados or actual in IMPLICITAS:
            continue
        visitados.add(actual)

        if actual in ya_tiene and actual not in [s.lower() for s in semillas]:
            ya_estaban.append(actual)
            continue

        mod = por_id.get(actual)
        if mod is None:
            if actual in anidados_x:
                # Viaja dentro de otro jar: no hay que copiarla aparte.
                ya_estaban.append(f"{actual} (anidada en {anidados_x[actual].get('anidado_en','?')})")
                continue
            no_encontrados.append({
                "id": actual,
                "nota": "no esta en el pack de origen: hay que descargarla a mano",
            })
            continue

        hay_que_copiar.append({
            "id": mod["id"],
            "nombre": mod.get("nombre", mod["id"]),
            "version": mod.get("version", "?"),
            "fichero": mod.get("fichero"),
            "entorno": mod.get("entorno", "*"),
        })
        for dep in (mod.get("depende") or {}):
            d = dep.lower()
            if d not in visitados and d not in IMPLICITAS:
                pendientes.append(d)

    return {
        "semillas": semillas,
        "copiar": hay_que_copiar,
        "ya_estaban": sorted(set(ya_estaban)),
        "sin_localizar": no_encontrados,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Compara dos indices de modpack.")
    ap.add_argument("indice_x", help="indice del pack ORIGEN (del que copias)")
    ap.add_argument("indice_y", help="indice del pack DESTINO (al que pegas)")
    ap.add_argument("--si-porto", default=None,
                    help="id(s) de mod separados por coma: calcula que arrastra")
    ap.add_argument("--salida", default=None, help="JSON con el resultado")
    args = ap.parse_args()

    x, y = cargar(args.indice_x), cargar(args.indice_y)
    nombre_x = x.get("nombre_pack") or Path(x["ruta"]).name
    nombre_y = y.get("nombre_pack") or Path(y["ruta"]).name

    nivel, notas = veredicto(x, y)
    print("=" * 68)
    print(f"ORIGEN  {nombre_x}: MC {x.get('minecraft')} / {x.get('loader')} "
          f"/ {len(x.get('mods', []))} mods")
    print(f"DESTINO {nombre_y}: MC {y.get('minecraft')} / {y.get('loader')} "
          f"/ {len(y.get('mods', []))} mods")
    print("=" * 68)
    print(f"COMPATIBILIDAD: {nivel}")
    for n in notas:
        print(f"  - {n}")
    print()

    ids_x = {m["id"].lower(): m for m in x.get("mods", []) if m.get("id")}
    ids_y = ids_disponibles(y)
    solo_x = [m for i, m in ids_x.items() if i not in ids_y]
    distintos = [
        (m["id"], m.get("version"), next((n.get("version") for n in y.get("mods", [])
                                          if n.get("id", "").lower() == i), "?"))
        for i, m in ids_x.items() if i in ids_y
    ]
    distintos = [d for d in distintos if d[1] != d[2] and d[2] != "?"]

    print(f"MODS SOLO EN ORIGEN: {len(solo_x)}  (candidatos a portar)")
    for m in sorted(solo_x, key=lambda m: m["id"])[:40]:
        marca = {"client": " [solo cliente]", "server": " [solo SERVIDOR]"}.get(
            m.get("entorno", "*"), "")
        print(f"  {m['id']:<28} {str(m.get('version'))[:18]:<18}{marca}")
    if len(solo_x) > 40:
        print(f"  ... y {len(solo_x) - 40} mas (estan en el JSON de salida)")
    print()

    if distintos:
        print(f"MISMO MOD, VERSION DISTINTA: {len(distintos)}")
        for i, vx, vy in distintos[:20]:
            print(f"  {i:<28} origen {str(vx)[:16]:<16} destino {str(vy)[:16]}")
        print("  Cuidado: pisar la version del destino puede romper otros mods.")
        print()

    resultado = {
        "compatibilidad": nivel, "notas": notas,
        "solo_en_origen": [m["id"] for m in solo_x],
        "version_distinta": [{"id": i, "origen": vx, "destino": vy} for i, vx, vy in distintos],
    }

    if args.si_porto:
        semillas = [s.strip() for s in args.si_porto.split(",") if s.strip()]
        plan = faltantes(x, y, semillas)
        resultado["plan_dependencias"] = plan
        print("-" * 68)
        print(f"SI PORTAS: {', '.join(semillas)}")
        print(f"\nHay que copiar {len(plan['copiar'])} jar(s):")
        for m in plan["copiar"]:
            marca = {"client": "  [solo cliente]", "server": "  [solo SERVIDOR]"}.get(
                m.get("entorno", "*"), "")
            print(f"  + {m['fichero']}{marca}")
        if plan["ya_estaban"]:
            print(f"\nYa cubiertas por el destino: {len(plan['ya_estaban'])}")
            print("  " + ", ".join(plan["ya_estaban"][:12])
                  + (" ..." if len(plan["ya_estaban"]) > 12 else ""))
        if plan["sin_localizar"]:
            print(f"\nFALTAN y no estan en el origen: {len(plan['sin_localizar'])}")
            for d in plan["sin_localizar"]:
                print(f"  ! {d['id']} — {d['nota']}")
            print("  Descargalas de Modrinth/CurseForge para la version correcta.")
        if any(m.get("entorno") == "server" for m in plan["copiar"]):
            print("\n  AVISO: hay mods de SERVIDOR en la lista. Colarlos en el cliente")
            print("  es una fuente clasica de crashes. Separa cliente y servidor.")
        print()

    if args.salida:
        Path(args.salida).write_text(
            json.dumps(resultado, indent=2, ensure_ascii=False), "utf-8")
        print(f"Resultado -> {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
