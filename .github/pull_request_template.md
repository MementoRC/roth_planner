## Summary
<one or two sentences: the behaviour added or changed>

Closes #<issue>

## Evidence (filled by implementer)
- Commit tested: `<full SHA>`
- Tree: `<absolute path>` (worktree: <yes/no>)

| Gate | Command (exact) | Exit | Result excerpt |
|------|-----------------|------|----------------|
| Tests | `pixi run -e ci test` | <0/n> | <passed/failed/skipped counts> |
| Lint | `<command>` | <0/n> | <violations count> |
| Format | `<command>` | <0/n> | <files changed count> |
| Types | `<command>` | <0/n> | <error count> |
| Acceptance checksum | `<verify command>` | <0/n> | <verify output line> |
| Held-out scenarios | CI job `<name>` | <pass/fail> | <link to run> |
| Mutation (if run) | `<command>` | <0/n> | <killed/survived/no-tests> |

Files changed:
- `<path>`: <one-line reason>

Protected paths: `tests/acceptance/**` and the checksum manifest are unchanged in this PR. Verify output: `<line>`

## Owner review (filled by owner after merge)
- Read before approving: <files or hunks opened>
- Minutes: <n>
- Gates trusted from Evidence without opening the log: <list, or none>
