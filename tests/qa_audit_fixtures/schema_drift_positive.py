"""POSITIVE fixture for Q14 schema drift — tools_schemas declares names
the audit should find unbound when no handler exists. The fixture file
itself can't simulate the full schema-vs-handler pairing — so this is
a sentinel module documenting the EXPECTED behaviour.

The actual Q14 check reads `chat/tools/tool_schemas.py` directly and
scans the entire service for handler definitions. A meaningful unit
test would patch `tool_schemas.py` content — out of scope here. The
audit fixture-test instead asserts the heuristic recognises all
documented binding patterns (in schema_drift_negative.py).
"""

# Sentinel — see schema_drift_negative.py for the actual binding-pattern
# verification.
