"""Fixture for `# noqa: audit-QX` suppression support.

Every line below has the same kind of code that another fixture
already proves is normally flagged. With `# noqa: audit-QX` on that
same line, the audit must NOT flag it.
"""
from datetime import datetime
import subprocess


def suppressed_utcnow():
    # `# noqa: audit-Q3` — suppress the deprecated-call audit on this line
    return datetime.utcnow()  # noqa: audit-Q3


def suppressed_eval():
    return eval("1 + 1")  # noqa: audit-Q9


def suppressed_shell_true():
    return subprocess.run("ls", shell=True)  # noqa: audit-Q10


def multi_suppressed():
    # Multi-check suppression on same line
    return datetime.utcnow(), eval("1")  # noqa: audit-Q3, audit-Q9


async def suppressed_blocking_io():
    import time
    time.sleep(1)  # noqa: audit-Q12
    return {"success": True}
