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

extensions = []

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- General configuration ---------------------------------------------------
extensions = [
    "sphinx_rtd_theme",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.todo",
    "sphinx.ext.coverage",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
    "sphinx_multiversion",
    "myst_parser",
    "breathe"
]

autodoc_member_order = 'bysource' # Or 'groupwise', or 'alphabetical'

# -- HTML theme options ------------------------------------------------------
html_theme = 'sphinx_rtd_theme'

# -- HTML options ------------------------------------------------------------
html_logo = "_static/images/squirrel.png"   # path to your logo file
html_static_path = ['_static']
html_css_files = ['css/custom.css']

# Optional — set favicon if you want the browser tab icon
html_favicon = "_static/images/squirrel.png"

# -- Other settings ----------------------------------------------------------
templates_path = ['_templates']
exclude_patterns = []

copybutton_prompt_text = r">>> |\$ "
copybutton_prompt_is_regexp = True
copybutton_line_continuation_character = "\\"