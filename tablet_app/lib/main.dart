import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_live2d/flutter_live2d.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import 'livetalking_controller.dart';

enum _AvatarMode {
  real('真人商务形象', '使用服务器返回的视频数字人'),
  haru(
    'Haru 接待员',
    '商务接待 · 本地 Live2D',
    modelDir: 'haru_greeter',
    modelFile: 'haru_greeter_t05.model3.json',
    idleGroup: '',
  ),
  ren(
    'Ren Foster',
    '科技咨询 · 本地 Live2D',
    modelDir: 'ren',
    modelFile: 'Ren.model3.json',
  ),
  hiyori(
    'Hiyori Momose',
    '校园交流 · 本地 Live2D',
    modelDir: 'hiyori',
    modelFile: 'Hiyori.model3.json',
  );

  const _AvatarMode(
    this.title,
    this.subtitle, {
    this.modelDir,
    this.modelFile,
    this.idleGroup = 'Idle',
  });
  final String title;
  final String subtitle;
  final String? modelDir;
  final String? modelFile;
  final String idleGroup;
  bool get isLive2D => modelDir != null;
}

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
  late final Live2DViewController _haruController;
  bool _initializing = true;
  bool _chromeVisible = false;
  _AvatarMode _avatarMode = _AvatarMode.real;
  bool _haruModelReady = false;
  bool _haruLoading = false;
  bool _mouthUpdateInFlight = false;
  Timer? _haruMouthTimer;
  int _avatarGeneration = 0;
  Future<void> _loadQueue = Future<void>.value();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _controller = LiveTalkingController();
    _haruController = Live2DViewController();
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
    _haruMouthTimer?.cancel();
    _haruController.dispose();
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

  void _selectAvatarMode(_AvatarMode mode) {
    if (mode == _avatarMode) return;
    final generation = ++_avatarGeneration;
    _haruMouthTimer?.cancel();
    _haruMouthTimer = null;
    setState(() {
      _avatarMode = mode;
      _haruModelReady = false;
      _haruLoading = mode.isLive2D;
    });
    debugPrint('[Avatar] selected ${mode.name} generation=$generation');
    if (mode.isLive2D) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _loadQueue = _loadQueue.then((_) => _loadAvatarModel(mode, generation));
      });
    }
  }

  Future<void> _loadAvatarModel(_AvatarMode mode, int generation) async {
    bool current() => mounted && generation == _avatarGeneration;
    if (!current()) return;
    try {
      await _haruController.whenAttached.timeout(const Duration(seconds: 8));
      // The Android TextureView reports `attached` before its EGL surface has
      // received the first size callback. The native plugin rejects a load
      // while that size is still zero, so retry after the render thread has
      // had a chance to initialize the surface.
      var loaded = false;
      for (var attempt = 1; attempt <= 8 && !loaded; attempt++) {
        await Future<void>.delayed(const Duration(milliseconds: 180));
        if (!current()) return;
        loaded = await _haruController.loadModel(
          modelDir: 'assets/models/${mode.modelDir}/',
          modelFileName: mode.modelFile!,
        );
        debugPrint(
          '[Avatar] ${mode.name} load attempt=$attempt result=$loaded',
        );
      }
      if (!current()) return;
      if (!loaded) {
        throw StateError('${mode.title} 模型加载失败');
      }
      await _haruController.startMotion(
        group: mode.idleGroup,
        index: 0,
        priority: 1,
      );
      if (!current()) return;
      setState(() => _haruModelReady = true);
      _startHaruMouthAnimation();
    } on Live2DException catch (error) {
      if (current()) _showAvatarError('${error.code}: ${error.message}');
    } catch (error) {
      debugPrint('[Avatar] ${mode.name} load failed: $error');
      if (current()) _showAvatarError(error.toString());
    } finally {
      if (current()) setState(() => _haruLoading = false);
    }
  }

  void _startHaruMouthAnimation() {
    _haruMouthTimer?.cancel();
    var phase = 0.0;
    _haruMouthTimer = Timer.periodic(const Duration(milliseconds: 80), (_) {
      if (!_haruModelReady || !_haruController.value.isLoaded) return;
      final speaking = _controller.state == ConversationState.speaking;
      final value = speaking ? 0.08 + ((math.sin(phase) + 1) * 0.06) : 0.0;
      phase += 0.85;
      if (_mouthUpdateInFlight) return;
      _mouthUpdateInFlight = true;
      _haruController
          .setParameter('ParamMouthOpenY', value)
          .catchError((Object error) {
            debugPrint('[Avatar] mouth update: $error');
          })
          .whenComplete(() => _mouthUpdateInFlight = false);
    });
  }

  void _showAvatarError(String message) {
    if (!mounted) return;
    final title = _avatarMode.title;
    _selectAvatarMode(_AvatarMode.real);
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('$title 暂时不可用：$message')));
  }

  Future<void> _showAvatarPicker() async {
    final selected = await showDialog<_AvatarMode>(
      context: context,
      builder: (dialogContext) {
        return AlertDialog(
          backgroundColor: const Color(0xFF11161D),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          title: const Text('选择数字人形象'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                for (final mode in _AvatarMode.values)
                  _avatarChoice(
                    dialogContext,
                    mode: mode,
                    icon: mode.isLive2D
                        ? Icons.face_retouching_natural_rounded
                        : Icons.person_outline_rounded,
                    title: mode.title,
                    subtitle: mode.subtitle,
                  ),
                const Padding(
                  padding: EdgeInsets.only(top: 12),
                  child: Text(
                    'This content uses sample data owned and copyrighted by Live2D Inc. The sample data are utilized in accordance with terms and conditions set by Live2D Inc. This content itself is created at the author’s sole discretion.',
                    style: TextStyle(fontSize: 10, color: Color(0xFF7F8998)),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
    if (selected != null && mounted) {
      _selectAvatarMode(selected);
    }
  }

  Widget _avatarChoice(
    BuildContext dialogContext, {
    required _AvatarMode mode,
    required IconData icon,
    required String title,
    required String subtitle,
  }) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon),
      title: Text(title),
      subtitle: Text(subtitle),
      trailing: mode == _avatarMode
          ? const Icon(Icons.check_circle, color: Color(0xFF38D39F))
          : null,
      onTap: () => Navigator.of(dialogContext).pop(mode),
    );
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
              if (_avatarMode.isLive2D) ...[
                Live2DView(controller: _haruController),
                if (_haruLoading || !_haruModelReady)
                  Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: <Widget>[
                        const CircularProgressIndicator(strokeWidth: 2),
                        const SizedBox(height: 14),
                        Text(
                          _haruLoading ? '正在加载 Live2D 形象…' : '准备 Live2D 形象…',
                          style: const TextStyle(
                            color: Color(0xFFB8C0CC),
                            fontSize: 15,
                          ),
                        ),
                        if (_haruLoading) ...[
                          const SizedBox(height: 5),
                          const Text(
                            '首次加载需要初始化本地渲染资源，请稍候',
                            style: TextStyle(
                              color: Color(0xFF7F8998),
                              fontSize: 12,
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
              ] else if (_controller.rendererReady)
                RTCVideoView(
                  _controller.videoRenderer,
                  objectFit: RTCVideoViewObjectFit.RTCVideoViewObjectFitContain,
                ),
              const _TopShade(),
              const _BottomShade(),
              // Keep the start/error panel below the top controls. If this
              // is the last Stack child it intercepts the avatar selector,
              // making Haru impossible to select before connecting.
              if (!_avatarMode.isLive2D &&
                  (!_controller.started ||
                      _controller.state == ConversationState.error))
                _buildStartLayer(),
              _buildTopBar(),
              _buildBottomControls(),
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
              icon: _avatarMode.isLive2D
                  ? Icons.face_retouching_natural_rounded
                  : Icons.person_outline_rounded,
              tooltip: '选择数字人形象',
              onPressed: _showAvatarPicker,
            ),
            const SizedBox(width: 4),
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
    final isHaru = _avatarMode.isLive2D;
    return ColoredBox(
      // Keep the Live2D character visible while the server is being started.
      // The real-video mode keeps the stronger scrim for readability, while
      // Haru only needs a light scrim behind the start controls.
      color: isHaru ? const Color(0x5505070A) : const Color(0xDD05070A),
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
