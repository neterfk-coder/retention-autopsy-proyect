# Guía rápida (español)

El README está en inglés porque los jueces lo van a leer. Esto es para vos.

## Qué hace

Une dos cosas que YouTube nunca junta:

- **La curva de retención** — cuánta gente seguía viendo en el segundo `t`.
- **La edición** — qué estabas haciendo en ese mismo segundo: cuánto llevaba la
  toma sin cortar, qué tan rápido cortabas, qué tan rápido hablabas, si sonaba
  un patrocinio.

Studio te muestra la caída pero no sabe qué hay en el video. Tu editor sabe qué
hay en el video pero no sabe de la caída. Nadie las cruza.

## Para probarlo ahora mismo

```bash
pip install -e .
python -m autopsy demo
```

Abrí `out/report.html`. Son datos sintéticos, marcados como tales en el reporte.

## Para el demo real (esto es lo que da los puntos bonus)

El orden importa. Hacé esto **el día anterior**, no una hora antes:

1. Google Cloud: proyecto nuevo, activá *YouTube Data API v3* y *YouTube Analytics
   API*, creá credenciales OAuth tipo **Desktop app**, descargá el JSON.
2. En la pantalla de consentimiento, agregá tu cuenta en **Test users**. Sin esto
   el OAuth falla y es el error que arruina demos a las 3 de la mañana.
3. `python -m autopsy auth --secrets client_secrets.json`
4. `python -m autopsy scan --secrets client_secrets.json --max-videos 40`
5. `python -m autopsy edit --video-id XXX --file video.mp4 --subtitles video.srt`
   sobre 3 o 4 videos.

En el demo en vivo corré solo `python -m autopsy report`. Tarda segundos y produce
el resultado delante de los jueces. Lo lento ya está cacheado.

El guion minuto a minuto está en `docs/DEMO_SCRIPT.md`.

**Antes de ensayar, leé tu propio reporte.** Los números del README salen del
generador sintético. Si recitás esos mientras tu reporte real está en pantalla,
es lo único que un juez va a cachar seguro. El guion usa `[corchetes]` justo por
eso: rellenalos con lo que dice *tu* `out/report.html`.

**Guardá una copia de `out/channel.json` fuera del repo.** Ahí vive cada `edit`
que corriste — horas de ffmpeg y transcripción. `make clean` y cualquier
`rm -rf out` se lo llevan puesto.

## Lo que tenés que poder defender

**"¿Esto no es solo correlación?"** Sí, y está escrito en el README. Son efectos
observacionales sobre tu propio catálogo. Excluye los confounds medibles
(patrocinios, intros, cierres) pero no afirma causalidad. Te dice dónde mirar.

**"¿Por qué no usar un LLM para detectar el patrocinio?"** Determinismo. Si un
juez vuelve a correrlo, obtiene exactamente el mismo segmento. Y funciona sin red.

**"¿Y si tengo 5 videos?"** Los hallazgos por video andan desde el primero. Los
patrones del canal necesitan bastante más, y el reporte lo dice en vez de
inventar un número.

**"Tus p-values no son significativos."** Correcto, y el reporte los etiqueta
`weak` en vez de redondear para arriba. Con pocos videos cortos no alcanza para
que un intervalo signifique algo. El estimador está validado contra efectos
plantados en el fixture — el límite acá es la muestra, no el método. Decilo vos
antes de que lo pregunten; queda mucho mejor que que te lo encuentren.

## Lo que falta y podés agregar si te sobra tiempo

Por orden de retorno para el esfuerzo:

1. **Exportar marcadores para el NLE.** Un `.edl` o CSV de marcadores que se
   importe a Premiere o Resolve y le ponga un marker rojo en cada cliff. Que el
   editor abra su timeline y vea los problemas ahí adentro es un cierre muy fuerte.
2. **Comparar contra tu propio mejor video** en vez del promedio del canal.
3. **Detección de patrocinio en español** — hoy los keywords son solo en inglés.
   Está todo en `autopsy/extract/transcript.py`, es agregar frases al diccionario.

No agregues nada de esto antes de tener el demo real funcionando de punta a punta.
