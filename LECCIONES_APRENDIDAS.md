# Lecciones Aprendidas — Cerebro Digital

**Documento vivo**: se agrega una entrada cada vez que encontramos un error, un workaround, una decisión importante o una mejora relevante. Sirve como **memoria institucional** para futuras sesiones — humanas o de IA.

Última actualización: 2026-04-17.

---

## Cómo usar este documento

### Al inicio de cada sesión
Leer el índice y las lecciones relevantes al dominio de lo que se va a hacer.

### Al terminar una sesión
Agregar nuevas entradas con el formato:

```markdown
### L-XX · Título corto (Fecha) · [Categoría]

**Problema**
Qué pasó, qué intentamos, qué no funcionaba.

**Causa raíz**
Por qué pasó. Entender esto es más importante que el fix.

**Fix**
Qué hicimos para resolverlo.

**Lección**
Qué recordar / aplicar en el futuro.

**Evitar a futuro**
Regla práctica para no repetirlo.
```

---

## Índice

- [Infraestructura y migración](#infraestructura-y-migración)
- [Performance Postgres](#performance-postgres)
- [MCPs y Edge Functions](#mcps-y-edge-functions)
- [ChatGPT Developer Mode](#chatgpt-developer-mode)
- [Supabase billing y cuotas](#supabase-billing-y-cuotas)
- [Datos y calidad](#datos-y-calidad)
- [Comunicación con el usuario](#comunicación-con-el-usuario)
- [Meta: trabajo con Claude](#meta-trabajo-con-claude)

---

## Infraestructura y migración

### L-01 · Parquets exportados sin headers (2026-04-16) · [Datos]

**Problema**
Las tablas `tinsa.proyectos`, `tinsa.terminaciones`, `tinsa.tipologa` se cargaron con columnas genéricas `column00`, `column01`, ..., `column69`. ChatGPT no podía hacer queries significativas.

**Causa raíz**
La exportación desde MotherDuck a Parquet perdió los headers originales. Los archivos `.parquet` tenían nombres genéricos.

**Fix**
Crear **views** (`v_proyectos`, `v_tipologa`, `v_terminaciones`) con nombres **inferidos del contenido** de cada columna (ej: ver que `column01` = "ESTACION CENTRAL" → inferir que es `comuna`).

**Lección**
Los nombres originales de columnas son metadatos valiosos. Si se pierden, la tabla se vuelve inútil para herramientas automáticas.

**Evitar a futuro**
- Al exportar Parquet: siempre incluir headers (`HEADER = TRUE` en DuckDB).
- Si viene sin headers: **crear views inmediatamente** con nombres semánticos, antes de exponer los datos.

---

### L-02 · psql/DATABASE_URL no disponible localmente (2026-04-16) · [Infra]

**Problema**
No teníamos la password de Postgres. No podíamos usar `pg_dump`, `psql \copy`, ni conexión directa desde scripts locales.

**Causa raíz**
Diseño inicial de Supabase: la password es un secreto que no debe rotar, y solo se expone una vez en el dashboard.

**Fix**
Crear un **Edge Function `bulk-ingest`** que acepta `copy_csv` como acción, usa la variable `SUPABASE_DB_URL` inyectada por Supabase, y hace `COPY FROM STDIN` internamente. Loader Python en local hace HTTP POSTs con CSV chunks.

**Lección**
Los Edge Functions tienen `SUPABASE_DB_URL` auto-inyectada — son el puente perfecto cuando no tienes la password local.

**Evitar a futuro**
Si el usuario no tiene la password: **no pedir que la busque**, mejor proponer el bridge de Edge Function. Más seguro y más rápido.

---

### L-03 · Render era 10× más caro que Edge Functions (2026-04-17) · [Infra]

**Problema**
La arquitectura inicial usaba Render ($7/mes) para un FastAPI que consultaba Pinecone + Supabase. Sobre-ingeniería.

**Causa raíz**
Inercia histórica: el proyecto empezó con Render. Se mantuvo sin cuestionar.

**Fix**
Migrar toda la lógica a Edge Functions de Supabase (Deno/TypeScript). Costo: $0 adicional (incluido en Pro). Latencia menor (intra-datacenter).

**Lección**
Cada vez que haya un servicio HTTP consultando Postgres/Pinecone, pensar primero en Edge Functions antes que en servicios externos.

**Evitar a futuro**
Regla: **si la lógica es "recibir HTTP, consultar DB, devolver JSON", usa Edge Functions**. Render/Railway/Fly solo se justifican si hay estado, jobs largos, o librerías Python específicas.

---

## Performance Postgres

### L-04 · COUNT sobre 17M filas = timeout garantizado (2026-04-17) · [Performance]

**Problema**
Consulta tipo `SELECT comuna, COUNT(*) FROM personas_censo_2024 GROUP BY comuna` se cancelaba por `statement_timeout` de 30s. ChatGPT reintentaba y fallaba.

**Causa raíz**
Con 17.5M filas y comuna como columna de baja cardinalidad (~346 valores distintos), el hash aggregate es caro. Índices no ayudan para COUNT GROUP BY total.

**Fix**
Crear **tabla pre-agregada** `censo.poblacion_comuna` (429 filas) con población 2017, 2024 y % crecimiento ya calculados. Se materializa una vez; queries subsecuentes son instantáneas.

**Lección**
Para agregaciones que se repiten (especialmente las que cruzan MCPs), **materializar una tabla pequeña** es 600× más rápido que recomputar cada vez.

**Evitar a futuro**
Al diseñar un MCP sobre tabla grande: preguntar **"¿qué agregación van a hacer los usuarios 95% del tiempo?"** y materializarla de una.

---

### L-05 · Views con muchos LEFT JOINs son lentas (2026-04-17) · [Performance]

**Problema**
Las views `v_personas_2024` (con 15 LEFT JOINs a `value_labels_2024` para decodificar cada variable) tardaban minutos en agregados grandes.

**Causa raíz**
Aunque Postgres hace hash join contra la tabla chica (544 filas), multiplicar por 15 JOINs sobre 17M filas hace que la vista sea más lenta que la tabla cruda.

**Fix**
- Documentar en las `SERVER_INSTRUCTIONS` que las `v_*` son para **row-level** con filtros, no para COUNT GROUP BY masivo.
- Para agregaciones geográficas, usar la tabla cruda + `mv_geo_2024_lookup` (materialized view pequeña).

**Lección**
Decodificar es caro. Si el resultado final no necesita los nombres (solo códigos para GROUP BY), usar la tabla cruda es más rápido.

**Evitar a futuro**
- Crear materialized views pequeñas (diccionarios) para lookup rápido.
- No usar views con muchos JOINs en agregaciones grandes.

---

### L-06 · `CREATE INDEX CONCURRENTLY` no funciona via MCP de Supabase (2026-04-17) · [Postgres]

**Problema**
Error `25001: CREATE INDEX CONCURRENTLY cannot run inside a transaction block` al intentar crearlo vía `execute_sql` del MCP de Supabase.

**Causa raíz**
`execute_sql` envuelve toda query en `BEGIN/COMMIT`. `CREATE INDEX CONCURRENTLY` requiere autocommit, no puede vivir dentro de una transacción.

**Fix**
Usar `CREATE INDEX` sin `CONCURRENTLY`. Bloquea la tabla brevemente, pero funciona.

**Lección**
Las herramientas de MCP envuelven queries en transacciones automáticamente — no son 1:1 con psql.

**Evitar a futuro**
Si necesitas DDL que no soporta transacciones (CONCURRENTLY, VACUUM FULL, etc.), hacerlo en momento de poco tráfico y aceptar el lock, o usar pg_cron / scripts con conexión directa.

---

### L-07 · MCP timeouts en queries largas no siempre significan rollback (2026-04-17) · [Postgres]

**Problema**
`apply_migration` hizo timeout en el cliente MCP, pero el `CREATE INDEX` siguió corriendo server-side.

**Causa raíz**
El Supabase Management API mantiene la conexión incluso si el cliente HTTP se desconecta. La DDL continúa y eventualmente commitea.

**Fix**
Después de un timeout, **verificar si el cambio se aplicó** con `SELECT FROM pg_indexes` o `information_schema`, en vez de asumir rollback y reintentar.

**Lección**
Timeout del cliente ≠ rollback del servidor. Siempre verificar estado antes de reintentar.

**Evitar a futuro**
Después de DDL largas con timeout:
```sql
-- verifica si existe ya
SELECT * FROM pg_indexes WHERE indexname = 'nombre_indice';
-- o
SELECT relname FROM pg_class WHERE relname = 'tabla_nueva';
```

---

## MCPs y Edge Functions

### L-08 · `deploy_edge_function` no setea env vars (2026-04-17) · [MCP]

**Problema**
El template compartido del MCP usaba `LIBRARY = Deno.env.get("LIBRARY")`. Pero el tool `deploy_edge_function` no permite pasar env vars. Resultado: `LIBRARY` vacío en tiempo de ejecución.

**Causa raíz**
El API de deploy solo acepta nombre, archivos, entrypoint y `verify_jwt`. Las env vars/secrets son otro endpoint (`set_secrets`) no expuesto por el MCP.

**Fix**
Hardcodear `const LIBRARY = "censo"` (o la librería correspondiente) dentro de cada archivo `.ts`. Un archivo por librería, con ese valor ya fijo.

**Lección**
Cuando el deploy tool es limitado, preferir **literal en código** sobre **env vars externas**.

**Evitar a futuro**
Si se tienen N librerías con diferencias mínimas (como aquí), generar N archivos desde un template con script, no apoyarse en env vars.

---

### L-09 · ChatGPT Developer Mode no soporta Bearer header (2026-04-17) · [MCP]

**Problema**
El formulario de Developer Mode solo ofrece **OAuth / Sin autenticación / Mixta**. No hay opción para "Bearer token" directo.

**Causa raíz**
Decisión de diseño de OpenAI. Para connectors custom quieren OAuth estándar o nada.

**Fix**
Embeber el token en el **query string** de la URL:
```
https://.../mcp-censo-sse?key=mcp_censo_abc123
```
El servidor lee `url.searchParams.get("key")` además del header `Authorization`. Usuario configura "Sin autenticación" en el dropdown.

**Lección**
Si el cliente no soporta el mecanismo de auth preferido, **embeberlo en la URL** es el patrón estándar (aunque menos elegante que header).

**Evitar a futuro**
- El token en URL queda en logs del navegador/proxy. Advertir al usuario de no compartir la URL.
- Tener `mcp_auth.revoke_user()` disponible para rotar rápido si hay exposición.

---

### L-10 · ChatGPT cachea SERVER_INSTRUCTIONS al conectar (2026-04-17) · [MCP]

**Problema**
Después de redesplegar un MCP con nuevas instrucciones, los chats **abiertos** seguían usando la versión vieja.

**Causa raíz**
El protocolo MCP hace handshake `initialize` una vez por sesión. Las `SERVER_INSTRUCTIONS` se cachean ahí.

**Fix**
Para actualizar un chat existente:
1. Settings → Connectors → desconectar
2. Reconectar con la misma URL
3. Alternativa: abrir chat nuevo (el handshake es fresco)

**Lección**
Los cambios en Edge Function son **instantáneos** para requests nuevos, pero **sesiones abiertas son stale**.

**Evitar a futuro**
Después de redeploy, avisar al usuario explícitamente: "en tu chat actual no se verán los cambios, usa chat nuevo o reconecta".

---

### L-11 · ChatGPT confunde librerías por nombres parecidos (2026-04-17) · [MCP]

**Problema**
Usuario tenía un conector llamado "CENSO Chile" que apuntaba a la URL de CASEN. ChatGPT insistía en que "CENSO está devolviendo tablas de CASEN" (porque efectivamente era así).

**Causa raíz**
Humano pegó URL incorrecta al configurar el connector. Los nombres "CENSO" y "CASEN" son muy parecidos visualmente.

**Fix**
Revisar y corregir la URL del connector. Y documentar claramente qué URL corresponde a qué librería.

**Lección**
Copiar/pegar URLs con tokens similares es propenso a errores. Validar la primera llamada con `list_tables` y comparar con lo esperado.

**Evitar a futuro**
- Al dar URLs al usuario, incluir **en la descripción** qué schema espera ver. Permite detectar el swap rápido.
- Considerar que el servidor también devuelva su `library` en `initialize` para que el cliente compare.

---

### L-12 · El MCP protocolario requiere SSE + JSON-RPC, no REST plano (2026-04-17) · [MCP]

**Problema**
Los primeros 5 MCPs estaban implementados como REST simple (`POST /functions/v1/mcp-censo`). ChatGPT Developer Mode no los podía conectar.

**Causa raíz**
Developer Mode espera **Model Context Protocol real**: JSON-RPC 2.0 sobre Server-Sent Events, con métodos `initialize`, `tools/list`, `tools/call`.

**Fix**
Reescribir los 5 MCPs como servidores MCP-compliant (archivos `-sse`), mantener los REST originales para uso programático (curl, scripts).

**Lección**
"MCP" es un protocolo específico, no "cualquier API para LLM". Si se integra con herramientas del ecosistema (Claude Desktop, ChatGPT Connectors), hay que seguir el spec.

**Evitar a futuro**
Al empezar un nuevo MCP:
1. Definir: ¿Se va a conectar a Claude Desktop/ChatGPT Connectors? → usar protocolo SSE+JSON-RPC.
2. ¿Solo se va a llamar desde curl/Python? → REST simple está bien.

---

## ChatGPT Developer Mode

### L-13 · `@CENSO` no invoca el conector en chat normal (2026-04-17) · [ChatGPT]

**Problema**
El usuario pensó que `@CENSO Chile @CASEN Chile` en un mensaje invocaba los MCPs. No lo hace — `@` es para Custom GPTs, no para Developer Mode connectors.

**Causa raíz**
Confusión entre dos features de ChatGPT:
- **Custom GPT + Actions**: se invoca por URL del GPT, tools OpenAPI.
- **Developer Mode + MCP Connectors**: se activa el conector en el toolbar del chat, no por `@`.

**Fix**
Instruir al usuario que active los connectors vía el botón de herramientas (clip/+) en el chat, no con `@`.

**Lección**
Hay muchas formas de conectar IA a datos externos. Saber cuál usa el usuario evita confusiones.

**Evitar a futuro**
Al entregar un MCP a un usuario, incluir **screenshot de cómo activarlo en su cliente específico**.

---

### L-14 · Queries complejas cross-MCP toman 3-5 minutos (2026-04-17) · [ChatGPT]

**Problema**
Pregunta real de 3 MCPs cruzados tardaba 3-5 minutos en ChatGPT. Usuario ansioso.

**Causa raíz**
- ChatGPT llama secuencialmente (no paralelo) cada tool
- Queries sobre 17M filas toman 20-30s cada una
- ChatGPT razona entre llamadas
- Si hay timeout, reintenta con otra estrategia → +30s más
- Multiplicado por 3 MCPs = 3-5 min

**Fix**
Crear tablas pre-agregadas (ver L-04) → tiempos bajan a ~10s total.

**Lección**
El tiempo de respuesta de un cruce multi-MCP es **lineal en el peor query**. Optimizar el más lento baja el total.

**Evitar a futuro**
Antes de exponer un nuevo MCP, correr el **top-10 queries que los usuarios harán** y asegurar que todas respondan en <5s.

---

## Supabase billing y cuotas

### L-15 · Spend Cap ON puede detener writes (2026-04-17) · [Billing]

**Problema**
El usuario tenía "Spend Cap enabled" por default. Esto corta servicios al exceder cuota incluida (read-only mode, edge functions paradas).

**Causa raíz**
Es la opción default para evitar sorpresas en la factura, pero tiene efecto secundario: la DB puede entrar en read-only.

**Fix**
Desactivar Spend Cap → permitir auto-escalado con costo marginal. Agregar alerta de billing como salvaguarda.

**Lección**
"Spend Cap" protege el bolsillo pero mata el servicio. Para producción, siempre preferir auto-scaling + alertas.

**Evitar a futuro**
Al configurar Supabase Pro: deshabilitar Spend Cap, configurar alerta a email para umbral de gasto (ej $30).

---

### L-16 · Disk auto-scaling avisa por email (2026-04-17) · [Billing]

**Problema**
Llegó un email "Your Supabase project's disk was automatically expanded" que asustó al usuario.

**Causa raíz**
Al cruzar 8 GB (cuota incluida), Supabase auto-expande en 50% cada vez que se cruza el 90%. Notificación automática.

**Fix**
Ninguno — es comportamiento esperado. Solo explicar al usuario el costo marginal ($0.125/GB-mes).

**Lección**
Los emails automáticos de cloud providers son informativos, no de emergencia. Pero asustan al primero.

**Evitar a futuro**
- Al diseñar un sistema con datos grandes: avisar proactivamente al usuario que va a recibir notifications de auto-scaling.
- Pre-calcular costo esperado y compartirlo: "tu DB estará en 14GB = $0.75/mes extra, el email te va a llegar mañana".

---

## Datos y calidad

### L-17 · CASEN repite cada hogar en múltiples filas (2026-04-17) · [Datos]

**Problema**
Cálculos de ingreso mediano "de hogar" daban números raros (muy bajos) porque cada hogar aparecía una vez por cada persona del hogar.

**Causa raíz**
CASEN es microdato a **nivel persona**. El campo `folio` identifica al hogar; un hogar con 4 personas aparece en 4 filas con el mismo `folio` y mismo `ytotcorh`.

**Fix**
Deduplicar por `folio` antes de calcular estadísticas de hogar:
```sql
WITH hogares_unicos AS (
  SELECT DISTINCT ON (folio) folio, ytotcorh, expr, ...
  FROM casen_2024
)
SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ytotcorh) FROM hogares_unicos;
```

**Lección**
Datasets de encuestas suelen estar a nivel persona pero tener columnas de hogar repetidas. **Siempre verificar la granularidad antes de agregar.**

**Evitar a futuro**
Embeber la dedup en tablas pre-agregadas, en docs del MCP, y en ejemplos.

---

### L-18 · Nombres de comunas inconsistentes entre schemas (2026-04-17) · [Datos]

**Problema**
Censo 2024 (view) tenía "Santiago" en mayúscula y con acentos. CENSO 2017 (raw) tenía código numérico. TINSA tenía "SANTIAGO" y a veces con variaciones de espacios.

**Causa raíz**
Cada fuente fue cargada sin normalización. Los convenios de nombres difieren entre Ministerio, INE y empresas privadas.

**Fix**
- Normalizar con `UPPER(TRIM(nombre))` al cruzar.
- En tablas pre-agregadas, almacenar siempre en MAYÚSCULAS.
- En `SERVER_INSTRUCTIONS`, advertir explícitamente.

**Lección**
Los nombres geográficos parecen estandarizados pero nunca lo son completamente. **Normalizar al momento de materializar**, no al momento de consultar.

**Evitar a futuro**
Crear una tabla maestra de comunas (`common.comunas_chile`) con código CUT oficial + nombre canonicalizado. Todas las tablas deberían referenciar ese código, no el nombre.

---

### L-19 · `periodo_muestra` no es obvio qué filtrar (2026-04-17) · [Datos]

**Problema**
TINSA tiene datos desde 2012. Sin filtro de `periodo_muestra`, "contar proyectos" devuelve acumulado histórico (10,000+) en vez de oferta activa (~70).

**Causa raíz**
El campo es tipo texto "1P 2012", "4P 2025" (período 1-4 del año). No es obvio para un LLM qué significa "reciente".

**Fix**
- En `SERVER_INSTRUCTIONS`: definir explícitamente "período activo = 4P 2025".
- Tabla pre-agregada con filtro ya aplicado (`oferta_comuna_reciente`).

**Lección**
Dominios específicos (inmobiliario, ambiental, etc.) tienen convenciones que no son obvias para un LLM. **Documentarlas en el MCP**.

**Evitar a futuro**
Al exponer datos a LLMs: escribir una página de "glosario y convenciones" dentro de las `SERVER_INSTRUCTIONS`.

---

## Comunicación con el usuario

### L-20 · No poder mandar email/SMS no es un blocker (2026-04-17) · [Comunicación]

**Problema**
Usuario se fue y pidió "envíame un email/SMS cuando termines". No tengo esas herramientas.

**Causa raíz**
El entorno de Claude no siempre tiene integraciones de mensajería. Asumirlo ya es un error.

**Fix**
Ser honesto inmediato: "no tengo esas herramientas, pero te dejo un archivo `.md` con el resumen". Cumplir lo que sí puedo.

**Lección**
Sincerarse rápido cuando hay una limitación. El usuario puede ajustar expectativas.

**Evitar a futuro**
Al prometer algo, verificar primero que las herramientas existen (`ToolSearch`). Si no existen, proponer la alternativa más cercana.

---

### L-21 · "Hazlo todo tú" requiere tolerancia a timeouts del user (2026-04-17) · [Comunicación]

**Problema**
Usuario pidió autonomía completa. Yo hacía deploys y queries, el user me preguntaba "como vas?" cada 5 min.

**Causa raíz**
El usuario no puede saber cuánto falta si no se lo digo. La paciencia humana es corta.

**Fix**
Dar **estimaciones numéricas concretas** ("~15 min más, 2 deploys y 1 test"), no evasivas ("ya casi").

**Lección**
Trabajo autónomo largo = reportar progreso con números reales, no con frases.

**Evitar a futuro**
Al empezar una tarea larga, anunciar: "X minutos, Y pasos. Te aviso cada Z". Y cumplirlo.

---

## Meta: trabajo con Claude

### L-22 · Claude se enamora del detalle técnico (2026-04-17) · [Meta]

**Problema**
En varios momentos empecé a explicar SSE, JSON-RPC, CTEs, window functions cuando el usuario solo quería "que funcione".

**Causa raíz**
Default de Claude: explicar el "por qué" antes del "qué". Útil en docs, inútil en chat operacional.

**Fix**
Cuando el usuario está en modo "hazlo rápido", responder con el **qué** directo y dejar el "por qué" para si lo pide.

**Lección**
Leer la urgencia del usuario. En modo producción → acciones + resultados. En modo exploración → acciones + contexto.

**Evitar a futuro**
Antes de enviar una respuesta, preguntarse: **"¿el usuario me pidió entenderlo, o pidió que lo haga?"**

---

### L-23 · El usuario prefiere lenguaje natural, no jerga (2026-04-17) · [Meta]

**Problema**
Prompts y respuestas con términos técnicos ("percentile_cont", "ivfflat probes", "materialized view") confundían al usuario.

**Causa raíz**
Default de Claude al trabajar con desarrolladores: jerga técnica. Pero el usuario es de negocio.

**Fix**
Parafrasear constantemente:
- "mediana" en vez de "PERCENTILE_CONT 0.5"
- "tabla pre-calculada" en vez de "materialized view"
- "grupo de tablas" en vez de "schema"

**Lección**
Adaptar vocabulario al usuario real, no al usuario que uno imagina.

**Evitar a futuro**
Primera interacción: preguntar o inferir el nivel técnico. Mantenerlo consistente.

---

### L-24 · TodoWrite sirve para mostrar avance, no para recordar (2026-04-17) · [Meta]

**Problema**
Me olvidaba de actualizar el todo list cuando completaba cosas. El user no sabía qué iba pasando.

**Causa raíz**
`TodoWrite` es útil para el usuario más que para Claude. Claude recuerda en contexto.

**Fix**
Usar TodoWrite **al terminar cada pieza concreta**, no como checklist interno.

**Lección**
Tools de UI (todos, chapters, screenshots) son para el humano.

**Evitar a futuro**
Al iniciar una tarea con 3+ pasos: crear todo list. Actualizar cada vez que cambia el estado real.

---

### L-25 · Cada DB expuesta a un MCP necesita "contexto LLM-facing" embebido (2026-04-17) · [MCPs y Edge Functions] · ⭐ PATRÓN CORE

**Problema**
Cuando conectas un MCP a una DB sin documentación interna, el LLM:
- Inventa nombres de tablas (`censo_2017` cuando en realidad es `personas_censo_2017`)
- Confunde schemas similares (CENSO vs CASEN)
- No sabe qué variable usar (ej: ¿es `ytotcor` o `ytotcorh` para ingreso del hogar?)
- Intenta queries sobre tablas crudas cuando hay vistas optimizadas
- Busca en la web por desconfianza de los datos que tiene

**Causa raíz**
Una DB "sin contexto" es una jungla para un LLM. Los nombres de tablas y columnas no son auto-explicativos. Ningún humano puede interpretar `p28_autoid_pueblo` sin el libro de códigos.

**Fix**
En cada MCP, la propiedad `instructions` del `initialize` handshake debe incluir:

1. **Mapa de tablas**: qué tablas hay, cuántas filas, qué contienen
2. **Variables clave**: nombre exacto + descripción + tipo
3. **Casos de uso frecuentes**: "para X pregunta usa Y tabla"
4. **Patrones de query listos**: SQL copy-paste que sabemos que funciona
5. **Advertencias de performance**: "esta tabla es lenta, usa la pre-agregada"
6. **Glosario del dominio**: convenciones internas ("4P 2025" = período 4 del 2025)
7. **Cómo decodificar**: reglas para JOIN con diccionarios
8. **Reglas estrictas**: "NO busques en web", "siempre cita fuente con este formato"

**Lección**
El MCP no es solo "HTTP a SQL". Es **HTTP + Manual de Usuario específico para LLMs**. Sin el manual, el LLM adivina mal.

**Evitar a futuro**
Antes de deployar un MCP a producción:
- Correr las top-10 preguntas que hará el usuario
- Verificar que las `instructions` cubren los casos
- Incluir ejemplos de query exitosa
- Incluir anti-patrones explícitos ("NO hagas X")

Template estándar de `SERVER_INSTRUCTIONS`:

```
Eres un analista de [dominio].

======================================================
 REGLAS CRÍTICAS
======================================================
1. NO BUSQUES EN WEB. [...]
2. USA SIEMPRE [tabla pre-agregada]. [...]
3. CITA fuentes como: [...]

======================================================
 TABLAS PRE-AGREGADAS (USA PRIMERO)
======================================================
[tabla]: [descripción]
  [ejemplo de query]

======================================================
 VARIABLES CLAVE
======================================================
[nombre_exacto]: [descripción]
[...]

======================================================
 PATRONES RECOMENDADOS
======================================================
1. Para [caso X] usa: [SQL]
2. Para [caso Y] usa: [SQL]

======================================================
 CÓDIGOS Y GLOSARIO
======================================================
[convenciones del dominio]
```

---

### L-26 · MCP devuelve códigos numéricos al usuario final (2026-04-18) · [MCPs] · ⭐ CRÍTICO

**Problema**
Usuario preguntó a CASEN "top comunas por ingreso" y ChatGPT respondió:
"Top ingresos: códigos 13132, 13114, 13113 (sector oriente RM principalmente)"
En vez de "Vitacura, Las Condes, La Reina".

**Causa raíz**
La tabla pre-agregada `casen.ingreso_comuna_2024` originalmente solo tenía `comuna_cod` (numérico), no `comuna_nombre`. El LLM devolvió lo que le dimos. Es culpa del diseño, no del LLM.

Es un patrón repetible: **cualquier tabla o view que exponemos a un MCP debe tener los nombres legibles pre-calculados**, no solo códigos.

**Fix**
1. Agregar columnas `*_nombre` a todas las pre-agregadas.
2. En las `SERVER_INSTRUCTIONS` poner regla explícita: "NUNCA devuelvas códigos al usuario final, siempre nombres".
3. Dar ejemplos de query MAL vs BIEN.
4. Para decodificar variables categóricas (sexo, pobreza), incluir bloque con CASE WHEN listo para pegar.

**Lección**
Lo que el LLM puede ver es lo que va a mostrar al usuario. Si en tu DB hay solo códigos, el usuario verá códigos. **Los nombres deben materializarse, no inferirse.**

**Evitar a futuro**
Al crear cualquier tabla/view para un MCP:
1. Revisar: ¿cada columna tiene versión legible?
2. Si alguna es código numérico, agregar su versión `_nombre` o `_texto` vía JOIN con diccionarios.
3. En las `SERVER_INSTRUCTIONS` del MCP:
   - Regla explícita: "NUNCA códigos al usuario final"
   - Ejemplos MAL/BIEN
   - Snippet de CASE WHEN para decodificar variables comunes
4. Probar con una pregunta ambigua: "dame top X por Y". Si responde con códigos, arreglar el MCP, no el prompt.

---

### L-27 · Encoding UTF-8 roto al cargar Excel con acentos (2026-04-18) · [Datos]

**Problema**
Los nombres de comunas del Censo 2017 salían como `MAIPÃš`, `CONCHALÃ`, `ALHUÃ‰` en vez de `MAIPÚ`, `CONCHALÍ`, `ALHUÉ`. Además había duplicados: `ALHUÉ` (bien) y `ALHUÃ‰` (mal) como si fueran comunas distintas.

**Causa raíz**
El Excel `variables_geograficas_censo_2017.xlsx` tenía los nombres codificados en **Latin-1** pero pandas los leyó como UTF-8 (o viceversa). Al cargarlos a Postgres quedaron mal desde el origen.

Al cruzar Censo 2017 con Censo 2024 **por nombre de comuna** (en lugar de código), los nombres malos no matcheaban con los buenos, creando filas huérfanas.

**Fix**
1. Rebuildar `poblacion_comuna` cruzando por **CUT (código único territorial)** en lugar de por nombre.
2. Priorizar el nombre de `geo_2024` (bien codificado) sobre el de `geo_comunas_2017`.
3. `FULL OUTER JOIN` para no perder comunas que solo aparecen en un censo.

**Lección**
Los nombres de entidades geográficas son **malos claves de join** — Chile tiene código CUT oficial, usarlo siempre. Los nombres son para mostrar, no para hacer match.

**Evitar a futuro**
Al integrar fuentes geográficas distintas:
1. **Unir por código CUT/INE**, nunca por nombre.
2. Normalizar nombres a una sola fuente autorizada (la más reciente bien codificada).
3. Si un Excel tiene acentos raros al importar: `pd.read_excel(..., encoding='latin-1')` o detectar con `chardet`.
4. Tener una tabla maestra `comunas_chile(cut, nombre, region)` para referenciar.

---

### L-28 · Embeddings via OpenAI son casi gratis para escala personal/PYME (2026-04-21) · [Integraciones con terceros]

**Problema**
Tentación de evitar OpenAI para "ahorrar" los cargos por embeddings en el MCP-rag (vector search). Se evaluaron alternativas: pgai, HuggingFace, Cloudflare Workers AI, self-host en Render/HF Spaces.

**Causa raíz**
Miedo a costos recurrentes sin medir el costo real. En realidad text-embedding-3-small cuesta $0.020 / 1M tokens = **$0.0000006 por búsqueda de 30 tokens**. Incluso 10K búsquedas/mes son $0.006.

**Fix**
Quedarse con OpenAI. El setup alternativo (HF Spaces, CF Workers, HF Inference) toma 15-30 min y solo ahorra centavos al mes.

**Lección**
Antes de migrar por "ahorro de costos", calcular el gasto REAL. Si es < $10/mes, no vale la pena el tiempo de migración + mantención.

**Evitar a futuro**
Para integraciones con terceros, la regla: **solo migrar si el costo mensual real es >10x el tiempo de migración valorizado**. Si el ahorro es marginal, priorizar simplicidad.

Dato útil: texto-embedding-3-small de OpenAI es el modelo comercial con mejor relación costo/calidad actualmente (2026-04). Solo superable por auto-host a escala >100K queries/mes.

### L-29 · pgai NO está disponible en Supabase Cloud (2026-04-21) · [Supabase]

**Problema**
Recomendé "pgai de Supabase" para generar embeddings gratis dentro de Postgres. El usuario preguntó si era rápido/bueno. Al verificar las extensiones disponibles, pgai **no está** en Supabase Cloud (es de Timescale Cloud).

**Causa raíz**
Confusión entre productos: pgai es una extensión creada por Timescale, disponible en su servicio "Timescale Cloud", pero NO en Supabase Cloud aunque ambos usen Postgres. Supabase tiene `pgvector` y `pg_net`, pero no un motor de inferencia integrado.

**Fix**
Corregí la recomendación. Las opciones reales para embeddings sin OpenAI son:
- Hugging Face Inference API (free tier 30K/mes)
- Cloudflare Workers AI (free tier 10K/día)
- Self-host en HF Spaces

**Lección**
Antes de recomendar una extensión/feature, **verificar** con `list_extensions` o docs oficiales del proveedor específico. No asumir que features de "Postgres con IA" están universalmente disponibles.

**Evitar a futuro**
Antes de afirmar que X feature está disponible: listar extensiones/capabilities reales del proyecto. Ser explícito entre: "Postgres estándar lo soporta" vs "Supabase lo hostea" vs "Timescale/Neon/otro lo hostea". Son clouds distintos aunque todos usen Postgres.

---

### L-30 · `request_log` silenciosamente vacío por 2 bugs simultáneos (2026-04-23) · [MCPs y Edge Functions] · ⭐ CRÍTICO

**Problema**
Fernando preguntó "¿qué queries ha hecho Laszlo?". Al consultar `mcp_auth.request_log` filtrando por `user_label='laszlog'`: **0 filas**. Sin embargo `mcp_auth.tokens.last_used_at` sí se actualizaba correctamente — sabíamos CUÁNDO usó cada librería, pero no QUÉ consultó.

Además: tampoco había filas de Fernando (el owner). El log llevaba semanas silenciosamente vacío. Nadie lo había notado porque los MCPs respondían OK al usuario final.

**Causa raíz**
Dos bugs encadenados:

1. **Permisos de rol**: el `INSERT INTO mcp_auth.request_log` desde el Edge Function corría con rol `authenticated` (o `anon`), que no tenía `GRANT INSERT` sobre esa tabla. Postgres rechazaba el INSERT silenciosamente (el código tenía `try{}catch{}` vacío para no romper la request principal).

2. **Overload ambiguo**: al crear `mcp_auth.log_request()` como función `SECURITY DEFINER` para esquivar el problema de permisos, dejé dos overloads coexistiendo (versión de 7 params y versión de 8 params con `sql_text`). Postgres no podía resolver cuál llamar y tiraba error al invocarla desde Deno/postgres.js — error que también se tragaba el `try/catch`.

**Fix**
1. Hacer `DROP FUNCTION` de ambos overloads.
2. Crear UNA sola versión con 8 params y `p_sql text DEFAULT NULL`:
   ```sql
   CREATE FUNCTION mcp_auth.log_request(
     p_library text, p_user_label text, p_tool text,
     p_ok boolean, p_latency_ms integer, p_rows integer,
     p_error text, p_sql text DEFAULT NULL
   ) RETURNS void
   LANGUAGE plpgsql SECURITY DEFINER
   SET search_path = mcp_auth, public
   AS $$
   BEGIN
     INSERT INTO mcp_auth.request_log(library, user_label, tool, ok, latency_ms,
                                      rows_returned, error_msg, sql_text)
     VALUES (p_library, p_user_label, p_tool, p_ok, p_latency_ms, p_rows, p_error, p_sql);
   EXCEPTION WHEN OTHERS THEN NULL;
   END $$;
   GRANT EXECUTE ON FUNCTION mcp_auth.log_request(text,text,text,boolean,integer,integer,text,text)
     TO authenticated, anon, service_role, postgres, PUBLIC;
   ```
3. Redeployar los 5 Edge Functions (censo v10, casen v7, tinsa v5, etnografico v4, rag v4) usando el mismo patrón `logReq(user,tool,ok,ms,rc,err,sqlArg)` y capturando `params.arguments.sql` como `sqlArg`.
4. Verificar con curl sobre los 5 → 5 filas nuevas en `request_log` con sql_text completo.

**Lección**
`try{}catch{}` vacío en código de logging + `EXCEPTION WHEN OTHERS THEN NULL` en la función SQL = **observabilidad ciega**. Dos capas de "no quiero romper la request principal" hacen que un bug de permisos o sintaxis viva por semanas sin detección.

**Evitar a futuro**
1. **Canario de logging**: agregar query programada (`pg_cron` cada 30 min) que verifica `COUNT(*) FROM request_log WHERE ts > now() - interval '1 hour'`. Si es 0 y los tokens tienen `last_used_at` reciente → alerta.
2. **Nunca tener overloads de funciones SQL con mismo nombre**. Usar `DEFAULT` para parámetros opcionales en una sola firma. Si ya hay dos: `DROP FUNCTION ... (firma_exacta)` antes de crear la nueva.
3. **Cuando `try/catch` silencia errores de logging**, al menos loguear a `console.error` del Edge Function — Supabase guarda esos logs por 7 días en `get_logs(service='edge-function')`.
4. **Preferir `SECURITY DEFINER` + `GRANT EXECUTE`** sobre intentar `GRANT INSERT` directo a tablas de `mcp_auth`. Centraliza permisos en una función, más fácil auditar.
5. Para cualquier tabla de auditoría/log: en los tests end-to-end incluir un `assert count(*) > 0` después de cada smoke test — pilla este tipo de bugs el día 1, no el día 40.

---

### L-31 · Hardening Supabase modela completo: 42 lints → 0 ERROR + 7 ataques bloqueados (2026-04-29) · [Seguridad] · ⭐ CRÍTICO

**Problema**
Llegó email Supabase 2026-04-29 alertando "Security vulnerabilities detected in your Supabase projects" sobre `modela`. El email solo agregaba 2 categorías (`rls_disabled_in_public` + `security_definer_view`). Al cruzar con MCP `get_advisors` aparecieron **42 lints distintos detrás** (3 ERROR + 39 WARN).

Hallazgos críticos en producción:
1. `public.mcp_api_tokens` con columna `token` en TEXTO PLANO + RLS off + expuesta vía PostgREST con anon key. Atacante con solo la URL del proyecto + anon key (que es pública por diseño) podía hacer `GET /rest/v1/mcp_api_tokens` y leer todos los tokens MCP en texto plano, suplantando a Fernando contra cualquier MCP-SSE.
2. `socio_estrategico.workspaces` SIN RLS — multi-tenant del producto Socio Estratégico roto.
3. View `public.mcp_tokens_overview` con `SECURITY DEFINER` — bypass total de RLS aunque arregles V1.
4. 12 funciones `SECURITY DEFINER` con `EXECUTE` para anon (`mcp_issue_token`, `insert_vectores_eia_batch`, `mcp_auth.mint_token`, etc.) — permitían emitirse tokens MCP sin auth.
5. 26 funciones con `search_path` mutable.
6. Bucket `urbanismo-raw` con policies UPDATE/INSERT abiertas para `authenticated` — usuarios podían modificar archivos.
7. 13 views con `SECURITY DEFINER` (default Postgres) — bypass de RLS automático.

**Causa raíz**
1. Setup inicial de la BD priorizó velocidad de desarrollo sobre rigor de seguridad. Patrón "primero hago que funcione, después aseguro" → terminó siendo "después" = nunca, hasta que llegó la alerta.
2. El email de Supabase reporta solo categorías agregadas, no el detalle. Dependerse del email = subestimar el riesgo. Hay que ir al security advisor o usar MCP `get_advisors` para ver el detalle completo.
3. Tablas con secrets en schema `public` por descuido. Schema `public` está expuesto a PostgREST con anon key por default — cualquier tabla nueva ahí necesita RLS desde el día 1.
4. Funciones `SECURITY DEFINER` reciben `EXECUTE` para anon por default en muchos templates de Supabase. Si la función modifica estado o accede a datos sensibles, eso es game-over.
5. Views por default son `SECURITY DEFINER` en Postgres. Si seleccionan de tablas con RLS, hacen bypass automático.
6. 2 sistemas paralelos de tokens en un mismo proyecto (`public.mcp_api_tokens` plain + `mcp_auth.tokens` HASH) por iteración del producto — ambos requieren protección, no solo el "actual".

**Fix**
Hardening completo en una sesión (2026-04-29). Patrón aplicado:

```sql
-- 1. Cualquier tabla *_tokens, *_secrets, *_credentials:
ALTER TABLE <schema>.<tabla> ENABLE ROW LEVEL SECURITY;
CREATE POLICY service_role_only ON <schema>.<tabla>
  FOR ALL TO service_role USING (true) WITH CHECK (true);
REVOKE ALL ON <schema>.<tabla> FROM anon, authenticated, PUBLIC;

-- 2. Cualquier view bypass-able:
ALTER VIEW <schema>.<view> SET (security_invoker = true);

-- 3. Cualquier función SECURITY DEFINER que reciba inputs sensibles:
REVOKE EXECUTE ON FUNCTION <schema>.<func>(args) FROM anon, authenticated, PUBLIC;
GRANT EXECUTE ON FUNCTION <schema>.<func>(args) TO service_role;
ALTER FUNCTION <schema>.<func>(args) SET search_path = pg_catalog, <schema>, public;

-- 4. Storage policies UPDATE/INSERT abiertas → DROP. Buckets públicos sirven URLs sin policies SELECT.
DROP POLICY IF EXISTS <bucket>_update ON storage.objects;
DROP POLICY IF EXISTS <bucket>_upload ON storage.objects;
```

Resultados:
- 9 tablas con RLS aplicado (incluyendo las 2 críticas)
- 12 funciones `SECURITY DEFINER` cerradas a anon
- 13 views convertidas a SECURITY INVOKER
- 3 storage policies eliminadas
- 8 funciones con search_path fijado
- Verificado con 7 ataques simulados con anon key → todos bloqueados (`{"code":"42501","message":"permission denied"}`)
- Custom GPT y MCPs (urbanismo, censo, casen, tinsa, etnografico, rag, buscar-notas) siguen funcionando — **0 downtime** durante hardening
- 42 lints → 22 (todos WARN no críticos: `function_search_path_mutable` residual + `vector_extension_in_public` aceptado por trade-off — moverlo rompería todos los tipos vector en producción)

Hardening complementario:
- 2FA cuenta Supabase activado con 2 factores (Google Authenticator + Microsoft Authenticator en iPhone). Supabase NO da recovery codes — por eso 2 apps = backup mutuo.
- 3 repos git limpiados: `socio-estrategico-digital`, `beneficios-bancarios-chile`, `api_rag_mvp` con `.env.backup` removido del tracking + `.gitignore` agregado.

**Lección**
1. **NUNCA confiar solo en el email de alerta de Supabase.** Reporta categorías agregadas, no el detalle real. Siempre ir al security advisor o usar MCP `get_advisors`.
2. **Tablas con tokens API NUNCA en schema `public` sin RLS.** Schema `public` está expuesto a PostgREST con anon key por default. Si tienes `*_tokens`, `*_secrets`, `*_credentials`: RLS ON desde día 1.
3. **Funciones `SECURITY DEFINER` + EXECUTE a anon = bomba.** Saltan RLS. Default seguro: `REVOKE EXECUTE FROM anon, authenticated` y solo grant a `service_role`.
4. **Views por default son SECURITY DEFINER.** Aplicar `security_invoker = true` a todas.
5. **2FA con 2 apps** porque Supabase no tiene recovery codes. Si pierdes el dispositivo, único recurso = Support con prueba de identidad.
6. **schemas internos NO se exponen a anon por default** (solo `public`). Schemas como `urbanismo`, `censo`, `casen`, etc están protegidos por defecto. PERO funciones `SECURITY DEFINER` en `public` pueden tocar tablas de schemas internos y bypass todo.
7. **2 sistemas paralelos de tokens** en un proyecto (legacy + nuevo) requieren proteger AMBOS, no asumir que solo el "actual" se usa.

**Evitar a futuro**
1. **Checklist pre-deploy obligatorio** en cualquier proyecto Supabase nuevo:
   - ¿Hay tablas en `public` con secretos? → moverlas a schema separado o RLS ON
   - ¿Hay tokens en texto plano? → migrar a `bytea` con HASH
   - ¿Hay funciones `SECURITY DEFINER` con grant default a anon? → REVOKE
   - ¿Hay views con datos sensibles? → `security_invoker = true`
   - ¿Bucket público necesita policies UPDATE/INSERT? → casi nunca, DROP
2. **Auditoría mensual** con `mcp__supabase__get_advisors`. Si aparecen ERROR nuevos → investigar.
3. **Documento `HARDENING_<proyecto>_<fecha>.md`** en raíz del proyecto cada vez que se haga audit.
4. **Rotación de keys cada 6 meses** como práctica estándar.
5. **CI hook con gitleaks** o similar para detectar secrets en commits.
6. **Cuando heredas un proyecto** con sistemas legacy (2+ generaciones de tokens, etc.) cerrar TODOS los sistemas con RLS, no asumir cuál se usa.
7. Para CUALQUIER tabla nueva con datos sensibles, aplicar el patrón template mencionado en Fix sección 1-4.

**Documento portable completo:** `04.RAG_EIA-DIA_Seleccionados_Modela/HARDENING_MODELA_2026-04-29.md` — incluye SQL exacto aplicado, comandos de verificación, ataques simulados, checklist para futuras auditorías. Aplica a TODOS los productos del proyecto modela (urbanismo, censo, casen, tinsa, etnografico, socio_estrategico, EIA ambiental).

---

## Plantilla para agregar lecciones nuevas

Copiar y rellenar:

```markdown
### L-XX · Título (YYYY-MM-DD) · [Categoría]

**Problema**


**Causa raíz**


**Fix**


**Lección**


**Evitar a futuro**

```

Categorías sugeridas:
- Infraestructura
- Performance
- MCPs y Edge Functions
- ChatGPT Developer Mode / Claude Desktop
- Supabase / Billing
- Datos y calidad
- Seguridad
- Comunicación con el usuario
- Meta: trabajo con Claude

---

## Estadísticas del documento

- Lecciones totales: **31**
- Primera lección: 2026-04-16
- Última lección: 2026-04-29
- Categorías cubiertas: 8 / 8

## Documentos de seguridad relacionados

- **`HARDENING_MODELA_2026-04-29.md`** — hardening completo cross-producto (urbanismo + censo + casen + tinsa + etnografico + socio_estrategico + EIA). SQL exacto, comandos de verificación, lección portable.
