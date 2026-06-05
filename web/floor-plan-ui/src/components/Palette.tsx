/**
 * Object palette — drag-drop modality producer (spec §11.3.3).
 *
 * Categorized grid with silhouette icons.  HTML5 dragstart sets a JSON
 * payload (objectClass + defaults) on dataTransfer; the canvas listens
 * for drop and constructs a TypedObject via the AddObject command.
 *
 * Searchable: typing into the search box filters items by label.
 */
import { useMemo, useState } from "react";
import { CLASS_META, PALETTE_CATEGORIES } from "../canvas/objectClasses";
import { SilhouetteFor } from "../canvas/silhouettes";
import { transition } from "../canvas/motionTokens";

const PALETTE_BG = "#171A1E";
const ITEM_BG = "#1F242A";
const ITEM_BG_HOVER = "#2A3038";
const TEXT = "#DDDDDD";
const TEXT_DIM = "#8A8E92";
const BORDER = "#2E3237";

const TIER_COLORS: Record<string, string> = {
    franka_panda: "#5A8DEE",
    ur5e: "#4A7DCE",
    ur10e: "#4A7DCE",
    kinova_gen3: "#4A7DCE",
    iiwa: "#4A7DCE",
    jaco7: "#4A7DCE",
    nova_carter: "#3A6DAE",
    conveyor: "#FFA800",
    bin: "#5E6571",
    cube: "#8B7355",
    table: "#4A5560",
    ramp: "#4A5560",
    wall: "#343940",
    station_marker: "#00C8B4",
    camera_sensor: "#00C8B4",
    lidar_sensor: "#00C8B4",
};

export interface PaletteDragPayload {
    kind: "palette_item";
    objectClass: string;
}

export function Palette() {
    const [search, setSearch] = useState("");
    const filtered = useMemo(() => {
        const s = search.trim().toLowerCase();
        if (!s) return PALETTE_CATEGORIES;
        return PALETTE_CATEGORIES.map((cat) => ({
            ...cat,
            classes: cat.classes.filter((c) =>
                CLASS_META[c]?.label.toLowerCase().includes(s) ||
                c.includes(s),
            ),
        })).filter((cat) => cat.classes.length > 0);
    }, [search]);

    return (
        <div
            style={{
                width: 200,
                background: PALETTE_BG,
                borderRight: `1px solid ${BORDER}`,
                display: "flex",
                flexDirection: "column",
                color: TEXT,
                fontSize: 12,
                overflow: "hidden",
            }}
            data-testid="palette"
        >
            <div
                style={{
                    padding: "10px 12px 8px",
                    borderBottom: `1px solid ${BORDER}`,
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                }}
            >
                <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: 0.5, color: TEXT_DIM }}>
                    PALETTE
                </div>
                <input
                    type="text"
                    placeholder="Search…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    style={{
                        background: "#0F1216",
                        border: `1px solid ${BORDER}`,
                        borderRadius: 4,
                        color: TEXT,
                        fontSize: 12,
                        padding: "5px 8px",
                        outline: "none",
                    }}
                />
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: "8px 6px" }}>
                {filtered.map((cat) => (
                    <div key={cat.name} style={{ marginBottom: 12 }}>
                        <div
                            style={{
                                fontSize: 10,
                                color: TEXT_DIM,
                                fontWeight: 600,
                                letterSpacing: 0.5,
                                padding: "4px 8px",
                            }}
                        >
                            {cat.name.toUpperCase()}
                        </div>
                        <div
                            style={{
                                display: "grid",
                                gridTemplateColumns: "1fr 1fr",
                                gap: 4,
                                padding: "0 4px",
                            }}
                        >
                            {cat.classes.map((c) => (
                                <PaletteItem key={c} objectClass={c} />
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

function PaletteItem({ objectClass }: { objectClass: string }) {
    const meta = CLASS_META[objectClass];
    const tint = TIER_COLORS[objectClass] ?? "#888";
    const [hover, setHover] = useState(false);

    if (!meta) return null;

    const onDragStart = (e: React.DragEvent<HTMLDivElement>) => {
        const payload: PaletteDragPayload = { kind: "palette_item", objectClass };
        e.dataTransfer.setData("application/x-isaac-palette", JSON.stringify(payload));
        e.dataTransfer.effectAllowed = "copy";
    };

    return (
        <div
            draggable
            onDragStart={onDragStart}
            onMouseEnter={() => setHover(true)}
            onMouseLeave={() => setHover(false)}
            style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: 4,
                background: hover ? ITEM_BG_HOVER : ITEM_BG,
                border: `1px solid ${hover ? tint : BORDER}`,
                borderRadius: 4,
                padding: "8px 4px",
                cursor: "grab",
                userSelect: "none",
                color: tint,
                transition: transition("react", "border-color"),
            }}
            title={`${meta.label} (${meta.defaultSize.w}×${meta.defaultSize.h} m)`}
            data-class={objectClass}
            data-testid={`palette-item-${objectClass}`}
        >
            <div style={{ height: 32, width: 32 }}>
                {SilhouetteFor(objectClass, { size: 32, color: tint })}
            </div>
            <div
                style={{
                    fontSize: 10,
                    color: hover ? TEXT : TEXT_DIM,
                    textAlign: "center",
                    lineHeight: 1.1,
                }}
            >
                {meta.label}
            </div>
        </div>
    );
}
