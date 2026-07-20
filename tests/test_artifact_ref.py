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


# --------------------------------------------------------------------------
# 21. Deep semantic immutability of metadata (Prompt 18R-A)
# --------------------------------------------------------------------------


def test_top_level_metadata_item_assignment_rejected():
    ref = ArtifactRef(artifact_type="plan", path="p", metadata={"k": "v"})
    with pytest.raises(TypeError):
        ref.metadata["new"] = 1  # type: ignore[index]
    assert ref.metadata == {"k": "v"}


def test_top_level_metadata_deletion_rejected():
    ref = ArtifactRef(artifact_type="plan", path="p", metadata={"k": "v"})
    with pytest.raises(TypeError):
        del ref.metadata["k"]  # type: ignore[misc]
    assert ref.metadata == {"k": "v"}


def test_nested_mapping_mutation_is_harmless():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"nested": {"value": 1}}
    )
    nested = ref.metadata["nested"]
    nested["value"] = 2
    nested["added"] = True
    assert ref.metadata["nested"] == {"value": 1}
    assert ref.to_dict()["metadata"] == {"nested": {"value": 1}}


def test_nested_sequence_append_is_harmless():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"sequence": [1, 2]}
    )
    seq = ref.metadata["sequence"]
    seq.append(3)
    assert ref.metadata["sequence"] == [1, 2]
    assert ref.to_dict()["metadata"] == {"sequence": [1, 2]}


def test_nested_sequence_item_replacement_is_harmless():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"sequence": [1, 2]}
    )
    seq = ref.metadata["sequence"]
    seq[0] = 99
    assert ref.metadata["sequence"] == [1, 2]


def test_nested_sequence_deletion_is_harmless():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"sequence": [1, 2, 3]}
    )
    seq = ref.metadata["sequence"]
    del seq[0]
    assert ref.metadata["sequence"] == [1, 2, 3]


def test_mapping_inside_nested_sequence_mutation_is_harmless():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"rows": [{"k": 1}]}
    )
    row = ref.metadata["rows"][0]
    row["k"] = 2
    row["added"] = True
    assert ref.metadata["rows"] == [{"k": 1}]


def test_view_getitem_returns_fresh_detached_values():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"nested": {"values": [1, 2]}}
    )
    first = ref.metadata["nested"]
    second = ref.metadata["nested"]
    assert first == second
    assert first is not second
    first["values"].append(3)
    assert ref.metadata["nested"]["values"] == [1, 2]


def test_caller_owned_nested_list_mutation_after_construction_is_isolated():
    caller_list = [1, 2]
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"seq": caller_list}
    )
    caller_list.append(3)
    caller_list[0] = 99
    assert ref.metadata["seq"] == [1, 2]


def test_caller_owned_invalid_mutable_leaf_is_not_retained():
    invalid = bytearray(b"a")
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"invalid": invalid}
    )
    invalid.extend(b"bcd")
    # The reference never holds the caller's mutable object: reading it does not
    # return the bytearray, and serialization still rejects it by original type.
    with pytest.raises(ArtifactMetadataSerializationError):
        _ = ref.metadata["invalid"]
    with pytest.raises(ArtifactMetadataSerializationError) as excinfo:
        ref.to_dict()
    assert "bytearray" in str(excinfo.value)


def test_metadata_view_is_not_a_dict_but_compares_by_value():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"coords": [1, 2]}
    )
    assert not isinstance(ref.metadata, dict)
    assert ref.metadata == {"coords": [1, 2]}
    assert ref.metadata != {"coords": [1, 3]}


def test_metadata_view_is_unhashable_like_dict():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"coords": [1, 2]}
    )
    with pytest.raises(TypeError):
        hash(ref.metadata)


def test_to_dict_metadata_uses_exact_plain_container_types():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"nested": {"values": [1, 2]}},
    )
    result = ref.to_dict()
    assert type(result["metadata"]) is dict
    assert type(result["metadata"]["nested"]) is dict
    assert type(result["metadata"]["nested"]["values"]) is list


def test_cycle_construction_succeeds_and_serialization_rejects_unchanged():
    cycle: dict = {}
    cycle["self"] = cycle
    ref = ArtifactRef(artifact_type="plan", path="p", metadata=cycle)
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_dict()
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_json()


def test_shared_acyclic_value_not_mistaken_for_cycle():
    shared = {"value": 1}
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"left": shared, "right": shared},
    )
    assert ref.to_dict()["metadata"] == {
        "left": {"value": 1},
        "right": {"value": 1},
    }


def test_to_json_output_is_deterministic_after_immutability_change():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"b": 2, "a": {"y": [1, 2], "x": 1}},
    )
    assert ref.to_json() == ref.to_json()
    assert ref.to_json() == (
        '{"artifact_ref_schema_version": "1", "artifact_type": "plan", '
        '"metadata": {"a": {"x": 1, "y": [1, 2]}, "b": 2}, "path": "p"}'
    )


def test_from_dict_round_trip_preserves_equality_after_immutability_change():
    import json

    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"coords": (1, 2), "nested": {"rows": [("a", "b")]}},
    )
    assert ArtifactRef.from_dict(ref.to_dict()) == ref
    assert ArtifactRef.from_dict(json.loads(ref.to_json())) == ref


# --------------------------------------------------------------------------
# 22. Stored representation is genuinely immutable (Prompt 18R-A repair)
# --------------------------------------------------------------------------


def test_reaching_root_attribute_cannot_replace_it():
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={"nested": {"value": 1}}
    )
    with pytest.raises(AttributeError):
        ref.metadata._root = {"replacement": True}  # type: ignore[attr-defined]
    assert ref.to_dict()["metadata"] == {"nested": {"value": 1}}


def test_reaching_root_exposes_no_mutable_container():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"nested": {"value": 1}, "sequence": [1, 2]},
    )
    root = ref.metadata._root
    # The root and its entries are immutable: no dict/list to edit or replace.
    assert isinstance(root.entries, tuple)
    with pytest.raises(AttributeError):
        root.entries = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        root.entries[0] = ("hacked", True)  # type: ignore[index]
    # The old mutable backing-graph attribute no longer exists.
    assert not hasattr(ref.metadata, "_graph")
    assert ref.to_dict()["metadata"] == {
        "nested": {"value": 1},
        "sequence": [1, 2],
    }


def test_old_defect_direct_graph_mutation_is_impossible():
    ref = ArtifactRef(
        artifact_type="plan",
        path="p",
        metadata={"nested": {"value": 1}, "sequence": [1, 2]},
    )
    before = ref.to_dict()
    # None of the previously-successful mutations are reachable anymore.
    assert not hasattr(ref.metadata, "_graph")
    with pytest.raises(AttributeError):
        ref.metadata._root = {"replacement": True}  # type: ignore[attr-defined]
    assert ref.to_dict() == before


def test_mutable_non_string_key_is_not_retained_or_exposed():
    class MutableKey:
        __hash__ = object.__hash__

    caller_key = MutableKey()
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={caller_key: "value"}
    )
    exposed = next(iter(ref.metadata))
    assert exposed is not caller_key
    # Serialization still rejects the non-string key lazily.
    with pytest.raises(ArtifactMetadataSerializationError):
        ref.to_dict()


def test_distinct_unsupported_values_do_not_compare_equal():
    left = ArtifactRef(
        artifact_type="x", path="p", metadata={"bad": bytearray(b"a")}
    )
    right = ArtifactRef(
        artifact_type="x", path="p", metadata={"bad": bytearray(b"b")}
    )
    assert (left == right) is False
    assert left != right


def test_distinct_unsupported_keys_do_not_compare_equal():
    class MutableKey:
        __hash__ = object.__hash__

    left = ArtifactRef(
        artifact_type="x", path="p", metadata={MutableKey(): "v"}
    )
    right = ArtifactRef(
        artifact_type="x", path="p", metadata={MutableKey(): "v"}
    )
    assert (left == right) is False


def test_cyclic_metadata_equality_never_leaks_recursionerror():
    c1: dict = {}
    c1["self"] = c1
    c2: dict = {}
    c2["self"] = c2
    r1 = ArtifactRef(artifact_type="x", path="p", metadata=c1)
    r2 = ArtifactRef(artifact_type="x", path="p", metadata=c2)
    # Must not raise RecursionError; a plain bool result is required.
    assert isinstance(r1 == r2, bool)
    assert isinstance(r1 == r1, bool)


def test_mutating_caller_non_string_key_after_construction_is_isolated():
    class MutableKey:
        __slots__ = ("tag",)
        __hash__ = object.__hash__

        def __init__(self):
            self.tag = 1

    caller_key = MutableKey()
    ref = ArtifactRef(
        artifact_type="plan", path="p", metadata={caller_key: "value"}
    )
    caller_key.tag = 999
    exposed = next(iter(ref.metadata))
    assert exposed is not caller_key
    assert not hasattr(exposed, "tag")


# ---------------------------------------------------------------------------
# 23. Scalar subclasses are canonicalized to exact builtins (Prompt 18R-A repair)
# ---------------------------------------------------------------------------


class _TaggedInt(int):
    def __new__(cls, value, tag):
        obj = int.__new__(cls, value)
        obj.tag = tag
        return obj

    def __eq__(self, other):
        if isinstance(other, _TaggedInt):
            return int(self) == int(other) and self.tag == other.tag
        return int.__eq__(self, other)

    __hash__ = int.__hash__


class _TaggedStr(str):
    def __new__(cls, value, tag):
        obj = str.__new__(cls, value)
        obj.tag = tag
        return obj


class _TaggedFloat(float):
    def __new__(cls, value, tag):
        obj = float.__new__(cls, value)
        obj.tag = tag
        return obj


def test_mutable_scalar_subclass_is_not_retained():
    caller_value = _TaggedInt(7, "a")
    ref = ArtifactRef(
        artifact_type="x", path="p", metadata={"value": caller_value}
    )
    stored_value = ref.metadata["value"]
    assert stored_value is not caller_value
    assert type(stored_value) is int
    assert stored_value == 7


def test_to_dict_returns_exact_builtin_scalar_leaves():
    ref = ArtifactRef(
        artifact_type="x",
        path="p",
        metadata={
            "text": _TaggedStr("hi", "t"),
            "count": _TaggedInt(3, "c"),
            "ratio": _TaggedFloat(1.5, "r"),
        },
    )
    result = ref.to_dict()
    assert type(result["metadata"]["text"]) is str
    assert type(result["metadata"]["count"]) is int
    assert type(result["metadata"]["ratio"]) is float


def test_to_dict_does_not_alias_caller_scalar_subclass():
    caller_count = _TaggedInt(42, "c")
    ref = ArtifactRef(
        artifact_type="x", path="p", metadata={"count": caller_count}
    )
    result = ref.to_dict()
    assert result["metadata"]["count"] is not caller_count
    assert type(result["metadata"]["count"]) is int


def test_mutating_caller_scalar_subclass_does_not_change_equality():
    caller_value = _TaggedInt(7, "a")
    ref_before = ArtifactRef(
        artifact_type="x", path="p", metadata={"value": caller_value}
    )
    equivalent_ref = ArtifactRef(
        artifact_type="x", path="p", metadata={"value": 7}
    )
    assert ref_before == equivalent_ref
    caller_value.tag = "changed"
    assert ref_before == equivalent_ref


def test_bool_scalar_subclass_path_preserves_bool():
    # bool is an int subclass; ordering must keep it a bool.
    ref = ArtifactRef(
        artifact_type="x", path="p", metadata={"flag": True, "n": 1}
    )
    result = ref.to_dict()
    assert result["metadata"]["flag"] is True
    assert type(result["metadata"]["n"]) is int


def test_string_subclass_key_is_detached():
    caller_key = _TaggedStr("k", "t")
    ref = ArtifactRef(
        artifact_type="x", path="p", metadata={caller_key: "v"}
    )
    exposed_key = next(iter(ref.metadata))
    assert exposed_key is not caller_key
    assert type(exposed_key) is str
    assert exposed_key == "k"


def test_serialization_keys_are_exact_strings():
    ref = ArtifactRef(
        artifact_type="x",
        path="p",
        metadata={_TaggedStr("a", "t"): 1, "b": 2},
    )
    result = ref.to_dict()
    for key in result["metadata"]:
        assert type(key) is str
    assert type(next(iter(result["metadata"]))) is str
