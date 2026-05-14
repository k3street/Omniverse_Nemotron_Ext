#!/bin/bash
# Symlink cuRobo's bundled URDFs into the paths task prompts reference.
#
# Several QA tasks (M-01, A-01, A-02, A-03, L-XX) reference URDF paths in
# the user's home directory ("~/robots/franka_panda/panda.urdf",
# "~/projects/myarm/myarm.urdf", etc). Without these files actually
# existing locally, list_local_files cannot discover them and the agent
# is forced to ask the user.
#
# This script sets up the fixtures by symlinking from cuRobo's bundled
# robot library to the task-prompt paths. Idempotent: re-runs are safe.

set -euo pipefail

CUROBO_ROOT="/home/anton/miniconda3/envs/isaac_lab_env/lib/python3.11/site-packages/curobo/content/assets/robot"

if [[ ! -d "$CUROBO_ROOT" ]]; then
    echo "ERROR: cuRobo content not found at $CUROBO_ROOT"
    echo "  Activate the isaac_lab_env conda environment first."
    exit 1
fi

mkdir -p ~/robots/franka_panda
ln -sf "$CUROBO_ROOT/franka_description/franka_panda.urdf" ~/robots/franka_panda/panda.urdf
ln -sf "$CUROBO_ROOT/franka_description/franka_panda.urdf" ~/robots/franka_panda/franka_panda.urdf
ln -sf "$CUROBO_ROOT/franka_description" ~/robots/franka_panda/franka_description
echo "  ~/robots/franka_panda/panda.urdf + franka_panda.urdf  → cuRobo Franka"

mkdir -p ~/robots/ur10e
ln -sf "$CUROBO_ROOT/ur_description/ur10e.urdf" ~/robots/ur10e/ur10e.urdf
ln -sf "$CUROBO_ROOT/ur_description" ~/robots/ur10e/ur_description
echo "  ~/robots/ur10e/ur10e.urdf  → cuRobo UR10e"

mkdir -p ~/projects/myarm
ln -sf "$CUROBO_ROOT/simple/simple_mimic_robot.urdf" ~/projects/myarm/myarm.urdf
ln -sf "$CUROBO_ROOT/simple" ~/projects/myarm/simple
echo "  ~/projects/myarm/myarm.urdf  → cuRobo simple_mimic_robot (4-DOF stand-in)"

mkdir -p ~/Downloads
ln -sf "$CUROBO_ROOT/ur_description/ur10e.urdf" ~/Downloads/my_ur10.urdf 2>/dev/null || true
echo "  ~/Downloads/my_ur10.urdf  → cuRobo UR10e (for AD-05 path-discovery)"

echo
echo "Done. list_local_files / catalog_search now discover task fixtures."
