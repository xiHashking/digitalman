# 安卓平板部署

平板端入口：

```text
https://你的域名/tablet.html
```

页面会保留数字人画面以及麦克风、打断、重连、全屏四项操作。首次点击“开始”时会建立 WebRTC 会话、请求麦克风权限并进入全屏。也可以从 Chrome 菜单选择“添加到主屏幕”，之后以独立全屏应用启动。

## 必须使用 HTTPS

安卓 Chrome 不允许普通局域网 HTTP 页面调用麦克风。`http://电脑IP:8010/tablet.html` 可以看到画面，但无法进行语音对话。

本项目可以同时启动 HTTP `8010` 和 HTTPS `8443`。先生成局域网证书：

```bash
./scripts/create_local_https_cert.sh 192.168.10.46
```

重启服务后，在平板上先访问下面的 HTTP 地址下载 CA 证书，并在安卓的“安装证书 / CA 证书”设置中安装：

```text
http://192.168.10.46:8010/local-ca.crt
```

随后通过真正的 HTTPS 地址打开平板页面：

```text
https://192.168.10.46:8443/tablet.html
```

证书只适用于生成时指定的 IP。电脑 IP 变化后需要重新生成并在平板上重新安装。`.certs/` 包含私钥且已被 Git 忽略。

公网生产环境仍建议在 LiveTalking 前面配置 HTTPS 反向代理，例如 Caddy：

```caddyfile
avatar.example.com {
    reverse_proxy 127.0.0.1:8010
}
```

域名需要解析到运行 LiveTalking 的设备。若只在内网使用，也需要让平板信任内网证书；自签名但未被系统信任的证书仍可能导致麦克风或 WebSocket 失败。

## 可选参数

可以通过地址参数指定形象和参考音频：

```text
/tablet.html?avatar=wav2lip_business_consultant_v2&refaudio=zh-CN-XiaoxiaoNeural
```

`data/` 不纳入 Git。新环境首次部署时，可通过已提交的睁眼、闭眼源图重新生成商务角色：

```bash
.venv/bin/python scripts/build_blink_avatar.py
.venv/bin/python -m avatars.wav2lip.genavatar \
  --video_path data/avatar_sources/business_consultant_v2.mp4 \
  --avatar_id wav2lip_business_consultant_v2
```

口型幅度由 `config.yaml` 的 `wav2lip_blend_strength` 控制；当前 `0.45` 为轻微开合，数值越大动作越明显。

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
