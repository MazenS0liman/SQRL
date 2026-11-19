import os
import sys
import ast
import toml
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
_pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
release = toml.loads(_pyproject.read_text(encoding="utf-8"))["project"]["version"]
version = release

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

intersphinx_mapping = {
    "sklearn": ("https://scikit-learn.org/stable/", None),
}

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

autosummary_generate = True


def _generate_class_index() -> None:
    """Generate grouped reference pages from the backend package."""
    backend_root = Path(__file__).resolve().parents[2]
    package_root = backend_root / "squirrel"

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
        if class_names or module_name.startswith("squirrel.api."):
            modules[module_name] = class_names

    generated_dir = Path(__file__).parent / "generated"
    generated_dir.mkdir(exist_ok=True)

    category_titles = {
        "api": "HTTP API",
        "schemas": "Data Schemas",
        "services": "Services",
        "agents": "Agents",
        "prompts": "Prompt Components",
        "providers": "Model Providers",
        "inspectors": "Data Inspectors",
        "preprocessors": "Preprocessors",
        "core": "Core Infrastructure",
        "models": "Machine Learning Models",
        "constants": "Constants",
        "other": "Supporting Components",
    }

    grouped_modules: dict[str, list[tuple[str, list[str]]]] = {
        category: [] for category in category_titles
    }

    def category_for(module_name: str) -> str:
        parts = module_name.split(".")
        return parts[2] if len(parts) > 2 and parts[1] == "modules" else parts[1]

    for module_name, class_names in sorted(modules.items()):
        category = category_for(module_name)
        if category not in grouped_modules:
            category = "other"
        grouped_modules[category].append((module_name, class_names))

    for generated_file in generated_dir.glob("*.rst"):
        generated_file.unlink()

    category_pages: list[tuple[str, str]] = []
    for category, grouped in grouped_modules.items():
        if not grouped:
            continue

        page_name = f"{category}_reference"
        title = category_titles[category]
        lines = [
            title,
            "=" * len(title),
            "",
            f"Reference documentation for SQRL {title.lower()}.",
            "",
        ]

        for module_name, class_names in grouped:
            module_parts = module_name.split(".")
            module_title = module_parts[-1]
            if module_title[:1].isupper() and len(module_parts) > 1:
                module_title = module_parts[-2]
            module_title = module_title.replace("_", " ").title()
            lines += [
                module_title,
                "-" * len(module_title),
                "",
            ]
            if class_names:
                for class_name in class_names:
                    lines += [
                        f".. autoclass:: {module_name}.{class_name}",
                        "   :members:",
                        "   :show-inheritance:",
                        "",
                    ]
            else:
                lines += [
                    f".. automodule:: {module_name}",
                    "   :members:",
                    "   :show-inheritance:",
                    "",
                ]

        (generated_dir / f"{page_name}.rst").write_text(
            "\n".join(lines), encoding="utf-8"
        )
        category_pages.append((category, page_name))

    index_lines = [
        "Backend API Reference",
        "=====================",
        "",
        "Browse the generated backend reference by responsibility rather than by file.",
        "",
    ]
    for category, page_name in category_pages:
        index_lines += [
            category_titles[category],
            "-" * len(category_titles[category]),
            "",
            ".. toctree::",
            "   :maxdepth: 1",
            "",
            f"   generated/{page_name}",
            "",
        ]
    (Path(__file__).parent / "generated_classes.rst").write_text(
        "\n".join(index_lines), encoding="utf-8"
    )


_generate_class_index()

autodoc_member_order = 'bysource'  # Or 'groupwise', or 'alphabetical'
add_module_names = False
autodoc_default_options = {
    'members': True,
    'show-inheritance': True,
}
autodoc_typehints = 'description'

# -- HTML theme options ------------------------------------------------------
html_theme = 'sphinx_rtd_theme'
html_title = 'SQRL Documentation'
html_theme_options = {
    'collapse_navigation': False,
    'navigation_depth': 4,
    'sticky_navigation': True,
    'titles_only': True,
    'logo_only': True,
}

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