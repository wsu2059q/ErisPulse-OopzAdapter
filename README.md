<div align="center">

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="ErisPulse-OopzAdapter" />

# ErisPulse-OopzAdapter

**ErisPulse 的 Oopz 用户账号适配模块**

<p>
  <a href="https://pypi.org/project/ErisPulse-OopzAdapter/"><img src="https://img.shields.io/pypi/v/ErisPulse-OopzAdapter?style=for-the-badge&logo=pypi&logoColor=white" alt="PyPI"></a>
  <a href="https://pypi.org/project/ErisPulse-OopzAdapter/"><img src="https://img.shields.io/badge/Python-3.10+-FFD43B?style=for-the-badge&logo=python&logoColor=blue" alt="Python"></a>
  <a href="https://github.com/ErisPulse/ErisPulse/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License"></a>
  <img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/adapter.svg" alt="ErisPulse">
</p>

*基于用户账号接入 Oopz（非官方接口，建议小号）*

</div>

## 声明与风险提示（使用前必读）

> [!IMPORTANT]
> 本项目与 Oopz **没有任何关系**：本项目未获得 Oopz 官方的任何授权、合作或认可，Oopz 官方亦不为本项目提供任何形式的支持。文档中出现的「Oopz」名称与相关标识仅作平台识别用途，其一切权利归属原权利人。

> [!WARNING]
> - Oopz **没有官方机器人平台或开放 API**。本项目基于对 Oopz 客户端协议的社区逆向研究实现，属于非官方接入方案，**随时可能因客户端更新而失效**
> - 以用户账号自动化接入**存在账号被限制或封禁的风险**，请务必使用专用小号，不要使用主账号
> - 本项目仅供学习与技术研究，使用时请自行遵守 Oopz 用户协议及所在地区法律法规；**因使用本项目产生的一切后果由使用者自行承担**
> - 本项目按「现状」提供，不对其可用性、稳定性、持续性作任何明示或默示的承诺

> [!NOTE]
> 配置中的手机号、密码、私钥、JWT 均为敏感凭据，请妥善保管：不要提交到公开仓库、不要截图分享、不要提供给他人。

## 简介

[Oopz](https://oopz.cn) 是游戏开黑语音社区平台。本适配器是社区实现的非官方接入方案（与 Oopz 无任何关联），让 [ErisPulse](https://github.com/ErisPulse/ErisPulse) 框架能够通过 Oopz 用户账号收发消息、管理域与频道，事件与响应均转换为 ErisPulse 基线协议（OneBot12）标准格式。

## 原生事件 → OneBot12 映射关系

| Oopz WS 事件 | 原生含义 | OneBot12 事件 |
|---|---|---|
| `9` | 频道消息 | `message/group`（`group_id`=频道，`oopz_area_id`/`guild_id`=域） |
| `7` | 私聊消息 | `message/private`（`user_id`=发送者） |
| `8` / `6` | 频道/私聊消息撤回 | `notice` + `sub_type=recall` |
| `57` / `56` | 频道/私聊消息编辑 | `notice` + `sub_type=message_edit`（`oopz_edited_text`） |
| `32` | 表情回应增撤 | `notice` + `sub_type=reaction`（`oopz_emoji`/`oopz_reaction_type`） |
| `2` | 好友申请 | `request/friend`（`request_id`=`<target>:<friendRequestId>` 复合键） |
| `4` | 好友被删 | `notice/friend_decrease` |
| `12` / `11` | 频道禁言/禁麦 | `notice` + `sub_type=text_ban`/`voice_ban` |
| `20` / `19` | 语音频道进出 | `notice` + `sub_type=voice_enter`/`voice_leave`（`oopz_persons`） |
| `25` / `18` / `13` | 频道创建/更新/删除 | `notice` + `oopz_channel_*` |
| `28` | 域信息变更 | `notice/guild` + `sub_type=area_update` |
| `52` | 身份组变更 | `notice/guild` + `sub_type=role_change` |
| `26` / `27` | 用户信息变更 / 登录态 | `notice/user` + `oopz_*` |
| 其它 | 未建模事件 | `unknown` + `warning`（原始数据在 `oopz_raw`） |

所有事件均携带 `oopz_raw`（完整原始数据）与 `oopz_raw_type`；消息段支持 `text` / `mention` / `mention_all` / `reply` / `image` / `audio` / `file`。

## 消息发送示例

```python
adapter = adapter.get("oopz")

# 频道消息（group 目标 = 频道 ID，域由适配器自动解析）
await adapter.Send.To("group", "01KPxxxx频道ID").Text("Hello Oopz!")

# 私聊消息（user 目标 = 对方 UID）
await adapter.Send.To("user", "6adxxxx对方UID").Text("私信内容")

# @人 / @全体 / 回复
await adapter.Send.To("group", "01KPxxxx").At("6adxxxx").Text(" 叫你呢")
await adapter.Send.To("group", "01KPxxxx").AtAll().Text("全体注意")
await adapter.Send.To("group", "01KPxxxx").Reply("原始messageId").Text("回复内容")

# 图片（支持 URL / 本地路径 / bytes / base64://）
await adapter.Send.To("group", "01KPxxxx").Image("https://example.com/cat.png")

# 公告样式消息
await adapter.Send.To("group", "01KPxxxx").Important().Text("重要公告")

# OneBot12 段直接发送
await adapter.Send.To("group", "01KPxxxx").Raw_ob12([
    {"type": "text", "data": {"text": "看图 "}},
    {"type": "image", "data": {"file": "https://example.com/img.png"}},
])

# 撤回（需要可见目标上下文）
await adapter.Send.To("group", "01KPxxxx").Recall("messageId")

# 多账号
await adapter.Send.Using("小号甲").To("group", "01KPxxxx").Text("来自甲")
```

> 语音/视频/文件发送：Oopz 平台仅支持图片上传，其余媒体段按标准以 `retcode=10002` 降级拒绝。

## 好友申请处理

```python
@adapter.on_request
async def handle(event):
    # event.request_id 为复合键 "<target>:<friendRequestId>"
    await event.approve()   # 同意
    # await event.reject()  # 拒绝
```

## 标准与扩展 API

```python
await adapter.Api.Using("小号甲").get_self_info()
await adapter.Api.Using("小号甲").get_user_info(user_id="6adxxxx")
await adapter.Api.Using("小号甲").get_group_info(group_id="01KPxxxx")
await adapter.Api.call("oopz.mute_member", area_id="域ID", user_id="对方UID", duration=10)
await adapter.Api.call("oopz.get_history_messages", area_id="域ID", channel_id="频道ID", size=50)
```

完整动作列表见 [platform-features.md](platform-features.md)。

## 凭据获取指引

**账密模式只需手动提取一样东西：客户端签名私钥（`private_key`）**——uid / JWT / 设备 ID 适配器会自动登录换新，不用管。

1. 浏览器登录 [web.oopz.cn](https://web.oopz.cn)（建议小号），`F12` → Console 粘贴：

```js
const raw = crypto.subtle.sign.bind(crypto.subtle);
crypto.subtle.sign = async (a, k, d) => {
  if (k.extractable && !window.pk) {
    const b64 = btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey("pkcs8", k))));
    window.pk = 'private_key = "-----BEGIN PRIVATE KEY-----\\n' + b64.replace(/(.{64})(?=.)/g, "$1\\n") + '\\n-----END PRIVATE KEY-----"';
    console.log("%c" + window.pk, "color:#2ecc71;font-weight:bold;font-size:15px;line-height:1.9;background:#1e1e1e;padding:12px;border-radius:6px");
    navigator.clipboard?.writeText(window.pk).catch(() => {});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([window.pk], { type: "text/plain" }));
    link.download = "oopz_private_key.txt";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    console.log("%c已同步：控制台打印 + 剪贴板 + 下载 oopz_private_key.txt，粘进账户配置段，补上 phone / password 即可", "color:#2ecc71;font-weight:bold;font-size:14px");
  }
  return raw(a, k, d);
};
console.log("%c发一条消息或切一个频道，private_key 会打出 + 复制 + 下载文件（勿刷新页面）", "color:#e67e22;font-weight:bold;font-size:14px");
```

2. **发一条消息或切换一个频道**——客户端发起签名请求时私钥被捕获，`private_key = "..."` 整行同步三路交付：控制台打印、剪贴板、下载 `oopz_private_key.txt`
3. 粘进 `[Oopz_Adapter.accounts.<名字>]`，补上 `phone` / `password`，完成

> **原理**：私钥是客户端做请求签名用的 WebCrypto 密钥，脚本 hook 住 `crypto.subtle.sign` 将其导出；**中途刷新页面会清掉 hook**，需重新粘贴。该私钥全客户端通用、非账号专属。

> [!NOTE]
> token 四件套模式（uid / JWT / 设备 ID 在 `localStorage["session__OopzSession"]`）**仅作备用**：仅当账密登录被风控拦截时改用它——凭据来自真实浏览器会话、不走登录接口，但 JWT 过期后需重新提取，日常不推荐。

## 配置说明

```toml
[Oopz_Adapter]
gateway_url = "https://gateway.oopz.cn"   # 网关地址（一般无需修改）
heartbeat_interval = 10                    # WS 心跳间隔（秒）
auto_subscribe_areas = true                # 连接后自动订阅已加入的域
subscribe_areas = []                       # 额外订阅的域 ID
ignore_self = true                         # 忽略账号自己发的消息

[Oopz_Adapter.accounts.default]
enabled = true
login_type = "password"        # password=账密自动登录（推荐）；token=四件套（仅密码登录被风控时的备用）
phone = "13800000000"
password = "your-password"
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
# token 备用模式另需（JWT 过期需重新提取）：
# person_uid = "你的UID"
# jwt_signature = "你的JWT"
# device_id = "提取到的设备ID"
```

多账号：复制多个 `[Oopz_Adapter.accounts.<名字>]` 段即可，事件会通过 `self.account_id` 标记来源账户。

## 运行环境

- Python 3.10+
- ErisPulse ≥ 2.7.1（软依赖，运行时检测）
- 依赖：`cryptography`

## 许可证

[MIT](LICENSE)

---

> [!IMPORTANT]
> 再次声明：本项目是独立的开源社区项目，与 Oopz 及其运营方**不存在任何隶属、合作、授权或认可关系**，亦不代表 Oopz 官方立场；Oopz 官方未参与、不知晓亦不支持本项目。本项目仅供学习与技术研究，使用者应自行承担一切使用后果。
