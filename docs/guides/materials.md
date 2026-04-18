# Materials & Appearance

How to create materials, assign them to objects, and use common presets.

---

## Creating Materials

Isaac Assist supports the standard OmniPBR, OmniGlass, and OmniSurface material types.

=== "OmniPBR"

    The default physically-based material. Good for metals, plastics, wood, and most surfaces.

    > Create an OmniPBR material named BrushedSteel

    > Make a shiny red plastic material

=== "OmniGlass"

    Transparent and translucent materials with refraction.

    > Create an OmniGlass material with blue tint

    > Make a frosted glass material with roughness 0.3

=== "OmniSurface"

    Advanced material with subsurface scattering, thin film, and coat layers.

    > Create an OmniSurface material for skin

    > Make a wax-like material with subsurface scattering

---

## Assigning Materials to Objects

Apply a material to any prim in the scene.

> Apply the BrushedSteel material to /World/Cube

> Make /World/Sphere look like glass

> Assign the wood material to all objects under /World/Table

!!! tip "Direct assignment"
    You don't have to create the material first. Just describe the look you want and Isaac Assist creates and assigns it in one step:
    > Make /World/Box look like brushed steel

---

## Material Parameters

Fine-tune appearance by setting specific parameters.

### OmniPBR Parameters

| Parameter | Range | Description |
|-----------|-------|-------------|
| `albedo_color` | RGB (0-1) | Base color |
| `metallic` | 0.0 - 1.0 | 0 = dielectric, 1 = metal |
| `roughness` | 0.0 - 1.0 | 0 = mirror, 1 = matte |
| `opacity` | 0.0 - 1.0 | Transparency |
| `emissive_color` | RGB (0-1) | Glow color |
| `emissive_intensity` | 0+ | Glow brightness |

> Create a material with metallic 0.9, roughness 0.2, and albedo color 0.8, 0.8, 0.85

> Set the roughness of /World/Looks/BrushedSteel to 0.15

### OmniGlass Parameters

| Parameter | Range | Description |
|-----------|-------|-------------|
| `glass_color` | RGB (0-1) | Tint color |
| `glass_ior` | 1.0 - 3.0 | Index of refraction (glass = 1.5) |
| `roughness` | 0.0 - 1.0 | Surface roughness |
| `thin_walled` | bool | Thin glass mode (windows) |

> Create glass with IOR 1.5 and a green tint

---

## Common Material Presets

Describe a real-world material and Isaac Assist picks appropriate parameters.

| Preset | Chat Message | Key Settings |
|--------|-------------|-------------|
| Brushed steel | `Make this look like brushed steel` | metallic=0.95, roughness=0.25, albedo grey |
| Polished chrome | `Make this chrome` | metallic=1.0, roughness=0.05 |
| Wood | `Apply a wood material` | metallic=0.0, roughness=0.6, brown albedo |
| Rubber | `Make this rubber` | metallic=0.0, roughness=0.9, dark albedo |
| Concrete | `Make this look like concrete` | metallic=0.0, roughness=0.85, grey albedo |
| Glass | `Make this transparent glass` | OmniGlass, IOR=1.5, roughness=0.0 |
| Frosted glass | `Make this frosted glass` | OmniGlass, roughness=0.3 |
| Gold | `Make this gold` | metallic=1.0, roughness=0.2, albedo (1,0.76,0.33) |
| Matte plastic | `Make this matte red plastic` | metallic=0.0, roughness=0.8 |

> Make /World/Cube look like polished gold

> Apply a rubber material to all wheels

> Make the table surface look like oak wood

---

## Modifying Existing Materials

> Make /World/Looks/Steel more rough

> Change the color of /World/Looks/Plastic to blue

> Set opacity of /World/Looks/Glass to 0.5

---

## Batch Material Operations

Apply materials across multiple objects at once.

> Make all cubes in the scene look like steel

> Apply the wood material to everything under /World/Furniture

> Set roughness to 0.5 on all materials in the scene

!!! note "Material sharing"
    When you apply the same material description to multiple objects, Isaac Assist creates one material and binds it to all targets. This is more efficient than creating separate materials for each object.

---

## Practical Example: Styled Scene

> Create a cube named Table at 0, 0, 0.4 with scale 1.2, 0.8, 0.02

> Make the Table look like dark walnut wood

> Create a sphere named MetalBall at 0, 0, 0.5 with radius 0.05

> Make MetalBall chrome

> Create a cube named GlassBox at 0.3, 0, 0.45 with scale 0.1

> Make GlassBox transparent blue glass

!!! warning "Material visibility"
    Materials only render correctly in **RTX Real-Time** or **RTX Path-Traced** mode. Check the viewport renderer in **Render Settings** if materials look flat.

---

## What's Next?

- [Creating Objects](creating-objects.md) -- Create the objects to apply materials to
- [Scene Building](scene-building.md) -- Build complete styled environments
- [Sensors & Cameras](sensors-and-cameras.md) -- Capture rendered images
