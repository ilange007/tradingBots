# Backtester direccional USDT-M (offline)

```bash
python -m backtest.directional.run --symbol BTCUSDT --start 2023-01-01 --capital 500 --leverage 3
python -m backtest.directional.run --symbol BTCUSDT --start 2023-01-01 --leverages 1,2,3,5,10,20 --json
python -m pytest backtest/directional/tests -q
```

Sólo lee CSV existentes en `backtest/funding/data/`, usando el lector de
`backtest/funding/data.py`; nunca descarga ni escribe caché. Une intervalos
solapados (el fichero más reciente prevalece). `--end` es exclusivo; por defecto
usa el final común de las cachés spot y perp, no el reloj del sistema. Errores de
cobertura del perp se rechazan, no se rellenan velas. Spot sólo requiere el open
inicial y close final para buy&hold: en BTC falta una vela spot intermedia
(2023-03-24 13:00 UTC), que no interviene en esa comparación.

## Correspondencia con producción

Revisados `utilities/cerebro.py` y `position_bot.py`:

* LONG: close > SMA200 y MACDh(12,26,9) > 0; SHORT: ambas desigualdades
  inversas. No exige un **cruce** de MACD aunque el comentario diga «cruce ... AHORA».
* RSI14 se calcula en producción pero su filtro está comentado. `--rsi-filter`
  lo habilita literalmente: mínimo de las últimas tres velas cerradas < 40 para
  LONG; máximo > 60 para SHORT. Las constantes RSI 30/70 no se utilizan.
* Trailing: pico de ROI **> 15%**, retroceso **>= 5 puntos**; hard stop:
  ROI **< -15%**. La docstring y algunos comentarios dicen activación al 5%,
  pero el código ejecuta 15%. No hay salida por señal contraria ni take profit.
* Indicadores pandas puros, convención nativa pandas_ta sin TA-Lib: SMA200;
  EMA con semilla SMA y `adjust=False`; MACD señal EMA9 desde el primer MACD
  válido; RSI RMA con `alpha=1/14`, `adjust=True`, `min_periods=14`.
  Se reproduce la reinicialización sobre la ventana descargada de 300 velas:
  al evaluar la penúltima, sólo 299 velas cerradas contribuyen. No se usa la
  última vela en formación. Si pandas_ta usa TA-Lib en producción, la semilla
  del MACD/RSI puede diferir; aquí se fija la convención nativa determinista.
* Producción usa CAPITAL de entorno como margen fijo, busca el leverage máximo,
  sondea ticker cada cinco segundos, registra el ticker en lugar del fill real,
  intenta cerrar una cantidad por defecto de **1 moneda**, borra su memoria
  antes de confirmar la orden y ejecuta `sys.exit(0)` tras cerrar. Esas decisiones
  operativas no se trasladan: se usa margen compuesto configurable, leverage
  explícito, cantidad real de la posición y reentradas según lo solicitado.

## Ejecución y contabilidad

Señal al cierre t; entrada en open t+1. Una posición por símbolo; tras una salida
intravela puede entrar al siguiente open con la señal recién cerrada. Una salida
en el propio open no permite reentrada en ese mismo open. Las primeras 199 velas
sin histórico previo son calentamiento, fuera de mercado; el CLI precarga hasta
299 horas anteriores si existen. El buy&hold usa todo el intervalo solicitado,
incluido este calentamiento.

Recorrido intravela determinista: open/gap, eventos de funding, extremo adverso,
extremo favorable, close. El open actualiza el pico si sobrevive al gap. El adverso sólo usa el trailing previamente activado;
el favorable actualiza el pico y el close puede disparar el nuevo trailing en
esa misma vela. No se reutiliza el low/high anterior con un pico futuro.
Los stops se rellenan al **umbral**, incluso en gaps, con slippage adverso.
Si el open ya atraviesa la liquidación, ésta tiene prioridad: un stop no puede
rescatar un margen ya liquidado. En un recorrido continuo, se ejecuta primero
el nivel protector más cercano, de modo que normalmente el hard stop precede
a la liquidación. La igualdad no dispara hard stop ni activación; sí trailing
y liquidación (tolerancia numérica de 1e-12 del precio de entrada). Las salidas intravela llevan timestamp de fin de hora.

ROI para stops excluye costes: `side * (precio / entrada - 1) * leverage`.
`--stop-mode price --ref-leverage 3` fija movimientos de precio en 15/3% para
activación/stop y 5/3 puntos para retroceso, cualquiera sea el leverage efectivo.
Slippage en bps por fill; comisión taker por lado sobre cantidad × fill (default
0.0005). Para financiar la comisión inicial, margen = patrimonio × fracción /
(1 + leverage × fee); nocional = margen × leverage. Por eso margen nominal con
fracción 1 es ligeramente menor que el patrimonio, sin efectivo negativo al entrar.

Funding positivo paga LONG y cobra SHORT: `-side * cantidad * mark * rate`.
Sólo se aplica si entrada < timestamp < fin del histórico y sigue abierta.
Una posición existente paga si el evento coincide con el open; una nueva no.
Se usa mark_price real si está presente; si falta (ocurre en la caché), se usa
el open de esa hora, sin consultar close futuro. Todos los eventos intrahora se
aplican antes del recorrido de extremos: OHLC no permite conocer si el stop
ocurrió antes; es una aproximación explícita. Gaps de precio se procesan antes.
Funding se añade/retira del colateral aislado, sin reposición automática.

Liquidación simplificada con mantenimiento m=0.005 sobre nocional al precio P:
`margen + funding + side*q*(P-entry) = m*q*P`.
No usa tiers, mark OHLC ni tasas especiales Binance. Se pierde todo el colateral
restante del margen; la comisión de cierre se contabiliza **dentro** de esa
confiscación, evitando descontar dos veces del resto de la cuenta. Con fracción
1 una liquidación agota el patrimonio. El precio de fill de liquidación se
informa con slippage, pero no altera la pérdida fija del colateral solicitado.

## Métricas y API

`engine.run_backtest(candles, funding, config, signals=None, start=None, spot=None)`
es puro y determinista. DataFrames con DatetimeIndex UTC; OHLC positivo y
continuo a 1h; funding con funding_rate y mark_price opcional. `signals` permite
inyectar -1/0/1 en tests (sigue ejecutándose desplazado una vela). Devuelve
métricas, trades y curva de patrimonio, sin mutar las entradas.

PnL y trades incluyen costes y funding; win rate, medias y profit factor usan
PnL **neto** por operación. `average_loss` es negativo. Profit factor es `null`
cuando no hay pérdidas; anualizado compuesto usa 365.25 días, vale -100% si se
agota el capital y `null` si desborda. Se liquida voluntariamente cualquier
posición al último close (`end_of_data`), con costes. La peor racha cuenta
pérdidas consecutivas; una operación a cero la interrumpe. PnL por año natural
se obtiene de patrimonio mark-to-market, reconciliando con PnL total (no sólo
por fecha de cierre de trades). Fin de vela en 1 de enero pertenece al año
anterior. Drawdown incluye extremos intravela visitados y fills de stops,
además de costes; no incluye extremos posteriores a la salida.

Buy&hold **spot** 1x invierte capital inicial al primer open y vende al último
close spot, con los mismos costes configurados por lado para comparar ejecución.
No sustituye spot con perp; en la API sin spot devuelve `null`.

Los tests cubren tendencia/trailing, límites estrictos, shorts, hard stop y
slippage al umbral, liquidación por gap, prioridad de stops, reentrada,
causalidad de señales e indicadores, comisiones exactas, funding y conciliación.
