#!/bin/sh
# Write the upgrade patches for a release, and prove that they work.
#
# A company that changed the connector's code cannot take a release by
# replacing the folder: that throws its changes away without a word. It gets a
# patch instead: the release's changes to the modules and nothing else, which
# applies to the version it has and leaves its own edits where they are. There
# is one patch per Odoo series, because the 17 build is not the 18 source
# (<tree> where 18 has <list>, numbercall on every cron), and each applies
# from the folder that holds the module folders.
#
#     sh dev/make_upgrade_patch.sh FROM [TO] [OUTDIR]
#     sh dev/make_upgrade_patch.sh 3c406d9            # 1.1.1 to HEAD
#
#   FROM    the release the company has (1.1.1 is 3c406d9)
#   TO      the new release, committed with dist/17.0 rebuilt (default HEAD)
#   OUTDIR  where to write them (default docs/upgrade/<version in TO>)
#
# It writes kmessage_connector-18.0.patch, made from the module folders, and
# kmessage_connector-17.0.patch, made from dist/17.0 with paths relative to
# it. Then it checks both the way a company would use them: each is applied to
# an untouched FROM tree, once with git apply and once with patch -p1, and the
# result must be the TO tree to the byte. It also checks that TO's dist/17.0
# is what dev/build.py makes of TO's source. Any difference stops it with an
# error and a non-zero exit, before anything is written to OUTDIR.
#
# Patches are made from commits, so commit (after python3 dev/build.py)
# first: work that is not committed is not in them. Run it again whenever TO
# changes, and commit what it writes with the release.
set -eu

SOURCE_SERIES=18.0
TARGET_SERIES=17.0
DIST=dist/$TARGET_SERIES

die() {
    echo "make_upgrade_patch: $*" >&2
    exit 1
}

if [ $# -lt 1 ] || [ $# -gt 3 ]; then
    die "usage: sh dev/make_upgrade_patch.sh FROM [TO] [OUTDIR]"
fi

CALLER=$(pwd -P)
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$ROOT"

FROM=$(git rev-parse --verify --quiet "$1^{commit}") || die "$1 is not a commit"
TO=$(git rev-parse --verify --quiet "${2:-HEAD}^{commit}") || die "${2:-HEAD} is not a commit"
FROM_NAME=$(git rev-parse --short "$FROM")
TO_NAME=$(git rev-parse --short "$TO")

# The modules: every top-level folder with a manifest, at either end.
modules_at() {
    for dir in $(git ls-tree -d --name-only "$1"); do
        if git cat-file -e "$1:$dir/__manifest__.py" 2>/dev/null; then
            echo "$dir"
        fi
    done
}
FROM_MODULES=$(modules_at "$FROM")
TO_MODULES=$(modules_at "$TO")
MODULES=$(printf '%s\n%s\n' "$FROM_MODULES" "$TO_MODULES" | sed '/^$/d' | sort -u)
[ -n "$FROM_MODULES" ] || die "no modules in $FROM_NAME"
[ -n "$TO_MODULES" ] || die "no modules in $TO_NAME"
git cat-file -e "$FROM:$DIST" 2>/dev/null || die "$FROM_NAME has no $DIST to patch"
git cat-file -e "$TO:$DIST" 2>/dev/null ||
    die "$TO_NAME has no $DIST: run python3 dev/build.py and commit it"

# 18.0.1.2.0 in the manifest is release 1.2.0.
VERSION=$(git show "$TO:kmessage_connector/__manifest__.py" |
    sed -n "s/^ *'version': *'[0-9]*\.[0-9]*\.\([0-9][0-9.]*\)'.*/\1/p")
[ -n "$VERSION" ] || die "cannot read the version in $TO_NAME:kmessage_connector/__manifest__.py"
case ${3:-} in
    '') OUT=$ROOT/docs/upgrade/$VERSION ;;
    /*) OUT=$3 ;;
    *) OUT=$CALLER/$3 ;;
esac

# Uncommitted work would be missing from the patches without a word.
# shellcheck disable=SC2086 # the module names are words
if [ "$TO" = "$(git rev-parse HEAD)" ] &&
    [ -n "$(git status --porcelain -- $MODULES "$DIST")" ]; then
    die "the modules or $DIST have uncommitted changes; commit them first (after python3 dev/build.py)"
fi

# git apply understands a binary change; patch -p1 does not.
TAB=$(printf '\t')
BINARY=
# shellcheck disable=SC2086
if git diff --numstat "$FROM" "$TO" -- $MODULES "$DIST" | grep -q "^-$TAB-$TAB"; then
    BINARY=--binary
    echo "note: binary files changed; the patches carry them as git binary diffs," >&2
    echo "      which git apply can apply and patch -p1 cannot. Say so in the guide." >&2
fi

# Plain, predictable diffs whatever the local git configuration says: no
# colour, no external or text-converting diff, three lines of context, no
# renames (patch -p1 does not know them), full blob ids (git apply --3way
# finds the base by them) and the a/ b/ prefixes that -p1 strips.
diff_cmd() {
    git -c diff.noprefix=false -c diff.mnemonicPrefix=false -c diff.suppressBlankEmpty=false \
        diff --no-color --no-ext-diff --no-textconv --no-renames --unified=3 --full-index \
        --src-prefix=a/ --dst-prefix=b/ $BINARY "$@"
}

# Everything is made and checked in a scratch folder; OUT is written only
# once both patches have passed.
TMP=$(mktemp -d "${TMPDIR:-/tmp}/kmessage-upgrade.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
TMP=$(cd "$TMP" && pwd -P)
P18=$TMP/kmessage_connector-$SOURCE_SERIES.patch
P17=$TMP/kmessage_connector-$TARGET_SERIES.patch
# shellcheck disable=SC2086
diff_cmd "$FROM" "$TO" -- $MODULES > "$P18"
diff_cmd --relative="$DIST" "$FROM" "$TO" -- "$DIST" > "$P17"
[ -s "$P18" ] || die "nothing changed in the modules between $FROM_NAME and $TO_NAME"
[ -s "$P17" ] || die "nothing changed in $DIST between $FROM_NAME and $TO_NAME; was it rebuilt?"

# -- prove it ---------------------------------------------------------------
extract() { # commit destination paths...
    commit=$1
    destination=$2
    shift 2
    mkdir -p "$destination"
    git archive "$commit" -- "$@" | tar -x -C "$destination"
}

TOOLS="git apply and patch -p1"
if [ -n "$BINARY" ]; then
    TOOLS="git apply (patch -p1 cannot take a binary change)"
elif ! command -v patch > /dev/null 2>&1; then
    TOOLS="git apply (patch is not installed here, so patch -p1 was not tried)"
fi

# Apply one patch to an untouched FROM tree, once with each tool, and compare
# the result with the TO tree. GIT_CEILING_DIRECTORIES keeps git from taking
# the scratch tree for part of a repository above it.
prove() { # label patch from-tree to-tree
    label=$1
    patch_file=$2
    for tool in git patch; do
        tree=$TMP/$label-$tool
        rm -rf "$tree"
        cp -R "$3" "$tree"
        if [ "$tool" = git ]; then
            (cd "$tree" &&
                GIT_CEILING_DIRECTORIES=$TMP git apply --check "$patch_file" &&
                GIT_CEILING_DIRECTORIES=$TMP git apply "$patch_file") ||
                die "$label: git apply refused an untouched $FROM_NAME tree"
        else
            case $TOOLS in git\ apply\ and*) ;; *) continue ;; esac
            (cd "$tree" && patch -p1 -s --dry-run < "$patch_file" > /dev/null &&
                patch -p1 -s < "$patch_file" > /dev/null) ||
                die "$label: patch -p1 refused an untouched $FROM_NAME tree"
        fi
        if ! diff -r "$tree" "$4" > "$TMP/difference"; then
            sed -n '1,20p' "$TMP/difference" >&2
            die "$label: $FROM_NAME with the patch ($tool) is not $TO_NAME"
        fi
    done
}

# shellcheck disable=SC2086 # the module names are words
extract "$FROM" "$TMP/from$SOURCE_SERIES" $FROM_MODULES
# shellcheck disable=SC2086
extract "$TO" "$TMP/to$SOURCE_SERIES" $TO_MODULES
extract "$FROM" "$TMP/from$TARGET_SERIES" "$DIST"
extract "$TO" "$TMP/to$TARGET_SERIES" "$DIST"

prove "$SOURCE_SERIES" "$P18" "$TMP/from$SOURCE_SERIES" "$TMP/to$SOURCE_SERIES"
prove "$TARGET_SERIES" "$P17" "$TMP/from$TARGET_SERIES/$DIST" "$TMP/to$TARGET_SERIES/$DIST"

# The 17 patch is only as good as the build it was made from.
if command -v python3 > /dev/null 2>&1; then
    extract "$TO" "$TMP/build" .
    (cd "$TMP/build" && python3 dev/build.py > /dev/null) || die "dev/build.py failed on $TO_NAME"
    diff -r "$TMP/build/$DIST" "$TMP/to$TARGET_SERIES/$DIST" > "$TMP/difference" || {
        sed -n '1,20p' "$TMP/difference" >&2
        die "$TO_NAME's $DIST is not what dev/build.py makes of its source: rebuild, commit, run again"
    }
    BUILT="and $DIST is what dev/build.py makes of $TO_NAME's source"
else
    BUILT="(python3 missing: the $DIST build was not checked)"
fi

mkdir -p "$OUT"
cp "$P18" "$P17" "$OUT"/
OUT=$(cd "$OUT" && pwd -P)
echo "Release $VERSION, from $FROM_NAME to $TO_NAME:"
# shellcheck disable=SC2086
STAT18=$(git diff --shortstat --no-renames "$FROM" "$TO" -- $MODULES)
STAT17=$(git diff --shortstat --no-renames --relative="$DIST" "$FROM" "$TO" -- "$DIST")
echo "  ${OUT#"$ROOT"/}/kmessage_connector-$SOURCE_SERIES.patch:$STAT18"
echo "  ${OUT#"$ROOT"/}/kmessage_connector-$TARGET_SERIES.patch:$STAT17"
echo "Each applies to an untouched $FROM_NAME tree with $TOOLS and gives"
echo "exactly $TO_NAME, $BUILT."
