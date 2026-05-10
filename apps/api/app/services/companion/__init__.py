"""AURA companion services (EPIC #373).

Module skeleton for the companion layer. The route itself doesn't
exist yet — pre-implementation lock-ins (citation contract #370,
trust gate #372, feedback bridge #371) land here as importable
services so they're in place when the route is wired.

Sub-modules are imported directly by call sites (no top-level
re-exports) so adding a new lock-in doesn't ripple into this file.
"""
