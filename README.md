# Modpack Migrator

Skill de [Claude Code](https://claude.com/claude-code) para hacer ingeniería inversa de modpacks de Minecraft y portar features de uno a otro sin romperlos.

Le dices *"me gusta cómo funciona el sistema de hogueras de este pack, llévalo al mío"* y se encarga: localiza qué mod lo gobierna, calcula qué dependencias arrastra, simula los cambios, los aplica con respaldo y te deja un comando para deshacerlo.

Funciona con **Fabric, Quilt, Forge y NeoForge**, en carpeta de instancia, `.mrpack` o export de CurseForge.

---

## Por qué

Portar una feature entre modpacks a mano tiene tres problemas que no se ven hasta que es tarde:

1. **`grep` no entra en un zip.** Recetas, biomas, loot tables y texturas viven dentro de los `.jar`. Buscar en `config/` no encuentra casi nada.
2. **Nada es autónomo.** Una receta apunta a items de otro mod; un bioma coloca bloques de un tercero. Copiar el fichero suelto da un `NullPointerException` en el arranque, no un error claro.
3. **Las dependencias están escondidas.** Los mods de Fabric empaquetan librerías dentro de sí mismos (Jar-in-Jar). Un pack de 130 jars tiene 500+ dependencias declaradas y 240 anidadas que no aparecen en `mods/`.

Esta skill resuelve los tres con herramientas propias, y añade lo que faltaba: **simulación previa y rollback**.

---

## Instalación

Requiere Claude Code y **Python 3.9+** (solo biblioteca estándar: sin pip, sin dependencias).

```bash
git clone https://github.com/rdl7999-hub/minecraft-modpack-migrator.git
cp -r minecraft-modpack-migrator/.claude/skills/modpack-migrator ~/.claude/skills/
```

En Windows:

```powershell
xcopy /E /I ".claude\skills\modpack-migrator" "%USERPROFILE%\.claude\skills\modpack-migrator"
```

Copia la carpeta **entera**: las herramientas van dentro, junto al `SKILL.md`.

O simplemente abre la carpeta del kit con Claude Code y empieza a hablar.

---

## Uso

Descríbelo en lenguaje normal, como lo ves en el juego:

```
Me gusta el sistema de sed de C:\packs\HardcorePack.
Quiero llevarlo a mi pack en C:\packs\MiPack.
```

```
Analiza C:\packs\CoolPack y dime cómo consigue ese HUD.
```

```
Mi pack peta al arrancar desde que copié unos mods. Arréglalo.
```

### Lo que hace, en orden

1. **Indexa los dos packs** — versión de MC, loader, mods, dependencias anidadas, superficies de contenido
2. **Veredicto de compatibilidad** antes de tocar nada:

   | | |
   |---|---|
   | 🟢 **VERDE** | misma versión y loader: se puede portar todo |
   | 🟡 **AMARILLO** | versión cercana: los jars van, con riesgo de `NoSuchMethodError` |
   | 🔴 **ROJO** | loaders distintos o versiones lejanas: solo datapacks y resourcepacks |

3. **Clasifica la feature** — receta, loot, worldgen, config, arte o código Java. Si es código Java compilado, lo dice: eso no se porta copiando ficheros
4. **Busca dentro de los jars** y localiza el mod responsable
5. **Calcula el coste real** — cierre transitivo de dependencias. Portar una feature suele costar entre 3 y 8 mods
6. **Simula, aplica y registra** — con respaldo de todo lo que pise
7. **Deshace** con un comando si no convence

---

## Las herramientas

Cinco scripts de Python que funcionan también por su cuenta:

| Script | Qué hace |
|---|---|
| `escanear-pack.py` | Indexa un pack: loader, versión de MC, todos los mods leyendo `fabric.mod.json` / `mods.toml` / `neoforge.mods.toml`, jars anidados, mods de solo-cliente y solo-servidor, y avisos de versión |
| `buscar-en-jars.py` | Busca por nombre de entrada y por contenido **dentro de cada jar y zip**, un nivel de Jar-in-Jar incluido. Clasifica cada hallazgo (receta, loot, worldgen, arte, código Java…) |
| `comparar-packs.py` | Compara dos índices, da el veredicto de compatibilidad y, con `--si-porto <modid>`, calcula el cierre transitivo de dependencias contando como resueltas las que el destino ya tiene |
| `migrar.py` | Aplica un plan JSON: copiar, extraer de dentro de un jar, fusionar JSON conservando el destino, anexar o escribir. **Simula por defecto**; con `--aplicar` respalda y deja manifiesto con sha256 |
| `revertir.py` | Deshace una migración entera desde su manifiesto. Respeta los ficheros editados después de migrar |

```bash
python tools/escanear-pack.py "C:/packs/MiPack" --salida indice.json
python tools/buscar-en-jars.py "C:/packs/MiPack" "soul_reaper"
python tools/comparar-packs.py indice-origen.json indice-destino.json --si-porto simplyswords
python tools/migrar.py plan.json --aplicar
python tools/revertir.py "C:/packs/MiPack/migraciones/20260905-143022/manifiesto.json" --aplicar
```

---

## Trampas que ya conoce

Están cableadas en la skill porque cada una cuesta un arranque perdido:

- **Las etiquetas de versión mienten.** Un mod anunciado como 1.20.1 puede traer un jar `+1.20.4`. El índice los marca como `sospechosos_version`
- **`assets/minecraft/font/default.json` sustituye la fuente entera.** Sin los tres proveedores vanilla, todo el texto del juego sale en cuadrados. La búsqueda marca estos ficheros como `fuente-CUIDADO`
- **Un mod de servidor en el cliente lo rompe**, y el error rara vez menciona al culpable
- **El crash real no siempre está en `latest.log`** — cuando un mod muere antes de que arranque el juego, solo aparece en `launcher_log.txt`
- **Rutas que cambian entre versiones:** `recipes/` → `recipe/` y `loot_tables/` → `loot_table/` a partir de 1.21
- **Worldgen solo cuenta en mundo nuevo.** Un datapack de biomas copiado a un mundo existente no hace nada

---

## Límites

No hace magia. Si la feature vive en código Java compilado, no se puede portar copiando ficheros: o te llevas el mod entero con sus dependencias, o se recrea con un datapack lo que se pueda. La skill lo dice antes de intentarlo en vez de dejarte con un pack roto.

Tampoco descarga mods: si falta una dependencia que no está en el pack de origen, te dice cuál y la bajas tú de Modrinth o CurseForge.

---

## Sobre redistribución

Muchos mods no permiten redistribución. Portar features a tu pack o al servidor de tus amigos es una cosa; publicar un pack con jars ajenos dentro es otra, y bastantes autores lo prohíben expresamente. Si vas a distribuir el pack, lista los mods y que cada uno se descargue de su fuente — que es justo lo que hace el formato `.mrpack`.

---

## Licencia

MIT.
