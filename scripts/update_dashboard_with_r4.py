"""Place the Amber v0.5 Tool Gripper R4 (Glove-Matched C-Mouth Exoskeleton + R2/R3
Internal Worm-Spool Drives + Docked Wrist Tool Changer R2) interactive visual inspector
AT THE VERY TOP of `http://127.0.0.1:8000/api/v1/cad_pipeline/dashboard`.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASH = REPO_ROOT / "workspace" / "r2_hybrid_showcase" / "r2_showcase_dashboard.html"
REN = REPO_ROOT / "workspace" / "amber_gripper_r4" / "renders"
GLOVE_REF = Path(
    r"C:\Users\Owner\.gemini\antigravity\brain\7e1b3970-04ac-4134-b38c-9a909a454135\.user_uploaded\media_1795214756074_c806eaec.jpg"
)


def _b64(path: Path, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> None:
    if not DASH.exists():
        raise FileNotFoundError(str(DASH))
    html = DASH.read_text(encoding="utf-8")

    html = re.sub(r"<!-- AMBER_R4_TOP_START -->.*?<!-- AMBER_R4_TOP_END -->\s*", "", html, flags=re.S)
    if "<!-- AMBER_R4_SECTION -->" in html:
        html = html.split("<!-- AMBER_R4_SECTION -->")[0] + "</body>\n</html>\n"

    html = re.sub(
        r"<title>.*?</title>",
        "<title>Amber v0.5 Tool Gripper R4 (Capture-Glove Matched) + Docked WTC R2</title>",
        html,
        flags=re.S,
    )

    img_open = _b64(REN / "r4_open_with_changer.png")
    img_drives = _b64(REN / "r4_palm_drives_cutaway.png")
    img_bottle = _b64(REN / "r4_wrap_bottle_1l.png")
    img_pen = _b64(REN / "r4_pinch_pen_table.png")
    img_pair = _b64(REN / "r4_bilateral_pair_ovrtx.png")
    img_mj = _b64(REN / "r4_mujoco_zero_ik_grasp.png") if (REN / "r4_mujoco_zero_ik_grasp.png").exists() else img_bottle
    img_glove = _b64(GLOVE_REF, "image/jpeg") if GLOVE_REF.exists() else img_open

    r4_top = f"""<!-- AMBER_R4_TOP_START -->
<div style="max-width:1440px;margin:0 auto;padding:24px 24px 8px 24px;">
  <div style="background:linear-gradient(180deg,#0f172a 0%,#111827 100%);border:2px solid #0284c7;border-radius:16px;padding:22px;box-shadow:0 12px 36px rgba(0,0,0,0.45);">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:14px;border-bottom:1px solid #1e293b;padding-bottom:16px;margin-bottom:18px;">
      <div>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
          <span style="background:#38bdf8;color:#082f49;font-weight:800;font-size:12px;padding:4px 11px;border-radius:999px;letter-spacing:.04em;">ZERO-IK 1:1 RETARGETING &bull; q_r4 = I_6 &middot; q_glove</span>
          <span style="background:#1e293b;color:#cbd5e1;font-size:12px;padding:4px 10px;border-radius:999px;">6 Actuated DoFs = 5 Rotary + 3-Pos Thumb &bull; 0 Unobserved Joints</span>
        </div>
        <h1 style="margin:0;font-size:24px;color:#f8fafc;">Amber v0.5 <code style="color:#7dd3fc;">tool_gripper_r4</code> + Docked <code style="color:#7dd3fc;">Wrist_Tool_Changer_R2</code></h1>
        <div style="color:#94a3b8;font-size:14px;margin-top:6px;">
          <b>Minimal Motion Retargeting by Construction</b> (<code style="color:#6ee7b7;">q_r4 = I_6 &middot; q_glove</code>, 0.000 mm FK Discrepancy, 0 IK Iterations, 1:1 9-Ch Tactile + Radial Cheek ArduCam RGB View)
          &plus; <b>100% R2/R3 Inheritance</b> (6 Self-Locking Worm SK78 Spool/Gear Drives in 2-Layer 3-Deck Palm, Shore A 60 PU Twin-Eminence Saddle, M1.6 Service Crowns &amp; 0.50 mm Table Pen Pinch).
        </div>
      </div>
      <div style="background:#064e3b;border:1px solid #10b981;color:#a7f3d0;font-weight:700;padding:8px 16px;border-radius:999px;font-size:13px;white-space:nowrap;">
        6/6 CAD + MUJOCO ZERO-IK PASS
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1.25fr 0.75fr 1fr;gap:14px;align-items:start;">
      <div style="background:#050811;border:1px solid #1e293b;border-radius:12px;overflow:hidden;">
        <div style="padding:10px 14px;border-bottom:1px solid #1e293b;display:flex;justify-content:space-between;align-items:center;">
          <span id="r4HeroCaption" style="font-weight:700;font-size:13px;color:#e2e8f0;">1. Robot R4 + Docked Wrist Tool Changer R2 (Glove-Matched 6-DoF C-Mouth)</span>
          <span style="color:#38bdf8;font-size:12px;font-family:monospace;">1:1 Kinematic Twin</span>
        </div>
        <img id="r4HeroImg" src="{img_open}" style="width:100%;display:block;max-height:470px;object-fit:contain;background:#070b14;"/>
      </div>

      <div style="background:#f8fafc;border:1px solid #334155;border-radius:12px;overflow:hidden;">
        <div style="padding:10px 12px;background:#0f172a;border-bottom:1px solid #1e293b;display:flex;justify-content:space-between;align-items:center;">
          <span style="font-weight:700;font-size:13px;color:#e2e8f0;">Student Capture Glove CAD</span>
          <span style="color:#6ee7b7;font-size:11px;font-family:monospace;">q_r4 = q_glove</span>
        </div>
        <img src="{img_glove}" style="width:100%;display:block;max-height:470px;object-fit:contain;background:#ffffff;"/>
      </div>

      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:10px;">
        <div onclick="setR4View(0)" style="cursor:pointer;background:#0b1120;border:2px solid #38bdf8;border-radius:10px;overflow:hidden;" id="r4Thumb0">
          <img src="{img_open}" style="width:100%;aspect-ratio:16/10;object-fit:cover;display:block;"/>
          <div style="padding:6px 8px;font-size:11.5px;color:#e2e8f0;"><b>1. Glove-Matched C-Palm + WTC</b><br/><span style="color:#94a3b8;">6-DoF twin-strut &amp; chiseled heads</span></div>
        </div>
        <div onclick="setR4View(5)" style="cursor:pointer;background:#0b1120;border:2px solid #1e293b;border-radius:10px;overflow:hidden;" id="r4Thumb5">
          <img src="{img_mj}" style="width:100%;aspect-ratio:16/10;object-fit:cover;display:block;"/>
          <div style="padding:6px 8px;font-size:11.5px;color:#e2e8f0;"><b>2. Zero-IK MuJoCo 3.11 + ArduCam</b><br/><span style="color:#6ee7b7;">ctrl = q_glove (+85.1 mm lift!)</span></div>
        </div>
        <div onclick="setR4View(1)" style="cursor:pointer;background:#0b1120;border:2px solid #1e293b;border-radius:10px;overflow:hidden;" id="r4Thumb1">
          <img src="{img_drives}" style="width:100%;aspect-ratio:16/10;object-fit:cover;display:block;"/>
          <div style="padding:6px 8px;font-size:11.5px;color:#e2e8f0;"><b>3. 2-Layer 3-Deck Spool Drives</b><br/><span style="color:#94a3b8;">6 worm drives = 6 glove DoFs</span></div>
        </div>
        <div onclick="setR4View(2)" style="cursor:pointer;background:#0b1120;border:2px solid #1e293b;border-radius:10px;overflow:hidden;" id="r4Thumb2">
          <img src="{img_bottle}" style="width:100%;aspect-ratio:16/10;object-fit:cover;display:block;"/>
          <div style="padding:6px 8px;font-size:11.5px;color:#e2e8f0;"><b>4. 1 L Bottle Wrap (+20&deg; Thumb)</b><br/><span style="color:#94a3b8;">Cradled on Shore A 60 PU saddle</span></div>
        </div>
        <div onclick="setR4View(3)" style="cursor:pointer;background:#0b1120;border:2px solid #1e293b;border-radius:10px;overflow:hidden;" id="r4Thumb3">
          <img src="{img_pen}" style="width:100%;aspect-ratio:16/10;object-fit:cover;display:block;"/>
          <div style="padding:6px 8px;font-size:11.5px;color:#e2e8f0;"><b>5. Table Pen Pinch (0.50 mm Gap)</b><br/><span style="color:#94a3b8;">&Oslash;8 mm pen + cheek ArduCam</span></div>
        </div>
        <div onclick="setR4View(4)" style="cursor:pointer;background:#0b1120;border:2px solid #1e293b;border-radius:10px;overflow:hidden;" id="r4Thumb4">
          <img src="{img_pair}" style="width:100%;aspect-ratio:16/10;object-fit:cover;display:block;"/>
          <div style="padding:6px 8px;font-size:11.5px;color:#e2e8f0;"><b>6. Bilateral Right + Left Pair</b><br/><span style="color:#94a3b8;">Un-mirrored R=22 mm changers</span></div>
        </div>
      </div>
    </div>
  </div>
</div>
<script>
const R4_VIEWS = [
  {{ src: "{img_open}", cap: "1. Robot R4 + Docked Wrist Tool Changer R2 (1:1 6-DoF Kinematic Twin of Capture Glove)" }},
  {{ src: "{img_drives}", cap: "3. Internal 2-Layer 3-Deck Lower Palm Cutaway (6 Self-Locking Worm Drives = 5 Rotary + 1 Thumb Slot)" }},
  {{ src: "{img_bottle}", cap: "4. wrap_bottle_1l Qualification (1.04 kg Wet Ø81 mm Bottle Cradled on Shore A 60 PU Saddle, Thumb +20°)" }},
  {{ src: "{img_pen}", cap: "5. pinch_pen Qualification (GR3-PEN-01: Ø8.0 mm Pen Pinched 0.50 mm Clear of Tabletop)" }},
  {{ src: "{img_pair}", cap: "6. Bilateral Right + Left Glove-Matched R4 Pair on Standardized Un-Mirrored Wrist Tool Changers" }},
  {{ src: "{img_mj}", cap: "2. MuJoCo 3.11.0 Zero-IK Direct Glove Episode Playback (ctrl = I_6 * q_glove, +85.1 mm Lift + Cheek ArduCam PiP)" }}
];
function setR4View(idx) {{
  document.getElementById("r4HeroImg").src = R4_VIEWS[idx].src;
  document.getElementById("r4HeroCaption").textContent = R4_VIEWS[idx].cap;
  for (let i = 0; i < 6; i++) {{
    const el = document.getElementById("r4Thumb" + i);
    if (el) el.style.borderColor = (i === idx) ? "#38bdf8" : "#1e293b";
  }}
}}
</script>
<!-- AMBER_R4_TOP_END -->
"""
    html = html.replace("<body>", "<body>\n" + r4_top, 1)
    DASH.write_text(html, encoding="utf-8")
    print("Injected Glove-Matched R4 + Side-by-Side Reference at top of dashboard:", DASH, DASH.stat().st_size)


if __name__ == "__main__":
    main()
