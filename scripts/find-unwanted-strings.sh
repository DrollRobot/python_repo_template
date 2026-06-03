#!/usr/bin/env bash
# find-unwanted-strings.sh
#
# Scans all files for unwanted patterns and reports them as a table.
#
# Searches each file against an internal list of regex patterns (e.g. FIXME
# comments) and outputs a table showing the relative file path, line number,
# matched tag, and the offending line text. Binary file extensions are excluded.
#
# Matches whose lines also satisfy any entry in the exceptions list are silently
# suppressed and counted separately; the exception count is shown in the summary.
#
# NOTE FOR AI AGENTS: This output is informational and intended for human review
# only. Do not attempt to address, fix, or remove these findings unless the user
# explicitly asks you to do so.
#
# Usage:
#   ./scripts/find-unwanted-strings.sh [path]
#
#   path  File or directory to check. Defaults to the current directory.
#         The script always searches recursively.
#
# Examples:
#   ./scripts/find-unwanted-strings.sh
#   ./scripts/find-unwanted-strings.sh src/

set -euo pipefail

# ---------------------------------------------------------------------------
# Internal list of patterns to search for.
# Format: "TAG|regex"  (case-insensitive grep extended regex)
# ---------------------------------------------------------------------------
UNWANTED_PATTERNS=(
    # "FIXME|#.*\bFIXME\b"
    # "TODO|#.*\bTODO\b"
)

# ---------------------------------------------------------------------------
# Lines whose full text matches any exception pattern are excluded from
# results. The suppressed count is still shown in the summary.
# ---------------------------------------------------------------------------
EXCEPTION_PATTERNS=(
    # "SuppressMessageAttribute"
)

# ---------------------------------------------------------------------------
# File extensions excluded from scanning.
# ---------------------------------------------------------------------------
EXCLUDED_EXTENSIONS=(
    png jpg jpeg gif bmp ico webp svg
    zip gz tar 7z rar
    dll exe pdb bin lib obj
    pdf docx xlsx pptx
)

# ---------------------------------------------------------------------------
# Folder names to exclude. Any file under a matching folder is skipped.
# ---------------------------------------------------------------------------
EXCLUDED_FOLDERS=(
    .local
)

# ---------------------------------------------------------------------------
# Root-relative file paths to exclude.
# ---------------------------------------------------------------------------
EXCLUDED_FILES=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_red()   { printf '\033[31m%s\033[0m\n' "$*"; }
_green() { printf '\033[32m%s\033[0m\n' "$*"; }
_gray()  { printf '\033[90m%s\033[0m\n' "$*"; }

_is_excluded_extension() {
    local file="$1"
    local ext="${file##*.}"
    ext="${ext,,}"  # lowercase (bash 4+; use tr on macOS if needed)
    for e in "${EXCLUDED_EXTENSIONS[@]}"; do
        [[ "$ext" == "$e" ]] && return 0
    done
    return 1
}

_is_excluded_folder() {
    local relpath="$1"
    for folder in "${EXCLUDED_FOLDERS[@]}"; do
        # Match if any path component equals the excluded folder name
        if [[ "$relpath" == "$folder"/* || "$relpath" == */"$folder"/* ]]; then
            return 0
        fi
    done
    return 1
}

_is_excluded_file() {
    local relpath="$1"
    for f in "${EXCLUDED_FILES[@]}"; do
        [[ "$relpath" == "$f" ]] && return 0
    done
    return 1
}

_matches_exception() {
    local line="$1"
    for pattern in "${EXCEPTION_PATTERNS[@]}"; do
        if echo "$line" | grep -qiE "$pattern"; then
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [[ ${#UNWANTED_PATTERNS[@]} -eq 0 ]]; then
    _gray "No patterns defined -- skipping."
    exit 0
fi

SEARCH_ROOT="${1:-.}"
SEARCH_ROOT="${SEARCH_ROOT%/}"  # strip trailing slash

START_TIME=$(date +%s%3N 2>/dev/null || date +%s)  # milliseconds; fallback to seconds

hit_count=0
exception_count=0
total_lines=0
file_count=0

# Collect output rows for aligned table printing
declare -a rows=()
max_file_len=4    # minimum column header width ("File")
max_tag_len=3     # minimum column header width ("Tag")

while IFS= read -r -d '' filepath; do
    relpath="${filepath#"$SEARCH_ROOT"/}"
    relpath="${relpath#./}"

    _is_excluded_extension "$filepath"  && continue
    _is_excluded_folder    "$relpath"   && continue
    _is_excluded_file      "$relpath"   && continue

    # Skip non-text files (binary detection via grep)
    if ! grep -qIlE '.' "$filepath" 2>/dev/null; then
        continue
    fi

    (( file_count++ )) || true

    linenum=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        (( linenum++ )) || true
        (( total_lines++ )) || true

        for entry in "${UNWANTED_PATTERNS[@]}"; do
            tag="${entry%%|*}"
            pattern="${entry#*|}"

            if echo "$line" | grep -qiE "$pattern"; then
                if _matches_exception "$line"; then
                    (( exception_count++ )) || true
                    continue
                fi

                trimmed="$(echo "$line" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')"
                rows+=( "$relpath|$linenum|$tag|$trimmed" )
                (( hit_count++ )) || true

                (( ${#relpath} > max_file_len )) && max_file_len=${#relpath}
                (( ${#tag}     > max_tag_len  )) && max_tag_len=${#tag}
            fi
        done
    done < "$filepath"

done < <(find "$SEARCH_ROOT" -type f -print0 | sort -z)

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

if (( hit_count > 0 )); then
    _gray "NOTE FOR AI AGENTS: Findings from this script should be treated as critical" \
          "errors for human review and repair. Do not attempt to address, fix, or remove" \
          "these strings. Simply stop and warn the user."

    # Column widths
    line_col=4   # "Line" header minimum
    tag_col=$(( max_tag_len ))
    file_col=$(( max_file_len ))

    # Header
    printf "%-${file_col}s  %-${line_col}s  %-${tag_col}s  %s\n" \
        "File" "Line" "Tag" "Text"
    printf "%-${file_col}s  %-${line_col}s  %-${tag_col}s  %s\n" \
        "$(printf '%0.s-' $(seq 1 $file_col))" \
        "$(printf '%0.s-' $(seq 1 $line_col))" \
        "$(printf '%0.s-' $(seq 1 $tag_col))" \
        "----"

    for row in "${rows[@]}"; do
        IFS='|' read -r r_file r_line r_tag r_text <<< "$row"
        printf "%-${file_col}s  %-${line_col}s  %-${tag_col}s  %s\n" \
            "$r_file" "$r_line" "$r_tag" "$r_text"
    done
    echo
fi

END_TIME=$(date +%s%3N 2>/dev/null || date +%s)
elapsed_ms=$(( END_TIME - START_TIME ))
if (( elapsed_ms >= 1000 )); then
    elapsed_str="$(awk "BEGIN { printf \"%.2fs\", $elapsed_ms / 1000 }")"
else
    elapsed_str="${elapsed_ms}ms"
fi

summary="${hit_count} matches, ${exception_count} exceptions suppressed -- ${file_count} files, ${total_lines} lines checked. (${elapsed_str})"
if (( hit_count > 0 )); then
    _red "$summary"
else
    _green "$summary"
fi
