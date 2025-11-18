import os
import time
import math
from datetime import datetime
import numpy as np
import pandas as pd
import ccxt
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

EXCHANGE = os.getenv("EXCHANGE", "binanceusdm").lower()
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
SMA_FAST = int(os.getenv("SMA_FAST", "30"))
SMA_TREND = int(os.getenv("SMA_TREND", "200"))
MACD_FAST = int(os.getenv("MACD_FAST", "12"))
MACD_SLOW = int(os.getenv("MACD_SLOW", "26"))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", "9"))
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "")
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_SMTP_SERVER = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "587"))

def send_email_alert(subject: str, body: str):
    """
    Envía una alerta por correo electrónico.
    """
    if not all([EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER]):
        print("Error: Credenciales de correo no configuradas. No se pudo enviar el correo.")
        return

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = EMAIL_SENDER
        recipients = [email.strip() for email in EMAIL_RECEIVER.split(',')]
        msg['To'] = ", ".join(recipients)

        with smtplib.SMTP(EMAIL_SMTP_SERVER, EMAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"Alerta enviada a {EMAIL_RECEIVER} con el asunto: {subject}")
    except Exception as e:
        print(f"Error al enviar el correo: {e}")

send_email_alert("pru","bita")