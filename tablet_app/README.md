# LiveTalking Android 平板端

这是 `LiveTalking` 的原生 Flutter Android 客户端，不使用 WebView。应用直接连接现有后端：

- WebRTC 接收数字人视频和声音
- Android 原生麦克风采集 PCM16 音频
- WebSocket `/api/asr` 语音识别
- HTTP `/human`、`/is_speaking`、`/interrupt_talk` 对话控制
- 自动断句、暂停麦克风、打断回答、重连和竖屏沉浸全屏

## 环境

仓库根目录通过 FVM 固定 Flutter `3.44.0`。所有 Flutter 命令从仓库根目录或本目录使用 `fvm flutter` 执行。

```bash
fvm flutter --version
cd tablet_app
fvm flutter pub get
fvm flutter run
```

## 连接服务器

首次启动需要在 App 内输入服务器的 `IP:端口`，例如 `192.168.1.100:8010`。也可以随时通过右上角设置按钮修改，应用会保存设置。

平板里的 `127.0.0.1` 指向平板自身，不能用于访问 Mac 上的 LiveTalking 服务。平板和 Mac 应连接同一局域网，并确保 macOS 防火墙允许服务端口访问。

原生 Android 客户端允许局域网 HTTP/WS，因此不依赖浏览器的 HTTPS 麦克风安全策略。

## 构建 APK

```bash
cd tablet_app
fvm flutter analyze
fvm flutter test
fvm flutter build apk --release
```

产物位于：

```text
build/app/outputs/flutter-apk/app-release.apk
```

连接开启 USB 调试的安卓平板后安装：

```bash
adb install -r build/app/outputs/flutter-apk/app-release.apk
```
