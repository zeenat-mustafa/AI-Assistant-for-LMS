# Package marker — MCP tool modules, one per domain.
#
# Each module exposes a register(server) function that the server calls at
# startup, rather than importing the server instance itself — that keeps
# registration one-directional (server -> tools) and avoids a circular
# import. See session_tools.py for the pattern.
