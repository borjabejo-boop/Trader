---
name: trader-morning
description: >-
  Resumen diario antes de la apertura. Analiza una lista de tickers con
  indicadores técnicos (medias de 20 y 50 días, RSI, soportes y resistencias,
  ATR) y un sentimiento simple de los titulares, y propone Comprar, Vender o
  Esperar con entrada, stop y objetivo. Escribe docs/data.json para una web app
  en el iPhone y envía un correo. Usar cuando el usuario diga "resumen de la
  mañana", "qué comprar hoy", "informe pre-apertura" o "enviarme las
  recomendaciones por correo". Después, seguir con /trader-timemachine,
  /trader-budget y /trader-risk antes de cualquier operación.
argument-hint: "[watchlist.txt]"
---

# trader-morning

Un solo comando prepara el día: datos, análisis, página y correo. No envía órdenes.

```bash
python3 trader-morning/morning.py                  # analiza y escribe docs/data.json
python3 trader-morning/morning.py --send           # además envía el correo
python3 trader-morning/morning.py --send --gate-ny 8   # solo a las 8:xx de Nueva York
```

## Arquitectura (tres capas)

| capa | funciones | notas |
| --- | --- | --- |
| datos | `get`, `bars`, `sentiment` | Yahoo (gráfico diario de 6 meses, titulares RSS). Reintenta 3 veces en 429 y 5xx |
| análisis | `analyse` | puntuación de -4 a +4; compra con 3 o más, venta con -2 o menos |
| presentación | `write_json`, `mail_text`, `send_mail` | la web y el correo leen el mismo resultado |

## Reglas de señal

- Comprar: puntuación 3 o más y RSI bajo 75. Stop = precio - 1,5 x ATR(14). Objetivo = 2R.
- Vender: puntuación -2 o menos. Significa salir si la tienes; no abre cortos.
- Esperar: todo lo demás.

## Pasos para Claude

1. Lee la lista de tickers (`watchlist.txt`) y ejecuta `morning.py`.
2. Presenta las señales tal cual las imprime la herramienta, con el motivo.
3. Recuerda que el sentimiento es un léxico simple en inglés: una señal débil.
4. Para cualquier compra, sigue el flujo del pack: Time Machine, presupuesto, riesgo y tu sí.

## Errores

Cada ticker falla por separado y se anota en `errors`. Si fallan todos, sale con código 1.
Si el correo falla, el JSON ya está escrito y el código de salida es 1.

## Safety

- Educación y herramientas, no asesoramiento financiero.
- Datos gratuitos, no oficiales y con retraso. Compruébalos en tu broker.
- Nada de esta skill coloca órdenes. Cuenta de papel por defecto.
- Las claves (SMTP_PASS) viven en secretos del repositorio, nunca en el chat ni en el código.
