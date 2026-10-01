import sys

# Keep the restricted entry point independent of artist, routes and editor imports.
if sys.argv[1:] == ["mcp-readonly"]:
    from .readonly_mcp import main
else:
    from .cli import main
main()
