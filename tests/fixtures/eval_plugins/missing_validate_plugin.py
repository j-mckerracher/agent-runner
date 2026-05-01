from eval.check_helpers import contains_check
from eval.plugin_api import PLUGIN_API_VERSION

api_version = PLUGIN_API_VERSION


def get_checks(story):
    return [contains_check("never_loaded", "Never loaded", "agent_output", story.title)]
