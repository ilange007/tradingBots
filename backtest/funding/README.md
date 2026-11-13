# Arbitraje histórico de funding

```bash
python -m backtest.funding.run --symbol BTCUSDT --start 2023-01-01 --capital 500
python -m backtest.funding.run --symbol BTCUSDT --start 2023-01-01 --end 2024-01-01 --interval 1h --json
python -m pytest backtest/funding/tests -q
```

Todas las fechas son UTC, con inicio inclusivo y fin exclusivo, alineados al intervalo.
Sin `--end` se usa la última frontera de vela completa. El CLI exige cobertura
completa en los extremos spot. El motor recorta a la cobertura común de ambos
mercados y rellena huecos de hasta 24 velas consecutivas con el cierre anterior
(OHLC plano, volumen cero), emitiendo un aviso. Huecos mayores son un error. Las descargas
usan endpoints públicos, paginación, timeout y reintentos limitados; `--refresh`
invalida la caché del rango exacto. La caché CSV queda en `data/`, ignorada por Git.
Los errores HTTP se propagan y no se sustituyen por históricos inventados.

## API y parámetros

`run_backtest(spot, perp, funding, BacktestConfig(...))` no modifica los DataFrames
ni realiza I/O. Los precios requieren índice `DatetimeIndex`, columnas
`open/high/low/close` positivas y velas regulares de 8h (o 1h). Funding requiere
`funding_rate` y opcionalmente `mark_price`. Devuelve `BacktestResult` con
`metrics`, curva `equity` y operaciones `trades`. Los índices sin zona se interpretan
como UTC. Los datos de funding anteriores al inicio solo sirven para el calentamiento.

Por defecto: capital 500 USDT, presupuesto spot 50%, resto como colateral aislado,
apalancamiento máximo 2x, taker spot 0.1%, perp 0.05%, slippage 0 bps por pata y
mínimo nocional 5 USDT por orden de entrada. El mínimo configurable no reproduce
filtros históricos de Binance, lotes ni redondeos. Las salidas siempre cierran toda
la posición, también si ha caído por debajo del mínimo de entrada.

La cantidad es idéntica en ambas patas y es el menor tamaño financiable por ambos
presupuestos, incluyendo costes de entrada. El presupuesto de margen completo,
menos comisión del corto, respalda la posición; por tanto el apalancamiento efectivo
puede ser inferior al máximo configurado. El rebalanceo puede transferir efectivo
libre al colateral.
El margen no se resta dos veces del patrimonio: ya está incluido en el efectivo.
No hay préstamos ni intereses; cada reducción mantiene la misma cantidad en ambas patas.
El siguiente ciclo sí usa el patrimonio actualizado para dimensionar su entrada.

## Señal, funding y ejecuciones

La media de las últimas N liquidaciones (default 3) debe superar estrictamente
`entry_threshold` (default 0); se sale si cae estrictamente bajo `exit_threshold`
(default 0). Solo se usan liquidaciones con timestamp **anterior** a la apertura.
El funding exactamente en la apertura se paga/cobra antes de las órdenes y solo
para una posición previamente abierta. Los eventos posteriores dentro de una vela
se abonan si la posición permanece abierta. Una liquidación en el extremo final
del rango queda excluida. La frecuencia efectiva la dictan los registros de funding,
normalmente cada 8h; nunca se inventan pagos en cada vela de 1h.

Funding = cantidad corta × mark_price del evento × tasa. Si falta mark_price se
usa la apertura del perpetuo de esa vela. El colateral recibe también tasas negativas.
Las entradas y salidas por señal se ejecutan en OPEN con slippage adverso en ambas
patas; las comisiones se aplican al nocional ejecutado. La posición restante se cierra
al último CLOSE, incluyendo ambas comisiones y slippage.

Ratio de margen = (colateral + PnL no realizado del corto) / nocional actual.
Se mide con el HIGH del perpetuo. Si cae bajo `rebalance_margin_ratio` (default
0.5), se programa un rebalanceo para el siguiente OPEN. Se reduce por igual spot
y corto, con comisiones y slippage sobre la cantidad operada, y se transfieren
los ingresos y el efectivo disponible al colateral para alcanzar `1/leverage`.
La reducción es la mínima necesaria; si el precio se recupera y el margen ya
alcanza el objetivo, no se reduce. La señal de salida tiene prioridad sobre el
rebalanceo. `min_margin_ratio` se conserva por compatibilidad, sin provocar cierres.

Si el ratio alcanza `maintenance_margin` (default 0.5%), se liquida el corto:
se pierde el colateral asignado y se cobra `liquidation_fee` (default 1.25%) sobre
el nocional al HIGH. El spot se vende al CLOSE, con comisión y slippage. Un salto
en OPEN que cruce mantenimiento se liquida al OPEN antes del rebalanceo pendiente.
OHLC no revela el orden intravela: se evalúa primero HIGH y después funding interno;
si HIGH ya liquida, esos pagos se excluyen. Si el funding negativo cruza el umbral,
se liquida tras esos pagos. La exposición cuenta la vela completa. No se permite
reentrada en la misma vela de una salida. Se trata de un modelo simplificado,
sin tramos históricos de mantenimiento ni reconstrucción del motor del exchange.

## Métricas

PnL neto incluye funding, base, comisiones y slippage de entradas, rebalanceos
y salidas. `rebalances`, `liquidations` y `rebalance_costs` contabilizan las acciones
y su coste; el coste de rebalanceo ya está incluido en `total_costs`.
`basis_pnl = net_pnl - gross_funding + total_costs`: las comisiones usan precios
ejecutados y el slippage ya está descontado del PnL; no se descuenta dos veces.
Anualizado es rentabilidad geométrica sobre días reales de exposición del histórico
(año de 365.25 días); si el capital final es <= 0 se informa -100%, y si desborda la
representación numérica, `null`. Los porcentajes de salida están expresados en %;
`min_margin_ratio` es una fracción o `null` si nunca hubo posición.
`max_drawdown_pct` usa solo aperturas y cierres. `stress_drawdown_pct` añade
el estrés conjunto LOW spot/HIGH perp como métrica separada; nunca es un relleno
de ejecución. En liquidaciones, `basis_pnl` incluye la pérdida del corto limitada
al colateral.
Buy & hold invierte todo el capital en spot al inicio y vende al final, con las mismas
comisiones spot y slippage. La referencia de efectivo tiene PnL cero.
