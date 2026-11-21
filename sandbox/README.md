# Atom's workspace

This folder is the **only** place Atom may read *and* write freely.

* `[write: notes.txt | hi]` → `sandbox/notes.txt`
* Relative paths in any tool call are resolved here, not against the process
  working directory, so `notes.txt` always means `sandbox/notes.txt`.
* Files stored here never need a permission prompt.

## Everything else is read-only

Atom may **read** any file on the machine (that is how it answers questions
about your code), but a **write** outside this folder is shown to you first and
only runs when you approve it:

| Setting | Effect |
| --- | --- |
| `permissions.sandbox_dir` | Which folder this is (relative paths resolve against the project root). |
| `permissions.allow_outside_writes` | `true` (default) → outside writes are allowed **after you approve each one**. `false` → outside writes are refused outright, approval or not. |

Commands while Atom is running: `/workspace` (show this folder), `/allow` and
`/deny` (answer a pending permission prompt), `/status` (policy summary).

Scratch files written here are git-ignored — only this README is tracked.
