from __future__ import annotations

"""WhatsApp Bridge Integration - Python client for the Node.js WhatsApp bridge."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Awaitable, Optional

import aiohttp

logger = logging.getLogger(__name__)


class WhatsAppBridgeClient:
    """Client for communicating with the WhatsApp Bridge Node.js server."""
    
    def __init__(self, base_url: str = "http://localhost:8765"):
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_session: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._message_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._status_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._connected = False
        self._qr_code: Optional[str] = None
        
    async def connect(self) -> None:
        """Connect to the bridge."""
        self._session = aiohttp.ClientSession()
        # Try to connect WebSocket for real-time events
        try:
            await self._connect_websocket()
        except Exception as e:
            logger.warning(f"WebSocket connection failed, falling back to polling: {e}")
    
    async def _connect_websocket(self) -> None:
        """Connect to WebSocket for real-time events."""
        ws_url = self.base_url.replace("http", "ws") + "/ws"
        self._ws_session = await self._session.ws_connect(ws_url)
        self._ws_task = asyncio.create_task(self._ws_listener())
    
    async def _ws_listener(self) -> None:
        """Listen for WebSocket messages."""
        try:
            async for msg in self._ws_session:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_ws_message(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self._ws_session.exception()}")
                    break
        except Exception as e:
            logger.error(f"WebSocket listener error: {e}")
        finally:
            # Try to reconnect after a delay
            await asyncio.sleep(5)
            if self._session and not self._session.closed:
                await self._connect_websocket()
    
    async def _handle_ws_message(self, data: dict) -> None:
        """Handle incoming WebSocket message."""
        event_type = data.get("type", "unknown")
        
        if event_type == "message" and self._message_callback:
            await self._message_callback(data.get("payload", {}))
        elif event_type == "status" and self._status_callback:
            await self._status_callback(data.get("payload", {}))
            self._connected = data.get("payload", {}).get("connected", False)
            self._qr_code = data.get("payload", {}).get("qr_code")
    
    def set_message_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set callback for incoming messages."""
        self._message_callback = callback
    
    def set_status_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Set callback for status changes."""
        self._status_callback = callback
    
    async def get_status(self) -> dict:
        """Get current WhatsApp connection status."""
        if not self._session:
            await self.connect()
        async with self._session.get(f"{self.base_url}/status") as resp:
            return await resp.json()
    
    async def get_qr_code(self) -> Optional[str]:
        """Get current QR code as base64 string."""
        if self._qr_code:
            return self._qr_code
        if not self._session:
            await self.connect()
        async with self._session.get(f"{self.base_url}/qr") as resp:
            data = await resp.json()
            self._qr_code = data.get("qr_code")
            return self._qr_code
    
    async def send_message(self, to: str, text: str) -> dict:
        """Send a text message."""
        if not self._session:
            await self.connect()
        async with self._session.post(
            f"{self.base_url}/send",
            json={"to": to, "text": text}
        ) as resp:
            return await resp.json()
    
    async def send_media(self, to: str, media_path: str, caption: str = "") -> dict:
        """Send media file."""
        if not self._session:
            await self.connect()
        # Upload file
        with open(media_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("file", f, filename=Path(media_path).name)
            form.add_field("to", to)
            form.add_field("caption", caption)
            async with self._session.post(f"{self.base_url}/send-media", data=form) as resp:
                return await resp.json()
    
    async def reset_connection(self) -> dict:
        """Reset WhatsApp connection (generate new QR)."""
        if not self._session:
            await self.connect()
        async with self._session.post(f"{self.base_url}/reset") as resp:
            return await resp.json()
    
    async def get_contacts(self) -> dict:
        """Get WhatsApp contacts."""
        if not self._session:
            await self.connect()
        async with self._session.get(f"{self.base_url}/contacts") as resp:
            return await resp.json()
    
    async def get_chats(self) -> dict:
        """Get WhatsApp chats."""
        if not self._session:
            await self.connect()
        async with self._session.get(f"{self.base_url}/chats") as resp:
            return await resp.json()
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def qr_code(self) -> Optional[str]:
        return self._qr_code
    
    async def close(self) -> None:
        """Close connections."""
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws_session:
            await self._ws_session.close()
        if self._session:
            await self._session.close()


class WhatsAppBridgeManager:
    """Manages the WhatsApp Bridge lifecycle (start/stop Node.js server)."""
    
    def __init__(self, bridge_dir: Path):
        self.bridge_dir = Path(bridge_dir)
        self._process: Optional[asyncio.subprocess.Process] = None
        self._client: Optional[WhatsAppBridgeClient] = None
    
    async def start(self, port: int = 8765) -> WhatsAppBridgeClient:
        """Start the Node.js bridge server."""
        # Check if Node.js is available
        try:
            import subprocess
            result = subprocess.run(["node", "--version"], capture_output=True)
            if result.returncode != 0:
                raise RuntimeError("Node.js not found")
        except Exception as e:
            raise RuntimeError(f"Node.js not available: {e}")
        
        # Check if dependencies are installed
        node_modules = self.bridge_dir / "node_modules"
        if not node_modules.exists():
            logger.info("Installing WhatsApp Bridge dependencies...")
            proc = await asyncio.create_subprocess_exec(
                "npm", "install",
                cwd=self.bridge_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"npm install failed: {stderr.decode()}")
        
        # Start the bridge server
        env = {
            **os.environ,
            "MARKXLVIII_WA_PORT": str(port),
            "CYBERBOT_WA_PORT": str(port),
        }
        
        self._process = await asyncio.create_subprocess_exec(
            "node", "server.js",
            cwd=self.bridge_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Wait for server to be ready
        await asyncio.sleep(3)
        
        # Create client
        self._client = WhatsAppBridgeClient(f"http://localhost:{port}")
        await self._client.connect()
        
        return self._client
    
    async def stop(self) -> None:
        """Stop the bridge server."""
        if self._client:
            await self._client.close()
            self._client = None
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None
    
    @property
    def client(self) -> Optional[WhatsAppBridgeClient]:
        return self._client
    
    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None


# Global manager instance
_bridge_manager: Optional[WhatsAppBridgeManager] = None


async def get_whatsapp_bridge(bridge_dir: Optional[Path] = None) -> WhatsAppBridgeManager:
    """Get or create the global WhatsApp Bridge manager."""
    global _bridge_manager
    if _bridge_manager is None:
        if bridge_dir is None:
            from src.jarvis.core.plataforma import get_base_dir
            bridge_dir = get_base_dir() / "whatsapp_bridge"
        _bridge_manager = WhatsAppBridgeManager(bridge_dir)
    return _bridge_manager


async def start_whatsapp_bridge(port: int = 8765, bridge_dir: Optional[Path] = None) -> WhatsAppBridgeClient:
    """Start the WhatsApp Bridge and return the client."""
    manager = await get_whatsapp_bridge(bridge_dir)
    return await manager.start(port)


import os