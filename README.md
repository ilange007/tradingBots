1. Asegúrate de tener Python instalado en tu sistema.
2. Descarga el código de la aplicación y el archivo requirements.txt.
3. Abre una terminal y navega hasta el directorio del proyecto.
4. Sigue estos pasos:
- Crea un entorno virtual: python -m venv .venv
- Activa el entorno virtual:
    - Linux/Mac: source .venv/bin/activate
    - Windows: .venv\Scripts\activate
- Instala las dependencias: pip install -r requirements.txt
5. Ejecuta la aplicación: python app.py (o el nombre del archivo que quieras ejecutar)

______________________________________________________________________________________

La estrategia inicial consiste en ejecutar decision_bot y esperar a que las señales SMA y MACD estén al 100%.
Luego en Binances ejecutar el trading indicado (Long o Short) mientras las señales SMA y MACD estén al 100%.
OJO: Ambas señales deben indicar Long o Short, pueden darse situaciones en las que no coincidan, en ese caso no hacer nada.