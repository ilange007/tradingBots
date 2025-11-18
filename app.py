import os
import subprocess
from flask import Flask, request, render_template_string

# --- Configuración de Flask ---
app = Flask(__name__)

# --- HTML de la página web ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Signal Bot Activator</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Inter', sans-serif;
            background-color: #f0f4f8;
            color: #2d3748;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            padding: 20px;
        }
        .container {
            background-color: #ffffff;
            padding: 3rem;
            border-radius: 1rem;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
            text-align: center;
            width: 100%;
            max-width: 500px;
        }
        h1 {
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            color: #1a202c;
        }
        p {
            font-size: 1rem;
            margin-bottom: 2rem;
            color: #718096;
        }
        .action-button {
            background-color: #4c51bf;
            color: white;
            padding: 0.75rem 1.5rem;
            border: none;
            border-radius: 0.5rem;
            font-size: 1rem;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease-in-out;
        }
        .action-button:hover {
            background-color: #3f479e;
            transform: translateY(-2px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
        .status-message {
            margin-top: 1.5rem;
            padding: 0.75rem;
            border-radius: 0.5rem;
            font-weight: 700;
            display: none; /* Oculto por defecto */
        }
        .success {
            background-color: #d1e7dd;
            color: #0f5132;
        }
        .error {
            background-color: #f8d7da;
            color: #721c24;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Activar Signal Bot</h1>
        <p>Haz clic en el botón para iniciar el escaneo de mercados. El bot se detendrá automáticamente después de enviar una alerta por correo electrónico.</p>
        <button class="action-button" id="startButton">Iniciar Escaneo</button>
        <div id="statusMessage" class="status-message"></div>
    </div>

    <script>
        document.getElementById('startButton').addEventListener('click', async () => {
            const statusMessage = document.getElementById('statusMessage');
            
            // Oculta el mensaje anterior y muestra un mensaje de carga
            statusMessage.style.display = 'block';
            statusMessage.className = 'status-message';
            statusMessage.textContent = 'Iniciando el bot... por favor espera.';

            try {
                const response = await fetch('/start_scan');
                const result = await response.text();
                
                if (response.ok) {
                    statusMessage.textContent = result;
                    statusMessage.classList.add('success');
                } else {
                    statusMessage.textContent = 'Error: ' + result;
                    statusMessage.classList.add('error');
                }
            } catch (error) {
                statusMessage.textContent = 'Error de conexión con el servidor. Por favor, inténtalo de nuevo.';
                statusMessage.classList.add('error');
            }
        });
    </script>
</body>
</html>
"""

# --- Rutas del Servidor ---

@app.route("/")
def home():
    """Sirve la página principal con el botón."""
    return render_template_string(HTML_TEMPLATE)

@app.route("/start_scan", methods=["GET"])
def start_scan():
    """
    Inicia el proceso del signal_bot en un subproceso
    y retorna un mensaje de confirmación.
    """
    try:
        # La bandera 'start_new_session=True' se usa para crear un nuevo grupo de procesos,
        # lo que previene que el proceso hijo se termine cuando se cierre el proceso padre.
        subprocess.Popen(
            ["python3", "signal_bot.py"],
            cwd=os.getcwd(),
            start_new_session=True
        )
        return "El proceso del bot se ha iniciado correctamente. Te llegará un correo si se encuentra una señal."
    except Exception as e:
        return f"Error al iniciar el proceso: {e}", 500

# --- Inicio de la aplicación ---
if __name__ == "__main__":
    # Normalmente, en un cPanel, esto se ejecuta de una manera diferente
    # (por ejemplo, a través de WSGI).
    # Esta línea es para pruebas locales.
    app.run(host="0.0.0.0", port=5000, debug=True)
