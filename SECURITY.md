# Security Policy

Do not open a public issue containing API keys, tokens, private scene data, or
an exploit that could execute arbitrary code through Isaac Assist.

Report security issues privately through the repository host's security
advisory feature. Include the affected revision, reproduction steps, expected
impact, and whether the report involves the FastAPI service, generated Python,
Kit RPC, ROS2, file access, or a third-party model provider.

Only the current default branch is supported. Until a fix is available, avoid
running untrusted prompts or generated patches with live Kit/ROS access. Keep
the service bound to a trusted network, protect provider credentials through
environment variables, and review medium/high-risk actions before execution.

The secret redactor and governance engine reduce accidental exposure; they are
not security boundaries or substitutes for operating-system isolation.
