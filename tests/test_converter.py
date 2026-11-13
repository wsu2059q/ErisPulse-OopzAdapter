"""事件转换：WS 事件码 → OneBot12 标准事件"""
import json

from OopzAdapter import protocol
from OopzAdapter.Converter import OopzConverter

from .conftest import make_channel_message


def _convert(raw):
    return OopzConverter().convert(raw, bot_id="bot1")


def test_protocol_frames_are_suppressed():
    for code in (1, 21, 249, 253, 254):
        assert _convert({"event": code, "body": {}}) is None


def test_channel_message_conversion():
    ev = _convert(make_channel_message())
    assert ev["type"] == "message"
    assert ev["detail_type"] == "group"
    assert ev["sub_type"] == "text"
    assert ev["group_id"] == "C1"
    assert ev["guild_id"] == "A1"
    assert ev["oopz_area_id"] == "A1"
    assert ev["user_id"] == "u1"
    assert ev["message_id"] == "m1"
    assert ev["id"] == "bot1:msg:m1"
    assert ev["time"] == 1700000000  # 微秒 → 秒
    assert ev["platform"] == "oopz"
    assert ev["self"] == {"platform": "oopz", "user_id": "bot1"}
    assert ev["oopz_raw"]["data"]["messageId"] == "m1"
    assert ev["oopz_raw_type"] == "message"
    assert ev["message"] == [{"type": "text", "data": {"text": "hi"}}]
    assert ev["alt_message"] == "hi"


def test_private_message_conversion():
    raw = make_channel_message(area="", channel="S1", extra={"target": "u1"})
    raw["event"] = 7
    ev = _convert(raw)
    assert ev["type"] == "message"
    assert ev["detail_type"] == "private"
    assert ev["user_id"] == "u1"
    assert "group_id" not in ev
    assert ev["oopz_raw_type"] == "message.private"


def test_mention_segmentation():
    text = "hello (met)u2(met) world"
    raw = make_channel_message(text=text, extra={
        "mentionList": [{"person": "u2", "isBot": False, "botType": "", "offset": 6}],
    })
    ev = _convert(raw)
    types = [seg["type"] for seg in ev["message"]]
    assert types == ["text", "mention", "text"]
    assert ev["message"][1]["data"]["user_id"] == "u2"
    assert ev["alt_message"] == "hello @u2 world"


def test_mention_all_segmentation():
    raw = make_channel_message(text="(met)All(met) 上线啦", extra={"isMentionAll": True})
    ev = _convert(raw)
    assert ev["message"][0]["type"] == "mention_all"
    assert ev["alt_message"].endswith("上线啦")


def test_image_placeholder_becomes_image_segment():
    text = "看图 ![IMAGEw10h20](key1) 完"
    raw = make_channel_message(text=text, extra={
        "attachments": [{
            "attachmentType": "IMAGE", "fileKey": "key1", "url": "https://img.example/1.png",
            "displayName": "1.png", "fileSize": 123, "width": 10, "height": 20,
        }],
    })
    ev = _convert(raw)
    types = [seg["type"] for seg in ev["message"]]
    assert types == ["text", "image", "text"]
    img = ev["message"][1]["data"]
    assert img["file_id"] == "key1"
    assert img["url"] == "https://img.example/1.png"
    assert img["width"] == 10 and img["height"] == 20 and img["size"] == 123
    assert "[图片]" in ev["alt_message"]


def test_unreferenced_audio_attachment_appends_audio_segment():
    raw = make_channel_message(text="语音来了", extra={
        "attachments": [{
            "attachmentType": "AUDIO", "fileKey": "k-audio", "url": "https://img.example/a.mp3",
            "displayName": "a.mp3", "duration": 5,
        }],
    })
    ev = _convert(raw)
    types = [seg["type"] for seg in ev["message"]]
    assert types == ["text", "audio"]
    audio = ev["message"][1]["data"]
    assert audio["duration"] == 5 and audio["file_id"] == "k-audio"


def test_reply_reference_kept():
    raw = make_channel_message(extra={"referenceMessageId": "m0"})
    ev = _convert(raw)
    assert ev["oopz_reference_message_id"] == "m0"


def test_recall_notice():
    raw = {"event": 8, "body": {"area": "A1", "channel": "C1", "messageId": "m9", "person": "u1"}}
    ev = _convert(raw)
    assert ev["type"] == "notice"
    assert ev["detail_type"] == "group"
    assert ev["sub_type"] == "recall"
    assert ev["message_id"] == "m9"
    assert ev["group_id"] == "C1"
    assert ev["id"] == "bot1:recall:m9"

    raw["event"] = 6
    ev = _convert(raw)
    assert ev["detail_type"] == "private"


def test_message_edit_notice():
    raw = {"event": 57, "body": {"data": json.dumps({
        "messageId": "m1", "person": "u1", "area": "A1", "channel": "C1",
        "text": "edited", "timestamp": "1700000000000000",
    })}}
    ev = _convert(raw)
    assert ev["type"] == "notice"
    assert ev["sub_type"] == "message_edit"
    assert ev["message_id"] == "m1"
    assert ev["oopz_edited_text"] == "edited"


def test_friend_request_composite_request_id():
    raw = {"event": 2, "body": {
        "person": "p1", "friendRequestId": 123, "name": "小明", "avatar": "http://a",
        "createTime": "1700000000",
    }}
    ev = _convert(raw)
    assert ev["type"] == "request"
    assert ev["detail_type"] == "friend"
    assert ev["request_id"] == "p1:123"
    assert ev["user_id"] == "p1"
    assert ev["user_nickname"] == "小明"


def test_friend_delete_notice():
    ev = _convert({"event": 4, "body": {"person": "p1"}})
    assert ev["type"] == "notice"
    assert ev["detail_type"] == "friend_decrease"
    assert ev["user_id"] == "p1"


def test_reaction_notice():
    raw = {"event": 32, "body": {
        "messageId": "m1", "area": "A1", "channel": "C1", "person": "u1",
        "emoji": "👍", "type": "REPLY",
    }}
    ev = _convert(raw)
    assert ev["type"] == "notice"
    assert ev["sub_type"] == "reaction"
    assert ev["oopz_emoji"] == "👍"
    assert ev["oopz_reaction_type"] == "REPLY"
    assert ev["group_id"] == "C1"


def test_ban_and_voice_notices():
    ev = _convert({"event": 12, "body": {"area": "A1", "disableTo": "1700000900000"}})
    assert ev["sub_type"] == "text_ban"
    assert ev["oopz_area_id"] == "A1"

    ev = _convert({"event": 20, "body": {"area": "A1", "channel": "C1", "persons": ["u1", "u2"], "activeNum": 2}})
    assert ev["sub_type"] == "voice_enter"
    assert ev["group_id"] == "C1"
    assert ev["oopz_persons"] == ["u1", "u2"]


def test_channel_lifecycle_notices():
    ev = _convert({"event": 25, "body": {"area": "A1", "channel": "C2", "name": "新频道", "type": "TEXT"}})
    assert ev["sub_type"] == "channel_create"
    assert ev["oopz_channel_name"] == "新频道"
    assert ev["group_id"] == "C2"

    ev = _convert({"event": 13, "body": {"area": "A1", "channel": "C2"}})
    assert ev["sub_type"] == "channel_delete"


def test_area_and_role_notices():
    ev = _convert({"event": 28, "body": {"area": "A1", "name": "域名"}})
    assert ev["detail_type"] == "guild"
    assert ev["guild_id"] == "A1"

    ev = _convert({"event": 52, "body": {"area": "A1", "roleID": 5, "name": "管理"}})
    assert ev["oopz_role_id"] == 5


def test_unknown_event_carries_warning_and_raw():
    ev = _convert({"event": 999, "body": {"foo": "bar"}})
    assert ev["type"] == "unknown"
    assert "warning" in ev
    assert ev["oopz_raw"] == {"foo": "bar"}
    assert ev["oopz_raw_type"] == "event_999"


def test_double_serialized_body_data_is_tolerated():
    msg = {"type": "TEXT", "data": json.dumps({
        "messageId": "m1", "timestamp": "1700000000000000", "person": "u1",
        "text": "hi", "content": "hi", "area": "A1", "channel": "C1",
    })}
    ev = _convert({"event": 9, "body": msg})
    assert ev["message_id"] == "m1"
