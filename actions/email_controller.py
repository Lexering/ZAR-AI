import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"

def _get_email_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        d = json.load(f)
    return d.get("email", {})

PROVIDERS = {
    "gmail": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "imap_host": "imap.gmail.com",
    },
    "outlook": {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
    },
    "hotmail": {
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "imap_host": "outlook.office365.com",
    },
    "yahoo": {
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "imap_host": "imap.mail.yahoo.com",
    },
}

def _detect_provider(email_address: str) -> dict:
    domain = email_address.split("@")[-1].lower()
    if "gmail" in domain:
        return PROVIDERS["gmail"]
    elif "outlook" in domain or "hotmail" in domain or "live" in domain:
        return PROVIDERS["outlook"]
    elif "yahoo" in domain:
        return PROVIDERS["yahoo"]
    return PROVIDERS["gmail"]

def email_controller(parameters: dict, player=None, speak=None) -> str:
    action   = parameters.get("action", "send")
    cfg      = _get_email_config()

    if not cfg:
        return "No hay cuenta de correo configurada. Dile a ZAR que configure tu correo."

    email_address = cfg.get("address", "")
    password      = cfg.get("password", "")
    provider      = _detect_provider(email_address)

    if action == "send":
        to      = parameters.get("to", "")
        subject = parameters.get("subject", "Sin asunto")
        body    = parameters.get("body", "")

        try:
            msg = MIMEMultipart()
            msg["From"]    = email_address
            msg["To"]      = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(provider["smtp_host"], provider["smtp_port"]) as server:
                server.ehlo()
                server.starttls()
                server.login(email_address, password)
                server.sendmail(email_address, to, msg.as_string())

            return f"Correo enviado a {to} con asunto '{subject}'."
        except Exception as e:
            return f"Error enviando correo: {e}"

    elif action == "read":
        count = parameters.get("count", 5)
        folder = parameters.get("folder", "INBOX")

        try:
            mail = imaplib.IMAP4_SSL(provider["imap_host"])
            mail.login(email_address, password)
            mail.select(folder)

            _, data = mail.search(None, "ALL")
            ids = data[0].split()
            latest = ids[-count:][::-1]

            results = []
            for uid in latest:
                _, msg_data = mail.fetch(uid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])

                subject, enc = decode_header(msg["Subject"])[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(enc or "utf-8", errors="ignore")

                sender = msg.get("From", "Desconocido")
                date   = msg.get("Date", "")

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore")[:200]
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")[:200]

                results.append(f"De: {sender}\nAsunto: {subject}\nFecha: {date}\nPreview: {body}\n---")

            mail.logout()
            return "\n".join(results) if results else "No hay correos."

        except Exception as e:
            return f"Error leyendo correos: {e}"

    return "Acción no reconocida. Usa 'send' o 'read'."