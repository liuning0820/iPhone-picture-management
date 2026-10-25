# Skip-extension filtering for iPhone transfers

The change adds a repeatable `--skip-extension` option for excluding source files before an iPhone copy begins. Values such as `MOV` and `.MOV` are normalized to the same case-insensitive extension, and filtering uses the final suffix rather than matching the filename body. The filtered files are absent from the source manifest, so they cannot reach the MTP copy backend; the existing destination-file size check remains in `copy_selection()`. README usage and unittest coverage were updated, and the recorded validation reports 21 passing tests plus successful compilation.

Watch for: no blocking concern found in the implementation diff.

**Verdict**: APPROVED

## High-level view

Argument parsing preserves each repeated `--skip-extension` value, while a single normalization boundary converts dotted or undotted values to case-folded suffixes. This keeps the public CLI flexible without changing the internal matching contract.

Filtering occurs while the source manifest is built, before `SourceSelection.files` is returned. Consequently, the copy loop and MTP backend receive only retained files, while `skipped_count` gives the CLI a visible explanation of the reduced source total.

The existing destination behavior is separated from source filtering: files already present with the expected size are still skipped by `copy_selection()`, and the verification manifest is built from the filtered selection. README examples document the PowerShell command, repeated options, matching rules, and the distinction between the two skip behaviors.

<details>
<summary>Issues (0)</summary>

No blocking concerns.

</details>

<details>
<summary>Details</summary>

### CLI contract and extension normalization

`build_parser()` uses `action="append"`, so repeated options such as `--skip-extension .MOV --skip-extension .MP4` remain available to `run()`. `normalize_skip_extensions()` removes one optional leading dot, applies `casefold()`, rejects an empty value, and returns a set, so `MOV`, `.MOV`, and mixed-case spellings converge on `mov` without duplicate work.

The parser test verifies repeated values, and the normalization test verifies dotted, undotted, and mixed-case inputs. Existing destination, overwrite, list-only, timeout, and default-directory arguments remain unchanged.

### Source manifest boundary prevents MTP copies

`collect_source_selection()` examines only non-folder items and compares the final component after the last dot. A file such as `clip.MoV` is excluded, while `MOV_notes.JPG` and `clip.MOV.bak` remain in the manifest. The selected files are therefore the only inputs to `copy_selection()`, which is the only path that invokes `backend.copy_file()`.

The tests cover both suffix matching and filename-subject non-matching, and the copy test records that only the JPG reaches the fake backend. This verifies the important boundary: a filtered MOV is not merely reported as skipped later; it is never eligible for an MTP transfer.

### Output and independent destination skipping

The run path reports the selected source count and size, then reports the number of extension-filtered files and the normalized extensions whenever a filter is supplied. This also occurs before the `--list-only` return, so list-only runs expose the filtering result without starting a copy.

`copy_selection()` still checks an existing target's type and byte size independently. A same-size target increments the existing destination-skip count and does not call the backend; the recorded test suite includes this regression case. Verification also uses the filtered selection, so intentionally excluded source files are not treated as missing destinations.

### Tests, documentation, and scope

The recorded verification states that `python -m unittest discover -s tests -v` ran 21 tests with all tests passing, and that `python -m py_compile iphone_copy.py tests/test_iphone_copy.py` returned success. The covered behaviors include parser repetition, normalization, case-insensitive matching, subject-text protection, exclusion from the copy call list, no-filter regression behavior, and same-size destination skipping. Per the request, those commands were not rerun during this review.

README now includes the requested PowerShell OneDrive example, a multiple-extension example, the optional-dot and case-insensitive rules, the pre-manifest filtering boundary, and the distinction from same-size destination skipping. The implementation diff is limited to `iphone_copy.py`, `tests/test_iphone_copy.py`, and `README.md`. The separately staged `.kiro/settings/lsp.json` and untracked task notes are outside that feature diff and were not included as implementation changes; no project-external path was modified.

</details>

<details>
<summary>File map</summary>

- `iphone_copy.py` — adds extension normalization, CLI parsing, manifest filtering, skip counting, and output.
- `tests/test_iphone_copy.py` — covers CLI input, normalization, suffix matching, copy exclusion, and regressions.
- `README.md` — documents the option, PowerShell examples, and skip semantics.

Full diff: `git diff HEAD -- iphone_copy.py tests/test_iphone_copy.py README.md`.

</details>
