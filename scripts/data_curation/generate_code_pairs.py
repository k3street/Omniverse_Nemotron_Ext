import json
import os

def generate_synthetic_code_pairs(output_file: str):
    """
    Generates synthetic system/instruction/response pairings to teach Nemotron how to 
    write Omniverse Python SDK code.
    """
    print("Generating code instruction pairs...")
    
    # In a real workflow, these would be curated from Isaac Sim examples, 
    # github repos, and Kit extension boilerplates.
    synthetic_code_snippets = [
        {
            "instruction": "Write a Python script to create a simple physics scene with a ground plane and a falling cube in Isaac Sim.",
            "input": "",
            "output": \"\"\"import omni.usd
from pxr import UsdGeom, Gf

def create_physics_scene():
    stage = omni.usd.get_context().get_stage()
    
    # Create Ground Plane
    omni.kit.commands.execute('AddGroundPlaneCommand',
        stage=stage,
        planePath='/World/GroundPlane',
        axis='Z',
        size=1000.0,
        position=Gf.Vec3f(0.0),
        color=Gf.Vec3f(0.5))
        
    # Create Cube
    omni.kit.commands.execute('CreateMeshPrimWithDefaultXform',
        prim_type='Cube',
        prim_path='/World/Cube')
    cube_prim = stage.GetPrimAtPath('/World/Cube')
    
    # Move cube up
    xform = UsdGeom.Xformable(cube_prim)
    xform.AddTranslateOp().Set(Gf.Vec3f(0, 0, 500))
    \"\"\"
        },
        {
            "instruction": "How do I subscribe to selection changes in an Omniverse extension?",
            "input": "",
            "output": \"\"\"import omni.usd

class SelectionObserver:
    def __init__(self):
        self._events = omni.usd.get_context().get_stage_event_stream()
        self._sub = self._events.create_subscription_to_pop(
            self._on_stage_event, name="MySelectionObserver"
        )
        
    def _on_stage_event(self, event):
        if event.type == int(omni.usd.StageEventType.SELECTION_CHANGED):
            selection = omni.usd.get_context().get_selection().get_selected_prim_paths()
            print(f"Selection changed: {selection}")
            
    def destroy(self):
        self._sub = None
        self._events = None
\"\"\"
        }
    ]
    
    with open(output_file, "w", encoding="utf-8") as out:
        for entry in synthetic_code_snippets:
            out.write(json.dumps(entry) + "\n")
            
    print(f"Generated {len(synthetic_code_snippets)} synthetic code pairs in {output_file}")

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    generate_synthetic_code_pairs("data/code_finetune_dataset.jsonl")
