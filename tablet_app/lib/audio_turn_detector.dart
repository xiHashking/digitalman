import 'dart:math' as math;
import 'dart:typed_data';

/// Detects a completed speech turn from little-endian PCM16 mono audio.
class AudioTurnDetector {
  AudioTurnDetector({
    this.sampleRate = 16000,
    this.speechThreshold = 700,
    this.speechConfirmation = const Duration(milliseconds: 120),
    this.trailingSilence = const Duration(milliseconds: 700),
    this.minimumRecording = const Duration(milliseconds: 450),
    this.maximumRecording = const Duration(seconds: 30),
  });

  final int sampleRate;
  final double speechThreshold;
  final Duration speechConfirmation;
  final Duration trailingSilence;
  final Duration minimumRecording;
  final Duration maximumRecording;

  Duration _elapsed = Duration.zero;
  Duration _speechCandidate = Duration.zero;
  Duration _silence = Duration.zero;
  bool _speechDetected = false;

  bool get speechDetected => _speechDetected;

  /// Returns true once when the current utterance should be submitted.
  bool add(Uint8List pcmBytes) {
    final usableLength = pcmBytes.lengthInBytes - (pcmBytes.lengthInBytes % 2);
    if (usableLength == 0) return false;

    final sampleCount = usableLength ~/ 2;
    final chunkDuration = Duration(
      microseconds:
          (sampleCount * Duration.microsecondsPerSecond) ~/ sampleRate,
    );
    _elapsed += chunkDuration;

    final data = ByteData.sublistView(pcmBytes, 0, usableLength);
    var squareSum = 0.0;
    for (var offset = 0; offset < usableLength; offset += 2) {
      final sample = data.getInt16(offset, Endian.little).toDouble();
      squareSum += sample * sample;
    }
    final rms = math.sqrt(squareSum / sampleCount);

    if (rms >= speechThreshold) {
      _silence = Duration.zero;
      if (!_speechDetected) {
        _speechCandidate += chunkDuration;
        if (_speechCandidate >= speechConfirmation) {
          _speechDetected = true;
        }
      }
    } else if (_speechDetected) {
      _speechCandidate = Duration.zero;
      _silence += chunkDuration;
      if (_elapsed >= minimumRecording && _silence >= trailingSilence) {
        return true;
      }
    } else {
      _speechCandidate = Duration.zero;
    }

    return _elapsed >= maximumRecording && _speechDetected;
  }

  void reset() {
    _elapsed = Duration.zero;
    _speechCandidate = Duration.zero;
    _silence = Duration.zero;
    _speechDetected = false;
  }
}
