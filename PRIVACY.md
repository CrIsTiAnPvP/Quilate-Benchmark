# Privacidad en Quilate

Última actualización: 18 de agosto de 2026 · Aplica desde: **Quilate 2.8.0**

---

## Resumen en cinco líneas

- Quilate **no modifica tu sistema**. Solo lee.
- Tu **informe completo nunca sale de tu equipo**. Ni el JSON, ni el HTML, ni el
  plan de PowerShell, ni el histórico.
- Desde la versión 2.8.0, Quilate envía un **resumen técnico reducido** de cada
  ejecución para poder comparar equipos entre sí. La lista completa está abajo.
- **Ese envío no se puede desactivar con ninguna opción del programa**, tampoco
  con `--no-net`. Lo que sí puedes hacer está en «Cómo evitarlo».
- No se envía nada que identifique tu equipo, tus ficheros, tu red ni a ti.

---

## Qué cambió en la versión 2.8.0

Hasta la versión 2.7.0, Quilate no enviaba absolutamente ningún dato de tu
sistema. Desde la 2.8.0 sí envía el resumen que se detalla más abajo.

**Por qué**: para poder decirte si tu equipo rinde por encima o por debajo de
otros equipos con el mismo procesador. Esa comparación no se puede hacer sin
datos de muchos equipos.

Está dicho aquí sin rodeos porque es un cambio de política y las versiones
anteriores prometían lo contrario.

---

## Qué se envía exactamente

Solo esto:

**Sobre la ejecución**
- Versión de Quilate y del esquema de datos
- Si fue una ejecución rápida (`--quick`) o completa
- Un identificador aleatorio de instalación (ver abajo)

**Sobre el equipo**
- Versión del sistema operativo
- Modelo de procesador y de tarjeta gráfica
- Cantidad de RAM, su velocidad y si va en uno o varios canales
- Tipo de disco del sistema (NVMe, SSD SATA o mecánico)

**Sobre las medidas**
- Puntuaciones del benchmark, por componente y total
- Tiempo de arranque en segundos
- Temperatura máxima alcanzada por la CPU
- Dos indicadores de calidad de la medida (dispersión y carga del equipo
  durante la prueba)
- Los identificadores de las comprobaciones que dieron hallazgo — por ejemplo
  `power_plan` o `ram_slow`. **Solo el identificador, nunca el texto**, porque
  el texto lleva datos de tu equipo dentro.

Y nada más.

## Qué NO se envía, nunca

- Rutas ni nombres de fichero, de ningún tipo
- El resultado del rastreo de archivos grandes
- Nombre de tu equipo, de tu usuario o de tu dominio
- Nombre de tu red wifi (SSID), punto de acceso (BSSID) ni direcciones MAC —
  Quilate ni siquiera los recoge
- Números de serie de discos ni ningún identificador único de tu hardware
- Qué programas tienes instalados o en el arranque
- Tu dirección IP: no se envía, y el servidor que recibe los datos **no la
  registra**
- Texto libre de ninguna clase

## El identificador de instalación

Es un número aleatorio que se genera en tu equipo la primera vez y **se cambia
solo cada 90 días**.

- No se calcula a partir de tu hardware. Dos equipos idénticos tienen
  identificadores distintos, y copiar Quilate a otro equipo no lo arrastra.
- Sirve solo para no contar cincuenta veces el mismo equipo cuando ejecutas
  Quilate cincuenta veces. Sin eso las medias no valdrían nada.
- Puedes verlo, borrarlo o cambiarlo cuando quieras: está en un fichero de texto
  en `%LOCALAPPDATA%\Quilate` (Windows) o `~/.local/share/Quilate` (Linux).

**Una precisión honesta**: como este identificador existe, estos datos son
*seudonimizados*, no *anónimos*. Es una diferencia real y por eso no vas a leer
en ningún sitio de este proyecto que la telemetría sea "anónima".

## Cómo evitarlo

**No hay una opción para desactivarlo.** Conviene que quede dicho así, sin
adornos, porque es lo primero que se busca en una página como esta.

En concreto, **`--no-net` no lo desactiva**. Esa bandera corta las sondas de
latencia y DNS y la comprobación de versión, pero no el envío de este resumen.
Hasta la 2.7.0 sí significaba «no sale nada del equipo»; desde la 2.8.0 ya no.

Lo que sí puedes hacer, en orden de facilidad:

1. **Quedarte en la versión 2.7.0.** Sigue publicada, sigue funcionando y no
   envía absolutamente nada. No se le va a retirar.
2. **Bloquear el destino** en tu cortafuegos. Es uno solo y es este:

   ```text
   telemetria.cristianac.es
   ```

   No hay ningún otro, no se reintenta y no se busca una vía alternativa: si no
   llega, no llega, y Quilate no te lo va a decir ni lo va a guardar para
   mandarlo luego. Si algún día cambiara, se anunciaría en el CHANGELOG y en
   esta página — bloquear este nombre no puede dejar de funcionar en silencio.
3. **Compilar tu propia versión sin el envío.** Quilate es software libre bajo
   licencia MIT: tienes derecho a modificarlo y a redistribuirlo, y quitar esta
   funcionalidad es cambiar unas pocas líneas. No es una escapatoria que se
   mencione a regañadientes — es lo que la licencia dice que puedes hacer, y
   sigue siendo verdad después de este cambio.

## Tus derechos

**Ver tus datos o pedir que se borren**: escribe a
[info@cristianac.es](mailto:info@cristianac.es) indicando tu identificador de
instalación. Lo tienes ejecutando `quilate --mi-id`. Sin él no hay forma de
localizar tus filas, porque no se guarda nada más que permita hacerlo — que es
la contrapartida de que aquí no haya ni tu nombre, ni tu correo, ni tu IP.

**Oponerte al tratamiento**: las tres vías de la sección anterior. No existe una
opción dentro del programa que lo detenga.

**Conservación**: las filas individuales se borran a los **12 meses**. Los datos
agregados que salen de ellas —medias por modelo de procesador, y poco más— no
llevan identificador y se conservan de forma indefinida.

Conviene entender cómo se combina esto con la rotación de 90 días del
identificador, porque juntos hacen más de lo que parece: pasados esos 90 días tus
filas nuevas ya no se pueden enlazar con las viejas, y pasados 12 meses las
viejas dejan de existir. El plazo se ha fijado en 12 y no en 24 meses justamente
por eso: con la rotación puesta, un año de filas individuales ya no aporta nada
que un año más pudiera mejorar.

## Lo que sigue siendo verdad

Aunque esto haya cambiado, no ha cambiado lo demás:

- Quilate **no modifica tu sistema**. Cuando encuentra algo arreglable, escribe
  un script de PowerShell que puedes leer entero antes de ejecutarlo.
- El **histórico se queda en tu equipo**, en un fichero de texto que puedes
  leer, copiar o borrar. Ese fichero no se envía.
- El **código es abierto**. Puedes comprobar cada afirmación de esta página en
  el repositorio, y esa es la razón de que aquí no haya nada exagerado.

---

*Responsable del tratamiento: Cristian Alonso · [info@cristianac.es](mailto:info@cristianac.es)*