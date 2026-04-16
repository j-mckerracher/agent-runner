# Rubric Evaluation Prompt

## Task Context

**Task description:**
{{task_description}}

**Criterion being evaluated:**
{{criterion_description}}

**Score scale for this criterion:** {{scale}}

**Pass threshold:** {{threshold}}

## Artifacts Produced

{{artifact_summary}}

## Event Log Excerpt

{{event_log_excerpt}}

## Calibration Anchors

Use these concrete examples to anchor your scoring:

- **Score 0 (lowest):** The artifact is absent, completely incorrect, or directly contradicts the criterion. No meaningful attempt is evident.
- **Score at midpoint of scale:** The artifact partially satisfies the criterion. Key elements are present but incomplete, inaccurate in places, or missing important sub-requirements.
- **Score at top of scale:** The artifact fully satisfies the criterion. All required elements are present, correct, and well-formed. No significant gaps.

## Output Instructions

You MUST respond with a single JSON object and nothing else. No prose before or after. No markdown fences. No explanations outside the JSON.

Required schema:
```
{"score": <integer within scale>, "rationale": <string, 1-3 sentences>, "evidence_refs": [<string>, ...]}
```

- `score`: An integer within the stated scale (e.g. if scale is "0-3", score must be 0, 1, 2, or 3).
- `rationale`: A 1-3 sentence explanation citing specific evidence from the artifacts or event log.
- `evidence_refs`: A list of zero or more short strings identifying the artifact paths or event log lines that support your score (e.g. `["output/report.md:L12", "event#42"]`). May be empty list.

**You MUST NOT:**
- Execute code, call tools, or perform any computation outside of reading the provided artifacts.
- Ask clarifying questions.
- Output anything other than the JSON object described above.
- Invent evidence not present in the artifact summary or event log excerpt.

Example valid response:
{"score": 2, "rationale": "The report.md file is present and contains the required summary section, but is missing the cost breakdown table.", "evidence_refs": ["output/report.md"]}
