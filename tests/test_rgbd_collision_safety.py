import numpy as np
import pytest

from scripts.rgbd_collision_safety import (
    assess_motion_safety,
    backproject_depth,
    bounding_box_mask,
    capsule_point_clearance,
    depth_gate_detection_mask,
    draw_collision_overlay,
    fuse_detection_with_depth,
    oriented_footprint_geometry,
    pregrasp_axis_alignment_observation,
    predict_detection_collisions,
    summarize_labeled_scene_geometry,
    swept_capsule_clearance,
    transform_matrix_from_pose_xyzw,
)


def test_bounding_box_depth_backprojects_into_base_frame():
    depth = np.full((4, 6), 2.0, dtype=np.float32)
    mask = bounding_box_mask(depth.shape, (2, 1, 4, 3))
    intrinsics = np.array([[2.0, 0.0, 2.5], [0.0, 2.0, 1.5], [0.0, 0.0, 1.0]])
    camera_to_base = np.eye(4)
    camera_to_base[:3, 3] = [1.0, 0.0, 0.5]
    points = backproject_depth(
        depth, intrinsics, mask=mask, camera_to_base=camera_to_base
    )
    assert points.shape == (4, 3)
    assert np.allclose(points[:, 2], 2.5)
    assert np.all((points[:, 0] >= 0.5) & (points[:, 0] <= 1.5))


def test_capsule_and_swept_volume_report_signed_clearance():
    obstacle = np.array([[0.5, 0.12, 0.0]], dtype=np.float32)
    starts = np.array([[0.0, 0.0, 0.0]])
    ends = np.array([[1.0, 0.0, 0.0]])
    radii = np.array([0.10])
    assert capsule_point_clearance(obstacle, starts, ends, radii) == pytest.approx(0.02)
    proposed_starts = starts + np.array([0.0, 0.10, 0.0])
    proposed_ends = ends + np.array([0.0, 0.10, 0.0])
    assert swept_capsule_clearance(
        obstacle, starts, ends, proposed_starts, proposed_ends, radii
    ) < 0.0


def test_safety_fusion_detects_slip_drop_and_rgbd_collision():
    reference = np.array([0.0, 0.0, 0.15])
    assessment = assess_motion_safety(
        phase="above_plate",
        eef_xyz=np.array([0.5, 0.0, 0.25]),
        object_xyz=np.array([0.45, 0.0, 0.02]),
        reference_eef_minus_object=reference,
        object_initial_z=0.02,
        rgbd_clearance_m=0.01,
    )
    assert assessment.safe is False
    assert set(assessment.reasons) == {
        "grasp_transform_drift",
        "object_drop",
        "predicted_collision",
    }


def test_safety_fusion_accepts_stable_clear_transport():
    assessment = assess_motion_safety(
        phase="above_plate",
        eef_xyz=np.array([0.5, 0.0, 0.25]),
        object_xyz=np.array([0.5, 0.0, 0.10]),
        reference_eef_minus_object=np.array([0.0, 0.0, 0.15]),
        object_initial_z=0.02,
        rgbd_clearance_m=0.08,
    )
    assert assessment.safe is True
    assert assessment.reasons == ()


def test_box_depth_gate_rejects_background_and_builds_3d_detection():
    depth = np.full((12, 12), 2.0, dtype=np.float32)
    depth[3:9, 3:9] = 0.8
    mask, seed = depth_gate_detection_mask(depth, (2, 2, 10, 10))
    assert seed == pytest.approx(0.8)
    assert mask.sum() == 36
    detection = fuse_detection_with_depth(
        label="box",
        score=0.9,
        xyxy=(2, 2, 10, 10),
        depth_m=depth,
        intrinsics=np.array(
            [[10.0, 0.0, 5.5], [0.0, 10.0, 5.5], [0.0, 0.0, 1.0]]
        ),
        camera_to_base=np.eye(4),
        stride=1,
    )
    assert len(detection.points_base) == 36
    assert detection.center_base[2] == pytest.approx(0.8)


def test_robot_self_mask_is_excluded_before_depth_fusion():
    depth = np.full((10, 10), 0.8, dtype=np.float32)
    robot_mask = np.zeros_like(depth, dtype=bool)
    robot_mask[:, :5] = True
    selected, _ = depth_gate_detection_mask(
        depth,
        (0, 0, 10, 10),
        exclusion_mask=robot_mask,
    )
    assert not selected[:, :5].any()
    assert selected[:, 5:].all()


def test_rgbd_detection_predicts_swept_collision_and_allowed_contact():
    depth = np.ones((10, 10), dtype=np.float32)
    intrinsics = np.array(
        [[100.0, 0.0, 4.5], [0.0, 100.0, 4.5], [0.0, 0.0, 1.0]]
    )
    detection = fuse_detection_with_depth(
        label="cup",
        score=0.95,
        xyxy=(2, 2, 8, 8),
        depth_m=depth,
        intrinsics=intrinsics,
        camera_to_base=np.eye(4),
        stride=1,
    )
    starts = np.array([[-0.2, 0.0, 0.8]])
    ends = np.array([[0.2, 0.0, 0.8]])
    proposed_starts = starts + np.array([0.0, 0.0, 0.2])
    proposed_ends = ends + np.array([0.0, 0.0, 0.2])
    collision = predict_detection_collisions(
        [detection],
        starts,
        ends,
        np.array([0.04]),
        proposed_segment_starts=proposed_starts,
        proposed_segment_ends=proposed_ends,
    )[0]
    assert collision.potential_collision is True
    allowed = predict_detection_collisions(
        [detection],
        starts,
        ends,
        np.array([0.04]),
        proposed_segment_starts=proposed_starts,
        proposed_segment_ends=proposed_ends,
        allowed_contact_labels={"cup"},
    )[0]
    assert allowed.potential_collision is False
    assert allowed.allowed_contact is True
    overlay = draw_collision_overlay(np.zeros((10, 10, 3), dtype=np.uint8), [collision])
    assert overlay.shape == (10, 10, 3)
    assert overlay.any()


def test_optical_camera_pose_transform_uses_xyzw_convention():
    transform = transform_matrix_from_pose_xyzw(
        np.array([1.0, 2.0, 3.0]),
        np.array([0.0, 0.0, np.sqrt(0.5), np.sqrt(0.5)]),
    )
    point = np.array([1.0, 0.0, 0.0, 1.0])
    assert np.allclose(transform @ point, [1.0, 3.0, 3.0, 1.0])


def test_oriented_footprint_reports_rotated_object_axes():
    yaw = np.deg2rad(28.0)
    rotation = np.array(
        [[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]]
    )
    xy = np.array(
        [
            [-0.04, -0.02],
            [0.04, -0.02],
            [0.04, 0.02],
            [-0.04, 0.02],
        ]
    ) @ rotation.T
    footprint = oriented_footprint_geometry(
        np.column_stack((xy, np.full(4, 0.03)))
    )
    major = np.asarray(footprint["oriented_footprint_axes_base"][0])
    expected = np.array([np.cos(yaw), np.sin(yaw), 0.0])
    assert abs(float(np.dot(major, expected))) == pytest.approx(1.0, abs=1e-5)
    assert footprint["oriented_footprint_extents_m"] == pytest.approx(
        [0.08, 0.04], abs=1e-5
    )


def test_pregrasp_alignment_exposes_correction_and_admission():
    scene = {
        "geometries": [
            {
                "runtime_id": "object",
                "oriented_footprint_axes_base": [
                    [np.sqrt(0.5), np.sqrt(0.5), 0.0],
                    [-np.sqrt(0.5), np.sqrt(0.5), 0.0],
                ],
                "oriented_footprint_extents_m": [0.08, 0.04],
            }
        ]
    }
    misaligned = pregrasp_axis_alignment_observation(
        scene_geometry=scene,
        actuator_geometry={"closing_axis_robot_root": [1.0, 0.0, 0.0]},
        object_runtime_id="object",
        maximum_error_deg=12.0,
    )
    assert misaligned["available"] is True
    assert misaligned["aligned"] is False
    assert misaligned["minimum_axis_error_deg"] == pytest.approx(45.0)
    assert abs(
        misaligned["axis_comparisons"][0]["candidate_yaw_correction_deg"]
    ) == pytest.approx(45.0)

    aligned = pregrasp_axis_alignment_observation(
        scene_geometry=scene,
        actuator_geometry={
            "closing_axis_robot_root": [np.sqrt(0.5), np.sqrt(0.5), 0.0]
        },
        object_runtime_id="object",
        maximum_error_deg=12.0,
    )
    assert aligned["aligned"] is True
    assert aligned["minimum_axis_error_deg"] == pytest.approx(0.0)


def test_instance_geometry_groups_scene_assets_and_reports_base_frame_axes():
    depth = np.full((16, 16), 1.0, dtype=np.float32)
    instance_ids = np.zeros((16, 16), dtype=np.int32)
    instance_ids[:, :8] = 11
    instance_ids[:, 8:] = 12
    summary = summarize_labeled_scene_geometry(
        depth_m=depth,
        instance_ids=instance_ids,
        id_to_labels={
            "11": "/World/envs/env_0/scene/object/mesh",
            "12": {"class": "/World/envs/env_0/scene/support/mesh"},
            "13": "/World/envs/env_0/robot/link",
        },
        intrinsics=np.array(
            [[40.0, 0.0, 7.5], [0.0, 40.0, 7.5], [0.0, 0.0, 1.0]]
        ),
        camera_to_base=np.eye(4),
        stride=2,
        minimum_points=8,
    )
    assert summary["available"] is True
    geometries = summary["geometries"]
    assert [item["runtime_id"] for item in geometries] == ["object", "support"]
    assert all(item["point_count"] == 32 for item in geometries)
    assert all(len(item["principal_axes_base"]) == 3 for item in geometries)
    assert all(
        len(item["oriented_footprint_axes_base"]) == 2 for item in geometries
    )
    assert all(item["visible_aabb_min_base_m"][2] == 1.0 for item in geometries)
