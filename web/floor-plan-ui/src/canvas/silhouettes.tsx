/**
 * Robot/object silhouette glyphs — placeholder SVGs at 32×32 used by the
 * palette and as Konva fallbacks when no class-specific icon ships.
 *
 * Spec §12.6: replace generic "robot arm" emoji with class-specific
 * silhouettes so users can read at a glance.  These are vector-only — no
 * external image deps — and use `currentColor` so they tint with the
 * agency-tier class color on hover/select.
 */
import { JSX } from "react";

export interface SilhouetteProps {
    size?: number;
    color?: string;
}

const SVG = ({ children, size = 32, color }: SilhouetteProps & { children: JSX.Element }) => (
    <svg
        width={size}
        height={size}
        viewBox="0 0 32 32"
        fill="none"
        stroke={color ?? "currentColor"}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ display: "block" }}
    >
        {children}
    </svg>
);

export const FrankaSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="11" y="24" width="10" height="4" rx="1" />
            <path d="M16 24 L16 18 L23 11" />
            <path d="M23 11 L18 6" />
            <circle cx="16" cy="18" r="2" fill="currentColor" />
            <circle cx="23" cy="11" r="2" fill="currentColor" />
            <circle cx="18" cy="6" r="1.5" />
        </g>
    </SVG>
);

export const URSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="10" y="25" width="12" height="3" rx="1" />
            <path d="M16 25 L16 19 L24 12 L24 7" />
            <circle cx="16" cy="19" r="1.8" fill="currentColor" />
            <circle cx="24" cy="12" r="1.8" fill="currentColor" />
            <rect x="22" y="4" width="4" height="3" rx="0.5" />
        </g>
    </SVG>
);

export const KinovaSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="11" y="25" width="10" height="3" rx="1" />
            <path d="M16 25 L16 18 L21 13 L21 8" />
            <path d="M21 8 L18 5" />
            <circle cx="16" cy="18" r="1.6" fill="currentColor" />
            <circle cx="21" cy="13" r="1.6" fill="currentColor" />
        </g>
    </SVG>
);

export const IIWASilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="10" y="25" width="12" height="3" rx="1" />
            <path d="M16 25 L16 17 L20 13 L24 9 L24 5" />
            <circle cx="16" cy="17" r="1.4" fill="currentColor" />
            <circle cx="20" cy="13" r="1.4" fill="currentColor" />
            <circle cx="24" cy="9" r="1.4" fill="currentColor" />
        </g>
    </SVG>
);

export const JacoSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="11" y="25" width="10" height="3" rx="1" />
            <path d="M16 25 L16 18 L20 14 L23 9 L20 5" />
            <circle cx="16" cy="18" r="1.4" fill="currentColor" />
            <circle cx="20" cy="14" r="1.4" fill="currentColor" />
            <circle cx="23" cy="9" r="1.4" fill="currentColor" />
            <path d="M19 5 L21 5 M19 7 L21 7" />
        </g>
    </SVG>
);

export const NovaCarterSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="6" y="10" width="20" height="14" rx="2" />
            <circle cx="11" cy="24" r="3" fill="currentColor" />
            <circle cx="21" cy="24" r="3" fill="currentColor" />
            <path d="M10 14 L22 14 M10 18 L22 18" />
        </g>
    </SVG>
);

export const ConveyorSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="3" y="13" width="26" height="8" rx="1" />
            <path d="M5 17 L9 17 M11 17 L15 17 M17 17 L21 17 M23 17 L27 17" />
            <path d="M9 22 L9 26 M23 22 L23 26" />
        </g>
    </SVG>
);

export const BinSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <path d="M9 9 L23 9 L21 26 L11 26 Z" />
            <path d="M7 9 L25 9" />
            <path d="M14 13 L14 22 M16 13 L16 22 M18 13 L18 22" />
        </g>
    </SVG>
);

export const CubeSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="9" y="9" width="14" height="14" rx="0.5" />
            <path d="M9 15 L23 15 M16 9 L16 23" />
        </g>
    </SVG>
);

export const TableSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="3" y="11" width="26" height="3" rx="0.5" />
            <path d="M6 14 L6 26 M26 14 L26 26 M16 14 L16 22" />
            <rect x="14" y="22" width="4" height="4" rx="0.5" />
        </g>
    </SVG>
);

export const StationMarkerSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <path d="M16 4 L16 22" />
            <path d="M16 4 L24 8 L16 12 Z" fill="currentColor" />
            <circle cx="16" cy="25" r="2" fill="currentColor" />
        </g>
    </SVG>
);

export const CameraSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="4" y="10" width="20" height="14" rx="1" />
            <circle cx="14" cy="17" r="4" />
            <rect x="22" y="13" width="6" height="8" rx="0.5" />
        </g>
    </SVG>
);

export const LidarSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="9" y="6" width="14" height="6" rx="2" />
            <rect x="11" y="12" width="10" height="14" rx="1" />
            <path d="M11 18 L21 18" />
        </g>
    </SVG>
);

export const RampSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <path d="M4 26 L28 26 L28 18 Z" />
            <path d="M4 26 L28 18" />
        </g>
    </SVG>
);

export const WallSilhouette = (props: SilhouetteProps) => (
    <SVG {...props}>
        <g>
            <rect x="3" y="9" width="26" height="14" rx="0.5" />
            <path d="M3 13 L11 13 L11 19 L3 19 M11 16 L19 16 L19 22 M19 13 L29 13 L29 19" />
        </g>
    </SVG>
);

export function SilhouetteFor(objectClass: string, props: SilhouetteProps = {}) {
    switch (objectClass) {
        case "franka_panda": return <FrankaSilhouette {...props} />;
        case "ur5e":
        case "ur10e": return <URSilhouette {...props} />;
        case "kinova_gen3": return <KinovaSilhouette {...props} />;
        case "iiwa": return <IIWASilhouette {...props} />;
        case "jaco7": return <JacoSilhouette {...props} />;
        case "nova_carter": return <NovaCarterSilhouette {...props} />;
        case "conveyor": return <ConveyorSilhouette {...props} />;
        case "bin": return <BinSilhouette {...props} />;
        case "cube": return <CubeSilhouette {...props} />;
        case "table": return <TableSilhouette {...props} />;
        case "station_marker": return <StationMarkerSilhouette {...props} />;
        case "camera_sensor": return <CameraSilhouette {...props} />;
        case "lidar_sensor": return <LidarSilhouette {...props} />;
        case "ramp": return <RampSilhouette {...props} />;
        case "wall": return <WallSilhouette {...props} />;
        default:
            return (
                <SVG {...props}>
                    <g>
                        <rect x="6" y="6" width="20" height="20" rx="2" />
                        <path d="M12 16 L20 16 M16 12 L16 20" />
                    </g>
                </SVG>
            );
    }
}
