import os
import sys
import ast
from pathlib import Path

sys.path.insert(0, os.path.abspath('../..'))

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'squirrel'
copyright = '2026, Mazen Soliman'
author = 'Mazen Soliman'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx_rtd_theme",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.todo",
    "sphinx.ext.coverage",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

autosummary_generate = True


def _generate_class_index() -> None:
    """Generate one page per module, containing all of that module's classes.

    Replaces the old approach of one autosummary entry (and one generated
    page) per class. Now classes belonging to the same module are grouped
    together and documented inline on a single module page, and the top
    level index just links out to each module page.
    """
    backend_root = Path(__file__).resolve().parents[2]
    package_root = backend_root / "squirrel"

    # module_name -> [class_name, ...]
    modules: dict[str, list[str]] = {}

    for source_file in sorted(package_root.rglob("*.py")):
        if source_file.name == "__init__.py":
            continue

        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        module_parts = source_file.relative_to(backend_root).with_suffix("").parts
        module_name = ".".join(module_parts)
        class_names = [
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        ]
        if class_names:
            modules[module_name] = class_names

    generated_dir = Path(__file__).parent / "generated"
    generated_dir.mkdir(exist_ok=True)

    module_pages = []
    for module_name, class_names in sorted(modules.items()):
        # Flatten dotted module name into a safe filename, e.g.
        # squirrel.schemas.workspace -> squirrel_schemas_workspace
        page_name = module_name.replace(".", "_")
        module_pages.append(page_name)

        lines = [
            module_name,
            "=" * len(module_name),
            "",
        ]
        for class_name in class_names:
            lines += [
                f".. autoclass:: {module_name}.{class_name}",
                "   :members:",
                "   :show-inheritance:",
                "",
            ]

        (generated_dir / f"{page_name}.rst").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    index_lines = [
        "Generated API classes",
        "=====================",
        "",
        ".. toctree::",
        "   :maxdepth: 1",
        "",
        *(f"   generated/{page_name}" for page_name in module_pages),
        "",
    ]
    (Path(__file__).parent / "generated_classes.rst").write_text(
        "\n".join(index_lines), encoding="utf-8"
    )


_generate_class_index()

autodoc_member_order = 'bysource'  # Or 'groupwise', or 'alphabetical'
autodoc_default_options = {
    'members': True,
    'show-inheritance': True,
}
autodoc_typehints = 'description'

# -- HTML theme options ------------------------------------------------------
html_theme = 'sphinx_rtd_theme'

# -- HTML options ------------------------------------------------------------
html_logo = "_static/images/sqrl.png"
html_static_path = ['_static']
html_css_files = ['css/custom.css']

# Optional — set favicon if you want the browser tab icon
html_favicon = "_static/images/sqrl.png"

# -- Other settings ----------------------------------------------------------
copybutton_prompt_text = r">>> |\$ "
copybutton_prompt_is_regexp = True
copybutton_line_continuation_character = "\\"