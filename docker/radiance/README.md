# Radiance Runtime Image

This image is a batch-style runtime for live Radiance execution. It intentionally
does not define a health check because it is not a long-running service image.

The base image is pinned by immutable digest:

```text
docker.io/ladybugtools/ladybug-radiance:0.2.11@sha256:be585d9ebea1045efe90202597877a555e3ceb142d02b79c0ff27bbf32c1407a
```

To update the base pin:

1. Inspect the replacement tag:

   ```bash
   docker buildx imagetools inspect ladybugtools/ladybug-radiance:<tag>
   ```

2. Confirm the digest is for a supported manifest and Python/Radiance toolchain.
3. Update the `FROM` line and `org.opencontainers.image.base.name` label.
4. Rebuild and run the smoke check:

   ```bash
   python scripts/dev/radiance_docker_smoke.py
   ```

Runtime Python dependencies are installed from
`docker/radiance/requirements.runtime.txt` with `pip --require-hashes`. Regenerate
that file only through the repository uv export workflow documented in the root
README.
