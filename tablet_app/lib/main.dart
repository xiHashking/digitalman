import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import 'livetalking_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SystemChrome.setPreferredOrientations(const <DeviceOrientation>[
    DeviceOrientation.portraitUp,
  ]);
  await SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
  await WakelockPlus.enable();
  runApp(const LiveTalkingApp());
}

class LiveTalkingApp extends StatelessWidget {
  const LiveTalkingApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '数字人助手',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF090B0F),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF38D39F),
          error: Color(0xFFFF626A),
          surface: Color(0xFF11161D),
        ),
        fontFamilyFallback: const <String>['Noto Sans CJK SC', 'sans-serif'],
      ),
      home: const TabletConversationPage(),
    );
  }
}

class TabletConversationPage extends StatefulWidget {
  const TabletConversationPage({super.key});

  @override
  State<TabletConversationPage> createState() => _TabletConversationPageState();
}

class _TabletConversationPageState extends State<TabletConversationPage>
    with WidgetsBindingObserver {
  late final LiveTalkingController _controller;
  bool _initializing = true;
  bool _chromeVisible = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _controller = LiveTalkingController();
    unawaited(_initialize());
  }

  Future<void> _initialize() async {
    await _controller.initialize();
    if (mounted) setState(() => _initializing = false);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && !_chromeVisible) {
      unawaited(
        SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky),
      );
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller.dispose();
    super.dispose();
  }

  Future<void> _toggleSystemChrome() async {
    final nextVisible = !_chromeVisible;
    if (nextVisible) {
      await SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    } else {
      await SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    }
    if (mounted) setState(() => _chromeVisible = nextVisible);
  }

  Future<void> _startExperience() async {
    if (_controller.serverAddress.isEmpty) {
      await _showSettings(reconnectAfterSave: false);
      if (!mounted || _controller.serverAddress.isEmpty) return;
    }
    await _controller.connect();
  }

  Future<void> _showSettings({bool reconnectAfterSave = true}) async {
    final textController = TextEditingController(
      text: _controller.serverAddress,
    );
    String? validationError;
    final shouldReconnect = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (dialogContext) {
        return StatefulBuilder(
          builder: (context, setDialogState) {
            return AlertDialog(
              backgroundColor: const Color(0xFF11161D),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
              ),
              title: const Text('服务器设置'),
              content: SizedBox(
                width: 460,
                child: TextField(
                  controller: textController,
                  autofocus: true,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: InputDecoration(
                    labelText: '服务器地址',
                    hintText: '例如：192.168.1.100:8010',
                    helperText: '填写运行 LiveTalking 服务的电脑局域网地址',
                    errorText: validationError,
                    border: const OutlineInputBorder(),
                  ),
                ),
              ),
              actions: <Widget>[
                TextButton(
                  onPressed: () => Navigator.of(dialogContext).pop(false),
                  child: const Text('取消'),
                ),
                FilledButton(
                  onPressed: () async {
                    try {
                      await _controller.saveServerAddress(textController.text);
                      if (dialogContext.mounted) {
                        Navigator.of(dialogContext).pop(true);
                      }
                    } on FormatException catch (error) {
                      setDialogState(() => validationError = error.message);
                    }
                  },
                  child: const Text('保存'),
                ),
              ],
            );
          },
        );
      },
    );
    textController.dispose();
    if (!_chromeVisible) {
      await SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    }
    if (shouldReconnect == true && reconnectAfterSave && _controller.started) {
      await _controller.connect();
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          return Stack(
            fit: StackFit.expand,
            children: <Widget>[
              const ColoredBox(color: Color(0xFF090B0F)),
              if (_controller.rendererReady)
                RTCVideoView(
                  _controller.videoRenderer,
                  objectFit: RTCVideoViewObjectFit.RTCVideoViewObjectFitContain,
                ),
              const _TopShade(),
              const _BottomShade(),
              _buildTopBar(),
              _buildBottomControls(),
              if (!_controller.started ||
                  _controller.state == ConversationState.error)
                _buildStartLayer(),
            ],
          );
        },
      ),
    );
  }

  Widget _buildTopBar() {
    return SafeArea(
      minimum: const EdgeInsets.fromLTRB(18, 14, 18, 0),
      child: Align(
        alignment: Alignment.topCenter,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            _StatusPill(
              state: _controller.state,
              label: _initializing ? '正在初始化' : _controller.statusText,
            ),
            const Spacer(),
            _IconControl(
              icon: Icons.refresh_rounded,
              tooltip: '重新连接',
              onPressed: _initializing ? null : _controller.connect,
            ),
            const SizedBox(width: 10),
            _IconControl(
              icon: Icons.settings_outlined,
              tooltip: '服务器设置',
              onPressed: _initializing ? null : _showSettings,
            ),
            const SizedBox(width: 10),
            _IconControl(
              icon: _chromeVisible
                  ? Icons.fullscreen_rounded
                  : Icons.fullscreen_exit_rounded,
              tooltip: _chromeVisible ? '进入沉浸全屏' : '显示系统导航栏',
              onPressed: _toggleSystemChrome,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildBottomControls() {
    return SafeArea(
      minimum: const EdgeInsets.fromLTRB(18, 0, 18, 18),
      child: Align(
        alignment: Alignment.bottomCenter,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                _ActionControl(
                  icon: _controller.micPaused
                      ? Icons.mic_off_rounded
                      : Icons.mic_rounded,
                  label: _controller.micPaused ? '继续对话' : '聆听中',
                  primary: !_controller.micPaused,
                  onPressed: _controller.canControl
                      ? _controller.toggleMicrophone
                      : null,
                ),
                const SizedBox(width: 12),
                _ActionControl(
                  icon: Icons.stop_rounded,
                  label: '打断',
                  danger: true,
                  onPressed: _controller.canInterrupt
                      ? _controller.interrupt
                      : null,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStartLayer() {
    final hasError = _controller.state == ConversationState.error;
    return ColoredBox(
      color: const Color(0xDD05070A),
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(
                '数字人助手',
                style: Theme.of(context).textTheme.displaySmall?.copyWith(
                  color: Colors.white,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0,
                ),
              ),
              const SizedBox(height: 10),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 620),
                child: Text(
                  hasError
                      ? _controller.errorMessage
                      : _initializing
                      ? '正在初始化设备'
                      : _controller.serverAddress.isEmpty
                      ? '请先设置 LiveTalking 服务器地址'
                      : '轻触开始语音对话',
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                    color: Color(0xFFB8C0CC),
                    fontSize: 17,
                    letterSpacing: 0,
                  ),
                ),
              ),
              const SizedBox(height: 26),
              SizedBox(
                width: 180,
                height: 58,
                child: FilledButton.icon(
                  onPressed: _initializing ? null : _startExperience,
                  icon: Icon(
                    hasError ? Icons.refresh_rounded : Icons.play_arrow_rounded,
                  ),
                  label: Text(
                    _controller.serverAddress.isEmpty
                        ? '设置并开始'
                        : hasError
                        ? '重试'
                        : '开始',
                  ),
                  style: FilledButton.styleFrom(
                    foregroundColor: const Color(0xFF07150F),
                    backgroundColor: const Color(0xFF38D39F),
                    disabledBackgroundColor: const Color(0x8038D39F),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                    textStyle: const TextStyle(
                      fontSize: 18,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 14),
              TextButton.icon(
                onPressed: _initializing ? null : _showSettings,
                icon: const Icon(Icons.dns_outlined, size: 19),
                label: Text(
                  _controller.serverAddress.isEmpty
                      ? '设置服务器地址'
                      : _controller.serverAddress,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.state, required this.label});

  final ConversationState state;
  final String label;

  @override
  Widget build(BuildContext context) {
    final dotColor = switch (state) {
      ConversationState.listening => const Color(0xFF38D39F),
      ConversationState.error => const Color(0xFFFF626A),
      ConversationState.speaking => const Color(0xFF57A8FF),
      _ => const Color(0xFF8C96A5),
    };
    return Container(
      constraints: const BoxConstraints(minHeight: 46, maxWidth: 300),
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 9),
      decoration: BoxDecoration(
        color: const Color(0xCC0E1218),
        border: Border.all(color: const Color(0x2EFFFFFF)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Container(
            width: 9,
            height: 9,
            decoration: BoxDecoration(
              color: dotColor,
              shape: BoxShape.circle,
              boxShadow: <BoxShadow>[
                BoxShadow(
                  color: dotColor.withValues(alpha: 0.28),
                  spreadRadius: 4,
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Flexible(
            child: Text(
              label,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w600,
                letterSpacing: 0,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _IconControl extends StatelessWidget {
  const _IconControl({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
  });

  final IconData icon;
  final String tooltip;
  final FutureOr<void> Function()? onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 48,
      height: 48,
      child: IconButton(
        onPressed: onPressed,
        tooltip: tooltip,
        icon: Icon(icon),
        style: IconButton.styleFrom(
          foregroundColor: Colors.white,
          backgroundColor: const Color(0xCC0E1218),
          disabledBackgroundColor: const Color(0x660E1218),
          side: const BorderSide(color: Color(0x2EFFFFFF)),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
      ),
    );
  }
}

class _ActionControl extends StatelessWidget {
  const _ActionControl({
    required this.icon,
    required this.label,
    required this.onPressed,
    this.primary = false,
    this.danger = false,
  });

  final IconData icon;
  final String label;
  final FutureOr<void> Function()? onPressed;
  final bool primary;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    final foreground = primary ? const Color(0xFF07150F) : Colors.white;
    final background = primary
        ? const Color(0xFF38D39F)
        : const Color(0xEE0E1218);
    final border = danger
        ? const Color(0x8CFF626A)
        : primary
        ? Colors.transparent
        : const Color(0x2EFFFFFF);
    return SizedBox(
      height: 60,
      child: FilledButton.icon(
        onPressed: onPressed,
        icon: Icon(icon, size: 24),
        label: Text(label),
        style: FilledButton.styleFrom(
          minimumSize: const Size(138, 60),
          padding: const EdgeInsets.symmetric(horizontal: 20),
          foregroundColor: foreground,
          backgroundColor: background,
          disabledForegroundColor: Colors.white38,
          disabledBackgroundColor: const Color(0x770E1218),
          side: BorderSide(color: border),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w700,
            letterSpacing: 0,
          ),
        ),
      ),
    );
  }
}

class _TopShade extends StatelessWidget {
  const _TopShade();

  @override
  Widget build(BuildContext context) {
    return const Align(
      alignment: Alignment.topCenter,
      child: IgnorePointer(
        child: SizedBox(
          width: double.infinity,
          height: 130,
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: <Color>[Color(0xC9000000), Colors.transparent],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _BottomShade extends StatelessWidget {
  const _BottomShade();

  @override
  Widget build(BuildContext context) {
    return const Align(
      alignment: Alignment.bottomCenter,
      child: IgnorePointer(
        child: SizedBox(
          width: double.infinity,
          height: 190,
          child: DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.bottomCenter,
                end: Alignment.topCenter,
                colors: <Color>[Color(0xD9000000), Colors.transparent],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
