# Synthetic Data Generation Workflow

This workflow uses Omniverse Replicator through Isaac Assist to generate labeled training data for computer vision models. You will build a scene, configure annotators, set up cameras, and run a data generation pipeline -- all through chat.

---

## Step 1: Build the Scene

Start with a tabletop scene containing objects you want to detect:

```
Load the tabletop manipulation scene template
```

Or build from scratch:

```
Create a ground plane
```

```
Create a cube at 0, 0, 0.4 with scale 0.8, 0.8, 0.02 and name it Table
Apply collision and static physics to /World/Table
```

```
Create a cube at 0.2, 0.1, 0.45 with scale 0.05, 0.05, 0.05 and name it RedCube
Create a sphere at -0.1, 0.15, 0.43 with scale 0.03, 0.03, 0.03 and name it GreenBall
Create a cylinder at 0.0, -0.1, 0.44 with scale 0.02, 0.02, 0.04 and name it BlueCan
```

## Step 2: Add Materials for Visual Variety

Materials provide the visual features that your model will learn:

```
Create a red material and apply it to /World/RedCube
Create a green material and apply it to /World/GreenBall
Create a blue metallic material and apply it to /World/BlueCan
```

## Step 3: Apply Semantic Labels

Semantic labels map prims to class names in the training output:

```
Set the semantic label of /World/RedCube to "cube"
Set the semantic label of /World/GreenBall to "ball"
Set the semantic label of /World/BlueCan to "can"
```

!!! tip "Semantic labels are required"
    Bounding box and segmentation annotators use semantic labels to identify objects. Without labels, objects appear as "unlabeled" in the output.

## Step 4: Set Up a Camera

Position a camera with a good view of the objects:

```
Create a camera at 0.5, 0.5, 0.8 with rotation -35, 0, 135 and name it TrainingCamera
```

```
Switch the viewport to /World/TrainingCamera
```

Verify the view captures all objects.

## Step 5: Add Lighting

Good lighting reduces artifacts in synthetic data:

```
Create a dome light with intensity 500 and name it EnvironmentLight
```

```
Create a distant light with rotation -45, 30, 0 and name it KeyLight
```

## Step 6: Configure and Run SDG

```
Generate 100 frames of synthetic data with bounding boxes, semantic segmentation, and RGB annotators at 1280x720 resolution, output to /tmp/sdg_output
```

Isaac Assist calls `configure_sdg` with:

- **Annotators**: `rgb`, `bounding_box_2d`, `semantic_segmentation`
- **Frames**: 100
- **Resolution**: 1280x720
- **Output**: `/tmp/sdg_output`

---

## Available Annotators

| Annotator | Output | Use Case |
|-----------|--------|----------|
| `rgb` | PNG images | Base training images |
| `bounding_box_2d` | JSON with `[x, y, width, height]` per object | Object detection (YOLO, SSD, Faster R-CNN) |
| `semantic_segmentation` | PNG mask where each pixel = class ID | Semantic segmentation models |
| `instance_segmentation` | PNG mask where each pixel = instance ID | Instance segmentation (Mask R-CNN) |
| `distance_to_camera` | Float32 array | Depth estimation models |
| `normals` | RGB-encoded surface normals | Surface reconstruction, relighting |

## Step 7: Verify Output

After generation completes, check the output directory:

```
Run a script to list files in /tmp/sdg_output
```

The output structure looks like:

```
/tmp/sdg_output/
  rgb/
    0000.png, 0001.png, ...
  bounding_box_2d/
    0000.json, 0001.json, ...
  semantic_segmentation/
    0000.png, 0001.png, ...
```

## Step 8: Review a Sample

```
Capture a viewport screenshot
```

Visually confirm the camera angle and object placement before running large batches.

---

## Advanced: Domain Randomization

For more robust training data, randomize scene elements between frames:

```
Generate 500 frames with domain randomization: randomize object positions on the table, 
vary lighting intensity between 200 and 800, and rotate objects randomly
```

!!! note "Domain randomization scope"
    The current Replicator integration supports basic randomization through the `configure_sdg` tool. Advanced randomization (custom DR OmniGraph nodes, texture swaps) requires `run_usd_script` with custom Replicator Python code.

---

## Output Formats

The default writer is Replicator's `BasicWriter`, which outputs:

| Format | Description |
|--------|-------------|
| PNG | RGB images and segmentation masks |
| JSON | Bounding box annotations with class labels, coordinates, and visibility |
| NPY | Depth and normal maps as NumPy arrays |

### Converting to COCO Format

Replicator's JSON output can be converted to COCO format for training with standard frameworks:

```
Run a script to convert /tmp/sdg_output/bounding_box_2d to COCO JSON format
```

### Converting to KITTI Format

```
Run a script to convert the output to KITTI format
```

---

## Tips for Quality Data

!!! tip "Camera placement"
    Place cameras at angles similar to your real deployment. A top-down camera in simulation but a 45-degree camera in production will reduce model accuracy.

!!! tip "Start small"
    Generate 10 frames first and verify the annotations are correct before running thousands. Bad labels waste GPU time.

!!! tip "Scene variety"
    Create multiple scenes with different table positions, lighting conditions, and object arrangements. Train on all of them for better generalization.

!!! warning "VRAM usage"
    High-resolution SDG (1920x1080+) consumes significant GPU memory. Start with 640x480 for testing. If you hit CUDA OOM, reduce resolution or close other GPU applications.
