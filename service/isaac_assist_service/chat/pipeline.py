"""
chat/pipeline.py
-----------------
Pipeline planner for autonomous multi-step robot simulation scenarios.

Takes a high-level prompt ("Nova Carter in a home environment") and produces
a structured multi-phase plan.  Each phase maps to a focused chat message
that the extension-side executor sends to the regular /message endpoint.

Supports:
  - Template-based plans for known robots (reliable, fast)
  - LLM-generated plans for arbitrary scenarios (flexible, slower)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Phase templates for known robots
# ---------------------------------------------------------------------------

_NOVA_CARTER_PHASES = [
    {
        "id": 1,
        "name": "Scene Setup",
        "prompt": (
            "Create a {scenario} environment scene:\n"
            "1. Add a ground plane with collision at position 0,0,0.\n"
            "2. {scenario_assets}\n"
            "3. Set physics gravity to -9.81 m/s² on the Z axis.\n"
            "Make sure every object has collision enabled."
        ),
        "verification": "Check that the ground plane and environment objects exist with collision APIs.",
        "retry_hint": "If any prim is missing CollisionAPI, apply it now.",
    },
    {
        "id": 2,
        "name": "Robot Import",
        "prompt": (
            "Import a Nova Carter robot at position 0, 0, 0.\n"
            "CRITICAL: Do NOT set fixedBase=True — Nova Carter is a mobile wheeled robot.\n"
            "Apply rigid body physics and collision to the chassis and all wheel links.\n"
            "Delete any rootJoint (6-DOF free joint) if present — it causes instability.\n"
            "Set wheel collision approximation to convexHull."
        ),
        "verification": "Verify /World/NovaCarter (or similar) exists with RigidBodyAPI on chassis.",
        "retry_hint": "If the robot import failed, search the asset catalog for 'Nova Carter' first, then use the correct USD path.",
    },
    {
        "id": 3,
        "name": "ROS2 Differential Drive",
        "prompt": (
            "Create a ROS2 OmniGraph at /World/CarterROS2Graph for the Nova Carter robot:\n"
            "1. OnPlaybackTick as the tick source\n"
            "2. ROS2SubscribeTwist node subscribing to /cmd_vel\n"
            "3. DifferentialController for the two front drive wheels "
            "(rear casters are passive — do NOT drive them)\n"
            "4. IsaacArticulationController targeting the Nova Carter robot\n"
            "5. ROS2PublishOdometry publishing to /odom\n\n"
            "IMPORTANT: ROS2SubscribeTwist outputs double3 but DifferentialController "
            "expects scalar inputs. Use Break3Vector nodes to extract X (linear) and Z (angular) components."
        ),
        "verification": "Verify the OmniGraph exists at /World/CarterROS2Graph with ROS2 nodes.",
        "retry_hint": "If OmniGraph creation failed, check that isaacsim.ros2.bridge extension is enabled. Use isaacsim.* node type namespace, NOT omni.isaac.*.",
    },
    {
        "id": 4,
        "name": "Sensor Setup",
        "prompt": (
            "Add sensors to the Nova Carter robot:\n"
            "1. Add an Intel RealSense D435i camera to the chassis_link "
            "at position 0.2, 0, 0.15 facing forward.\n"
            "2. Look up the RealSense D435i product specs to get the correct FOV and resolution."
        ),
        "verification": "Verify a camera prim exists under the Nova Carter's chassis link.",
        "retry_hint": "If sensor attachment failed, try creating a simple UsdGeom.Camera as a child of the robot's chassis prim.",
    },
    {
        "id": 5,
        "name": "Final Verification",
        "prompt": (
            "Give me a complete summary of the current scene. List:\n"
            "1. All prims and their types\n"
            "2. Which prims have physics APIs (RigidBody, Collision)\n"
            "3. Any OmniGraph nodes and their connections\n"
            "4. Any console errors or warnings\n"
            "Do NOT generate any code — just report the scene state."
        ),
        "verification": None,  # This phase IS the verification
        "retry_hint": None,
        "is_data_only": True,
    },
]

_JETBOT_PHASES = [
    {
        "id": 1,
        "name": "Scene Setup",
        "prompt": (
            "Create a {scenario} environment scene:\n"
            "1. Add a ground plane with collision at position 0,0,0.\n"
            "2. {scenario_assets}\n"
            "3. Set physics gravity to -9.81 m/s² on the Z axis.\n"
            "Make sure every object has collision enabled."
        ),
        "verification": "Check that the ground plane and environment objects exist with collision APIs.",
        "retry_hint": "If any prim is missing CollisionAPI, apply it now.",
    },
    {
        "id": 2,
        "name": "Robot Import",
        "prompt": (
            "Import a Jetbot robot at position 0, 0, 0.\n"
            "The Jetbot is a small two-wheeled differential drive robot.\n"
            "Do NOT set fixedBase=True — Jetbot needs to drive around.\n"
            "Apply rigid body physics and collision to the chassis and wheels."
        ),
        "verification": "Verify the Jetbot prim exists with physics APIs.",
        "retry_hint": "Search the asset catalog for 'Jetbot' to find the correct USD path.",
    },
    {
        "id": 3,
        "name": "ROS2 Differential Drive",
        "prompt": (
            "Create a ROS2 OmniGraph at /World/JetbotROS2Graph for the Jetbot:\n"
            "1. OnPlaybackTick as the tick source\n"
            "2. ROS2SubscribeTwist subscribing to /cmd_vel\n"
            "3. DifferentialController for the left and right drive wheels\n"
            "4. IsaacArticulationController targeting the Jetbot\n"
            "5. ROS2PublishOdometry publishing to /odom\n\n"
            "Use Break3Vector to extract scalar components from the Twist message."
        ),
        "verification": "Verify the OmniGraph exists with ROS2 nodes.",
        "retry_hint": "Check node type namespaces: use isaacsim.* not omni.isaac.*.",
    },
    {
        "id": 4,
        "name": "Camera Sensor",
        "prompt": (
            "Add a camera sensor to the Jetbot's chassis, positioned at "
            "0.05, 0, 0.05 facing forward. This will be the robot's onboard camera."
        ),
        "verification": "Verify a camera prim exists under the Jetbot.",
        "retry_hint": "Create a simple UsdGeom.Camera as a child of the robot chassis.",
    },
    {
        "id": 5,
        "name": "Final Verification",
        "prompt": (
            "Give me a complete summary of the current scene. List all prims, "
            "physics APIs, OmniGraph nodes, and any console errors. "
            "Do NOT generate any code."
        ),
        "verification": None,
        "retry_hint": None,
        "is_data_only": True,
    },
]

_FRANKA_PHASES = [
    {
        "id": 1,
        "name": "Scene Setup",
        "prompt": (
            "Create a {scenario} environment scene:\n"
            "1. Add a ground plane with collision at position 0,0,0.\n"
            "2. {scenario_assets}\n"
            "3. Add a table (flat box 1.0 x 0.6 x 0.05) at position 0.5, 0, 0.4 "
            "with rigid body physics and collision — this is the robot's workspace.\n"
            "4. Add a small cube (0.05m) named 'TargetCube' at 0.5, 0.2, 0.475 "
            "on the table with rigid body physics and collision.\n"
            "5. Set physics gravity to -9.81 on Z."
        ),
        "verification": "Check that ground, table, and target cube exist with physics.",
        "retry_hint": "Apply CollisionAPI and RigidBodyAPI to any objects missing them.",
    },
    {
        "id": 2,
        "name": "Robot Import",
        "prompt": (
            "Import a Franka Emika Panda robot at the origin (0, 0, 0).\n"
            "This is a stationary robot arm — use anchor_robot with fixedBase=True.\n"
            "The robot should be bolted to the ground."
        ),
        "verification": "Verify the Franka robot exists with ArticulationRootAPI.",
        "retry_hint": "Search asset catalog for 'Franka' to find the correct USD path.",
    },
    {
        "id": 3,
        "name": "ROS2 JointState Graph",
        "prompt": (
            "Create a ROS2 OmniGraph at /World/FrankaROS2Graph:\n"
            "1. OnPlaybackTick tick source\n"
            "2. ROS2SubscribeJointState subscribing to /joint_command\n"
            "3. IsaacArticulationController targeting the Franka robot\n"
            "4. ROS2PublishJointState publishing to /joint_states\n"
            "Wire them all together."
        ),
        "verification": "Verify the OmniGraph exists with JointState nodes.",
        "retry_hint": "Use isaacsim.* namespace for node types.",
    },
    {
        "id": 4,
        "name": "Final Verification",
        "prompt": (
            "Give me a complete scene summary: all prims, physics APIs, "
            "OmniGraph nodes, and any errors. Do NOT generate code."
        ),
        "verification": None,
        "retry_hint": None,
        "is_data_only": True,
    },
]

_UNITREE_G1_PHASES = [
    {
        "id": 1,
        "name": "Scene Setup",
        "prompt": (
            "Create a {scenario} environment scene:\n"
            "1. Add a ground plane with collision at position 0,0,0.\n"
            "2. {scenario_assets}\n"
            "3. Set physics gravity to -9.81 m/s² on the Z axis.\n"
            "Make sure every object has collision enabled."
        ),
        "verification": "Check that the ground plane and environment objects exist with collision APIs.",
        "retry_hint": "If any prim is missing CollisionAPI, apply it now.",
    },
    {
        "id": 2,
        "name": "Robot Import",
        "prompt": (
            "Search the asset catalog for 'Unitree G1' or 'G1' humanoid robot.\n"
            "Import it at position 0, 0, 0.\n"
            "The G1 is a bipedal humanoid — do NOT set fixedBase=True, it needs to stand freely.\n"
            "Apply rigid body physics and collision to all links.\n"
            "If the asset is not in the catalog, try searching for 'Unitree' "
            "and use whatever humanoid model is available."
        ),
        "verification": "Verify the Unitree G1 prim exists with physics APIs.",
        "retry_hint": "If not found in catalog, try importing from a generic humanoid URDF or search for 'humanoid'.",
    },
    {
        "id": 3,
        "name": "ROS2 JointState Graph",
        "prompt": (
            "Create a ROS2 OmniGraph at /World/G1ROS2Graph:\n"
            "1. OnPlaybackTick tick source\n"
            "2. ROS2SubscribeJointState subscribing to /joint_command\n"
            "3. IsaacArticulationController targeting the G1 robot\n"
            "4. ROS2PublishJointState publishing to /joint_states\n"
            "Wire them all together. The G1 has many joints — the articulation "
            "controller should handle all of them."
        ),
        "verification": "Verify the OmniGraph exists with JointState nodes.",
        "retry_hint": "Use isaacsim.* namespace for node types.",
    },
    {
        "id": 4,
        "name": "Final Verification",
        "prompt": (
            "Give me a complete scene summary: all prims, physics APIs, "
            "OmniGraph nodes, and any errors. Do NOT generate code."
        ),
        "verification": None,
        "retry_hint": None,
        "is_data_only": True,
    },
]

_UNITREE_GO2_PHASES = [
    {
        "id": 1,
        "name": "Scene Setup",
        "prompt": (
            "Create a {scenario} environment scene:\n"
            "1. Add a ground plane with collision at position 0,0,0.\n"
            "2. {scenario_assets}\n"
            "3. Set physics gravity to -9.81 m/s² on the Z axis.\n"
            "Make sure every object has collision enabled."
        ),
        "verification": "Check that the ground plane and environment objects exist with collision APIs.",
        "retry_hint": "If any prim is missing CollisionAPI, apply it now.",
    },
    {
        "id": 2,
        "name": "Robot Import",
        "prompt": (
            "Search the asset catalog for 'Unitree Go2' or 'Go2' quadruped robot.\n"
            "Import it at position 0, 0, 0.3 (elevated slightly so legs don't clip the ground).\n"
            "The Go2 is a quadruped — do NOT set fixedBase=True, it walks on 4 legs.\n"
            "Apply rigid body physics and collision to all links."
        ),
        "verification": "Verify the Go2 prim exists with physics APIs.",
        "retry_hint": "If not in catalog, search for 'quadruped' or 'Unitree'.",
    },
    {
        "id": 3,
        "name": "ROS2 JointState Graph",
        "prompt": (
            "Create a ROS2 OmniGraph at /World/Go2ROS2Graph:\n"
            "1. OnPlaybackTick tick source\n"
            "2. ROS2SubscribeJointState subscribing to /joint_command\n"
            "3. IsaacArticulationController targeting the Go2 robot\n"
            "4. ROS2PublishJointState publishing to /joint_states\n"
            "Wire them all together."
        ),
        "verification": "Verify the OmniGraph exists with JointState nodes.",
        "retry_hint": "Use isaacsim.* namespace for node types.",
    },
    {
        "id": 4,
        "name": "Final Verification",
        "prompt": (
            "Give me a complete scene summary: all prims, physics APIs, "
            "OmniGraph nodes, and any errors. Do NOT generate code."
        ),
        "verification": None,
        "retry_hint": None,
        "is_data_only": True,
    },
]

# Scenario asset descriptions for template interpolation
_SCENARIO_ASSETS = {
    "home": (
        "Create a simple home environment:\n"
        "   - 4 wall prims (thin boxes, 5m long, 0.1m thick, 2.5m tall) forming a room\n"
        "   - A table (flat box 1.2x0.6x0.05) at position 2, 1, 0.4\n"
        "   - A shelf (box 0.8x0.3x1.2) at position -1.5, 2, 0.6 against a wall\n"
        "   - A chair-like shape (box 0.4x0.4x0.45) at position 1.5, 1.2, 0.225"
    ),
    "warehouse": (
        "Create a warehouse environment:\n"
        "   - A large open floor area (the ground plane serves as floor)\n"
        "   - 3 shelf racks (tall boxes 2x0.5x2m) at positions (-3,0,1), (0,0,1), (3,0,1)\n"
        "   - A conveyor belt placeholder (long flat box 4x0.6x0.3) at position 0, 3, 0.3\n"
        "   - 3 small boxes (0.3m cubes) stacked near the conveyor at (2, 3, 0.15), (2, 3, 0.45), (2, 3, 0.75)"
    ),
    "office": (
        "Create an office environment:\n"
        "   - 2 desks (flat boxes 1.5x0.7x0.05) at positions (1, 0, 0.72) and (1, 2, 0.72)\n"
        "   - Desk legs (4 thin cylinders per desk)\n"
        "   - 2 chairs (box 0.4x0.4x0.45) at positions (0.3, 0, 0.225) and (0.3, 2, 0.225)\n"
        "   - A small cabinet (box 0.5x0.4x0.8) at position (-2, 1, 0.4)"
    ),
    "outdoor": (
        "Create a simple outdoor environment:\n"
        "   - The ground plane serves as the terrain\n"
        "   - A few obstacle boxes at positions (2,1,0.25), (-1,2,0.5), (3,-1,0.3)\n"
        "   - A ramp (tilted box) from (0,3,0) to (0,4,0.5) for driving up/down"
    ),
    "simple": (
        "Keep the environment minimal:\n"
        "   - Just the ground plane\n"
        "   - A few colored cubes as landmarks at (2,0,0.25), (0,2,0.25), (-2,0,0.25)"
    ),
}

# Robot alias mapping
_ROBOT_ALIASES = {
    "nova_carter": ["nova carter", "novacarter", "carter"],
    "jetbot": ["jetbot", "jet bot"],
    "franka": ["franka", "panda", "franka emika", "franka panda"],
    "unitree_g1": ["unitree g1", "g1 humanoid", "g1 robot"],
    "unitree_go2": ["unitree go2", "go2", "go 2"],
}

_ROBOT_TEMPLATES = {
    "nova_carter": _NOVA_CARTER_PHASES,
    "jetbot": _JETBOT_PHASES,
    "franka": _FRANKA_PHASES,
    "unitree_g1": _UNITREE_G1_PHASES,
    "unitree_go2": _UNITREE_GO2_PHASES,
}


# ---------------------------------------------------------------------------
#  LLM-based plan generation prompt
# ---------------------------------------------------------------------------

PIPELINE_PLAN_PROMPT = """\
You are the Isaac Sim Pipeline Planner. Given a high-level description of a robot
simulation scenario, generate a structured multi-phase execution plan.

Each phase should be a focused task that can be accomplished in one chat turn
(one call to the Isaac Assist orchestrator with its tool-calling loop).

RULES:
- Phase 1 is ALWAYS scene/environment setup (ground plane, obstacles, furniture)
- Phase 2 is ALWAYS robot import + physics setup
- Phase 3+ are ROS2 graphs, sensors, motion planning, etc.
- The LAST phase is ALWAYS a verification-only phase (no code generation)
- Each phase prompt must be specific enough for the LLM to call the right tools
- For WHEELED robots (Nova Carter, Jetbot): NEVER set fixedBase=True
- For STATIONARY robots (Franka, UR10): use anchor_robot with fixedBase=True
- Include Isaac Sim-specific API hints (isaacsim.* namespace, Break3Vector for Twist, etc.)

Respond with a JSON object:
{{
  "title": "Pipeline title",
  "robot": "Robot name",
  "scenario": "Scenario description",
  "phases": [
    {{
      "id": 1,
      "name": "Phase name",
      "prompt": "Detailed prompt for this phase",
      "verification": "What to verify after this phase (null for data-only phases)",
      "retry_hint": "Hint for fixing failures (null for data-only phases)",
      "is_data_only": false
    }}
  ]
}}

User request: {user_request}
"""


class PipelinePlanner:
    """Generates structured multi-phase plans for robot simulation pipelines."""

    def plan(self, prompt: str) -> Dict[str, Any]:
        """
        Generate a pipeline plan from a high-level prompt.

        First tries to match a known robot template.
        Falls back to LLM-generated plan for unknown scenarios.
        """
        prompt_lower = prompt.lower()

        # 1. Detect robot
        robot_key = self._detect_robot(prompt_lower)

        # 2. Detect scenario
        scenario_key = self._detect_scenario(prompt_lower)

        # 3. Try template
        if robot_key and robot_key in _ROBOT_TEMPLATES:
            return self._from_template(robot_key, scenario_key, prompt)

        # 4. Fallback: return metadata for LLM-based planning
        return {
            "needs_llm": True,
            "detected_robot": robot_key,
            "detected_scenario": scenario_key,
            "user_prompt": prompt,
        }

    async def plan_with_llm(self, prompt: str, llm_provider) -> Dict[str, Any]:
        """Use LLM to generate a plan for arbitrary scenarios."""
        filled_prompt = PIPELINE_PLAN_PROMPT.format(user_request=prompt)
        messages = [
            {"role": "system", "content": "You are a pipeline planner. Respond with valid JSON only."},
            {"role": "user", "content": filled_prompt},
        ]

        response = await llm_provider.complete(messages, {})
        text = response.text or ""

        # Extract JSON from response
        try:
            # Try to find JSON block
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                plan = json.loads(json_match.group())
                plan["source"] = "llm"
                return plan
        except json.JSONDecodeError:
            pass

        return {
            "error": "Failed to generate a valid pipeline plan from LLM",
            "raw_response": text[:1000],
        }

    def _detect_robot(self, prompt_lower: str) -> Optional[str]:
        for key, aliases in _ROBOT_ALIASES.items():
            for alias in aliases:
                if alias in prompt_lower:
                    return key
        return None

    def _detect_scenario(self, prompt_lower: str) -> str:
        for scenario in _SCENARIO_ASSETS:
            if scenario in prompt_lower:
                return scenario
        # Default scenarios based on keywords
        if any(w in prompt_lower for w in ["room", "house", "kitchen", "living"]):
            return "home"
        if any(w in prompt_lower for w in ["factory", "industrial", "shelf", "conveyor"]):
            return "warehouse"
        if any(w in prompt_lower for w in ["desk", "cubicle"]):
            return "office"
        if any(w in prompt_lower for w in ["park", "field", "terrain", "outside"]):
            return "outdoor"
        return "simple"

    def _from_template(
        self, robot_key: str, scenario_key: str, user_prompt: str
    ) -> Dict[str, Any]:
        """Build a plan from a known robot template."""
        template = _ROBOT_TEMPLATES[robot_key]
        scenario_assets = _SCENARIO_ASSETS.get(scenario_key, _SCENARIO_ASSETS["simple"])

        # Interpolate scenario into phase prompts
        phases = []
        for phase in template:
            p = dict(phase)  # shallow copy
            p["prompt"] = p["prompt"].format(
                scenario=scenario_key,
                scenario_assets=scenario_assets,
            )
            phases.append(p)

        robot_display = robot_key.replace("_", " ").title()
        return {
            "title": f"{robot_display} — {scenario_key.title()} Pipeline",
            "robot": robot_display,
            "scenario": scenario_key,
            "phases": phases,
            "source": "template",
            "total_phases": len(phases),
        }
