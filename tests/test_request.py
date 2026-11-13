"""Request DSL：好友申请复合 request_id 审批"""
import pytest

from OopzAdapter.Core import OopzAdapter

from .conftest import RestStub, make_adapter


def _make_request(adapter, request_id):
    req = OopzAdapter.Request.__new__(OopzAdapter.Request)
    req._adapter = adapter
    req._request_id = request_id
    req._account_id = "main"
    return req


@pytest.mark.asyncio
async def test_accept_parses_composite_request_id(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub({
        "/client/v1/friendship/v1/response": lambda p, b: {"ok": True},
    })
    a._rest["main"] = rest
    req = _make_request(a, "p1:123")

    resp = await req._do_accept()
    assert resp["status"] == "ok"
    call = rest.last("/client/v1/friendship/v1/response")
    assert call["body"] == {"agree": True, "friendRequestId": 123, "target": "p1"}
    assert resp["data"]["request_id"] == "p1:123"
    assert resp["data"]["handled"] == "accept"


@pytest.mark.asyncio
async def test_reject_sends_agree_false(private_key_pem):
    a = make_adapter(private_key_pem)
    rest = RestStub({
        "/client/v1/friendship/v1/response": lambda p, b: {"ok": True},
    })
    a._rest["main"] = rest
    req = _make_request(a, "p2:456")

    resp = await req._do_reject()
    assert resp["status"] == "ok"
    call = rest.last("/client/v1/friendship/v1/response")
    assert call["body"]["agree"] is False
    assert call["body"]["friendRequestId"] == 456


@pytest.mark.asyncio
async def test_invalid_request_id_is_10003(private_key_pem):
    a = make_adapter(private_key_pem)
    a._rest["main"] = RestStub()
    req = _make_request(a, "bogus")

    resp = await req._do_accept()
    assert resp["status"] == "failed"
    assert resp["retcode"] == 10003

    resp = await req._do_reject(request_id="p1:not-a-number")
    assert resp["retcode"] == 10003
