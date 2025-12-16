1. Asegúrate de tener Python instalado en tu sistema. (3.13)
2. Descarga el código de la aplicación y el archivo requirements.txt.
3. Abre una terminal y navega hasta el directorio del proyecto.
4. Sigue estos pasos:
- Crea un entorno virtual: python -m venv .venv
- Activa el entorno virtual:
    - Linux/Mac: source .venv/bin/activate
    - Windows: .venv\Scripts\activate
- Instala las dependencias: pip install -r requirements.txt
- Crea tu archivo .env (puedes usar el ejemplo de env.txt)
5. Ejecuta la aplicación: python app.py (o el nombre del archivo que quieras ejecutar)

______________________________________________________________________________________

La estrategia inicial consiste en ejecutar decision_bot (No necesita API Key ni API Secret) y esperar a que las señales SMA y MACD estén al 100%.
Luego en Binances ejecutar el trading indicado (Long o Short) mientras las señales SMA y MACD estén al 100%.
OJO: Ambas señales deben indicar Long o Short, pueden darse situaciones en las que no coincidan, en ese caso no hacer nada.
______________________________________________________________________________________

**CONFIGURACIÓN BINANCE**
Estos pasos son críticos, además definen el riesgo y cómo se ejecutarán las órdenes de tu bot en tiempo real (en caso de que se use scalping_bot).

**Activar el Modo de Margen (Margin Mode)**
El modo de margen define cómo se utiliza el capital en tu billetera de futuros para mantener las posiciones abiertas. Para el trading con bots y la gestión de riesgo, generalmente se recomienda el modo Aislado (Isolated).

Navegación: Ve a la interfaz de trading de futuros (generalmente haciendo clic en la pestaña "Futures" o "Derivados").

Encuentra la Opción: Busca la sección donde aparece el par de trading (ej. BTCUSDT). Debajo o al lado del par, verás una etiqueta que dice "Cross" (Cruzado) o "Isolated" (Aislado).

**Seleccionar "Isolated" (Aislado):**

Isolated (Aislado): Solo se utilizará el capital que asignaste a esa posición específica para cubrir las pérdidas. Si se alcanza el precio de liquidación, solo pierdes el margen inicial de esa posición. Recomendado para bots.

Cross (Cruzado): Se utiliza todo el saldo de tu billetera de futuros para evitar la liquidación de la posición. Esto implica un riesgo mayor.

Confirma el cambio: Haz clic en la opción y selecciona "Isolated".