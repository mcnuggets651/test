# AIrsenal Chat — reliability operations

This experiment remains isolated on `mcnuggets651/test:prototype/airsenal-chat` and never writes FPL account state.

## Everyday command

```bash
cd ~/test
./experiments/airsenal_free/run.sh --private-repo ~/fpl
```

The command now performs a fast preflight, verifies or rebuilds the exact pinned runtime, performs a post-bootstrap integrity check, then runs the genuine H3/H5 solve and private publication. `--doctor-only` performs all runtime/private-boundary checks without running the model.

```bash
./experiments/airsenal_free/run.sh --private-repo ~/fpl --doctor-only
```

Doctor reports are stored mode `0600` under `~/.local/share/airsenal-chat/doctor-pre.json` and `doctor-post.json`. They are local operational diagnostics and are not uploaded as public artifacts.

## Exact runtime cache

The runtime remains pinned by `pins.json`. Bootstrap writes `runtime.json` schema `airsenal-chat-runtime-v2` containing the upstream SHA, license/lock identities, exact Python/uv versions, the pins-file SHA-256, and a digest of installed package names/versions. A repeat run skips dependency reconstruction only when all of those identities, the clean upstream checkout, Python version, AIrsenal version, and installed-distribution digest still match. Any drift causes a deterministic rebuild.

This is a performance optimization only. It does not float packages, relax the lock, change the model, or reuse a stale AIrsenal database snapshot for H3/H5. The football database is still refreshed and frozen separately by each operational run.

## Private health surface

Successful publication remains exactly two fast-forward commits:

1. immutable `airsenal/runs/entry-63984/gw<GW>/<run-id>/...`;
2. a mutable commit that advances both `airsenal/latest/entry-63984.json` and `airsenal/health/entry-63984.json` to that immutable run.

The health record contains only non-secret operational fields: status, entry/GW, last success time, run/context hashes, public experiment SHA, pinned upstream SHA, horizons, and whether execution was direct-local or self-hosted Actions. It deliberately excludes runner/machine/user names and any owner squad/price/auth content.

## Private Mac runner

The private repository owns the service installer/watchdog because the runner is a private infrastructure concern, not an AIrsenal model concern. The governed private tooling installs a custom macOS LaunchAgent that invokes GitHub's required `runsvc.sh` entry point with `RunAtLoad=true` and `KeepAlive=true`, verifies its plist, and offers `status`, `kickstart`, and `doctor` operations.

No hosted-runner fallback is permitted. If the Mac is unavailable, private work queues/fails closed rather than incurring paid compute or moving owner data public.


## macOS JAX/optimizer process isolation

Each H3/H5 horizon now runs AIrsenal prediction generation and transfer optimization in separate fresh Python processes against the same isolated DB copy. This deliberately lets the prediction/JAX process terminate before AIrsenal creates its fork-based optimizer workers, avoiding the upstream-documented JAX + `os.fork()` deadlock class without reducing horizons, optimizer iterations, worker count, transfer search limits, or model components. The prediction-stage tag and pre/post DB hashes are verified before optimization begins.
