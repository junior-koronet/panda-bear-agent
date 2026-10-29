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

## Arranque

Arranque local: `python main.py` y luego abre el dashboard en http://localhost:8000/.
Variables de entorno: ver `.env.example`.
