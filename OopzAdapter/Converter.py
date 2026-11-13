"""Oopz WS 事件 → OneBot12 标准事件转换。

输入为 decode_frame 解包后的 ``(事件码, body)``；输出标准事件字典，
协议内部帧（认证/心跳/订阅/握手）返回 None 不进事件流。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Optional

from ErisPulse.Core.Bases import BaseConverter

from . import protocol

__all__ = ["OopzConverter"]


def _jsonish(value) -> dict:
    """body.data 这类字段可能二次序列化为 JSON 字符串。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class OopzConverter(BaseConverter):
    # 协议内部帧，不产生平台事件
    _PROTOCOL_EVENTS = frozenset({
        protocol.EVENT_SERVER_ID,
        protocol.EVENT_AUTH_RESULT,
        protocol.EVENT_SUBSCRIBE_AREA_EVENTS,
        protocol.EVENT_AUTH,
        protocol.EVENT_HEARTBEAT,
    })

    def __init__(self):
        super().__init__(platform="oopz")
        self._handlers = {
            protocol.EVENT_CHAT_MESSAGE: self._handle_message,
            protocol.EVENT_PRIVATE_MESSAGE: self._handle_message,
            protocol.EVENT_MESSAGE_EDIT: self._handle_message_edit,
            protocol.EVENT_PRIVATE_MESSAGE_EDIT: self._handle_message_edit,
            protocol.EVENT_MESSAGE_DELETE: self._handle_recall,
            protocol.EVENT_PRIVATE_MESSAGE_DELETE: self._handle_recall,
            protocol.EVENT_FRIEND_REQUEST: self._handle_friend_request,
            protocol.EVENT_FRIEND_DELETE: self._handle_friend_delete,
            protocol.EVENT_MESSAGE_REACTION: self._handle_reaction,
            protocol.EVENT_CHANNEL_VOICE_BAN: self._handle_ban,
            protocol.EVENT_CHANNEL_MESSAGE_BAN: self._handle_ban,
            protocol.EVENT_USER_ENTER_VOICE_CHANNEL: self._handle_voice,
            protocol.EVENT_USER_LEAVE_VOICE_CHANNEL: self._handle_voice,
            protocol.EVENT_PUBLIC_CHANNEL_CREATE: self._handle_channel_create,
            protocol.EVENT_CHANNEL_UPDATE: self._handle_channel_update,
            protocol.EVENT_CHANNEL_DELETE: self._handle_channel_delete,
            protocol.EVENT_AREA_UPDATE: self._handle_area_update,
            protocol.EVENT_ROLE_CHANGED: self._handle_role_change,
            protocol.EVENT_USER_UPDATE: self._handle_user_update,
            protocol.EVENT_USER_LOGIN_STATE_CHANGED: self._handle_login_state,
        }

    def convert(self, raw_event: dict, bot_id: str = "") -> Optional[dict]:
        """raw_event: {"event": 事件码, "body": 解包后的 body dict}。"""
        event_code = _to_int(raw_event.get("event"), -1)
        body = raw_event.get("body")
        if not isinstance(body, dict):
            body = _jsonish(body)
        if event_code in self._PROTOCOL_EVENTS:
            return None
        handler = self._handlers.get(event_code)
        if handler is None:
            return self._handle_unknown(event_code, body, bot_id)
        return handler(event_code, body, bot_id)

    # ==================== 公共字段 ====================

    def _build_base(
        self,
        body: dict,
        raw_type: str,
        bot_id: str,
        event_type: str,
        detail_type: str,
        sub_type: str = "",
        event_key: str = "",
        timestamp: str = "",
    ) -> dict:
        ts = timestamp.strip()
        if ts.isdigit() and len(ts) >= 16:
            # 消息时间戳为微秒字符串
            ts = str(int(int(ts) / 1_000_000))
        elif not ts.isdigit():
            ts = str(int(time.time()))
        event_id = f"{bot_id}:{event_key}" if event_key else f"{bot_id}:{uuid.uuid4()}"
        return {
            "id": event_id,
            "time": int(ts),
            "type": event_type,
            "detail_type": detail_type,
            "sub_type": sub_type,
            "platform": self.platform,
            "self": {"platform": self.platform, "user_id": str(bot_id)},
            f"{self.platform}_raw": body,
            f"{self.platform}_raw_type": raw_type,
        }

    @staticmethod
    def _scope_of(body: dict) -> tuple[str, str, str]:
        """按 §9 推断：channel → group，仅 person → private。返回 (detail_type, group_id, area)。"""
        channel = str(body.get("channel") or "")
        area = str(body.get("area") or "")
        if channel:
            return "group", channel, area
        return "private", "", area

    # ==================== 消息 ====================

    def _handle_message(self, event_code: int, body: dict, bot_id: str) -> dict:
        is_private = event_code == protocol.EVENT_PRIVATE_MESSAGE
        msg = _jsonish(body.get("data"))
        raw_type = "message.private" if is_private else "message"

        sender = str(msg.get("person") or body.get("person") or "")
        message_id = str(msg.get("messageId") or "")
        segments, alt_message = self._build_segments(msg)

        if is_private:
            detail_type, group_id, _ = "private", "", ""
            event = self._build_base(
                body, raw_type, bot_id, "message", "private",
                sub_type=str(msg.get("type") or "text").lower(),
                event_key=f"msg:{message_id}", timestamp=str(msg.get("timestamp") or ""),
            )
            event["user_id"] = sender
        else:
            channel = str(msg.get("channel") or "")
            area = str(msg.get("area") or "")
            event = self._build_base(
                body, raw_type, bot_id, "message", "group",
                sub_type=str(msg.get("type") or "text").lower(),
                event_key=f"msg:{message_id}", timestamp=str(msg.get("timestamp") or ""),
            )
            event["detail_type"] = "group"
            event["group_id"] = channel
            event["guild_id"] = area
            event[f"{self.platform}_area_id"] = area
            event["user_id"] = sender

        event["message_id"] = message_id
        event["message"] = segments
        event["alt_message"] = alt_message
        if str(msg.get("referenceMessageId") or ""):
            event.setdefault("oopz_reference_message_id", str(msg["referenceMessageId"]))
        return event

    def _handle_message_edit(self, event_code: int, body: dict, bot_id: str) -> dict:
        msg = _jsonish(body.get("data"))
        is_private = event_code == protocol.EVENT_PRIVATE_MESSAGE_EDIT
        detail_type, group_id, area = (
            ("private", "", "") if is_private else self._scope_of(msg)
        )
        message_id = str(msg.get("messageId") or "")
        text = str(msg.get("text") or msg.get("content") or "")
        event = self._build_base(
            body, "message.private.edit" if is_private else "message.edit",
            bot_id, "notice", detail_type, sub_type="message_edit",
            event_key=f"edit:{message_id}",
            timestamp=str(msg.get("timestamp") or msg.get("editTime") or ""),
        )
        event["message_id"] = message_id
        event["user_id"] = str(msg.get("person") or "")
        if group_id:
            event["group_id"] = group_id
            event[f"{self.platform}_area_id"] = area
        event[f"{self.platform}_edited_text"] = text
        return event

    def _handle_recall(self, event_code: int, body: dict, bot_id: str) -> dict:
        is_private = event_code == protocol.EVENT_PRIVATE_MESSAGE_DELETE
        detail_type = "private" if is_private else "group"
        message_id = str(body.get("messageId") or "")
        event = self._build_base(
            body, "recall.private" if is_private else "recall",
            bot_id, "notice", detail_type, sub_type="recall",
            event_key=f"recall:{message_id}",
        )
        event["message_id"] = message_id
        event["user_id"] = str(body.get("person") or "")
        event["operator_id"] = str(body.get("person") or "")
        if not is_private:
            event["group_id"] = str(body.get("channel") or "")
            event[f"{self.platform}_area_id"] = str(body.get("area") or "")
        return event

    # ==================== 好友 ====================

    def _handle_friend_request(self, event_code: int, body: dict, bot_id: str) -> dict:
        person = str(body.get("person") or "")
        request_id = int(body.get("friendRequestId") or 0)
        event = self._build_base(
            body, "friend.request", bot_id, "request", "friend",
            sub_type="add", event_key=f"friend_req:{request_id}",
            timestamp=str(body.get("createTime") or ""),
        )
        # 复合键：审批端点需要 (target, friendRequestId) 两个参数，无状态免留存表
        event["request_id"] = f"{person}:{request_id}"
        event["user_id"] = person
        event["user_nickname"] = str(body.get("name") or "")
        comment = str(body.get("desc") or body.get("comment") or "")
        if comment:
            event["comment"] = comment
        return event

    def _handle_friend_delete(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "friend.delete", bot_id, "notice", "friend_decrease",
            sub_type="friend_delete", event_key=f"friend_del:{body.get('person') or uuid.uuid4()}",
        )
        event["user_id"] = str(body.get("person") or "")
        return event

    # ==================== 互动与管理通知 ====================

    def _handle_reaction(self, event_code: int, body: dict, bot_id: str) -> dict:
        detail_type, group_id, area = self._scope_of(body)
        message_id = str(body.get("messageId") or "")
        event = self._build_base(
            body, "message.reaction", bot_id, "notice", detail_type,
            sub_type="reaction", event_key=f"reaction:{message_id}:{body.get('emoji')}:{body.get('person')}",
        )
        event["message_id"] = message_id
        event["user_id"] = str(body.get("person") or "")
        if group_id:
            event["group_id"] = group_id
            event[f"{self.platform}_area_id"] = area
        event[f"{self.platform}_emoji"] = str(body.get("emoji") or "")
        event[f"{self.platform}_reaction_type"] = str(body.get("type") or "")
        return event

    def _handle_ban(self, event_code: int, body: dict, bot_id: str) -> dict:
        is_voice = event_code == protocol.EVENT_CHANNEL_VOICE_BAN
        event = self._build_base(
            body, "moderation.voice_ban" if is_voice else "moderation.text_ban",
            bot_id, "notice", "group",
            sub_type="voice_ban" if is_voice else "text_ban",
            event_key=f"ban:{body.get('ackId') or uuid.uuid4()}",
        )
        event[f"{self.platform}_area_id"] = str(body.get("area") or "")
        event[f"{self.platform}_ban_until"] = str(body.get("disableTo") or "")
        return event

    def _handle_voice(self, event_code: int, body: dict, bot_id: str) -> dict:
        is_enter = event_code == protocol.EVENT_USER_ENTER_VOICE_CHANNEL
        event = self._build_base(
            body, "voice.enter" if is_enter else "voice.leave",
            bot_id, "notice", "group",
            sub_type="voice_enter" if is_enter else "voice_leave",
            event_key=f"voice:{event_code}:{body.get('channel')}:{int(time.time() * 1000)}",
        )
        event["group_id"] = str(body.get("channel") or "")
        event[f"{self.platform}_area_id"] = str(body.get("area") or "")
        persons = body.get("persons")
        event[f"{self.platform}_persons"] = [str(p) for p in persons] if isinstance(persons, list) else []
        from_channel = str(body.get("fromChannel") or "")
        if from_channel:
            event[f"{self.platform}_from_channel"] = from_channel
        return event

    def _handle_channel_create(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "channel.create", bot_id, "notice", "group",
            sub_type="channel_create", event_key=f"channel_create:{body.get('channel') or uuid.uuid4()}",
        )
        event["group_id"] = str(body.get("channel") or "")
        event[f"{self.platform}_area_id"] = str(body.get("area") or "")
        event[f"{self.platform}_channel_name"] = str(body.get("name") or "")
        event[f"{self.platform}_channel_type"] = str(body.get("channelType") or body.get("type") or "")
        return event

    def _handle_channel_update(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "channel.update", bot_id, "notice", "group",
            sub_type="channel_update", event_key=f"channel_update:{body.get('channel')}:{int(time.time() * 1000)}",
        )
        event["group_id"] = str(body.get("channel") or "")
        event[f"{self.platform}_area_id"] = str(body.get("area") or "")
        event[f"{self.platform}_channel_name"] = str(body.get("name") or "")
        return event

    def _handle_channel_delete(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "channel.delete", bot_id, "notice", "group",
            sub_type="channel_delete", event_key=f"channel_delete:{body.get('channel') or uuid.uuid4()}",
        )
        event["group_id"] = str(body.get("channel") or "")
        event[f"{self.platform}_area_id"] = str(body.get("area") or "")
        return event

    def _handle_area_update(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "area.update", bot_id, "notice", "guild",
            sub_type="area_update", event_key=f"area_update:{body.get('area')}:{int(time.time() * 1000)}",
        )
        event["guild_id"] = str(body.get("area") or "")
        event[f"{self.platform}_area_name"] = str(body.get("name") or "")
        return event

    def _handle_role_change(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "role.change", bot_id, "notice", "guild",
            sub_type="role_change", event_key=f"role:{body.get('ackId') or uuid.uuid4()}",
        )
        event["guild_id"] = str(body.get("area") or "")
        event[f"{self.platform}_role_id"] = _to_int(body.get("roleID"))
        event[f"{self.platform}_role_name"] = str(body.get("name") or "")
        return event

    def _handle_user_update(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "user.update", bot_id, "notice", "user",
            sub_type="user_update", event_key=f"user_update:{body.get('person')}:{int(time.time() * 1000)}",
        )
        event["user_id"] = str(body.get("person") or "")
        return event

    def _handle_login_state(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, "user.login_state", bot_id, "notice", "user",
            sub_type="login_state", event_key=f"login_state:{body.get('person')}:{int(time.time() * 1000)}",
        )
        event["user_id"] = str(body.get("person") or "")
        event[f"{self.platform}_login_state"] = str(body.get("type") or "")
        return event

    def _handle_unknown(self, event_code: int, body: dict, bot_id: str) -> dict:
        event = self._build_base(
            body, f"event_{event_code}", bot_id, "unknown", "unknown",
            event_key=f"unknown:{event_code}:{uuid.uuid4()}",
        )
        event["warning"] = f"未知 Oopz 事件码 {event_code}，原始数据见 oopz_raw"
        return event

    # ==================== 消息分段 ====================

    def _build_segments(self, msg: dict) -> tuple[list[dict], str]:
        """text + mentionList + attachments → 标准段列表与纯文本兜底。"""
        text = str(msg.get("text") or msg.get("content") or "")
        attachments = msg.get("attachments") if isinstance(msg.get("attachments"), list) else []
        mention_list = msg.get("mentionList") if isinstance(msg.get("mentionList"), list) else []

        persons = {str(m.get("person")) for m in mention_list if m.get("person")}
        persons.add("All")
        att_by_key = {str(a.get("fileKey")): a for a in attachments if a.get("fileKey")}
        image_keys = {
            key for key, a in att_by_key.items()
            if str(a.get("attachmentType") or "").upper() == protocol.ATTACHMENT_IMAGE
        }

        token_re = self._build_token_re(persons, image_keys)
        segments: list[dict] = []
        used_keys: set[str] = set()
        pos = 0
        for match in token_re.finditer(text):
            if match.start() > pos:
                segments.append(self.text(text[pos:match.start()]))
            token = match.group(0)
            image_match = protocol.IMAGE_PLACEHOLDER_RE.fullmatch(token)
            if image_match:
                key = image_match.group(3)
                used_keys.add(key)
                segments.append(self._media_segment(att_by_key.get(key), "image"))
            else:
                mention_match = protocol.MENTION_TOKEN_RE.fullmatch(token)
                person = mention_match.group(1) if mention_match else token
                if person == "All":
                    segments.append({"type": "mention_all", "data": {}})
                else:
                    segments.append({"type": "mention", "data": {"user_id": person}})
            pos = match.end()
        if pos < len(text):
            segments.append(self.text(text[pos:]))

        # 未被占位符引用的附件（无文本占位的语音/文件/图片）
        for att in attachments:
            key = str(att.get("fileKey") or "")
            if key and key in used_keys:
                continue
            att_type = str(att.get("attachmentType") or "").upper()
            if att_type == protocol.ATTACHMENT_IMAGE:
                segments.append(self._media_segment(att, "image"))
            elif att_type == protocol.ATTACHMENT_AUDIO:
                segments.append(self._media_segment(att, "audio"))
            elif att_type == protocol.ATTACHMENT_FILE:
                segments.append(self._media_segment(att, "file"))

        return segments or ([self.text(text)] if text else []), self._plain_text(text)

    @staticmethod
    def _build_token_re(persons: set[str], image_keys: set[str]) -> re.Pattern:
        parts = []
        if persons:
            alternation = "|".join(re.escape(p) for p in sorted(persons, key=len, reverse=True))
            parts.append(rf"\(met\)(?:{alternation})\(met\)")
        if image_keys:
            keys = "|".join(re.escape(k) for k in sorted(image_keys, key=len, reverse=True))
            parts.append(rf"!\[IMAGEw\d+h\d+\]\((?:{keys})\)(?:\r?\n)*")
        if not parts:
            return re.compile(r"(?!x)x")  # 永不匹配
        return re.compile("|".join(parts), re.DOTALL)

    def _media_segment(self, att: Optional[dict], seg_type: str) -> dict:
        att = att or {}
        data: dict = {"file_id": str(att.get("fileKey") or "")}
        if att.get("url"):
            data["url"] = str(att["url"])
        if att.get("displayName"):
            data["file_name"] = str(att["displayName"])
        for src, dst in (("width", "width"), ("height", "height"), ("fileSize", "size"), ("duration", "duration")):
            value = _to_int(att.get(src), 0)
            if value > 0:
                data[dst] = value
        return {"type": seg_type, "data": data}

    @staticmethod
    def _plain_text(text: str) -> str:
        plain = protocol.IMAGE_PLACEHOLDER_RE.sub("[图片]", text)
        plain = protocol.MENTION_TOKEN_RE.sub(lambda m: f"@{m.group(1)}", plain)
        return plain.strip()
