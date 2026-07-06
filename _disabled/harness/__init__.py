"""Native provider transports and on-disk history readers.

``native_harness`` implements the real passthrough transports (Codex app-server
JSON-RPC, Claude Code stream-json + permission bridge, Cursor Agent print mode,
Antigravity ``agy`` + transcript reads). ``claude_permission_bridge`` is launched
as a subprocess by Claude Code to route approvals back over loopback HTTP.
"""
