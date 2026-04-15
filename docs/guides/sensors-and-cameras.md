# Sensors & Cameras

How to create cameras, attach sensors, look up real product specs, and capture images.

---

## Creating Cameras

Create camera prims and control the viewport.

> Create a camera named FrontCam at 2, 0, 1.5 looking at the origin

> Add a camera above the table looking down

> Switch the viewport to /World/FrontCam

Camera parameters you can set:

| Parameter | Example | Description |
|-----------|---------|-------------|
| Focal length | `50mm` | Lens focal length |
| Aperture | `f/2.8` | Depth of field control |
| Clipping range | `near 0.01, far 1000` | Render distance |
| Resolution | `1280x720` | Output image size |

> Create a camera with focal length 35mm and resolution 1920x1080

---

## Adding Sensors

Isaac Assist can attach various sensor types to prims in your scene.

=== "Camera Sensor"

    > Attach a camera sensor to /World/Franka/panda_hand

    > Add an RGB-D camera to the robot wrist link

=== "Lidar"

    > Add a lidar sensor to /World/Carter/base_link

    > Attach an RTX lidar on top of the robot

    RTX lidar uses ray-tracing for physically accurate simulation.

=== "IMU"

    > Add an IMU sensor to /World/Jetbot/base_link

    > Attach an inertial measurement unit to the robot base

=== "Contact Sensor"

    > Add a contact sensor to /World/Franka/panda_leftfinger

    > Attach contact sensors to both gripper fingers

| Sensor Type | Tool Name | Typical Mount Point |
|-------------|-----------|-------------------|
| Camera | `camera` | End-effector, base, overhead |
| Lidar | `lidar` | Robot top, bumper |
| RTX Lidar | `rtx_lidar` | Robot top (GPU-accelerated) |
| IMU | `imu` | Base link |
| Contact Sensor | `contact_sensor` | Gripper fingers, feet |

---

## Looking Up Product Specs

Isaac Assist can look up specifications for real sensor products and configure simulated sensors to match.

> Look up the specs for Intel RealSense D435i

> What are the specifications of a Velodyne VLP-16?

> Find specs for Ouster OS1-64

The `lookup_product_spec` tool searches a database of common robotics sensors:

| Product | Type | Key Specs |
|---------|------|-----------|
| Intel RealSense D435i | RGB-D Camera | 1280x720, 87 deg FOV, 0.1-10m range |
| Intel RealSense L515 | Lidar Camera | 1024x768, 70 deg FOV, 0.25-9m range |
| Velodyne VLP-16 | 3D Lidar | 16 channels, 100m range, 300K pts/s |
| Ouster OS1-64 | 3D Lidar | 64 channels, 120m range, 1.3M pts/s |
| Hokuyo UST-10LX | 2D Lidar | 270 deg FOV, 10m range |

---

## Full Workflow: Spec Lookup to Sensor Attachment

A practical workflow for adding a realistic sensor to your robot:

**Step 1:** Look up the real sensor specs.

> Look up the specs for Intel RealSense D435i

Isaac Assist returns the FOV, resolution, range, and frame rate.

**Step 2:** Attach the sensor with those specs.

> Attach a RealSense D435i camera to /World/Franka/panda_hand

Isaac Assist creates a camera sensor matching the D435i specifications (resolution, FOV, clipping range) and mounts it at the specified link.

**Step 3:** Verify by switching the viewport.

> Switch viewport to the wrist camera

You see the scene from the robot's camera perspective.

!!! tip "One-step shortcut"
    You can combine lookup and attachment in a single request:
    > Attach a RealSense D435i to the Franka wrist

---

## Capturing Images

Take a screenshot from any camera in the scene.

> Capture a screenshot from the viewport

> Save an image from /World/FrontCam

The `capture_viewport` tool renders the current viewport and saves it to disk. Useful for:

- Documenting your scene setup
- Generating training data
- Visual verification of sensor placement

> Capture the viewport and save to /tmp/scene_capture.png

---

## Multiple Sensors

Mount several sensors at different links:

> Attach an RGB camera to /World/Franka/panda_hand

> Add a lidar to /World/Franka/panda_link0

> Attach contact sensors to both gripper fingers

!!! note "Performance"
    Each active sensor adds computational overhead. RTX lidar is especially GPU-intensive. Disable sensors you are not actively using.

---

## What's Next?

- [Importing Robots](importing-robots.md) -- Bring in robots to attach sensors to
- [ROS2 Integration](ros2.md) -- Stream sensor data over ROS2 topics
- [Debugging & Diagnostics](debugging.md) -- Diagnose sensor issues
