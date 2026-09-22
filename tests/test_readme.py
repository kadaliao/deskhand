"""The front page example is checked, because nothing else checks it.

The reference implementation this project learned from documented an environment
variable and a set of actions on its website that the code did not have, within two
commits of adding the site. A README example is the most-read code in a repository
and the least likely to be run, so it gets a cheap guard: it has to parse, its
imports have to resolve, the names it uses have to exist, and the commands it shows
have to be real commands.
"""

from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"


def readme() -> str:
    return README.read_text(encoding="utf-8")


def first_python_block() -> str:
    match = re.search(r"```python\n(.*?)```", readme(), re.S)
    assert match is not None, "README.md has no python block any more"
    return match.group(1)


def run_imports(block: str) -> dict[str, object]:
    namespace: dict[str, object] = {}
    for node in ast.parse(block).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            statement = ast.Module(body=[node], type_ignores=[])
            exec(compile(statement, "<README>", "exec"), namespace)
    return namespace


class TestREADMEExample:
    def test_it_is_still_there(self) -> None:
        assert "from deskhand import" in first_python_block()

    def test_it_parses(self) -> None:
        ast.parse(first_python_block())

    def test_every_import_resolves(self) -> None:
        try:
            run_imports(first_python_block())
        except ImportError as exc:  # pragma: no cover - only on a broken README
            pytest.fail(f"README imports something that does not exist: {exc}")

    def test_every_name_it_uses_comes_from_somewhere_real(self) -> None:
        block = first_python_block()
        namespace = run_imports(block)
        tree = ast.parse(block)
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        # Names bound inside the example itself: assignments, lambda and function
        # arguments, comprehension targets, defined functions.
        bound = {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
        bound |= {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        bound |= {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        missing = sorted(
            n for n in used if n not in namespace and n not in bound and not hasattr(builtins, n)
        )
        assert missing == [], f"README example uses names that do not exist: {missing}"


class TestREADMECommands:
    """Every `deskhand <command>` a reader is told to run must be a real command."""

    def commands(self) -> list[str]:
        # The invocation form only: a command followed by a flag, a trailing comment,
        # or the end of the line. "deskhand does not ..." in prose is not a command.
        return sorted(set(re.findall(r"deskhand (\w+)(?=\s+(?:--|#)|[ \t]*$)", readme(), re.M)))

    def test_the_readme_shows_commands(self) -> None:
        assert self.commands()

    def test_each_one_exists(self) -> None:
        from deskhand.cli import main

        for command in self.commands():
            with pytest.raises(SystemExit) as caught:
                main([command, "--help"])
            assert caught.value.code == 0, f"'{command}' is not a real deskhand command"
