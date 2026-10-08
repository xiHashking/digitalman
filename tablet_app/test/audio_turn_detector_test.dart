import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:livetalking_tablet/audio_turn_detector.dart';

Uint8List pcmChunk({required int milliseconds, required int amplitude}) {
  const sampleRate = 16000;
  final sampleCount = sampleRate * milliseconds ~/ 1000;
  final bytes = ByteData(sampleCount * 2);
  for (var index = 0; index < sampleCount; index++) {
    bytes.setInt16(
      index * 2,
      index.isEven ? amplitude : -amplitude,
      Endian.little,
    );
  }
  return bytes.buffer.asUint8List();
}

void main() {
  test('ignores a short sound spike', () {
    final detector = AudioTurnDetector();

    expect(detector.add(pcmChunk(milliseconds: 60, amplitude: 2400)), isFalse);
    for (var index = 0; index < 15; index++) {
      expect(detector.add(pcmChunk(milliseconds: 60, amplitude: 0)), isFalse);
    }

    expect(detector.speechDetected, isFalse);
  });

  test('ends a confirmed utterance after trailing silence', () {
    final detector = AudioTurnDetector();

    expect(detector.add(pcmChunk(milliseconds: 60, amplitude: 2400)), isFalse);
    expect(detector.add(pcmChunk(milliseconds: 60, amplitude: 2400)), isFalse);
    expect(detector.speechDetected, isTrue);

    var ended = false;
    for (var index = 0; index < 12; index++) {
      ended = detector.add(pcmChunk(milliseconds: 60, amplitude: 0));
    }
    expect(ended, isTrue);
  });

  test('reset clears the current turn', () {
    final detector = AudioTurnDetector();
    detector.add(pcmChunk(milliseconds: 120, amplitude: 2400));
    expect(detector.speechDetected, isTrue);

    detector.reset();

    expect(detector.speechDetected, isFalse);
    expect(detector.add(pcmChunk(milliseconds: 720, amplitude: 0)), isFalse);
  });
}
