# Kernel guidance

- Recovery cleanup under `kernel/run` and `kernel/runtime` is only for a durable, exact-bound `schedule_setup_compensation_intent`: allow only its child Run `CREATED -> CANCELLED` and bound budget `RESERVED -> RELEASED`; it is not delegated task authority and must never authorize forward work.
- `ObjectStore.object_envelopes.schema_id` identifies the outer `nexus.object` envelope; a governed artifact's semantic payload schema is inside its classified, integrity-checked bytes.
- `ContextPackService.read_compiled()` returns a verified result envelope; use its `serialized_byte_size` for canonical pack size checks, not the serialization size of the returned envelope.
