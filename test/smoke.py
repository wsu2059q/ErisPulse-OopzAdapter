"""真机冒烟脚本。

本脚本不保存、不生成任何凭据，只加载你本地的配置文件：

    python test/smoke.py                    # 默认读 test/config.toml
    python test/smoke.py D:/path/config.toml  # 或显式指定
    OOPZ_CONFIG=D:/path/config.toml python test/smoke.py

配置文件结构见 README「配置说明」；请勿把含凭据的配置文件提交到仓库。
流程：装载框架 → 启动 → 10 秒内收事件 → 回显到来源会话 → 退出。
"""
import asyncio
import os
import sys
from pathlib import Path


def _resolve_config(argv) -> Path:
    explicit = argv[1] if len(argv) > 1 else os.environ.get("OOPZ_CONFIG", "")
    return Path(explicit) if explicit else Path(__file__).parent / "config.toml"


async def main(argv):
    config_path = _resolve_config(argv)
    if not config_path.exists():
        print(
            f"未找到配置文件: {config_path}\n"
            "请按 README「配置说明」创建该文件并填入账户凭据（该文件不应提交到仓库），\n"
            "或通过参数 / 环境变量 OOPZ_CONFIG 指定其他路径。"
        )
        return 1

    import ErisPulse
    ErisPulse.init(config=str(config_path))

    from ErisPulse import sdk

    adapter = sdk.adapter.get("oopz")
    if adapter is None:
        print("适配器 oopz 未加载（检查 pip install -e . 或 entry-point）")
        return 1

    received = []

    @sdk.event.on("message")
    async def on_message(event):
        received.append(event)
        print(f"[收到] {event.get('detail_type')}: {event.get('alt_message', '')!r}")
        try:
            if event.get("detail_type") == "group":
                await adapter.Send.To("group", event["group_id"]).Text(f"回显: {event.get('alt_message', '')}")
            elif event.get("detail_type") == "private":
                await adapter.Send.To("user", event["user_id"]).Text(f"回显: {event.get('alt_message', '')}")
        except Exception as exc:
            print(f"回显失败: {exc}")

    @sdk.event.on("request")
    async def on_request(event):
        print(f"[好友申请] {event.get('user_id')} request_id={event.get('request_id')}")

    await sdk.adapter.startup()
    print("已启动，10 秒内往小号所在频道/私聊发条消息…")
    await asyncio.sleep(10)
    print(f"共收到 {len(received)} 条消息事件")
    await sdk.adapter.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
