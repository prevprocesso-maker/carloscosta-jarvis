from __future__ import annotations

import asyncio
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass
from abc import ABC, abstractmethod

from ..core.config import load_config
from ..paths import get_jarvis_paths


class Notifier(ABC):
    @abstractmethod
    async def send(self, title: str, message: str, level: str = "info") -> bool:
        pass


@dataclass
class Notification:
    title: str
    message: str
    level: str = "info"  # info, warning, error, success
    timestamp: str = ""
    metadata: Dict[str, Any] = None


class ToastNotifier(Notifier):
    """Windows Toast / Linux notify-send / macOS osascript."""
    
    async def send(self, title: str, message: str, level: str = "info") -> bool:
        import platform
        system = platform.system().lower()
        
        try:
            if system == "windows":
                # Use PowerShell to show toast
                ps = f"""
                [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
                $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
                $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
                $text = $xml.GetElementsByTagName("text")
                $text[0].AppendChild($xml.CreateTextNode("{title}")) | Out-Null
                $text[1].AppendChild($xml.CreateTextNode("{message}")) | Out-Null
                $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
                [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("JARVIS").Show($toast)
                """
                subprocess.run(["powershell", "-Command", ps], timeout=10)
            
            elif system == "linux":
                subprocess.run(["notify-send", "-u", "normal", "-t", "5000", title, message], timeout=5)
            
            elif system == "darwin":
                subprocess.run(["osascript", "-e", f'display notification "{message}" with title "{title}"'], timeout=5)
            
            return True
        except Exception as e:
            print(f"[Toast] Error: {e}")
            return False


class WebhookNotifier(Notifier):
    def __init__(self, webhook_url: str, secret: str = None):
        self.webhook_url = webhook_url
        self.secret = secret
    
    async def send(self, title: str, message: str, level: str = "info") -> bool:
        import aiohttp
        import hmac
        import hashlib
        
        payload = {
            "title": title,
            "message": message,
            "level": level,
            "source": "jarvis-updater",
        }
        
        headers = {"Content-Type": "application/json"}
        body = json.dumps(payload)
        
        if self.secret:
            signature = hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Signature"] = f"sha256={signature}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, data=body, headers=headers) as resp:
                    return resp.status == 200
        except Exception as e:
            print(f"[Webhook] Error: {e}")
            return False


class EmailNotifier(Notifier):
    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str, 
                 from_addr: str, to_addrs: list[str], use_tls: bool = True):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.use_tls = use_tls
    
    async def send(self, title: str, message: str, level: str = "info") -> bool:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        try:
            msg = MIMEMultipart()
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)
            msg["Subject"] = f"[JARVIS] {title}"
            
            body = f"Level: {level.upper()}\n\n{message}"
            msg.attach(MIMEText(body, "plain"))
            
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)
            
            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()
            return True
        except Exception as e:
            print(f"[Email] Error: {e}")
            return False


class LogNotifier(Notifier):
    """Log to file."""
    
    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
    
    async def send(self, title: str, message: str, level: str = "info") -> bool:
        try:
            import datetime
            entry = f"[{datetime.datetime.now().isoformat()}] [{level.upper()}] {title}: {message}\n"
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(entry)
            return True
        except Exception:
            return False


class CompositeNotifier(Notifier):
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers
    
    async def send(self, title: str, message: str, level: str = "info") -> bool:
        results = await asyncio.gather(*[
            n.send(title, message, level) for n in self.notifiers
        ], return_exceptions=True)
        return any(r is True for r in results)


class UpdateNotifier:
    def __init__(self, config_path: Path = None):
        self.config = load_config(config_path) if config_path else {}
        self.notifiers = self._build_notifiers()
    
    def _build_notifiers(self) -> list:
        notifiers = [LogNotifier(get_jarvis_paths().logs / "notifications.log")]
        
        # Toast
        notifiers.append(ToastNotifier())
        
        # Webhook
        webhook_url = self.config.get("notifications", {}).get("webhook_url")
        if webhook_url:
            notifiers.append(WebhookNotifier(
                webhook_url, 
                self.config.get("notifications", {}).get("webhook_secret")
            ))
        
        # Email
        email_cfg = self.config.get("notifications", {}).get("email")
        if email_cfg and email_cfg.get("enabled"):
            notifiers.append(EmailNotifier(
                smtp_host=email_cfg["smtp_host"],
                smtp_port=email_cfg["smtp_port"],
                username=email_cfg["username"],
                password=email_cfg["password"],
                from_addr=email_cfg["from_addr"],
                to_addrs=email_cfg["to_addrs"],
                use_tls=email_cfg.get("use_tls", True),
            ))
        
        return notifiers
    
    async def notify_update_available(self, version: str, channel: str, url: str) -> bool:
        return await self._send(
            f"JARVIS Update Available",
            f"Version {version} ({channel}) is available.\nDownload: {url}",
            "info"
        )
    
    async def notify_update_started(self, version: str) -> bool:
        return await self._send(
            "JARVIS Update Started",
            f"Applying update to version {version}...",
            "info"
        )
    
    async def notify_update_success(self, version: str) -> bool:
        return await self._send(
            "JARVIS Update Complete",
            f"Successfully updated to version {version}",
            "success"
        )
    
    async def notify_update_failed(self, version: str, error: str, rolled_back: bool) -> bool:
        return await self._send(
            "JARVIS Update Failed",
            f"Update to {version} failed: {error}\n{'Rollback performed' if rolled_back else 'Manual intervention required'}",
            "error"
        )
    
    async def notify_rollback(self, version: str) -> bool:
        return await self._send(
            "JARVIS Rollback Performed",
            f"Rolled back to version {version}",
            "warning"
        )
    
    async def _send(self, title: str, message: str, level: str) -> bool:
        from .notifier import CompositeNotifier
        composite = CompositeNotifier(self.notifiers)
        return await composite.send(title, message, level)