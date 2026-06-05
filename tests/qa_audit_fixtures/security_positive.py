"""POSITIVE fixtures for Q9 (eval/exec) and Q10 (shell=True)."""
import subprocess


def uses_eval():
    """eval is forbidden in non-test code."""
    return eval("1 + 1")  # AUDIT_EXPECT: Q9 hit


def uses_exec():
    """exec is forbidden in non-test code."""
    exec("x = 1")  # AUDIT_EXPECT: Q9 hit
    return None


def uses_shell_true():
    """shell=True is forbidden."""
    return subprocess.run("ls /tmp", shell=True)  # AUDIT_EXPECT: Q10 hit


def uses_shell_true_with_check():
    """shell=True even when paired with check= is still forbidden."""
    return subprocess.run("echo hi", shell=True, check=True)  # AUDIT_EXPECT: Q10 hit
