#!/usr/bin/env python3
"""
revertir.py — deshace una migracion entera a partir de su manifiesto.

Restaura los ficheros que se pisaron y borra los que se crearon. Antes de
borrar comprueba el sha256: si el fichero ha cambiado desde la migracion
(porque lo has editado tu despues), lo respeta y avisa, en vez de tirarlo.

Uso:
    python revertir.py <migraciones/AAAAMMDD-HHMMSS/manifiesto.json>
    python revertir.py <manifiesto.json> --aplicar   # ejecuta de verdad
    python revertir.py <manifiesto.json> --aplicar --forzar   # borra igualmente

Solo biblioteca estandar. Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Deshace una migracion de modpack.")
    ap.add_argument("manifiesto")
    ap.add_argument("--aplicar", action="store_true", help="ejecuta (sin esto simula)")
    ap.add_argument("--forzar", action="store_true",
                    help="borra tambien los ficheros modificados despues de migrar")
    args = ap.parse_args()

    ruta = Path(args.manifiesto)
    if not ruta.is_file():
        print(f"ERROR: no existe {ruta}", file=sys.stderr)
        return 2

    manifiesto = json.loads(ruta.read_text("utf-8"))
    destino = Path(manifiesto["destino"])
    respaldos = ruta.parent / "respaldo"
    cambios = manifiesto.get("cambios", [])

    modo = "REVIRTIENDO" if args.aplicar else "SIMULACION (nada se toca)"
    print("=" * 68)
    print(f"{modo}   migracion {manifiesto.get('sello')}  —  {manifiesto.get('nota','')}")
    print(f"destino: {destino}")
    print(f"{len(cambios)} cambios registrados")
    print("=" * 68)

    restaurados = borrados = respetados = ausentes = 0

    # Al reves: si una operacion escribio dos veces el mismo fichero, el
    # respaldo bueno es el de la primera.
    for cambio in reversed(cambios):
        objetivo = destino / cambio["destino"]
        etiqueta = cambio["destino"]

        if not objetivo.exists():
            print(f"  ausente  {etiqueta} (ya no estaba)")
            ausentes += 1
            continue

        # Si lo has tocado despues de migrar, no se toca sin --forzar.
        esperado = cambio.get("sha256")
        if esperado and sha(objetivo) != esperado and not args.forzar:
            print(f"  RESPETA  {etiqueta} (modificado despues de la migracion)")
            respetados += 1
            continue

        if cambio["existia"] and cambio.get("respaldo"):
            copia = respaldos / cambio["respaldo"]
            if not copia.is_file():
                print(f"  ! sin respaldo para {etiqueta}: se deja como esta")
                respetados += 1
                continue
            print(f"  restaura {etiqueta}")
            if args.aplicar:
                objetivo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copia, objetivo)
            restaurados += 1
        else:
            print(f"  borra    {etiqueta}")
            if args.aplicar:
                objetivo.unlink()
            borrados += 1

    # Carpetas que se quedan vacias tras borrar lo creado.
    if args.aplicar:
        for cambio in cambios:
            padre = (destino / cambio["destino"]).parent
            while padre != destino and padre.is_dir():
                try:
                    next(padre.iterdir())
                    break
                except StopIteration:
                    padre.rmdir()
                    padre = padre.parent
                except OSError:
                    break

    print("\n" + "=" * 68)
    print(f"restaurados {restaurados} · borrados {borrados} · "
          f"respetados {respetados} · ausentes {ausentes}")
    if not args.aplicar:
        print("\nSimulacion. Repite con --aplicar para revertir de verdad.")
    elif respetados:
        print("\nHay ficheros que cambiaron despues de migrar y se han respetado.")
        print("Si quieres tirarlos igualmente: --aplicar --forzar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
