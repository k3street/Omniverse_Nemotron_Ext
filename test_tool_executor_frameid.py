from service.isaac_assist_service.chat.tools.tool_executor import _gen_create_graph

args = {
    "graph_path": "/World/Graphs/CarterLidar",
    "template": "ros2_lidar",
    "topic": "/scan",
    "lidar_path": "/World/NovaCarter/RPLidar_S2E",
    "root_prim": "/World/NovaCarter",
    "target_prims": "sim_lidar"
}
gen_code = _gen_create_graph(args)
print(gen_code)
