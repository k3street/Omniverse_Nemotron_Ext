# cuRobo install — reproducerbara steg

cuRobo i isaac_lab_env är en library-only install och saknar två
saker som måste finnas för att `MotionPlannerCfg.create(robot='franka.yml')`
ska fungera:

1. **cuda-core-runtime** — `cuda.core` modulen som cuRobo's default kernel-backend behöver
2. **content/ directory** — `configs/` (franka.yml + task YAMLs) + `assets/` (URDF + meshes)

Utan dessa: `ModuleNotFoundError: No module named 'cuda'` + `KeyError 'kinematics'`.

## Steg 1 — Installera cuda-core

```bash
/home/anton/miniconda3/envs/isaac_lab_env/bin/pip install 'cuda-core[cu12]'
```

Installerar `cuda-core`, `cuda-bindings`, `cuda-pathfinder`,
`nvidia-cuda-nvcc-cu12`, `nvidia-nvfatbin-cu12`. Storlek ~80 MB.

## Steg 2 — Synka content/ från NVlabs GitHub

```bash
cd /tmp
rm -rf curobo-clone && mkdir -p curobo-clone && cd curobo-clone
git init -q
git remote add origin https://github.com/NVlabs/curobo.git
git config core.sparseCheckout true
echo "curobo/content/" > .git/info/sparse-checkout
git fetch --depth=1 origin main
git checkout FETCH_HEAD

rsync -a curobo/content/configs \
  /home/anton/miniconda3/envs/isaac_lab_env/lib/python3.11/site-packages/curobo/content/
rsync -a curobo/content/assets \
  /home/anton/miniconda3/envs/isaac_lab_env/lib/python3.11/site-packages/curobo/content/
```

Totalt ~84 MB. Inkluderar `franka.yml`, `ur10e.yml`, `unitree_g1.yml`,
default task YAMLs (`ik/lbfgs_ik.yml`, `trajopt/lbfgs_bspline_trajopt.yml`,
`metrics_base.yml`, etc.), Franka URDF + meshes, UR10e, etc.

## Verifiering

```bash
cd /home/anton/projects/Omniverse_Nemotron_Ext
python -c "
import httpx
code = '''
import sys, importlib
sp = \"/home/anton/miniconda3/envs/isaac_lab_env/lib/python3.11/site-packages\"
if sp in sys.path: sys.path.remove(sp)
sys.path.insert(0, sp)
importlib.invalidate_caches()
import warp as wp
_orig = wp.func
def _p(f=None, *, name=None, module=None, **_kw):
    return _orig(f, name=name) if f is not None else _orig
wp.func = _p
from curobo.motion_planner import MotionPlanner, MotionPlannerCfg
cfg = MotionPlannerCfg.create(robot=\"franka.yml\", use_cuda_graph=False, self_collision_check=True)
planner = MotionPlanner(cfg)
print(\"OK joint_names:\", list(planner.joint_names))
'''
r = httpx.post('http://127.0.0.1:8001/exec_sync', json={'code': code, 'timeout': 30}, timeout=35)
print(r.json().get('output'))
"
```

Ska printa: `OK joint_names: ['panda_joint1', ..., 'panda_joint7']`

## Status efter install (verifierat 2026-04-21)

- **env-bridge**: `sys.path.insert` + `importlib.invalidate_caches()` (I-29)
- **Warp-kompat**: `wp.func` monkey-patch för `module=` kwarg (I-28)
- **cuda-core**: installerat (steg 1)
- **content/franka.yml**: synkad (steg 2)
- **MotionPlanner**: bygger, `plan_pose` returnerar trajektorier i ~0.5s efter CUDA graph-warmup
- **setup_pick_place_controller(target_source='curobo')**: installerar, planerar 5 segment, kör dem, hand når drop-target inom 3mm
- **Grip-resultat**: **0/4** (I-35) — planen saknar kuber som obstacles → armens svep slår kuber av bandet

## Nästa steg för 4/4 leverans

Lägg till scene obstacles i `MotionPlannerCfg.create(scene_model=...)`:

```python
from curobo.scene import Scene, Cuboid
# Build world_config from USD prim bounding boxes
obstacles = []
for prim_path in ['/World/ConveyorBelt', '/World/Bin', '/World/Table',
                   '/World/Cube_1', '/World/Cube_2', '/World/Cube_3', '/World/Cube_4']:
    # ComputeWorldBound → Cuboid with dims + pose
    ...
world_cfg = Scene(cuboid=obstacles)
```

Med dessa skulle cuRobo planera runt kuber istället för att svepa genom.
FAS 6d+ framtida arbete.
