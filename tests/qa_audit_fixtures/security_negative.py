"""NEGATIVE fixtures for Q9 (eval/exec) and Q10 (shell=True) — must NOT flag."""
import ast
import subprocess


def uses_ast_literal_eval():
    """ast.literal_eval is safe — NOT real eval()."""
    return ast.literal_eval("[1, 2, 3]")


def uses_subprocess_list_form():
    """List form is safe — no shell."""
    return subprocess.run(["ls", "/tmp"], check=True)


def uses_subprocess_shell_false():
    """shell=False is the default — must not be flagged."""
    return subprocess.run(["echo", "hi"], shell=False)


def name_collision_with_eval():
    """A local 'eval' variable that shadows the builtin — NOT a call to builtin eval.

    The audit relies on `ast.Call.func.id == 'eval'` which would match
    this — and it's a true positive (we're calling something named 'eval'
    even if shadowed). Document this as an accepted edge case:
    naming a local 'eval' is suspicious anyway.
    """
    # No actual call to builtin eval here — just defining a function reference
    my_eval_lookalike = lambda x: x  # noqa: E731
    return my_eval_lookalike(42)
