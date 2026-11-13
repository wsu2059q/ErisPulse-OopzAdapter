"""call_api 分发：标准动作路由、错误码映射、账户参数双兼容、登录流程"""
import json

import pytest

from OopzAdapter import protocol

from .conftest import RestStub, make_adapter


@pytest.mark.asyncio
async def test_meta_actions(private_key_pem):
    a = make_adapter(private_key_pem)
    resp = await a.call_api("get_status", account_id="main")
    assert resp["status"] == "ok"
    assert resp["data"]["online"] is True

    resp = await a.call_api("get_version")
    assert resp["data"]["version"] == "0.1.0"

    resp = await a.call_api("get_supported_actions")
    assert "get_self_info" in resp["data"]["actions"]
    assert "oopz.mute_member" in resp["data"]["actions"]


@pytest.mark.asyncio
async def test_unsupported_standard_action_degrades_to_10002(private_key_pem):
    a = make_adapter(private_key_pem)
    resp = await a.call_api("upload_file", account_id="main")
    assert resp["status"] == "failed"
    assert resp["retcode"] == 10002


@pytest.mark.asyncio
async def test_unknown_action_degrades_to_10002(private_key_pem):
    a = make_adapter(private_key_pem)
    resp = await a.call_api("no_such_action", account_id="main")
    assert resp["retcode"] == 10002


@pytest.mark.asyncio
async def test_missing_required_param_is_10003(private_key_pem):
    a = make_adapter(private_key_pem)
    resp = await a.call_api("get_user_info", account_id="main")
    assert resp["retcode"] == 10003
    assert "user_id" in resp["message"]


@pytest.mark.asyncio
async def test_get_user_info_routes_to_person_infos(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub({
        "/client/v1/person/v1/personInfos": lambda p, b: [
            {"uid": "u2", "name": "Bob", "avatar": "http://a/b.png"},
        ],
    })
    a._rest["main"] = rest

    resp = await a.call_api("get_user_info", account_id="main", user_id="u2")
    assert resp["status"] == "ok"
    assert resp["data"]["user_id"] == "u2"
    assert resp["data"]["nickname"] == "Bob"
    call = rest.last("/client/v1/person/v1/personInfos")
    assert call["body"] == {"persons": ["u2"], "commonIds": []}
    assert call["method"] == "POST"


@pytest.mark.asyncio
async def test_platform_failure_maps_to_34100_with_raw(private_key_pem):
    a = make_adapter(private_key_pem)

    class _FailRest(RestStub):
        async def request_data(self, method, path, params=None, body=None):
            raise protocol.OopzApiError("域不存在", payload={"code": 404})

    a._rest["main"] = _FailRest()
    resp = await a.call_api("get_user_info", account_id="main", user_id="u2")
    assert resp["status"] == "failed"
    assert resp["retcode"] == 34100
    assert resp["message"] == "域不存在"
    assert resp["oopz_raw"] == {"code": 404}


@pytest.mark.asyncio
async def test_account_param_dual_compat(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub()
    a._rest["main"] = rest

    resp1 = await a.call_api("get_status", account_id="main")
    resp2 = await a.call_api("get_status", _account_id="main")
    assert resp1["status"] == resp2["status"] == "ok"

    # 框架对未知账户回退首个启用账户，不报错
    resp3 = await a.call_api("get_status", account_id="no-such")
    assert resp3["status"] == "ok"


@pytest.mark.asyncio
async def test_raw_endpoint_passthrough(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub({
        "/custom/v1/endpoint": lambda p, b: {"echo": b},
    })
    a._rest["main"] = rest

    resp = await a.call_api("/custom/v1/endpoint", account_id="main", k=1)
    assert resp["status"] == "ok"
    assert resp["data"] == {"echo": {"k": 1}}


@pytest.mark.asyncio
async def test_group_info_merges_area_and_channel(private_key_pem):
    a = make_adapter(private_key_pem)
    a._channel_area["C1"] = "A1"
    rest = RestStub({
        "/area/v3/info": lambda p, b: {"name": "域名", "owner": "p9", "memberCount": 12},
        "/area/v3/channel/setting/info": lambda p, b: {"name": "闲聊频道"},
    })
    a._rest["main"] = rest

    resp = await a.call_api("get_group_info", account_id="main", group_id="C1")
    assert resp["status"] == "ok"
    assert resp["data"] == {
        "group_id": "C1",
        "group_name": "闲聊频道",
        "area_id": "A1",
        "area_name": "域名",
        "area_owner": "p9",
        "member_count": 12,
    }


@pytest.mark.asyncio
async def test_delete_message_requires_context(private_key_pem):
    a = make_adapter(private_key_pem)
    a._channel_area["C1"] = "A1"
    rest = RestStub()
    a._rest["main"] = rest

    resp = await a.call_api("delete_message", account_id="main", message_id="m1")
    assert resp["retcode"] == 34100  # 无频道/私信上下文

    resp = await a.call_api("delete_message", account_id="main", message_id="m1", channel_id="C1")
    assert resp["status"] == "ok"
    call = rest.last("/im/session/v1/recallGim")
    assert call["body"]["messageId"] == "m1"


@pytest.mark.asyncio
async def test_extension_mute_member_picks_interval(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub({
        "/client/v1/area/v1/member/v1/disableText": lambda p, b: {"muted": True},
    })
    a._rest["main"] = rest

    resp = await a.call_api("oopz.mute_member", account_id="main", area_id="A1", user_id="u3", duration=10)
    assert resp["status"] == "ok"
    call = rest.last("/client/v1/area/v1/member/v1/disableText")
    assert call["params"] == {"area": "A1", "target": "u3", "intervalId": "2"}


@pytest.mark.asyncio
async def test_joined_area_ids_reads_id_field(private_key_pem):
    """订阅列表的域标识字段是 id（不是 areaId），读错会导致永远订阅不上任何域。"""
    a = make_adapter(private_key_pem)
    a._rest["main"] = RestStub({
        "/userSubscribeArea/v1/list": lambda p, b: [
            {"id": "AREA_1", "name": "小eris 的后花园", "owner": "p1"},
            {"id": "AREA_2", "name": "第二个域", "owner": "p9"},
            {"name": "缺 id 的脏数据"},
        ],
    })

    areas = await a._joined_area_ids("main", a._rest["main"])
    assert areas == ["AREA_1", "AREA_2"]

    # 15 秒缓存命中，不重复请求
    await a._joined_area_ids("main", a._rest["main"])
    assert len([c for c in a._rest["main"].calls if c["path"] == "/userSubscribeArea/v1/list"]) == 1


@pytest.mark.asyncio
async def test_subscribe_frame_contains_joined_area_ids(private_key_pem, monkeypatch):
    from OopzAdapter.Core import OopzAdapter, OopzGlobalConfig

    a = make_adapter(private_key_pem)
    a._rest["main"] = RestStub({
        "/userSubscribeArea/v1/list": lambda p, b: [{"id": "AREA_1", "name": "域"}],
    })
    # cfg 实时读配置库，桩实例没有配置库 → 直接替换该属性
    monkeypatch.setattr(
        OopzAdapter, "cfg",
        property(lambda self: OopzGlobalConfig(subscribe_areas=["AREA_2"])),
    )

    frame, areas = await a._build_subscribe_frame("main", {"person_uid": "p1"})
    assert areas == ["AREA_2", "AREA_1"]
    assert frame, "订阅帧不应为空——为空时服务端不会推送任何域事件"
    from OopzAdapter import protocol
    event, body = protocol.decode_frame(frame)
    assert event == protocol.EVENT_SUBSCRIBE_AREA_EVENTS
    assert body["areas"] == ["AREA_2", "AREA_1"]
    assert body["uid"] == "p1"


@pytest.mark.asyncio
async def test_guild_list_reads_id_field(private_key_pem):
    a = make_adapter(private_key_pem)
    a._rest["main"] = RestStub({
        "/userSubscribeArea/v1/list": lambda p, b: [{"id": "AREA_1", "name": "域名"}],
    })
    resp = await a.call_api("get_guild_list", account_id="main")
    assert resp["data"]["guilds"] == [{"guild_id": "AREA_1", "guild_name": "域名"}]


@pytest.mark.asyncio
async def test_password_login_flow(monkeypatch, private_key_pem):
    a = make_adapter(private_key_pem)
    calls = {}

    class FakeResp:
        status = 200
        headers = {}

        async def json(self):
            return {"status": True, "data": {"uid": "u1", "signature": "jwt123", "signatureNearExpired": "0"}}

    async def fake_post(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return FakeResp()

    monkeypatch.setattr(protocol, "client", type("C", (), {"post": staticmethod(fake_post)}))

    creds = await protocol.password_login(
        "https://gateway.oopz.cn", "13800000000", "pw",
        protocol.Signer(private_key_pem), "dev-9",
    )
    assert creds == {"person_uid": "u1", "jwt": "jwt123", "device_id": "dev-9", "expires_at": "0"}
    assert calls["url"] == "https://gateway.oopz.cn/client/v1/login/v2/login"

    body = json.loads(calls["kwargs"]["data"].decode("utf-8"))
    assert body["loginType"] == "PASSWORD"
    assert body["phone"] == "13800000000"
    assert body["deviceId"] == "dev-9"
    assert len(body["code"]) > 0  # OAEP 加密后的密码

    headers = calls["kwargs"]["headers"]
    assert "Oopz-Sign" in headers and "Oopz-Person" not in headers


@pytest.mark.asyncio
async def test_password_login_failure_raises_login_error(monkeypatch, private_key_pem):
    class FakeResp:
        status = 200
        headers = {}

        async def json(self):
            return {"status": False, "message": "密码错误"}

    async def fake_post(url, **kwargs):
        return FakeResp()

    monkeypatch.setattr(protocol, "client", type("C", (), {"post": staticmethod(fake_post)}))

    with pytest.raises(protocol.OopzLoginError, match="密码错误"):
        await protocol.password_login(
            "https://gateway.oopz.cn", "138", "pw",
            protocol.Signer(private_key_pem), "dev",
        )
