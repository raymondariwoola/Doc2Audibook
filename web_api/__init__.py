"""Local FastAPI interface for Doc2Audiobook.

The web API is additive: it imports the reusable engine and provider layers but
does not change the CLI or the Gradio prototype.
"""

from .main import app, create_app

__all__ = ["app", "create_app"]
