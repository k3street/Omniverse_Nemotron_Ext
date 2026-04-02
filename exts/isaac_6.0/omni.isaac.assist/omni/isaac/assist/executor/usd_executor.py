import omni.usd
import omni.kit.commands
import logging

class UsdPatchExecutor:
    """Runs in the extension process for direct USD/pxr execution."""
    
    def __init__(self):
        self.context = omni.usd.get_context()
        self.logger = logging.getLogger(__name__)

    def apply_usd_action(self, action_dict: dict) -> bool:
        """
        Receives a `PatchAction` payload from the FastAPI backend and physically changes
        the active Omniverse Stage.
        """
        stage = self.context.get_stage()
        if not stage:
            return False

        target_path = action_dict.get("target_path", "")
        action_type = action_dict.get("action_type", "")
        new_value = action_dict.get("new_value", "")
        
        # Wrapped in omni.kit.commands so the user can literally hit `CTRL+Z` in the UI to undo the AI!
        try:
            if action_type == "add_schema":
                omni.kit.commands.execute('ApplyAPISchema',
                    api=new_value,
                    prims=[omni.usd.get_context().get_stage().GetPrimAtPath(target_path)]
                )
                self.logger.info(f"AI applied Schema {new_value} to {target_path}")
                return True
                
            elif action_type == "set_property":
                # Mock structure for property manipulation
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to execute USD patch: {e}")
            return False
            
        return False
