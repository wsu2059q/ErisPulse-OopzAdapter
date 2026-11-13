"""发送链路：Raw_ob12 分段 → 平台 payload 组装与降级"""
import pytest

from OopzAdapter import protocol

from .conftest import RestStub, make_adapter


def _png(width=30, height=40):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def _message(payload):
    assert set(payload.keys()) == {"message"}
    return payload["message"]


@pytest.mark.asyncio
async def test_group_text_send_builds_gim_payload(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub()
    a._channel_area["C1"] = "A1"
    send = type("S", (), {"_style_tags": None})()

    resp = await a._build_and_send(rest, "main", "group", "C1", [
        {"type": "text", "data": {"text": "hi"}},
    ], send)

    assert resp["status"] == "ok"
    assert resp["message_id"] == "msg-new-1"
    call = rest.last("/im/session/v2/sendGimMessage")
    msg = _message(call["body"])
    assert msg["text"] == msg["content"] == "hi"
    assert msg["area"] == "A1" and msg["channel"] == "C1"
    assert msg["mentionList"] == [] and msg["isMentionAll"] is False
    assert msg["attachments"] == []
    assert msg["clientMessageId"].isdigit()


@pytest.mark.asyncio
async def test_private_send_opens_session_and_sets_target(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub()
    send = type("S", (), {"_style_tags": None})()

    resp = await a._build_and_send(rest, "main", "user", "u9", [
        {"type": "reply", "data": {"message_id": "m0"}},
        {"type": "text", "data": {"text": "在吗 "}},
        {"type": "mention", "data": {"user_id": "u9"}},
        {"type": "mention_all", "data": {}},
    ], send)

    assert resp["status"] == "ok"
    session_call = rest.last("/client/v1/chat/v1/to")
    assert session_call["params"] == {"target": "u9"}

    msg = _message(rest.last("/im/session/v2/sendImMessage")["body"])
    assert msg["target"] == "u9"
    assert msg["channel"] == "session-u9"  # 会话 ID 作为 channel
    assert msg["referenceMessageId"] == "m0"
    assert msg["isMentionAll"] is True
    assert "(met)u9(met)" in msg["text"]
    assert "(met)All(met)" in msg["text"]
    assert msg["mentionList"][0]["person"] == "u9"

    # 二次发送复用会话缓存，不再请求开.session 端点
    await a._build_and_send(rest, "main", "user", "u9", [
        {"type": "text", "data": {"text": "again"}},
    ], send)
    to_calls = [c for c in rest.calls if c["path"] == "/client/v1/chat/v1/to"]
    assert len(to_calls) == 1


@pytest.mark.asyncio
async def test_group_send_resolves_area_when_cache_missing(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub({
        "/userSubscribeArea/v1/list": lambda p, b: [{"areaId": "A1"}],
        "/client/v1/area/v1/detail/v1/channels": lambda p, b: [
            {"groupId": "g1", "channels": [{"channelId": "C7", "name": "闲聊"}]},
        ],
    })
    send = type("S", (), {"_style_tags": None})()

    resp = await a._build_and_send(rest, "main", "group", "C7", [
        {"type": "text", "data": {"text": "ok"}},
    ], send)

    assert resp["status"] == "ok"
    assert a._channel_area["C7"] == "A1"  # 解析结果回填缓存
    msg = _message(rest.last("/im/session/v2/sendGimMessage")["body"])
    assert msg["area"] == "A1"


@pytest.mark.asyncio
async def test_image_send_uploads_and_inserts_placeholder(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub()
    a._channel_area["C1"] = "A1"
    send = type("S", (), {"_style_tags": None})()

    resp = await a._build_and_send(rest, "main", "group", "C1", [
        {"type": "text", "data": {"text": "图:"}},
        {"type": "image", "data": {"file": _png()}},
    ], send)

    assert resp["status"] == "ok"
    upload = rest.last("/rtc/v1/cos/v1/signedUploadUrl")
    assert upload["body"]["ext"] == "png"

    msg = _message(rest.last("/im/session/v2/sendGimMessage")["body"])
    assert msg["attachments"][0]["fileKey"] == "uploaded-key"
    assert "![IMAGEw30h40](uploaded-key)" in msg["text"]


@pytest.mark.asyncio
async def test_voice_segment_degrades_to_10002(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub()
    send = type("S", (), {"_style_tags": None})()

    resp = await a._build_and_send(rest, "main", "group", "C1", [
        {"type": "voice", "data": {"file": "x.mp3"}},
    ], send)
    assert resp["status"] == "failed"
    assert resp["retcode"] == 10002

    resp = await a._build_and_send(rest, "main", "group", "C1", [
        {"type": "keyboard", "data": {}},
    ], send)
    assert resp["retcode"] == 10002


@pytest.mark.asyncio
async def test_important_modifier_sets_style_tags(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub()
    a._channel_area["C1"] = "A1"
    send = type("S", (), {"_style_tags": None})()
    send._style_tags = [protocol.STYLE_TAG_IMPORTANT]

    await a._build_and_send(rest, "main", "group", "C1", [
        {"type": "text", "data": {"text": "公告"}},
    ], send)

    msg = _message(rest.last("/im/session/v2/sendGimMessage")["body"])
    assert msg["styleTags"] == ["IMPORTANT"]


@pytest.mark.asyncio
async def test_recall_dispatches_by_target_type(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub()
    a._rest["main"] = rest
    a._channel_area["C1"] = "A1"

    from OopzAdapter.Core import OopzAdapter

    recall_send = OopzAdapter.Send.__new__(OopzAdapter.Send)
    recall_send._adapter = a
    recall_send._target_type = "group"
    recall_send._target_id = "C1"
    recall_send._account_id = "main"

    resp = await OopzAdapter.Send._do_recall(recall_send, "m9")
    assert resp["status"] == "ok"
    call = rest.last("/im/session/v1/recallGim")
    assert call["body"]["messageId"] == "m9"
    assert call["body"]["area"] == "A1" and call["body"]["channel"] == "C1"
    assert resp["message_id"] == "m9"
