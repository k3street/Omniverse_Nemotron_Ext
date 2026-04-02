import omni.usd

class PrimInspector:
    """
    Native Omniverse Extension logic (Isaac 5.1/6.0).
    Has direct access to Omniverse API and `pxr.Usd` namespace.
    Translates the active Stage/Selection into a JSON string to pass over standard HTTP 
    to our heavy `isaac_assist_service` analysis block.
    """
    
    def __init__(self):
        self.context = omni.usd.get_context()
        
    def collect_selection_payload(self):
        """ 
        Reads currently selected items in the Viewport, looks up their Schemas,
        and builds the JSON payload expected by `POST /api/v1/analysis/run`.
        """
        stage = self.context.get_stage()
        selection = self.context.get_selection().get_selected_prim_paths()
        
        prims_data = []
        for path in selection:
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                continue
                
            # Serialize basic data
            node = {
                "path": str(path),
                "type": prim.GetTypeName(),
                "schemas": []
            }
            
            # Extract applied schemas natively
            for schema_name in prim.GetAppliedSchemas():
                node["schemas"].append(schema_name)
                
            prims_data.append(node)
            
        return {
            "scope": "selection",
            "prims": prims_data
        }
