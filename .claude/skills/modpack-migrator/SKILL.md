---
name: modpack-migrator
description: "Analiza un modpack de Minecraft y porta features suyas a otro modpack de forma segura. Usa esta skill cuando el usuario quiera copiar una mecánica, receta, bioma, sistema de progresión, look visual o configuración de un modpack a otro; entender cómo está hecho un modpack; comparar dos packs; averiguar qué mod controla algo; o arreglar un pack que peta al arrancar tras añadir mods. Triggers: 'porta esta feature a mi pack', 'cómo hace este modpack X', 'copia esta mecánica', 'qué mod controla esto', 'analiza este modpack', 'compara estos dos packs', 'me gusta cómo funciona X en este pack', 'quiero esto en mi modpack', 'mi pack peta al arrancar', 'qué dependencias me faltan', 'ingeniería inversa de un modpack', 'modpack reverse engineering', 'port modpack feature'."
---

# Modpack Migrator — ingeniería inversa y porte de features

Desmonta un modpack (el **origen**) y lleva una feature suya a otro (el **destino**) sin romperlo.

**Regla fundamental: nunca copies un fichero sin saber de qué mod depende.** En un modpack casi nada es autónomo. Una receta apunta a items de otro mod, un bioma coloca bloques de un tercero, y un script menciona IDs que en el destino no existen. Copiar a ciegas no da un error claro: da un `NullPointerException` en el arranque o, peor, un mundo que se genera mal y no se arregla luego.

Las herramientas están en `tools/`, junto a este fichero. Son Python 3.9+ de biblioteca estándar: no hay nada que instalar.

---

## Fase 0 — Compatibilidad primero

Esto va **antes** de buscar nada. Si los packs no son compatibles, todo el análisis posterior sobra.

Pide las dos rutas (origen y destino) y ejecuta:

```bash
python tools/escanear-pack.py "<RUTA_ORIGEN>"  --salida indice-origen.json
python tools/escanear-pack.py "<RUTA_DESTINO>" --salida indice-destino.json
python tools/comparar-packs.py indice-origen.json indice-destino.json
```

`comparar-packs.py` da un veredicto:

| Veredicto | Qué significa | Qué se puede portar |
|---|---|---|
| **VERDE** | misma versión de MC y mismo loader | todo: jars, datapacks, resourcepacks, configs |
| **AMARILLO** | mismo loader, versión cercana (1.20.1 ↔ 1.20.4) | jars con riesgo de `NoSuchMethodError`; datapacks casi siempre |
| **ROJO** | loaders distintos, o versiones lejanas | solo datapacks (`data/`) y resourcepacks (`assets/`), que son vanilla |

**Un jar de Forge no carga en Fabric jamás**, coincida o no la versión. Si el veredicto es ROJO, dilo claramente antes de seguir y reorienta el trabajo hacia recrear la feature (Fase 5) o buscar el mod equivalente para el loader de destino.

Formatos aceptados: carpeta de instancia, carpeta de pack, `.mrpack` y `.zip` de CurseForge. Los dos últimos **no contienen los jars**, solo un índice con URLs: si el usuario da un pack empaquetado, hay que instalarlo o extraerlo antes de poder inspeccionar contenido.

---

## Fase 1 — Clasificar la feature antes de buscarla

Esto es lo que decide todo lo demás. Pregunta al usuario **qué ve en pantalla** y tradúcelo a una de estas categorías:

| Lo que el usuario pide | Dónde vive de verdad | Cómo se porta |
|---|---|---|
| Receta, crafteo | `data/<ns>/recipes/` (`recipe/` en 1.21+) | **datapack propio**; no hace falta el mod si los items existen en el destino |
| Drops, botín, cofres | `data/<ns>/loot_tables/` | datapack |
| Qué cuenta como qué (madera, minerales) | `data/<ns>/tags/` | datapack; los tags **se fusionan**, no se pisan |
| Biomas, estructuras, generación | `data/<ns>/worldgen/`, `structures/` | datapack, **pero** si coloca bloques del mod, arrastra el mod |
| Avances, misiones | `data/<ns>/advancements/`, `config/ftbquests/` | copiable; depende de que existan los items citados |
| Balance, números, dificultad | `config/*.toml`, `*.json5`, `*.properties` | **fusionar claves, nunca sobrescribir el fichero** |
| Texturas, modelos, HUD, fuentes | `assets/` dentro del jar, `resourcepacks/` | resourcepack propio |
| Menú, pantallas, título | mod de UI + su config | copiar mod + config |
| Mecánica real: combate, mobs, magia, física | **código Java dentro del jar** | **no se puede copiar**: o llevas el mod entero con sus dependencias, o la recreas |
| Recetas/eventos con scripts | `kubejs/`, `scripts/` | requiere KubeJS o CraftTweaker en el destino; hay que sanear IDs |

Si no sabes en qué categoría cae, búscalo y deja que los resultados lo digan: `buscar-en-jars.py` etiqueta cada hallazgo por tipo (`receta`, `loot`, `worldgen`, `arte`, `codigo-java`…).

**Si todo lo que aparece es `codigo-java`, la feature no es portable copiando ficheros. Dilo antes de intentar nada.**

---

## Fase 2 — Localizar la feature

`grep` no entra en un zip, y en un modpack casi todo vive dentro de los jars. Usa la herramienta:

```bash
# por nombre visible en pantalla (aparece en los lang/)
python tools/buscar-en-jars.py "<RUTA_ORIGEN>" "Soul Harvest" --en contenido

# por ID técnico, una vez lo sabes
python tools/buscar-en-jars.py "<RUTA_ORIGEN>" "soul_harvest"

# por tipo de fichero: todas las recetas de un namespace
python tools/buscar-en-jars.py "<RUTA_ORIGEN>" "data/mimod/recipes?/" --en nombres

# volcar todo a JSON para analizarlo sin llenar la conversación
python tools/buscar-en-jars.py "<RUTA_ORIGEN>" "patron" --salida hallazgos.json
```

Estrategia que funciona: **empieza por el texto que el usuario ve**. El nombre en pantalla está en `assets/<mod>/lang/en_us.json` y te da el ID real; con el ID ya encuentras recetas, loot y todo lo demás. Buscar directamente por una traducción al español rara vez funciona.

**Disciplina de contexto:** los resultados van agrupados por contenedor y recortados a propósito. No vuelques jars enteros a la conversación. Si necesitas ver un fichero concreto, extráelo con `migrar.py` y léelo, o usa `--salida` y haz grep sobre el JSON.

---

## Fase 3 — Calcular el coste real

Ya sabes qué mod gobierna la feature. Ahora, qué arrastra:

```bash
python tools/comparar-packs.py indice-origen.json indice-destino.json --si-porto <modid>
```

Devuelve el cierre transitivo de dependencias: qué jars hay que copiar, cuáles ya están cubiertos (incluidas las que viajan anidadas dentro de otro jar por Jar-in-Jar) y cuáles no están ni en el origen y hay que descargar a mano de Modrinth o CurseForge.

Antes de seguir, **enseña esta lista al usuario y espera confirmación**. Portar una feature suele costar 3-8 mods, no uno. Es su decisión saber si le compensa.

Comprueba también:
- **Mods de servidor y de cliente.** El escáner marca `[solo cliente]` y `[solo SERVIDOR]`. Colar un mod de servidor en el cliente es una fuente clásica de crashes. Si el destino tiene pack de cliente y de servidor separados, cada mod portado va a donde le toca.
- **Mismo mod, versión distinta.** Si el destino ya lo tiene en otra versión, pisarlo puede romper los mods que dependían de la versión anterior. Casi siempre es mejor quedarse con la del destino.
- **`sospechosos_version` en el índice.** Jars que el loader acepta pero que están compilados contra otra versión de MC. No son un problema hasta que hay un crash raro; entonces son los primeros sospechosos.

---

## Fase 4 — Migrar con red

**Nunca copies a mano ni con `cp`.** Se hace con un plan, porque un plan se simula, se revisa y se deshace.

Escribe `plan.json`:

```json
{
  "origen":  "<RUTA_ORIGEN>",
  "destino": "<RUTA_DESTINO>",
  "nota": "porta el sistema de hogueras de PackX",
  "operaciones": [
    {"tipo": "copiar", "origen": "mods/hogueras-1.2.jar", "destino": "mods/hogueras-1.2.jar"},

    {"tipo": "extraer", "jar": "mods/hogueras-1.2.jar",
     "entrada": "data/hogueras/recipes/",
     "destino": "datapacks/port-hogueras/data/hogueras/recipe"},

    {"tipo": "fusionar-json", "origen": "config/hogueras.json",
     "destino": "config/hogueras.json", "claves": ["dificultad"]},

    {"tipo": "anexar", "origen": "config/extra.toml", "destino": "config/extra.toml"}
  ]
}
```

Operaciones disponibles: `copiar` (fichero o carpeta), `extraer` (saca entradas de dentro de un jar; acepta `*` y rutas de carpeta), `fusionar-json` (mete claves en el JSON del destino **conservando lo que ya tenía**), `anexar` (añade al final con una cabecera fechada) y `escribir` (crea un fichero con contenido literal, útil para `pack.mcmeta`).

Ejecuta siempre en dos tiempos:

```bash
python tools/migrar.py plan.json              # simula: no toca nada
python tools/migrar.py plan.json --aplicar    # ejecuta
```

Al aplicar, todo fichero que se pise se respalda en `<destino>/migraciones/<fecha>/respaldo/` y queda registrado con su sha256 en `manifiesto.json`. Para deshacer la migración entera:

```bash
python tools/revertir.py "<destino>/migraciones/<fecha>/manifiesto.json" --aplicar
```

`revertir.py` restaura lo pisado y borra lo creado, pero **respeta los ficheros que se hayan editado después** de la migración (compara el hash). Eso hace que sea seguro revertir aunque el usuario haya estado toqueteando.

**Regla de configs: fusionar, nunca sobrescribir.** El `config/` del destino tiene ajustes que su dueño puso a mano. Sobrescribir un `.toml` entero para cambiar un número es la forma más rápida de romper un pack que funcionaba.

---

## Fase 5 — Cuando no se copia, se recrea

Si la feature es código Java, o el veredicto fue ROJO, copiar no es una opción. Entonces se recrea lo que se pueda con datapacks vanilla, que funcionan en cualquier loader:

- recetas, loot tables, tags y avances: portables tal cual entre versiones cercanas
- mecánicas simples: `data/<ns>/functions/*.mcfunction` con un `tick.json`
- estructuras: exportables si los bloques existen en el destino

Sé honesto sobre el límite: un datapack no reproduce una entidad nueva, un sistema de combate ni una GUI. Si la feature es eso, hay tres respuestas posibles y solo tres:

1. **"Hay que llevar el mod, y esto es lo que cuesta"** — con la lista de la Fase 3.
2. **"Existe para tu loader"** — si el mismo mod, o un equivalente, tiene versión para el loader de destino, esa es la respuesta barata y hay que darla primero.
3. **"Hay que escribirlo"** — cuando la mecánica solo existe en el otro loader y no hay equivalente.

**El caso 3 es donde termina esta skill y empieza `modpack-modsmith`**, que escribe mods propios cuadrados con el pack de destino y los prueba antes de entregarlos. Si está instalada, pásale tres cosas: la versión del loader del pack destino (ya está en el índice, campo `version_loader`), qué mecánica hay que recrear, y los números que hayas sacado leyendo el mod original.

Un aviso que conviene dar aquí: cuando alguien pide **"una alternativa"** a un mod, casi nunca quiere que le instales otro mod ajeno parecido. Quiere **eso mismo, en su pack**. Pregunta cuál de las dos cosas es antes de dar por buena la fácil.

---

## Fase 6 — Validar el arranque

Dile al usuario que arranque. Si peta:

1. **Mira los logs en el orden correcto.** `crash-reports/` el más reciente, luego `logs/latest.log`, y **`logs/launcher_log.txt`** — cuando un mod muere antes de que arranque el juego, el error real solo aparece ahí y `latest.log` no dice nada útil.

2. **Lee la excepción de verdad, no la primera línea roja.** `Missing dependency`, `Incompatible mod set` y `NoSuchMethodError` significan cosas distintas:
   - `Missing dependency` / `requires X` → falta un mod: vuelve a la Fase 3
   - `NoSuchMethodError` / `NoClassDefFoundError` → jar compilado contra otra versión: mira `sospechosos_version`
   - `Mixin apply failed` → dos mods se pisan; el culpable suele ser el que nombra el mixin
   - `NullPointerException` en registro → un ID que no existe: un fichero copiado apunta a un mod que no está

3. **Si no está claro, biseca; no supongas.** Ir mod por mod probando hipótesis es lento y falla. Aparta la mitad de lo que has portado, arranca, y repite con la mitad que falle. Cinco cortes aíslan el culpable entre treinta mods.

4. **Deshaz si hace falta.** Está `revertir.py`. Un destino que arranca vale más que una feature a medias.

---

## Trampas que cuestan arranques

- **Las etiquetas de versión mienten.** Un mod anunciado como 1.20.1 puede traer un jar `+1.20.4`. Fíate del nombre del fichero y de lo que declara el jar, no de la web.
- **`assets/minecraft/font/default.json` sustituye la fuente entera.** Si un mod o resourcepack lo trae sin los tres proveedores vanilla (`include/space`, `include/default`, `include/unifont`), **todo el texto del juego sale como cuadrados**. `buscar-en-jars.py` marca estos ficheros como `fuente-CUIDADO`.
- **`options.txt` lo reescribe Minecraft al cerrarse.** Editarlo con el juego abierto no sirve de nada.
- **Un mod de servidor en el cliente rompe el cliente**, y a veces el error no menciona al culpable.
- **Los datapacks copiados a un mundo ya creado no siempre se aplican.** Worldgen y estructuras solo cuentan en mundo nuevo. Avísalo antes de que el usuario se lleve el chasco.
- **Rutas que cambian entre versiones:** `recipes/` → `recipe/`, `loot_tables/` → `loot_table/`, `advancements/` → `advancement/` a partir de 1.21. Un datapack de 1.20 no carga en 1.21 sin renombrar.

---

## Qué presentar al terminar

1. Qué feature se ha portado y de dónde sale
2. Los ficheros y mods que se han añadido, con la ruta del manifiesto para revertir
3. Lo que **no** se ha podido portar y por qué
4. Qué comprobar en el juego, y si hace falta mundo nuevo
5. El comando exacto de `revertir.py`, por si acaso

Un aviso que conviene dar una vez: muchos mods no permiten redistribución. Portar features para un pack propio o para un servidor de amigos es una cosa; publicar un pack con jars ajenos dentro es otra, y muchos autores lo prohíben expresamente. Si el usuario va a distribuir el pack, lo correcto es listar los mods y que cada uno se descargue de su fuente (que es justo lo que hacen los `.mrpack`).
