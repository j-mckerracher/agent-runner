from eval.check_helpers import contains_check

api_version = "0.9"


def validate():
    return None


def get_checks(story):
    return [contains_check("never_loaded", "Never loaded", "agent_output", story.title)]
