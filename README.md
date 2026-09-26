# Bot de práctica del teórico B (prototipo)

30 preguntas **originales**, no copiadas del banco de examen de la DGT. No es un producto oficial ni sustituye la preparación reglada. Las referencias normativas de cada pregunta son una ayuda para revisar la respuesta; antes de usarlo a largo plazo, conviene revisar el banco cuando cambie la normativa.

## Qué hace

- `/start`: alta privada y activación de avisos diarios. `/baja`: desactiva los avisos.
- Envía tres preguntas cada día a las **09:00, hora peninsular** (modificable con `DAILY_HOUR`, `DAILY_MINUTE` y `DAILY_COUNT`, de 3 a 5). Solo puede escribir a quien primero pulse `/start`.
- `/diario`: hace la práctica diaria en ese momento; no envía un segundo lote el mismo día.
- `/examen`: 30 preguntas aleatorias, 30 minutos activos, aprobado con como máximo 3 errores. El cronómetro se congela con `/pausar` y continúa con `/seguir`; `/abandonar` cancela. `/repetir` ofrece los errores respondidos en el último test. En el examen, da las explicaciones al terminar, no durante la prueba. Al agotarse el plazo, las no respondidas cuentan como fallo.
- Estadísticas por tema al terminar, resumen de aciertos de los últimos siete días y racha diaria; prioriza temas fallados en el historial. Guarda progreso en SQLite.
- Las 30 preguntas incluyen una imagen local verificada: fotos reales para escenas de tráfico y señales normativas para señales. Algunas fotografías sirven de contexto y no muestran literalmente todos los detalles hipotéticos del enunciado; la regla se responde según el texto. Los créditos y licencias están en `CREDITOS.md`.

## Probarlo localmente

1. En Telegram, abre [@BotFather](https://t.me/BotFather), manda `/newbot`, elige nombre y nombre de usuario terminado en `bot`. BotFather devuelve el token. **No lo pegues en el chat ni lo subas a GitHub.**
2. Instala Python 3.10 o superior. Descarga o descomprime esta carpeta y, en la carpeta `dgt_bot`, ejecuta:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN='TOKEN_QUE_TE_DIO_BOTFATHER'
python bot.py
```

En Windows PowerShell: `py -m venv .venv`, `.venv\Scripts\Activate.ps1`, `pip install -r requirements.txt`, `$env:TELEGRAM_BOT_TOKEN='...'`, `python bot.py`.

3. Abre el enlace a tu bot en Telegram, pulsa **Iniciar** o manda `/start`. Prueba `/diario`, pulsa una respuesta y después `/examen`. Deja el proceso encendido para los avisos diarios.
4. Para que el familiar lo use, envíale el nombre de usuario del bot y pídele que pulse `/start` en privado. No hace falta su número ni añadirlo a ningún grupo. Si quieres probar sin molestar a nadie, empieza contigo mismo.

## Despliegue

Este bot usa *long polling*, no necesita dominio ni webhook, pero **el proceso debe estar siempre encendido** a las 09:00 y durante los exámenes. En un VPS Linux con Python, instala dependencias, configura `TELEGRAM_BOT_TOKEN` como variable de entorno y ejecuta `python bot.py` con un supervisor (por ejemplo `systemd`). Pon `DB_PATH` en una ruta persistente con permisos de escritura, haz copias de seguridad y ejecuta **una sola instancia**: el sondeo concurrente con el mismo token produce conflictos, y dos instancias con SQLite aislado perderían progreso.

Railway o Render también pueden ejecutar el proceso como *worker*, pero sus planes gratuitos, disponibilidad y almacenamiento persistente pueden cambiar; no prometemos un despliegue gratuito permanente. No uses el disco efímero de un servicio para guardar el progreso. Si el host suspende el worker, no enviará el diario y un examen puede verse interrumpido. Para uso continuado, usa un host siempre activo con volumen persistente y revisa costes y cuotas antes de desplegar.

## Añadir fotos reales

Guarda una foto real de una escena vial en `assets/` y escribe el nombre del archivo en `imagen` de la pregunta relevante en `questions.json`, por ejemplo `"imagen": "paso-peatones.jpg"`. La ruta es local y relativa a `assets`. No pongas URLs remotas sin descargarlas primero. Comprueba licencia, autor y que la foto representa la situación de la pregunta; anota su crédito en `CREDITOS.md`. Para una señal puedes usar una imagen legalmente reutilizable, no una foto inventada o una ilustración presentada como real. El código muestra la imagen antes de la pregunta. `visual: "foto"` indica necesidad de escena fotográfica; `visual: "senal"` indica placa/señal.

## Fuentes y créditos

- Normativa consolidada, Reglamento General de Circulación: https://www.boe.es/eli/es/rd/2003/11/21/1428/con/
- Práctica oficial de la DGT (para contraste, no se copian preguntas): https://www.dgt.es/nuestros-servicios/permisos-de-conducir/obtener-un-nuevo-permiso-de-conducir/requisitos-preparacion-y-presentacion-a-examen
- Telegram BotFather: https://core.telegram.org/bots/tutorial
- Biblioteca y programador de tareas: https://docs.python-telegram-bot.org/en/stable/telegram.ext.jobqueue.html
- Créditos de imágenes en `CREDITOS.md`.

## Render Free y despertador

Esta versión sirve como web service en Render: comando de construcción `pip install -r requirements.txt`, comando de arranque `sh start.sh`, plan **Free**; configura el token nuevo como variable secreta `TELEGRAM_BOT_TOKEN` en el dashboard, nunca en Git. `/health` devuelve `ok` para un monitor HTTP. Si usas un servicio de pings, configura una petición GET al URL público de Render con ruta `/health` cada 10 minutos (el monitor y su URL se comprueban después de crear el servicio). Render duerme servicios Free después de 15 minutos sin peticiones entrantes; el ping intenta evitarlo, pero puede fallar o quedar suspendido. **No garantiza los envíos de cada mañana.** Según su documentación, SQLite local pierde usuarios registrados, respuestas, rachas y estadísticas cuando el proceso duerme, reinicia o se redespliega. Un ping no da persistencia ante reinicios ni caídas del monitor. Render limita a 750 horas gratuitas por workspace al mes; el resto de límites también pueden suspender el servicio. Para una promesa de entrega diaria hace falta almacenamiento persistente y monitoreo de salud, o un host de otro tipo.

Fuente oficial de límites: https://render.com/docs/free

## Mantener el banco y publicar cambios

`questions.json` contiene una lista de 30 preguntas. Cada objeto lleva `id` único, `tema`, `pregunta`, `opciones` (tres textos), `correcta` (índice 0, 1 o 2), `explicacion`, `imagen` (archivo local en `assets/`), `visual` (`foto` o `senal`) y `fuente` (artículo o fuente). Para cambiar una pregunta, edita su objeto conservando el `id` y las tres opciones. Para añadir una foto, colócala en `assets/`, enlaza el nombre en `imagen` y añade autor/licencia/fuente en `CREDITOS.md`; verifica visualmente que de verdad corresponde al supuesto. Nunca subas fotos sin licencia ni claves. Ejecuta `python -m unittest -v test_bot.py`, comprueba que los 30 `imagen` apuntan a archivos existentes y prueba la pregunta en Telegram.

Si Render está conectado al repositorio Git, un commit en la rama desplegada inicia un nuevo deploy cuando auto-deploy está activado. Si se usó la opción de URL de repositorio público, revisa en Render si auto-deploy se ofrece o lanza un deploy manual después del push. Verifica `/health`, `/start` y `/diario` al terminar. El nuevo deploy borra el SQLite local en Render Free: las personas tendrán que volver a pulsar `/start` antes de los avisos diarios. No conviertas el token en archivo `.env` versionado; edita las variables de entorno en Render.
