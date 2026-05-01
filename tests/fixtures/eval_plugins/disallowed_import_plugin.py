import requests

from eval.check_helpers import contains_check
from eval.plugin_api import PLUGIN_API_VERSION

api_version = PLUGIN_API_VERSION


def validate():
    return None


def get_checks(story):
    return [contains_check("disallowed", "Disallowed", "agent_output", story.title)]
