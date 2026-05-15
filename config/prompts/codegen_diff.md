# Diff-mode codegen instruction

You are updating an existing file. Return ONLY a unified diff in standard format. Do not output the whole file.

Rules:
- Start with `--- a/<path>` and `+++ b/<path>` header lines using the exact path given below.
- Include one or more `@@ -A,B +C,D @@` hunk markers.
- Prefix removed lines with `-`, added lines with `+`, unchanged context lines with ` ` (one space).
- Include at least 3 lines of unchanged context around each changed region when the file is long enough.
- No prose, no explanation, no code fences, no whole-file output.

## Target path

{target_path}

## Current file content

{existing_content}

## Spec

{spec_prompt}
