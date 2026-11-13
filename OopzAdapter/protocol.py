"""Oopz 网关协议层：请求签名、账密登录、WS 信封与 REST 数据面。

Oopz 没有官方开放平台，本层协议形态参考社区逆向成果（MIT:
tangqingfeng7/Oopzbot-SDK），仅作实现参考、不构成依赖。
协议随客户端更新可能变动，接口失败时优先怀疑协议漂移。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

from ErisPulse.Core import client
from ErisPulse.Core.Bases.errors import ClientError

__all__ = [
    "DEFAULT_GATEWAY_URL", "resolve_ws_url", "DEFAULT_HEADERS",
    "EVENT_AUTH", "EVENT_AUTH_RESULT", "EVENT_HEARTBEAT", "EVENT_SUBSCRIBE_AREA_EVENTS",
    "EVENT_SERVER_ID", "EVENT_FRIEND_REQUEST", "EVENT_FRIEND_DELETE",
    "EVENT_PRIVATE_MESSAGE_DELETE", "EVENT_PRIVATE_MESSAGE", "EVENT_MESSAGE_DELETE",
    "EVENT_CHAT_MESSAGE", "EVENT_CHANNEL_VOICE_BAN", "EVENT_CHANNEL_MESSAGE_BAN",
    "EVENT_CHANNEL_DELETE", "EVENT_CHANNEL_UPDATE", "EVENT_USER_LEAVE_VOICE_CHANNEL",
    "EVENT_USER_ENTER_VOICE_CHANNEL", "EVENT_PUBLIC_CHANNEL_CREATE", "EVENT_USER_UPDATE",
    "EVENT_USER_LOGIN_STATE_CHANGED", "EVENT_AREA_UPDATE", "EVENT_MESSAGE_REACTION",
    "EVENT_ROLE_CHANGED", "EVENT_PRIVATE_MESSAGE_EDIT", "EVENT_MESSAGE_EDIT",
    "ATTACHMENT_IMAGE", "ATTACHMENT_AUDIO", "ATTACHMENT_FILE", "STYLE_TAG_IMPORTANT",
    "LOGIN_PATH", "OopzProtocolError", "OopzApiError", "OopzAuthExpired",
    "OopzRateLimited", "OopzTransientError", "OopzLoginError",
    "Signer", "OopzRest", "password_login", "normalize_private_key",
    "encode_frame", "decode_frame", "build_auth_frame", "build_heartbeat_frame",
    "build_subscribe_frame", "is_auth_rejected",
    "compact_json", "timestamp_ms", "timestamp_us", "new_client_message_id",
    "build_message_payload", "mention_token", "MENTION_ALL_TOKEN",
    "image_placeholder", "IMAGE_PLACEHOLDER_RE", "MENTION_TOKEN_RE",
    "guess_image_ext", "guess_image_dimensions", "upload_image_bytes", "decode_jwt_exp",
]

# ==================== 路由与版本 ====================

DEFAULT_GATEWAY_URL = "https://gateway.oopz.cn"

# 网关与 WS 网关成对部署，换网关必须换配套 WS 路由
GATEWAY_WS_ROUTES = {
    DEFAULT_GATEWAY_URL: "wss://ws.oopz.cn",
    "https://gateway1.oopz.cn": "wss://ws1.oopz.cn",
    "https://gateway2.oopz.cn": "wss://ws2.oopz.cn",
    "https://gateway-web1.oopz.cn": "wss://ws-web1.oopz.cn",
}


def resolve_ws_url(gateway_url: str) -> str:
    return GATEWAY_WS_ROUTES.get(gateway_url.rstrip("/"), "wss://ws.oopz.cn")


CLIENT_VERSION = "0.73.817"
APP_VERSION_NUMBER = "73817"

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/json;charset=utf-8",
    "Origin": "https://web.oopz.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
}

LOGIN_PATH = "/client/v1/login/v2/login"

# ==================== WS 事件码 ====================

EVENT_SERVER_ID = 1                 # 握手下发 serverId
EVENT_FRIEND_REQUEST = 2            # 收到好友申请
EVENT_FRIEND_DELETE = 4             # 被删除好友
EVENT_PRIVATE_MESSAGE_DELETE = 6    # 私聊消息撤回
EVENT_PRIVATE_MESSAGE = 7           # 私聊消息
EVENT_MESSAGE_DELETE = 8            # 频道消息撤回
EVENT_CHAT_MESSAGE = 9              # 频道消息
EVENT_CHANNEL_VOICE_BAN = 11        # 频道禁麦
EVENT_CHANNEL_MESSAGE_BAN = 12      # 频道禁言
EVENT_CHANNEL_DELETE = 13           # 删除频道
EVENT_CHANNEL_UPDATE = 18           # 频道设置变更
EVENT_USER_LEAVE_VOICE_CHANNEL = 19 # 退出语音频道
EVENT_USER_ENTER_VOICE_CHANNEL = 20 # 进入语音频道
EVENT_PUBLIC_CHANNEL_CREATE = 25    # 创建频道
EVENT_USER_UPDATE = 26              # 用户信息变更
EVENT_USER_LOGIN_STATE_CHANGED = 27 # 登录/登出
EVENT_AREA_UPDATE = 28              # 域信息变更
EVENT_MESSAGE_REACTION = 32         # 表情回应增撤
EVENT_ROLE_CHANGED = 52             # 身份组变更
EVENT_PRIVATE_MESSAGE_EDIT = 56     # 私聊消息编辑
EVENT_MESSAGE_EDIT = 57             # 频道消息编辑
EVENT_SUBSCRIBE_AREA_EVENTS = 249   # 订阅域事件推送
EVENT_AUTH = 253                    # WS 认证帧
EVENT_AUTH_RESULT = 21              # 认证结果（checkRes=false 即被拒）
EVENT_HEARTBEAT = 254               # 心跳

ATTACHMENT_IMAGE = "IMAGE"
ATTACHMENT_AUDIO = "AUDIO"
ATTACHMENT_FILE = "FILE"
STYLE_TAG_IMPORTANT = "IMPORTANT"

# 禁言时长 → intervalId（平台仅认这几个档位；1 分钟档未见文档不收录）
TEXT_MUTE_INTERVALS = ((5, "2"), (60, "3"), (1440, "4"), (4320, "5"), (10080, "6"))
VOICE_MUTE_INTERVALS = ((5, "8"), (60, "9"), (1440, "10"), (4320, "11"), (10080, "12"))


def pick_mute_interval(minutes: int, voice: bool = False) -> str:
    """把任意分钟数吸附到最近的可用禁言档位。"""
    table = VOICE_MUTE_INTERVALS if voice else TEXT_MUTE_INTERVALS
    return min(table, key=lambda item: abs(item[0] - minutes))[1]


# ==================== 异常 ====================

class OopzProtocolError(Exception):
    """协议层异常基类；payload 为平台原始响应（可为 None）。"""

    def __init__(self, message: str, payload: Any = None):
        super().__init__(message)
        self.payload = payload


class OopzApiError(OopzProtocolError):
    """平台返回业务失败（status 非真）或非 200/429/401/428。"""


class OopzAuthExpired(OopzProtocolError):
    """凭据失效（HTTP 401/428 或 WS 鉴权拒绝），调用方应重登。"""


class OopzRateLimited(OopzProtocolError):
    """限流（HTTP 429）。"""

    def __init__(self, message: str, retry_after: int = 0, payload: Any = None):
        super().__init__(message, payload)
        self.retry_after = retry_after


class OopzTransientError(OopzProtocolError):
    """瞬时故障（网络异常 / 5xx），可重试。"""


class OopzLoginError(OopzProtocolError):
    """登录凭据错误或被风控拦截，重试无意义。"""


# ==================== 基础工具 ====================

def compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def timestamp_us() -> str:
    return str(int(time.time() * 1_000_000))


def new_client_message_id() -> str:
    """客户端消息幂等 ID：微秒时间戳压缩 + 两位随机后缀。"""
    base_id = int(time.time() * 1_000_000) % 10_000_000_000_000
    return str(base_id * 100 + secrets.randbelow(90) + 10)


def decode_jwt_exp(token: str) -> int | None:
    """解出 JWT exp（秒）；不验签，仅用于本地临期判断。"""
    try:
        part = str(token or "").split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part.encode("utf-8")))
        exp = payload.get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def normalize_private_key(pem: str) -> str:
    """容忍用户从终端/JSON 里复制出来的转义与引号。"""
    pem = str(pem or "").strip()
    if len(pem) >= 2 and pem[0] == pem[-1] and pem[0] in ("'", '"'):
        pem = pem[1:-1]
    pem = pem.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
    return pem.strip()


# ==================== 请求签名 ====================

def _load_private_key(pem: str):
    from cryptography.hazmat.primitives import serialization

    pem = normalize_private_key(pem)
    if not pem.startswith("-----BEGIN"):
        raise OopzLoginError(
            "private_key 不是 PEM 私钥（应含 -----BEGIN PRIVATE KEY----- 头），请检查提取方式"
        )
    try:
        return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise OopzLoginError(f"无法加载客户端签名私钥: {exc}") from exc


class Signer:
    """持有客户端签名私钥，产出 Oopz-* 签名请求头。"""

    def __init__(self, private_key_pem: str):
        self.private_key_pem = normalize_private_key(private_key_pem)
        self._key = _load_private_key(self.private_key_pem)

    def sign(self, data: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        signature = self._key.sign(
            data.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def build_headers(
        self,
        *,
        sign_path: str,
        body_str: str,
        device_id: str,
        person_uid: str = "",
        jwt: str = "",
    ) -> dict[str, str]:
        """Oopz-Sign = RSA-SHA256( MD5(path+body)hex + ts_ms )；path 含查询串，
        签名与 Oopz-Time 头必须用同一个时间戳。"""
        ts = timestamp_ms()
        digest = hashlib.md5((sign_path + body_str).encode("utf-8")).hexdigest()
        headers = {
            **DEFAULT_HEADERS,
            "Oopz-Sign": self.sign(digest + ts),
            "Oopz-Request-Id": str(uuid.uuid4()),
            "Oopz-Time": ts,
            "Oopz-App-Version-Number": APP_VERSION_NUMBER,
            "Oopz-Channel": "Web",
            "Oopz-Device-Id": device_id,
            "Oopz-Platform": "windows",
            "Oopz-Web": "true",
        }
        if person_uid:
            headers["Oopz-Person"] = person_uid
        if jwt:
            headers["Oopz-Signature"] = jwt
        return headers


# 服务端密码加密公钥（RSA-OAEP 模数，JWK n 字段 base64url）。
# 公开材料，与官方 Web 客户端用于加密登录密码的密钥一致。
_PASSWORD_MODULUS_B64URL = (
    "wf935Evk12yBMDq5u-2WGXG7rI80-WTu5BQXVnMm2HRZWKmOEdjW5XG4WhsbidDF2hhxO4emVqRlEE-EAZ8Ro936fk-oDvuYcLGbe_AUhgkb_uCldl32inF7dYXuONb-eigNSlGjapv8Ef__rFi0srg1dJg7_j8IQBm2RC5Hgd98-7QKokpjZHQmi-WpYAyaW7rZk1s6Ac6vZiAAq8HEa-eaEFTeQBcxrgYMwL65FfNFomh3Xvd1tMMtTx-dp4dfARfJB8UHhC8_xsUlHp14KYg4YRq9eRelnM_pV1ZXEAjPCbl6hwEsGrxWz40m-OeVsEZy-XGcoeeZfZ5dbTsicQ"
)


def encrypt_password(password: str) -> str:
    """登录密码 RSA-OAEP(SHA-256/MGF1-SHA256) 加密后 base64。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    raw = base64.urlsafe_b64decode(_PASSWORD_MODULUS_B64URL + "=" * ((4 - len(_PASSWORD_MODULUS_B64URL) % 4) % 4))
    public_key = rsa.RSAPublicNumbers(e=65537, n=int.from_bytes(raw, "big")).public_key()
    encrypted = public_key.encrypt(
        password.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(encrypted).decode("utf-8")


# ==================== 登录 ====================

def _extract_message(payload: Any, default: str = "") -> str:
    if not isinstance(payload, dict):
        return default
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for source in (payload, data):
        for key in ("message", "msg", "error", "errorMessage", "reason"):
            value = source.get(key)
            if value:
                return str(value)
    return default


async def password_login(
    gateway_url: str,
    phone: str,
    password: str,
    signer: Signer,
    device_id: str,
) -> dict:
    """账密登录，成功返回 {"person_uid", "jwt", "device_id", "expires_at"}。

    凭据错误/风控抛 OopzLoginError；限流抛 OopzRateLimited；
    网络或 5xx 抛 OopzTransientError。
    """
    phone = str(phone or "").strip()
    password = str(password or "")
    if not phone or not password:
        raise OopzLoginError("手机号和密码不能为空")

    body_payload = {
        "auto": True,
        "code": encrypt_password(password),
        "loginType": "PASSWORD",
        "phone": phone,
        "autoRegister": True,
        "deviceId": device_id,
        "deviceRam": "TBD",
        "deviceProcessor": "0",
        "loggedIn": device_id,
        "osEdition": "web",
        "osVersion": "web/BrowserName.chrome",
        "resolution": "TBD",
        "graphics": "TBD",
        "clientVersion": CLIENT_VERSION,
    }
    body_str = compact_json(body_payload)
    headers = signer.build_headers(
        sign_path=LOGIN_PATH, body_str=body_str, device_id=device_id,
    )

    try:
        resp = await client.post(
            gateway_url.rstrip("/") + LOGIN_PATH,
            data=body_str.encode("utf-8"),
            headers=headers,
            timeout=20,
        )
    except ClientError as exc:
        raise OopzTransientError(f"登录请求失败: {exc}") from exc

    if resp.status == 429:
        raise OopzRateLimited("登录接口限流 (HTTP 429)", retry_after=_retry_after_of(resp))
    if resp.status >= 500:
        raise OopzTransientError(f"登录服务暂时不可用 (HTTP {resp.status})")

    try:
        payload = await resp.json()
    except Exception as exc:
        raise OopzLoginError(f"登录接口返回非 JSON 响应 (HTTP {resp.status})", payload=None) from exc

    if resp.status >= 400 or not isinstance(payload, dict) or not _coerce_bool(payload.get("status")):
        raise OopzLoginError(
            _extract_message(payload) or f"登录失败 (HTTP {resp.status})", payload=payload,
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise OopzLoginError("登录成功但响应 data 格式异常", payload=payload)

    person_uid = str(data.get("uid") or "")
    jwt = str(data.get("signature") or "")
    if not person_uid or not jwt:
        raise OopzLoginError("登录成功但未返回 uid/signature", payload=payload)

    return {
        "person_uid": person_uid,
        "jwt": jwt,
        "device_id": device_id,
        "expires_at": str(data.get("signatureNearExpired") or ""),
    }


def _retry_after_of(resp) -> int:
    try:
        headers = resp.headers or {}
        value = headers.get("Retry-After") if hasattr(headers, "get") else None
        seconds = int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
    return seconds if seconds > 0 else 0


def _coerce_bool(value: Any) -> bool:
    """平台 status 字段可能是 bool/整数/字符串（含 "false"/"0"），严格判真。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text not in ("", "false", "0", "null", "none")


# ==================== WS 信封 ====================

def encode_frame(event: int, body: dict) -> str:
    """客户端出站帧：body 双层序列化为 JSON 字符串。"""
    return compact_json({
        "time": timestamp_ms(),
        "body": json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        "event": event,
    })


def decode_frame(raw: Any) -> tuple[int, dict]:
    """入站帧解包：envelope → event int + body dict（容忍 body 双层序列化）。"""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError):
        return -1, {}
    if not isinstance(envelope, dict):
        return -1, {}
    try:
        event = int(envelope.get("event", -1))
    except (TypeError, ValueError):
        event = -1
    body = envelope.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (TypeError, ValueError):
            body = {}
    return event, body if isinstance(body, dict) else {}


def build_auth_frame(person_uid: str, device_id: str, jwt: str) -> str:
    return encode_frame(EVENT_AUTH, {
        "person": person_uid,
        "deviceId": device_id,
        "signature": jwt,
        "deviceName": device_id,
        "platformName": "web",
        "reconnect": 0,
    })


def build_heartbeat_frame(person_uid: str) -> str:
    return encode_frame(EVENT_HEARTBEAT, {"person": person_uid})


def build_subscribe_frame(areas: list[str], uid: str) -> str:
    clean = [str(a).strip() for a in areas if str(a).strip()]
    if not clean:
        return ""
    return encode_frame(EVENT_SUBSCRIBE_AREA_EVENTS, {"areas": clean, "type": 1, "uid": uid})


def is_auth_rejected(event: int, body: dict) -> bool:
    """event=21 且 checkRes 为假 → 凭据被拒，随后服务端会以 1006 断开。"""
    return event == EVENT_AUTH_RESULT and body.get("checkRes") is False


# ==================== 消息文本约定 ====================

MENTION_ALL_TOKEN = "(met)All(met)"
MENTION_TOKEN_RE = re.compile(r"\(met\)(.+?)\(met\)")
IMAGE_PLACEHOLDER_RE = re.compile(r"!\[IMAGEw(\d+)h(\d+)\]\(([^)\s]+)\)(?:\r?\n)*")


def mention_token(person_uid: str) -> str:
    return f"(met){person_uid}(met)"


def image_placeholder(width: int, height: int, file_key: str) -> str:
    return f"![IMAGEw{width}h{height}]({file_key})\n"


def build_message_payload(
    *,
    text: str,
    area: str = "",
    channel: str = "",
    target: str = "",
    attachments: list[dict] | None = None,
    mention_list: list[dict] | None = None,
    is_mention_all: bool = False,
    style_tags: list[str] | None = None,
    reference_message_id: str | None = None,
    animated: bool = False,
    display_name: str = "",
    duration: int = 0,
    client_message_id: str | None = None,
) -> dict:
    """构造 v2 发送体（外层 {"message": {...}}）。"""
    payload = {
        "area": area,
        "channel": channel,
        "target": target,
        "text": text,
        "content": text,
        "clientMessageId": client_message_id or new_client_message_id(),
        "timestamp": timestamp_us(),
        "isMentionAll": bool(is_mention_all),
        "mentionList": list(mention_list or []),
        "styleTags": list(style_tags or []),
        "referenceMessageId": reference_message_id,
        "animated": bool(animated),
        "displayName": display_name,
        "duration": int(duration),
        "attachments": list(attachments or []),
    }
    return {"message": payload}


# ==================== 图片扩展名嗅探 ====================

_MAGIC_EXTS = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),  # RIFF....WEBP
)


def guess_image_ext(payload: bytes, filename: str = "") -> str:
    lower = (filename or "").lower()
    for ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
        if lower.endswith("." + ext):
            return "jpg" if ext == "jpeg" else ext
    head = bytes(payload[:16] or b"")
    for magic, ext in _MAGIC_EXTS:
        if head.startswith(magic) and ext != "webp":
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return "png"


def guess_image_dimensions(payload: bytes) -> tuple:
    """从图片头嗅探 (width, height)；无法识别返回 (0, 0)。"""
    data = bytes(payload or b"")
    # PNG: IHDR 在固定偏移，宽高各 4 字节大端
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    # GIF: 逻辑屏幕尺寸，2×2 字节小端
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    # JPEG: 扫描 SOF0-SOF15 段
    if data[:2] == b"\xff\xd8":
        pos = 2
        while pos + 9 < len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker = data[pos + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            seg_len = int.from_bytes(data[pos + 2:pos + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC) and pos + 9 <= len(data):
                height = int.from_bytes(data[pos + 5:pos + 7], "big")
                width = int.from_bytes(data[pos + 7:pos + 9], "big")
                return width, height
            pos += 2 + seg_len
    # WebP: VP8X / VP8 / VP8L 粗嗅探
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            width = 1 + int.from_bytes(data[24:27], "little")
            height = 1 + int.from_bytes(data[27:30], "little")
            return width, height
    return 0, 0


# ==================== REST 数据面 ====================

class OopzRest:
    """单账户 REST 数据面：签名头、状态码语义与 data 字段解包。

    - 429 → OopzRateLimited（带 Retry-After）
    - 401/428 → OopzAuthExpired（调用方决定重登）
    - 网络/5xx → OopzTransientError
    - 业务失败/其余非 200 → OopzApiError（payload 保留原始响应）
    """

    AUTH_STATUS_CODES = frozenset({401, 428})

    def __init__(
        self,
        gateway_url: str,
        signer: Signer,
        device_id: str,
        person_uid: str = "",
        jwt: str = "",
        timeout: float = 30,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        self.signer = signer
        self.device_id = device_id
        self.person_uid = person_uid
        self.jwt = jwt
        self.timeout = timeout

    def update_session(self, person_uid: str, jwt: str) -> None:
        self.person_uid = person_uid
        self.jwt = jwt

    async def request_data(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: Any = None,
    ) -> Any:
        """发签名请求并解出响应 data 字段。"""
        method = method.upper()
        sign_path = f"{path}?{urlencode(params)}" if params else path

        if body is not None:
            body_str = compact_json(body)
            data = body_str.encode("utf-8")
        elif method in ("POST", "PUT", "PATCH"):
            body_str = "{}"
            data = b"{}"
        else:
            body_str = ""
            data = None

        headers = self.signer.build_headers(
            sign_path=sign_path,
            body_str=body_str,
            device_id=self.device_id,
            person_uid=self.person_uid,
            jwt=self.jwt,
        )

        try:
            resp = await client.request(
                method,
                self.gateway_url + path,
                params=params,
                data=data,
                headers=headers,
                timeout=self.timeout,
            )
        except ClientError as exc:
            raise OopzTransientError(f"请求失败: {exc}") from exc

        if resp.status == 429:
            raise OopzRateLimited("接口限流 (HTTP 429)", retry_after=_retry_after_of(resp))
        if resp.status in self.AUTH_STATUS_CODES:
            raise OopzAuthExpired(f"凭据失效 (HTTP {resp.status})")
        if resp.status >= 500:
            raise OopzTransientError(f"服务暂时不可用 (HTTP {resp.status})")

        try:
            payload = await resp.json()
        except Exception as exc:
            raise OopzApiError(f"响应非 JSON (HTTP {resp.status})", payload=None) from exc

        if resp.status != 200:
            raise OopzApiError(
                _extract_message(payload) or f"HTTP {resp.status}", payload=payload,
            )
        if not isinstance(payload, dict) or not _coerce_bool(payload.get("status")):
            raise OopzApiError(
                _extract_message(payload) or "Oopz API 请求失败", payload=payload,
            )
        if "data" not in payload:
            raise OopzApiError("响应缺少 data 字段", payload=payload)
        return payload["data"]

    # ---- 高层封装（核心端点，供 Core.py 与扩展动作共用）----

    async def open_private_session(self, target: str) -> str:
        data = await self.request_data(
            "PATCH", "/client/v1/chat/v1/to", params={"target": str(target)},
        )
        session_id = str((data or {}).get("sessionId") or "")
        if not session_id:
            raise OopzApiError(f"打开私信会话失败: target={target}", payload=data)
        return session_id

    async def send_channel_message(self, message_payload: dict) -> dict:
        return await self.request_data(
            "POST", "/im/session/v2/sendGimMessage", body=message_payload,
        )

    async def send_private_message(self, message_payload: dict) -> dict:
        return await self.request_data(
            "POST", "/im/session/v2/sendImMessage", body=message_payload,
        )

    async def recall_channel_message(self, message_id: str, area: str, channel: str) -> dict:
        return await self.request_data("POST", "/im/session/v1/recallGim", body={
            "area": area,
            "channel": channel,
            "messageId": str(message_id),
            "timestamp": timestamp_us(),
            "target": "",
        })

    async def recall_private_message(self, message_id: str, channel: str, target: str) -> dict:
        return await self.request_data("POST", "/im/session/v1/recallIm", body={
            "area": "",
            "channel": channel,
            "messageId": str(message_id),
            "timestamp": timestamp_us(),
            "target": target,
        })

    async def upload_image_bytes(
        self,
        payload: bytes,
        ext: str,
        animated: bool = False,
        display_name: str = "",
        width: int = 0,
        height: int = 0,
    ) -> dict:
        """COS 签名票上传图片，返回附件 payload（可直接塞进 attachments）。"""
        ticket = await self.request_data(
            "PUT", "/rtc/v1/cos/v1/signedUploadUrl",
            body={"type": ATTACHMENT_IMAGE, "ext": ext},
        )
        ticket = ticket or {}
        file_key = str(ticket.get("file") or "")
        signed_url = str(ticket.get("signedUrl") or "")
        url = str(ticket.get("url") or "")
        if not file_key or not signed_url or not url:
            raise OopzApiError("上传票缺少 file/signedUrl/url", payload=ticket)

        try:
            resp = await client.put(
                signed_url,
                data=payload,
                headers={"Content-Type": "application/octet-stream"},
                timeout=120,
            )
        except ClientError as exc:
            raise OopzTransientError(f"图片上传失败: {exc}") from exc
        if resp.status not in (200, 201):
            raise OopzApiError(f"图片上传失败 (HTTP {resp.status})", payload=None)

        attachment = {
            "fileKey": file_key,
            "url": url,
            "attachmentType": ATTACHMENT_IMAGE,
            "displayName": display_name or "image",
            "fileSize": len(payload),
            "animated": bool(animated),
            "hash": "",
        }
        if width > 0:
            attachment["width"] = int(width)
        if height > 0:
            attachment["height"] = int(height)
        return attachment
