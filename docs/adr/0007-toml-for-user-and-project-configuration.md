# TOML is the canonical user and project configuration format

**Status:** Accepted / Implemented

FFAST will use TOML as the canonical human-authored format for user and project visualization configuration because partial overrides, named tables, comments, and reviewable minimal patches are central to the configuration model. JSON Schema remains the validation/tooling contract, and JSON may be accepted during migration or compatibility import, but TOML is the format FFAST writes and documents.
