from __future__ import annotations

from scripts.sensor_invalidation_registry import (
    PredicateResult,
    SensorObservation,
    SensorObservationSnapshot,
    SensorPredicateLease,
    SensorPredicateRegistry,
    SensorPredicateSpec,
)


def _no_new_objects(values, parameters):
    visible = set(values["rgbd.visible_object_ids"])
    baseline = set(parameters["baseline_object_ids"])
    additions = sorted(visible - baseline)
    return PredicateResult(
        valid=not additions,
        reason=("visible_object_set_stable" if not additions else "object_entered_frame"),
        evidence={"new_object_ids": additions},
    )


def _registry() -> SensorPredicateRegistry:
    registry = SensorPredicateRegistry()
    registry.register(
        SensorPredicateSpec(
            predicate_id="vision.no_new_objects",
            description="No unplanned object enters the observed scene.",
            required_channels=("rgbd.visible_object_ids",),
            maximum_age_s=0.25,
            evaluator=_no_new_objects,
        )
    )
    return registry


def _observation(value, *, timestamp_s=10.0, valid=True):
    return SensorObservation(
        channel_id="rgbd.visible_object_ids",
        source_id="ros2.rgbd_detector",
        sequence=42,
        timestamp_s=timestamp_s,
        value=value,
        valid=valid,
        frame_id="camera_optical_frame",
    )


def _lease():
    return SensorPredicateLease(
        predicate_id="vision.no_new_objects",
        parameters={"baseline_object_ids": ["banana", "plate"]},
    )


def test_runtime_sensor_predicate_accepts_stable_scene():
    result = _registry().assess(
        [_lease()],
        SensorObservationSnapshot([_observation(["banana", "plate"])]),
        evaluated_at_s=10.1,
    )
    assert result.valid
    assert result.invalidation_events == ()
    assert result.evaluations[0].source_ids == ("ros2.rgbd_detector",)
    assert result.evaluations[0].channel_sequences == {
        "rgbd.visible_object_ids": 42
    }


def test_object_entering_rgbd_frame_invalidates_without_lease_core_changes():
    result = _registry().assess(
        [_lease()],
        SensorObservationSnapshot(
            [_observation(["banana", "plate", "human_hand"])]
        ),
        evaluated_at_s=10.1,
    )
    assert not result.valid
    assert result.invalidation_reasons == ("object_entered_frame",)
    event = result.invalidation_events[0]
    assert event.evidence["new_object_ids"] == ["human_hand"]


def test_required_missing_invalid_and_stale_ros2_evidence_fail_closed():
    cases = (
        (SensorObservationSnapshot([]), 10.1, "sensor_observation_missing"),
        (
            SensorObservationSnapshot([_observation([], valid=False)]),
            10.1,
            "sensor_observation_invalid",
        ),
        (
            SensorObservationSnapshot([_observation([], timestamp_s=9.0)]),
            10.1,
            "sensor_observation_stale",
        ),
    )
    for snapshot, now, reason in cases:
        result = _registry().assess([_lease()], snapshot, evaluated_at_s=now)
        assert not result.valid
        assert result.invalidation_reasons == (reason,)


def test_unregistered_predicate_fails_closed_but_unleased_sensor_does_not_stop():
    registry = _registry()
    no_lease = registry.assess(
        [],
        SensorObservationSnapshot([_observation(["unexpected"])]),
        evaluated_at_s=10.1,
    )
    assert no_lease.valid
    unknown = registry.assess(
        [SensorPredicateLease("future.force_spike", {})],
        SensorObservationSnapshot([]),
        evaluated_at_s=10.1,
    )
    assert not unknown.valid
    assert unknown.invalidation_reasons == ("predicate_not_registered",)
