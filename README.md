# Panda Bear Agent — People Operations (Koronet)

> **Regla base: Aprobar = crear borradores, nunca enviar.**
> El agente nunca envía ni publica nada por sí solo. Junior revisa cada borrador y le da Enviar.
> Lee [DECISIONS.md](DECISIONS.md) antes de tocar el flujo de aprobación, y corre
> `python tests/verify_approval_flow.py` antes de dar cualquier cambio por terminado.

## Aprobación en el dashboard (pestaña Approvals)

Cada ingreso trae 3 mensajes y **cada uno se aprueba por separado**, con sus propios botones
(✅ Aprobar · ✏️ Editar · ⏭ Omitir · ❌ Rechazar):

| Mensaje | Al aprobar se crea… |
|---|---|
| 💬 Mensaje al líder | borrador en el DM de Slack con el líder |
| 🎉 Bienvenida en #koronet | borrador en el canal #koronet |
| 📧 Correo de bienvenida | borrador en Gmail (cuenta de Junior) |

- **Aprobar uno** crea solo ese borrador; los otros dos siguen pendientes.
- **✅ Aprobar todos (N)** aprueba de una vez los que sigan pendientes.
- Cada mensaje muestra su estado: ⏳ pendiente · 📝 borrador creado · ✗ falló (con el motivo) ·
  ⏭ omitido · ✗ rechazado. "✓ Ya lo envié" lo marca solo Junior, después de enviarlo él mismo.
- Esto es automático en todos los ingresos: no hay que pedir que se cree el borrador.

**Aprobar = crear borrador. Nunca enviar.** Ningún botón del dashboard envía ni publica nada.

El dashboard se sirve con `Cache-Control: no-store`. Si alguna vez ves la interfaz vieja, fuerza
la recarga con **Ctrl + Shift + R** (o Ctrl + F5). La versión actual muestra la etiqueta
"v4.1 · APROBACIÓN POR MENSAJE" debajo del logo.

## Agendamiento (pestaña 📅 Agendamiento)

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

**Approvals** muestra arriba de cada ingreso si su fecha de inicio cae en fin de semana o en feriado
(Colombia y/o su país), para negociar la fecha con tiempo, y el estado de todas sus sesiones.

## Arranque

Arranque local: `python main.py` y luego abre el dashboard en http://localhost:8000/.
Variables de entorno: ver `.env.example`.
