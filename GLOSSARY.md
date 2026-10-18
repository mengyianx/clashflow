# ClashFlow

ClashFlow is a self-hosted management panel for a mihomo (Clash.Meta) proxy stack on a single personal gateway, shipped as one Docker container. This glossary fixes the domain vocabulary used in issues, code, and docs.

## Language

**Kernel**:
The mihomo process that performs all proxying and whose lifecycle ClashFlow supervises.
_Avoid_: core, clash core

**Management Panel**:
The web interface served by ClashFlow itself, covering subscription management, config editing, and service control.
_Avoid_: dashboard

**Runtime Dashboard**:
The bundled metacubexd web interface used for runtime operations against the Kernel API (node selection, delay tests, connection and traffic monitoring).
_Avoid_: dashboard

**Subscription**:
A remote URL that yields proxy nodes, fetched by ClashFlow to produce a Config.
_Avoid_: sub, feed

**Config**:
A mihomo YAML configuration file managed by ClashFlow; the active Config determines the Kernel's behavior.
_Avoid_: profile

**Service Control**:
ClashFlow's authority over the Kernel process lifecycle and over applying a Config to it.
_Avoid_: service management
