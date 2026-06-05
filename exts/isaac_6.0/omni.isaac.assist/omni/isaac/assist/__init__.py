try:
    from .extension import IsaacAssistExtension
except ModuleNotFoundError as exc:
    # Kit-only modules such as omni.ext are unavailable in ordinary pytest
    # imports; keep submodules importable for bridge/unit tests outside Kit.
    if exc.name != "omni.ext":
        raise
    IsaacAssistExtension = None
