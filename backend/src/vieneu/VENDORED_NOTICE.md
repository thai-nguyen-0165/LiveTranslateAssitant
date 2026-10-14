# VieNeu-TTS vendored runtime

This directory contains source derived from VieNeu-TTS 3.2.3 by Phạm Nguyễn
Ngọc Bảo, licensed under Apache License 2.0. The full license is distributed at
`THIRD_PARTY_LICENSES/VieNeu-TTS-LICENSE.txt`.

DubFlow modifications:

- package imports were moved under the `src` namespace;
- the v3 Turbo ONNX constructor accepts custom graph, codec and voices paths;
- the runtime is connected to DubFlow's provider, settings and desktop
  packaging flow.

