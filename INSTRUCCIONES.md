# Instrucciones — Modpack Migrator

Analiza un modpack de Minecraft y lleva features suyas a otro pack, sin romperlo.

---

## Requisitos

- **Claude Code** instalado
- **Python 3.9 o superior** — comprueba con `python --version` en una terminal

  Si no lo tienes, descárgalo de https://python.org/downloads y marca **"Add Python to PATH"** durante la instalación.

No hace falta nada más. Ni pip, ni librerías, ni cuentas de nada.

---

## Instalación

**Opción A — usar el kit tal cual (más simple)**

1. Descomprime la carpeta `kit-modpack-migrator` donde quieras
2. Abre esa carpeta con Claude Code
3. Escribe cualquier cosa y Claude te dará la bienvenida

**Opción B — tenerlo disponible en todos tus proyectos**

Copia la skill entera a tu carpeta global de skills:

```bash
# Windows
xcopy /E /I ".claude\skills\modpack-migrator" "%USERPROFILE%\.claude\skills\modpack-migrator"

# macOS / Linux
cp -r .claude/skills/modpack-migrator ~/.claude/skills/
```

Importante: hay que copiar **la carpeta entera**, no solo el `SKILL.md`. Las herramientas de `tools/` van dentro.

---

## Cómo usarlo

### Preparar las rutas

Necesitas la carpeta donde vive cada pack. La que contiene `mods/`.

| Launcher | Dónde está |
|---|---|
| Modrinth App | `%APPDATA%\ModrinthApp\profiles\<nombre>` |
| CurseForge | `Documents\curseforge\minecraft\Instances\<nombre>` |
| Prism / MultiMC | `<carpeta de Prism>\instances\<nombre>\.minecraft` |
| Servidor | la carpeta del servidor, donde está el `.jar` |

También acepta un `.mrpack` o el `.zip` de CurseForge sin descomprimir, aunque para portar contenido de verdad hace falta el pack instalado (los archivos empaquetados no traen los mods dentro, solo la lista).

### Pedirlo

Abre la carpeta con Claude Code y descríbelo en lenguaje normal:

> "Me gusta cómo funciona el sistema de sed en `C:\packs\SurvivalHardcore`. Quiero llevarlo a mi pack en `C:\packs\MiPack`."

> "Analiza `C:\packs\CoolPack` y dime cómo consigue que el HUD se vea así."

> "Compara estos dos packs y dime qué mods tiene uno que no tenga el otro."

> "Mi pack peta al arrancar desde que copié unos mods. Arréglalo."

**Describe la feature como la ves en el juego**, no con nombres técnicos. "El sistema de hogueras", "los cofres que se abren solos", "la barra de estamina". Claude ya se encarga de traducirlo a IDs.

---

## Qué va a pasar

1. **Indexa los dos packs** y te dice versión, loader y número de mods de cada uno
2. **Veredicto de compatibilidad** — VERDE, AMARILLO o ROJO. Si es ROJO te explica por qué y qué se puede hacer en su lugar
3. **Busca la feature** dentro de los jars y te dice qué mod la gobierna
4. **Te enseña el coste** — la lista de mods que hay que llevarse. Aquí decides tú si sigue adelante
5. **Simula la migración** — verás exactamente qué ficheros tocaría, sin que toque ninguno
6. **La aplica** cuando digas que sí, con respaldo de todo lo que pise
7. **Te da el comando para deshacerlo** entero si no te gusta

---

## Deshacer

Cada migración deja una carpeta en tu pack de destino:

```
<tu pack>/migraciones/20260905-143022/
├── manifiesto.json      ← qué se cambió
└── respaldo/            ← copia de todo lo que se pisó
```

Para revertirla, pídeselo a Claude o hazlo tú:

```bash
python tools/revertir.py "<tu pack>/migraciones/20260905-143022/manifiesto.json" --aplicar
```

Respeta los ficheros que hayas editado tú después de migrar: no los borra.

---

## Las herramientas por separado

Están en `.claude/skills/modpack-migrator/tools/` y funcionan solas si te apañas con la terminal:

| Herramienta | Para qué |
|---|---|
| `escanear-pack.py` | índice completo de un pack: mods, versiones, dependencias, avisos |
| `buscar-en-jars.py` | busca texto o ficheros **dentro** de los jars, que es donde está todo |
| `comparar-packs.py` | compara dos packs y calcula qué arrastra portar un mod |
| `migrar.py` | aplica un plan de migración, con simulación y respaldo |
| `revertir.py` | deshace una migración entera |

Todas responden a `--help`.

---

## Estructura del kit

```
kit-modpack-migrator/
├── CLAUDE.md                 ← bienvenida y reglas
├── INSTRUCCIONES.md          ← este fichero
├── README.md                 ← para GitHub
└── .claude/skills/modpack-migrator/
    ├── SKILL.md              ← la skill
    └── tools/                ← las 5 herramientas
```

---

## Si algo va mal

**"python no se reconoce como comando"** — Python no está instalado o no está en el PATH. Reinstálalo marcando "Add Python to PATH".

**"no es una carpeta"** — le has dado un `.mrpack` o un `.zip` a una herramienta que necesita el pack instalado. Instala el pack en tu launcher primero.

**El pack destino ya no arranca** — revierte con `revertir.py` y cuéntaselo a Claude: lee los logs y te dice qué pasó.

**No encuentra la feature** — prueba a describirla con el texto exacto que aparece en pantalla en inglés. Los mods guardan sus nombres en `lang/en_us.json` y ese es el mejor punto de entrada.
