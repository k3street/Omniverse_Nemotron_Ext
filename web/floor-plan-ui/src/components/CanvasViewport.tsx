/**
 * Konva canvas viewport — interactive editing surface.
 *
 * Spec §6.1-§6.3:
 * - LMB drag: pan / select / move object
 * - Click / shift-click: select / extend selection
 * - Konva Transformer: 8-handle resize + rotation handle on selected nodes
 * - Drop: accepts dataTransfer "application/x-isaac-palette" → AddObject
 * - Snap: compositeSnap (object > polar > grid) with 8-px screen threshold
 * - Smart guide overlay drawn from snap-result guideLines
 *
 * Coordinate frame:
 * - Konva stage is screen-space.  World-space is centered on canvas with
 *   +x = right, +y = up, scale PX_PER_M.  Conversion happens at the edge
 *   of every event handler — store always holds world-space.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
    Stage,
    Layer,
    Rect,
    Circle,
    Line,
    Text,
    Transformer,
    Group,
} from "react-konva";
import Konva from "konva";
import { TypedObject, Position } from "../api/types";
import { useFloorPlanStore } from "../store/floorPlanStore";
import { CLASS_META } from "../canvas/objectClasses";
import { compositeSnap, GuideLine } from "../canvas/snap";
import { generateName } from "../canvas/objectClasses";
import { PaletteDragPayload } from "./Palette";

const CANVAS_BG = "#111214";
const GRID_MAJOR = "#272E38";
const GRID_MINOR = "#1E2228";
const ORIGIN = "#76B900";
const TEXT_PRIMARY = "#DDDDDD";
const TEXT_SECONDARY = "#8A8E92";
const SELECT_BLUE = "#5A8DEE";
const GUIDE_COLOR = "#FFB800";

const CLASS_COLORS: Record<string, string> = {
    franka_panda: "#5A8DEE",
    ur5e: "#4A7DCE",
    ur10e: "#4A7DCE",
    kinova_gen3: "#4A7DCE",
    iiwa: "#4A7DCE",
    jaco7: "#4A7DCE",
    nova_carter: "#3A6DAE",
    ur10: "#4A7DCE",
    conveyor: "#FFA800",
    conveyor_short: "#FFA800",
    conveyor_long: "#FFA800",
    camera_sensor: "#00C8B4",
    camera_overhead: "#00C8B4",
    camera_side: "#00C8B4",
    lidar_sensor: "#00C8B4",
    rtx_lidar: "#00C8B4",
    station_marker: "#00C8B4",
    bin: "#5E6571",
    cube: "#8B7355",
    cube_small: "#8B7355",
    cube_medium: "#8B7355",
    cube_large: "#8B7355",
    table: "#4A5560",
    table_small: "#4A5560",
    table_medium: "#4A5560",
    table_large: "#4A5560",
    shelf: "#4A5560",
    ramp: "#4A5560",
    wall: "#343940",
    obstacle_box: "#343940",
    groundplane: "#343940",
    distant_light: "#FFE08A",
};

const PX_PER_M = 100;

// ─── Coord helpers ───────────────────────────────────────────────────

function worldToScreen(p: Position, cx: number, cy: number): { x: number; y: number } {
    return { x: cx + p.x * PX_PER_M, y: cy - p.y * PX_PER_M };
}

function screenToWorld(p: { x: number; y: number }, cx: number, cy: number): Position {
    return { x: (p.x - cx) / PX_PER_M, y: (cy - p.y) / PX_PER_M };
}

// ─── Component ───────────────────────────────────────────────────────

export function CanvasViewport() {
    const containerRef = useRef<HTMLDivElement>(null);
    const stageRef = useRef<Konva.Stage>(null);
    const transformerRef = useRef<Konva.Transformer>(null);
    const [size, setSize] = useState({ w: 800, h: 600 });
    const [guides, setGuides] = useState<GuideLine[]>([]);
    const [marquee, setMarquee] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);

    const objects = useFloorPlanStore((s) => s.spec?.objects ?? []);
    const selectedIds = useFloorPlanStore((s) => s.selectedIds);
    const setSelection = useFloorPlanStore((s) => s.setSelection);
    const clearSelection = useFloorPlanStore((s) => s.clearSelection);
    const moveObject = useFloorPlanStore((s) => s.moveObject);
    const resizeObject = useFloorPlanStore((s) => s.resizeObject);
    const rotateObject = useFloorPlanStore((s) => s.rotateObject);
    const addObject = useFloorPlanStore((s) => s.addObject);
    const deleteObjects = useFloorPlanStore((s) => s.deleteObjects);
    const undo = useFloorPlanStore((s) => s.undo);
    const redo = useFloorPlanStore((s) => s.redo);

    // ─── Resize observer ─────────────────────────────────────────────
    useEffect(() => {
        const el = containerRef.current;
        if (!el) return;
        const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
        update();
        const ro = new ResizeObserver(update);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const cx = size.w / 2;
    const cy = size.h / 2;

    // ─── Transformer attach ──────────────────────────────────────────
    useEffect(() => {
        const tr = transformerRef.current;
        const stage = stageRef.current;
        if (!tr || !stage) return;
        const nodes: Konva.Node[] = [];
        for (const id of selectedIds) {
            const node = stage.findOne(`#obj-${id}`);
            if (node) nodes.push(node);
        }
        tr.nodes(nodes);
        tr.getLayer()?.batchDraw();
    }, [selectedIds, objects]);

    // ─── Keyboard shortcuts ──────────────────────────────────────────
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
            if (tag === "input" || tag === "textarea") return;
            if ((e.metaKey || e.ctrlKey) && e.key === "z" && !e.shiftKey) {
                e.preventDefault(); undo(); return;
            }
            if ((e.metaKey || e.ctrlKey) && (e.key === "y" || (e.key === "z" && e.shiftKey))) {
                e.preventDefault(); redo(); return;
            }
            if ((e.key === "Delete" || e.key === "Backspace") && selectedIds.length > 0) {
                e.preventDefault(); deleteObjects(selectedIds); return;
            }
            if (e.key === "Escape") { clearSelection(); return; }
            if ((e.metaKey || e.ctrlKey) && e.key === "a") {
                e.preventDefault();
                setSelection(objects.map((o) => o.id));
                return;
            }
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, [undo, redo, deleteObjects, clearSelection, setSelection, selectedIds, objects]);

    // ─── HTML5 drop from palette ─────────────────────────────────────
    const onDragOver = (e: React.DragEvent<HTMLDivElement>) => {
        if (e.dataTransfer.types.includes("application/x-isaac-palette")) {
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
        }
    };
    const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        const data = e.dataTransfer.getData("application/x-isaac-palette");
        if (!data) return;
        const payload = JSON.parse(data) as PaletteDragPayload;
        const meta = CLASS_META[payload.objectClass];
        if (!meta) return;
        const rect = containerRef.current?.getBoundingClientRect();
        if (!rect) return;
        const screenPos = { x: e.clientX - rect.left, y: e.clientY - rect.top };
        const world = screenToWorld(screenPos, cx, cy);

        // Snap drop position
        const snap = compositeSnap(world, "__new__", objects, PX_PER_M);
        const finalPos = snap.snapped;

        const existingNames = objects.map((o) => o.name);
        const id = `obj_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
        const newObj: TypedObject = {
            id,
            class: payload.objectClass,
            name: generateName(payload.objectClass, existingNames),
            position: { x: round(finalPos.x), y: round(finalPos.y) },
            rotation: 0,
            size: { ...meta.defaultSize },
            color: undefined,
            notes: "",
            notes_sensitive: false,
            metadata: {},
            locked: false,
            layer: "default",
        };
        addObject(newObj);
        setSelection([id]);
    };

    // ─── Drag move handler — applies snap ────────────────────────────
    const handleDragMove = (
        e: Konva.KonvaEventObject<DragEvent>,
        obj: TypedObject,
    ) => {
        const node = e.target;
        const screenPos = { x: node.x(), y: node.y() };
        const world = screenToWorld(screenPos, cx, cy);
        const snap = compositeSnap(world, obj.id, objects, PX_PER_M);
        const screenSnapped = worldToScreen(snap.snapped, cx, cy);
        node.x(screenSnapped.x);
        node.y(screenSnapped.y);
        setGuides(snap.guideLines);
    };

    const handleDragEnd = (
        e: Konva.KonvaEventObject<DragEvent>,
        obj: TypedObject,
    ) => {
        const node = e.target;
        const world = screenToWorld({ x: node.x(), y: node.y() }, cx, cy);
        const dest = { x: round(world.x), y: round(world.y) };
        if (dest.x !== obj.position.x || dest.y !== obj.position.y) {
            moveObject(obj.id, obj.position, dest);
        }
        setGuides([]);
    };

    // ─── Transformer transform handler ───────────────────────────────
    const handleTransformEnd = (
        e: Konva.KonvaEventObject<Event>,
        obj: TypedObject,
    ) => {
        const node = e.target as Konva.Node;
        const scaleX = node.scaleX();
        const scaleY = node.scaleY();
        const newW = Math.max(0.01, obj.size.w * scaleX);
        const newH = Math.max(0.01, obj.size.h * scaleY);
        const newRot = ((node.rotation() % 360) + 360) % 360;
        node.scaleX(1);
        node.scaleY(1);
        if (newW !== obj.size.w || newH !== obj.size.h) {
            resizeObject(obj.id, obj.size, { w: round(newW), h: round(newH) });
        }
        if (newRot !== obj.rotation) {
            rotateObject(obj.id, obj.rotation, round(newRot, 1));
        }
        // Reposition: Transformer may shift center if user drags from a corner
        const world = screenToWorld({ x: node.x(), y: node.y() }, cx, cy);
        const dest = { x: round(world.x), y: round(world.y) };
        if (dest.x !== obj.position.x || dest.y !== obj.position.y) {
            moveObject(obj.id, obj.position, dest);
        }
    };

    // ─── Stage click — empty area clears selection / starts marquee ──
    const onStageMouseDown = (e: Konva.KonvaEventObject<MouseEvent>) => {
        if (e.target === e.target.getStage()) {
            clearSelection();
            const stage = e.target.getStage()!;
            const pos = stage.getPointerPosition();
            if (pos) setMarquee({ x1: pos.x, y1: pos.y, x2: pos.x, y2: pos.y });
        }
    };
    const onStageMouseMove = (e: Konva.KonvaEventObject<MouseEvent>) => {
        if (!marquee) return;
        const stage = e.target.getStage()!;
        const pos = stage.getPointerPosition();
        if (pos) setMarquee({ ...marquee, x2: pos.x, y2: pos.y });
    };
    const onStageMouseUp = () => {
        if (marquee) {
            const minX = Math.min(marquee.x1, marquee.x2);
            const maxX = Math.max(marquee.x1, marquee.x2);
            const minY = Math.min(marquee.y1, marquee.y2);
            const maxY = Math.max(marquee.y1, marquee.y2);
            if (Math.abs(maxX - minX) > 4 && Math.abs(maxY - minY) > 4) {
                const hits: string[] = [];
                for (const o of objects) {
                    const s = worldToScreen(o.position, cx, cy);
                    if (s.x >= minX && s.x <= maxX && s.y >= minY && s.y <= maxY) {
                        hits.push(o.id);
                    }
                }
                if (hits.length > 0) setSelection(hits);
            }
            setMarquee(null);
        }
    };

    // ─── Object click — select / extend ──────────────────────────────
    const onObjectClick = (e: Konva.KonvaEventObject<MouseEvent>, id: string) => {
        e.cancelBubble = true;
        if (e.evt.shiftKey) {
            const exists = selectedIds.includes(id);
            setSelection(exists ? selectedIds.filter((s) => s !== id) : [...selectedIds, id]);
        } else {
            setSelection([id]);
        }
    };

    return (
        <div
            ref={containerRef}
            onDragOver={onDragOver}
            onDrop={onDrop}
            style={{ flex: 1, position: "relative", background: CANVAS_BG, minWidth: 0 }}
            data-testid="canvas-viewport"
        >
            <Stage
                width={size.w}
                height={size.h}
                ref={stageRef}
                onMouseDown={onStageMouseDown}
                onMouseMove={onStageMouseMove}
                onMouseUp={onStageMouseUp}
            >
                <Layer listening={false}>
                    <Grid w={size.w} h={size.h} cx={cx} cy={cy} />
                    <OriginCross cx={cx} cy={cy} />
                </Layer>
                <Layer>
                    {objects.map((o) => (
                        <ObjectGroup
                            key={o.id}
                            obj={o}
                            cx={cx}
                            cy={cy}
                            selected={selectedIds.includes(o.id)}
                            onClick={(e) => onObjectClick(e, o.id)}
                            onDragMove={(e) => handleDragMove(e, o)}
                            onDragEnd={(e) => handleDragEnd(e, o)}
                            onTransformEnd={(e) => handleTransformEnd(e, o)}
                        />
                    ))}
                    <Transformer
                        ref={transformerRef}
                        rotateEnabled={true}
                        rotateAnchorOffset={20}
                        anchorSize={8}
                        anchorStroke={SELECT_BLUE}
                        anchorFill="#0F1216"
                        borderStroke={SELECT_BLUE}
                        borderDash={[4, 3]}
                        boundBoxFunc={(oldBox, newBox) => {
                            // Reject collapses below 5 px screen to avoid degenerate sizes
                            if (newBox.width < 5 || newBox.height < 5) return oldBox;
                            return newBox;
                        }}
                    />
                </Layer>
                <Layer listening={false}>
                    <GuideLines guides={guides} cx={cx} cy={cy} />
                </Layer>
                <Layer listening={false}>
                    {marquee && (
                        <Rect
                            x={Math.min(marquee.x1, marquee.x2)}
                            y={Math.min(marquee.y1, marquee.y2)}
                            width={Math.abs(marquee.x2 - marquee.x1)}
                            height={Math.abs(marquee.y2 - marquee.y1)}
                            fill="#5A8DEE22"
                            stroke={SELECT_BLUE}
                            strokeWidth={1}
                            dash={[4, 3]}
                        />
                    )}
                </Layer>
            </Stage>
            {objects.length === 0 && (
                <div
                    style={{
                        position: "absolute",
                        inset: 0,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: TEXT_SECONDARY,
                        fontSize: 12,
                        pointerEvents: "none",
                    }}
                >
                    Drag a class from the palette, or describe the layout in chat.
                </div>
            )}
        </div>
    );
}

// ─── Sub-components ──────────────────────────────────────────────────

function ObjectGroup({
    obj,
    cx,
    cy,
    selected,
    onClick,
    onDragMove,
    onDragEnd,
    onTransformEnd,
}: {
    obj: TypedObject;
    cx: number;
    cy: number;
    selected: boolean;
    onClick: (e: Konva.KonvaEventObject<MouseEvent>) => void;
    onDragMove: (e: Konva.KonvaEventObject<DragEvent>) => void;
    onDragEnd: (e: Konva.KonvaEventObject<DragEvent>) => void;
    onTransformEnd: (e: Konva.KonvaEventObject<Event>) => void;
}) {
    const stroke = obj.color ?? CLASS_COLORS[obj.class] ?? "#888";
    const fill = stroke + "26";
    const w = obj.size.w * PX_PER_M;
    const h = obj.size.h * PX_PER_M;
    const screen = worldToScreen(obj.position, cx, cy);
    const isRobot = ["franka_panda", "ur5e", "ur10e", "kinova_gen3", "iiwa", "jaco7"].includes(obj.class);
    const reachM = CLASS_META[obj.class]?.reachRadiusM;

    return (
        <Group
            id={`obj-${obj.id}`}
            x={screen.x}
            y={screen.y}
            rotation={obj.rotation}
            draggable={!obj.locked}
            onClick={onClick}
            onTap={onClick}
            onDragMove={onDragMove}
            onDragEnd={onDragEnd}
            onTransformEnd={onTransformEnd}
        >
            {isRobot && reachM && (
                <Circle
                    x={0}
                    y={0}
                    radius={reachM * PX_PER_M}
                    stroke={stroke + "55"}
                    strokeWidth={1}
                    dash={[3, 3]}
                    listening={false}
                />
            )}
            <Rect
                x={-w / 2}
                y={-h / 2}
                width={w}
                height={h}
                fill={fill}
                stroke={selected ? SELECT_BLUE : stroke}
                strokeWidth={selected ? 2.5 : 1.5}
            />
            {obj.locked && (
                <Text
                    x={-w / 2 + 4}
                    y={-h / 2 + 4}
                    text="🔒"
                    fontSize={11}
                    listening={false}
                />
            )}
            <Text
                x={-w / 2}
                y={h / 2 + 4}
                width={w}
                text={obj.name}
                fontSize={11}
                fill={TEXT_PRIMARY}
                align="center"
                listening={false}
            />
        </Group>
    );
}

function Grid({ w, h, cx, cy }: { w: number; h: number; cx: number; cy: number }) {
    const minor = PX_PER_M * 0.25;
    const major = PX_PER_M * 1.0;
    const lines: JSX.Element[] = [];
    for (let x = cx % minor; x < w; x += minor) {
        lines.push(<Line key={`mvx${x}`} points={[x, 0, x, h]} stroke={GRID_MINOR} strokeWidth={1} />);
    }
    for (let y = cy % minor; y < h; y += minor) {
        lines.push(<Line key={`mhy${y}`} points={[0, y, w, y]} stroke={GRID_MINOR} strokeWidth={1} />);
    }
    for (let x = cx % major; x < w; x += major) {
        lines.push(<Line key={`Mvx${x}`} points={[x, 0, x, h]} stroke={GRID_MAJOR} strokeWidth={1} />);
    }
    for (let y = cy % major; y < h; y += major) {
        lines.push(<Line key={`Mhy${y}`} points={[0, y, w, y]} stroke={GRID_MAJOR} strokeWidth={1} />);
    }
    return <>{lines}</>;
}

function OriginCross({ cx, cy }: { cx: number; cy: number }) {
    const arm = 24;
    return (
        <>
            <Line points={[cx - arm, cy, cx + arm, cy]} stroke={ORIGIN} strokeWidth={1} />
            <Line points={[cx, cy - arm, cx, cy + arm]} stroke={ORIGIN} strokeWidth={1} />
            <Circle x={cx} y={cy} radius={3} stroke={ORIGIN} strokeWidth={1} fill={ORIGIN} />
        </>
    );
}

function GuideLines({ guides, cx, cy }: { guides: GuideLine[]; cx: number; cy: number }) {
    return (
        <>
            {guides.map((g, i) => {
                if (g.axis === "x") {
                    const sx = cx + g.coord * PX_PER_M;
                    const sy1 = cy - (g.toY ?? 5) * PX_PER_M;
                    const sy2 = cy - (g.fromY ?? -5) * PX_PER_M;
                    return (
                        <Line
                            key={i}
                            points={[sx, sy1, sx, sy2]}
                            stroke={GUIDE_COLOR}
                            strokeWidth={1}
                            dash={[3, 3]}
                        />
                    );
                }
                const sy = cy - g.coord * PX_PER_M;
                const sx1 = cx + (g.fromX ?? -5) * PX_PER_M;
                const sx2 = cx + (g.toX ?? 5) * PX_PER_M;
                return (
                    <Line
                        key={i}
                        points={[sx1, sy, sx2, sy]}
                        stroke={GUIDE_COLOR}
                        strokeWidth={1}
                        dash={[3, 3]}
                    />
                );
            })}
        </>
    );
}

// ─── Helpers ─────────────────────────────────────────────────────────

function round(v: number, decimals: number = 3): number {
    const m = Math.pow(10, decimals);
    return Math.round(v * m) / m;
}
