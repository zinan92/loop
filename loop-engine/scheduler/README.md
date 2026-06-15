# Scheduler

The scheduler is a user-space daemon controlled by `loopctl.py`.

It is intentionally not installed through launchd, cron, or login autostart in
v1. Loading the scheduler is an explicit operator action.

```bash
python3 loop-engine/bin/loopctl.py scheduler load --project example-product
python3 loop-engine/bin/loopctl.py scheduler status --project example-product
python3 loop-engine/bin/loopctl.py scheduler uninstall --project example-product
```

Job state is separate:

```bash
python3 loop-engine/bin/loopctl.py start --project example-product
python3 loop-engine/bin/loopctl.py pause --project example-product
python3 loop-engine/bin/loopctl.py resume --project example-product
python3 loop-engine/bin/loopctl.py stop --project example-product
```

Runtime scheduler entrypoint scripts are generated locally and ignored by Git.
