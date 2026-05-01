# Eval plugin API changelog

## 1.0

- Initial plugin protocol.
- `PLUGIN_API_VERSION = "1.0"`.
- Plugins expose `validate()` and `get_checks(story)`.
- Check definitions use the shared dataclasses in `eval.models`.
- Structured metric details should be carried through score/check metadata; Opik
  reporting uses `ScoreResult.metadata` in later phases.
