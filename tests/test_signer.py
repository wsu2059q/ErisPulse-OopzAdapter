"""协议层：签名、登录体、WS 信封、图片嗅探"""
import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from OopzAdapter import protocol


def _public_key(pem: str):
    private = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    return private.public_key()


def test_build_headers_carry_full_signature(private_key_pem):
    signer = protocol.Signer(private_key_pem)
    headers = signer.build_headers(
        sign_path="/im/session/v2/sendGimMessage",
        body_str='{"a":1}',
        device_id="dev-1",
        person_uid="p1",
        jwt="jwt-token",
    )
    for key in (
        "Oopz-Sign", "Oopz-Request-Id", "Oopz-Time", "Oopz-App-Version-Number",
        "Oopz-Channel", "Oopz-Device-Id", "Oopz-Platform", "Oopz-Web",
        "Oopz-Person", "Oopz-Signature",
    ):
        assert key in headers, f"缺少请求头 {key}"
    assert headers["Oopz-Person"] == "p1"
    assert headers["Oopz-Signature"] == "jwt-token"
    assert headers["Oopz-Device-Id"] == "dev-1"

    # 验证签名可被公钥校验：Oopz-Sign = RSA-SHA256(md5(path+body)hex + ts)
    ts = headers["Oopz-Time"]
    digest = hashlib.md5(('/im/session/v2/sendGimMessage{"a":1}').encode("utf-8")).hexdigest()
    _public_key(private_key_pem).verify(
        base64.b64decode(headers["Oopz-Sign"]),
        (digest + ts).encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_login_headers_omit_person_and_signature(private_key_pem):
    signer = protocol.Signer(private_key_pem)
    headers = signer.build_headers(
        sign_path=protocol.LOGIN_PATH, body_str="{}", device_id="dev-1",
    )
    assert "Oopz-Person" not in headers
    assert "Oopz-Signature" not in headers


def test_normalize_private_key_tolerates_pasted_forms(private_key_pem):
    quoted = "'" + private_key_pem.replace("\n", "\\n") + "'"
    assert protocol.normalize_private_key(quoted) == private_key_pem.strip()
    assert protocol.normalize_private_key(f'  {private_key_pem}  ') == private_key_pem.strip()


def test_encrypt_password_is_base64_rsa_block():
    encrypted = protocol.encrypt_password("secret-password")
    raw = base64.b64decode(encrypted)
    assert len(raw) == 256  # 2048-bit RSA
    assert isinstance(encrypted, str)


def test_frame_encode_decode_roundtrip():
    raw = protocol.encode_frame(253, {"person": "p1", "signature": "jwt"})
    envelope = json.loads(raw)
    assert envelope["event"] == 253
    assert isinstance(envelope["body"], str)  # body 双层序列化

    event, body = protocol.decode_frame(raw)
    assert event == 253
    assert body == {"person": "p1", "signature": "jwt"}

    # body 为 dict 的宽容路径
    event, body = protocol.decode_frame(json.dumps({"event": 9, "body": {"a": 1}}))
    assert (event, body) == (9, {"a": 1})

    event, body = protocol.decode_frame("not-json")
    assert event == -1 and body == {}


def test_is_auth_rejected():
    event, body = protocol.decode_frame(protocol.encode_frame(21, {"checkRes": False}))
    assert protocol.is_auth_rejected(event, body)
    event, body = protocol.decode_frame(protocol.encode_frame(21, {"checkRes": True}))
    assert not protocol.is_auth_rejected(event, body)


def test_build_subscribe_frame_skips_empty():
    assert protocol.build_subscribe_frame([], "p1") == ""
    event, body = protocol.decode_frame(protocol.build_subscribe_frame(["a1", " a2 "], "p1"))
    assert event == 249
    assert body == {"areas": ["a1", "a2"], "type": 1, "uid": "p1"}


def test_build_message_payload_v2_wrapper():
    payload = protocol.build_message_payload(
        text="hi", area="A1", channel="C1", mention_list=[{"person": "u"}],
        is_mention_all=True, style_tags=["IMPORTANT"], reference_message_id="m0",
    )
    assert set(payload.keys()) == {"message"}
    msg = payload["message"]
    assert msg["text"] == msg["content"] == "hi"
    assert msg["area"] == "A1" and msg["channel"] == "C1"
    assert msg["isMentionAll"] is True
    assert msg["styleTags"] == ["IMPORTANT"]
    assert msg["referenceMessageId"] == "m0"
    assert msg["clientMessageId"].isdigit()


def test_mention_and_image_text_conventions():
    assert protocol.mention_token("u1") == "(met)u1(met)"
    assert protocol.MENTION_ALL_TOKEN == "(met)All(met)"
    assert protocol.image_placeholder(10, 20, "key") == "![IMAGEw10h20](key)\n"
    match = protocol.IMAGE_PLACEHOLDER_RE.match("![IMAGEw10h20](abc)")
    assert match.groups() == ("10", "20", "abc")


def test_guess_image_ext_and_dimensions():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (300).to_bytes(4, "big") + (200).to_bytes(4, "big")
    assert protocol.guess_image_ext(png_header) == "png"
    assert protocol.guess_image_ext(b"\xff\xd8\xff\xe0rest") == "jpg"
    assert protocol.guess_image_ext(b"whatever", "photo.jpeg") == "jpg"

    assert protocol.guess_image_dimensions(png_header) == (300, 200)

    gif = b"GIF89a" + (64).to_bytes(2, "little") + (48).to_bytes(2, "little")
    assert protocol.guess_image_dimensions(gif) == (64, 48)

    assert protocol.guess_image_dimensions(b"") == (0, 0)


def test_pick_mute_interval_snaps_to_supported_slots():
    assert protocol.pick_mute_interval(10) == "2"        # 5分钟
    assert protocol.pick_mute_interval(60, voice=True) == "9"
    assert protocol.pick_mute_interval(100000) == "6"    # 7天


def test_decode_jwt_exp():
    import time as _time

    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1700000000}).encode()).decode().rstrip("=")
    token = f"header.{payload}.signature"
    assert protocol.decode_jwt_exp(token) == 1700000000
    assert protocol.decode_jwt_exp("garbage") is None


def test_new_client_message_id_shape():
    mid = protocol.new_client_message_id()
    assert mid.isdigit()
    assert mid.endswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"))
