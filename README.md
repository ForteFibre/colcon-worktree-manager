# Colcon Worktree Manager (CWM)

A CLI tool that integrates `git worktree` with `colcon` for parallel ROS 2 development. CWM automates overlay workspace management, enabling developers to work on multiple branches simultaneously without full rebuilds or environment variable conflicts.

## Features

- **Smart diff-based builds** - Automatically detects changed packages via `git diff` and builds only what's needed
- **ABI-safe reverse dependency resolution** - Rebuilds affected packages via `build`/`build_export` dependencies to prevent ODR violations and runtime crashes; runtime-only (`exec`) dependents are skipped
- **Environment isolation** - Activates per-worktree environment (ROS overlays, `AMENT_PREFIX_PATH`) via `cwm activate` / `cwm deactivate`
- **Per-worktree `ROS_DOMAIN_ID`** - Each worktree leases its own domain ID and localhost-only discovery, so nodes from parallel worktrees never see each other
- **Optimised colcon arguments** - Generates `--packages-select` and `--allow-overriding` flags automatically
- **Multi-repo worktrees** - One worktree can check out several repositories from `src/` on the same branch; changes across all of them feed a single diff-based build

## Installation

```bash
uv tool install .
# or
pip install .
```

## Quick Start

### Shell integration (one-time setup)

Add the following line to `~/.bashrc` (or `~/.zshrc`) so that `cwm activate` and `cwm deactivate` can mutate the current shell environment:

```bash
eval "$(cwm shell-init)"
```

Without shell integration you can still activate a worktree with the long form:

```bash
source <(cwm activate <branch>)
deactivate
```

### Adopting an existing workspace

If you already have a colcon workspace (e.g. `~/ws/ibis_ws` with `src/autoware.universe/` cloned):

```bash
cd ~/ws/ibis_ws
cwm init                           # ROS 2 underlay auto-detected; repo auto-selected if unique
cwm worktree add feature-perception
cwm activate feature-perception
cwm ws build
cwm deactivate
```

If multiple repositories are in `src/`, choose the *default set* — the
repositories checked out into a new worktree when `--repos` is not given:

```bash
cwm repo add autoware.universe
cwm repo add core/autoware_core
cwm worktree add feature-perception                 # both repositories
cwm worktree add fix-core --repos core/autoware_core # just one
```

`cwm init --repo PATH` may also be repeated to seed the default set.

### Starting fresh

```bash
mkdir my_ws && cd my_ws
cwm init                           # creates .cwm/ and worktrees/ only
mkdir src
git clone <your-repo> src/my_repo
cwm repo add my_repo               # add it to the default set
colcon build --symlink-install
cwm worktree add feature-perception
cwm activate feature-perception
cwm ws build
cwm deactivate
```

## Commands

### Shell / setup

| Command | Description |
|---------|-------------|
| `cwm init [--underlay PATH] [--repo PATH]...` | Initialise a CWM project (underlay auto-detected; repo auto-selected if `src/` has a single git repo; `--repo` is repeatable) |
| `cwm activate [branch]` | Activate a worktree environment (interactive menu when branch is omitted) |
| `cwm deactivate` | Restore the previous environment (provided by shell integration) |
| `cwm switch <branch>` | Activate a worktree **and** navigate to it in one step |
| `cwm cd [branch [repo]\|repo\|base]` | Jump to a worktree root or one of its repository checkouts via shell integration |
| `cwm shell-init` | Print the shell integration function — add `eval "$(cwm shell-init)"` to `.bashrc` |

### Repository management

| Command | Description |
|---------|-------------|
| `cwm repo show` | List the default repository set |
| `cwm repo add <path>` | Add a repository (relative to `src/`) to the default set |
| `cwm repo remove <path>` | Remove a repository from the default set (existing worktrees are untouched) |
| `cwm repo switch <path>` | Set the default set to exactly this one repository |

Worktrees flatten each repository to its basename (`core/autoware_core` →
`<branch>_ws/src/autoware_core`), so two repositories with the same basename
cannot be used in the same worktree; CWM rejects such a selection.

### Workspace operations

| Command | Description |
|---------|-------------|
| `cwm ws build [--dry-run] [--no-rdeps] [--rdeps-depth N]` | Build changed packages + their ABI reverse deps (`build`/`build_export`; `exec`-only excluded) in the active worktree |
| `cwm ws test [-w BRANCH] [--dry-run] [--no-rdeps]` | Run `colcon test` on changed packages + their ABI reverse deps (underlay+overlay sourced) |
| `cwm ws test-result [-w BRANCH]` | Show the test summary; exits non-zero if any test failed (`--return-code-on-test-failure`) |
| `cwm ws clean [--all]` | Clean build artifacts |
| `cwm ws status [--json]` | Show the state of the base workspace and all worktrees |

### Worktree management

| Command | Description |
|---------|-------------|
| `cwm worktree add <branch> [--repos a,b]` | Create a new overlay worktree with each selected repository (default: the default set) checked out on `<branch>` |
| `cwm worktree focus <branch> [--add REPO]... [--remove REPO]... [--list]` | Add or remove repositories in an existing worktree (`--rm` is an alias; removing the last one is refused) |
| `cwm worktree remove <branch> [--force] [--delete-branch]` | Remove a worktree (every repository checkout) and its artifacts; also syncs `git worktree` state |
| `cwm worktree list` | List all managed worktrees |
| `cwm worktree prune [--force]` | Remove stale worktree state and run `git worktree prune` |

Several commands accept `--json` for machine-readable output: `cwm ws status`, `cwm worktree add`, `cwm worktree focus`, `cwm worktree remove`, and `cwm worktree list`.

For each repository, `worktree add` / `focus --add` resolve `<branch>` as follows:

1. a local branch `<branch>` exists → it is checked out;
2. otherwise CWM runs `git fetch origin <branch>` (failures and offline use are
   tolerated); if `origin/<branch>` exists, a local tracking branch is created
   from it;
3. otherwise `<branch>` is created from the base checkout's `HEAD`.

If any repository fails, everything created by that call (checkouts, newly
created branches, the workspace directory and its metadata) is rolled back.
The base checkout's `HEAD` at that moment is recorded per repository and used
as the diff base for `cwm ws build` / `ws test` / `inspect changed`, which take
the union of changed files across all repositories in the worktree.

### Inspection / tooling

| Command | Description |
|---------|-------------|
| `cwm inspect env <branch>` | Show environment variables and setup script paths for a worktree (JSON) |
| `cwm inspect detect [--cwd PATH]` | Detect whether the directory is inside a CWM project (outputs JSON) |
| `cwm inspect changed [-w BRANCH] [--json]` | Preview changed packages + their ABI reverse-dep rebuild set without building |
| `cwm inspect graph [-w BRANCH] [--json]` | Print the package dependency graph (`build`/`build_export` edges), scan only |

`cwm doctor [--json]` gives a cross-cutting health check: base build/dirty
state with a count of stale base build dirs (run `cwm base doctor --fix` to
repair), plus each worktree's built/dirty/missing status.

`ws build --rdeps-depth N` bounds the reverse-dependency rebuild to `N` levels
(`1` = direct consumers only) — a middle ground between the full transitive
rebuild (default) and `--no-rdeps` (skip entirely; the two are mutually
exclusive).

### Base workspace

| Command | Description |
|---------|-------------|
| `cwm base update [-- <colcon args>]` | Pull every repository in the default set (reported per repo) and rebuild the base workspace |
| `cwm base build [-- <colcon args>]` | Rebuild the base workspace without pulling |
| `cwm base clean [--yes]` | Remove the base build artifacts (build/, install/, log/) |
| `cwm base status [--json]` | Show whether the base is built and dirty |
| `cwm base doctor [--fix] [--json]` | Detect (and with `--fix` delete) stale build dirs pointing at missing sources |

`base update` and `base build` source the ROS 2 underlay
(`underlay` in `.cwm/config.yaml`, e.g. `/opt/ros/jazzy`) before invoking
colcon, symmetric with the worktree build path. Extra arguments after `--` are
forwarded to `colcon build` (e.g. `cwm base build -- --continue-on-error`).

`base clean` removes the shared base install that every worktree overlays as
its underlay, so all worktrees will need rebuilding afterwards (it prompts for
confirmation unless `--yes` is given). `cwm ws clean --base` is deprecated in
favour of `cwm base clean`.

`base doctor` reads each `build/<pkg>/CMakeCache.txt` and flags build
directories whose source no longer exists (e.g. a moved or deleted package),
which otherwise surface as `CMake Error`s on the next build; `--fix` deletes
only those directories.

### AI agent integration

CWM intercepts `git worktree` invocations inside a CWM project so that AI
coding agents (or any tool that defaults to raw `git` knowledge) can drive the
overlay workflow without breaking the `<branch>_ws/src/<repo>` layout. The
interception is two-tiered:

1. **Shell function** — `eval "$(cwm shell-init)"` installs a `git()` function
   that intercepts `git worktree …` whenever the current directory (or an
   ancestor) contains `.cwm/`. Activation is *not* required.
2. **PATH shim** — `cwm activate <branch>` prepends `<project>/.cwm/bin` to
   `PATH`. The `git` script there forwards `worktree` subcommands to CWM and
   delegates everything else to the real `git`. This catches `git` calls made
   from subprocesses (Python `subprocess`, `bash -c …`) that bypass the shell
   function.

For an agent, `git worktree add -b feature-x ../feature-x` then transparently
creates `worktrees/feature-x_ws/`, drops a symlink at `../feature-x`, and
prints the next step (`source <(cwm activate feature-x)`) to stderr. Both the
symlink and the real workspace are valid working paths.

The repository is taken from the current directory: run inside
`src/<repo>` (or a worktree checkout of it), the CWM worktree is created with
just that repository — or, if a CWM worktree for the branch already exists, the
repository is added to it like `cwm worktree focus --add`. Run elsewhere in the
project, the default set is used.

| Subcommand | Behaviour |
|---|---|
| `git worktree add [-b] <path> [<branch>]` | Creates the CWM workspace (or adds the current repository to it) and a symlink at `<path>`. Recognised flags (`-f`, `--detach`, `--lock`, `--orphan`, …) are accepted but the CWM layout is always produced. |
| `git worktree list [--porcelain]` | Lists CWM-managed worktrees (one row each, however many repositories) plus the base checkouts in `git worktree list` format. |
| `git worktree remove <path>` | Resolves `<path>` (symlink or real workspace) back to a branch and runs `cwm worktree remove`. |
| `git worktree prune` | Forwards to `cwm worktree prune`. |
| `lock` / `unlock` / `move` / `repair` | Refused with a pointer to `cwm worktree --help`. |

The symlink path is recorded in the worktree metadata and removed automatically
by `cwm worktree remove`.

### ROS_DOMAIN_ID per worktree

`cwm worktree add` leases the lowest free ID from `domain_id_pool` in
`.cwm/config.yaml` (inclusive, default `[215, 232]`) and records it in the
worktree metadata; `cwm worktree remove` releases it. `cwm activate` (and
`cwm inspect env`) then export

```bash
ROS_DOMAIN_ID=<leased id>
ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
```

and `deactivate` restores the previous values. The default pool is chosen
because Linux-safe domain IDs are 0–101 and 215–232, and `ament_cmake_ros`'s
domain coordinator (used by isolated launch tests) hands out 1–100. Worktrees
created before leasing existed get an ID lazily on their next activation.
`cwm worktree add` fails with a clear error when the pool is exhausted. The
leased ID appears in `cwm worktree list` and `cwm ws status` (and their JSON
as `ros_domain_id`).

### Concurrency / locking

All worktree lifecycle operations (`add`, `focus`, `remove`, `prune`, domain-ID
leases, and the agent `git worktree` interceptions) are serialized project-wide via a POSIX `flock`
on `.cwm/lock`. Concurrent `cwm worktree add` invocations run one after another,
preventing corruption of `.git/worktrees` and the per-branch metadata. This is
an intentional Linux/ROS 2 trade-off: build and test operations are deliberately
*not* locked, so parallel builds across worktrees remain fully concurrent.

### colcon passthrough

After activation, `cwm` acts as a drop-in replacement for `colcon`. Any flags
not recognised by `cwm ws build` are forwarded to colcon, and any colcon verb not
defined by cwm is run verbatim in the active worktree workspace:

```bash
cwm activate feature-perception

# Smart diff-based build; extra flags forwarded to colcon
cwm ws build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

# Run tests, list packages, inspect the graph — any colcon verb works
cwm test --packages-select my_pkg
cwm list
cwm graph
```

## Architecture

CWM consists of three core modules:

1. **Colcon Discovery Controller** (`core/colcon_discovery.py`) - Detects changed packages via git diff and controls colcon's package discovery
2. **Dependency Graph Analyzer** (`core/dependency_graph.py`) - Parses `package.xml` files to build a DAG and computes ABI reverse dependencies (`build_depends`/`build_export_depends`; `exec_depends` are runtime-only and excluded)
3. **Worktree State Manager** (`core/worktree_state.py`) - Manages git worktree lifecycle and environment isolation

### Directory Structure

CWM treats the workspace root itself as the base workspace — matching standard colcon conventions.
Each worktree contains a checkout of each of its repositories under `src/<repo-basename>/`:

```
my_ws/                      # project root = base colcon workspace
├── .cwm/                   # CWM metadata and config
│   ├── config.yaml         # underlay, default repos, worktrees_dir
│   └── worktrees/          # per-branch metadata YAML files (repos + base SHAs)
├── src/
│   ├── autoware.universe/  # git repository (in the default set)
│   └── core/
│       └── autoware_core/  # git repository (in the default set)
├── build/
├── install/
├── log/
└── worktrees/              # overlay worktrees (created by cwm worktree add)
    └── feature-X_ws/
        ├── src/
        │   ├── autoware.universe/  # git worktree checkout
        │   └── autoware_core/      # git worktree checkout (flattened to basename)
        ├── build/
        ├── install/
        └── log/
```

## Shell Completion

`cwm` supports tab completion for subcommands, worktree branch names, and ROS 2 underlay paths.

**Bash** — add to `~/.bashrc`:

```bash
eval "$(_CWM_COMPLETE=bash_source cwm)"
```

**Zsh** — add to `~/.zshrc`:

```zsh
eval "$(_CWM_COMPLETE=zsh_source cwm)"
```

**Fish** — save to `~/.config/fish/completions/cwm.fish`:

```fish
_CWM_COMPLETE=fish_source cwm | source
```

For faster shell startup, generate the completion script once:

```bash
_CWM_COMPLETE=bash_source cwm > ~/.cwm-complete.bash
# then in ~/.bashrc:
source ~/.cwm-complete.bash
```

| Argument / Option | Completion |
|---|---|
| `cwm worktree add BRANCH` | Local and remote git branch names from the default repositories |
| `cwm worktree add --repos`, `focus --add` | Repositories under `src/` (comma lists supported) |
| `cwm worktree focus --remove`, `cwm cd BRANCH REPO` | Repositories in that worktree |
| `cwm worktree remove BRANCH` | Existing CWM worktree names |
| `cwm activate BRANCH` | Existing CWM worktree names |
| `cwm init --underlay` | Detected ROS 2 distro paths (`/opt/ros/*`) |

## Development

```bash
# Install with dev dependencies
uv sync --group dev

# Run tests
uv run python -m pytest tests/ -v
```

## License

Apache License 2.0
