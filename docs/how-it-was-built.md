# How it was built

Sovereign Nations and this kit were built by AI coding agents (Claude, through Claude
Code) working for one human maintainer, who wrote the requirements, played the builds
and decided what counted as done. The workflow is part of what makes the kit
reproducible, so it is written down here.

## Numbered task files

Every piece of work starts as a short Markdown file in `tasks/` (`01-nation-ui-as-panel.md`,
`07-separate-player-and-nation-flags.md`, …): who asked, why, what the player should be
able to do, and an **Acceptance** section. An agent takes one task, works in its own git
worktree and branch, and hands back a report: what changed, which tests ran, what is
still open. The maintainer reviews, plays the build and merges.

## Tests as the acceptance gate

A task is done when its tests pass, not when the code looks finished:

- **Facts are code.** Every address or byte claim the docs rely on is a fact in the
  project's fact checker, re-checked against the exe on every run (197 facts). That file holds
  the original bytes of every site and is not published; `tools/exe/check_sites.py` is the
  same idea for your own facts. A disproven finding is fixed in the same commit that disproves
  it.
- **Unit tests** for the pure logic (Sovereign Nations' `dll/tests/run_tests.py`, 695 checks),
  built with the same compiler as the DLL and run without the game.
- **Static patch tests** that re-disassemble every patched site and check that nothing
  outside the patches changed.
- **In-game suites that run in the background**: the game in a small corner window of a
  disposable test install, driven through the mod's own Lua API and the game's input
  handlers, never taking focus or the user's mouse. Feature suite, scenario saves, a real
  conquest battle, probes per task, screenshots per build (`techniques/07-testing.md`).
- Every claim in a research note is labelled (verified statically, observed at runtime,
  reported by earlier work, or hypothesis), so the next agent knows what it can rely on.

## Rules learned the hard way

Earlier attempts in the same development workspace went wrong in ways that repeat (see
`dead-ends.md`). The rules that came out of them:

1. **Value-gated.** Every task names the player-visible test it moves forward. No test
   ID, no task.
2. **Feasibility first.** Before building on a field or constant, list every consumer of
   it and write down the ceiling.
3. **Dynamic first.** Answer runtime questions with a breakpoint or a memory watch, not by
   re-reading decompiled code.
4. **Classify faults before chasing them** (`techniques/08-debugging.md`).
5. **No broad reconstruction.** Read only the functions on your call path; rebuild a
   function only when you must change it. Byte-exactness is never a goal in itself.
6. **Reproducible builds.** One script turns the pinned base exe and an ordered patch list
   into an output plus a manifest of hashes.
7. **Clean test environment.** Deploy and undeploy only by script, back up saves before
   every experiment, never test on an install with other mods deployed.
8. **Commit at the end of every session.**

## Practicalities

- Parallel agents share one test install through a lock directory (`07-testing.md`);
  only one game runs at a time, and only while a test needs it.
- Long unattended runs keep the PC awake and the screen saver away
  (`tools/test/keepawake.py`), because Direct3D 8 cannot start while a screen saver is up.
- Agents that ran without limits lost much of their work (one earlier effort ran up to
  ~96 concurrent agents and lost about 60% of the runs to rate limits and conflicts).
  A handful of agents with clear, test-gated tasks worked better.
