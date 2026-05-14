# Camera + Sensor Pipeline Research

For vision-gated canonicals (quality gate, parcel singulation, kitting, color sort, 3D bin-pick).

## Camera types in Isaac Sim

| Type | API | Use case |
|---|---|---|
| RGB | `isaacsim.sensors.camera.Camera` + `rep.AnnotatorRegistry` `rgb` | Inspection, label, classification |
| RGB-D | `SingleViewDepthSensor` | 3D bin-pick |
| Depth (raw) | `Camera` + `add_distance_to_image_plane_to_frame()` | Range maps, occupancy |
| Stereo | 2× `Camera` at fixed baseline | 6-DoF pose estimation, Hawk/ZED |
| Fisheye | `Camera` with `opencvFisheye` distortion | Wide-angle, AMR rear-view |

Annotators (call after `.initialize()`):
```
add_rgb_to_frame() → frame["rgb"]
add_distance_to_image_plane_to_frame()
add_bounding_box_2d_tight_to_frame()
add_semantic_segmentation_to_frame()
add_instance_segmentation_to_frame()
add_motion_vectors_to_frame()
add_occlusion_to_frame()
attach_annotator("DepthSensorDistance")  # stereo-specific
```

## Mounting wrist camera on Franka EE

```python
from isaacsim.sensors.camera import Camera
import isaacsim.core.utils.numpy.rotations as rot_utils
import numpy as np

camera = Camera(
    prim_path="/World/Franka/panda_hand/wrist_cam",  # child of EE link → inherits pose
    frequency=30,
    resolution=(84, 84),  # CNN obs typical
    orientation=rot_utils.euler_angles_to_quats(
        np.array([0, 90, 0]), degrees=True)
)
camera.initialize()
camera.add_rgb_to_frame()

# Per-step read (after world.step(render=True)):
frame = camera.get_current_frame()
rgb = frame["rgb"]  # numpy HxWx4 uint8
```

For Isaac Lab N parallel envs: use `TiledCameraCfg` (10x throughput vs N separate cameras).

## Mounting overhead/side camera

```python
camera = Camera(
    prim_path="/World/Sensors/OverheadCam",
    position=np.array([0.0, 0.0, 3.0]),
    orientation=rot_utils.euler_angles_to_quats(np.array([0, 90, 0]), degrees=True),
    frequency=20,
    resolution=(1280, 720),
)
camera.initialize()
camera.add_rgb_to_frame()
camera.add_bounding_box_2d_tight_to_frame()
```

USD attrs: `focalLength` (mm), `horizontalAperture`, `clippingRange`. FoV: `hfov = 2*atan(aperture/(2*focal))`.

## Existing tools (already in our registry)

| Tool | What it does |
|---|---|
| `capture_viewport` | One PNG of active viewport |
| `capture_camera_image` | PNG from specific camera (Replicator render product) |
| `inspect_camera` | Read UsdGeom.Camera attrs |
| `configure_camera` | Set resolution, FoV, depth stream |
| `get_camera_params` / `set_camera_params` | Full USD attr R/W |
| `list_cameras` | Inventory all Camera prims |
| `set_camera_look_at` | Orient toward world target |
| `configure_zmq_stream` | OmniGraph ZMQ PUB stream |
| `vision_detect_objects` | Viewport → Gemini object detection |
| `vision_bounding_boxes` | Viewport → bboxes |
| `vision_analyze_scene` | Viewport → free-form VLM Q&A |
| `vision_plan_trajectory` | Viewport → waypoints |

## Gaps

1. **`add_wrist_camera(robot_path, ee_link, offset, fov, resolution)`** — no dedicated tool; needed for M-12/wrist-cam canonicals. Currently requires `create_prim(Camera)` + manual xform parenting + `configure_camera`.

2. **`add_overhead_camera(position, look_at, fov, resolution)`** — composable, but no single-call wrapper.

3. **`read_camera_frame(camera_path) -> bytes`** — partially covered by `capture_camera_image` BUT that does Replicator create/destroy per call (200ms+ overhead). Persistent render-product version missing.

4. **`classify_object_at_camera(camera_path, classifier_id)`** — not implemented. Currently `vision_*` tools only read **active viewport**, not a named camera. Need `camera_path` arg + route through `capture_camera_image`.

## capture_viewport vs proper camera

`capture_viewport` reads active viewport (UI-bound camera). For vision-canonicals this is wrong:
1. Overhead/wrist camera may not be active viewport
2. Can't run headless reliably
3. One-shot UI call, not Replicator annotator loop

`capture_camera_image` is the right primitive but creates Replicator render product per call. Per-step use should cache the render product.

## Vision-gated canonicals in 33-set

| Canonical | Vision need |
|---|---|
| K-08 vision bin-pick | 3D camera (SICK PLB) + FoundationPose; overhead depth |
| M-12 wrist-cam RL | 84×84 RGB wrist cam, TiledCameraCfg, per-step obs |
| D-07 fisheye one-shot | Specific camera capture, headless |
| D-04 RealSense D435 depth | Depth stream config, sim-to-real |
| S-09 stereo (Hawk) | Dual-camera, intrinsic calibration |
| D-03 camera inventory / quality gate | `list_cameras` + `inspect_camera` |
| D-08 sun-glare | `capture_camera_image` from specific cam |
| K-07 pose estimation / bin pick | Vision + motion planning |
| Inspect-divert (#4) | RGB + classification |
| Parcel singulation (#8) | Heap RGB + label read |
| Color sort multi-class (#15, #16) | RGB + multi-class classifier |

Source: Sonnet agent `a2a2ae00b8f8d1d40` 2026-05-07.
