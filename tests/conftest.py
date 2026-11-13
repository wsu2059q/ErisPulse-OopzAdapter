import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SDK_SRC = ROOT.parent.parent / "SDK" / "ErisPulse" / "src"
if SDK_SRC.exists():
    try:
        import ErisPulse  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(SDK_SRC))

import base64

import pytest


@pytest.fixture(scope="session")
def private_key_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("utf-8")


def make_account(private_key_pem: str, name: str = "main"):
    from OopzAdapter.Core import OopzBotConfig

    return OopzBotConfig(
        login_type="token",
        person_uid="p1",
        jwt_signature="jwt-token",
        device_id="dev-1",
        private_key=private_key_pem,
        name=name,
    )


def make_adapter(private_key_pem: str):
    """绕过 __init__ 构造适配器实例（桩模式，与 YunhuAdapter 测试一致）"""
    from OopzAdapter import protocol
    from OopzAdapter.Core import OopzAdapter

    a = OopzAdapter.__new__(OopzAdapter)
    a._platform = "oopz"
    a.adapter = type("A", (), {"emit": None})()
    a._accounts_data = {"main": make_account(private_key_pem)}
    a._bot_id_registry = {}
    a._auth = {"main": {"person_uid": "p1", "jwt": "jwt-token", "device_id": "dev-1"}}
    a._rest = {}
    a._channel_area = {}
    a._area_cache = {}
    a._dm_sessions = {}
    a._gateway = protocol.DEFAULT_GATEWAY_URL
    a._heartbeat_interval = 10
    a._is_running = False
    a._current_api_account = ""
    a.logger = type("L", (), {
        "info": staticmethod(lambda *a2, **k: None),
        "warning": staticmethod(lambda *a2, **k: None),
        "error": staticmethod(lambda *a2, **k: None),
        "debug": staticmethod(lambda *a2, **k: None),
    })()
    a._register_api_routes()
    return a


class RestStub:
    """OopzRest 桩：按路径分发预置响应，记录全部调用"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def _record(self, method, path, params, body):
        self.calls.append({"method": method, "path": path, "params": params, "body": body})

    def last(self, path):
        for call in reversed(self.calls):
            if call["path"] == path:
                return call
        raise AssertionError(f"未请求过端点 {path}；实际请求: {[c['path'] for c in self.calls]}")

    async def request_data(self, method, path, params=None, body=None):
        self._record(method, path, params, body)
        handler = self.routes.get(path)
        if handler is None:
            from OopzAdapter import protocol
            raise protocol.OopzApiError(f"桩未预置端点 {path}")
        result = handler(params, body)
        return result

    async def open_private_session(self, target):
        self._record("PATCH", "/client/v1/chat/v1/to", {"target": target}, None)
        return f"session-{target}"

    async def send_channel_message(self, payload):
        self._record("POST", "/im/session/v2/sendGimMessage", None, payload)
        return {"messageId": "msg-new-1", "timestamp": "1700000000000000"}

    async def send_private_message(self, payload):
        self._record("POST", "/im/session/v2/sendImMessage", None, payload)
        return {"messageId": "msg-new-2", "timestamp": "1700000000000000"}

    async def recall_channel_message(self, message_id, area, channel):
        self._record("POST", "/im/session/v1/recallGim", None, {
            "messageId": message_id, "area": area, "channel": channel,
        })
        return {}

    async def recall_private_message(self, message_id, channel, target):
        self._record("POST", "/im/session/v1/recallIm", None, {
            "messageId": message_id, "channel": channel, "target": target,
        })
        return {}

    async def upload_image_bytes(self, payload, ext, animated=False, display_name="", width=0, height=0):
        self._record("PUT", "/rtc/v1/cos/v1/signedUploadUrl", None, {"ext": ext, "width": width, "height": height})
        return {"fileKey": "uploaded-key", "url": "https://img.example/up.png", "displayName": display_name}


def make_channel_message(message_id="m1", person="u1", text="hi", area="A1", channel="C1", extra=None) -> dict:
    msg = {
        "type": "TEXT",
        "data": {
            "target": "",
            "area": area,
            "channel": channel,
            "type": "TEXT",
            "messageId": message_id,
            "timestamp": "1700000000000000",
            "person": person,
            "content": text,
            "text": text,
            "mentionList": [],
            "isMentionAll": False,
            "styleTags": [],
            "referenceMessageId": None,
            "attachments": [],
        },
    }
    if extra:
        msg["data"].update(extra)
    return {"event": 9, "body": msg}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
