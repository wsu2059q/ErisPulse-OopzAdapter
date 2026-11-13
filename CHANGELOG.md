# 更新日志

## 0.1.0 (2026-11-11)

首个版本。

- WebSocket 接入：账密二选一凭据（password / token），JWT 临期自动重登，断线指数退避重连
- 事件转换：频道/私聊消息（text/mention/mention_all/reply/image/audio/file 段）、撤回、编辑、表情回应、好友申请/删除、禁言、语音进出、频道增删改、域变更、身份组变更等 → OneBot12 标准事件
- 发送：Text/Image/@/回复/Important（公告样式）/Recall，图片经 COS 签名票上传；语音/视频/文件按标准 10002 降级
- 好友申请：复合 request_id（`<target>:<friendRequestId>`）无状态审批
- 标准动作：get_self_info / get_user_info / get_friend_list / get_group_info / get_group_list / get_guild_info / get_guild_list / get_channel_list / get_guild_members / delete_message / get_file / leave_guild
- 管理扩展（oopz.*）：频道增删、禁言/禁麦、踢人、域黑名单、成员列表、身份组分配、表情回应、消息置顶、历史消息、好友请求列表与审批
