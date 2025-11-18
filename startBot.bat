@echo off
:: Ruta del entorno virtual
set VENV_PATH=.venv

:: Ruta del script del bot
set BOT_SCRIPT=bots\decision_bot\decision_bot.py

:: Activar el entorno virtual
echo Activando entorno virtual...
call %VENV_PATH%\Scripts\activate

:: Verificar si el entorno virtual se activó correctamente
if not defined VIRTUAL_ENV (
    echo Error: No se pudo activar el entorno virtual.
    pause
    exit /b 1
)

:: Ejecutar el bot
echo Ejecutando bot...
python %BOT_SCRIPT%
cmd /k
:: Desactivar el entorno virtual (opcional)
:: deactivate