# Panda Bear Agent — People Operations (Koronet)

> **Regla base: Aprobar = crear borradores, nunca enviar.**
> El agente nunca envía ni publica nada por sí solo. Junior revisa cada borrador y le da Enviar.
> Lee [DECISIONS.md](DECISIONS.md) antes de tocar el flujo de aprobación, y corre
> `python tests/verify_approval_flow.py` antes de dar cualquier cambio por terminado.

## Estructura del dashboard

El menú tiene una sola pestaña por función, en este orden:

| Pestaña | Para qué sirve |
|---|---|
| 👤 **Próximos ingresos** | Página de inicio. Ingresos de los próximos 60 días según BambooHR, con ▶ Run Sync (corre el sync ya), 🔄 Recargar (solo vuelve a leer la lista) y 🔍 Dry Run. |
| 📨 **Mensajes programados** | Bandeja: solo lo que espera tu acción — mensajes por revisar, y los aprobados cuyo borrador se está creando o **falló** (con el motivo y 🔁 Reintentar). Aquí se aprueba. El número junto al nombre es cuántos hay. |
| 📅 **Agendamientos programados** | Bandeja: solo las sesiones que esperan tu acción — propuestas sin agendar y las que **fallaron** al agendarse (con el motivo, para reintentar). No se mezclan con los mensajes. |
| ✅ **Histórico de aprobaciones** | Lo ya resuelto, en **una ficha por persona**: mensajes con su borrador creado (y fecha de aprobación), sesiones agendadas (fecha y hora), y lo rechazado u omitido con ese estado. Lo más reciente arriba. Buscador por nombre y filtros por tipo (mensaje / sesión) y por estado. Aquí no se aprueba nada; solo "✓ Ya lo envié" y 🔁 Reagendar una sesión. |
| 💬 **Chats internos** | Tu chat privado con Panda Bear (modos RR.HH. y Personal). Nada de lo que escribes ahí se le envía a nadie. |
| ⚡ **Historial de ejecuciones** | Sección "Sistema". Cada sync (🤖 automático / 👤 manual, con su error si falló) y, plegado, el rastro técnico de cada mensaje. |
| 🚨 **Incidents** | Sección "Sistema". Errores de los servicios del agente. |

Se quitaron Morning Brief, Todos los mensajes, Identity, Memory, Decisions, Services, Ops Center y Prod Audit (y sus
rutas de la API). Los datos siguen en la base de datos y el agente sigue usando su memoria y sus
decisiones internamente; solo dejaron de mostrarse.

### Bandejas e histórico: la regla

Las bandejas (Mensajes programados y Agendamientos programados) **solo muestran lo pendiente de tu acción**.

| Haces… | Resultado | Dónde queda |
|---|---|---|
| ✅ Aprobar un mensaje | se crea el borrador (nunca se envía) | sale de la bandeja → Histórico, "✅ Aprobado" + estado del borrador |
| 📅 Agendar una sesión | queda en tu calendario | sale de Agendamientos al instante → Histórico, "✅ Agendada" + fecha y hora |
| ❌ Rechazar / ⏭ Omitir | no se crea nada | sale de la bandeja → Histórico, "❌ Rechazado" / "⏭ Omitido(a)" |
| …y **falla** el borrador o el agendamiento | no se creó | **se queda en su bandeja** como "✗ Falló" con el motivo, para reintentar. No entra al Histórico. |

- Un borrador de Slack tarda ~1 min en crearse: mientras tanto el mensaje sigue en la bandeja como
  "⟳ creando borrador…" y pasa al Histórico cuando existe.
- Excepción: un fallo de un ingreso que **ya empezó** ya no se puede arreglar; pasa al Histórico como
  "⚠️ Aprobado, sin borrador · el ingreso ya pasó" (p. ej. dos bienvenidas de julio de la versión anterior).
- Una sesión ya agendada vuelve a Agendamientos solo mientras falte agregar a un ingreso del mismo día
  (➕ Agregar), o si la abres con 🔁 Reagendar desde el Histórico.
- Las sesiones que ya estaban en tu calendario (p. ej. la de HR que agendó Moira) salen en el
  Histórico como "🔗 Ya existía".
- Nada se borra de la base de datos: la bandeja o el Histórico dependen solo del estado de cada ítem.

Debajo del menú se ve siempre la fecha y hora del **último sync exitoso** y el próximo sync automático.

## Mensajes programados (aprobación)

Cada ingreso trae 3 mensajes y **cada uno se aprueba por separado**, con sus propios botones
(✅ Aprobar · ✏️ Editar · ⏭ Omitir · ❌ Rechazar):

| Mensaje | Al aprobar se crea… |
|---|---|
| 💬 Mensaje al líder | borrador en el DM de Slack con el líder |
| 🎉 Bienvenida en #koronet | borrador en el canal #koronet |
| 📧 Correo de bienvenida | borrador en Gmail (cuenta de Junior) |

- **Aprobar uno** crea solo ese borrador; ese mensaje sale de Mensajes programados y pasa al
  **Histórico de aprobaciones**. Los otros siguen pendientes.
- **✅ Aprobar todos (N)** aprueba de una vez los que sigan pendientes.
- En el Histórico cada mensaje muestra su borrador: 📝 creado · 📤 enviado por ti. "✓ Ya lo envié"
  lo marca solo Junior, después de enviarlo él mismo.
- Esto es automático en todos los ingresos: no hay que pedir que se cree el borrador.
- Arriba de cada ingreso se avisa si su fecha de inicio cae en fin de semana o en feriado
  (Colombia y/o su país), para negociar la fecha con tiempo.

**Aprobar = crear borrador. Nunca enviar.** Ningún botón del dashboard envía ni publica nada.

El dashboard se sirve con `Cache-Control: no-store`. Si alguna vez ves la interfaz vieja, fuerza
la recarga con **Ctrl + Shift + R** (o Ctrl + F5). La versión actual muestra la etiqueta
"v4.9 · SIN MORNING BRIEF" debajo del logo.

## Borradores vigilados (chequeo previo)

Para que **cada aprobación cree su borrador de verdad**, Panda Bear comprueba, al arrancar y antes de
cada sync automático (8:00, 12:00, 16:00), que todo lo necesario funciona — sin crear nada:

| Chequeo | Para qué | Si falla |
|---|---|---|
| Gmail | correo de bienvenida | corre `python setup_google_auth.py` |
| Bot de Slack | ubicar al líder y #koronet | revisa `SLACK_BOT_TOKEN` en `.env` |
| Claude + conector de Slack | borradores de Slack (DM al líder, #koronet) | abre Claude Code en este PC: sesión iniciada y conector "claude.ai Slack" conectado |

- Debajo del menú se ve **✍️ Borradores: ✅ listos** (o qué falla). Clic ahí = comprobar ahora.
- Si algo falla, Mensajes programados muestra un aviso rojo arriba **antes de que apruebes**, y el bot
  te escribe por DM (una vez, y otra cuando se arregla).
- El chequeo de Claude solo busca la herramienta de borradores (modelo haiku, ~4 s, < US$0.01): nunca
  crea, envía ni programa nada.
- Si aun así un borrador falla al aprobar, se queda en su bandeja con el motivo y 🔁 Reintentar
  (los de Slack se reintentan solos 3 veces; los fallidos se reintentan cada hora).

## Sync automático

Panda Bear corre el sync solo **3 veces al día: 8:00, 12:00 y 16:00 (hora Colombia)**.

- El sync **solo detecta ingresos en BambooHR y prepara sus mensajes en Mensajes programados**.
  Nunca envía nada ni crea borradores: eso solo pasa cuando tú apruebas.
- Si el agente estaba apagado a la hora de un sync, lo hace apenas vuelve a arrancar.
- **Si un sync automático falla, el bot Panda Bear te avisa por DM en Slack con el error.**
  Si BambooHR no devuelve a ningún empleado (API caída, sin red, API key inválida), eso cuenta
  como fallo y no como un sync "exitoso" con 0 ingresos.
- En **Historial de ejecuciones** se ve si cada sync fue 🤖 Automático o 👤 Manual (▶ Run Sync), y el error si falló.
  Los runs anteriores a este cambio aparecen con "—".

| Variable | Default | |
|---|---|---|
| `SYNC_TIMES` | `08:00,12:00,16:00` | horas del sync, separadas por coma |
| `SYNC_TIMEZONE` | `America/Bogota` | zona horaria de esas horas |
| `SYNC_LOOKBACK_DAYS` | `60` | |

## Modelo de IA (Groq)

El chat, "mejorar mensaje con IA" y las notas de reunión usan Groq con el modelo de `GROQ_MODEL`
(por defecto `openai/gpt-oss-120b`). El modelo anterior, `llama-3.3-70b-versatile`, fue retirado
por Groq; si algún día el chat vuelve a responder con error, revisa primero que el modelo siga existiendo.

## Agendamientos (pestaña 📅 Agendamientos)

Para cada ingreso próximo Panda Bear propone sus sesiones de onboarding. **Nada llega al calendario
hasta que le das 📅 Agendar** (o ➕ Agregar, en Product): ese clic es la aprobación.

| Sesión | Propuesta | Con |
|---|---|---|
| 🚀 HR — "On - Boarding HR Koronet 🚀", 60 min | tal cual el correo de bienvenida (fecha de ingreso + hora) | Barra: **Ambas** · Solo Guadalupe · Solo Moira · Otros |
| 💻 IT — "On - Boarding IT Koronet 💻", 30 min | 10:00 CO, primer día hábil en Colombia desde la sesión de HR | Jeisson (editable) |
| 📊 Payroll — "On - Boarding Payroll Koronet - 📊", 30 min | **solo ingresos de Colombia**; 11:00 CO, misma regla que IT | Dani (editable) |
| 🧩 Product — "Monthly Product Onboarding" de Adrian | la ocurrencia más cercana desde el ingreso; se agrega **solo a esa**, nunca a la serie | — |
| ✨ First month — "{Nombre} / Guada - First month ✨", 30 min | horarios libres de Guada y tuyos el día 30 (o 29/28 si el 30 no sirve), lun–vie sin feriado | Guadalupe (editable) |

- **Varios ingresos el mismo día comparten sesiones:** una sola de HR para los hispanohablantes
  (contigo) y, si hay no hispanohablantes, otra de HR para ellos con Moira; una sola de IT (solo
  hispanohablantes — a los demás Jeisson los copia en Slack); una sola de Payroll (solo los de Colombia);
  todos a la misma Product; First month va por persona. Si alguien se suma a un día que ya estaba
  agendado, aparece "Falta agregar a…" y con **➕ Agregar** entra al MISMO evento (no se crea otro).
- **Horarios libres que siguen a la fecha (First month, IT, Payroll):** al cambiar la fecha, la duración
  o el responsable (también en Reagendar) se recalculan los horarios en que el responsable y tú están
  libres: ese día si hay espacio; si no, dice por qué (fin de semana, feriado, ausencia en BambooHR,
  antes de HR, sin espacio en común) y muestra los siguientes días hábiles. "Ver más días →" sigue
  buscando. IT/Payroll nunca proponen antes de que termine HR. Buscar horarios nunca agenda nada.
- **Alertas en cada sesión:** feriados (Colombia, país del ingreso y de los responsables — todos los
  países, con la librería `holidays`), ausencias en BambooHR, y si los responsables o tú ya tienen algo a
  esa hora. Si la sesión de HR cae en feriado en Colombia, avisa que la da Moira.
- **Sesiones que ya existen** en tu calendario (p. ej. Moira agendó la de HR) se vinculan en vez de
  proponerse otra vez: no se duplican.
- **🔁 Reagendar:** mueve la sesión ya agendada (o pasa al ingreso a otra fecha de Product). Si la mueves
  directo en Google Calendar, Panda Bear se entera solo.
- **Responsable / Otros:** buscas por nombre en el directorio de BambooHR y queda el correo de trabajo.
- El correo del ingreso sale del sheet **NEW HIRES- KORONET** (columna "Mail Koronet"). Leerlo requiere
  la Google Sheets API habilitada en el proyecto de Google Cloud y el permiso `spreadsheets.readonly`.

## Arranque

El agente corre en el PC de Junior (no en Render: los borradores de Slack necesitan la sesión de
Claude Code de este PC). Arranca solo al iniciar Windows y con el ícono "Iniciar Panda Bear Agent"
del escritorio. Ambos lanzan `start_agent_hidden.vbs` → `run_agent_loop.bat`, un watchdog que
**reinicia el agente a los 5 segundos si se cae** y anota cada reinicio en
`panda_bear_startup.log` (líneas `[watchdog]`). Si el agente ya está corriendo, el ícono no abre
una segunda copia.

**Revisión de salud cada 5 minutos** (`healthcheck.ps1`, desde el Programador de tareas de Windows,
tarea "Panda Bear Healthcheck"): si `/health` no responde 3 veces seguidas (agente colgado) lo
reinicia, y si el watchdog no está corriendo lo vuelve a lanzar. Deja registro en `healthcheck.log`.
Para crear la tarea (una sola vez):
`schtasks /Create /TN "Panda Bear Healthcheck" /TR "wscript.exe \"C:\PandaBearAgent\healthcheck.vbs\"" /SC MINUTE /MO 5 /F`

⚠️ Si el PC se suspende, el dashboard no responde hasta que despierta (los syncs perdidos se hacen al
despertar). Para que no pase, el PC debe estar enchufado y sin suspensión.

Arranque manual (para desarrollo): `python main.py` y luego abre el dashboard en http://localhost:8000/.
Variables de entorno: ver `.env.example`.
