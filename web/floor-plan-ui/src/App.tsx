/**
 * Block 1A.3 SPA scaffold — minimal Konva canvas + status bar wired against
 * the canvas REST API.  This is the entry point next session extends with:
 *
 * - left toolbar (select/place/annotate/lock per spec §11.3.2)
 * - object palette (drag from sidebar to canvas per spec §11.3.3)
 * - properties/layers/constraints right dock (spec §11.3.4)
 * - smart guides + snap markers (spec §6.3)
 * - dimension lines + constraint indicators (spec §6.4)
 * - persistent chat input ribbon at very bottom (spec §11.2)
 *
 * The skeleton below proves wiring: it loads the LayoutSpec from the
 * backend, renders objects as plain Konva rectangles + class-tinted fills,
 * and shows a status bar with revision + connection state.
 */
import { useEffect, useMemo, useState } from "react";
import { Stage, Layer, Rect, Circle, Line, Text } from "react-konva";
import { CanvasGetResponse, LayoutSpec, TypedObject } from "./api/types";
import { createCanvasApi } from "./api/floorPlanApi";

const SESSION_ID =
    new URLSearchParams(window.location.search).get("session") ??
    "default_session";

const api = createCanvasApi("");

// ─── Visual tokens — mirror service/.../multimodal/render.py ──────────
// Same agency-tier palette so the SPA and Kit-mirror look identical.
const CANVAS_BG = "#111214";
const GRID_MAJOR = "#272E38";
const GRID_MINOR = "#1E2228";
const ORIGIN = "#76B900";
const TEXT_PRIMARY = "#DDDDDD";
const TEXT_SECONDARY = "#8A8E92";

const CLASS_COLORS: Record<string, string> = {
    franka_panda: "#5A8DEE",
    ur5e: "#4A7DCE",
    ur10e: "#4A7DCE",
    kinova_gen3: "#4A7DCE",
    iiwa: "#4A7DCE",
    jaco7: "#4A7DCE",
    nova_carter: "#3A6DAE",
    conveyor: "#FFA800",
    camera_sensor: "#00C8B4",
    lidar_sensor: "#00C8B4",
    station_marker: "#00C8B4",
    bin: "#5E6571",
    cube: "#8B7355",
    table: "#4A5560",
    ramp: "#4A5560",
    wall: "#343940",
    boundary: "#343940",
};

const REACH_RADIUS_M: Record<string, number> = {
    franka_panda: 0.855,
    ur5e: 0.85,
    ur10e: 1.3,
    kinova_gen3: 0.902,
    iiwa: 0.82,
    jaco7: 0.902,
};

const ROBOT_CLASSES = new Set(Object.keys(REACH_RADIUS_M));

// ─── App ──────────────────────────────────────────────────────────────

export function App() {
    const [spec, setSpec] = useState<LayoutSpec | null>(null);
    const [revision, setRevision] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        api.get(SESSION_ID)
            .then((res: CanvasGetResponse) => {
                setSpec(res.spec);
                setRevision(res.revision);
                setLoading(false);
            })
            .catch((e) => {
                setError(String(e));
                setLoading(false);
            });
    }, []);

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                width: "100vw",
                height: "100vh",
                background: CANVAS_BG,
            }}
        >
            <Header />
            <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
                <Toolbar />
                <CanvasArea spec={spec} loading={loading} error={error} />
                <RightDock spec={spec} />
            </div>
            <StatusBar revision={revision} loading={loading} error={error} />
        </div>
    );
}

// ─── Header ───────────────────────────────────────────────────────────

function Header() {
    return (
        <div
            style={{
                height: 40,
                background: "#1A1C1F",
                borderBottom: "1px solid #2E3237",
                display: "flex",
                alignItems: "center",
                paddingLeft: 12,
                color: TEXT_PRIMARY,
                fontSize: 13,
                fontWeight: 600,
            }}
        >
            Isaac Assist · Floor Plan ·
            <span style={{ marginLeft: 8, color: TEXT_SECONDARY, fontSize: 11 }}>
                session: {SESSION_ID}
            </span>
        </div>
    );
}

// ─── Left toolbar (placeholder) ───────────────────────────────────────

function Toolbar() {
    return (
        <div
            style={{
                width: 48,
                background: "#1A1C1F",
                borderRight: "1px solid #2E3237",
                display: "flex",
                flexDirection: "column",
                padding: "8px 0",
                gap: 8,
                color: TEXT_SECONDARY,
                fontSize: 11,
                textAlign: "center",
            }}
        >
            <div style={{ height: 32, lineHeight: "32px" }}>↖</div>
            <div style={{ height: 32, lineHeight: "32px" }}>+</div>
            <div style={{ height: 32, lineHeight: "32px" }}>↔</div>
        </div>
    );
}

// ─── Canvas (Konva) ───────────────────────────────────────────────────

const PX_PER_M = 100;

function CanvasArea({
    spec,
    loading,
    error,
}: {
    spec: LayoutSpec | null;
    loading: boolean;
    error: string | null;
}) {
    const [size, setSize] = useState({ w: 800, h: 600 });

    useEffect(() => {
        const el = document.getElementById("canvas-container");
        if (!el) return;
        const update = () => {
            setSize({ w: el.clientWidth, h: el.clientHeight });
        };
        update();
        window.addEventListener("resize", update);
        return () => window.removeEventListener("resize", update);
    }, []);

    const cx = size.w / 2;
    const cy = size.h / 2;

    const objects = spec?.objects ?? [];

    return (
        <div
            id="canvas-container"
            style={{ flex: 1, position: "relative", background: CANVAS_BG }}
        >
            {loading && <Overlay text="Loading…" />}
            {error && <Overlay text={`Error: ${error}`} color="#FF4444" />}
            {!loading && !error && objects.length === 0 && (
                <Overlay text="LayoutSpec.intent only — no objects to render. Use the Modes button in the chat panel to start." />
            )}
            <Stage width={size.w} height={size.h}>
                <Layer listening={false}>
                    <Grid w={size.w} h={size.h} cx={cx} cy={cy} />
                    <OriginCross cx={cx} cy={cy} />
                </Layer>
                <Layer>
                    {objects.map((o) => (
                        <ObjectShape
                            key={o.id}
                            obj={o}
                            cx={cx}
                            cy={cy}
                            pxPerM={PX_PER_M}
                        />
                    ))}
                </Layer>
            </Stage>
        </div>
    );
}

function Overlay({ text, color = TEXT_SECONDARY }: { text: string; color?: string }) {
    return (
        <div
            style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color,
                fontSize: 12,
                pointerEvents: "none",
                zIndex: 10,
            }}
        >
            {text}
        </div>
    );
}

function Grid({ w, h, cx, cy }: { w: number; h: number; cx: number; cy: number }) {
    const lines: JSX.Element[] = [];
    const minor = PX_PER_M * 0.25;
    const major = PX_PER_M * 1.0;

    // minor
    for (let x = cx % minor; x < w; x += minor) {
        lines.push(<Line key={`mvx${x}`} points={[x, 0, x, h]} stroke={GRID_MINOR} strokeWidth={1} />);
    }
    for (let y = cy % minor; y < h; y += minor) {
        lines.push(<Line key={`mhy${y}`} points={[0, y, w, y]} stroke={GRID_MINOR} strokeWidth={1} />);
    }
    // major (drawn over minor)
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
        </>
    );
}

function ObjectShape({
    obj,
    cx,
    cy,
    pxPerM,
}: {
    obj: TypedObject;
    cx: number;
    cy: number;
    pxPerM: number;
}) {
    const w = obj.size.w * pxPerM;
    const h = obj.size.h * pxPerM;
    const x = cx + obj.position.x * pxPerM - w / 2;
    const y = cy - obj.position.y * pxPerM - h / 2;

    const stroke = CLASS_COLORS[obj.class] ?? "#888";
    const fill = stroke + "26"; // 15% alpha

    const isRobot = ROBOT_CLASSES.has(obj.class);
    const reachRadius = isRobot ? (REACH_RADIUS_M[obj.class] ?? 0.855) * pxPerM : 0;

    return (
        <>
            {isRobot && (
                <Circle
                    x={cx + obj.position.x * pxPerM}
                    y={cy - obj.position.y * pxPerM}
                    radius={reachRadius}
                    stroke={stroke + "60"}
                    strokeWidth={1}
                />
            )}
            <Rect
                x={x}
                y={y}
                width={w}
                height={h}
                fill={fill}
                stroke={stroke}
                strokeWidth={2}
                rotation={obj.rotation}
                offsetX={w / 2 - (cx + obj.position.x * pxPerM - x)}
                offsetY={h / 2 - (cy - obj.position.y * pxPerM - y)}
            />
            <Text
                x={cx + obj.position.x * pxPerM - w / 2}
                y={cy - obj.position.y * pxPerM - h / 2 - 14}
                text={obj.name}
                fontSize={11}
                fill={TEXT_PRIMARY}
                width={w}
                align="center"
            />
        </>
    );
}

// ─── Right dock (placeholder) ─────────────────────────────────────────

function RightDock({ spec }: { spec: LayoutSpec | null }) {
    return (
        <div
            style={{
                width: 280,
                background: "#1A1C1F",
                borderLeft: "1px solid #2E3237",
                padding: 12,
                color: TEXT_PRIMARY,
                fontSize: 12,
                overflow: "auto",
            }}
        >
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Properties</div>
            {spec ? (
                <div style={{ color: TEXT_SECONDARY, lineHeight: 1.5 }}>
                    <div>pattern: {spec.intent.pattern_hint}</div>
                    <div>robots: {spec.intent.counts.robots}</div>
                    <div>conveyors: {spec.intent.counts.conveyors}</div>
                    <div>bins: {spec.intent.counts.bins}</div>
                    <div>cubes: {spec.intent.counts.cubes}</div>
                    <div style={{ marginTop: 8, fontSize: 10 }}>
                        revision: {spec.revision}
                    </div>
                </div>
            ) : (
                <div style={{ color: TEXT_SECONDARY }}>No LayoutSpec loaded</div>
            )}
        </div>
    );
}

// ─── Status bar ───────────────────────────────────────────────────────

function StatusBar({
    revision,
    loading,
    error,
}: {
    revision: number;
    loading: boolean;
    error: string | null;
}) {
    let statusColor = ORIGIN;
    let statusText = `Ready · rev ${revision}`;
    if (loading) {
        statusColor = "#FFA800";
        statusText = "Loading…";
    } else if (error) {
        statusColor = "#FF4444";
        statusText = `Error: ${error.slice(0, 60)}`;
    }
    return (
        <div
            style={{
                height: 24,
                background: "#181A1D",
                borderTop: "1px solid #2E3237",
                display: "flex",
                alignItems: "center",
                paddingLeft: 12,
                paddingRight: 12,
                color: TEXT_SECONDARY,
                fontSize: 11,
                gap: 12,
            }}
        >
            <span style={{ color: statusColor }}>● {statusText}</span>
        </div>
    );
}
