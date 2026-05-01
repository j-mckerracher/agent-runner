from eval.check_helpers import contains_check
from eval.plugin_api import PLUGIN_API_VERSION

api_version = PLUGIN_API_VERSION
validate_calls = 0


def validate():
    global validate_calls
    validate_calls += 1


def get_checks(story):
    return [contains_check("counting", "Counting", "agent_output", story.title)]
