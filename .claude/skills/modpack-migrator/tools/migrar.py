#!/usr/bin/env python3
"""
migrar.py — aplica un plan de migracion con copia de seguridad y manifiesto.

Nada se toca sin --aplicar: por defecto SIMULA e imprime lo que haria.
Todo fichero que se pise se guarda antes en la carpeta de la migracion, y el
manifiesto deja constancia de cada cambio para poder revertirlo entero con
revertir.py.

Formato del plan (JSON):

{
  "origen":  "C:/packs/PackX",
  "destino": "C:/packs/PackY",
  "nota":    "porta el sistema de hogueras",
  "operaciones": [
    {"tipo": "copiar",  "origen": "mods/foo.jar", "destino": "mods/foo.jar"},

    {"tipo": "extraer", "jar": "mods/foo.jar",
     "entrada": "data/foo/recipes/hoguera.json",
     "destino": "datapacks/mi-port/data/foo/recipe/hoguera.json"},

    {"tipo": "fusionar-json", "origen": "config/foo.json",
     "destino": "config/foo.json", "claves": ["combate", "dificultad"]},

    {"tipo": "anexar", "origen": "config/foo.toml", "destino": "config/foo.toml"},

    {"tipo": "escribir", "destino": "datapacks/mi-port/pack.mcmeta",
     "contenido": "{...}"}
  ]
}

Las rutas de "origen" y "destino" dentro de cada operacion son relativas a las
raices del plan. Nunca se sale de ellas.

Uso:
    python migrar.py plan.json              # simula
    python migrar.py plan.json --aplicar    # ejecuta y crea el manifiesto

Solo biblioteca estandar. Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(encoding="utf-8", errors="replace")


def sha(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def dentro(raiz: Path, relativa: str) -> Path:
    """Impide que un plan escriba fuera del pack de destino."""
    destino = (raiz / relativa).resolve()
    if raiz.resolve() not in destino.parents and destino != raiz.resolve():
        raise ValueError(f"ruta fuera del pack: {relativa}")
    return destino


def json_laxo(texto: str):
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        limpio = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
        limpio = re.sub(r"(?m)^\s*//.*$", "", limpio)
        limpio = re.sub(r",(\s*[}\]])", r"\1", limpio)
        return json.loads(limpio)


def fusionar(base: dict, nuevo: dict, claves: list[str] | None) -> dict:
    """Mete lo de 'nuevo' en 'base' sin borrar lo que base ya tenia."""
    if claves:
        nuevo = {k: v for k, v in nuevo.items() if k in claves}
    for k, v in nuevo.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            fusionar(base[k], v, None)
        else:
            base[k] = v
    return base


class Migracion:
    def __init__(self, plan: dict, aplicar: bool):
        self.origen = Path(plan["origen"]).expanduser().resolve()
        self.destino = Path(plan["destino"]).expanduser().resolve()
        self.aplicar = aplicar
        self.nota = plan.get("nota", "")
        self.operaciones = plan.get("operaciones", [])
        self.sello = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.carpeta = self.destino / "migraciones" / self.sello
        self.respaldos = self.carpeta / "respaldo"
        self.registro: list[dict] = []
        self.errores: list[str] = []

    # -- utilidades de escritura -------------------------------------------

    def _respaldar(self, destino: Path) -> str | None:
        if not destino.exists():
            return None
        rel = destino.relative_to(self.destino)
        copia = self.respaldos / rel
        if self.aplicar:
            copia.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destino, copia)
        return str(rel).replace("\\", "/")

    def _anotar(self, tipo: str, destino: Path, respaldo: str | None, detalle: str = ""):
        self.registro.append({
            "tipo": tipo,
            "destino": str(destino.relative_to(self.destino)).replace("\\", "/"),
            "existia": respaldo is not None,
            "respaldo": respaldo,
            "sha256": sha(destino) if (self.aplicar and destino.is_file()) else None,
            "detalle": detalle,
        })

    def _escribir(self, destino: Path, datos: bytes, tipo: str, detalle: str = ""):
        respaldo = self._respaldar(destino)
        verbo = "PISA " if respaldo else "crea "
        print(f"  {verbo} {destino.relative_to(self.destino)}"
              f"{'  <- ' + detalle if detalle else ''}")
        if self.aplicar:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(datos)
            self._anotar(tipo, destino, respaldo, detalle)

    # -- operaciones --------------------------------------------------------

    def op_copiar(self, op: dict):
        src = dentro(self.origen, op["origen"])
        if not src.exists():
            self.errores.append(f"copiar: no existe en el origen {op['origen']}")
            return
        dst = dentro(self.destino, op.get("destino", op["origen"]))
        if src.is_dir():
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    self._escribir(dst / f.relative_to(src), f.read_bytes(),
                                   "copiar", op["origen"])
        else:
            self._escribir(dst, src.read_bytes(), "copiar", op["origen"])

    def op_extraer(self, op: dict):
        jar = dentro(self.origen, op["jar"])
        if not jar.is_file():
            self.errores.append(f"extraer: no existe el jar {op['jar']}")
            return
        try:
            with zipfile.ZipFile(jar) as zf:
                nombres = zf.namelist()
                patron = op["entrada"]
                # Si la entrada acaba en / o trae comodin, se saca el grupo entero.
                if patron.endswith("/") or "*" in patron:
                    rx = re.compile(patron.replace("*", ".*"))
                    seleccion = [n for n in nombres if rx.match(n) and not n.endswith("/")]
                else:
                    seleccion = [n for n in nombres if n == patron]
                if not seleccion:
                    self.errores.append(f"extraer: '{patron}' no esta en {jar.name}")
                    return
                base_destino = op["destino"]
                for n in seleccion:
                    if len(seleccion) > 1:
                        sufijo = n[len(patron.rstrip("*")):] if patron.rstrip("*") in n else Path(n).name
                        dst = dentro(self.destino, f"{base_destino.rstrip('/')}/{sufijo.lstrip('/')}")
                    else:
                        dst = dentro(self.destino, base_destino)
                    self._escribir(dst, zf.read(n), "extraer", f"{jar.name}!{n}")
        except zipfile.BadZipFile:
            self.errores.append(f"extraer: {jar.name} no es un zip valido")

    def op_fusionar_json(self, op: dict):
        src = dentro(self.origen, op["origen"])
        dst = dentro(self.destino, op.get("destino", op["origen"]))
        if not src.is_file():
            self.errores.append(f"fusionar-json: no existe {op['origen']}")
            return
        try:
            nuevo = json_laxo(src.read_text("utf-8", "replace"))
        except json.JSONDecodeError as e:
            self.errores.append(f"fusionar-json: el origen no es JSON valido ({e})")
            return
        if dst.is_file():
            try:
                base = json_laxo(dst.read_text("utf-8", "replace"))
            except json.JSONDecodeError:
                self.errores.append(
                    f"fusionar-json: el destino {op.get('destino')} no es JSON valido; "
                    "se deja como esta")
                return
        else:
            base = {}
        if not isinstance(base, dict) or not isinstance(nuevo, dict):
            self.errores.append("fusionar-json: solo se fusionan objetos JSON")
            return
        fundido = fusionar(base, nuevo, op.get("claves"))
        datos = json.dumps(fundido, indent=2, ensure_ascii=False).encode("utf-8")
        claves = ",".join(op["claves"]) if op.get("claves") else "todo"
        self._escribir(dst, datos, "fusionar-json", f"claves: {claves}")

    def op_anexar(self, op: dict):
        src = dentro(self.origen, op["origen"])
        dst = dentro(self.destino, op.get("destino", op["origen"]))
        if not src.is_file():
            self.errores.append(f"anexar: no existe {op['origen']}")
            return
        anadido = src.read_text("utf-8", "replace")
        cabecera = f"\n\n# --- anadido por modpack-migrator {self.sello} ---\n"
        previo = dst.read_text("utf-8", "replace") if dst.is_file() else ""
        if anadido.strip() and anadido.strip() in previo:
            print(f"  salta {dst.relative_to(self.destino)} (ya estaba)")
            return
        self._escribir(dst, (previo + cabecera + anadido).encode("utf-8"),
                       "anexar", op["origen"])

    def op_escribir(self, op: dict):
        dst = dentro(self.destino, op["destino"])
        self._escribir(dst, op["contenido"].encode("utf-8"), "escribir", "contenido del plan")

    # -- ejecucion ----------------------------------------------------------

    def ejecutar(self) -> int:
        if not self.destino.is_dir():
            print(f"ERROR: el destino no existe: {self.destino}", file=sys.stderr)
            return 2
        if not self.origen.exists():
            print(f"ERROR: el origen no existe: {self.origen}", file=sys.stderr)
            return 2

        modo = "APLICANDO" if self.aplicar else "SIMULACION (nada se toca)"
        print("=" * 68)
        print(f"{modo}   {self.nota}")
        print(f"origen  : {self.origen}")
        print(f"destino : {self.destino}")
        print("=" * 68)

        despacho = {
            "copiar": self.op_copiar,
            "extraer": self.op_extraer,
            "fusionar-json": self.op_fusionar_json,
            "anexar": self.op_anexar,
            "escribir": self.op_escribir,
        }

        for i, op in enumerate(self.operaciones, 1):
            tipo = op.get("tipo")
            print(f"\n[{i}/{len(self.operaciones)}] {tipo}")
            fn = despacho.get(tipo)
            if fn is None:
                self.errores.append(f"operacion desconocida: {tipo}")
                continue
            try:
                fn(op)
            except (ValueError, KeyError, OSError) as e:
                self.errores.append(f"{tipo}: {e}")

        print("\n" + "=" * 68)
        if self.errores:
            print(f"ERRORES ({len(self.errores)}):")
            for e in self.errores:
                print(f"  ! {e}")

        if not self.aplicar:
            print("\nSimulacion terminada. Nada se ha modificado.")
            print("Repite con --aplicar cuando el plan te convenza.")
            return 1 if self.errores else 0

        manifiesto = {
            "sello": self.sello,
            "nota": self.nota,
            "origen": str(self.origen),
            "destino": str(self.destino),
            "cambios": self.registro,
            "errores": self.errores,
        }
        self.carpeta.mkdir(parents=True, exist_ok=True)
        ruta_manifiesto = self.carpeta / "manifiesto.json"
        ruta_manifiesto.write_text(
            json.dumps(manifiesto, indent=2, ensure_ascii=False), "utf-8")

        pisados = sum(1 for c in self.registro if c["existia"])
        print(f"\n{len(self.registro)} ficheros escritos ({pisados} pisados, "
              f"con respaldo).")
        print(f"Manifiesto -> {ruta_manifiesto}")
        print(f"Para deshacerlo entero:\n  python revertir.py \"{ruta_manifiesto}\"")
        return 1 if self.errores else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Aplica un plan de migracion de modpack.")
    ap.add_argument("plan", help="JSON con el plan")
    ap.add_argument("--aplicar", action="store_true",
                    help="ejecuta de verdad (sin esto solo simula)")
    args = ap.parse_args()

    ruta = Path(args.plan)
    if not ruta.is_file():
        print(f"ERROR: no existe el plan {ruta}", file=sys.stderr)
        return 2
    plan = json.loads(ruta.read_text("utf-8"))
    return Migracion(plan, args.aplicar).ejecutar()


if __name__ == "__main__":
    raise SystemExit(main())
