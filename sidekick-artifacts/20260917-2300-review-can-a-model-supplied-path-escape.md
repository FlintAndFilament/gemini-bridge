# gemini_review — Can a model-supplied path escape the repo root or reach a denied file? Cite exac

- tool: gemini_review
- model: gemini-3.5-flash
- session: default
- saved: 2026-09-17 23:00

---

No, a model-supplied path **cannot** escape the repository root or reach a denied file. The confinement design in `src/gemini_bridge/sandbox.py` is exceptionally robust and secure:

*   **No Root Escape:** 
    *   `_lexical()` (lines 125–135) resolves absolute/relative input paths using `os.path.normpath()` (line 131) and strictly checks containment via `_inside()` (lines 122–123).
    *   `resolve()` (lines 142–150) resolves all symlinks via `Path.resolve()` (line 147) and re-verifies confinement of the fully-resolved target (line 148), blocking symlink-based traversal.
    *   `resolve_for_write()` (lines 151–165) resolves the parent directory (line 159), re-verifies its confinement (line 160), and constructs the final target locally (line 161) without following a final symlink component, preventing write-through.

*   **No Reaching Denied Files:**
    *   `is_denied()` (lines 113–120) iterates through every ancestor prefix of the root-relative path (lines 115–117) and matches them case-insensitively against deny patterns using `_pattern_matches()` (lines 78–98). This ensures that nested files under a blocked directory (e.g., `.git/config`) are recursively denied.
    *   `_check()` (lines 136–140) actively blocks any path if it matches the deny-list.

*   **Safe Directory Walking:**
    *   `walk()` (lines 167–191) disables directory symlink following (`followlinks=False`, line 177), filters out directory symlinks (line 183), checks denied paths (line 184), and filters files with `_allowed_file()` (lines 192–198) which drops files matching the deny-list or resolving outside the root.
