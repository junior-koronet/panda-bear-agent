# Panda Bear — Decisiones de diseño

Reglas que ningún cambio futuro puede romper. Si un cambio las contradice, el cambio está mal.

## 1. Aprobar = crear borradores, nunca enviar

- **Aprobar un mensaje crea su borrador. El agente nunca envía ni publica nada por sí solo.**
  Junior revisa cada borrador y le da Enviar a mano.
- Ningún código que se ejecute al aprobar puede llamar a `chat_postMessage`, `files_upload_v2`,
  `drafts().send`, `messages().send` ni `chat.scheduleMessage` para un mensaje de onboarding.
- El sync (automático 3 veces al día o ▶ Run Sync) solo detecta ingresos y deja sus mensajes en
  `pending_approval` (Mensajes programados). Nunca crea borradores ni envía.
- `sent` lo marca **solo Junior** ("✓ Ya lo envié" en el dashboard) y solo desde
  `draft_created` o `ready_for_slack_draft`. El agente nunca pone `sent`.

## 2. Una aprobación por mensaje

Cada ingreso tiene 3 mensajes independientes, cada uno con su propio estado en `messages.status`:

| Mensaje | Borrador | Destino |
|---|---|---|
| `manager_slack` | Slack DM | líder (`slackTarget` = user id) |
| `bienvenidas_slack` | Slack | #koronet (`CDTF8BJP4`) |
| `employee_email` | Gmail (cuenta de Junior) | empleado, CC `moira.gago@koronet.com` si no es hispanohablante |

Estados: `pending_approval` → (`approved`, transitorio) → `draft_created` | `ready_for_slack_draft` | `failed`.
Además, `skipped`, `rejected` y `sent` (este último solo lo marca Junior).

- Aprobar uno solo crea solo ese borrador; los otros quedan `pending_approval`.
- "Aprobar todos" aprueba los pendientes uno por uno, con el mismo código.
- Las transiciones son `UPDATE … WHERE status IN (…)`: un doble clic no crea dos borradores.
- `batches.status` es solo un resumen de sus 3 mensajes (`_refresh_batch_status`).

## 3. Un fallo nunca queda en silencio

- Las bandejas (Mensajes programados, Agendamientos) muestran solo lo pendiente de Junior. Lo aprobado
  con éxito, rechazado u omitido pasa al Histórico de aprobaciones. **Un borrador o agendamiento fallido
  se queda en su bandeja** con el motivo; nunca aparece en el Histórico como aprobado.

- Si crear un borrador falla, el mensaje queda en `failed` con el motivo en `deliveryError`.
  Ese motivo se ve en el dashboard, en la respuesta de la API y en el DM a Junior.
  Nunca queda en `approved` ni en `sent`.
- Un borrador de Gmail cuenta como creado solo después de releerlo de Gmail (`drafts.get`).
- Los `failed` se reintentan cada hora (máximo 24 veces) y con 🔁 en el dashboard. Los `approved`
  que se quedaron a medias por un reinicio se retoman a los 10 minutos.
- Cualquier excepción no controlada de la API se guarda con su traceback en `logs/errors.log`
  y el dashboard muestra el mensaje real, no solo "HTTP 500".

## 4. Borradores de Slack: se crean solos al aprobar

El bot de Panda Bear (`xoxb`) solo puede publicar, y publicar es enviar. Por eso nunca se usa para
mensajes de onboarding. El borrador lo pone Claude con el conector de Slack de Junior
(`slack_send_message_draft`): deja el texto en el chat correspondiente como borrador, igual que
si Junior lo escribiera y no le diera Enviar.

Al aprobar un mensaje de Slack:
1. El agente verifica el destino con el bot token (`slackTarget` = id del líder o `CDTF8BJP4`
   para #koronet). Si el token falla o no encuentra al líder, el mensaje queda en `failed`.
2. El mensaje queda en `ready_for_slack_draft` ("creando borrador…") y en ese mismo momento
   `services/slack_draft_worker.py` lanza Claude Code en segundo plano (`claude -p`). Claude solo
   tiene permitidas `ToolSearch` y `slack_send_message_draft`; las herramientas para enviar o
   programar están bloqueadas, así que es técnicamente incapaz de enviar.
3. Si el borrador se crea, el mensaje pasa a `draft_created` con su `draftId` y Junior recibe el
   DM de confirmación. Si falla 3 veces, pasa a `failed` con el motivo y Junior recibe un DM.
   Cada 5 minutos un barrido retoma cualquier mensaje aprobado que siga sin borrador (por ejemplo,
   después de un reinicio).

Requisitos: Claude Code instalado y con sesión iniciada en el PC del agente (`CLAUDE_BIN` en
`.env`) y el conector "claude.ai Slack" conectado. Esto **no funciona en Render**, porque allá no
hay sesión de Claude.

## 5. Recordatorios de aprobaciones pendientes

Viven en `services/hire_reminders.py`, son DMs del bot Panda Bear a `JUNIOR_SLACK_ID` y se
configuran por variables de entorno:

| Variable | Default | Regla |
|---|---|---|
| `REMINDER_FIRST_BUSINESS_DAYS` | 5 | primer recordatorio cuando faltan 5 días hábiles |
| `REMINDER_DAILY_HOUR` | 8 | uno diario desde las 8:00 |
| `REMINDER_URGENT_BUSINESS_DAYS` | 1 | texto urgente el día hábil anterior al ingreso |
| `REMINDER_TIMEZONE` | America/Bogota | |
| `REMINDER_WEEKENDS` | false | también sábados y domingos |
| `DASHBOARD_URL` | http://localhost:8000/ | enlace en el DM |

- Máximo uno por ingreso al día: lo garantiza `UNIQUE(batchId, reminderDate)` en `hire_reminders`.
- Se detienen cuando ya no queda ningún mensaje del ingreso en `pending_approval`.
- `GET/POST /cron/tick?key=$CRON_SECRET` despierta el servicio y corre el chequeo (sirve para hosts
  que se duermen, como Render free).

## 6. Verificación obligatoria

Antes de dar por terminado cualquier cambio al flujo de aprobación, corre:

```
python tests/verify_approval_flow.py
```

El script simula aprobar un mensaje, aprobar todos, un fallo forzado, el doble clic, un HTTP 500
y el disparo de recordatorios. Si alguna prueba falla, el cambio no está listo.
