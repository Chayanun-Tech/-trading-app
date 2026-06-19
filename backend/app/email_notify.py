"""ส่งแจ้งเตือนทาง Gmail SMTP (App Password).

ตั้งค่าใน backend/.env:
  GMAIL_USER=your@gmail.com
  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Google → Manage Account → Security → App Passwords
  ALERT_NOTIFY_EMAIL=destination@gmail.com  # ปลายทาง (ใส่อีเมลเดียวกันก็ได้)
"""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_alert_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    from_user: str,
    app_password: str,
) -> bool:
    """ส่งอีเมลผ่าน Gmail SMTP port 587. คืน True ถ้าสำเร็จ."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_user
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(from_user, app_password)
            smtp.sendmail(from_user, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[email_notify] ส่งไม่สำเร็จ: {e}")
        return False
