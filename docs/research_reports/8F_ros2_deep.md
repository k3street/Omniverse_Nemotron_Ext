# Phase 8F — ROS2 Deep Integration: Critique

**Agent:** Research 8F ROS2 Deep  
**Date:** 2026-04-15  
**Status:** Complete

## 8F.2 — Hard Blocker

`isaacsim.ros2.urdf` is a **URDF importer from ROS2**, not an exporter. The spec has the direction completely backwards. Correct approach: `isaacsim.asset.exporter.urdf` (GUI-only) + manual `rclpy` publisher.

## 8F.1 — TF Viewer

Works but requires active `ROS2PublishTransformTree` OmniGraph node. No built-in "print tree" formatter — must parse `get_transforms()` output manually.

## 8F.3 — Bridge Configuration

- `ROS2Context` node mandatory but not mentioned in spec
- Services require 3 OmniGraph nodes each
- Action servers/clients NOT supported as OmniGraph nodes
- Lifecycle nodes not supported

## 8F.4 — DR + ROS2

Cannot be "wired together" — different execution contexts. Requires standalone workflow coordinator.

## ROS2 Versions

Humble + Jazzy supported. Kilted experimental. `tf_viewer.initialize()` needs dynamic distro detection.

## Sources
- [isaacsim.ros2.tf_viewer](https://docs.isaacsim.omniverse.nvidia.com/latest/py/source/extensions/isaacsim.ros2.tf_viewer/docs/index.html)
- [isaacsim.ros2.urdf](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/py/source/extensions/isaacsim.ros2.urdf/docs/index.html)
- [USD to URDF Export](https://docs.isaacsim.omniverse.nvidia.com/4.5.0/robot_setup/export_urdf.html)
