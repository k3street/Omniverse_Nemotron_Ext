/**
 * Command pattern for undo/redo.  Each user mutation (move/resize/rotate/add/
 * delete/set_attr) is a Command with apply() + undo() methods.  Linear
 * history, 100-step depth, redo cleared on new action after undo.
 *
 * Per spec §2.3.  Commands operate on TypedObject lists and produce inverse
 * commands so undo restores previous state exactly.
 */
import { Position, Size, TypedObject } from "../api/types";

// ─── Command interface ───────────────────────────────────────────────

export interface Command {
    readonly type: string;
    readonly description: string;
    apply(objects: TypedObject[]): TypedObject[];
    undo(objects: TypedObject[]): TypedObject[];
}

// ─── MoveObject ──────────────────────────────────────────────────────

export class MoveObject implements Command {
    readonly type = "move";
    readonly description: string;
    constructor(
        public readonly id: string,
        public readonly from: Position,
        public readonly to: Position,
    ) {
        this.description = `Move ${id.slice(0, 8)} (${from.x.toFixed(2)}, ${from.y.toFixed(2)}) → (${to.x.toFixed(2)}, ${to.y.toFixed(2)})`;
    }
    apply(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, position: this.to } : o,
        );
    }
    undo(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, position: this.from } : o,
        );
    }
}

// ─── ResizeObject ────────────────────────────────────────────────────

export class ResizeObject implements Command {
    readonly type = "resize";
    readonly description: string;
    constructor(
        public readonly id: string,
        public readonly from: Size,
        public readonly to: Size,
    ) {
        this.description = `Resize ${id.slice(0, 8)}`;
    }
    apply(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, size: this.to } : o,
        );
    }
    undo(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, size: this.from } : o,
        );
    }
}

// ─── RotateObject ────────────────────────────────────────────────────

export class RotateObject implements Command {
    readonly type = "rotate";
    readonly description: string;
    constructor(
        public readonly id: string,
        public readonly from: number,
        public readonly to: number,
    ) {
        this.description = `Rotate ${id.slice(0, 8)} ${from}° → ${to}°`;
    }
    apply(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, rotation: this.to } : o,
        );
    }
    undo(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, rotation: this.from } : o,
        );
    }
}

// ─── AddObject ───────────────────────────────────────────────────────

export class AddObject implements Command {
    readonly type = "add";
    readonly description: string;
    constructor(public readonly object: TypedObject) {
        this.description = `Add ${object.class} ${object.name}`;
    }
    apply(objects: TypedObject[]): TypedObject[] {
        return [...objects, this.object];
    }
    undo(objects: TypedObject[]): TypedObject[] {
        return objects.filter((o) => o.id !== this.object.id);
    }
}

// ─── DeleteObject ────────────────────────────────────────────────────

export class DeleteObject implements Command {
    readonly type = "delete";
    readonly description: string;
    constructor(public readonly object: TypedObject) {
        this.description = `Delete ${object.class} ${object.name}`;
    }
    apply(objects: TypedObject[]): TypedObject[] {
        return objects.filter((o) => o.id !== this.object.id);
    }
    undo(objects: TypedObject[]): TypedObject[] {
        return [...objects, this.object];
    }
}

// ─── SetAttr (single object fields) ──────────────────────────────────

export type SetAttrKey = "class" | "name" | "notes" | "color" | "layer" | "locked";

export class SetAttr implements Command {
    readonly type = "set_attr";
    readonly description: string;
    constructor(
        public readonly id: string,
        public readonly attr: SetAttrKey,
        public readonly from: unknown,
        public readonly to: unknown,
    ) {
        this.description = `Set ${attr} on ${id.slice(0, 8)}`;
    }
    apply(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, [this.attr]: this.to } : o,
        ) as TypedObject[];
    }
    undo(objects: TypedObject[]): TypedObject[] {
        return objects.map((o) =>
            o.id === this.id ? { ...o, [this.attr]: this.from } : o,
        ) as TypedObject[];
    }
}

// ─── BulkUpdate (used by agent writes) ───────────────────────────────

export class BulkUpdate implements Command {
    readonly type = "bulk_update";
    readonly description: string;
    constructor(
        public readonly before: TypedObject[],
        public readonly after: TypedObject[],
        description?: string,
    ) {
        this.description =
            description ?? `Bulk update (${before.length} → ${after.length})`;
    }
    apply(_: TypedObject[]): TypedObject[] {
        return [...this.after];
    }
    undo(_: TypedObject[]): TypedObject[] {
        return [...this.before];
    }
}
