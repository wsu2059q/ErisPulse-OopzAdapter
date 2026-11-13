"""ErisPulse Oopz 适配器核心。

Oopz（oopz.cn）无官方机器人平台，本适配器以用户账号身份接入（小号），
协议层见 protocol.py。多账户：[Oopz_Adapter.accounts.<name>]，
凭据「账密二选一」：password（手机号+密码+客户端私钥）或 token（四件套）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ErisPulse import sdk
from ErisPulse.Core import BaseAdapter, client
from ErisPulse.Core.Bases import BaseConfig, BaseI18n, BotAccountConfig, I18nKey
from ErisPulse.Core.Bases.websocket import WSMessage

try:
    from ErisPulse.runtime.tasks import spawn_background
except ImportError:  # pragma: no cover
    spawn_background = None

from . import protocol
from .Converter import OopzConverter

__version__ = "0.1.0"

# 软依赖的框架最低版本（运行时检测，仅提示不强制）
MIN_FRAMEWORK_VERSION = (2, 7, 1)

_LOGIN_REFRESH_LEEWAY = 300  # JWT 临期提前量（秒）


@dataclass
class OopzGlobalConfig(BaseConfig):
    """Oopz 适配器全局配置"""

    gateway_url: str = field(
        default=protocol.DEFAULT_GATEWAY_URL,
        metadata={
            "description": {"i18n": "oopz_adapter.gateway_url", "default": "Gateway base URL"},
            "required": False,
            "ui": {"widget": "text", "group": "connection", "order": 1},
        },
    )
    heartbeat_interval: int = field(
        default=10,
        metadata={
            "description": {"i18n": "oopz_adapter.heartbeat_interval", "default": "WS heartbeat interval (seconds)"},
            "required": False,
            "ui": {"widget": "number", "group": "connection", "order": 2},
        },
    )
    auto_subscribe_areas: bool = field(
        default=True,
        metadata={
            "description": {"i18n": "oopz_adapter.auto_subscribe_areas", "default": "Auto subscribe joined areas on connect"},
            "required": False,
            "ui": {"widget": "switch", "group": "connection", "order": 3},
        },
    )
    subscribe_areas: list = field(
        default_factory=list,
        metadata={
            "description": {"i18n": "oopz_adapter.subscribe_areas", "default": "Extra area IDs to subscribe"},
            "required": False,
            "ui": {"widget": "list", "group": "connection", "order": 4},
        },
    )
    ignore_self: bool = field(
        default=True,
        metadata={
            "description": {"i18n": "oopz_adapter.ignore_self", "default": "Ignore messages sent by the account itself"},
            "required": False,
            "ui": {"widget": "switch", "group": "basic", "order": 5},
        },
    )


@dataclass
class OopzBotConfig(BotAccountConfig):
    """Oopz 账户配置（账密二选一）"""

    login_type: str = field(
        default="password",
        metadata={
            "description": {"i18n": "oopz_adapter.login_type", "default": "Credential mode (password recommended; token only as fallback when password login is risk-controlled)"},
            "required": True,
            "ui": {
                "widget": "select",
                "group": "credentials",
                "order": 1,
                "options": [{"label": "账号密码（推荐）", "value": "password"}, {"label": "Token 四件套（备用）", "value": "token"}],
            },
        },
    )
    phone: str = field(
        default="",
        metadata={
            "description": {"i18n": "oopz_adapter.phone", "default": "Phone number (password mode)"},
            "required": False,
            "ui": {
                "widget": "text",
                "group": "credentials",
                "order": 2,
                "disabled_if": {"field": "login_type", "op": "eq", "value": "token"},
            },
        },
    )
    password: str = field(
        default="",
        metadata={
            "description": {"i18n": "oopz_adapter.password", "default": "Password (password mode)"},
            "required": False,
            "secret": True,
            "ui": {
                "widget": "password",
                "group": "credentials",
                "order": 3,
                "disabled_if": {"field": "login_type", "op": "eq", "value": "token"},
            },
        },
    )
    private_key: str = field(
        default="",
        metadata={
            "description": {"i18n": "oopz_adapter.private_key", "default": "Client signing private key (see README for extraction)"},
            "required": True,
            "secret": True,
            "ui": {"widget": "password", "group": "credentials", "order": 4},
        },
    )
    person_uid: str = field(
        default="",
        metadata={
            "description": {"i18n": "oopz_adapter.person_uid", "default": "Account UID (token mode)"},
            "required": False,
            "ui": {
                "widget": "text",
                "group": "credentials",
                "order": 5,
                "disabled_if": {"field": "login_type", "op": "eq", "value": "password"},
            },
        },
    )
    jwt_signature: str = field(
        default="",
        metadata={
            "description": {"i18n": "oopz_adapter.jwt_signature", "default": "JWT signature (token mode)"},
            "required": False,
            "secret": True,
            "ui": {
                "widget": "password",
                "group": "credentials",
                "order": 6,
                "disabled_if": {"field": "login_type", "op": "eq", "value": "password"},
            },
        },
    )
    device_id: str = field(
        default="",
        metadata={
            "description": {"i18n": "oopz_adapter.device_id", "default": "Device ID (token mode required; auto-generated in password mode)"},
            "required": False,
            "ui": {
                "widget": "text",
                "group": "credentials",
                "order": 7,
                "disabled_if": {"field": "login_type", "op": "eq", "value": "password"},
            },
        },
    )


class OopzAdapter(BaseAdapter):
    """Oopz 用户账号适配器（非官方接口，建议使用小号）"""

    ConfigClass = OopzGlobalConfig
    AccountConfigClass = OopzBotConfig
    _platform = "oopz"

    # 未实现/不适用的 OneBot12 标准动作 → retcode 10002
    _UNSUPPORTED_STANDARD_ACTIONS = frozenset({
        "send_message", "upload_file", "get_latest_events",
        "set_group_name", "set_group_admin", "set_group_leave", "set_group_card",
        "leave_channel", "leave_group",
    })

    class I18nClass(BaseI18n):
        """Oopz 适配器翻译键声明"""

        gateway_url: I18nKey = I18nKey(default="Gateway base URL", zh_CN="网关地址", en="Gateway base URL")
        heartbeat_interval: I18nKey = I18nKey(default="WS heartbeat interval (seconds)", zh_CN="WS 心跳间隔（秒）", en="WS heartbeat interval (seconds)")
        auto_subscribe_areas: I18nKey = I18nKey(default="Auto subscribe joined areas on connect", zh_CN="连接后自动订阅已加入的域", en="Auto subscribe joined areas on connect")
        subscribe_areas: I18nKey = I18nKey(default="Extra area IDs to subscribe", zh_CN="额外订阅的域 ID 列表", en="Extra area IDs to subscribe")
        ignore_self: I18nKey = I18nKey(default="Ignore messages sent by the account itself", zh_CN="忽略账号自己发送的消息", en="Ignore messages sent by the account itself")
        login_type: I18nKey = I18nKey(default="Credential mode (password recommended; token only as fallback when password login is risk-controlled)", zh_CN="凭据模式（账密推荐；token 仅作密码登录被风控时的备用）", en="Credential mode (password recommended; token only as fallback when password login is risk-controlled)")
        phone: I18nKey = I18nKey(default="Phone number (password mode)", zh_CN="手机号（账密模式）", en="Phone number (password mode)")
        password: I18nKey = I18nKey(default="Password (password mode)", zh_CN="密码（账密模式）", en="Password (password mode)")
        private_key: I18nKey = I18nKey(default="Client signing private key (see README for extraction)", zh_CN="客户端签名私钥（提取方式见 README）", en="Client signing private key (see README for extraction)")
        person_uid: I18nKey = I18nKey(default="Account UID (token mode)", zh_CN="账号 UID（Token 模式）", en="Account UID (token mode)")
        jwt_signature: I18nKey = I18nKey(default="JWT signature (token mode)", zh_CN="JWT 签名（Token 模式）", en="JWT signature (token mode)")
        device_id: I18nKey = I18nKey(default="Device ID (token mode required; auto-generated in password mode)", zh_CN="设备 ID（Token 模式必填；账密模式自动生成）", en="Device ID (token mode required; auto-generated in password mode)")

    class EventMixin:
        """事件便捷访问（挂载到 OneBot12 事件对象上）"""

        def get_raw_event(self) -> dict:
            return getattr(self, "oopz_raw", None)

        def get_area_id(self) -> str:
            return getattr(self, "oopz_area_id", "") or getattr(self, "guild_id", "")

        def get_edited_text(self) -> str:
            return getattr(self, "oopz_edited_text", "")

        def get_emoji(self) -> str:
            return getattr(self, "oopz_emoji", "")

        def get_reaction_type(self) -> str:
            return getattr(self, "oopz_reaction_type", "")

        def get_voice_persons(self) -> list:
            return getattr(self, "oopz_persons", [])

    # ==================== 账户与会话 ====================

    def _get_config_key(self) -> str:
        return "Oopz_Adapter"

    def _load_accounts(self) -> dict:
        from ErisPulse.Core.config import config as config_mgr
        from ErisPulse.runtime.config_schema import dict_to_dataclass

        key = f"{self._get_config_key()}.accounts"
        data = config_mgr.getConfig(key)
        if not isinstance(data, dict):
            return {}
        accounts: Dict[str, Any] = {}
        for name, account_data in data.items():
            if not isinstance(account_data, dict):
                continue
            instance = dict_to_dataclass(OopzBotConfig, dict(account_data))
            instance.name = name
            missing = self._missing_credential(instance)
            if missing:
                self._get_logger().error(f"账户 {name} 缺少凭据字段 {missing}，已跳过")
                continue
            accounts[name] = instance
        return accounts

    @staticmethod
    def _missing_credential(account: OopzBotConfig) -> str:
        if not getattr(account, "private_key", ""):
            return "private_key"
        login_type = getattr(account, "login_type", "password")
        if login_type == "password":
            if not getattr(account, "phone", ""):
                return "phone"
            if not getattr(account, "password", ""):
                return "password"
            return ""
        if not getattr(account, "person_uid", ""):
            return "person_uid"
        if not getattr(account, "jwt_signature", ""):
            return "jwt_signature"
        if not getattr(account, "device_id", ""):
            return "device_id"
        return ""

    def __init__(self, sdk_instance=None):
        super().__init__(sdk_instance)
        self.adapter = sdk.adapter
        cfg = self.cfg
        self._gateway = (cfg.gateway_url or protocol.DEFAULT_GATEWAY_URL).rstrip("/")
        self.convert = OopzConverter().convert
        self._signers: Dict[str, protocol.Signer] = {}
        self._rest: Dict[str, protocol.OopzRest] = {}
        self._auth: Dict[str, dict] = {}
        self._ws_tasks: Dict[str, Any] = {}
        self._ws_conns: Dict[str, Any] = {}
        self._channel_area: Dict[str, str] = {}
        self._area_cache: Dict[str, dict] = {}      # 账户 → {"areas": [...], "ts": float}
        self._dm_sessions: Dict[str, Dict[str, str]] = {}
        self._api_routes: Dict[str, Any] = {}
        self._extension_routes: Dict[str, Any] = {}
        self._required_params: Dict[str, tuple] = {}
        self._current_api_account = ""
        self._heartbeat_interval = max(5, int(cfg.heartbeat_interval or 10))
        self._is_running = False
        self._register_api_routes()
        self._check_framework_version()
        self._get_logger().info(f"OopzAdapter v{__version__} 已加载")

    @staticmethod
    def _parse_version(version: str):
        parts = []
        for chunk in str(version or "").split("."):
            digits = ""
            for ch in chunk:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            parts.append(int(digits or 0))
        return tuple(parts[:3])

    def _check_framework_version(self):
        try:
            from ErisPulse import __version__ as fw_version
            if self._parse_version(fw_version) < MIN_FRAMEWORK_VERSION:
                self._get_logger().warning(
                    f"当前 ErisPulse 版本 {fw_version} 低于建议版本 {MIN_FRAMEWORK_VERSION}，"
                    "部分框架能力（账户解析/事件账户绑定）可能不可用"
                )
        except Exception:
            pass

    def on_config_update(self, old_config, new_config):
        try:
            gateway = (new_config.gateway_url or protocol.DEFAULT_GATEWAY_URL).rstrip("/")
            if gateway != self._gateway:
                self._gateway = gateway
                self._get_logger().warning("网关地址已更新，重启框架后对已建立连接生效")
        except Exception:
            pass

    # ==================== 登录与会话维护 ====================

    def _new_device_id(self, name: str, device_id: str) -> str:
        if device_id:
            return device_id
        device_id = str(uuid.uuid4())
        try:
            from ErisPulse.Core.config import config as config_mgr
            config_mgr.setConfig(f"{self._get_config_key()}.accounts.{name}.device_id", device_id)
        except Exception:
            pass
        return device_id

    async def _login_account(self, name: str, account: OopzBotConfig) -> dict:
        """账密登录并登记会话；token 模式直接登记配置值。"""
        login_type = getattr(account, "login_type", "password")
        if login_type == "password":
            device_id = self._new_device_id(name, getattr(account, "device_id", ""))
            creds = await protocol.password_login(
                self._gateway,
                account.phone,
                account.password,
                self._signers[name],
                device_id,
            )
            auth = {
                "person_uid": creds["person_uid"],
                "jwt": creds["jwt"],
                "device_id": creds["device_id"],
            }
        else:
            auth = {
                "person_uid": str(account.person_uid),
                "jwt": str(account.jwt_signature),
                "device_id": str(account.device_id),
            }
        self._auth[name] = auth
        rest = self._rest.get(name)
        if rest is not None:
            rest.update_session(auth["person_uid"], auth["jwt"])
        try:
            self.update_bot_id(name, auth["person_uid"])
        except Exception:
            pass
        return auth

    async def _prepare_account(self, name: str, account: OopzBotConfig) -> dict:
        signer = self._signers.get(name)
        if signer is None:
            signer = protocol.Signer(account.private_key)
            self._signers[name] = signer
        if name not in self._rest:
            self._rest[name] = protocol.OopzRest(
                self._gateway, signer, str(getattr(account, "device_id", "") or ""),
            )
        return await self._login_account(name, account)

    def _jwt_near_expiry(self, name: str) -> bool:
        auth = self._auth.get(name)
        if not auth:
            return True
        exp = protocol.decode_jwt_exp(auth.get("jwt", ""))
        if exp is None:
            return False  # 解不出 exp 时按长期有效处理，交给 401 触发续期
        return exp - _LOGIN_REFRESH_LEEWAY <= time.time()

    async def _ensure_session(self, name: str, account: Optional[OopzBotConfig]) -> dict:
        if name not in self._auth or (account is not None and account.login_type == "password" and self._jwt_near_expiry(name)):
            if account is None:
                raise protocol.OopzLoginError(f"账户 {name} 尚未建立会话")
            await self._prepare_account(name, account)
        return self._auth[name]

    def _account_for(self, name: str) -> Optional[OopzBotConfig]:
        return (self._accounts_data or {}).get(name)

    async def _resolve_send_account(self, account_id: Optional[str]):
        """Send/Request 链路的账户解析：失败时抛 _AccountError。

        仅校验账户存在；REST 会话缺失由 _guarded 在调用时兜底。
        """
        try:
            name, account = self._resolve_account(account_id)
        except ValueError as exc:
            raise _AccountError(str(exc)) from exc
        if not name:
            raise _AccountError(f"账户 {account_id} 未解析")
        return name, account

    async def _guarded(self, name: str, account: Optional[OopzBotConfig], factory, retry_transient: bool = True):
        """统一执行 REST 调用：401/428 自动重登一次，429/瞬时故障限次重试。

        返回 ("ok", 平台原始数据) 或 ("error", 标准错误响应)。
        """
        for attempt in range(2):
            try:
                rest = self._rest.get(name)
                if rest is None:
                    raise protocol.OopzLoginError(f"账户 {name} 尚未建立会话")
                return "ok", await factory(rest)
            except protocol.OopzAuthExpired:
                if attempt == 0 and account is not None and getattr(account, "login_type", "") == "password":
                    try:
                        await self._login_account(name, account)
                        continue
                    except protocol.OopzProtocolError as exc:
                        return "error", self.make_error(34100, f"凭据失效且重新登录失败: {exc}")
                return "error", self.make_error(34100, "凭据失效，且当前模式无法自动重新登录（token 模式请更新 jwt_signature）")
            except protocol.OopzRateLimited as exc:
                wait = exc.retry_after or 2
                if attempt == 0 and wait <= 30:
                    await asyncio.sleep(wait)
                    continue
                return "error", self.make_error(33001, f"接口限流: {exc}")
            except protocol.OopzTransientError as exc:
                if attempt == 0 and retry_transient:
                    await asyncio.sleep(1)
                    continue
                return "error", self.make_error(33001, str(exc))
            except protocol.OopzApiError as exc:
                return "error", self.make_error(34100, str(exc), raw=exc.payload)
            except _AccountError as exc:
                return "error", self.make_error(34005, str(exc))
            except Exception as exc:  # 框架 client 网络异常兜底
                return "error", self.make_error(33001, f"网络异常: {exc}")
        return "error", self.make_error(33001, "请求重试次数耗尽")

    # ==================== 域/频道解析 ====================

    def _remember_channel_area(self, body: dict, msg: Optional[dict] = None):
        channel = str(body.get("channel") or "")
        area = str(body.get("area") or "")
        if not channel and msg is not None:
            channel = str(msg.get("channel") or "")
            area = str(msg.get("area") or "")
        if channel and area:
            self._channel_area[channel] = area

    async def _joined_area_ids(self, name: str, rest) -> list:
        cached = self._area_cache.get(name)
        if cached and time.time() - cached["ts"] < 15:
            return cached["areas"]
        data = await rest.request_data("GET", "/userSubscribeArea/v1/list")
        areas = []
        for item in data if isinstance(data, list) else []:
            # 该端点用 id 作为域标识（不是 areaId）
            area_id = str((item or {}).get("id") or (item or {}).get("areaId") or "")
            if area_id:
                areas.append(area_id)
        self._area_cache[name] = {"areas": areas, "ts": time.time()}
        return areas

    async def _resolve_area(self, name: str, rest, channel: str) -> str:
        area = self._channel_area.get(channel)
        if area:
            return area
        for area_id in await self._joined_area_ids(name, rest):
            groups = await rest.request_data(
                "GET", "/client/v1/area/v1/detail/v1/channels", params={"area": area_id},
            )
            for group in groups if isinstance(groups, list) else []:
                for ch in (group or {}).get("channels") or []:
                    ch_id = str(ch.get("channelId") or "")
                    if ch_id:
                        self._channel_area[ch_id] = area_id
            if self._channel_area.get(channel):
                return self._channel_area[channel]
        raise protocol.OopzApiError(f"无法定位频道 {channel} 所属的域（未加入或频道不存在）")

    async def _dm_session(self, name: str, rest, target: str) -> str:
        sessions = self._dm_sessions.setdefault(name, {})
        session_id = sessions.get(target)
        if session_id:
            return session_id
        session_id = await rest.open_private_session(target)
        sessions[target] = session_id
        return session_id

    # ==================== Send ====================

    class Send(BaseAdapter.Send):
        _style_tags: Optional[list] = None

        def Important(self):
            """公告样式（styleTags=["IMPORTANT"]）修饰符，随后调用 Text/Image 生效。"""
            self._style_tags = [protocol.STYLE_TAG_IMPORTANT]
            return self

        def Recall(self, message_id: str = "", **kwargs):
            """撤回消息。group 目标需频道可见（缓存解析域），private 目标为对方 uid。"""
            return asyncio.create_task(self._do_recall(message_id, **kwargs))

        def Raw_ob12(self, message, **kwargs):
            if isinstance(message, dict):
                message = [message]
            return asyncio.create_task(self._send_segments(list(message or []), **kwargs))

        async def _send_segments(self, segments: list, **kwargs) -> dict:
            adapter = self._adapter
            try:
                ctx = self.send_context
                name, account = await adapter._resolve_send_account(ctx.get("account_id"))
            except _AccountError as exc:
                return adapter.make_error(34005, str(exc))

            target_type = (ctx.get("target_type") or "user").lower()
            target_id = str(ctx.get("target_id") or "")
            if not target_id:
                return adapter.make_error(10003, "缺少发送目标（Send.To(...)）")
            if target_type == "guild":
                return adapter.make_error(10003, "Oopz 不支持向整个域发消息，请指定频道（group 目标）")

            outcome, data = await adapter._guarded(
                name, account,
                lambda rest: adapter._build_and_send(rest, name, target_type, target_id, segments, self),
                retry_transient=False,
            )
            if outcome == "error":
                return data
            message_id = ""
            if isinstance(data, dict):
                message_id = str(data.get("messageId") or "")
            return adapter.make_response(message_id=message_id, data={"message_id": message_id}, raw=data)

        async def _do_recall(self, message_id: str, **kwargs) -> dict:
            adapter = self._adapter
            try:
                ctx = self.send_context
                name, account = await adapter._resolve_send_account(ctx.get("account_id"))
            except _AccountError as exc:
                return adapter.make_error(34005, str(exc))
            message_id = str(message_id or kwargs.get("message_id") or "")
            if not message_id:
                return adapter.make_error(10003, "缺少 message_id")
            target_type = (ctx.get("target_type") or "user").lower()
            target_id = str(ctx.get("target_id") or "")
            if not target_id:
                return adapter.make_error(10003, "缺少撤回目标（Send.To(...)）")

            async def _recall(rest):
                if target_type == "user":
                    session = await adapter._dm_session(name, rest, target_id)
                    return await rest.recall_private_message(message_id, session, target_id)
                area = await adapter._resolve_area(name, rest, target_id)
                return await rest.recall_channel_message(message_id, area, target_id)

            outcome, data = await adapter._guarded(name, account, _recall)
            if outcome == "error":
                return data
            return adapter.make_response(message_id=message_id, data={"message_id": message_id}, raw=data)

    # ==================== Api ====================

    class Api(BaseAdapter.Api):
        """动作经适配器 call_api 统一分发；标准动作与 oopz.* 扩展见 platform-features.md"""

        pass

    async def _dispatch_api_async(self, endpoint: str, name: str, account: Optional[OopzBotConfig], params: dict) -> dict:
        params = dict(params or {})

        if endpoint == "get_status":
            auth = self._auth.get(name) or {}
            return self.make_response(data={
                "online": bool(auth),
                "self": {"user_id": auth.get("person_uid", ""), "platform": self._platform},
            })
        if endpoint == "get_version":
            return self.make_response(data={"version": __version__, "protocol": "oopz-web", "platform": self._platform})
        if endpoint == "get_supported_actions":
            return self.make_response(data={"actions": sorted(self._api_routes) + sorted(self._extension_routes)})

        if endpoint in self._UNSUPPORTED_STANDARD_ACTIONS:
            return self.make_error(10002, f"Oopz 不支持标准动作 {endpoint}")

        handler = self._api_routes.get(endpoint) or self._extension_routes.get(endpoint)
        if handler is not None:
            missing = [key for key in self._required_params.get(endpoint, ()) if not params.get(key)]
            if missing:
                return self.make_error(10003, f"缺少参数: {', '.join(missing)}")
            outcome, resp = await self._guarded(name, account, lambda rest: handler(rest, params))
            if outcome == "error":
                return resp
            return self._wrap_api_data(resp)

        if endpoint.startswith("/"):
            outcome, resp = await self._guarded(
                name, account,
                lambda rest: rest.request_data("POST", endpoint, body=params),
            )
            if outcome == "error":
                return resp
            return self._wrap_api_data(resp)

        return self.make_error(10002, f"未知动作 {endpoint}（平台扩展动作需 oopz. 前缀，原始端点需以 / 开头）")

    def _wrap_api_data(self, resp) -> dict:
        """平台原始数据 → 标准成功响应；data 里的 message_id 顶置。"""
        message_id = ""
        if isinstance(resp, dict) and resp.get("message_id"):
            message_id = str(resp["message_id"])
        return self.make_response(data=resp, message_id=message_id)

    # ---- 标准动作实现（返回 data；由 _guarded 包装为标准响应）----

    def _register_api_routes(self):
        self._api_routes = {
            "get_self_info": self._api_get_self_info,
            "get_user_info": self._api_get_user_info,
            "get_friend_list": self._api_get_friend_list,
            "get_group_info": self._api_get_group_info,
            "get_group_list": self._api_get_group_list,
            "get_guild_info": self._api_get_guild_info,
            "get_guild_list": self._api_get_guild_list,
            "get_channel_list": self._api_get_channel_list,
            "get_guild_members": self._api_get_guild_members,
            "delete_message": self._api_delete_message,
            "get_file": self._api_get_file,
            "leave_guild": self._api_leave_guild,
        }
        self._extension_routes = {
            "oopz.create_channel": self._api_create_channel,
            "oopz.delete_channel": self._api_delete_channel,
            "oopz.mute_member": self._api_mute_member,
            "oopz.unmute_member": self._api_unmute_member,
            "oopz.mute_mic": self._api_mute_mic,
            "oopz.unmute_mic": self._api_unmute_mic,
            "oopz.kick_member": self._api_kick_member,
            "oopz.block_user": self._api_block_user,
            "oopz.unblock_user": self._api_unblock_user,
            "oopz.get_area_members": self._api_get_area_members,
            "oopz.get_assignable_roles": self._api_get_assignable_roles,
            "oopz.assign_role": self._api_assign_role,
            "oopz.revoke_role": self._api_revoke_role,
            "oopz.react_message": self._api_react_message,
            "oopz.unreact_message": self._api_unreact_message,
            "oopz.get_reactions": self._api_get_reactions,
            "oopz.get_reaction_persons": self._api_get_reaction_persons,
            "oopz.top_message": self._api_top_message,
            "oopz.get_history_messages": self._api_get_history_messages,
            "oopz.get_friend_requests": self._api_get_friend_requests,
            "oopz.respond_friend_request": self._api_respond_friend_request,
        }
        self._required_params = {
            "get_user_info": ("user_id",),
            "get_group_info": ("group_id",),
            "get_guild_info": ("guild_id",),
            "get_channel_list": ("guild_id",),
            "get_guild_members": ("guild_id",),
            "delete_message": ("message_id",),
            "get_file": ("file_id",),
            "leave_guild": ("guild_id",),
            "oopz.create_channel": ("area_id", "name"),
            "oopz.delete_channel": ("area_id", "channel_id"),
            "oopz.mute_member": ("area_id", "user_id"),
            "oopz.unmute_member": ("area_id", "user_id"),
            "oopz.mute_mic": ("area_id", "user_id"),
            "oopz.unmute_mic": ("area_id", "user_id"),
            "oopz.kick_member": ("area_id", "user_id"),
            "oopz.block_user": ("area_id", "user_id"),
            "oopz.unblock_user": ("area_id", "user_id"),
            "oopz.get_area_members": ("area_id",),
            "oopz.get_assignable_roles": ("area_id", "target_id"),
            "oopz.assign_role": ("area_id", "target_id", "role_id"),
            "oopz.revoke_role": ("area_id", "target_id", "role_id"),
            "oopz.react_message": ("message_id", "channel_id", "emoji"),
            "oopz.unreact_message": ("message_id", "channel_id", "emoji"),
            "oopz.get_reactions": ("message_id",),
            "oopz.get_reaction_persons": ("message_id", "channel_id", "emoji"),
            "oopz.top_message": ("message_id", "area_id", "channel_id"),
            "oopz.get_history_messages": ("area_id", "channel_id"),
            "oopz.respond_friend_request": ("target", "friend_request_id", "agree"),
        }

    async def _api_get_self_info(self, rest, params: dict) -> dict:
        uid = (self._auth.get(self._current_api_account) or {}).get("person_uid", "")
        data = await rest.request_data(
            "GET", "/client/v1/person/v2/selfDetail", params={"uid": uid},
        )
        return self._person_info_data(data, self_info=True)

    async def _api_get_user_info(self, rest, params: dict) -> dict:
        data = await rest.request_data(
            "POST", "/client/v1/person/v1/personInfos",
            body={"persons": [str(params["user_id"])], "commonIds": []},
        )
        users = data if isinstance(data, list) else []
        if not users:
            raise protocol.OopzApiError(f"用户不存在: {params['user_id']}")
        return self._person_info_data(users[0])

    async def _api_get_friend_list(self, rest, params: dict) -> dict:
        data = await rest.request_data("GET", "/client/v1/list/v1/friendship")
        friends = [self._person_info_data(item) for item in data if isinstance(item, dict)]
        return {"friends": friends}

    async def _api_get_friend_requests(self, rest, params: dict) -> dict:
        data = await rest.request_data("GET", "/client/v1/friendship/v1/requests")
        requests = (data or {}).get("requests") or []
        normalized = [{
            "request_id": f"{item.get('person')}:{item.get('friendRequestId')}",
            "user_id": str(item.get("person") or ""),
            "user_nickname": str(item.get("name") or ""),
        } for item in requests if isinstance(item, dict)]
        return {"requests": normalized}

    async def _api_respond_friend_request(self, rest, params: dict) -> dict:
        data = await rest.request_data("POST", "/client/v1/friendship/v1/response", body={
            "agree": bool(params["agree"]),
            "friendRequestId": int(params["friend_request_id"]),
            "target": str(params["target"]),
        })
        return data or {"handled": True}

    async def _api_get_group_info(self, rest, params: dict) -> dict:
        channel = str(params["group_id"])
        area = await self._resolve_area(self._current_api_account, rest, channel)
        area_info = await rest.request_data("GET", "/area/v3/info", params={"area": area})
        try:
            channel_info = await rest.request_data(
                "GET", "/area/v3/channel/setting/info", params={"channel": channel},
            )
        except protocol.OopzApiError:
            channel_info = {}
        return {
            "group_id": channel,
            "group_name": str((channel_info or {}).get("name") or ""),
            "area_id": area,
            "area_name": str((area_info or {}).get("name") or ""),
            "area_owner": str((area_info or {}).get("owner") or ""),
            "member_count": area_info.get("memberCount") or area_info.get("headcount") or 0,
        }

    async def _api_get_group_list(self, rest, params: dict) -> dict:
        groups = []
        for area_id in await self._joined_area_ids(self._current_api_account, rest):
            try:
                area_info = await rest.request_data("GET", "/area/v3/info", params={"area": area_id})
            except protocol.OopzApiError:
                area_info = {}
            area_name = str((area_info or {}).get("name") or "")
            channel_groups = await rest.request_data(
                "GET", "/client/v1/area/v1/detail/v1/channels", params={"area": area_id},
            )
            for group in channel_groups if isinstance(channel_groups, list) else []:
                for ch in (group or {}).get("channels") or []:
                    ch_id = str(ch.get("channelId") or "")
                    if not ch_id:
                        continue
                    self._channel_area[ch_id] = area_id
                    groups.append({
                        "group_id": ch_id,
                        "group_name": str(ch.get("name") or ""),
                        "channel_type": str(ch.get("channelType") or ch.get("type") or ""),
                        "area_id": area_id,
                        "area_name": area_name,
                    })
        return {"groups": groups}

    async def _api_get_guild_info(self, rest, params: dict) -> dict:
        data = await rest.request_data("GET", "/area/v3/info", params={"area": str(params["guild_id"])})
        data = data or {}
        return {
            "guild_id": str(params["guild_id"]),
            "guild_name": str(data.get("name") or ""),
            "owner": str(data.get("owner") or ""),
            "desc": str(data.get("desc") or ""),
        }

    async def _api_get_guild_list(self, rest, params: dict) -> dict:
        data = await rest.request_data("GET", "/userSubscribeArea/v1/list")
        guilds = [{
            "guild_id": str((item or {}).get("id") or (item or {}).get("areaId") or ""),
            "guild_name": str((item or {}).get("name") or ""),
        } for item in data if isinstance(item, dict)]
        return {"guilds": [g for g in guilds if g["guild_id"]]}

    async def _api_get_channel_list(self, rest, params: dict) -> dict:
        area = str(params.get("guild_id") or "")
        if not area:
            raise protocol.OopzApiError("缺少 guild_id（Oopz 频道隶属域）")
        groups = await rest.request_data(
            "GET", "/client/v1/area/v1/detail/v1/channels", params={"area": area},
        )
        channels = []
        for group in groups if isinstance(groups, list) else []:
            for ch in (group or {}).get("channels") or []:
                ch_id = str(ch.get("channelId") or "")
                if not ch_id:
                    continue
                self._channel_area[ch_id] = area
                channels.append({
                    "channel_id": ch_id,
                    "channel_name": str(ch.get("name") or ""),
                    "channel_type": str(ch.get("channelType") or ch.get("type") or ""),
                })
        return {"channels": channels}

    async def _api_get_guild_members(self, rest, params: dict) -> dict:
        area = str(params["guild_id"])
        offset_start = int(params.get("offset_start", 0))
        offset_end = int(params.get("offset_end", offset_start + 49))
        data = await rest.request_data("GET", "/area/v3/members", params={
            "area": area,
            "offsetStart": str(offset_start),
            "offsetEnd": str(offset_end),
        })
        members = []
        for m in (data or {}).get("members") or []:
            m = m if isinstance(m, dict) else {}
            person = m.get("person") if isinstance(m.get("person"), dict) else {}
            members.append({
                "user_id": str(m.get("uid") or person.get("uid") or ""),
                "nickname": str(m.get("name") or ""),
            })
        return {"members": members, "total": (data or {}).get("totalCount") or 0}

    async def _api_delete_message(self, rest, params: dict) -> dict:
        message_id = str(params["message_id"])
        channel = str(params.get("channel_id") or "")
        target = str(params.get("user_id") or "")
        if channel:
            area = await self._resolve_area(self._current_api_account, rest, channel)
            await rest.recall_channel_message(message_id, area, channel)
            return {"message_id": message_id}
        if target:
            session = await self._dm_session(self._current_api_account, rest, target)
            await rest.recall_private_message(message_id, session, target)
            return {"message_id": message_id}
        raise protocol.OopzApiError("撤回需要 channel_id（频道）或 user_id（私信）上下文")

    async def _api_get_file(self, rest, params: dict) -> dict:
        file_id = str(params.get("file_id") or "")
        if not file_id.startswith("http"):
            raise protocol.OopzApiError("get_file 需要附件完整 URL 作为 file_id")
        resp = await client.get(file_id, timeout=60)
        if resp.status != 200:
            raise protocol.OopzApiError(f"附件下载失败 (HTTP {resp.status})")
        content = await resp.read()
        content_type = ""
        try:
            content_type = str((resp.headers or {}).get("Content-Type") or "")
        except Exception:
            pass
        return {
            "file_name": file_id.rsplit("/", 1)[-1].split("?")[0] or "file",
            "content_type": content_type,
            "base64": base64.b64encode(content).decode("ascii"),
            "size": len(content),
        }

    async def _api_leave_guild(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "DELETE", "/client/v1/area/v1/quit", body={"area": str(params["guild_id"])},
        )

    # ---- 管理扩展 ----

    async def _api_create_channel(self, rest, params: dict) -> dict:
        area = str(params["area_id"])
        channel_type = str(params.get("channel_type") or "TEXT").upper()
        group_id = str(params.get("group_id") or "")
        if not group_id:
            groups = await rest.request_data(
                "GET", "/client/v1/area/v1/detail/v1/channels", params={"area": area},
            )
            if isinstance(groups, list) and groups:
                group_id = str((groups[0] or {}).get("groupId") or "")
        if not group_id:
            raise protocol.OopzApiError("域内没有可用频道分组，请显式传 group_id")
        return await rest.request_data("POST", "/client/v1/area/v1/channel/v1/create", body={
            "area": area,
            "group": group_id,
            "name": str(params["name"]),
            "type": channel_type,
            "secret": bool(params.get("secret", False)),
            "maxMember": int(params.get("max_member", 100)),
        })

    async def _api_delete_channel(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "DELETE", "/client/v1/area/v1/channel/v1/delete",
            params={"channel": str(params["channel_id"]), "area": str(params["area_id"])},
        )

    def _mute_interval_id(self, params: dict, voice: bool) -> str:
        minutes = int(params.get("duration", 5))
        return protocol.pick_mute_interval(minutes, voice=voice)

    async def _api_mute_member(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "PATCH", "/client/v1/area/v1/member/v1/disableText",
            params={
                "area": str(params["area_id"]),
                "target": str(params["user_id"]),
                "intervalId": self._mute_interval_id(params, voice=False),
            },
        )

    async def _api_unmute_member(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "PATCH", "/client/v1/area/v1/member/v1/recoverText",
            params={"area": str(params["area_id"]), "target": str(params["user_id"])},
        )

    async def _api_mute_mic(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "PATCH", "/client/v1/area/v1/member/v1/disableVoice",
            params={
                "area": str(params["area_id"]),
                "target": str(params["user_id"]),
                "intervalId": self._mute_interval_id(params, voice=True),
            },
        )

    async def _api_unmute_mic(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "PATCH", "/client/v1/area/v1/member/v1/recoverVoice",
            params={"area": str(params["area_id"]), "target": str(params["user_id"])},
        )

    async def _api_kick_member(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "POST", "/area/v3/remove",
            body={"area": str(params["area_id"]), "target": str(params["user_id"])},
        )

    async def _api_block_user(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "DELETE", "/client/v1/area/v1/block",
            params={"area": str(params["area_id"]), "target": str(params["user_id"])},
        )

    async def _api_unblock_user(self, rest, params: dict) -> dict:
        return await rest.request_data(
            "PATCH", "/client/v1/area/v1/unblock",
            params={"area": str(params["area_id"]), "target": str(params["user_id"])},
        )

    async def _api_get_area_members(self, rest, params: dict) -> dict:
        offset_start = int(params.get("offset_start", 0))
        offset_end = int(params.get("offset_end", offset_start + 49))
        data = await rest.request_data("GET", "/area/v3/members", params={
            "area": str(params["area_id"]),
            "offsetStart": str(offset_start),
            "offsetEnd": str(offset_end),
        })
        return data or {}

    async def _api_get_assignable_roles(self, rest, params: dict) -> dict:
        data = await rest.request_data("GET", "/area/v3/role/canGiveList", params={
            "area": str(params["area_id"]), "target": str(params["target_id"]),
        })
        return {"roles": (data or {}).get("roles") or []}

    async def _api_assign_role(self, rest, params: dict) -> dict:
        return await self._edit_user_role(rest, params, add=True)

    async def _api_revoke_role(self, rest, params: dict) -> dict:
        return await self._edit_user_role(rest, params, add=False)

    async def _edit_user_role(self, rest, params: dict, add: bool) -> dict:
        area = str(params["area_id"])
        target = str(params["target_id"])
        role_id = int(params["role_id"])
        detail = await rest.request_data("GET", "/area/v3/userDetail", params={"area": area, "target": target})
        current = [int(r.get("roleId") or r.get("roleID") or 0) for r in (detail or {}).get("roles") or []]
        current = [rid for rid in current if rid]
        if add:
            if role_id not in current:
                current.append(role_id)
        else:
            current = [rid for rid in current if rid != role_id]
        return await rest.request_data("POST", "/area/v3/role/editUserRole", body={
            "area": area, "target": target, "targetRoleIDs": current,
        })

    def _reaction_endpoint(self, private_target: str) -> tuple:
        if private_target:
            return "/im/session/v1/imReaction", "/im/session/v1/imReactions", "/im/session/v2/imReactionPersons"
        return "/im/session/v1/gimReaction", "/im/session/v1/gimReactions", "/im/session/v2/gimReactionPersons"

    async def _api_react_message(self, rest, params: dict) -> dict:
        endpoint, _, _ = self._reaction_endpoint(str(params.get("user_id") or ""))
        return await rest.request_data("POST", endpoint, body={
            "anchor": str(params.get("user_id") or ""),
            "area": str(params.get("area_id") or ""),
            "channel": str(params["channel_id"]),
            "emoji": str(params["emoji"]),
            "messageId": str(params["message_id"]),
            "target": str(params.get("user_id") or ""),
            "type": "REPLY",
        })

    async def _api_unreact_message(self, rest, params: dict) -> dict:
        endpoint, _, _ = self._reaction_endpoint(str(params.get("user_id") or ""))
        return await rest.request_data("POST", endpoint, body={
            "anchor": str(params.get("user_id") or ""),
            "area": str(params.get("area_id") or ""),
            "channel": str(params["channel_id"]),
            "emoji": str(params["emoji"]),
            "messageId": str(params["message_id"]),
            "target": str(params.get("user_id") or ""),
            "type": "WITHDRAW",
        })

    async def _api_get_reactions(self, rest, params: dict) -> dict:
        _, batch_endpoint, _ = self._reaction_endpoint(str(params.get("user_id") or ""))
        message_ids = params.get("message_ids") or [str(params["message_id"])]
        if len(message_ids) > 50:
            raise protocol.OopzApiError("message_ids 单次最多 50 条")
        data = await rest.request_data(
            "POST", batch_endpoint,
            body=[{"messageId": str(mid)} for mid in message_ids],
        )
        return {"reactions": data if isinstance(data, list) else []}

    async def _api_get_reaction_persons(self, rest, params: dict) -> dict:
        _, _, persons_endpoint = self._reaction_endpoint(str(params.get("user_id") or ""))
        return await rest.request_data("GET", persons_endpoint, params={
            "messageId": str(params["message_id"]),
            "channel": str(params["channel_id"]),
            "emoji": str(params["emoji"]),
            "page": int(params.get("page", 1)),
            "pageSize": int(params.get("page_size", 4)),
        })

    async def _api_top_message(self, rest, params: dict) -> dict:
        return await rest.request_data("POST", "/im/session/v1/messageTop", body={
            "messageId": str(params["message_id"]),
            "type": "CANCEL_TOP" if params.get("cancel") else "TOP",
            "area": str(params["area_id"]),
            "channel": str(params["channel_id"]),
        })

    async def _api_get_history_messages(self, rest, params: dict) -> dict:
        return await rest.request_data("GET", "/im/session/v2/messageBefore", params={
            "area": str(params["area_id"]),
            "channel": str(params["channel_id"]),
            "size": str(int(params.get("size", 50))),
        })

    # ---- 响应数据规整 ----

    def _person_info_data(self, item: dict, self_info: bool = False) -> dict:
        item = item or {}
        info = {
            "user_id": str(item.get("uid") or ""),
            "nickname": str(item.get("name") or item.get("nickName") or ""),
            "avatar": str(item.get("avatar") or ""),
        }
        if self_info:
            info["self_info"] = {
                "user_id": info["user_id"],
                "platform": self._platform,
            }
        return info

    # ==================== call_api 路由（适配器主入口）====================

    async def call_api(self, endpoint: str, _account_id: str = None, **params) -> dict:
        """双兼容账户参数（标准 account_id + 遗留 _account_id），分发到标准/扩展动作。"""
        account_id = params.pop("account_id", None) or _account_id
        try:
            name, account = await self._resolve_send_account(account_id)
        except _AccountError as exc:
            return self.make_error(34005, str(exc))

        previous = self._current_api_account
        self._current_api_account = name
        try:
            return await self._dispatch_api_async(endpoint, name, account, params)
        finally:
            self._current_api_account = previous

    async def _build_and_send(self, rest, name: str, target_type: str, target_id: str, segments: list, send) -> dict:
        text_parts: list[str] = []
        mention_list: list[dict] = []
        is_mention_all = False
        reference_message_id = ""
        attachments: list[dict] = []
        style_tags = list(getattr(send, "_style_tags", None) or [])

        for seg in segments:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type") or "")
            data = seg.get("data") or {}
            if seg_type == "text":
                text_parts.append(str(data.get("text") or ""))
            elif seg_type == "mention":
                uid = str(data.get("user_id") or "")
                if uid:
                    text_parts.append(protocol.mention_token(uid))
                    mention_list.append({"person": uid, "isBot": False, "botType": "", "offset": sum(len(p) for p in text_parts[:-1])})
            elif seg_type == "mention_all":
                text_parts.append(protocol.MENTION_ALL_TOKEN)
                is_mention_all = True
            elif seg_type == "reply":
                reference_message_id = str(data.get("message_id") or data.get("id") or "")
            elif seg_type == "image":
                payload_bytes, filename = await self._read_media_bytes(data.get("file"))
                width, height = self._image_size(data, payload_bytes)
                ext = protocol.guess_image_ext(payload_bytes, filename)
                attachment = await rest.upload_image_bytes(
                    payload_bytes, ext,
                    display_name=filename or str(data.get("file_name") or ""),
                    width=width, height=height,
                )
                attachments.append(attachment)
                text_parts.append(protocol.image_placeholder(width or 100, height or 100, attachment["fileKey"]))
            elif seg_type in ("voice", "audio", "video", "file"):
                # 平台仅支持图片上传，按发送规范降级拒绝（不抛异常）
                return self.make_error(10002, f"Oopz 平台仅支持图片上传，{seg_type} 段已降级")
            else:
                return self.make_error(10002, f"暂不支持的消息段类型: {seg_type}")

        text = "".join(text_parts)
        if target_type in ("user", "private"):
            session = await self._dm_session(name, rest, target_id)
            payload = protocol.build_message_payload(
                text=text, channel=session, target=target_id,
                attachments=attachments, mention_list=mention_list,
                is_mention_all=is_mention_all, style_tags=style_tags,
                reference_message_id=reference_message_id or None,
            )
            data = await rest.send_private_message(payload)
        else:
            area = await self._resolve_area(name, rest, target_id)
            payload = protocol.build_message_payload(
                text=text, area=area, channel=target_id,
                attachments=attachments, mention_list=mention_list,
                is_mention_all=is_mention_all, style_tags=style_tags,
                reference_message_id=reference_message_id or None,
            )
            data = await rest.send_channel_message(payload)

        data = data or {}
        message_id = str(data.get("messageId") or "")
        return self.make_response(message_id=message_id, data={"message_id": message_id}, raw=data)

    @staticmethod
    def _image_size(data: dict, payload: bytes) -> tuple:
        width = int(data.get("width") or 0)
        height = int(data.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
        size = protocol.guess_image_dimensions(payload)
        return size

    async def _read_media_bytes(self, source) -> tuple:
        """file 参数：bytes / base64 / data URI / file:// / 本地路径 / HTTP(S) URL。"""
        if isinstance(source, (bytes, bytearray)):
            return bytes(source), ""
        if source is None:
            raise protocol.OopzApiError("图片段缺少 file 参数")
        source = str(source)
        if source.startswith("data:"):
            _, _, b64data = source.partition(",")
            return base64.b64decode(b64data), ""
        if source.startswith("file://"):
            source = source[7:]
        lowered = source.lower()
        if lowered.startswith(("http://", "https://")):
            resp = await client.get(source, timeout=60)
            if resp.status != 200:
                raise protocol.OopzApiError(f"图片下载失败 (HTTP {resp.status})")
            filename = source.rsplit("/", 1)[-1].split("?")[0]
            return await resp.read(), filename
        if lowered.startswith("base64://"):
            return base64.b64decode(source[9:]), ""
        if os.path.isfile(source):
            with open(source, "rb") as fh:
                return fh.read(), os.path.basename(source)
        raise protocol.OopzApiError(f"无法识别的媒体来源: {source[:80]}")

    # ==================== Request（好友申请）====================

    class Request(BaseAdapter.Request):
        async def _do_accept(self, **kwargs) -> dict:
            return await self._respond(True, **kwargs)

        async def _do_reject(self, **kwargs) -> dict:
            return await self._respond(False, **kwargs)

        async def _respond(self, agree: bool, **kwargs) -> dict:
            adapter = self._adapter
            request_id = str(kwargs.get("request_id") or (self.request_context or {}).get("request_id") or "")
            target, _, friend_request_id = request_id.partition(":")
            if not target or not friend_request_id.isdigit():
                return adapter.make_error(
                    10003,
                    "request_id 应为复合键 \"<target>:<friendRequestId>\"（来自 friend.request 事件）",
                )
            account_id = (self.request_context or {}).get("account_id")
            try:
                name, account = await adapter._resolve_send_account(account_id)
            except _AccountError as exc:
                return adapter.make_error(34005, str(exc))

            async def _respond(rest):
                return await rest.request_data("POST", "/client/v1/friendship/v1/response", body={
                    "agree": agree,
                    "friendRequestId": int(friend_request_id),
                    "target": target,
                })

            outcome, data = await adapter._guarded(name, account, _respond)
            if outcome == "error":
                return data
            return adapter.make_response(data={
                "request_id": request_id,
                "handled": "accept" if agree else "reject",
                **(data if isinstance(data, dict) else {}),
            })

    # ==================== 生命周期 ====================

    async def start(self):
        self._is_running = True
        self._register_api_routes()
        self._accounts_data = self._load_accounts()
        cfg = self.cfg
        self._heartbeat_interval = max(5, int(cfg.heartbeat_interval or 10))
        enabled = {
            name: account for name, account in (self._accounts_data or {}).items()
            if getattr(account, "enabled", True)
        }
        if not enabled:
            self._get_logger().warning("Oopz 适配器没有已启用的账户，未启动任何连接（请在配置中添加 accounts）")
            self._is_running = False
            return

        for name, account in enabled.items():
            try:
                auth = await self._prepare_account(name, account)
            except protocol.OopzProtocolError as exc:
                self._get_logger().error(f"账户 {name} 登录失败，已跳过: {exc}")
                continue
            except Exception as exc:
                self._get_logger().error(f"账户 {name} 初始化异常，已跳过: {exc}")
                continue
            self._spawn_account_loop(name, account)
            self._get_logger().info(f"账户 {name}（{auth['person_uid']}）已启动")

    def _spawn_account_loop(self, name: str, account: OopzBotConfig):
        coro = self._ws_loop(name, account)

        async def _runner():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._get_logger().error(f"账户 {name} 连接循环异常退出: {exc}")

        if spawn_background is not None:
            self._ws_tasks[name] = spawn_background(_runner())
        else:
            self._ws_tasks[name] = asyncio.create_task(_runner())

    async def shutdown(self):
        self._is_running = False
        tasks = [task for task in self._ws_tasks.values() if task is not None]
        self._ws_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        conns = list(self._ws_conns.values())
        self._ws_conns.clear()
        for ws in conns:
            try:
                await ws.close()
            except Exception:
                pass
        for name, auth in self._auth.items():
            try:
                await self.emit_meta("disconnect", auth.get("person_uid", ""))
            except Exception:
                pass

    # ==================== WS 循环 ====================

    async def _ws_loop(self, name: str, account: OopzBotConfig):
        backoff = 1.0
        max_backoff = 300.0
        while self._is_running:
            ws = None
            heartbeat_task = None
            try:
                auth = await self._ensure_session(name, account)
                person_uid = auth["person_uid"]
                ws = await client.ws_connect(
                    protocol.resolve_ws_url(self._gateway), heartbeat=None, owner=f"oopz:{name}",
                )
                self._ws_conns[name] = ws
                await ws.send_text(protocol.build_auth_frame(person_uid, auth["device_id"], auth["jwt"]))
                subscribe, areas = await self._build_subscribe_frame(name, auth)
                if subscribe:
                    await ws.send_text(subscribe)
                    self._get_logger().info(f"账户 {name} 已订阅 {len(areas)} 个域的事件: {', '.join(areas)}")
                else:
                    self._get_logger().warning(
                        f"账户 {name} 未订阅任何域（域消息/通知将收不到，仅私聊可用）："
                        "请确认账号已加入域，或配置 subscribe_areas"
                    )
                await self.emit_meta("connect", person_uid)
                self._get_logger().info(f"账户 {name} WebSocket 已连接")
                backoff = 1.0

                heartbeat_task = asyncio.create_task(self._heartbeat_loop(name, ws))
                while self._is_running and not ws.closed:
                    msg = await ws.receive()
                    # 服务端事件可能以文本或二进制帧下发，两者都要解
                    if msg.type in (WSMessage.TEXT, WSMessage.BINARY):
                        await self._handle_ws_frame(name, person_uid, msg.data)
                    elif msg.type in (WSMessage.CLOSE, WSMessage.ERROR):
                        self._get_logger().info(f"账户 {name} WebSocket 已关闭，准备重连")
                        break
                    else:
                        self._get_logger().debug(f"账户 {name} 忽略 WS 帧类型 {msg.type}")
            except protocol.OopzLoginError as exc:
                self._get_logger().error(f"账户 {name} 登录失败（不再自动重连，请检查凭据）: {exc}")
                await self._emit_disconnect_safe(name)
                return
            except protocol.OopzAuthExpired:
                self._get_logger().warning(f"账户 {name} WS 鉴权被拒，尝试续期后重连")
                await self._emit_disconnect_safe(name)
                if account.login_type != "password":
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._get_logger().warning(f"账户 {name} 连接异常: {exc}，{backoff:.0f}s 后重连")
                await self._emit_disconnect_safe(name)
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                self._ws_conns.pop(name, None)

            if not self._is_running:
                return
            try:
                if account.login_type == "password" and self._jwt_near_expiry(name):
                    await self._login_account(name, account)
            except protocol.OopzProtocolError as exc:
                self._get_logger().warning(f"账户 {name} 续期失败: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _emit_disconnect_safe(self, name: str):
        auth = self._auth.get(name) or {}
        try:
            await self.emit_meta("disconnect", auth.get("person_uid", ""))
        except Exception:
            pass

    async def _heartbeat_loop(self, name: str, ws):
        while self._is_running and not ws.closed:
            await asyncio.sleep(self._heartbeat_interval)
            if self._is_running and not ws.closed:
                try:
                    auth = self._auth.get(name) or {}
                    await ws.send_text(protocol.build_heartbeat_frame(auth.get("person_uid", "")))
                except Exception:
                    break

    async def _build_subscribe_frame(self, name: str, auth: dict) -> tuple:
        """返回 (订阅帧, 订阅的域列表)；域列表为空时帧为空串。"""
        cfg = self.cfg
        areas = [str(a) for a in (cfg.subscribe_areas or []) if str(a).strip()]
        if cfg.auto_subscribe_areas:
            rest = self._rest.get(name)
            if rest is not None:
                try:
                    joined = await self._joined_area_ids(name, rest)
                    areas = list(dict.fromkeys([*areas, *joined]))
                except Exception as exc:
                    self._get_logger().warning(f"账户 {name} 拉取已加入域失败（仅私聊可用）: {exc}")
        return protocol.build_subscribe_frame(areas, auth.get("person_uid", "")), areas

    async def _handle_ws_frame(self, name: str, person_uid: str, raw) -> None:
        event_code, body = protocol.decode_frame(raw)
        if event_code < 0:
            return
        self._get_logger().debug(f"账户 {name} [Recv] event={event_code}")
        if protocol.is_auth_rejected(event_code, body):
            raise protocol.OopzAuthExpired("WS 鉴权校验未通过 (event=21, checkRes=false)")

        msg = body.get("data") if isinstance(body.get("data"), (dict, str)) else None
        if isinstance(msg, str):
            try:
                msg = json.loads(msg)
            except (TypeError, ValueError):
                msg = None
        self._remember_channel_area(body, msg if isinstance(msg, dict) else None)

        cfg = self.cfg
        if cfg.ignore_self and event_code in (
            protocol.EVENT_CHAT_MESSAGE, protocol.EVENT_PRIVATE_MESSAGE,
        ) and isinstance(msg, dict) and str(msg.get("person") or "") == person_uid:
            self._get_logger().debug(f"账户 {name} 忽略自己发送的消息（ignore_self）")
            return

        converted = self.convert({"event": event_code, "body": body}, bot_id=person_uid)
        if converted is None:
            return
        converted.setdefault("self", {})["account_id"] = name
        if hasattr(self.adapter, "emit"):
            await self.adapter.emit(converted)
        else:
            self._get_logger().warning("框架未提供 adapter.emit，事件已丢弃")


class _AccountError(Exception):
    """Send/Api/Request 链路的账户解析失败。"""
