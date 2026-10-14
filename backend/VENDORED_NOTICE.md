# thainp-tts vendored runtime notice

The thainp-tts product contains source derived from the upstream VieNeu-TTS
3.2.3 runtime by Phạm Nguyễn Ngọc Bảo, licensed under Apache License 2.0. The
full license is distributed at `VieNeu-TTS-LICENSE.txt`.

thainp-tts modifications:

- package imports were moved under the `src` namespace;
- the v3 Turbo ONNX constructor accepts custom graph, codec and voices paths;
- the runtime is used as the local Vietnamese speech output for LiveTrans;
  Gemini Live provides the recognition and Vietnamese translation pipeline.
