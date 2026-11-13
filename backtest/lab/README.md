# Laboratorio direccional diario, sin red

```bash
python -m pytest backtest/lab/tests -q
python -m backtest.lab.run --symbols BTCUSDT,ETHUSDT --capital 500
python -m backtest.lab.run --symbols BTCUSDT,ETHUSDT --capital 500 --max-leverage 1 --json
```

Solo lee `backtest/data_long/{BTCUSDT,ETHUSDT}_{perp_1h,funding}.csv`.
Usa numpy/pandas y pytest ya presentes en el proyecto. No descarga, optimiza,
selecciona parámetros, escribe resultados ni llama a Binance. `--json` emite un
único documento JSON; sin ese flag imprime tablas por símbolo, periodo y año.

## Separación temporal

Diseño: 2020-01-01 a 2024-12-31, ambos inclusive. Reservado: 2025-01-01 hasta
el último día UTC completo común a los símbolos (datos actuales: 2026-11-12).
Cada periodo comienza desde efectivo con 500 USDT, o `--capital`, y cuenta sus
propios costes de entrada. Los indicadores y estados de señales del reservado
usan historia anterior: esto no transfiere posiciones ni ganancias de diseño.
No se usa el reservado para elegir una candidata o calibrar parámetros.
Los años naturales desglosan cada simulación continua; no reinician capital.
El calentamiento inicial permanece en efectivo y forma parte de las métricas.

## Señales fijas

Las velas diarias agregan OHLCV de 24 velas horarias UTC completas. La exposición
de la fila D se calcula al cierre de D y solo se ejecuta al open de D+1.
Sin historia suficiente, la señal es cero. No se rellenan señales desde el futuro.

| Estrategia | Regla al cierre diario |
| --- | --- |
| sma_trend | +1 si close > SMA200, cero en otro caso |
| sma_cross_ls | +1 si SMA50 > SMA200; −1 si menor; cero si iguales |
| donchian | Entra long si close > máximo high de los 55 días anteriores; sale si close < mínimo low de los 20 días anteriores. Excluye el día actual de ambos canales |
| tsmom_vol | Si retorno simple de 90 días > 0, tamaño min(L, 0.40 / vol30); si no, cero. vol30 = desviación muestral de retornos diarios simples × √365. Volatilidad cero y momentum positivo implica tamaño L |
| rsi2_dip | Entra long si close > SMA200 y RSI de Wilder(2) < 10; sale si close > SMA5. Wilder usa semilla SMA de los dos primeros cambios; un precio plano tiene RSI 50 |
| ensemble | Media aritmética de sma_trend, donchian y tsmom_vol |
| buy_hold | Objetivo constante +1 desde el primer open; el CLI precarga este objetivo conocido antes del inicio del dataset |
| cash | Objetivo cero |

L limita **exposiciones objetivo**, no multiplica las reglas de tamaño 1.
Si L < 1, esas reglas se recortan a L. La exposición efectiva puede derivar
entre ejecuciones debido a cambios del patrimonio. `buy_hold` usa el mismo motor
de exposición constante con banda, costes y funding; es un benchmark de futuros,
no una compra spot de cantidad invariable.

## Contabilidad y eventos

El motor conserva cantidad firmada q entre órdenes. Patrimonio a precio P =
saldo + q × P. Al open diario calcula delta nocional = objetivo × patrimonio
antes de costes − q × open. Si |delta| / patrimonio < 0.10, no opera; la igualdad
sí opera. La banda también se aplica a salidas y reversiones: puede dejar una
posición residual. Revisa la exposición real, aunque no haya cambiado la señal.
Cada ajuste cuenta como una operación (una reversión es una operación por todo
el delta). No son round trips. Fracciones arbitrarias de cantidad, sin mínimos
de lote, tick ni notional de Binance.

Cada orden descuenta |delta| × 0.0005 de comisión y |delta| × 0.0002 de slippage.
El slippage es un débito monetario equivalente sobre el nocional al open, sin
alterar el precio contable. No hay cierre forzado al final: se marca a mercado y
no se inventa una comisión de salida. Los objetivos usan patrimonio antes de
costes, así que la exposición posterior puede exceder ligeramente el objetivo.

En cada hora: (1) comprobar insolvencia al open con la cantidad heredada;
(2) ejecutar el objetivo previo si es medianoche; (3) aplicar cada evento real
de funding de esa hora, en orden temporal; (4) comprobar el extremo adverso low
para long o high para short; (5) marcar al close. Un evento exactamente al open
se aplica después de la orden de ese open. Los milisegundos originales no se
eliminan ni deduplican; se agrupan por hora para la simulación. En ausencia de
trayectoria intrahoraria se aplican los eventos antes del extremo adverso.

Funding neto = −q × mark_price × funding_rate por evento, sin multiplicar por
8 ni por 24. Long paga una tasa positiva y short cobra. Si mark_price falta,
usa el open de esa hora (dato ya observable), nunca su cierre. No se interpolan
pagos ni se inventan tasas: el CLI exige cobertura en los extremos y el motor
acepta un calendario irregular, horario o de 8h.

Si el patrimonio al open, tras costes/funding o en el extremo horario cae a
<= 0, se registra una liquidación, el patrimonio se fija en cero y esa cuenta
permanece inactiva hasta el final del periodo. Es el umbral solicitado de
insolvencia, sin margen de mantenimiento, escalones ni comisión de liquidación.
El orden funding/extremo es una convención explícita por falta de ticks.

## Métricas y cartera

Patrimonio diario al cierre UTC, incluyendo costes y funding. Retorno anualizado
geométrico con 365 días; volatilidad = std muestral de retornos diarios × √365;
Sharpe = media diaria / std × √365, tasa libre de riesgo cero (0 con varianza
cero). Max drawdown usa marcas diarias e incluye el capital inicial como pico;
no representa el drawdown intrahorario salvo liquidación. Tras insolvencia los
retornos siguientes son cero. Un año posterior enteramente en cero tiene
retorno cero por convención; la pérdida ya se atribuyó al año de liquidación.

Tiempo expuesto = horas con cantidad distinta de cero / horas del periodo.
Una hora donde se liquida cuenta como expuesta. Costes totales = comisiones +
slippage, en USDT; funding neto es positivo cuando se cobra. Peor año es el menor
retorno observado entre los años del periodo, incluyendo años parciales: no se
extrapola, a diferencia del retorno anualizado. JSON incluye retorno total,
capital inicial/final y desglose de costes, además de todas las métricas de tabla.

La cartera BTC+ETH destina inicialmente 50% del capital a cada símbolo para cada
estrategia, sin rebalanceo ni transferencias entre cuentas. Se suman patrimonios,
costes, funding, órdenes y liquidaciones; se recalculan retornos, volatilidad,
Sharpe y drawdown sobre el patrimonio conjunto. Una cuenta liquidada no consume
la otra. Una hora se cuenta expuesta si cualquiera mantiene posición. Se usa
la linealidad del motor para escalar a la mitad las magnitudes monetarias de
cada simulación individual. Solo aparece si se solicitan ambos símbolos.

## Archivos y comprobaciones

`strategies.py`: agregación y exposiciones. `engine.py`: validación, ejecución,
contabilidad, métricas y cartera. `run.py`: carga local, cortes temporales y CLI.
`tests/`: datos sintéticos para causalidad en todas las estrategias, ejecución
desplazada, costes, signos y marcas de funding, banda, deriva de exposición,
tendencia, liquidación long/short y gaps, calentamiento y cartera.
Las reglas son experimentales: no incluyen el mantenimiento de margen ni las
restricciones de órdenes del exchange y no constituyen un bot ejecutable.
