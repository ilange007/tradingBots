Cómo Usar el Bot decision_bot.py.
Abre tu terminal y navega al directorio donde guardaste decision_bot.py.

Ejecuta el script con los 10 parámetros de los indicadores. El orden es importante.

Ejemplo de llamada:

python decision_bot.py BTC/USDT 1h 20 10 30 100 8 18 5 10
BTC/USDT (SYMBOL)
15m (TIMEFRAME)
20 (ATR_PERIOD)
10 (RSI_PERIOD)
30 (SMA_FAST)
100 (SMA_TREND)
8 (MACD_FAST)
18 (MACD_SLOW)
5 (MACD_SIGNAL)
10 (LOOP_SLEEP_SEC) Cada cuanto se repite el bucle

Muestra la decisión de SMA y MACD en cada ciclo.
Incluye un grado de certeza para cada decisión.
Te permite especificar la frecuencia de repetición del ciclo.

python decision_bot.py BTC/USDT 1h 14 14 30 200 12 26 9 10
SYMBOL: BTC/USDT

TIMEFRAME: 1h (Una hora).

ATR_PERIOD: 14

RSI_PERIOD: 14

SMA_FAST: 30

SMA_TREND: 200

MACD_FAST: 12

MACD_SLOW: 26

MACD_SIGNAL: 9

LOOP_SLEEP_SEC: 10
___________________________________________________________________________________________________________________________________________

manual_bot.py long BNB
Este bot te permite controlar manualmente la entrada, pero una vez que abres una posición, el bot se encarga de gestionarla. El bot usará el ATR para calcular los valores de TP y SL; pero deben ser creadas manualmente en Binance. Si los valores de TP_PCT y SL_PCT están en tu archivo .env, los usará en su lugar.

Puedes usar el archivo .env para configurar el bot. El bot buscará los valores de TP_PCT y SL_PCT para determinar cómo calcular el TP y el SL. 
El bot verificará si hay posiciones abiertas. 

De momento tengo la idea de usar n8n para recibir recomendaciones del decision_bot y ejecutarlas mediante una interfaz de airtable que active en n8n el llamado al manual_bot

____________________________________________________________________________________________________________________________________________

Tu Estrategia Resumida
Doble Confirmación para la Entrada: Al esperar que tanto el MACD como la SMA te den una señal de certeza del 100%, estás utilizando una estrategia de doble confirmación. Esto es crucial, ya que el MACD te indica el "momentum" o la fuerza de la señal, mientras que la SMA te confirma que el precio se está moviendo a favor de una tendencia más amplia. Entrar solo cuando ambos indicadores coinciden reduce drásticamente las señales falsas.

Enfoque en la Meta Diaria: El plan de repetir las operaciones solo hasta alcanzar tu objetivo de 10 USD es una disciplina de trading muy sólida. Esto te protege de la "avaricia" y de la tentación de sobreoperar, que son las causas más comunes de las pérdidas.

Gestión de Riesgo: Con un capital de 499 USD y una ganancia por operación de casi 10 USD (con los parámetros recomendados de un ratio de 1:2), una sola operación exitosa podría ser suficiente. Si la primera operación falla, puedes volver a intentarlo.

En resumen, tu plan es sólido porque se basa en confirmación, disciplina y gestión de riesgo. No estás buscando ganancias irreales ni operando sin un plan. La doble confirmación te dará una ventaja estadística, y la meta diaria te ayudará a mantenerte enfocado y rentable a largo plazo.

Si la SMA te indica una certeza del 100% de LONG y el MACD te indica una certeza del 100% de SHORT, significa que no debes hacer nada.
Tu plan de acción diario sería:

Ganancia: Si alcanzas tu meta de $10 USD, te detienes por el día.
Pérdida: Si pierdes $10 USD (dos operaciones perdedoras), te detienes por el día.

Esta disciplina te obliga a tener una mentalidad enfocada en la gestión de riesgos en lugar de perseguir ganancias. Es una forma sostenible de operar, ya que te permite controlar tus emociones y asegurar que, en el peor de los casos, tu pérdida diaria esté limitada.

Recomendación: Un Enfoque de Dos Pasos 🛡️
Para una gestión de riesgo segura y eficaz sin tener que mantener tu computadora encendida, te recomiendo lo siguiente:

Abre la Posición con el Bot: Usa manual_bot para abrir la posición en el momento que te indique decision_bot. Una vez que se confirma la apertura, puedes cerrar el manual_bot con seguridad.

Establece un Stop Loss Fijo en Binance (Paso Crítico): Inmediatamente después, abre tu aplicación de Binance y coloca una orden de Stop Loss fijo manual. Para este valor, usa el precio de Stop Loss que el bot calculó y te mostró en la consola (basado en el ATR). Esta orden es tu seguro. Si el mercado se mueve en tu contra, esta orden te sacará de la posición antes de que las pérdidas sean mayores.

Establece el Trailing Stop (para las Ganancias): Una vez que tu Stop Loss fijo esté en su lugar, puedes colocar tu orden de Trailing Stop con un 1% de "Trailing Delta". Esta orden es la que te permitirá capturar ganancias mayores y cerrar la posición si el precio se mueve a tu favor y luego retrocede.

En resumen, el Stop Loss fijo es para limitar tus pérdidas, mientras que el Trailing Stop es para maximizar tus ganancias. Usar ambos te brinda una estrategia de salida completa y segura.

¡Advertencia Importante!

Con esta modificación, el bot se convierte en una herramienta de "disparar y olvidar". No tendrá ninguna lógica para cerrar tu posición en caso de que el precio se mueva en tu contra o a tu favor.

Es absolutamente crucial que, inmediatamente después de ejecutar el bot, entres en la aplicación de Binance y establezcas manualmente un Stop Loss fijo y un Trailing Stop para gestionar el riesgo y las ganancias. De lo contrario, tu posición quedará desprotegida.
_____________________________________________________________________________________________________________________________________________

ATR_K: 2.5 parece ser el Stop Loss ideal, indicando que darle al precio un espacio moderado para moverse es la mejor forma de proteger el capital.

TRAIL_R_MULTIPLE: Un valor de 4.0 para el Trailing Stop demuestra que la estrategia se beneficia de dejar correr las ganancias lo más posible.