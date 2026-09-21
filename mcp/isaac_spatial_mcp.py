#!/usr/bin/env python3
"""Isaac spatial-awareness MCP server (dependency-free stdio JSON-RPC).

Purpose: kill the frame/orientation bug class that costs both agent
threads full sim cycles — unlabeled quaternions, wxyz/xyzw mixups,
base-vs-env frames, hand-derived mirrors — by making spatial facts
queryable instead of inferred.

Two tool tiers:
  math tier   — always available, pure python: quaternion conversion,
                pose compose/invert/between.  EVERY quaternion in and
                out is explicitly labeled wxyz or xyzw.
  sim tier    — proxied to the Isaac Assist in-Kit FastAPI service
                (ISAAC_ASSIST_URL, default http://localhost:8899):
                get_pose, screenshot, draw_axes.  Read-only except
                debug gizmos; never a second Kit process.

Register (Claude Code):
  claude mcp add isaac-spatial -- python3 /home/kimate/Omniverse_Nemotron_Ext/mcp/isaac_spatial_mcp.py
"""

import base64
import json
import math
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"


def _service_url() -> str:
    """Kit RPC base URL: env override, then the port file the RPC server
    writes on bind, then the default 8001."""
    override = os.environ.get("ISAAC_ASSIST_URL")
    if override:
        return override.rstrip("/")
    try:
        port = int(open("/tmp/isaac_assist_rpc_port").read().strip())
    except (OSError, ValueError):
        port = 8001
    return f"http://127.0.0.1:{port}"


# ----------------------------------------------------------------- math
def _quat_to_wxyz(values, convention):
    q = [float(v) for v in values]
    if len(q) != 4:
        raise ValueError("quaternion must have 4 values")
    if convention == "xyzw":
        q = [q[3], q[0], q[1], q[2]]
    elif convention != "wxyz":
        raise ValueError("convention must be 'wxyz' or 'xyzw'")
    n = math.sqrt(sum(v * v for v in q))
    if n < 1e-12:
        raise ValueError("zero quaternion")
    return [v / n for v in q]


def _wxyz_to_matrix(q):
    w, x, y, z = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _matrix_to_wxyz(m):
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return [0.25 * s, (m[2][1] - m[1][2]) / s,
                (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s]
    if m[0][0] >= m[1][1] and m[0][0] >= m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        return [(m[2][1] - m[1][2]) / s, 0.25 * s,
                (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s]
    if m[1][1] >= m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        return [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s,
                0.25 * s, (m[1][2] + m[2][1]) / s]
    s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
    return [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s,
            (m[1][2] + m[2][1]) / s, 0.25 * s]


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _mat_t(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _mat_vec(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def _rpy_from_matrix(m):
    sy = math.sqrt(m[0][0] ** 2 + m[1][0] ** 2)
    if sy > 1e-9:
        return [math.atan2(m[2][1], m[2][2]),
                math.atan2(-m[2][0], sy),
                math.atan2(m[1][0], m[0][0])]
    return [math.atan2(-m[1][2], m[1][1]), math.atan2(-m[2][0], sy), 0.0]


def _pose_in(pose):
    position = [float(v) for v in pose["position"]]
    quat = _quat_to_wxyz(pose["quaternion"],
                         pose.get("convention", "wxyz"))
    return position, quat


def _pose_out(position, quat_wxyz):
    m = _wxyz_to_matrix(quat_wxyz)
    return {
        "position": [round(v, 6) for v in position],
        "quaternion_wxyz": [round(v, 6) for v in quat_wxyz],
        "quaternion_xyzw": [round(v, 6) for v in
                            [quat_wxyz[1], quat_wxyz[2], quat_wxyz[3],
                             quat_wxyz[0]]],
        "rpy_rad": [round(v, 6) for v in _rpy_from_matrix(m)],
        "rotation_matrix_rows": [[round(v, 6) for v in row] for row in m],
    }


def tool_quat_convert(args):
    q = _quat_to_wxyz(args["quaternion"], args.get("convention", "wxyz"))
    return _pose_out([0.0, 0.0, 0.0], q)


def tool_pose_compose(args):
    pa, qa = _pose_in(args["parent_T_a"])
    pb, qb = _pose_in(args["a_T_b"])
    ma, mb = _wxyz_to_matrix(qa), _wxyz_to_matrix(qb)
    m = _mat_mul(ma, mb)
    p = [pa[i] + _mat_vec(ma, pb)[i] for i in range(3)]
    return _pose_out(p, _matrix_to_wxyz(m))


def tool_pose_invert(args):
    p, q = _pose_in(args["pose"])
    m = _wxyz_to_matrix(q)
    mt = _mat_t(m)
    pi = [-v for v in _mat_vec(mt, p)]
    return _pose_out(pi, _matrix_to_wxyz(mt))


def tool_pose_between(args):
    result = tool_pose_invert({"pose": args["a"]})
    inv = {"position": result["position"],
           "quaternion": result["quaternion_wxyz"], "convention": "wxyz"}
    out = tool_pose_compose({"parent_T_a": inv, "a_T_b": args["b"]})
    p = out["position"]
    angle = 2.0 * math.acos(min(1.0, abs(out["quaternion_wxyz"][0])))
    out["distance_m"] = round(math.sqrt(sum(v * v for v in p)), 6)
    out["rotation_angle_rad"] = round(angle, 6)
    out["rotation_angle_deg"] = round(math.degrees(angle), 3)
    return out


# ------------------------------------------------------------------ sim
def _service(path, payload=None, timeout=20.0, method=None):
    base = _service_url()
    url = f"{base}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(
            f"Isaac Assist Kit RPC unreachable at {base} ({error}); is a "
            "Kit session running with the omni.isaac.assist extension "
            "enabled?") from error


def tool_get_pose(args):
    return _service("/get_pose", {
        "prim_path": args["prim_path"],
        "in_frame": args.get("in_frame", "world"),
        "prefer": args.get("prefer", "fabric"),
    })


def tool_grasp_gap(args):
    return _service("/grasp_gap", {
        "object": args["object"],
        "hand_frame": args["hand_frame"],
        "digit_tips": args.get("digit_tips", []),
        "object_half_extents": args.get("object_half_extents"),
        "prefer": args.get("prefer", "fabric"),
    })


def tool_bounds(args):
    return _service("/bounds", {
        "prim_path": args["prim_path"],
        "in_frame": args.get("in_frame", "world"),
        "prefer": args.get("prefer", "fabric"),
        "include_proxy": bool(args.get("include_proxy", False)),
    })


def tool_get_contacts(args):
    return _service("/contacts", {
        "filter": args.get("filter", ""),
    }, timeout=25.0)


def tool_set_camera_pose(args):
    return _service("/set_camera_pose", {
        "eye": args["eye"],
        "target": args["target"],
    })


def tool_list_prims(args):
    from urllib.parse import urlencode
    query = {}
    if args.get("under_path"):
        query["under_path"] = args["under_path"]
    if args.get("filter_type"):
        query["filter_type"] = args["filter_type"]
    suffix = f"?{urlencode(query)}" if query else ""
    result = _service(f"/list_prims{suffix}")
    prims = result.get("prims", [])
    limit = int(args.get("limit", 80))
    if len(prims) > limit:
        result["prims"] = prims[:limit]
        result["truncated_to"] = limit
    return result


def tool_draw_axes(args):
    return _service("/draw_axes", {
        "position": args["position"],
        "quaternion": args.get("quaternion", [1, 0, 0, 0]),
        "convention": args.get("convention", "wxyz"),
        "label": args.get("label", ""),
        "scale": float(args.get("scale", 0.1)),
    })


def tool_screenshot(args):
    max_dim = int(args.get("max_dim", 1280))
    result = _service(f"/capture?max_dim={max_dim}", timeout=40.0)
    if "image_b64" not in result:
        raise RuntimeError(f"capture failed: {result}")
    return {"_image_png_base64": result["image_b64"]}


TOOLS = {
    "quat_convert": (
        tool_quat_convert,
        "Convert a quaternion between conventions; returns the same "
        "rotation as labeled wxyz, xyzw, rpy, and a rotation matrix.",
        {"type": "object", "properties": {
            "quaternion": {"type": "array", "items": {"type": "number"}},
            "convention": {"type": "string", "enum": ["wxyz", "xyzw"]}},
         "required": ["quaternion", "convention"]}),
    "pose_compose": (
        tool_pose_compose,
        "Compose parent_T_a * a_T_b. Poses are {position:[x,y,z], "
        "quaternion:[..], convention:'wxyz'|'xyzw'}.",
        {"type": "object", "properties": {
            "parent_T_a": {"type": "object"},
            "a_T_b": {"type": "object"}},
         "required": ["parent_T_a", "a_T_b"]}),
    "pose_invert": (
        tool_pose_invert,
        "Invert a pose (returns b_T_a given a_T_b).",
        {"type": "object", "properties": {"pose": {"type": "object"}},
         "required": ["pose"]}),
    "pose_between": (
        tool_pose_between,
        "Relative pose a_T_b from two poses in the same frame, plus "
        "distance and rotation angle.",
        {"type": "object", "properties": {
            "a": {"type": "object"}, "b": {"type": "object"}},
         "required": ["a", "b"]}),
    "get_pose": (
        tool_get_pose,
        "LIVE SIM: pose of a prim with labeled quaternion conventions. "
        "Reads the live Fabric transform (what physics wrote this frame) "
        "with USD-stage fallback; 'source' in the reply states which.",
        {"type": "object", "properties": {
            "prim_path": {"type": "string"},
            "in_frame": {"type": "string",
                         "description": "world (default) or a prim path"},
            "prefer": {"type": "string", "enum": ["fabric", "usd"]}},
         "required": ["prim_path"]}),
    "grasp_gap": (
        tool_grasp_gap,
        "LIVE SIM: pre-grasp geometry report — object pose in the hand "
        "frame, each digit tip's vector/distance to the object, aperture "
        "centroid offset, and per-axis box clearances when half extents "
        "are given. Measures what screenshot-guessing cannot.",
        {"type": "object", "properties": {
            "object": {"type": "string"},
            "hand_frame": {"type": "string"},
            "digit_tips": {"type": "array", "items": {"type": "string"}},
            "object_half_extents": {"type": "array",
                                    "items": {"type": "number"}},
            "prefer": {"type": "string", "enum": ["fabric", "usd"]}},
         "required": ["object", "hand_frame", "digit_tips"]}),
    "bounds": (
        tool_bounds,
        "LIVE SIM: geometric size of a prim with its live pose — half "
        "extents in the prim's own frame (covering its descendants too), "
        "plus a world-axis-aligned enclosing box and the volume ratio that "
        "approximation costs. Extents are read "
        "from USD geometry with ancestor transforms ignored while the "
        "centre comes from Fabric, because a USD world bound under Isaac "
        "Lab is the authoring-time box wherever the object started. "
        "list_prims returns paths and types only; this is where bounds "
        "come from.",
        {"type": "object", "properties": {
            "prim_path": {"type": "string"},
            "in_frame": {"type": "string",
                         "description": "world (default) or a prim path"},
            "prefer": {"type": "string", "enum": ["fabric", "usd"]},
            "include_proxy": {"type": "boolean"}},
         "required": ["prim_path"]}),
    "get_contacts": (
        tool_get_contacts,
        "LIVE SIM: PhysX contact report aggregated per body pair "
        "(points, total impulse, min separation). Only pairs carrying "
        "the contact-report API appear (Isaac Lab contact sensors apply "
        "it to tracked bodies).",
        {"type": "object", "properties": {
            "filter": {"type": "string",
                       "description": "substring filter on body paths"}}}),
    "set_camera_pose": (
        tool_set_camera_pose,
        "LIVE SIM: place the viewport camera at eye looking at target — "
        "makes captures deterministic across sessions.",
        {"type": "object", "properties": {
            "eye": {"type": "array", "items": {"type": "number"}},
            "target": {"type": "array", "items": {"type": "number"}}},
         "required": ["eye", "target"]}),
    "list_prims": (
        tool_list_prims,
        "LIVE SIM: list prim paths, filterable by subtree and USD type.",
        {"type": "object", "properties": {
            "under_path": {"type": "string"},
            "filter_type": {"type": "string"},
            "limit": {"type": "integer"}}}),
    "draw_axes": (
        tool_draw_axes,
        "LIVE SIM: draw a labeled RGB axes gizmo at a pose in the "
        "viewport (debug-only mutation).",
        {"type": "object", "properties": {
            "position": {"type": "array", "items": {"type": "number"}},
            "quaternion": {"type": "array", "items": {"type": "number"}},
            "convention": {"type": "string", "enum": ["wxyz", "xyzw"]},
            "label": {"type": "string"},
            "scale": {"type": "number"}},
         "required": ["position"]}),
    "screenshot": (
        tool_screenshot,
        "LIVE SIM: capture the viewport as a PNG image — look at the "
        "scene instead of inferring it from numbers.",
        {"type": "object", "properties": {
            "max_dim": {"type": "integer"}}}),
}


# ------------------------------------------------------------ MCP stdio
def _respond(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if method == "initialize":
            _respond(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "isaac-spatial", "version": "0.1.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _respond(msg_id, {"tools": [
                {"name": name, "description": desc, "inputSchema": schema}
                for name, (_, desc, schema) in TOOLS.items()]})
        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            if name not in TOOLS:
                _respond(msg_id, error={"code": -32602,
                                        "message": f"unknown tool {name}"})
                continue
            try:
                result = TOOLS[name][0](params.get("arguments", {}))
                if "_image_png_base64" in result:
                    content = [{"type": "image",
                                "data": result["_image_png_base64"],
                                "mimeType": "image/png"}]
                else:
                    content = [{"type": "text",
                                "text": json.dumps(result, indent=1)}]
                _respond(msg_id, {"content": content})
            except Exception as error:
                _respond(msg_id, {"content": [
                    {"type": "text", "text": f"error: {error}"}],
                    "isError": True})
        elif msg_id is not None:
            _respond(msg_id, error={"code": -32601,
                                    "message": f"unknown method {method}"})


if __name__ == "__main__":
    main()
