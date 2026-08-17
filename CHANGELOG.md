# Registro de cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
Este proyecto usa [versionado semántico](https://semver.org/lang/es/).

---

## [2.8.0] — 2026-08-18

### Cambio de política de datos — léelo antes de actualizar

Hasta la versión 2.7.0 incluida, **Quilate no enviaba ningún dato de tu sistema
a ninguna parte**. Esa promesa estaba escrita en la documentación y en el propio
código, y era cierta.

**Desde la 2.8.0 deja de serlo.** Quilate envía, al terminar cada análisis, un
resumen técnico reducido del equipo y de las puntuaciones obtenidas.

**Qué se envía**: versión del sistema operativo, modelo de CPU y GPU, cantidad y
velocidad de RAM, tipo de disco del sistema, las puntuaciones del benchmark, el
tiempo de arranque, la temperatura máxima de CPU, dos indicadores de calidad de
la medida, y los identificadores (no los textos) de las comprobaciones que
dieron hallazgo. Se acompaña de un identificador aleatorio de instalación que se
regenera cada 90 días.

**Qué NO se envía**: rutas, nombres de fichero, nombre de equipo o de usuario,
SSID/BSSID/MAC, números de serie, identificadores únicos de hardware, listado de
software instalado, ni ningún texto libre. La dirección IP no se envía y el
servidor que recibe los datos no la registra.

**Por qué**: para poder comparar tu equipo con otros equipos del mismo modelo de
procesador. Esa comparación no se puede construir sin datos de muchos equipos.

**Cómo desactivarlo**: no se puede desde el programa. **`--no-net` no lo
desactiva**: esa bandera corta las sondas de latencia y DNS y la comprobación de
versión, pero no este envío. Hasta la 2.7.0 significaba «no sale nada del
equipo» y desde la 2.8.0 ya no significa eso.

**Si no te parece bien**: la versión 2.7.0 sigue disponible en las releases,
seguirá funcionando y no envía nada. También puedes bloquear el destino en tu
cortafuegos, o compilar tu propia versión sin el envío — la licencia MIT te da
ese derecho y este cambio no lo toca.

Detalle completo en [PRIVACY.md](PRIVACY.md).

### Añadido
- Envío de resumen técnico de la ejecución. Ver arriba.
- Identificador de instalación aleatorio y rotatorio (90 días), consultable y
  borrable por el usuario.
- `--mi-id`, que lo muestra y dice dónde está el fichero que lo guarda. Es lo
  que hay que indicar para pedir el borrado de tus datos.
- Aviso de una sola vez en la primera ejecución tras actualizar, mostrado
  **antes** de que se envíe ningún dato: esa ejecución todavía no envía nada.
- `PRIVACY.md` con la lista cerrada de datos, y enlace desde el README.
- Aviso de versión nueva al final del informe, consultando las releases de
  GitHub. Es una petición GET sin cuerpo y sin parámetros: **no se envía la
  versión instalada**, la comparación se hace en local. La respuesta se guarda
  un día, y `--no-net` corta la consulta.

### Cambiado
- **`--no-net` ha cambiado de significado.** Sigue cortando las sondas de
  latencia y DNS y la comprobación de versión, pero ya no quiere decir «no sale
  nada del equipo». Se ha actualizado su texto de ayuda para que lo diga de
  forma explícita, en vez de dejar que se deduzca.
- La documentación de privacidad del README refleja la política nueva, y el
  aviso de la portada dice qué cambia respecto a la 2.7.0.

### Sin cambios
- Quilate **sigue sin modificar el sistema**. Solo lee.
- El **histórico sigue siendo local**. `historico.jsonl` no se envía, y
  `--no-history` no tiene nada que ver con el envío del resumen.
- El **informe completo sigue sin salir del equipo**: ni el JSON, ni el HTML,
  ni el plan de PowerShell.
- **Sigue sin recogerse** el SSID, el BSSID ni la MAC de tu red, ni ningún
  número de serie de hardware. Eso no ha cambiado y no va a cambiar.

---

## [2.7.0] y anteriores

Sin registro de cambios formal. El histórico está en las
[releases de GitHub](https://github.com/CrIsTiAnPvP/Quilate-Benchmark/releases)
y en el registro de commits.

**Para dejarlo dicho, porque importa**: todas las versiones hasta la 2.7.0
incluida **no envían ningún dato del sistema del usuario**. Si prefieres ese
comportamiento, esas versiones siguen publicadas y siguen siendo válidas.
