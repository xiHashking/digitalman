import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('every bundled Live2D model reference exists', () {
    final models = Directory('assets/models')
        .listSync(recursive: true)
        .whereType<File>()
        .where((file) => file.path.endsWith('.model3.json'));
    expect(models.length, greaterThanOrEqualTo(2));
    for (final model in models) {
      final data = jsonDecode(model.readAsStringSync()) as Map<String, dynamic>;
      final refs = data['FileReferences'] as Map<String, dynamic>;
      void check(String name) {
        expect(
          File('${model.parent.path}/$name').existsSync(),
          isTrue,
          reason: '${model.path}: missing $name',
        );
      }

      for (final key in ['Moc', 'Physics', 'Pose', 'DisplayInfo', 'UserData']) {
        if (refs[key] != null) check(refs[key] as String);
      }
      for (final texture in refs['Textures'] as List<dynamic>) {
        check(texture as String);
      }
      for (final expression in (refs['Expressions'] ?? []) as List<dynamic>) {
        check(expression['File'] as String);
      }
      for (final motions in (refs['Motions'] as Map<String, dynamic>).values) {
        for (final motion in motions as List<dynamic>) {
          check(motion['File'] as String);
          if (motion['Sound'] != null) check(motion['Sound'] as String);
        }
      }
    }
  });
}
