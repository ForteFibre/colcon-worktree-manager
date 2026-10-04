"""cwm shell-init - output the shell integration functions (bash/zsh or fish)."""

from __future__ import annotations

import click

from cwm.cli.main import cli

_SHELL_FUNCTION = """\
# cwm shell integration - allows 'cwm activate', 'cwm cd', and 'cwm switch' to work in-shell.
# Add to ~/.bashrc:  eval "$(cwm shell-init)"
cwm() {
    case "$1" in
        activate)
            eval "$(command cwm "$@")"
            ;;
        deactivate)
            if declare -f deactivate >/dev/null 2>&1; then
                deactivate
            else
                echo "cwm: no active workspace to deactivate" >&2
                return 1
            fi
            ;;
        cd)
            shift
            local __cwm_path __cwm_ret
            __cwm_path="$(command cwm __cd-resolve "$@")"
            __cwm_ret=$?
            if [ $__cwm_ret -ne 0 ]; then
                echo "$__cwm_path" >&2
                return $__cwm_ret
            fi
            cd "$__cwm_path"
            ;;
        switch)
            local __cwm_branch __cwm_path __cwm_ret
            __cwm_branch="$2"
            shift 2
            eval "$(command cwm activate "$__cwm_branch")" || return $?
            __cwm_path="$(command cwm __cd-resolve --auto-subrepo "$@")"
            __cwm_ret=$?
            if [ $__cwm_ret -ne 0 ]; then
                echo "$__cwm_path" >&2
                return $__cwm_ret
            fi
            cd "$__cwm_path"
            ;;
        *)
            command cwm "$@"
            ;;
    esac
}

# Walk up from $PWD looking for .cwm/.  No reliance on CWM_ACTIVE so that an
# activated shell which has cd'd into an unrelated repo does not hijack git.
# Guards against fixed-point dirname (e.g. dirname '.' -> '.') by breaking when
# the parent stops changing.
__cwm_in_project() {
    local dir prev
    dir="$PWD"
    while [[ -n "$dir" ]]; do
        [[ -d "$dir/.cwm" ]] && return 0
        [[ "$dir" == "/" ]] && return 1
        prev="$dir"
        dir="$(dirname "$dir")"
        [[ "$dir" == "$prev" ]] && return 1
    done
    return 1
}

# Return success if the real git subcommand is 'worktree', skipping any leading
# global options.  Options that take a separate value ('-C <path>', '-c <kv>',
# '--git-dir <path>', ...) are skipped in pairs so the value is not mistaken for
# the subcommand; otherwise 'git -C <path> worktree ...' would slip through.
__cwm_git_has_worktree() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -C|-c|--git-dir|--work-tree|--namespace|--super-prefix)
                shift 2 || return 1 ;;
            -*) shift ;;
            worktree) return 0 ;;
            *) return 1 ;;
        esac
    done
    return 1
}

# Intercept 'git worktree' inside CWM projects and forward to the CWM hook.
# All other git invocations (and 'git worktree' outside a CWM project) fall
# through to the real binary via 'command git'.
git() {
    if __cwm_in_project; then
        # Fast path: a bare 'git worktree ...' strips the subcommand token.
        if [[ "$1" == "worktree" ]]; then
            shift
            command cwm worktree __git_hook "$@"
            return $?
        fi
        # Behind leading global options (e.g. 'git -C <path> worktree ...'),
        # forward the original arguments unshifted so the hook can refuse
        # repository-retargeting options instead of letting them slip through.
        if __cwm_git_has_worktree "$@"; then
            command cwm worktree __git_hook "$@"
            return $?
        fi
    fi
    command git "$@"
}
"""


_FISH_FUNCTION = """\
# cwm shell integration (fish) - allows 'cwm activate', 'cwm cd', and 'cwm switch' to work in-shell.
# Add to ~/.config/fish/config.fish:  cwm shell-init --shell fish | source

# Deactivate the current worktree, if any.  The fish activation script is a
# diff against the current environment, so it must start from a clean one.
function __cwm_deactivate_if_active
    if set -q CWM_ACTIVE; and functions -q deactivate
        deactivate
    end
end

function cwm --description 'Colcon Worktree Manager (shell integration)'
    switch "$argv[1]"
        case activate
            __cwm_deactivate_if_active
            command cwm activate --shell fish $argv[2..-1] | source
            set -l __cwm_ret $pipestatus[1]
            test $__cwm_ret -eq 0; or return $__cwm_ret
        case deactivate
            if functions -q deactivate
                deactivate
            else
                echo "cwm: no active workspace to deactivate" >&2
                return 1
            end
        case cd
            set -l __cwm_path (command cwm __cd-resolve $argv[2..-1])
            or return $status
            cd $__cwm_path
        case switch
            __cwm_deactivate_if_active
            command cwm activate --shell fish $argv[2] | source
            set -l __cwm_ret $pipestatus[1]
            test $__cwm_ret -eq 0; or return $__cwm_ret
            set -l __cwm_path (command cwm __cd-resolve --auto-subrepo $argv[3..-1])
            or return $status
            cd $__cwm_path
        case '*'
            command cwm $argv
    end
end

# Walk up from $PWD looking for .cwm/.  No reliance on CWM_ACTIVE so that an
# activated shell which has cd'd into an unrelated repo does not hijack git.
# Breaks when dirname reaches a fixed point.
function __cwm_in_project
    set -l dir $PWD
    while test -n "$dir"
        test -d "$dir/.cwm"; and return 0
        test "$dir" = /; and return 1
        set -l prev $dir
        set dir (dirname -- $dir)
        test "$dir" = "$prev"; and return 1
    end
    return 1
end

# Return success if the real git subcommand is 'worktree', skipping any leading
# global options.  Options that take a separate value ('-C <path>', '-c <kv>',
# '--git-dir <path>', ...) are skipped in pairs so the value is not mistaken for
# the subcommand; otherwise 'git -C <path> worktree ...' would slip through.
function __cwm_git_has_worktree
    set -l args $argv
    while test (count $args) -gt 0
        set -l arg $args[1]
        switch $arg
            case -C -c --git-dir --work-tree --namespace --super-prefix
                test (count $args) -ge 2; or return 1
                set args $args[3..-1]
            case '-*'
                set args $args[2..-1]
            case worktree
                return 0
            case '*'
                return 1
        end
    end
    return 1
end

# Intercept 'git worktree' inside CWM projects and forward to the CWM hook.
# All other git invocations (and 'git worktree' outside a CWM project) fall
# through to the real binary via 'command git'.
function git
    if __cwm_in_project
        # Fast path: a bare 'git worktree ...' strips the subcommand token.
        if test "$argv[1]" = worktree
            command cwm worktree __git_hook $argv[2..-1]
            return $status
        end
        # Behind leading global options (e.g. 'git -C <path> worktree ...'),
        # forward the original arguments unshifted so the hook can refuse
        # repository-retargeting options instead of letting them slip through.
        if __cwm_git_has_worktree $argv
            command cwm worktree __git_hook $argv
            return $status
        end
    end
    command git $argv
end
"""


@cli.command("shell-init")
@click.option(
    "--shell",
    "shell",
    type=click.Choice(["bash", "zsh", "fish"]),
    default="bash",
    show_default=True,
    help="Shell to emit the integration for (zsh uses the bash functions).",
)
def shell_init(shell: str) -> None:
    """Output the shell integration functions.

    Add the following line to your ~/.bashrc (or ~/.zshrc):

    \\b
        eval "$(cwm shell-init)"

    For fish, add this to ~/.config/fish/config.fish:

    \\b
        cwm shell-init --shell fish | source

    This defines a 'cwm' shell function that makes 'cwm activate',
    'cwm deactivate', 'cwm cd', and 'cwm switch' work directly in the shell,
    plus a 'git' function that routes 'git worktree' inside a CWM project to
    CWM.
    """
    click.echo(_FISH_FUNCTION if shell == "fish" else _SHELL_FUNCTION, nl=False)
