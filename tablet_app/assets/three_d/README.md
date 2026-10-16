# Local VRM prototype

Default model: `education-teacher-v1.vrm`, created locally in VRoid Studio 2.14.0
using built-in presets. Editable source: `avatar_sources/vroid/education-teacher-v1.vroid`.
VRM 1.0; includes humanoid bones, blink and all five vowel expressions.
This is an appearance draft for review, not a finalized character design.
Use the page's 3D character selector to switch back to the original model.

`AvatarSample_B_teacher-experiment.vrm` is a byte-for-byte copy of the
original model. The preview's “教师配色实验” option only tints its existing
top, bottom and shoe materials at runtime; it does not replace the exposed
clothing geometry. This is a safe boundary test before a real Blender/Unity
clothing edit.

Original model: AvatarSample_B 1.1, VRoid Project / pixiv Inc.
Source: https://github.com/pixiv/ChatVRM/blob/main/public/AvatarSample_B.vrm
Embedded license permits corporation use, redistribution and modifications.
Additional conditions: https://vroid.pixiv.help/hc/ja/articles/4402394424089-AvatarSample-A-B-C
Includes blink and aa/ih/ou/ee/oh expressions. The older test model is retained
for reference and is no longer loaded by the prototype.

Dependencies: Three.js 0.180.0 and @pixiv/three-vrm 3.x, MIT; license files included.
`prototype-source.js` is bundled into `prototype.js` using esbuild with target chrome99.
The app serves assets over a loopback-only HTTP server. No external network is required.

Model: pixiv three-vrm official VRM1_Constraint_Twist_Sample, v1.0.1.
Source: https://github.com/pixiv/three-vrm/tree/dev/packages/three-vrm/examples/models
Copyright (c) 2022 pixiv Inc. License: https://vrm.dev/licenses/1.0/
Embedded metadata permits everyone, corporation commercial usage, redistribution,
and modification/redistribution. The model license's usage restrictions still apply.

Test audio: synthesized locally with macOS Tingting for this internal prototype.
Lip movement uses measured RMS audio volume, not phoneme recognition.
Real-device appearance/performance must be verified before claiming compatibility.

Motion prototype: normalized humanoid bones drive a greeting wave, nod and
speaking gesture with eased transitions. Idle breathing/weight shift and blink
continue between gestures. Drag rotates the camera; pinch/wheel zooms. Speech
automatically triggers repeating explanation gestures. No motion capture or
external animation service is involved.

Rebuild: install `three@0.180.0`, `@pixiv/three-vrm@3.5.5`, and `esbuild` in
a build directory, then bundle `prototype-source.js` to `prototype.js` with
`--bundle --minify --target=chrome99` (NODE_PATH may point to that node_modules).
