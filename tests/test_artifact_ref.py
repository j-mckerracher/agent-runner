"""Deterministic behavioral tests for the `ArtifactRef` contract (Prompt 18).

Bare-function pytest style, matching `tests/test_agent_contract.py` and
`tests/test_trace_events.py`. Covers prompt cases 1-20: identity, tri-state
consumers, schema pairing, validation-status round-trips, checksum rules,
outer-type rejection, serialization/round-trip, forward-compat, `from_dict`
safety, unsupported contract version, aggregated errors, and defensive copying.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artifacts import (
    ARTIFACT_REF_SCHEMA_VERSION,
    SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS,
    ArtifactMetadataSerializationError,
    ArtifactRef,
    ArtifactRefValidationError,
    ArtifactValidationStatus,
)


# --------------------------------------------------------------------------
# 1. Public import path + version constants
# --------------------------------------------------------------------------


def test_public_import_path_and_version_constants():
    assert ARTIFACT_REF_SCHEMA_VERSION == "1"
    assert SUPPORTED_ARTIFACT_REF_SCHEMA_VERSIONS == frozenset({"1"})


# --------------------------------------------------------------------------
# 2. Addressing: path-only / uri-only / both / neither
# --------------------------------------------------------------------------


def test_path_only_valid():
    ref = ArtifactRef(artifact_type="plan", path="planning/plan.json")
    assert ref.path == Path("planning/plan.json")
    assert ref.uri is None


def test_uri_only_valid():
    ref = ArtifactRef(artifact_type="plan", uri="s3://bucket/plan.json")
    assert ref.uri == "s3://bucket/plan.json"
    assert ref.path is None


def test_path_and_uri_both_allowed_no_equivalence_check():
    ref = ArtifactRef(
        artifact_type="plan", path="p/plan.json", uri="s3://bucket/other.json"
    )
    assert ref.path == Path("p/plan.json")
    assert ref.uri == "s3://bucket/other.json"


def test_missing_both_path_and_uri_rejected():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan")


# --------------------------------------------------------------------------
# 3. Empty/whitespace rejection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"artifact_type": "", "path": "p"},
        {"artifact_type": "   ", "path": "p"},
        {"artifact_type": "plan", "path": ""},
        {"artifact_type": "plan", "path": "   "},
        {"artifact_type": "plan", "uri": ""},
        {"artifact_type": "plan", "uri": "   ", "path": "p"},
        {"artifact_type": "plan", "path": "p", "producer_stage": ""},
        {"artifact_type": "plan", "path": "p", "producer_stage": "  "},
        {"artifact_type": "plan", "path": "p", "artifact_schema": "  ", "artifact_schema_version": "1"},
    ],
)
def test_empty_or_whitespace_fields_rejected(kwargs):
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(**kwargs)


def test_empty_string_path_never_becomes_dot():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="")


# --------------------------------------------------------------------------
# 4. consumer_stages tri-state
# --------------------------------------------------------------------------


def test_consumer_stages_omitted_key_absent():
    ref = ArtifactRef(artifact_type="plan", path="p")
    assert ref.consumer_stages is None
    assert "consumer_stages" not in ref.to_dict()


def test_consumer_stages_empty_preserved_as_empty_list():
    ref = ArtifactRef(artifact_type="plan", path="p", consumer_stages=())
    assert ref.consumer_stages == ()
    assert ref.to_dict()["consumer_stages"] == []


def test_consumer_stages_ordered_list_preserved():
    ref = ArtifactRef(
        artifact_type="plan", path="p", consumer_stages=["execution", "qa"]
    )
    assert ref.consumer_stages == ("execution", "qa")
    assert ref.to_dict()["consumer_stages"] == ["execution", "qa"]


def test_consumer_stages_duplicates_rejected():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="p", consumer_stages=["qa", "qa"])


@pytest.mark.parametrize("bad", [["qa", ""], ["qa", "  "], ["qa", 3]])
def test_consumer_stages_invalid_items_rejected(bad):
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="p", consumer_stages=bad)


# --------------------------------------------------------------------------
# 5. Schema name/version pairing
# --------------------------------------------------------------------------


def test_schema_name_and_version_pair_ok():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        artifact_schema="plan.schema",
        artifact_schema_version="2",
    )
    assert ref.artifact_schema == "plan.schema"
    assert ref.artifact_schema_version == "2"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"artifact_schema": "plan.schema"},
        {"artifact_schema_version": "2"},
    ],
)
def test_schema_name_version_must_pair(kwargs):
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="p", **kwargs)


# --------------------------------------------------------------------------
# 6. validation_status
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(ArtifactValidationStatus))
def test_every_validation_status_roundtrips(status):
    ref = ArtifactRef(artifact_type="plan", path="p", validation_status=status)
    assert ref.validation_status is status
    assert ref.to_dict()["validation_status"] == status.value
    assert ArtifactRef.from_dict(ref.to_dict()) == ref


def test_validation_status_accepts_string_value():
    ref = ArtifactRef(artifact_type="plan", path="p", validation_status="valid")
    assert ref.validation_status is ArtifactValidationStatus.VALID


def test_invalid_validation_status_string_rejected():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="p", validation_status="bad")


def test_absent_validation_status_stays_absent():
    ref = ArtifactRef(artifact_type="plan", path="p")
    assert ref.validation_status is None
    assert "validation_status" not in ref.to_dict()


# --------------------------------------------------------------------------
# 7. checksum_sha256
# --------------------------------------------------------------------------


_VALID_SHA = "a" * 64


def test_checksum_valid_lowercase_hex():
    ref = ArtifactRef(artifact_type="plan", path="p", checksum_sha256=_VALID_SHA)
    assert ref.checksum_sha256 == _VALID_SHA


@pytest.mark.parametrize(
    "bad",
    [
        "a" * 63,  # too short
        "a" * 65,  # too long (fullmatch rejects prefix)
        "A" * 64,  # uppercase
        "g" * 64,  # non-hex char
        "a" * 63 + "z",  # trailing non-hex
    ],
)
def test_checksum_invalid_string_rejected(bad):
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="p", checksum_sha256=bad)


def test_checksum_non_string_rejected():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="p", checksum_sha256=123)


def test_checksum_omitted_key_absent_not_zeros():
    ref = ArtifactRef(artifact_type="plan", path="p")
    assert ref.checksum_sha256 is None
    assert "checksum_sha256" not in ref.to_dict()


# --------------------------------------------------------------------------
# 8. Invalid outer types raise the aggregate contract error
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"consumer_stages": 42},
        {"consumer_stages": "qa"},  # str must not be a consumer collection
        {"metadata": None},
        {"metadata": [("k", "v")]},  # non-Mapping
        {"path": 123},
        {"checksum_sha256": 123},
        {"validation_status": object()},
    ],
)
def test_invalid_outer_types_raise_contract_error(kwargs):
    base = {"artifact_type": "plan", "path": "p"}
    base.update(kwargs)
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(**base)


# --------------------------------------------------------------------------
# 9. to_dict shape / serialization
# --------------------------------------------------------------------------


def test_to_dict_minimal_shape():
    ref = ArtifactRef(artifact_type="plan", path="planning/plan.json")
    assert ref.to_dict() == {
        "artifact_type": "plan",
        "artifact_ref_schema_version": "1",
        "path": "planning/plan.json",
    }


def test_to_dict_omits_unset_and_converts_types():
    ref = ArtifactRef(
        artifact_type="plan",
        uri="s3://b/plan.json",
        validation_status=ArtifactValidationStatus.VALID,
        consumer_stages=["qa"],
    )
    d = ref.to_dict()
    assert d == {
        "artifact_type": "plan",
        "artifact_ref_schema_version": "1",
        "uri": "s3://b/plan.json",
        "validation_status": "valid",
        "consumer_stages": ["qa"],
    }
    assert "path" not in d
    assert "checksum_sha256" not in d


def test_to_json_deterministic_sorted_keys():
    ref = ArtifactRef(
        artifact_type="plan", path="p", uri="s3://b/x", producer_stage="planning"
    )
    assert ref.to_json() == (
        '{"artifact_ref_schema_version": "1", "artifact_type": "plan", '
        '"path": "p", "producer_stage": "planning", "uri": "s3://b/x"}'
    )


# --------------------------------------------------------------------------
# 10. Round-trip equality
# --------------------------------------------------------------------------


def test_full_roundtrip_equality():
    ref = ArtifactRef(
        artifact_type="plan",
        path="planning/plan.json",
        uri="s3://b/plan.json",
        producer_stage="planning",
        consumer_stages=["execution", "qa"],
        artifact_schema="plan.schema",
        artifact_schema_version="2",
        validation_status=ArtifactValidationStatus.VALID,
        checksum_sha256=_VALID_SHA,
        metadata={"nested": {"n": [1, 2, 3]}, "flag": True},
    )
    import json

    assert ArtifactRef.from_dict(ref.to_dict()) == ref
    assert ArtifactRef.from_dict(json.loads(ref.to_json())) == ref


# --------------------------------------------------------------------------
# 11. from_dict forward-compat + safety
# --------------------------------------------------------------------------


def test_from_dict_ignores_unknown_top_level_field():
    ref = ArtifactRef.from_dict(
        {"artifact_type": "plan", "path": "p", "future_field": "ignored"}
    )
    assert ref.artifact_type == "plan"
    assert ref.path == Path("p")


def test_from_dict_empty_path_raises_not_dot():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef.from_dict({"artifact_type": "plan", "path": ""})


def test_from_dict_bad_validation_status_raises():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef.from_dict(
            {"artifact_type": "plan", "path": "p", "validation_status": "bad"}
        )


def test_from_dict_string_consumer_stages_raises_not_char_split():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef.from_dict(
            {"artifact_type": "plan", "path": "p", "consumer_stages": "qa"}
        )


@pytest.mark.parametrize("data", [None, 42, "x", [("artifact_type", "plan")]])
def test_from_dict_non_mapping_raises(data):
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef.from_dict(data)


def test_validate_payload_rejects_non_mapping():
    errors = ArtifactRef.validate_payload(None, where="ArtifactRef")
    assert errors and "mapping" in errors[0]


# --------------------------------------------------------------------------
# 12. Unsupported contract version
# --------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["0", "2", ""])
def test_unsupported_artifact_ref_schema_version_rejected(version):
    with pytest.raises(ArtifactRefValidationError) as excinfo:
        ArtifactRef(artifact_type="plan", path="p", artifact_ref_schema_version=version)
    assert any("artifact_ref_schema_version" in e for e in excinfo.value.errors)


def test_non_string_artifact_ref_schema_version_rejected():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef(artifact_type="plan", path="p", artifact_ref_schema_version=1)


# --------------------------------------------------------------------------
# 13. Aggregated errors
# --------------------------------------------------------------------------


def test_multiple_errors_reported_together():
    with pytest.raises(ArtifactRefValidationError) as excinfo:
        ArtifactRef(artifact_type="", checksum_sha256="nope")
    errors = excinfo.value.errors
    assert any("artifact_type" in e for e in errors)
    assert any("checksum_sha256" in e for e in errors)
    # Also missing path+uri.
    assert any("at least one of path or uri" in e for e in errors)
    assert len(errors) >= 3


# --------------------------------------------------------------------------
# 14. Metadata serialization
# --------------------------------------------------------------------------


def test_nested_jsonable_metadata_serializes():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"a": {"b": [1, 2, {"c": None}]}, "n": 3.5},
    )
    assert ref.to_dict()["metadata"] == {"a": {"b": [1, 2, {"c": None}]}, "n": 3.5}


def test_non_string_metadata_key_raises_no_partial_output():
    ref = ArtifactRef(artifact_type="plan", path="p", metadata={1: "v"})
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_dict()


def test_non_jsonable_metadata_value_raises_no_partial_output():
    ref = ArtifactRef(artifact_type="plan", path="p", metadata={"k": object()})
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_dict()


def test_empty_metadata_omitted():
    ref = ArtifactRef(artifact_type="plan", path="p")
    assert "metadata" not in ref.to_dict()


# --------------------------------------------------------------------------
# 15. Defensive copying
# --------------------------------------------------------------------------


def test_metadata_defensively_copied():
    caller = {"k": "v"}
    ref = ArtifactRef(artifact_type="plan", path="p", metadata=caller)
    caller["k"] = "mutated"
    caller["new"] = "x"
    assert ref.metadata == {"k": "v"}


def test_consumer_stages_defensively_copied():
    caller = ["execution", "qa"]
    ref = ArtifactRef(artifact_type="plan", path="p", consumer_stages=caller)
    caller.append("extra")
    caller[0] = "mutated"
    assert ref.consumer_stages == ("execution", "qa")


# --------------------------------------------------------------------------
# 16. from_dict required-field contract (Defect 1)
# --------------------------------------------------------------------------


def test_from_dict_empty_payload_raises_validation_error():
    with pytest.raises(ArtifactRefValidationError):
        ArtifactRef.from_dict({})


def test_from_dict_missing_artifact_type_reports_field_not_typeerror():
    with pytest.raises(ArtifactRefValidationError) as excinfo:
        ArtifactRef.from_dict({"path": "p"})
    assert any("artifact_type" in e for e in excinfo.value.errors)


def test_from_dict_aggregates_missing_type_with_other_field_errors():
    with pytest.raises(ArtifactRefValidationError) as excinfo:
        ArtifactRef.from_dict(
            {
                "path": "",
                "uri": " ",
                "consumer_stages": "qa",
                "checksum_sha256": "bad",
            }
        )
    errors = excinfo.value.errors
    assert any("artifact_type" in e for e in errors)
    assert any("path" in e for e in errors)
    assert any("uri" in e for e in errors)
    assert any("consumer_stages" in e for e in errors)
    assert any("checksum_sha256" in e for e in errors)


def test_from_dict_missing_type_never_leaks_raw_typeerror():
    # ArtifactRefValidationError subclasses ValueError; a bare TypeError
    # (or non-subclass ValueError) escaping would be the defect.
    try:
        ArtifactRef.from_dict({"path": "p"})
    except ArtifactRefValidationError:
        pass
    except TypeError as exc:  # pragma: no cover - defect regression guard
        pytest.fail(f"raw TypeError leaked: {exc!r}")


# --------------------------------------------------------------------------
# 17. Tuple canonicalization + round-trip equality (Defect 2)
# --------------------------------------------------------------------------


def test_top_level_tuple_metadata_canonicalized_to_list():
    ref = ArtifactRef(artifact_type="plan", path="p", metadata={"coords": (1, 2)})
    assert ref.metadata == {"coords": [1, 2]}
    assert isinstance(ref.metadata["coords"], list)


def test_deeply_nested_tuples_normalized_at_every_depth():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"nested": {"rows": [("a", "b"), {"values": (3, 4)}]}},
    )
    assert ref.metadata == {"nested": {"rows": [["a", "b"], {"values": [3, 4]}]}}
    rows = ref.metadata["nested"]["rows"]
    assert isinstance(rows[0], list)
    assert isinstance(rows[1]["values"], list)


def test_to_dict_metadata_contains_only_lists_not_tuples():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"coords": (1, 2), "n": {"rows": [("a", "b")]}},
    )
    meta = ref.to_dict()["metadata"]
    assert meta == {"coords": [1, 2], "n": {"rows": [["a", "b"]]}}
    assert isinstance(meta["coords"], list)
    assert isinstance(meta["n"]["rows"][0], list)


def test_tuple_metadata_dict_and_json_round_trip_to_equality():
    import json

    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"coords": (1, 2), "n": {"rows": [("a", "b")]}},
    )
    assert ArtifactRef.from_dict(ref.to_dict()) == ref
    assert ArtifactRef.from_dict(json.loads(ref.to_json())) == ref


# --------------------------------------------------------------------------
# 18. Non-finite float rejection (Defect 3)
# --------------------------------------------------------------------------


def test_nan_metadata_rejected_with_location():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"metrics": {"value": float("nan")}}
    )
    with pytest.raises(ArtifactMetadataSerializationError) as excinfo:
        ref.to_dict()
    msg = str(excinfo.value)
    assert "metrics" in msg and "value" in msg


def test_positive_and_negative_infinity_metadata_rejected():
    for bad in (float("inf"), float("-inf")):
        ref = ArtifactRef(
            artifact_type="plan", path="p", metadata={"metrics": {"value": bad}}
        )
        with pytest.raises(ArtifactMetadataSerializationError):
            ref.to_dict()
        with pytest.raises(ArtifactMetadataSerializationError):
            ref.to_json()


def test_finite_float_metadata_still_serializes():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"metrics": {"value": 3.5}}
    )
    assert ref.to_dict()["metadata"] == {"metrics": {"value": 3.5}}


# --------------------------------------------------------------------------
# 19. Cycle rejection + shared non-cyclic refs (Defect 4)
# --------------------------------------------------------------------------


def test_cyclic_dict_metadata_rejected_not_recursionerror():
    cycle: dict = {}
    cycle["self"] = cycle
    ref = ArtifactRef(artifact_type="plan", path="p", metadata=cycle)
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_dict()


def test_cyclic_list_metadata_rejected_not_recursionerror():
    inner: list = []
    inner.append(inner)
    ref = ArtifactRef(artifact_type="plan", path="p", metadata={"loop": inner})
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_dict()


def test_mixed_dict_list_cycle_rejected():
    node: dict = {"children": []}
    node["children"].append(node)
    ref = ArtifactRef(artifact_type="plan", path="p", metadata=node)
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_dict()


def test_shared_non_cyclic_child_serializes_successfully():
    child = {"x": 1}
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"left": child, "right": child}
    )
    assert ref.to_dict()["metadata"] == {"left": {"x": 1}, "right": {"x": 1}}


# --------------------------------------------------------------------------
# 20. Deep defensive detachment (Defect 5)
# --------------------------------------------------------------------------


def test_nested_caller_mutation_does_not_leak_into_ref():
    caller = {"nested": {"values": [1, 2]}}
    ref = ArtifactRef(artifact_type="plan", path="p", metadata=caller)
    caller["nested"]["values"].append(3)
    assert ref.metadata == {"nested": {"values": [1, 2]}}


def test_to_dict_result_mutation_does_not_change_ref():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"nested": {"values": [1, 2]}}
    )
    out = ref.to_dict()
    out["metadata"]["nested"]["values"].append(99)
    assert ref.metadata == {"nested": {"values": [1, 2]}}


def test_two_to_dict_results_are_independent():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"nested": {"values": [1, 2]}}
    )
    first = ref.to_dict()
    first["metadata"]["nested"]["values"].append(99)
    second = ref.to_dict()
    assert second["metadata"]["nested"]["values"] == [1, 2]


def test_tuple_normalization_does_not_alias_caller_containers():
    inner = [1, 2]
    caller = {"seq": (inner,)}
    ref = ArtifactRef(artifact_type="plan", path="p", metadata=caller)
    inner.append(3)
    assert ref.metadata == {"seq": [[1, 2]]}
