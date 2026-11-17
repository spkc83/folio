import ast
import pathlib
import subprocess
import sys

PACKAGE = pathlib.Path(__file__).resolve().parents[2] / "folio_graph"


def test_package_never_imports_folio():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(m.split(".")[0] == "living_reports" for m in names), path


def test_import_loads_no_folio_module():
    code = (
        "import sys, folio_graph; print(any(m.startswith('living_reports') for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=PACKAGE.parent,
    )
    assert out.stdout.strip() == "False"


def test_edge_change_id_is_stable():
    from folio_graph import EdgeChange

    change = EdgeChange(kind="joins_to", from_id="orders", to_id="dealers")
    assert change.id == "orders|joins_to|dealers|"
