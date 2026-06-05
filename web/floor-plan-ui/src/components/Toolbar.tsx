/**
 * Left toolbar — view/edit modes + history (spec §11.3.2).
 *
 * The toolbar is intentionally minimal in Block 1A.3.  Tool modes are
 * placeholders for future tools (annotate, measure, lock-region); the
 * primary interactions today are select/drag (default) and palette
 * drag-drop.  Undo/redo and zoom buttons are functional.
 */
import { useFloorPlanStore } from "../store/floorPlanStore";
import { transition } from "../canvas/motionTokens";

const BG = "#171A1E";
const BORDER = "#2E3237";
const ICON_DIM = "#8A8E92";
const ICON = "#DDDDDD";
const ACCENT = "#76B900";

export function Toolbar() {
    const undo = useFloorPlanStore((s) => s.undo);
    const redo = useFloorPlanStore((s) => s.redo);
    const canUndo = useFloorPlanStore((s) => s.undoStack.length > 0);
    const canRedo = useFloorPlanStore((s) => s.redoStack.length > 0);

    return (
        <div
            style={{
                width: 44,
                background: BG,
                borderRight: `1px solid ${BORDER}`,
                display: "flex",
                flexDirection: "column",
                padding: "8px 0",
                gap: 4,
                color: ICON_DIM,
            }}
            data-testid="toolbar"
        >
            <ToolButton label="Select" icon="↖" active />
            <ToolButton label="Place (use palette)" icon="+" />
            <ToolButton label="Annotate (v1.1)" icon="✎" disabled />
            <ToolButton label="Measure (v1.1)" icon="↔" disabled />
            <Divider />
            <ToolButton label="Undo (⌘Z)" icon="↶" onClick={undo} disabled={!canUndo} />
            <ToolButton label="Redo (⌘⇧Z)" icon="↷" onClick={redo} disabled={!canRedo} />
        </div>
    );
}

function ToolButton({
    label,
    icon,
    active,
    disabled,
    onClick,
}: {
    label: string;
    icon: string;
    active?: boolean;
    disabled?: boolean;
    onClick?: () => void;
}) {
    return (
        <button
            type="button"
            title={label}
            disabled={disabled}
            onClick={onClick}
            style={{
                width: 32,
                height: 32,
                margin: "0 6px",
                background: active ? "#2A3038" : "transparent",
                color: disabled ? "#4A4F55" : active ? ACCENT : ICON,
                border: active ? `1px solid ${ACCENT}55` : "1px solid transparent",
                borderRadius: 4,
                fontSize: 16,
                cursor: disabled ? "not-allowed" : "pointer",
                transition: transition("react"),
                opacity: disabled ? 0.5 : 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontFamily: "inherit",
            }}
        >
            {icon}
        </button>
    );
}

function Divider() {
    return (
        <div
            style={{
                height: 1,
                background: BORDER,
                margin: "4px 8px",
            }}
        />
    );
}
