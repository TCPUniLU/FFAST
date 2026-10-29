# Pydantic models define protocol and configuration schemas

**Status:** Accepted / Implemented

FFAST will use Pydantic models as the source of truth for protocol messages and configuration, including strict validation, unknown-key rejection, defaults, and generated JSON Schema for web clients. Validated models are converted to plain values for the existing WebSocket/msgpack transport chosen in ADR 0001, so schema adoption does not replace the wire protocol.
