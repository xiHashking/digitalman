import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:http/http.dart' as http;
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'audio_turn_detector.dart';

enum ConversationState {
  idle,
  connecting,
  listening,
  recognizing,
  thinking,
  speaking,
  paused,
  error,
}

class LiveTalkingController extends ChangeNotifier {
  LiveTalkingController({http.Client? httpClient})
    : _httpClient = httpClient ?? http.Client();

  static const _serverPreferenceKey = 'server_address';
  static const _requestTimeout = Duration(seconds: 20);
  static const _asrConfig = <String, Object>{
    'chunk_size': <int>[5, 10, 5],
    'wav_name': 'flutter',
    'is_speaking': true,
    'chunk_interval': 10,
    'itn': false,
    'mode': '2pass',
  };

  final http.Client _httpClient;
  final AudioRecorder _recorder = AudioRecorder();
  final AudioTurnDetector _turnDetector = AudioTurnDetector();
  final RTCVideoRenderer videoRenderer = RTCVideoRenderer();

  RTCPeerConnection? _peerConnection;
  WebSocket? _asrSocket;
  StreamSubscription<Uint8List>? _audioSubscription;
  StreamSubscription<dynamic>? _asrSubscription;
  bool _recorderRunning = false;
  bool _rendererReady = false;
  bool _disposed = false;
  bool _endingTurn = false;
  bool _micPaused = false;
  bool _started = false;
  Future<void>? _rendererInitialization;
  int _connectionEpoch = 0;
  int _turnEpoch = 0;

  ConversationState _state = ConversationState.idle;
  String _statusText = '等待启动';
  String _errorMessage = '';
  String _serverAddress = '';
  String? _sessionId;

  ConversationState get state => _state;
  String get statusText => _statusText;
  String get errorMessage => _errorMessage;
  String get serverAddress => _serverAddress;
  String? get sessionId => _sessionId;
  bool get rendererReady => _rendererReady;
  bool get started => _started;
  bool get micPaused => _micPaused;
  bool get isConnected => _sessionId != null;
  bool get canControl => isConnected && state != ConversationState.connecting;
  bool get canInterrupt => isConnected && state != ConversationState.connecting;

  Future<void> initialize() async {
    // Initializing the WebRTC renderer can take several seconds on the
    // tablet. It is only needed when the server video mode is used, so start
    // it in parallel and let the UI become interactive immediately. Haru can
    // then be selected while the renderer finishes warming up.
    _rendererInitialization ??= videoRenderer.initialize().then((_) {
      _rendererReady = true;
      _notify();
    });
    final preferences = await SharedPreferences.getInstance();
    _serverAddress = preferences.getString(_serverPreferenceKey) ?? '';
    _notify();
  }

  Future<void> saveServerAddress(String value) async {
    final normalized = normalizeServerAddress(value);
    _baseUriFor(normalized);
    _serverAddress = normalized;
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString(_serverPreferenceKey, normalized);
    _notify();
  }

  static String normalizeServerAddress(String value) {
    var result = value.trim();
    while (result.endsWith('/')) {
      result = result.substring(0, result.length - 1);
    }
    if (result.isEmpty) throw const FormatException('服务器地址不能为空');
    return result;
  }

  Future<void> connect() async {
    if (_serverAddress.trim().isEmpty) {
      _started = true;
      _fail('请先输入 LiveTalking 服务器地址');
      return;
    }
    final connectionEpoch = ++_connectionEpoch;
    ++_turnEpoch;
    _started = true;
    _micPaused = false;
    _sessionId = null;
    _errorMessage = '';
    _setState(ConversationState.connecting, '连接数字人中');

    // A real-video session needs the renderer to be ready before attaching
    // the incoming WebRTC track. Haru mode does not call connect(), so this
    // wait no longer blocks avatar selection during app startup.
    await _rendererInitialization;

    await _stopConversationAudio(closeSocket: true);
    final oldConnection = _peerConnection;
    _peerConnection = null;
    videoRenderer.srcObject = null;
    await oldConnection?.close();

    try {
      final pc = await createPeerConnection(<String, dynamic>{
        'sdpSemantics': 'unified-plan',
      });
      if (!_isCurrentConnection(connectionEpoch)) {
        await pc.close();
        return;
      }
      _peerConnection = pc;
      pc.onTrack = (event) {
        if (!_isCurrentConnection(connectionEpoch) || event.streams.isEmpty) {
          return;
        }
        if (event.track.kind == 'video' || videoRenderer.srcObject == null) {
          videoRenderer.srcObject = event.streams.first;
          _notify();
        }
      };
      pc.onConnectionState = (connectionState) {
        if (!_isCurrentConnection(connectionEpoch)) return;
        if (connectionState ==
            RTCPeerConnectionState.RTCPeerConnectionStateConnected) {
          unawaited(Helper.setSpeakerphoneOn(true));
        } else if (connectionState ==
                RTCPeerConnectionState.RTCPeerConnectionStateFailed ||
            connectionState ==
                RTCPeerConnectionState.RTCPeerConnectionStateDisconnected) {
          _fail('数字人连接已断开，请重新连接');
        }
      };

      await pc.addTransceiver(
        kind: RTCRtpMediaType.RTCRtpMediaTypeVideo,
        init: RTCRtpTransceiverInit(direction: TransceiverDirection.RecvOnly),
      );
      await pc.addTransceiver(
        kind: RTCRtpMediaType.RTCRtpMediaTypeAudio,
        init: RTCRtpTransceiverInit(direction: TransceiverDirection.RecvOnly),
      );

      final offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await _waitForIceGathering(pc);
      if (!_isCurrentConnection(connectionEpoch)) return;
      final localDescription = await pc.getLocalDescription();
      if (localDescription?.sdp == null || localDescription?.type == null) {
        throw StateError('无法生成 WebRTC 连接信息');
      }

      final response = await _httpClient
          .post(
            _httpUri('/offer'),
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode(<String, Object?>{
              'sdp': localDescription!.sdp,
              'type': localDescription.type,
            }),
          )
          .timeout(_requestTimeout);
      final answer = _decodeResponse(response, operation: '连接数字人');
      if (answer['sdp'] is! String || answer['sessionid'] == null) {
        throw const FormatException('服务器没有返回有效的 WebRTC 会话');
      }
      if (!_isCurrentConnection(connectionEpoch)) return;

      _sessionId = answer['sessionid'].toString();
      await pc.setRemoteDescription(
        RTCSessionDescription(answer['sdp'] as String, 'answer'),
      );
      if (!_isCurrentConnection(connectionEpoch)) return;
      await _startListening(connectionEpoch, ++_turnEpoch);
    } on TimeoutException {
      if (_isCurrentConnection(connectionEpoch)) {
        _fail('连接超时，请确认服务器地址和网络');
      }
    } catch (error) {
      if (_isCurrentConnection(connectionEpoch)) {
        _fail(_friendlyError(error));
      }
    }
  }

  Future<void> toggleMicrophone() async {
    if (!isConnected) return;
    if (_micPaused) {
      _micPaused = false;
      _notify();
      await _startListening(_connectionEpoch, ++_turnEpoch);
      return;
    }

    _micPaused = true;
    ++_turnEpoch;
    await _stopConversationAudio(closeSocket: true);
    _setState(ConversationState.paused, '麦克风已暂停');
  }

  Future<void> interrupt() async {
    final sessionId = _sessionId;
    if (sessionId == null) return;
    final connectionEpoch = _connectionEpoch;
    final turnEpoch = ++_turnEpoch;
    await _stopConversationAudio(closeSocket: true);

    try {
      await _postJson('/interrupt_talk', <String, Object>{
        'sessionid': sessionId,
      });
      if (!_isCurrentTurn(connectionEpoch, turnEpoch)) return;
      if (_micPaused) {
        _setState(ConversationState.paused, '麦克风已暂停');
      } else {
        await _startListening(connectionEpoch, turnEpoch);
      }
    } catch (error) {
      if (_isCurrentTurn(connectionEpoch, turnEpoch)) {
        _fail('打断失败：${_friendlyError(error)}');
      }
    }
  }

  Future<void> _startListening(int connectionEpoch, int turnEpoch) async {
    if (!_isCurrentTurn(connectionEpoch, turnEpoch) || _micPaused) return;
    await _stopConversationAudio(closeSocket: true);
    if (!_isCurrentTurn(connectionEpoch, turnEpoch) || _micPaused) return;

    try {
      _setState(ConversationState.connecting, '正在准备麦克风');
      if (!await _recorder.hasPermission()) {
        throw StateError('没有麦克风权限，请在系统设置中允许录音权限');
      }

      final socket = await WebSocket.connect(
        _webSocketUri('/api/asr').toString(),
      ).timeout(_requestTimeout);
      if (!_isCurrentTurn(connectionEpoch, turnEpoch) || _micPaused) {
        await socket.close();
        return;
      }
      _asrSocket = socket;
      _asrSubscription = socket.listen(
        (message) =>
            _handleAsrMessage(message, socket, connectionEpoch, turnEpoch),
        onError: (Object error, StackTrace stackTrace) {
          if (_asrSocket == socket &&
              _isCurrentTurn(connectionEpoch, turnEpoch)) {
            _fail('语音识别连接异常：${_friendlyError(error)}');
          }
        },
        onDone: () {
          if (_asrSocket == socket &&
              _isCurrentTurn(connectionEpoch, turnEpoch) &&
              state == ConversationState.listening) {
            _fail('语音识别连接已断开');
          }
        },
        cancelOnError: true,
      );
      socket.add(jsonEncode(_asrConfig));

      _turnDetector.reset();
      _endingTurn = false;
      final audioStream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
          autoGain: true,
          echoCancel: true,
          noiseSuppress: true,
          streamBufferSize: 1920,
        ),
      );
      _recorderRunning = true;
      if (!_isCurrentTurn(connectionEpoch, turnEpoch) || _micPaused) {
        await _stopConversationAudio(closeSocket: true);
        return;
      }

      _audioSubscription = audioStream.listen(
        (chunk) {
          if (!_isCurrentTurn(connectionEpoch, turnEpoch) ||
              _endingTurn ||
              _asrSocket != socket) {
            return;
          }
          socket.add(chunk);
          if (_turnDetector.add(chunk)) {
            _endingTurn = true;
            unawaited(_finishTurn(socket, connectionEpoch, turnEpoch));
          }
        },
        onError: (Object error, StackTrace stackTrace) {
          if (_isCurrentTurn(connectionEpoch, turnEpoch)) {
            _fail('麦克风录音异常：${_friendlyError(error)}');
          }
        },
        cancelOnError: true,
      );
      _setState(ConversationState.listening, '请讲话');
    } on TimeoutException {
      if (_isCurrentTurn(connectionEpoch, turnEpoch)) {
        _fail('连接语音识别服务超时');
      }
    } catch (error) {
      if (_isCurrentTurn(connectionEpoch, turnEpoch)) {
        _fail(_friendlyError(error));
      }
    }
  }

  Future<void> _finishTurn(
    WebSocket socket,
    int connectionEpoch,
    int turnEpoch,
  ) async {
    await _stopRecorder();
    if (!_isCurrentTurn(connectionEpoch, turnEpoch) || _asrSocket != socket) {
      return;
    }
    socket.add(
      jsonEncode(<String, Object>{..._asrConfig, 'is_speaking': false}),
    );
    _setState(ConversationState.recognizing, '正在识别');
  }

  void _handleAsrMessage(
    dynamic message,
    WebSocket socket,
    int connectionEpoch,
    int turnEpoch,
  ) {
    if (!_isCurrentTurn(connectionEpoch, turnEpoch) || _asrSocket != socket) {
      return;
    }
    if (message is! String) return;

    try {
      final payload = jsonDecode(message);
      if (payload is! Map<String, dynamic>) return;
      final text = (payload['text'] ?? '').toString().trim();
      final isFinal =
          payload['is_final'] == true ||
          payload['mode'] == '2pass-offline' ||
          payload['mode'] == 'offline';
      if (!isFinal) return;
      if (text.isEmpty) {
        unawaited(_startListening(connectionEpoch, turnEpoch));
        return;
      }
      _setState(ConversationState.thinking, '数字人思考中');
      unawaited(_submitTextAndWait(text, connectionEpoch, turnEpoch));
    } catch (error) {
      _fail('无法解析语音识别结果：${_friendlyError(error)}');
    }
  }

  Future<void> _submitTextAndWait(
    String text,
    int connectionEpoch,
    int turnEpoch,
  ) async {
    final sessionId = _sessionId;
    if (sessionId == null) return;
    try {
      await _postJson('/human', <String, Object>{
        'text': text,
        'type': 'chat',
        'sessionid': sessionId,
      });
      if (!_isCurrentTurn(connectionEpoch, turnEpoch)) return;

      var becameBusy = false;
      final busyDeadline = DateTime.now().add(const Duration(seconds: 30));
      while (_isCurrentTurn(connectionEpoch, turnEpoch) &&
          DateTime.now().isBefore(busyDeadline)) {
        if (await _isAvatarBusy(sessionId)) {
          becameBusy = true;
          _setState(ConversationState.speaking, '数字人回答中');
          break;
        }
        await Future<void>.delayed(const Duration(milliseconds: 300));
      }
      if (!_isCurrentTurn(connectionEpoch, turnEpoch)) return;
      if (!becameBusy) {
        throw StateError('服务器未开始生成回答');
      }

      final playbackDeadline = DateTime.now().add(const Duration(minutes: 2));
      while (_isCurrentTurn(connectionEpoch, turnEpoch) &&
          DateTime.now().isBefore(playbackDeadline)) {
        if (!await _isAvatarBusy(sessionId)) {
          await Future<void>.delayed(const Duration(milliseconds: 250));
          if (_isCurrentTurn(connectionEpoch, turnEpoch) && !_micPaused) {
            await _startListening(connectionEpoch, turnEpoch);
          }
          return;
        }
        await Future<void>.delayed(const Duration(milliseconds: 500));
      }
      if (_isCurrentTurn(connectionEpoch, turnEpoch)) {
        throw TimeoutException('等待数字人回答结束超时');
      }
    } catch (error) {
      if (_isCurrentTurn(connectionEpoch, turnEpoch)) {
        _fail(_friendlyError(error));
      }
    }
  }

  Future<bool> _isAvatarBusy(String sessionId) async {
    final result = await _postJson('/is_speaking', <String, Object>{
      'sessionid': sessionId,
    });
    return result['data'] == true;
  }

  Future<Map<String, dynamic>> _postJson(
    String path,
    Map<String, Object> body,
  ) async {
    final response = await _httpClient
        .post(
          _httpUri(path),
          headers: const {'Content-Type': 'application/json'},
          body: jsonEncode(body),
        )
        .timeout(_requestTimeout);
    return _decodeResponse(response, operation: path);
  }

  Map<String, dynamic> _decodeResponse(
    http.Response response, {
    required String operation,
  }) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw HttpException('$operation 失败（HTTP ${response.statusCode}）');
    }
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('服务器返回了无效数据');
    }
    if (decoded['code'] != null && decoded['code'] != 0) {
      throw StateError((decoded['msg'] ?? '$operation 失败').toString());
    }
    return decoded;
  }

  Future<void> _waitForIceGathering(RTCPeerConnection pc) async {
    if (pc.iceGatheringState ==
        RTCIceGatheringState.RTCIceGatheringStateComplete) {
      return;
    }
    final completer = Completer<void>();
    pc.onIceGatheringState = (iceState) {
      if (iceState == RTCIceGatheringState.RTCIceGatheringStateComplete &&
          !completer.isCompleted) {
        completer.complete();
      }
    };
    await completer.future.timeout(
      const Duration(seconds: 8),
      onTimeout: () {},
    );
    pc.onIceGatheringState = null;
  }

  Future<void> _stopRecorder() async {
    final subscription = _audioSubscription;
    _audioSubscription = null;
    await subscription?.cancel();
    if (_recorderRunning) {
      _recorderRunning = false;
      try {
        await _recorder.stop();
      } catch (_) {
        // The native recorder may already have stopped after a stream error.
      }
    }
  }

  Future<void> _stopConversationAudio({required bool closeSocket}) async {
    _endingTurn = false;
    _turnDetector.reset();
    await _stopRecorder();
    if (!closeSocket) return;

    final subscription = _asrSubscription;
    final socket = _asrSocket;
    _asrSubscription = null;
    _asrSocket = null;
    await subscription?.cancel();
    try {
      await socket?.close();
    } catch (_) {
      // A disconnected socket needs no further cleanup.
    }
  }

  Uri _httpUri(String path) {
    final base = _baseUriFor(_serverAddress);
    return base.replace(path: path, query: null, fragment: null);
  }

  Uri _webSocketUri(String path) {
    final base = _baseUriFor(_serverAddress);
    return base.replace(
      scheme: base.scheme == 'https' ? 'wss' : 'ws',
      path: path,
      query: null,
      fragment: null,
    );
  }

  static Uri _baseUriFor(String address) {
    final withScheme = address.contains('://') ? address : 'http://$address';
    final uri = Uri.parse(withScheme);
    if ((uri.scheme != 'http' && uri.scheme != 'https') || uri.host.isEmpty) {
      throw const FormatException('服务器地址格式不正确');
    }
    return uri;
  }

  bool _isCurrentConnection(int connectionEpoch) {
    return !_disposed && connectionEpoch == _connectionEpoch;
  }

  bool _isCurrentTurn(int connectionEpoch, int turnEpoch) {
    return _isCurrentConnection(connectionEpoch) && turnEpoch == _turnEpoch;
  }

  void _setState(ConversationState value, String label) {
    if (_disposed) return;
    _state = value;
    _statusText = label;
    _notify();
  }

  void _fail(String message) {
    _errorMessage = message;
    unawaited(_stopConversationAudio(closeSocket: true));
    _setState(ConversationState.error, '连接异常');
  }

  String _friendlyError(Object error) {
    if (error is SocketException) {
      return '无法连接服务器，请检查地址、Wi-Fi 和服务状态';
    }
    if (error is FormatException) return error.message;
    if (error is StateError) return error.message;
    return error.toString().replaceFirst('Exception: ', '');
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_connectionEpoch;
    ++_turnEpoch;
    unawaited(_stopConversationAudio(closeSocket: true));
    unawaited(_peerConnection?.close());
    _peerConnection = null;
    unawaited(videoRenderer.dispose());
    unawaited(_recorder.dispose());
    _httpClient.close();
    super.dispose();
  }
}
