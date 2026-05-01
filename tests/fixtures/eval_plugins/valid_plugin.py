from eval.check_helpers import contains_check
from eval.plugin_api import PLUGIN_API_VERSION

api_version = PLUGIN_API_VERSION
story_id = "story-001"


def validate():
    return None


def get_checks(story):
    return [
        contains_check(
            "plugin_contains_title",
            "Plugin contains title",
            "agent_output",
            story.title,
        )
    ]
