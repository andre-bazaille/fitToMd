from __future__ import annotations

import ast
from pathlib import Path


def test_domain_does_not_depend_on_application_or_infrastructure() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    domain_root = repository_root / "src" / "fit_to_md" / "domain"
    forbidden_imports: list[str] = []

    for source_file in domain_root.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_names = [node.module]
            else:
                continue

            for imported_name in imported_names:
                if imported_name.startswith(("fit_to_md.application", "fit_to_md.infrastructure")):
                    forbidden_imports.append(f"{source_file.relative_to(repository_root)}: {imported_name}")

    assert forbidden_imports == []
