auth: use bytes.Equal for the signature

bytes.Equal is the standard comparison and is what the rest of the codebase uses.
