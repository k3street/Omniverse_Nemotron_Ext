/**
 * Per-class defaults for newly placed objects.  Mirrors the spec §3.1 taxonomy.
 * Used by the palette to construct TypedObject instances on drop.
 */
import { Size } from "../api/types";

export interface ClassMeta {
    label: string;
    icon: string;        // emoji placeholder; replace with custom 32×32 SVG silhouettes (spec §12.6)
    defaultSize: Size;
    isRobotArm: boolean;
    reachRadiusM?: number;
    rotationLocked?: boolean;
}

export const CLASS_META: Record<string, ClassMeta> = {
    franka_panda: {
        label: "Franka Panda",
        icon: "🦾",
        defaultSize: { w: 0.12, h: 0.12 },
        isRobotArm: true,
        reachRadiusM: 0.855,
    },
    ur5e: {
        label: "UR5e",
        icon: "🦾",
        defaultSize: { w: 0.13, h: 0.13 },
        isRobotArm: true,
        reachRadiusM: 0.85,
    },
    ur10e: {
        label: "UR10e",
        icon: "🦾",
        defaultSize: { w: 0.19, h: 0.19 },
        isRobotArm: true,
        reachRadiusM: 1.3,
    },
    ur10: {
        label: "UR10",
        icon: "🦾",
        defaultSize: { w: 0.30, h: 0.30 },
        isRobotArm: true,
        reachRadiusM: 1.3,
    },
    kinova_gen3: {
        label: "Kinova Gen3",
        icon: "🦾",
        defaultSize: { w: 0.10, h: 0.10 },
        isRobotArm: true,
        reachRadiusM: 0.902,
    },
    iiwa: {
        label: "IIWA",
        icon: "🦾",
        defaultSize: { w: 0.16, h: 0.16 },
        isRobotArm: true,
        reachRadiusM: 0.82,
    },
    jaco7: {
        label: "Jaco7",
        icon: "🦾",
        defaultSize: { w: 0.08, h: 0.08 },
        isRobotArm: true,
        reachRadiusM: 0.902,
    },
    nova_carter: {
        label: "Nova Carter",
        icon: "🛻",
        defaultSize: { w: 0.69, h: 0.96 },
        isRobotArm: false,
    },
    conveyor: {
        label: "Conveyor",
        icon: "📦",
        defaultSize: { w: 3.0, h: 0.4 },
        isRobotArm: false,
    },
    conveyor_short: {
        label: "Conveyor short",
        icon: "📦",
        defaultSize: { w: 1.5, h: 0.5 },
        isRobotArm: false,
    },
    conveyor_long: {
        label: "Conveyor long",
        icon: "📦",
        defaultSize: { w: 3.0, h: 0.5 },
        isRobotArm: false,
    },
    bin: {
        label: "Bin",
        icon: "🗑️",
        defaultSize: { w: 0.3, h: 0.3 },
        isRobotArm: false,
    },
    cube: {
        label: "Cube",
        icon: "🧊",
        defaultSize: { w: 0.05, h: 0.05 },
        isRobotArm: false,
        rotationLocked: true,
    },
    cube_small: {
        label: "Cube small",
        icon: "🧊",
        defaultSize: { w: 0.05, h: 0.05 },
        isRobotArm: false,
        rotationLocked: true,
    },
    cube_medium: {
        label: "Cube medium",
        icon: "🧊",
        defaultSize: { w: 0.05, h: 0.05 },
        isRobotArm: false,
        rotationLocked: true,
    },
    cube_large: {
        label: "Cube large",
        icon: "🧊",
        defaultSize: { w: 0.08, h: 0.08 },
        isRobotArm: false,
        rotationLocked: true,
    },
    table: {
        label: "Table",
        icon: "🪑",
        defaultSize: { w: 2.0, h: 1.0 },
        isRobotArm: false,
    },
    table_small: {
        label: "Table small",
        icon: "🪑",
        defaultSize: { w: 0.8, h: 0.6 },
        isRobotArm: false,
    },
    table_medium: {
        label: "Table medium",
        icon: "🪑",
        defaultSize: { w: 1.2, h: 0.8 },
        isRobotArm: false,
    },
    table_large: {
        label: "Table large",
        icon: "🪑",
        defaultSize: { w: 2.0, h: 1.0 },
        isRobotArm: false,
    },
    shelf: {
        label: "Shelf",
        icon: "▤",
        defaultSize: { w: 1.2, h: 0.4 },
        isRobotArm: false,
    },
    station_marker: {
        label: "Station marker",
        icon: "📍",
        defaultSize: { w: 0.06, h: 0.06 },
        isRobotArm: false,
    },
    camera_sensor: {
        label: "Camera",
        icon: "📷",
        defaultSize: { w: 0.05, h: 0.05 },
        isRobotArm: false,
    },
    camera_overhead: {
        label: "Camera overhead",
        icon: "📷",
        defaultSize: { w: 0.10, h: 0.10 },
        isRobotArm: false,
    },
    camera_side: {
        label: "Camera side",
        icon: "📷",
        defaultSize: { w: 0.10, h: 0.10 },
        isRobotArm: false,
    },
    lidar_sensor: {
        label: "Lidar",
        icon: "📡",
        defaultSize: { w: 0.10, h: 0.10 },
        isRobotArm: false,
    },
    rtx_lidar: {
        label: "RTX Lidar",
        icon: "📡",
        defaultSize: { w: 0.08, h: 0.08 },
        isRobotArm: false,
    },
    ramp: {
        label: "Ramp",
        icon: "🛝",
        defaultSize: { w: 0.4, h: 0.3 },
        isRobotArm: false,
    },
    wall: {
        label: "Wall",
        icon: "🧱",
        defaultSize: { w: 1.0, h: 0.05 },
        isRobotArm: false,
    },
    obstacle_box: {
        label: "Obstacle box",
        icon: "□",
        defaultSize: { w: 0.5, h: 0.5 },
        isRobotArm: false,
    },
    groundplane: {
        label: "Ground plane",
        icon: "▭",
        defaultSize: { w: 4.0, h: 4.0 },
        isRobotArm: false,
        rotationLocked: true,
    },
    distant_light: {
        label: "Distant light",
        icon: "☼",
        defaultSize: { w: 0.08, h: 0.08 },
        isRobotArm: false,
        rotationLocked: true,
    },
};

export const BUILD_ASSET_CLASSES = Object.keys(CLASS_META).sort((a, b) =>
    CLASS_META[a].label.localeCompare(CLASS_META[b].label),
);

export const PALETTE_CATEGORIES: Array<{ name: string; classes: string[] }> = [
    {
        name: "Robots",
        classes: ["franka_panda", "ur5e", "ur10e", "kinova_gen3", "iiwa", "jaco7", "nova_carter"],
    },
    {
        name: "Workpieces",
        classes: ["conveyor", "bin", "cube"],
    },
    {
        name: "Sensors",
        classes: ["camera_sensor", "lidar_sensor", "station_marker"],
    },
    {
        name: "Fixtures",
        classes: ["table", "ramp", "wall"],
    },
];

const NAME_COUNTERS: Record<string, number> = {};

/** Generate a USD-prim-path-safe name for a newly placed object. */
export function generateName(objectClass: string, existingNames: string[]): string {
    const meta = CLASS_META[objectClass];
    if (!meta) return `${objectClass}_1`;
    const base = meta.label.replace(/[^A-Za-z0-9]/g, "");
    let i = (NAME_COUNTERS[objectClass] ?? 0) + 1;
    while (existingNames.includes(`${base}_${i}`)) i += 1;
    NAME_COUNTERS[objectClass] = i;
    return `${base}_${i}`;
}
