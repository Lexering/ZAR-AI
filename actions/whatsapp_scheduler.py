import pywhatkit as kit
import datetime
import json
from pathlib import Path

def whatsapp_scheduler(parameters: dict, player=None) -> str:
    phone   = parameters.get("phone", "")
    message = parameters.get("message", "")
    hour    = parameters.get("hour", None)
    minute  = parameters.get("minute", None)

    if not phone or not message:
        return "Necesito el número de teléfono y el mensaje."

    # Si no se especifica hora, enviar en 2 minutos
    if hour is None or minute is None:
        now    = datetime.datetime.now()
        future = now + datetime.timedelta(minutes=2)
        hour   = future.hour
        minute = future.minute

    try:
        kit.sendwhatmsg(phone, message, hour, minute, wait_time=15, tab_close=True)
        return f"WhatsApp programado para las {hour:02d}:{minute:02d} al número {phone}."
    except Exception as e:
        return f"Error programando WhatsApp: {e}"