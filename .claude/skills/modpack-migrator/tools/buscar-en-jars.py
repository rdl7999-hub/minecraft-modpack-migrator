#!/usr/bin/env python3
"""
buscar-en-jars.py — busca una feature dentro de un modpack, jars incluidos.

grep no entra en un zip, y en un modpack casi todo lo interesante (recetas,
loot tables, biomas, avances, texturas, traducciones) vive DENTRO de los jars.
Esta herramienta abre cada jar/zip del pack y busca en dos planos:

  - nombres de entrada  -> localiza el fichero que gobierna la feature
  - contenido de texto  -> localiza el ID, el valor o la cadena concreta

Uso:
    python buscar-en-jars.py <ruta-pack> <patron> [opciones]

    --en nombres|contenido|ambos   donde buscar (por defecto: ambos)
    --ext json,toml,js,zs,lang     extensiones a abrir en modo contenido
    --max 60                       tope de resultados que se imprimen
    --solo-mods                    ignora config/ y datapacks sueltos
    --salida hallazgos.json        vuelca todos los resultados a un JSON

Ejemplos:
    # que mod define el bioma "crystal_caves"
    python buscar-en-jars.py ./pack crystal_caves

    # de donde sale el texto que aparece en pantalla
    python buscar-en-jars.py ./pack "Soul Harvest" --en contenido --ext json

    # todas las recetas de un mod
    python buscar-en-jars.py ./pack "data/.*/recipes?/" --en nombres

Solo biblioteca estandar. Python 3.9+.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

# Ficheros de texto que merece la pena abrir. Abrir .class es tirar el tiempo:
# el codigo Java compilado no se lee, y si la feature vive ahi la respuesta es
# "hay que portar el mod entero", que es justo lo que el informe debe decir.
EXT_TEXTO = {"json", "json5", "toml", "properties", "js", "ts", "zs", "mcfunction",
             "txt", "cfg", "yaml", "yml", "snbt", "nbt", "mcmeta", "lang", "md"}

CONTENEDORES = (".jar", ".zip")

# La consola de Windows es cp1252 y los mods traen Unicode a manta (simbolos de
# rareza, acentos, CJK). Sin esto el script muere con UnicodeEncodeError al
# imprimir un extracto perfectamente valido.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(encoding="utf-8", errors="replace")

# Que superficie del pack toca cada ruta: el informe se lee mucho mejor asi.
def clasificar(ruta_interna: str) -> str:
    r = ruta_interna.lower()
    if "/recipe" in r:
        return "receta"
    if "loot_table" in r or "/loot/" in r:
        return "loot"
    if "/worldgen/" in r or "/structure" in r:
        return "worldgen"
    if "/advancement" in r:
        return "avance"
    if "/tags/" in r:
        return "tag"
    if "/lang/" in r:
        return "traduccion"
    if "/font/" in r:
        # Trampa clasica: un default.json de un mod SUSTITUYE la fuente entera.
        # Si no incluye los proveedores vanilla, todo el texto sale en cuadrados.
        return "fuente-CUIDADO"
    if "/textures/" in r or "/models/" in r:
        return "arte"
    if r.endswith(".class"):
        return "codigo-java"
    if "/config" in r or r.endswith((".toml", ".properties", ".json5")):
        return "config"
    if "/function" in r or r.endswith(".mcfunction"):
        return "funcion"
    return "otro"


def entradas_de_texto(nombre: str, exts: set[str]) -> bool:
    ext = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    return ext in exts


def recorte(texto: str, pos: int, ancho: int = 90) -> str:
    ini = max(0, pos - ancho // 3)
    frag = texto[ini:ini + ancho].replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", frag).strip()


def buscar_en_zip(ruta_zip: Path, patron: re.Pattern, modo: str, exts: set[str],
                  etiqueta: str | None = None, profundidad: int = 0) -> list[dict]:
    """Busca en un zip y, un nivel hacia dentro, en sus jars anidados."""
    hallazgos: list[dict] = []
    etiqueta = etiqueta or ruta_zip.name
    try:
        zf = zipfile.ZipFile(ruta_zip) if isinstance(ruta_zip, Path) else ruta_zip
    except (zipfile.BadZipFile, OSError):
        return hallazgos

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            nombre = info.filename

            if modo in ("nombres", "ambos") and patron.search(nombre):
                hallazgos.append({
                    "contenedor": etiqueta,
                    "ruta": nombre,
                    "tipo": clasificar(nombre),
                    "donde": "nombre",
                    "bytes": info.file_size,
                })

            # Un nivel de Jar-in-Jar: ahi viven librerias y a veces la feature.
            if (profundidad == 0 and nombre.lower().endswith(".jar")
                    and ("META-INF/jars/" in nombre or "META-INF/jarjar/" in nombre)):
                try:
                    with zf.open(info) as fh:
                        interno = io.BytesIO(fh.read())
                    with zipfile.ZipFile(interno) as zi:
                        hallazgos.extend(buscar_en_zip(
                            zi, patron, modo, exts,
                            etiqueta=f"{etiqueta}!{Path(nombre).name}", profundidad=1))
                except Exception:
                    pass
                continue

            if modo in ("contenido", "ambos") and entradas_de_texto(nombre, exts):
                if info.file_size > 2_000_000:  # un json de 2 MB no es una feature
                    continue
                try:
                    datos = zf.read(info).decode("utf-8", "replace")
                except Exception:
                    continue
                m = patron.search(datos)
                if m:
                    hallazgos.append({
                        "contenedor": etiqueta,
                        "ruta": nombre,
                        "tipo": clasificar(nombre),
                        "donde": "contenido",
                        "extracto": recorte(datos, m.start()),
                        "coincidencias": len(patron.findall(datos)),
                    })
    return hallazgos


def buscar_sueltos(raiz: Path, patron: re.Pattern, modo: str, exts: set[str]) -> list[dict]:
    """Ficheros que no estan dentro de un jar: config/, kubejs/, datapacks/..."""
    hallazgos: list[dict] = []
    ignorar = {"mods", "libraries", "versions", "saves", "logs", "crash-reports",
               ".git", "backups", "cache"}
    for f in raiz.rglob("*"):
        if not f.is_file():
            continue
        partes = set(p.lower() for p in f.relative_to(raiz).parts[:-1])
        if partes & ignorar:
            continue
        rel = f.relative_to(raiz).as_posix()

        if f.suffix.lower() in CONTENEDORES:
            hallazgos.extend(buscar_en_zip(f, patron, modo, exts, etiqueta=rel))
            continue

        if modo in ("nombres", "ambos") and patron.search(rel):
            hallazgos.append({"contenedor": "(suelto)", "ruta": rel,
                              "tipo": clasificar(rel), "donde": "nombre",
                              "bytes": f.stat().st_size})

        if modo in ("contenido", "ambos") and entradas_de_texto(f.name, exts):
            try:
                if f.stat().st_size > 2_000_000:
                    continue
                datos = f.read_text("utf-8", "replace")
            except OSError:
                continue
            m = patron.search(datos)
            if m:
                hallazgos.append({"contenedor": "(suelto)", "ruta": rel,
                                  "tipo": clasificar(rel), "donde": "contenido",
                                  "extracto": recorte(datos, m.start()),
                                  "coincidencias": len(patron.findall(datos))})
    return hallazgos


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Busca una feature dentro de un modpack, jars incluidos.")
    ap.add_argument("pack", help="carpeta del pack o de la instancia")
    ap.add_argument("patron", help="expresion regular (sin distinguir mayusculas)")
    ap.add_argument("--en", choices=["nombres", "contenido", "ambos"], default="ambos")
    ap.add_argument("--ext", default=",".join(sorted(EXT_TEXTO)),
                    help="extensiones a abrir en modo contenido")
    ap.add_argument("--max", type=int, default=60, help="tope de resultados impresos")
    ap.add_argument("--solo-mods", action="store_true", help="busca solo en mods/")
    ap.add_argument("--salida", default=None, help="JSON con todos los resultados")
    args = ap.parse_args()

    raiz = Path(args.pack).expanduser()
    if not raiz.is_dir():
        print(f"ERROR: {raiz} no es una carpeta. Extrae el pack antes de buscar.",
              file=sys.stderr)
        return 2
    for candidato in (".minecraft", "minecraft", "overrides", "pack"):
        if not (raiz / "mods").is_dir() and (raiz / candidato / "mods").is_dir():
            raiz = raiz / candidato
            break

    try:
        patron = re.compile(args.patron, re.IGNORECASE)
    except re.error as e:
        print(f"ERROR: patron invalido: {e}", file=sys.stderr)
        return 2

    exts = {e.strip().lstrip(".").lower() for e in args.ext.split(",") if e.strip()}
    hallazgos: list[dict] = []

    carpeta_mods = raiz / "mods"
    if carpeta_mods.is_dir():
        for jar in sorted(carpeta_mods.rglob("*.jar")):
            hallazgos.extend(buscar_en_zip(jar, patron, args.en, exts))

    if not args.solo_mods:
        hallazgos.extend(buscar_sueltos(raiz, patron, args.en, exts))

    # Un mod que sale 400 veces no aporta 400 lineas: agrupar por contenedor.
    por_contenedor: dict[str, list[dict]] = {}
    for h in hallazgos:
        por_contenedor.setdefault(h["contenedor"], []).append(h)

    orden = sorted(por_contenedor.items(), key=lambda kv: -len(kv[1]))

    print(f"PATRON: {args.patron}   ({args.en})")
    print(f"{len(hallazgos)} coincidencias en {len(orden)} contenedores\n")

    if not hallazgos:
        print("Sin resultados. Prueba con el ID del mod, el nombre en ingles,")
        print("o busca por nombre de fichero (--en nombres).")
        return 1

    impresos = 0
    for contenedor, lista in orden:
        tipos = {}
        for h in lista:
            tipos[h["tipo"]] = tipos.get(h["tipo"], 0) + 1
        resumen_tipos = ", ".join(f"{t}:{n}" for t, n in sorted(tipos.items(), key=lambda x: -x[1]))
        print(f"== {contenedor}  ({len(lista)}: {resumen_tipos})")
        for h in lista[:6]:
            if impresos >= args.max:
                break
            marca = "N" if h["donde"] == "nombre" else "C"
            print(f"   [{marca}] {h['ruta']}")
            if h.get("extracto"):
                print(f"        {h['extracto'][:110]}")
            impresos += 1
        if len(lista) > 6:
            print(f"   ... {len(lista) - 6} mas en este contenedor")
        print()
        if impresos >= args.max:
            print(f"(cortado en {args.max} resultados; usa --max o --salida)")
            break

    solo_codigo = all(h["tipo"] == "codigo-java" for h in hallazgos)
    if solo_codigo:
        print("AVISO: todas las coincidencias son codigo Java compilado.")
        print("Esta feature NO se puede portar copiando ficheros: hay que llevar")
        print("el mod entero con sus dependencias, o recrearla con un datapack.")

    if args.salida:
        Path(args.salida).write_text(
            json.dumps(hallazgos, indent=2, ensure_ascii=False), "utf-8")
        print(f"\nTodos los resultados -> {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
