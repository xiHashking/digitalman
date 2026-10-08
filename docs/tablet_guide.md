# 安卓平板部署

平板端入口：

```text
https://你的域名/tablet.html
```

页面会保留数字人画面以及麦克风、打断、重连、全屏四项操作。首次点击“开始”时会建立 WebRTC 会话、请求麦克风权限并进入全屏。也可以从 Chrome 菜单选择“添加到主屏幕”，之后以独立全屏应用启动。

## 必须使用 HTTPS

安卓 Chrome 不允许普通局域网 HTTP 页面调用麦克风。`http://电脑IP:8010/tablet.html` 可以看到画面，但无法进行语音对话。生产环境需要在 LiveTalking 前面配置 HTTPS 反向代理，例如 Caddy：

```caddyfile
avatar.example.com {
    reverse_proxy 127.0.0.1:8010
}
```

域名需要解析到运行 LiveTalking 的设备。若只在内网使用，也需要让平板信任内网证书；自签名但未被系统信任的证书仍可能导致麦克风或 WebSocket 失败。

## 可选参数

可以通过地址参数指定形象和参考音频：

```text
/tablet.html?avatar=wav2lip256_avatar1&refaudio=zh-CN-YunxiaNeural
```

跨公网部署且 WebRTC 无法建立时，可以先用以下地址验证 STUN：

```text
/tablet.html?stun=1
```

复杂网络环境仍需要部署 TURN 服务，仅有 STUN 不能保证连通。

## 平板设置建议

1. 将屏幕方向锁定为横屏，并关闭自动息屏或为应用开启“保持唤醒”。
2. 在 Chrome 的站点设置中永久允许麦克风权限。
3. 添加到主屏幕后从图标启动，PWA 配置会隐藏地址栏并使用全屏布局。
4. 正式使用前分别测试断网重连、服务重启、麦克风权限被撤销和长时间连续对话。
