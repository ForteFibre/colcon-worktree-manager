"""Custom exception hierarchy for CWM."""


class CWMError(Exception):
    """Base exception for all CWM errors."""


class ConfigNotFoundError(CWMError):
    """No .cwm/ directory found in the current or parent directories."""


class ConfigVersionError(CWMError):
    """The .cwm/config.yaml was written by an older version of CWM and needs re-initialisation."""


class WorktreeExistsError(CWMError):
    """A worktree for this branch already exists."""


class WorktreeNotFoundError(CWMError):
    """The named worktree does not exist."""


class NotActivatedError(CWMError):
    """Command requires an active CWM workspace (run 'cwm activate <branch>' first)."""


class GitError(CWMError):
    """A git command failed."""


class ColconError(CWMError):
    """A colcon build or invocation failed."""


class UnderlayNotFoundError(CWMError):
    """The specified underlay path does not exist or is invalid."""


class RepoNotFoundError(CWMError):
    """The specified repository path does not exist or is not a git repository."""


class NoRepoSelectedError(CWMError):
    """No repository has been selected; run 'cwm repo add <path>' or pass --repos."""


class BranchNameCollisionError(CWMError):
    """Branch name maps to the same directory as an existing worktree after sanitisation."""


class RepoNameCollisionError(CWMError):
    """Two repositories selected for one worktree share a basename (they would collide under src/)."""


class RepoNotInWorktreeError(CWMError):
    """The named repository is not checked out in the given worktree."""
