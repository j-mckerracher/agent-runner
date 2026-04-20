#!/usr/bin/env python3
"""Parse ADO test case XML (Microsoft.VSTS.TCM.Steps) into structured YAML."""

import argparse
import html
import re
import sys
import xml.etree.ElementTree as ET

import yaml


def strip_html(text: str) -> str:
    """Decode HTML entities and strip all HTML tags, then trim whitespace."""
    if not text:
        return ""
    decoded = html.unescape(text)
    stripped = re.sub(r"<[^>]+>", "", decoded)
    return stripped.strip()


def parse_steps(xml_content: str) -> list[dict]:
    """Parse the <steps> XML and return a list of step dicts."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        print(f"Error: failed to parse XML: {exc}", file=sys.stderr)
        sys.exit(1)

    steps = []
    for idx, step_elem in enumerate(root.findall("step"), start=1):
        params = step_elem.findall("parameterizedString")
        action_raw = params[0].text if len(params) > 0 and params[0].text else ""
        expected_raw = params[1].text if len(params) > 1 and params[1].text else ""

        steps.append(
            {
                "step_id": f"STEP-{idx}",
                "action": strip_html(action_raw),
                "expected": strip_html(expected_raw),
            }
        )

    return steps


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse ADO test case Steps XML into structured YAML."
    )
    parser.add_argument(
        "--work-item-id",
        type=int,
        default=None,
        help="ADO work item ID to include in output",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Test case title to include in output",
    )
    parser.add_argument(
        "input",
        metavar="xml_file_or_-",
        help="Path to XML file, or '-' to read from stdin",
    )

    args = parser.parse_args()

    try:
        if args.input == "-":
            xml_content = sys.stdin.read()
        else:
            with open(args.input, encoding="utf-8") as fh:
                xml_content = fh.read()
    except (OSError, IOError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    if not xml_content.strip():
        print("Error: empty input", file=sys.stderr)
        sys.exit(1)

    steps = parse_steps(xml_content)

    output = {
        "work_item_id": args.work_item_id,
        "title": args.title,
        "steps": steps,
        "steps_count": len(steps),
    }

    yaml.dump(
        output,
        sys.stdout,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


if __name__ == "__main__":
    main()
