# Resultados La Liga asistido por IA

## Estado de la versión inicial

Este primer commit conserva el prototipo con Qwen2.5 1.5B antes del cambio a Llama 3.2 3B.
Las pruebas deterministas registradas cubren los seis módulos Python y los cinco módulos
JavaScript al 100% en sus métricas correspondientes, pero eso no garantiza respuestas
correctas del modelo. La última conversación real pasó el resultado y el próximo partido,
y falló al redactar los tres posteriores; no se publicó una lista parcial.
Los detalles, límites y comandos reproducibles están en [tests/e2e/README.md](tests/e2e/README.md).
Los modelos y artefactos de prueba no se incluyen en el repositorio.

Una app local para preguntarle cosas de La Liga a una IA que corre **en tu navegador**.
Sin nube, sin API keys: los datos los sirve un backend Python y el modelo (WebLLM + WebGPU)
se ejecuta en tu propia GPU.

## Cómo está armado

- **Python (solo stdlib)** = datos. `laliga.py` baja la tabla, los resultados y los próximos
  partidos desde ESPN, los cachea 5 minutos y los deja en un payload. `tools.py` convierte
  ese payload en cinco herramientas con datos estructurados.
- **Navegador** = IA. `web/app.js` carga un modelo pequeño con WebLLM/WebGPU; `web/agent.js`
  hace el ciclo: el modelo **elige** una herramienta, la app ejecuta contra la API y el
  modelo **redacta** la respuesta con el JSON que volvió. Nada sale de tu equipo.

```
laliga.py      → fetch + cache + shaping + context_text
resolve.py     → contexto verificado: equipo, referencia temporal y cantidad
tools.py       → 5 herramientas: validan argumentos y devuelven datos estructurados
server.py      → estáticos de web/ + /api/* (incluye /api/tool)
web/agent.js   → ciclo de tool calling: el modelo elige, la app ejecuta, el modelo redacta
web/           → UI (HTML/CSS/JS, sin frameworks ni build)
web/webmcp.js  → herramientas WebMCP para agentes (sobre /api/ask)
check.py       → self-check del payload y del resolvedor
check_tools.py → self-check de la capa de herramientas (offline)
mcp_server.py  → servidor MCP (stdio) con tools + panel MCP-UI
check_mcp.py   → prueba end-to-end del servidor MCP con el cliente oficial
```

## Requisitos

- Python 3.14 (probado con 3.14.4). La app web y `check.py` usan solo la stdlib; el servidor
  MCP opcional necesita un venv con el paquete `mcp` (ver «MCP opcional»).
- Chrome o Edge 113+ con **WebGPU** habilitado (en Windows, aceleración por hardware activa).
- ~1 GB de descarga la primera vez que cargás el modelo (queda cacheado en el navegador).

## Cómo correrlo (desde Windows)

```powershell
wsl bash -c "cd '/home/jesus/prueba deepseek' && python3 server.py"
```

Después abrí <http://localhost:8000> en Chrome o Edge.

El servidor escucha en `0.0.0.0:8000` por defecto. Podés cambiarlo:

```bash
python3 server.py --host 127.0.0.1 --port 8080
```

## Modelos disponibles

| Modelo | ID | Tamaño aprox. |
| --- | --- | --- |
| Qwen2.5 1.5B (recomendado) | `Qwen2.5-1.5B-Instruct-q4f16_1-MLC` | ~1 GB |
| Llama 3.2 1B | `Llama-3.2-1B-Instruct-q4f16_1-MLC` | ~0.7 GB |
| Qwen2.5 3B | `Qwen2.5-3B-Instruct-q4f16_1-MLC` | ~1.9 GB |

La descarga **nunca arranca sola**: tocá «Cargar modelo». La elección se guarda en
`localStorage` (`prueba-deepseek:model`).

## Cómo el modelo consulta la API (tool calling real)

El modelo **no recibe la respuesta masticada**: recibe la pregunta, elige una herramienta y
recién después lee los datos que devolvió la API. El turno tiene tres pasos:

```
pregunta
   │
   ├─ 1. el modelo elige UNA herramienta   → {"herramienta": "...", "argumentos": {...}}
   │      (JSON forzado por gramática, no texto libre)
   ├─ 2. el navegador POSTea /api/tool     → Python valida y devuelve datos JSON
    └─ 3. el modelo redacta la respuesta    → una línea por partido usando SOLO ese JSON
```

Si el paso 2 devuelve `ok: false` (por ejemplo `posicion(equipo="liga")`, porque «liga» no es
un equipo), el error en español vuelve al modelo y hay **un** reintento. Si vuelve a fallar,
la UI muestra un aviso, sin sustituir la respuesta por texto del resolvedor.
Si falla la redacción, se conserva el último partido publicado como referencia.

### Seguimientos y varios partidos

- «Después de ese partido» busca encuentros posteriores al partido verificado que
  se mostró. El rival de ese encuentro no se convierte en un filtro permanente.
- «Después del 1 de noviembre de 2026» usa una fecha de calendario explícita,
  aunque se haya perdido el contexto. Se excluye ese día en hora de España.
- «Los tres próximos partidos del Madrid» vuelve al calendario próximo y solicita
  hasta tres encuentros; el modelo recibe todos y la interfaz muestra una tarjeta por encuentro.
  Para reducir la carga de Qwen pequeño, la redacción se hace en una llamada local
  breve por encuentro, en orden, en lugar de pedirle que redacte toda la lista a la vez.
  Esto aumenta las llamadas de generación (tres para tres encuentros), pero cada
  llamada recibe únicamente los datos de su partido. No se publica una lista parcial
  si alguna redacción no supera la validación.
- «Y los 3 siguientes» conserva el equipo y busca tres encuentros posteriores al
  último mostrado. «El siguiente a ese y después los tres siguientes» solicita
  cuatro en total: el siguiente y otros tres, sin saltarse el primero.
- «¿Seguro?» vuelve a consultar el encuentro exacto, sin avanzar ni regresar al primero.
- Las preguntas sin evidencia disponible, como el Balón de Oro, no reciben la tabla
  como contexto ni generan texto libre. El modelo local selecciona una respuesta
  limitada al alcance de la aplicación; la app valida esa selección antes de mostrarla.

`proximo_partido` acepta `cantidad` (entero de 1 a 4), `despues_de` (instante de un
evento verificado), `despues_del_dia` (`YYYY-MM-DD`, límite exclusivo en España) o
`fecha_exacta` (confirmación). Las referencias temporales son excluyentes. El chat
solicita un encuentro por defecto; los clientes existentes que omiten `cantidad`
conservan el límite anterior de hasta tres. Si hay menos disponibles, se devuelven
solo esos encuentros, sin inventar partidos ni horarios.

### Por qué no se usa `tools` nativo

WebLLM 0.2.79 solo soporta function calling en tres modelos, y la lista es literal:

- `Hermes-2-Pro-Llama-3-8B`
- `Hermes-2-Pro-Mistral-7B`
- `Hermes-3-Llama-3.1-8B`

Pasar `tools` con `Qwen2.5-*` o `Llama-3.2-1B` lanza `UnsupportedModelIdError`. Los tres Hermes
son de 8B: ~4.9 GB de VRAM, y esta máquina tiene una **iGPU Intel gen-12lp** que no los puede
cargar. El `tools` nativo queda descartado por hardware, no por gusto.

La alternativa que sí funciona es `response_format` con gramática:

```js
response_format: { type: "json_object", schema: JSON.stringify(schema) }
```

El motor compila un `GrammarMatcher` con ese esquema y el modelo **solo puede emitir** un JSON
que lo cumpla: la elección de herramienta deja de ser texto libre que hay que parsear a mano.
El esquema se arma **una sola vez** en `web/agent.js`: el matcher se reutiliza únicamente si la
cadena es idéntica byte a byte. La primera llamada paga ~2,5 s de `grammar_init_s`; las
siguientes, ~0.

### Tiempos medidos (headless Chrome, WebGPU con `--enable-unsafe-webgpu`)

| Paso | Medición |
| --- | --- |
| Carga del motor (Qwen2.5 1.5B q4f16, con el modelo ya descargado) | ~8–11 s (medido: 8,3 / 9,3 / 11,2 s) |
| Turno completo con una herramienta | 16–39 s (el primer turno paga la gramática) |
| Turno de seguimiento (`seguro?`) sin modelo | ~0,4 s |
| Elección de herramienta (una llamada con gramática) | 7–15 s |
| Primera llamada con un esquema nuevo (`grammar_init_s`) | +~2,5 s |
| Decodificación | ~5 tok/s |

Por eso el turno pide `max_tokens: 100` para elegir y `max_tokens: 90` para redactar: es un
modelo chico y el presupuesto de tokens es el reloj de la demo.

### El 7/8 y el reintento de validación

El prompt del selector acierta 7 de 8 preguntas de referencia. La que falla es «como va la
liga»: el modelo elige `posicion` con `equipo: "liga"`. No se arregla con un caso especial en
el código; se arregla **validando del lado de Python**: `/api/tool` responde `ok: false` con
««liga» no es un equipo de La Liga. Equipos disponibles: …», el cliente agrega ese error al
contexto y repite la llamada restringida. En el reintento el modelo elige `tabla`, que es la
herramienta correcta.

### Cómo se ve en el chat

Cada turno con modelo consulta una herramienta y muestra:

1. un panel MCP-UI con los datos consultados y escudos de ESPN;
2. la oración del modelo, sólo si pasa la validación de fecha, hora y estado provisional.
   Si falla la redacción, el panel permanece visible: no se publica una hora inventada.

La traza de herramientas queda en el estado interno y la consola de desarrollo, no en el chat.
Los argumentos que no declara una herramienta se descartan antes de ejecutar la petición.

### La oración final: proyección del JSON y candado anti-eco

Al redactor de 1,5B no le llega el payload crudo. Le llega **una proyección** (mismos valores,
menos filas) más **una tarea explícita según la forma del dato**:

| `datos` | Lo que ve el redactor |
| --- | --- |
| `partidos` | primer partido: `local`, `visitante`, `dia`, `hora` y estado de confirmación; sin estadio ni timestamp |
| goles | local, visitante y marcador: una oración completa en pasado con el marcador «4-1», nunca «marcó 4 goles» |
| `filas` de tabla | `total_equipos` + las 5 primeras filas: quién lidera y con cuántos puntos |
| `filas` de descenso | los 3 equipos: quiénes descienden y con cuántos puntos |
| una fila | puesto y puntos del equipo |

Sin esa poda el modelo **copiaba el JSON**. Medido: para «como va la liga» devolvía
`domingo 25 de octubre de 2026 { "pos": 1, … }`, porque la tabla de 20 filas no le entra en una
oración y la fecha de `today_label` lo invitaba a inventar un día que `tabla` no trae.
Dos candados quedaron en el código:

- `today_label` no viaja al redactor: cada partido lleva su propia fecha;
- si la oración trae llaves o un `"campo":`, `looksLikeJsonEcho` la descarta y la app muestra la
  panel con los datos verificados y un aviso de redacción no fiable. Fallar cerrado es
  mejor que mostrar JSON crudo como si fuera una respuesta.

Ejemplos reales de la última corrida (Chrome headless, modelo cargado):

| Pregunta | Oración del modelo |
| --- | --- |
| cuando juega el barca contra el madrid | El Barcelona juega contra el Real Madrid este domingo 25 de octubre de 2026 a las 21:00 horas. |
| como va la liga | Barcelona lidera la tabla con 27 puntos. |
| como salio el madrid | El Real Madrid ganó 4-1 al Sevilla el día domingo 18 de octubre de 2026. |
| quien desciende | El Espanyol está en descenso con 8 puntos, seguido del Valencia con 6 puntos y el Málaga con 5 puntos. |
| como va el betis | Real Betis está en el puesto 4 con 19 puntos. |

## Herramientas que el modelo puede elegir

| Tool | Argumentos | `datos` |
| --- | --- | --- |
| `proximo_partido` | `equipo`?, `rival`? | próximos 1–3 partidos (el cruce si hay dos equipos): local, visitante, `fecha_iso`, día, hora y estadio |
| `ultimo_resultado` | `equipo` | último partido jugado: local, visitante, goles, `fecha_iso`, día |
| `tabla` | — | 20 filas con puesto, puntos, jugados, ganados, empatados, perdidos y diferencia de goles |
| `posicion` | `equipo` | una fila de la tabla |
| `descenso` | — | los 3 equipos en descenso con puesto y puntos |

`tools.py` valida todo contra el payload: los alias (`barca`, `atleti`, `la real`) se resuelven
con la misma tabla que el resolvedor; un equipo inexistente, un rival igual al equipo o más de
dos equipos devuelven `ok: false` con un mensaje en español y la lista de equipos válidos.
Nunca completa un dato que la API no mandó: si ESPN no publica el estadio, `estadio` viaja
vacío.

## Qué sigue siendo determinístico

`resolve.py` no desapareció: cumple dos papeles que el modelo no puede cumplir.

- **Router**: clasifica la pregunta antes de tocar el modelo. Los seguimientos (`seguro?`,
  `creo q no`, `te lo estás inventando`) se re-verifican contra la API y **nunca** llegan al
  modelo; las preguntas ajenas al fútbol (`hola`, `cuál es la capital de Francia`) van al chat
  libre; `unknown_football` (fútbol que la API no puede responder, como «cómo está el vestuario
  del Barça») muestra un aviso en vez de improvisar.
- **Fallback sin modelo**: sin modelo cargado, o si el ciclo de herramientas falla dos veces,
  se muestra la tarjeta **Verificado · API**. La respuesta sale del mismo payload.

Con el modelo cargado el camino es: el modelo elige → Python consulta → el modelo redacta.
La tarjeta verificada queda como red de seguridad y como contexto de los seguimientos
(`previous_question`).

### Seguimientos y `previous_question`

El cliente conserva también `fixture_context`: equipo/rival y la fecha ISO del partido mostrado.
«Vale y el siguiente a ese?» reutiliza ese cruce con `despues_de`, consultando sólo encuentros
posteriores a ese evento de la fuente. Las repeticiones avanzan; «seguro?» y «a qué hora?»
verifican el partido actual sin volver al primero. Si falta contexto o se agota el calendario,
el seguimiento no se envía al chat libre ni se inventa otro partido. Una nueva pregunta
explícita por equipos vuelve a la selección normal. Limpiar el chat descarta el contexto.

`seguro?` / `creo q no` no son preguntas nuevas: son reacciones a la respuesta anterior. El
resolvedor no guarda estado, así que el cliente manda la pregunta que produjo esa respuesta
(`state.lastQuestion` en `web/app.js`) como `previous_question`. El servidor la recibe en
`POST /api/ask` o en `GET /api/ask?...&previous_question=...`, la resuelve de nuevo contra el
mismo payload y devuelve el intent del seguimiento con la misma respuesta verificada. Sin
contexto, el seguimiento vuelve vacío y la UI lo dice en vez de inventar.

## Preguntas de ejemplo

- ¿Cuándo juega el Barça contra el Madrid?
- ¿Cómo salió el Madrid?
- ¿Cómo va la liga?
- ¿Cuántos puntos tiene el Barcelona?
- ¿Quiénes están en descenso?
- Seguro? (después de una respuesta: la re-verifica contra la API)

## API

| Endpoint | Qué devuelve |
| --- | --- |
| `GET /api/health` | `{ok, season, updated_at}` |
| `GET /api/laliga/context` | payload completo (incluye `context_text`) |
| `GET /api/laliga/standings` | 20 filas de tabla |
| `GET /api/laliga/results?limit=N` | resultados, default 12, máx 100 |
| `GET /api/laliga/fixtures?limit=N` | próximos, default 6, máx 100 |
| `POST /api/ask` | body `{"question": "...", "previous_question": "..."}` (`previous_question` opcional, para seguimientos) → `{intent, confidence, answer, facts, teams, today_label, context_text}` |
| `GET /api/ask?q=...&previous_question=...` | mismo resultado que el POST (cómodo para curl); `previous_question` es opcional |
| `POST /api/tool` | body `{"name": "...", "args": {...}}` → `{ok: true, datos, content, isError: false}` o `{ok: false, error}`. `content` es el recurso MCP-UI y su texto de respaldo; el modelo sólo recibe `datos`. Un `args` que no sea objeto o un `name` faltante son `400` |
| `GET /api/tool?name=...&equipo=...&rival=...` | mismo resultado con los argumentos en la query (cómodo para curl) |

Todo JSON UTF-8 con `Cache-Control: no-store`. Si ESPN falla, la API responde
`502 {"error": "..."}` en vez de romperse.

## Self-check

No necesita el servidor levantado:

```bash
cd '/home/jesus/prueba deepseek' && python3 check.py
```

Verifica temporada, 20 equipos, ranks 1..20, resultados con scores enteros, fixtures
futuros, `today_label`/`HOY:` y que el `context_text` sea chico. Después corre el
resolvedor contra el payload en vivo: clásico, tabla, descenso y una invariante que
compara cada fecha de «próximo partido» contra un fixture real (el bug que esto evita).
Sale con código 1 si algo falla.

La capa de herramientas tiene su propio check con datos en vivo:

```bash
cd '/home/jesus/prueba deepseek' && python3 check_tools.py
```

Compara cada tool contra el payload en vivo sin hardcodear fechas, marcadores ni puntos:
`tabla` (20 filas 1..20 y los puntos del líder), `descenso` (exactamente 3, los últimos de
la tabla), `posicion` para los 20 equipos, `proximo_partido` (alias `barca`, y el clásico si
está programado), `ultimo_resultado` (`madrid`), los seis alias y los errores corregibles
(`equipo=liga`, rival igual al equipo, herramienta inexistente, equipo vacío).

Para el servidor MCP hay un check aparte que corre dentro del venv
(`.venv/bin/python check_mcp.py`): arranca el servidor por stdio, lista las 5 tools,
pregunta por el clásico y valida el panel MCP-UI. Ver «MCP-UI» más abajo.

## MCP opcional

`mcp_server.py` expone los datos como tools MCP por stdio y suma un panel MCP-UI. Necesita
el SDK oficial dentro del venv del proyecto:

```bash
cd '/home/jesus/prueba deepseek' && .venv/bin/python mcp_server.py
```

Sin el paquete instalado, el script avisa en español y sale con código 0.

## WebMCP (la app como herramienta para agentes)

La app no solo consume herramientas: también las ofrece. `web/webmcp.js` registra cinco
tools en `document.modelContext`, así un agente que tenga el navegador conectado puede
consultar La Liga sin pedirle nada al modelo local.

| Tool | Qué devuelve |
| --- | --- |
| `laliga_proximo_partido` | Próximo partido de un equipo (`team`), o de un cruce (`team` + `opponent`), con día, fecha y hora de España |
| `laliga_ultimo_resultado` | Último partido jugado de un equipo (`team`), con marcador y fecha |
| `laliga_tabla` | Tabla completa de 20 equipos con puntos, PJ y diferencia de goles |
| `laliga_descenso` | Los equipos que hoy están en descenso, con puesto y puntos |
| `laliga_preguntar` | Pregunta libre en español; si el resolvedor no reconoce el dato responde «No tengo ese dato verificado» |

Todas las tools van contra `POST /api/ask`, o sea contra el **resolvedor determinístico**:
nunca contra el LLM. Un agente recibe los mismos datos verificados que ves en el chat,
aunque no haya ningún modelo cargado.

Detalles que importan:

- **Hace falta el polyfill.** Chrome todavía no trae `navigator.modelContext` /
  `document.modelContext` nativo. `web/index.html` carga
  `https://cdn.jsdelivr.net/npm/@mcp-b/global@6.0.3/dist/index.iife.js` (288 KB,
  autocontenido) antes del módulo, con `defer` para no bloquear el render.
- **Si la CDN falla, no se rompe nada.** `webmcp.js` avisa una vez por consola y queda
  como no-op: el chat, la tabla y las tarjetas verificadas siguen funcionando igual.
- El registro ocurre **después de la primera carga exitosa** de datos y una sola vez
  (idempotente). El estado y el número de herramientas son internos y no aparecen en el header.
- `webmcpStatus()` es la única superficie que expone el módulo a la UI: devuelve
  `{ available, tools }`, sin estado mutable.

## MCP-UI (web y clientes MCP)

`laliga_panel(equipo="", rival="")` no devuelve solo texto: devuelve un `CallToolResult` con **dos** bloques,
para que sirva en cualquier cliente.

1. Un `EmbeddedResource` con `uri="ui://prueba-deepseek/panel"` y
   `mimeType="text/html"`: panel oscuro con CSS inline y un único script de ajuste de altura. Muestra escudos de
   `https://a.espncdn.com`, nombres, fecha y estado. Con equipo/rival filtra el cruce;
   sin argumentos muestra la tabla y los próximos seis partidos. Los textos se escapan.
2. Un `TextContent` de respaldo con la tabla en texto plano, así los clientes que no
   renderizan MCP-UI igual reciben los datos.

La web consume el mismo recurso generado por `panel.py` mediante `/api/tool` y lo renderiza
con `iframe.srcdoc` y `sandbox="allow-scripts"`, sin permisos de origen. La CSP permite sólo
el hash del script fijo de ajuste de altura; no permite conexiones, formularios ni otros scripts.
El chat acepta únicamente mensajes de altura numérica acotada desde la ventana exacta del panel,
sin dar acceso a su DOM. Así el recurso se integra sin una segunda barra de desplazamiento.
Las consultas de próximo partido muestran sólo el primer cruce; el JSON auditable conserva los demás.
Los módulos estáticos se sirven con `Cache-Control: no-store` para no conservar versiones antiguas.
Es el
formato **legacy MCP-UI**, transportado por un adaptador HTTP; no es una sesión MCP del
navegador ni una implementación de MCP Apps/AppBridge. El servidor stdio valida ese mismo
contenido con el SDK MCP. Qwen sigue descargado y ejecutándose localmente, sin OpenAI ni Claude.

El calendario consulta todos los años que abarca la temporada y elimina duplicados. Cuando
ESPN no confirma el horario (`timeValid` distinto de `true`), conserva el día publicado como
**fecha provisional** y muestra **Por definir**; no convierte la hora del timestamp provisional.

Pruebas determinísticas, sin consultar ESPN:

```bash
.venv/bin/python -B -m unittest discover -s tests -v
node --test web/agent.test.mjs
```

**Por qué no se usa `mcp-ui-server`.** Lo probamos y está roto contra este entorno:
`mcp-ui-server 1.0.0` llama a `create_ui_resource` y explota con
`ValidationError: TextResourceContents.uri Input should be a valid string [input_value=AnyUrl(...)]`
porque el `AnyUrl` de pydantic 2.14.1 no pasa la validación de `mcp` 2.x. Por eso el
recurso se arma a mano con los tipos del SDK (`types.EmbeddedResource`,
`types.TextResourceContents`) y `mcp-ui-server` quedó desinstalado del venv.

Cómo verificarlo de punta a punta (arranca el servidor como subproceso, lista las tools y
las ejecuta, incluido el panel):

```bash
cd '/home/jesus/prueba deepseek' && .venv/bin/python check_mcp.py
```

Sale con código 1 si algo falla. La fecha del clásico que se verifica **se deriva del
payload en vivo**, nunca está escrita a mano.

## Problemas comunes

- **«Este navegador no tiene WebGPU»**: usá Chrome/Edge 113+, actualizá el driver de video y
  revisá `chrome://gpu` (buscá «WebGPU»). En Linux puede hacer falta iniciar Chrome con
  `--enable-features=Vulkan`.
- **La descarga del modelo falla**: suele ser red o espacio en disco. Probá Llama 3.2 1B
  (0.7 GB) o borrá los datos del sitio para limpiar la caché de modelos.
- **«Out of memory»**: cerrá otras pestañas o elegí un modelo más chico.
- **Puerto ocupado**: `python3 server.py --port 8001`.
- **Desde Windows no abre `localhost:8000`**: probá `http://<ip-de-wsl>:8000`
  (`wsl hostname -I`). El server ya escucha en `0.0.0.0`.
- **Datos viejos**: se cachean 5 minutos; reiniciá el server para forzar la recarga.

Los datos salen del endpoint público de ESPN (`site.api.espn.com`) y se cachean 5 minutos.
