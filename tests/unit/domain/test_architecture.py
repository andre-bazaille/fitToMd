import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

_PROJECT_PACKAGE = "fit_to_md"
_DOMAIN_PACKAGE = f"{_PROJECT_PACKAGE}.domain"
_APPLICATION_PACKAGE = f"{_PROJECT_PACKAGE}.application"
_DOMAIN_CONTEXTS = frozenset({"activity", "privacy", "reporting"})
_ALLOWED_CONTEXT_DEPENDENCIES = frozenset({("reporting", "activity")})


def _is_module_or_child(imported_name: str, package_name: str) -> bool:
    return imported_name == package_name or imported_name.startswith(f"{package_name}.")


def _domain_context(module_name: str) -> str | None:
    prefix = f"{_DOMAIN_PACKAGE}."
    if not module_name.startswith(prefix):
        return None

    context = module_name.removeprefix(prefix).partition(".")[0]
    return context if context in _DOMAIN_CONTEXTS else None


def _imported_names(
    node: ast.Import | ast.ImportFrom,
    *,
    module_name: str,
    is_package: bool,
) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)

    package_name = module_name if is_package else module_name.rpartition(".")[0]
    if node.level:
        relative_name = f"{'.' * node.level}{node.module or ''}"
        imported_from = resolve_name(relative_name, package_name)
    elif node.module is not None:
        imported_from = node.module
    else:
        return ()

    imported_names = [imported_from]
    if imported_from in {_PROJECT_PACKAGE, _DOMAIN_PACKAGE} or node.module is None:
        imported_names.extend(
            f"{imported_from}.{alias.name}" for alias in node.names if alias.name != "*"
        )
    return tuple(imported_names)


def _dependency_violation(importer: str, imported_name: str) -> str | None:
    if _is_module_or_child(importer, _DOMAIN_PACKAGE):
        imported_root = imported_name.partition(".")[0]
        if imported_root in sys.stdlib_module_names:
            return None
        if imported_name == _PROJECT_PACKAGE:
            return None
        if not _is_module_or_child(imported_name, _DOMAIN_PACKAGE):
            if imported_root == _PROJECT_PACKAGE:
                return "domain code may depend only on domain modules"
            return "domain code may depend only on the standard library"

        importer_context = _domain_context(importer)
        imported_context = _domain_context(imported_name)
        if (
            importer_context is not None
            and imported_context is not None
            and importer_context != imported_context
            and (importer_context, imported_context)
            not in _ALLOWED_CONTEXT_DEPENDENCIES
        ):
            return (
                f"the {importer_context} context may not depend on "
                f"the {imported_context} context"
            )
        return None

    if _is_module_or_child(importer, _APPLICATION_PACKAGE):
        if imported_name == _PROJECT_PACKAGE or any(
            _is_module_or_child(imported_name, allowed_package)
            for allowed_package in (_APPLICATION_PACKAGE, _DOMAIN_PACKAGE)
        ):
            return None
        if imported_name.partition(".")[0] == _PROJECT_PACKAGE:
            return "application code may depend only on application and domain modules"

    return None


def _architecture_violations(
    source: str,
    *,
    module_name: str,
    source_name: str = "snippet.py",
    is_package: bool = False,
) -> list[str]:
    tree = ast.parse(source, filename=source_name)
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for imported_name in _imported_names(
            node, module_name=module_name, is_package=is_package
        ):
            reason = _dependency_violation(module_name, imported_name)
            if reason is not None:
                violations.append(
                    f"{source_name}:{node.lineno}: {imported_name}: {reason}"
                )

    return violations


def _module_name(source_file: Path, package_root: Path) -> str:
    relative_parts = source_file.relative_to(package_root).with_suffix("").parts
    if relative_parts[-1] == "__init__":
        relative_parts = relative_parts[:-1]
    return ".".join((_PROJECT_PACKAGE, *relative_parts))


@pytest.mark.parametrize(
    ("module_name", "source", "is_package"),
    (
        (
            "fit_to_md.domain.activity.entities",
            "from dataclasses import dataclass\nfrom .values import Value\n",
            False,
        ),
        (
            "fit_to_md.domain.activity",
            "from .entities import Activity\n",
            True,
        ),
        (
            "fit_to_md.domain.reporting.services",
            "from fit_to_md.domain.activity.entities import Activity\n",
            False,
        ),
        (
            "fit_to_md.domain.reporting.services",
            "from ..activity.entities import Activity\n",
            False,
        ),
        (
            "fit_to_md.application.use_cases.generate",
            "from fit_to_md.domain.reporting.ports import ReportRenderer\n",
            False,
        ),
    ),
)
def test_checker_allows_supported_dependencies(
    module_name: str, source: str, is_package: bool
) -> None:
    assert (
        _architecture_violations(source, module_name=module_name, is_package=is_package)
        == []
    )


@pytest.mark.parametrize(
    ("module_name", "source", "expected_import"),
    (
        (
            "fit_to_md.domain.reporting.services",
            "from fit_to_md.application.use_cases import GenerateReport\n",
            "fit_to_md.application.use_cases",
        ),
        (
            "fit_to_md.domain.reporting.services",
            "from ...infrastructure.fitdecode import Decoder\n",
            "fit_to_md.infrastructure.fitdecode",
        ),
        (
            "fit_to_md.domain.reporting.services",
            "from ... import cli\n",
            "fit_to_md.cli",
        ),
        (
            "fit_to_md.domain.reporting.services",
            "import fitdecode\n",
            "fitdecode",
        ),
        (
            "fit_to_md.domain.activity.entities",
            "from ..reporting.entities import FitReport\n",
            "fit_to_md.domain.reporting.entities",
        ),
        (
            "fit_to_md.domain.privacy.entities",
            "from ..activity.entities import Activity\n",
            "fit_to_md.domain.activity.entities",
        ),
        (
            "fit_to_md.domain.reporting.services",
            "from ..privacy.entities import FitSanitizationPolicy\n",
            "fit_to_md.domain.privacy.entities",
        ),
        (
            "fit_to_md.application.use_cases.generate",
            "from fit_to_md.infrastructure.markdown import MarkdownRenderer\n",
            "fit_to_md.infrastructure.markdown",
        ),
        (
            "fit_to_md.application.use_cases.generate",
            "from ...infrastructure.fitdecode import Decoder\n",
            "fit_to_md.infrastructure.fitdecode",
        ),
    ),
)
def test_checker_rejects_forbidden_dependencies(
    module_name: str, source: str, expected_import: str
) -> None:
    violations = _architecture_violations(source, module_name=module_name)

    assert len(violations) == 1
    assert expected_import in violations[0]


def test_domain_and_application_dependencies_follow_architecture_rules() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    package_root = repository_root / "src" / _PROJECT_PACKAGE
    violations: list[str] = []

    for layer_name in ("domain", "application"):
        for source_file in (package_root / layer_name).rglob("*.py"):
            source_name = str(source_file.relative_to(repository_root))
            violations.extend(
                _architecture_violations(
                    source_file.read_text(encoding="utf-8"),
                    module_name=_module_name(source_file, package_root),
                    source_name=source_name,
                    is_package=source_file.name == "__init__.py",
                )
            )

    assert violations == []
