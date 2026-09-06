#!/usr/bin/env python3
"""
escanear-pack.py — construye el indice de un modpack.

Acepta una carpeta (instancia o pack), un .mrpack o un .zip exportado de
CurseForge. Detecta loader y version de Minecraft, lee la identidad de cada
jar (incluidos los jars anidados por Jar-in-Jar) y mapea las superficies de
contenido del pack.

Escribe un JSON completo en disco y un resumen corto por pantalla: el JSON es
para consultar con grep, el resumen es lo unico que hace falta leer entero.

Uso:
    python escanear-pack.py <ruta> [--salida indice.json] [--detalle]

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

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------

# Versiones de Minecraft plausibles (1.16 a 1.29) que NO vengan precedidas de
# otro digito o punto. Sin esto, "cloth-config-11.1.136" se lee como "1.13" y
# el escaner avisa de 12 mods que estaban perfectos.
RE_VERSION_MC = re.compile(r"(?<![\d.])(1\.(?:1[6-9]|2\d)(?:\.\d{1,2})?)(?![\d])")

# La linea que el propio juego escribe al arrancar. Es la fuente mas fiable de
# la version del loader: no es lo que el pack dice que usa, es lo que uso.
RE_LOG_LOADER = re.compile(
    r"Loading Minecraft ([\d.]+) with (Fabric|Quilt) Loader ([\d.+\w-]+)", re.I
)


# La consola de Windows es cp1252 y los nombres de mod traen Unicode.
for _flujo in (sys.stdout, sys.stderr):
    if hasattr(_flujo, "reconfigure"):
        _flujo.reconfigure(encoding="utf-8", errors="replace")

def json_laxo(texto: str):
    """Muchos fabric.mod.json traen comentarios o comas colgando."""
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    limpio = re.sub(r"/\*.*?\*/", "", texto, flags=re.S)
    limpio = re.sub(r"(?m)^\s*//.*$", "", limpio)
    limpio = re.sub(r",(\s*[}\]])", r"\1", limpio)
    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        return None


def leer_toml(datos: bytes):
    if tomllib is not None:
        try:
            return tomllib.loads(datos.decode("utf-8", "replace"))
        except Exception:
            pass
    # Rescate por regex: solo lo imprescindible.
    texto = datos.decode("utf-8", "replace")
    mods = []
    for bloque in re.split(r"\[\[mods\]\]", texto)[1:]:
        mod_id = re.search(r'modId\s*=\s*"([^"]+)"', bloque)
        version = re.search(r'version\s*=\s*"([^"]+)"', bloque)
        nombre = re.search(r'displayName\s*=\s*"([^"]+)"', bloque)
        if mod_id:
            mods.append({
                "modId": mod_id.group(1),
                "version": version.group(1) if version else "?",
                "displayName": nombre.group(1) if nombre else mod_id.group(1),
            })
    return {"mods": mods} if mods else None


def texto_de(valor) -> str:
    if isinstance(valor, str):
        return valor
    if isinstance(valor, dict):
        for clave in ("name", "id", "value"):
            if clave in valor:
                return str(valor[clave])
    if isinstance(valor, list) and valor:
        return texto_de(valor[0])
    return str(valor) if valor is not None else ""


# --------------------------------------------------------------------------
# identidad de un jar
# --------------------------------------------------------------------------

def identificar_jar(zf: zipfile.ZipFile, nombre_fichero: str) -> dict | None:
    """Devuelve la identidad de un mod a partir de su jar abierto."""
    nombres = set(zf.namelist())

    # --- Fabric / Quilt ---
    for meta in ("fabric.mod.json", "quilt.mod.json"):
        if meta not in nombres:
            continue
        datos = json_laxo(zf.read(meta).decode("utf-8", "replace"))
        if not datos:
            continue
        if meta == "quilt.mod.json":
            ql = datos.get("quilt_loader", {})
            meta_q = ql.get("metadata", {})
            return {
                "id": ql.get("id", "?"),
                "nombre": meta_q.get("name", ql.get("id", "?")),
                "version": ql.get("version", "?"),
                "loader": "quilt",
                "entorno": "*",
                "depende": {k: texto_de(v) for k, v in (ql.get("depends") or {}).items()}
                if isinstance(ql.get("depends"), dict) else {},
                "anidados": [j.get("file") for j in (ql.get("jars") or []) if isinstance(j, dict)],
                "fichero": nombre_fichero,
            }
        depende = datos.get("depends") or {}
        return {
            "id": datos.get("id", "?"),
            "nombre": datos.get("name", datos.get("id", "?")),
            "version": datos.get("version", "?"),
            "loader": "fabric",
            # client / server / * — clave para no colar un mod de servidor en el cliente
            "entorno": datos.get("environment", "*"),
            "depende": {k: texto_de(v) for k, v in depende.items()} if isinstance(depende, dict) else {},
            "anidados": [j.get("file") for j in (datos.get("jars") or []) if isinstance(j, dict)],
            "fichero": nombre_fichero,
        }

    # --- Forge / NeoForge ---
    for meta, loader in (("META-INF/mods.toml", "forge"),
                         ("META-INF/neoforge.mods.toml", "neoforge")):
        if meta not in nombres:
            continue
        datos = leer_toml(zf.read(meta))
        if not datos or not datos.get("mods"):
            continue
        primero = datos["mods"][0]
        dependencias = {}
        for _, lista in (datos.get("dependencies") or {}).items():
            if isinstance(lista, list):
                for dep in lista:
                    if isinstance(dep, dict) and dep.get("modId"):
                        dependencias[dep["modId"]] = str(dep.get("versionRange", "*"))
        return {
            "id": primero.get("modId", "?"),
            "nombre": primero.get("displayName", primero.get("modId", "?")),
            "version": str(primero.get("version", "?")),
            "loader": loader,
            "entorno": "*",
            "depende": dependencias,
            "anidados": [],
            "fichero": nombre_fichero,
        }

    # --- jar sin metadatos: libreria plana o datapack empaquetado ---
    return {
        "id": Path(nombre_fichero).stem.lower(),
        "nombre": Path(nombre_fichero).stem,
        "version": "?",
        "loader": "desconocido",
        "entorno": "*",
        "depende": {},
        "anidados": [],
        "fichero": nombre_fichero,
        "aviso": "sin metadatos de mod",
    }


def jars_anidados(zf: zipfile.ZipFile, padre: str) -> list[dict]:
    """Los Jar-in-Jar traen dependencias que no aparecen en /mods."""
    salida = []
    for nombre in zf.namelist():
        if not nombre.lower().endswith(".jar"):
            continue
        if not (nombre.startswith("META-INF/jars/") or nombre.startswith("META-INF/jarjar/")):
            continue
        try:
            with zf.open(nombre) as fh:
                interno = io.BytesIO(fh.read())
            with zipfile.ZipFile(interno) as zi:
                ident = identificar_jar(zi, nombre)
        except Exception:
            continue
        if ident:
            ident["anidado_en"] = padre
            salida.append(ident)
    return salida


# --------------------------------------------------------------------------
# lectura del pack
# --------------------------------------------------------------------------

SUPERFICIES = {
    "mods": "jars de mods",
    "config": "configuracion de mods",
    "defaultconfigs": "configs por defecto de mundo nuevo",
    "kubejs": "scripts KubeJS",
    "scripts": "scripts CraftTweaker",
    "openloader": "datapacks/resourcepacks cargados por OpenLoader",
    "datapacks": "datapacks sueltos",
    "resourcepacks": "packs de recursos",
    "shaderpacks": "shaders",
    "global_packs": "packs globales",
    "packmenu": "menu personalizado",
    "patchouli_books": "libros/guias",
}


def desempaquetar_indice(ruta: Path) -> dict | None:
    """Lee el indice de un .mrpack o de un export de CurseForge sin extraerlo."""
    if not zipfile.is_zipfile(ruta):
        return None
    with zipfile.ZipFile(ruta) as zf:
        nombres = set(zf.namelist())
        if "modrinth.index.json" in nombres:
            datos = json_laxo(zf.read("modrinth.index.json").decode("utf-8", "replace")) or {}
            deps = datos.get("dependencies", {})
            loader = next((k.replace("-loader", "") for k in deps
                           if k.endswith("loader") or k in ("forge", "neoforge")), "?")
            return {
                "formato": "mrpack",
                "nombre_pack": datos.get("name", ruta.stem),
                "version_pack": datos.get("versionId", "?"),
                "minecraft": deps.get("minecraft", "?"),
                "loader": loader,
                "version_loader": deps.get(f"{loader}-loader", deps.get(loader, "?")),
                "mods_declarados": [
                    {"ruta": f.get("path", "?"),
                     "url": (f.get("downloads") or ["?"])[0],
                     "entorno": f.get("env", {})}
                    for f in (datos.get("files") or [])
                ],
                "tiene_overrides": any(n.startswith("overrides/") for n in nombres),
            }
        if "manifest.json" in nombres:
            datos = json_laxo(zf.read("manifest.json").decode("utf-8", "replace")) or {}
            mc = datos.get("minecraft", {})
            loaders = mc.get("modLoaders") or [{}]
            id_loader = loaders[0].get("id", "?")
            return {
                "formato": "curseforge",
                "nombre_pack": datos.get("name", ruta.stem),
                "version_pack": datos.get("version", "?"),
                "minecraft": mc.get("version", "?"),
                "loader": id_loader.split("-")[0],
                "version_loader": id_loader,
                "mods_declarados": [
                    {"projectID": f.get("projectID"), "fileID": f.get("fileID")}
                    for f in (datos.get("files") or [])
                ],
                "tiene_overrides": any(n.startswith("overrides/") for n in nombres),
            }
    return None


def localizar_raiz(ruta: Path) -> Path:
    """Una instancia puede tener el pack colgando de .minecraft/ o de overrides/."""
    if (ruta / "mods").is_dir():
        return ruta
    for candidato in (".minecraft", "minecraft", "overrides", "pack"):
        if (ruta / candidato / "mods").is_dir():
            return ruta / candidato
    return ruta


def loader_del_log(raiz: Path) -> dict | None:
    """
    Saca version de MC y del loader del log del juego.

    Una carpeta de instancia normal (Modrinth App, launcher oficial) no trae
    ningun fichero de metadatos, asi que sin esto 'version_loader' se quedaba
    siempre en '?'. Y ese dato importa: un mod compilado contra un loader mas
    nuevo del que corre el pack NO CARGA, y no da ningun error.
    """
    for patron in ("logs/latest.log", "logs/*.log", "logs/*.log.gz"):
        candidatos = sorted(raiz.glob(patron), key=lambda p: -p.stat().st_mtime)
        for log in candidatos[:6]:
            try:
                if log.suffix == ".gz":
                    import gzip
                    crudo = gzip.open(log, "rt", encoding="utf-8",
                                      errors="replace").read(200_000)
                else:
                    crudo = log.read_text("utf-8", "replace")[:200_000]
            except OSError:
                continue
            m = RE_LOG_LOADER.search(crudo)
            if m:
                return {
                    "minecraft": m.group(1),
                    "loader": m.group(2).lower(),
                    "version_loader": m.group(3),
                    "fuente_loader": f"{log.name} (el juego lo dijo al arrancar)",
                }
    return None


def escanear(ruta: Path, detalle: bool = False) -> dict:
    indice = {
        "ruta": str(ruta),
        "formato": "carpeta",
        "minecraft": "?",
        "loader": "?",
        "version_loader": "?",
        "fuente_loader": "?",
        "superficies": {},
        "mods": [],
        "anidados": [],
        "avisos": [],
        "sospechosos_version": [],
    }

    if ruta.is_file():
        empaquetado = desempaquetar_indice(ruta)
        if empaquetado:
            indice.update(empaquetado)
            indice["ruta"] = str(ruta)
            indice["avisos"].append(
                "Pack empaquetado: los jars NO estan aqui, solo declarados. "
                "Para inspeccionar contenido hay que extraerlo e instalarlo primero."
            )
            return indice
        indice["avisos"].append("El fichero no es un .mrpack ni un export de CurseForge.")
        return indice

    raiz = localizar_raiz(ruta)
    indice["ruta"] = str(raiz)

    for carpeta, descripcion in SUPERFICIES.items():
        d = raiz / carpeta
        if d.is_dir():
            n = sum(1 for _ in d.rglob("*") if _.is_file())
            indice["superficies"][carpeta] = {"ficheros": n, "que_es": descripcion}

    # Metadatos de instancia, si los hay.
    for meta in ("mmc-pack.json", "instance.json", "profile.json", "modrinth.index.json"):
        f = ruta / meta
        if not f.is_file():
            continue
        datos = json_laxo(f.read_text("utf-8", "replace"))
        if not isinstance(datos, dict):
            continue
        for comp in datos.get("components", []) or []:
            uid = comp.get("uid", "")
            if uid == "net.minecraft":
                indice["minecraft"] = comp.get("version", indice["minecraft"])
            elif "fabric" in uid or "forge" in uid or "quilt" in uid:
                indice["loader"] = uid.split(".")[-1]
                indice["version_loader"] = comp.get("version", "?")

    # Una carpeta de instancia no trae metadatos: el dato bueno esta en el log
    # del propio juego. Solo se usa si no lo hemos sacado ya de otro sitio.
    if indice["version_loader"] == "?":
        del_log = loader_del_log(raiz)
        if del_log:
            indice["loader"] = del_log["loader"]
            indice["version_loader"] = del_log["version_loader"]
            indice["fuente_loader"] = del_log["fuente_loader"]
            if indice["minecraft"] == "?":
                indice["minecraft"] = del_log["minecraft"]
    elif indice["fuente_loader"] == "?":
        indice["fuente_loader"] = "metadatos de la instancia"

    carpeta_mods = raiz / "mods"
    if not carpeta_mods.is_dir():
        indice["avisos"].append("No hay carpeta mods/: esto no parece un pack instalado.")
        return indice

    versiones_mc: dict[str, int] = {}
    loaders: dict[str, int] = {}

    for jar in sorted(carpeta_mods.rglob("*.jar")):
        try:
            with zipfile.ZipFile(jar) as zf:
                ident = identificar_jar(zf, jar.name)
                if detalle:
                    indice["anidados"].extend(jars_anidados(zf, jar.name))
                else:
                    indice["anidados"].extend(
                        {"id": a["id"], "version": a["version"], "anidado_en": jar.name}
                        for a in jars_anidados(zf, jar.name)
                    )
        except zipfile.BadZipFile:
            indice["avisos"].append(f"Jar corrupto o incompleto: {jar.name}")
            continue
        if not ident:
            continue
        loaders[ident["loader"]] = loaders.get(ident["loader"], 0) + 1
        mc = ident["depende"].get("minecraft") if ident["depende"] else None
        if mc:
            versiones_mc[str(mc)] = versiones_mc.get(str(mc), 0) + 1
        # Pista de version en el nombre del fichero: la etiqueta de la web miente,
        # el fichero suele decir la verdad. Solo cuentan versiones de MC
        # plausibles y no precedidas de otro numero (para no leer "11.1.136"
        # de cloth-config como si fuera "1.13").
        ident["mc_en_nombre"] = sorted(set(RE_VERSION_MC.findall(jar.name)))
        indice["mods"].append(ident)

    if indice["loader"] == "?" and loaders:
        indice["loader"] = max(
            (l for l in loaders if l != "desconocido"), key=lambda k: loaders[k], default="?")
    if indice["minecraft"] == "?" and versiones_mc:
        cruda = max(versiones_mc, key=lambda k: versiones_mc[k])
        limpia = re.search(r"1\.\d{2}(\.\d+)?", cruda)
        indice["minecraft"] = limpia.group(0) if limpia else cruda

    # Mods compilados para otra version de MC: el clasico "la etiqueta de la web
    # dice 1.20.1 y el jar es +1.20.4". Se mira el nombre del fichero, pero se
    # calla si el propio mod declara compatibilidad con la version del pack.
    base = indice["minecraft"]
    familia = ".".join(base.split(".")[:2]) if base != "?" else "?"
    if base != "?":
        for mod in indice["mods"]:
            marcas = mod.get("mc_en_nombre") or []
            if not marcas:
                continue
            if any(m == base or m == familia or m.startswith(familia + ".") for m in marcas):
                continue
            declarado = str((mod.get("depende") or {}).get("minecraft", ""))
            if familia in declarado or declarado.startswith(">="):
                # El loader lo acepta, asi que no es un aviso. Pero un jar
                # compilado contra otra version puede reventar en runtime con
                # NoSuchMethodError: queda anotado para cuando toque depurar.
                indice["sospechosos_version"].append({
                    "fichero": mod["fichero"],
                    "compilado_para": marcas,
                    "declara": declarado,
                })
                continue
            indice["avisos"].append(
                f"{mod['fichero']}: compilado para {'/'.join(marcas)}, el pack es {base}"
                " -> comprobar antes de portarlo")

    solo_cliente = [m["id"] for m in indice["mods"] if m.get("entorno") == "client"]
    solo_servidor = [m["id"] for m in indice["mods"] if m.get("entorno") == "server"]
    indice["solo_cliente"] = solo_cliente
    indice["solo_servidor"] = solo_servidor
    return indice


# --------------------------------------------------------------------------
# salida
# --------------------------------------------------------------------------

def resumir(indice: dict) -> str:
    lineas = []
    lineas.append(f"PACK: {indice.get('nombre_pack', Path(indice['ruta']).name)}")
    lineas.append(f"  ruta      : {indice['ruta']}")
    lineas.append(f"  formato   : {indice['formato']}")
    lineas.append(f"  minecraft : {indice['minecraft']}")
    lineas.append(f"  loader    : {indice['loader']} {indice.get('version_loader', '')}".rstrip())

    if indice["formato"] != "carpeta":
        lineas.append(f"  mods declarados: {len(indice.get('mods_declarados', []))}")
    else:
        lineas.append(f"  mods      : {len(indice['mods'])}"
                      f"  (+{len(indice['anidados'])} anidados)")
        if indice.get("solo_cliente"):
            lineas.append(f"  solo cliente : {len(indice['solo_cliente'])}")
        if indice.get("solo_servidor"):
            lineas.append(f"  solo servidor: {len(indice['solo_servidor'])} "
                          f"-> {', '.join(indice['solo_servidor'][:8])}")

    if indice["superficies"]:
        lineas.append("  superficies:")
        for nombre, info in indice["superficies"].items():
            lineas.append(f"    {nombre:<16} {info['ficheros']:>5} ficheros  ({info['que_es']})")

    sospechosos = indice.get("sospechosos_version", [])
    if sospechosos:
        lineas.append(f"  sospechosos de version: {len(sospechosos)} "
                      "(el loader los acepta; mirar si hay crash raro)")

    avisos = indice.get("avisos", [])
    if avisos:
        lineas.append(f"  AVISOS ({len(avisos)}):")
        for aviso in avisos[:12]:
            lineas.append(f"    ! {aviso}")
        if len(avisos) > 12:
            lineas.append(f"    ... y {len(avisos) - 12} mas (estan en el JSON)")
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser(description="Indexa un modpack de Minecraft.")
    ap.add_argument("ruta", help="carpeta del pack/instancia, .mrpack o .zip")
    ap.add_argument("--salida", default=None, help="ruta del JSON (por defecto: indice-<nombre>.json)")
    ap.add_argument("--detalle", action="store_true", help="incluye metadatos completos de los jars anidados")
    args = ap.parse_args()

    ruta = Path(args.ruta).expanduser()
    if not ruta.exists():
        print(f"ERROR: no existe {ruta}", file=sys.stderr)
        return 2

    indice = escanear(ruta, detalle=args.detalle)
    salida = Path(args.salida) if args.salida else Path(f"indice-{ruta.stem or ruta.name}.json")
    salida.write_text(json.dumps(indice, indent=2, ensure_ascii=False), "utf-8")

    print(resumir(indice))
    print(f"\nIndice completo -> {salida}")
    print("Consultalo con grep, no lo leas entero.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
