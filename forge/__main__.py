import sys

# Keep the restricted entry point independent of artist, routes and editor imports.
if sys.argv[1:] == ["mcp-readonly"]:
    from .readonly_mcp import main
elif len(sys.argv) > 1 and sys.argv[1] == "mcp-authoring":
    from .authoring_mcp import main as authoring_main
    authoring_main(sys.argv[2:])
    sys.exit(0)
else:
    from .cli import main
main()
