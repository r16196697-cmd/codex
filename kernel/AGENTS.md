# Kernel guidance

- Recovery cleanup under `kernel/run` and `kernel/runtime` is only for a durable, exact-bound `schedule_setup_compensation_intent`: allow only its child Run `CREATED -> CANCELLED` and bound budget `RESERVED -> RELEASED`; it is not delegated task authority and must never authorize forward work.
