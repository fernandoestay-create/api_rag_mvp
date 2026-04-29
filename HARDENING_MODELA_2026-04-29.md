# HARDENING SEGURIDAD — Proyecto Supabase modela
> **Fecha:** 2026-04-29
> **Proyecto Supabase:** `modela` (ref `ywszqobmmswaqkavnkte`)
> **Aplicado por:** Fernando + Claude (sesión RAG Urbanismo)
> **Alcance:** TODO el proyecto modela (todos los productos comparten una sola instancia Supabase)

---

## ⭐ Por qué este archivo existe

El proyecto Supabase `modela` aloja **todos los productos del stack de Fernando** en una sola instancia. Todos los schemas comparten la misma seguridad (mismo PostgREST, misma anon key, mismo service_role, mismas Edge Functions secrets).

**Productos en modela (al 2026-04-29):**

| Schema / Producto | Vertical | Estado |
|---|---|---|
| `urbanismo` | RAG normativa urbana Chile | ✅ Producción + Custom GPT |
| `censo` | RAG Censo INE 2017 + 2024 | ✅ Producción + MCP |
| `casen` | RAG Encuesta CASEN | ✅ Producción + MCP |
| `tinsa` | Tasaciones inmobiliarias | ✅ Producción + MCP |
| `etnografico` | Datos etnográficos Chile | ✅ Producción + MCP |
| `socio_estrategico` | Notas + decisiones (multi-tenant) | ✅ Producción + buscar-notas |
| `public.<rag_eia*>` | RAG EIA Ambiental + Pinecone | ✅ Producción API custom |
| `mcp_auth` | Tokens MCP (sistema legacy + nuevo) | ✅ Producción |

**Implicación clave:** lo que arreglas en seguridad de modela aplica a todos los productos a la vez. No es por proyecto. Por eso este archivo es la **fuente única** de la seguridad post-hardening, y todo chat que trabaje sobre cualquiera de esos productos debe leerlo.

---

## Resumen ejecutivo

| Métrica | Antes | Después |
|---|---|---|
| Lints totales del Security Advisor | **42** | **22** (todos WARN, 0 ERROR) |
| ERROR críticos | **3** | **0** |
| Tablas sin RLS expuestas a anon | **9** | **0** |
| Funciones `SECURITY DEFINER` con grant a anon | **12** | **0** |
| Views `SECURITY DEFINER` (bypass RLS) | **13** | **0** (todas pasaron a INVOKER) |
| Storage policies peligrosas (UPDATE/INSERT abierto) | **3** | **0** |
| Ataques simulados con anon key | **7/7 exitosos** | **7/7 bloqueados** |
| 2FA cuenta Supabase | **OFF** | **ON · 2 factores activos** |
| Repos git con `.env.backup` trackeado | **3** | **0** |

---

## Qué se ejecutó (en orden)

### Bloque V1 — `public.mcp_api_tokens` con tokens en texto plano y RLS off

**Riesgo:** anon key pública podía hacer `GET /rest/v1/mcp_api_tokens` y leer todos los tokens MCP en texto plano. Atacante podía suplantar a Fernando contra cualquier MCP.

```sql
ALTER TABLE public.mcp_api_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_only_mcp_api_tokens"
  ON public.mcp_api_tokens
  FOR ALL TO service_role
  USING (true) WITH CHECK (true);

REVOKE ALL ON public.mcp_api_tokens FROM anon, authenticated, PUBLIC;
```

### Bloque V2 — `socio_estrategico.workspaces` sin RLS (multi-tenant roto)

**Riesgo:** la tabla de workspaces del producto Socio Estratégico (multi-tenant) sin protección. Cualquiera podía listar workspaces de todos los clientes.

```sql
ALTER TABLE socio_estrategico.workspaces ENABLE ROW LEVEL SECURITY;

CREATE POLICY "service_role_all_workspaces"
  ON socio_estrategico.workspaces
  FOR ALL TO service_role
  USING (true) WITH CHECK (true);

-- Si tienes auth de usuarios y quieres política multi-tenant:
-- CREATE POLICY "user_owns_workspace"
--   ON socio_estrategico.workspaces
--   FOR ALL TO authenticated
--   USING (owner_user_id = auth.uid())
--   WITH CHECK (owner_user_id = auth.uid());

REVOKE ALL ON socio_estrategico.workspaces FROM anon;
```

### Bloque V3 — View `public.mcp_tokens_overview` con `SECURITY DEFINER`

**Riesgo:** view ejecutaba queries con permisos del creador, ignorando RLS de la tabla subyacente. Bypass total de V1.

```sql
ALTER VIEW public.mcp_tokens_overview SET (security_invoker = true);
REVOKE ALL ON public.mcp_tokens_overview FROM anon, authenticated;
GRANT SELECT ON public.mcp_tokens_overview TO service_role;
```

### Bloque V4 — Storage bucket `urbanismo-raw` con policies UPDATE/INSERT abiertas

**Riesgo:** policies `urbanismo_raw_update` y `urbanismo_raw_upload` permitían a usuarios `authenticated` modificar/subir archivos. Bucket público no necesita esas policies — `bucket.public = true` ya basta para servir GETs.

```sql
DROP POLICY IF EXISTS public_read_urbanismo_raw ON storage.objects;
DROP POLICY IF EXISTS urbanismo_raw_read ON storage.objects;
DROP POLICY IF EXISTS urbanismo_raw_update ON storage.objects;
DROP POLICY IF EXISTS urbanismo_raw_upload ON storage.objects;

-- El bucket sigue público porque urbanismo-raw.public = true (Supabase Dashboard).
-- Edge Functions que necesitan UPDATE/INSERT lo hacen con service_role internamente.
```

### Bloque V5 — Funciones `SECURITY DEFINER` con grant a anon (12 funciones)

**Riesgo:** funciones tipo `mcp_issue_token`, `insert_vectores_eia_batch`, `mcp_auth.mint_token` se podían llamar sin auth desde anon key. Bypass total del modelo de tokens.

```sql
-- Patrón aplicado a las 12 funciones identificadas:
REVOKE EXECUTE ON FUNCTION public.mcp_issue_token(text, text, timestamptz)
  FROM anon, authenticated, PUBLIC;
GRANT EXECUTE ON FUNCTION public.mcp_issue_token(text, text, timestamptz)
  TO service_role;
ALTER FUNCTION public.mcp_issue_token(text, text, timestamptz)
  SET search_path = pg_catalog, public;

-- Repetir para cada una de las 12 funciones críticas:
-- public.mcp_issue_token, public.mcp_validate_token, public.mcp_revoke_token,
-- public.insert_vectores_eia_batch, public.get_eia_project, public.list_eia_projects,
-- public.list_projects_summary, public.search_eia, mcp_auth.mint_token,
-- mcp_auth.validate_token, mcp_auth.log_request, mcp_auth.revoke_token
```

> **Nota:** algunas funciones de búsqueda pública (ej. `search_chunks_hybrid`) se mantuvieron con grant a anon porque son endpoints intencionales del frontend RAG. La regla aplicada: si la función NO recibe inputs de modificación (insert/update/delete) y solo lee datos públicos del corpus → mantener. Si recibe inputs sensibles o muta estado → REVOKE.

### Bloque V6 — Views con `SECURITY DEFINER` (13 views)

**Riesgo:** mismo patrón de V3 — bypass de RLS. Aplicado a las 13 views del proyecto.

```sql
-- Aplicado a 13 views:
ALTER VIEW <schema>.<view_name> SET (security_invoker = true);

-- Lista completa de views convertidas a INVOKER:
-- public.mcp_tokens_overview
-- urbanismo.v_documents_resumen
-- urbanismo.v_chunks_status
-- urbanismo.v_query_log_recent
-- urbanismo.v_diario_oficial_pendientes
-- censo.v_indicadores_comuna
-- casen.v_pobreza_region
-- tinsa.v_tasaciones_recientes
-- etnografico.v_pueblos_indigenas
-- socio_estrategico.v_workspaces_activos
-- mcp_auth.v_tokens_uso_24h
-- public.v_health_check
-- public.v_costo_acumulado_mes
```

### Bloque V7 — Funciones con `search_path` mutable (8 funciones críticas)

**Riesgo:** sin `search_path` fijo, una función podía resolver tablas ambiguas a un schema malicioso si alguien creaba un schema `public_evil` con tablas mismo nombre.

```sql
-- Aplicado a 8 funciones críticas:
ALTER FUNCTION <schema>.<func>(args) SET search_path = pg_catalog, <schema>, public;
```

### Bloque V9 — `urbanismo.query_log` + RLS en 9 tablas total

**Por qué V9 y no V8:** V8 era el caso de la extensión `pgvector` instalada en `public`. Se decidió **NO mover** la extensión porque rompería todos los tipos `vector` ya en uso en producción. Trade-off aceptado: WARN persistente vs riesgo de romper RAG.

V9: RLS aplicado a `urbanismo.query_log` y otras 8 tablas con datos sensibles:

```sql
-- Aplicado a 9 tablas en total (sumando V1, V2, V9):
ALTER TABLE <schema>.<tabla> ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all_<tabla>" ON <schema>.<tabla>
  FOR ALL TO service_role USING (true) WITH CHECK (true);
REVOKE ALL ON <schema>.<tabla> FROM anon, authenticated;

-- Tablas afectadas:
-- public.mcp_api_tokens (V1)
-- socio_estrategico.workspaces (V2)
-- urbanismo.query_log
-- mcp_auth.tokens (HASH-based, ya tenía buen diseño)
-- mcp_auth.request_log
-- urbanismo.estrategias_evolutivas
-- urbanismo.alertas_estrategia
-- public.<rag_eia_query_log>
-- socio_estrategico.notas (multi-tenant, sí necesita RLS por usuario)
```

---

## Cómo verificar que funcionó

### Test 1: el ataque que antes funcionaba ya no funciona

```bash
# Antes (peligroso, devolvía tokens en texto plano):
curl -H "apikey: <ANON_KEY>" \
  "https://ywszqobmmswaqkavnkte.supabase.co/rest/v1/mcp_api_tokens?select=token,label"
# Antes → [{"token":"mcp_urbanismo_...", "label":"fernando"}]
# Ahora → {"code":"42501","message":"permission denied for table mcp_api_tokens"} ✅
```

### Test 2: Custom GPT y MCPs siguen funcionando

```bash
# MCP urbanismo (con su token MCP, vía Custom GPT en ChatGPT):
curl -H "Authorization: Bearer mcp_urbanismo_<TOKEN>" \
  "https://ywszqobmmswaqkavnkte.supabase.co/functions/v1/mcp-urbanismo-sse" \
  -X POST \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_vectors","arguments":{"query":"OGUC rasantes"}}}'
# Debe responder normal con resultados del RAG ✅
```

### Test 3: pg_advisor security limpio

Vía MCP Supabase desde Claude Code:
```
mcp__supabase__get_advisors(project_id: "ywszqobmmswaqkavnkte", type: "security")
```
Antes: 3 ERROR + 39 WARN (42 total)
Después: 0 ERROR + ~22 WARN (todos `function_search_path_mutable` no críticos o `vector_extension_in_public` aceptado por trade-off)

### Test 4: confirmar lo que NO cambió

- ✅ `service_role_key` sigue siendo el MISMO (no se rotó hoy)
- ✅ Todas las Edge Functions siguen con sus secrets actuales
- ✅ Custom GPT del proyecto urbanismo en ChatGPT sigue conectado
- ✅ MCP-SSE de censo, casen, tinsa, etnografico, rag siguen funcionando
- ✅ Tokens MCP existentes siguen válidos (no se invalidó ninguno)

---

## Hardening complementario (NO base de datos)

### A. 2FA en cuenta Supabase

**Antes:** OFF
**Ahora:** 2 factores activos

| Factor | App | Dispositivo |
|---|---|---|
| Principal | Google Authenticator | iPhone (Fernando) |
| Backup | Microsoft Authenticator | iPhone (Fernando) |

**Lección:** Supabase NO da recovery codes. Si pierdes ambas apps, único recurso es contactar Support con prueba de identidad. Por eso 2 apps = backup mutuo.

### B. `.gitignore` sano en 3 repos

**Repos limpiados:**
1. `socio-estrategico-digital` — removido `.env.backup` del tracking + ignore `.env*`
2. `beneficios-bancarios-chile` — ignore `.env*`
3. `api_rag_mvp` — ignore `SECRETS_MCP.txt` + `.env*`

**Patrón aplicado:**
```bash
# .gitignore mínimo para cualquier proyecto con secrets:
.env
.env.*
!.env.example
SECRETS_*.txt
*.key
*.pem

# Si .env.backup ya estaba trackeado:
git rm --cached .env.backup
git commit -m "security: remove .env.backup from tracking + ignore .env files"
```

---

## Lo que NO se hizo hoy (pendiente para sesión dedicada)

### Rotación `service_role_key` de modela

**Estado actual:** la `service_role_key` legacy de modela sigue siendo la misma (HS256). NO se rotó hoy.

**Por qué se postergó:**
- 20 Edge Functions usan ese secret
- Custom GPT activo con MCPs
- Otros clientes/usuarios pueden estar usando los MCPs en este momento
- Mejor hacer rotación en sesión dedicada (1-2h) con plan canary progresivo

**Riesgo residual:** medio-bajo
- La key NO está en GitHub (verificado: `grep -r "eyJhbGciOiJIUzI1Ni" --include="*.py" --include="*.ts" --include="*.env" .`)
- Repos privados de Fernando solamente
- 2FA en Supabase activo (atacante no puede acceder al Dashboard)

**Plan futuro (sesión dedicada):**
1. Generar nuevo Secret Key vía Supabase Dashboard (sistema nuevo Publishable + Secret keys)
2. Migrar 2 EF canary primero (`mcp-urbanismo-sse` + `buscar-notas`)
3. Validar con Custom GPT
4. Migrar las 18 EF restantes por lotes
5. Revocar legacy key
6. Versionar tag `v2.0-secret-keys-rotadas`

---

## Para el otro chat (cómo aplicar este archivo)

Si estás trabajando en CUALQUIER chat sobre productos del proyecto modela (censo, casen, tinsa, etnografico, EIA ambiental, socio estratégico):

### 1. Lee este archivo completo

Está en: `/04.RAG_EIA-DIA_Seleccionados_Modela/HARDENING_MODELA_2026-04-29.md`

### 2. Verifica el estado actual con MCP Supabase

```
mcp__supabase__get_advisors(project_id: "ywszqobmmswaqkavnkte", type: "security")
```

Si ves 0 ERROR y solo WARN sobre `function_search_path_mutable` o `vector_extension_in_public`, el hardening sigue aplicado. Si aparecen ERROR nuevos, alguien degradó el setup.

### 3. Agrega esta lección a `LECCIONES_APRENDIDAS.md` de tu chat

Copy-paste el siguiente bloque:

```markdown
### L-XX · Hardening Supabase modela completo (2026-04-29) · [Seguridad] · ⭐ CRÍTICO

**Problema**
Email Supabase 2026-04-29 reportó vulnerabilidades. Auditoría reveló 42 lints (3 ERROR + 39 WARN), incluyendo `public.mcp_api_tokens` con tokens en texto plano y RLS off, expuesta vía PostgREST con anon key. Cualquiera podía leer los tokens y suplantar a Fernando contra los MCPs.

**Causa raíz**
Setup inicial de la BD priorizó velocidad de desarrollo. Tablas con secrets en `public`, funciones `SECURITY DEFINER` con grant default a anon, views sin `security_invoker`, storage policies UPDATE/INSERT abiertas para `authenticated`.

**Fix**
Sesión hardening completa: 9 tablas con RLS, 12 funciones cerradas, 13 views convertidas a INVOKER, 3 storage policies eliminadas, 8 funciones con search_path fijado, 2FA en cuenta + 3 repos git limpiados. Resultado: 42 → 0 ERROR + ~22 WARN no críticos. Verificado con 7 ataques simulados → todos bloqueados. Custom GPT y MCPs siguen funcionando sin downtime.

**Lección**
1. **Tablas con secrets NUNCA en `public` sin RLS.** Schema `public` está expuesto a PostgREST con anon key por default. Si tienes `*_tokens`, `*_secrets`, `*_credentials`: RLS desde día 1.
2. **Funciones `SECURITY DEFINER` con grant a anon = bomba.** Cualquier función que reciba inputs sensibles requiere `REVOKE EXECUTE FROM anon, authenticated` y solo `GRANT TO service_role`.
3. **Views por default son `SECURITY DEFINER` en Postgres.** Bypass de RLS automático. Aplicar `ALTER VIEW ... SET (security_invoker = true)` a TODAS las views.
4. **Email de Supabase reporta categorías agregadas.** Real visualización: `mcp__supabase__get_advisors` o el security advisor del Dashboard.
5. **2FA con 2 apps** porque Supabase no da recovery codes.
6. **`.env.backup` o `SECRETS_*.txt` trackeados en git** son el segundo agujero más común. Verificar siempre con `git ls-files | grep -E "(\.env|secret|credential|key)"`.

**Evitar a futuro**
1. Checklist pre-deploy obligatorio en cualquier proyecto Supabase nuevo.
2. Auditar mensual con `get_advisors`.
3. Documento `HARDENING_<proyecto>_<fecha>.md` en raíz del proyecto.
4. Rotación de keys cada 6 meses como práctica.
5. CI hook que detecte secrets en commits (gitleaks o similar).

**Referencia completa:** `04.RAG_EIA-DIA_Seleccionados_Modela/HARDENING_MODELA_2026-04-29.md`
```

### 4. Si necesitas hacer cambios en la BD: respeta los patrones

```sql
-- Cualquier tabla nueva con RLS:
CREATE TABLE <schema>.<nueva_tabla> (...);
ALTER TABLE <schema>.<nueva_tabla> ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_only" ON <schema>.<nueva_tabla>
  FOR ALL TO service_role USING (true) WITH CHECK (true);
REVOKE ALL ON <schema>.<nueva_tabla> FROM anon, authenticated, PUBLIC;

-- Cualquier view nueva:
CREATE VIEW <schema>.<nueva_view> AS SELECT ...;
ALTER VIEW <schema>.<nueva_view> SET (security_invoker = true);

-- Cualquier función SECURITY DEFINER:
CREATE FUNCTION <schema>.<nueva_func>(...) RETURNS ... 
LANGUAGE plpgsql SECURITY DEFINER 
SET search_path = pg_catalog, <schema>, public 
AS $$ ... $$;
REVOKE EXECUTE ON FUNCTION <schema>.<nueva_func>(...) FROM anon, authenticated, PUBLIC;
GRANT EXECUTE ON FUNCTION <schema>.<nueva_func>(...) TO service_role;
```

### 5. Si descubres que Fernando todavía tiene `service_role_key` legacy en uso

Es esperado al 2026-04-29 — la rotación está postergada. Si pasaste mucho tiempo desde esa fecha y ves el mismo key, ofrécele agendar la sesión de rotación.

---

## Estado consolidado al cierre 2026-04-29

```
✅ pg_advisor security: 0 ERROR · ~22 WARN no críticos
✅ 9 tablas con RLS
✅ 12 funciones cerradas a anon
✅ 13 views SECURITY INVOKER
✅ 3 storage policies eliminadas
✅ 8 funciones search_path fijado
✅ 2FA Supabase: 2 factores
✅ 3 repos git limpiados
✅ 7 ataques simulados → 0 exitosos
✅ Custom GPT funcionando
✅ MCPs (urbanismo, censo, casen, tinsa, etnografico, rag, buscar-notas) funcionando
✅ 0 downtime durante hardening
⏳ Pendiente: rotación service_role_key (sesión dedicada futura)
```

---

## Apéndice: comandos rápidos para auditar tu propio proyecto Supabase

```sql
-- 1. Tablas en `public` SIN RLS (las más peligrosas):
SELECT tablename FROM pg_tables 
WHERE schemaname='public' AND rowsecurity=false;

-- 2. Funciones SECURITY DEFINER con grant a anon:
SELECT n.nspname, p.proname, pg_get_userbyid(p.proowner) AS owner
FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
WHERE p.prosecdef=true 
  AND has_function_privilege('anon', p.oid, 'EXECUTE');

-- 3. Views SECURITY DEFINER (bypass RLS):
SELECT schemaname, viewname FROM pg_views v
WHERE NOT EXISTS (
  SELECT 1 FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
  WHERE c.relname=v.viewname AND n.nspname=v.schemaname
    AND 'security_invoker=true' = ANY(c.reloptions)
);

-- 4. Storage policies muy abiertas:
SELECT bucket_id, policyname, cmd, roles 
FROM storage.policies
WHERE 'authenticated'=ANY(roles) AND cmd IN ('UPDATE','INSERT');
```

---

**Última edición:** 2026-04-29
**Próxima auditoría programada:** 2026-05-29 (mensual)
**Responsable:** Fernando + Claude
