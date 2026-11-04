# Question experiments

The packaged default is `src/photo_metadata_agent/resources/questions.json`.
Copy it here under a versioned filename and pass `photo-agent import ... --questions path.json`
to compare instructions or criteria descriptions. Every queued job snapshots the complete
question specification and its hash. Allowed values must match the canonical Pydantic domain
schema; changing the taxonomy requires a schema version and code change.

Jev uses independent Choice questions, including yes/no/unknown for context labels.
This avoids treating missing family or ministry context as a negative label.
