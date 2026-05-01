# Eval plugin authoring

Plugins provide project-specific checks while keeping the core evaluation
framework stable. A plugin is a Python file that exports either `get_plugin()`,
`plugin`, or module-level protocol members.

```python
from eval.check_helpers import contains_check
from eval.plugin_api import PLUGIN_API_VERSION

api_version = PLUGIN_API_VERSION


def validate():
    return None


def get_checks(story):
    return [
        contains_check(
            "readme_mentions_story",
            "README mentions the story title",
            "agent_output",
            story.title,
            difficulty="low",
        )
    ]
```

Required protocol:

- `api_version == eval.plugin_api.PLUGIN_API_VERSION`
- `validate() -> None` (raise an exception to fail loading loudly)
- `get_checks(story: EvalStory) -> Sequence[CheckDefinition]`
- Optional `story_id = "<story id>"` to bind the plugin to one story.

Checks returned by a plugin must have unique IDs and must not collide with
built-in checks. Use helper factories from `eval.check_helpers` for the built-in
mechanisms:

- `contains_check(id, label, subject, substring)`
- `matches_check(id, label, subject, pattern)`
- `command_check(id, label, subject, cmd)`

Keep plugin imports lightweight. Plugins should not start network calls, mutate
repository state, or require credentials at import time.

Plugins are intentionally constrained to the Python standard library plus the
eval helper modules needed to define checks (`eval.check_helpers`,
`eval.plugin_api`, and `eval.models`). This keeps authoring deterministic and
prevents plugin checks from depending on undeclared third-party packages.
