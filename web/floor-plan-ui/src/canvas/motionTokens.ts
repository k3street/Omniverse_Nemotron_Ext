/**
 * Motion vocabulary — spec §12.5.
 *
 * Six tiers from "instant" (DOM operations <16ms) to "transit" (long
 * scene transitions 600-900ms).  Used by App-level components to keep
 * timings consistent across hover, selection, drop, agent-write, build.
 *
 * `breathe` is reserved for live-state polish (idle robot reach-circle
 * pulse) and runs as an infinite CSS keyframe on the live agency tier.
 */
export const MOTION = {
    instant: 0,        // DOM updates, cursor changes
    flash: 80,         // hover glow, snap-line pop-in
    react: 160,        // selection, palette item lift
    commit: 240,       // drop confirmation, undo flash
    arrive: 360,       // panel slide-in, agent-write reveal
    transit: 720,      // build progress sweep, mode switch
} as const;

export type MotionTier = keyof typeof MOTION;

export const MOTION_EASING = {
    instant: "linear",
    flash: "cubic-bezier(0.4, 0, 0.2, 1)",
    react: "cubic-bezier(0.4, 0, 0.2, 1)",
    commit: "cubic-bezier(0.34, 1.56, 0.64, 1)",  // slight overshoot
    arrive: "cubic-bezier(0.16, 1, 0.3, 1)",      // ease-out-expo
    transit: "cubic-bezier(0.65, 0, 0.35, 1)",
} as const;

/** Inline transition string for use in style objects. */
export function transition(tier: MotionTier, prop: string = "all"): string {
    return `${prop} ${MOTION[tier]}ms ${MOTION_EASING[tier]}`;
}

/** CSS keyframes string injected once at App boot for the breathe pulse. */
export const KEYFRAMES_BREATHE = `
@keyframes ia-breathe {
    0%, 100% { opacity: 0.35; }
    50% { opacity: 0.6; }
}
`;
