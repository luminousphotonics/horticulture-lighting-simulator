# Render deployment

The supported Render deployment is the public, authenticated-precomputed-only
application on one ASGI worker. The committed repository-root `precomputed/`
directory is the production data source. Render never starts `--live`, a native
worker, a runtime workspace, or live routes.

The checked-in [`render.yaml`](../../render.yaml) targets Render Standard with
the measured cache and work limits below. Its fields follow Render's current
[Blueprint specification](https://render.com/docs/blueprint-spec). The service
binds `0.0.0.0` and Render's assigned `PORT`, as required by Render's
[web-service runtime](https://render.com/docs/web-services), and uses
`/health/ready` for Render's [health check](https://render.com/docs/health-checks).

## Build and start

The Blueprint uses a normal, non-editable package installation:

```text
Build command: pip install .
Start command: fspm-optics --host 0.0.0.0 --port "$PORT" --precomputed-root ./precomputed
Health check: /health/ready
```

The production custom domain must also be in the ASGI trusted-host allowlist:

```text
FSPM_ALLOWED_HOSTS=sim.luminousphotonics.com
```

The built-in `*.onrender.com` allowance remains active. The public HTML emits
canonical, description, Open Graph, Twitter card, theme-color, and robots
metadata directly from the server response. Its absolute social image URL is
`https://sim.luminousphotonics.com/static/og-image.png`; the packaged image is
a 1200×630 opaque PNG served with ETag-based revalidation and no session state.

The equivalent manual shell command is:

```bash
export PORT=10000
fspm-optics \
  --host 0.0.0.0 \
  --port "$PORT" \
  --precomputed-root ./precomputed
```

Run it from the repository root. Startup authenticates all 24 bundles before
readiness becomes true. A missing, relocated, incomplete, or invalid catalog
fails application startup. Readiness reads that startup state and never
rehashes the catalog per probe. Liveness only confirms that the process and
event loop respond.

```bash
curl -fsS http://127.0.0.1:10000/health/live
curl -fsS http://127.0.0.1:10000/health/ready
```

Render provides `PORT` and other reserved values as documented in its
[environment-variable reference](https://render.com/docs/environment-variables).
Do not add `--live` to any production command.

## Existing-service dashboard migration

Migrate the existing Render web service in place so its service identity,
`rad-simul-engine.onrender.com` hostname, and configured custom domain remain
attached. Do this only after this implementation has been merged into `main`:
changing the source repository or branch can immediately trigger a deploy when
auto-deploy is enabled.

In the `rad-simul-engine` dashboard, set or confirm these exact values:

| Render dashboard field | Value |
| --- | --- |
| Service type | Web Service |
| Service name | `rad-simul-engine` (preserve) |
| Source repository | `luminousphotonics/fspm-optics-engine` |
| Branch | `main` |
| Root directory | blank |
| Runtime | Python |
| Instance type | Starter for an initial low-traffic verification; Standard recommended for public traffic |
| Auto-deploy | On Commit |
| Build filters | none |
| Build command | `pip install .` |
| Pre-deploy command | blank |
| Start command | `fspm-optics --host 0.0.0.0 --port "$PORT" --precomputed-root ./precomputed` |
| Health check path | `/health/ready` |
| Graceful shutdown delay | 30 seconds |
| Persistent disk | none |
| Deploy hook | leave blank/unchanged; none is required by this application |
| Custom domain | preserve `sim.luminousphotonics.com` and its existing DNS verification |

Three legacy settings must not survive the migration:

| Legacy setting | Replace with |
| --- | --- |
| Build: `python -m pip install -e ".[web,api,scientific]"` | `pip install .` |
| Start: `python scripts/deploy/render_start.py` | `fspm-optics --host 0.0.0.0 --port "$PORT" --precomputed-root ./precomputed` |
| Health check: `/healthz` | `/health/ready` |

Also replace the old source repository with
`luminousphotonics/fspm-optics-engine`, select `main`, and leave the root
directory blank. Do not manually define `PORT`; Render supplies it. Set
`PYTHON_VERSION=3.12.3` and
`FSPM_ALLOWED_HOSTS=sim.luminousphotonics.com`. Those two values are the
required explicit deployment environment. `FORWARDED_ALLOW_IPS` is optional;
leave its `127.0.0.1` default unless a known proxy topology requires a broader,
trusted range.

For the initial Starter verification, set the bounded capacity variables to:

```text
FSPM_BASE_BUNDLE_CACHE_SIZE=2
FSPM_PLAYBACK_CACHE_SIZE=4
FSPM_MAX_SESSIONS=128
FSPM_SESSION_TTL_SECONDS=900
FSPM_MAX_CONCURRENT_WORK=1
FSPM_WORK_WAIT_SECONDS=5
FSPM_PLAYBACK_ACTIVE_LIMIT=1
FSPM_PLAYBACK_WAITING_LIMIT=32
FSPM_PLAYBACK_RECORD_LIMIT=128
FSPM_PLAYBACK_RECORD_TTL_SECONDS=900
FSPM_PLAYBACK_EXPIRED_TTL_SECONDS=60
```

Starter is suitable only for the first low-traffic deployment and smoke test.
The measured one-core peak reached 505.5 MiB, leaving no responsible safety
margin under Starter's 512 MB limit. Before public traffic, move the existing
service to Standard and apply the Standard values from `render.yaml` (8/16
caches, 256 sessions, 1,800-second session TTL, 4 active work/playback slots,
128 waiting records, 512 total records, and the same record TTLs). The checked-
in Blueprint is the authoritative configuration for a new Standard service;
its Blueprint service name is `fspm-optics`, while the manual in-place
migration deliberately preserves the existing `rad-simul-engine` name.

After the deploy reports live, complete this smoke checklist through both the
custom and Render hostnames:

1. Confirm the deploy log shows a non-editable `pip install .`, successful
   authentication of all 24 bundles, one Uvicorn worker, and no `--live` or
   native worker startup.
2. Request `/health/live` and `/health/ready` on both
   `https://sim.luminousphotonics.com` and
   `https://rad-simul-engine.onrender.com`; require `200`, and require
   readiness JSON to report `ready`.
3. Open `/` on both hostnames. Require `200`, the Horticulture Lighting
   Simulator UI, and no trusted-host rejection or redirect loop.
4. Inspect the custom-domain HTML source. Confirm the canonical URL is the
   custom-domain root, the exact description is present, and the Open Graph and
   Twitter image URLs are absolute custom-domain URLs rather than values added
   later by JavaScript.
5. Request `/static/og-image.png` on the custom domain. Require `200`,
   `Content-Type: image/png`, a nonempty `ETag`, revalidation caching rather
   than `no-store`, and a 1200×630 preview when inspected.
6. Confirm `/static/homeleaf.png` still has the repository-approved SHA-256
   `f34e4905a9df5b122de1350a0310fab193e73ff213afb41252b8789230b46212`
   and that the navigation link and artwork are unchanged.
7. Run one representative Modularized LED playback, one Conventional LED
   playback, and one 1000W HPS playback. Wait for each queue request to reach
   `completed`; transient saturation retries must remain a polling detail and
   must not appear as a terminal browser failure.
8. For the completed results, load metrics, CSV export, scatter view, heatmap,
   and 3D viewer resources. Confirm request IDs and the documented security
   headers are present on public responses.
9. Confirm the public UI contains no live execution controls. Check the deploy
   and runtime logs for all 24 startup authentications and verify that no native
   worker, workspace, Radiance runtime, restart loop, readiness failure, or
   memory termination appears.
10. Re-request readiness after the playbacks and confirm it remains `200`.
    Review Render memory/CPU metrics before enabling normal traffic; upgrade to
    Standard first if the initial verification used Starter.
11. Confirm the custom domain has a valid TLS certificate and loads without a
    browser warning. Re-scrape the custom-domain URL in each target social
    platform's sharing debugger when its cache needs refreshing.

## Configuration

All limits are read once at application construction. Invalid or out-of-range
values fail startup.

| Variable | Allowed | Code default | Standard Blueprint | Starter profile |
| --- | ---: | ---: | ---: | ---: |
| `FSPM_BASE_BUNDLE_CACHE_SIZE` | 1–24 | 4 | 8 | 2 |
| `FSPM_PLAYBACK_CACHE_SIZE` | 1–64 | 8 | 16 | 4 |
| `FSPM_MAX_SESSIONS` | 1–4096 and at least playback cache | 256 | 256 | 128 |
| `FSPM_SESSION_TTL_SECONDS` | 1–86400 | 1800 | 1800 | 900 |
| `FSPM_MAX_CONCURRENT_WORK` | 1–8 | 4 | 4 | 1 |
| `FSPM_WORK_WAIT_SECONDS` | 0.1–30 | 5 | 5 | 5 |
| `FSPM_PLAYBACK_ACTIVE_LIMIT` | 1–8 | 4 | 4 | 1 |
| `FSPM_PLAYBACK_WAITING_LIMIT` | 1–1024 | 128 | 128 | 32 |
| `FSPM_PLAYBACK_RECORD_LIMIT` | active + waiting through 4096 | 512 | 512 | 128 |
| `FSPM_PLAYBACK_RECORD_TTL_SECONDS` | 1–86400 | 900 | 900 | 900 |
| `FSPM_PLAYBACK_EXPIRED_TTL_SECONDS` | 1–3600 | 60 | 60 | 60 |

`FSPM_ALLOWED_HOSTS` may add a comma-separated custom domain or wildcard
subdomain. The built-in allowlist covers localhost, test clients, and
`*.onrender.com`; a global `*` is rejected. `FORWARDED_ALLOW_IPS` controls which
proxy peers Uvicorn trusts for forwarded headers and defaults to `127.0.0.1`.
Only broaden it when the deployed proxy topology is known and trusted.

The base-bundle and derived-playback caches are thread-safe and bounded.
Identical cold loads use single-flight coordination. Session descriptors are
small, bounded, and removed lazily after TTL expiry. Queue records contain only
validated selectors, timing, state, and the small public result/error payload;
they never retain a loaded bundle or `WebPlayback` object.

The Standard profile dispatches four FIFO playback preparations and admits 128
additional waiting requests. A cached playback returns `200` immediately. A
cold playback returns `202` with a stable request ID, status URL, and suggested
poll delay; it does not keep the create request open. Queue states are
`queued`, `running`, `completed`, `failed`, and `expired`. Status reads are
`no-store`, do not acquire a work slot, and never load bundle data. When all
132 live admission slots are occupied, creation returns stable JSON with
status `503`, code `playback_queue_full`, and `Retry-After: 2`.

Clients should send a stable `Idempotency-Key` for each selection. Repeating
the same key and normalized selector returns the same record; reusing it for a
different selector returns `409`. Terminal records become small expired
tombstones after 15 minutes and are removed 60 seconds later. The browser
stores pending recovery state in `sessionStorage`, resumes polling after a
reload, transparently resubmits after a server restart, and hides transient
network, `429`, and `503` responses with capped exponential backoff and jitter.
Invalid selectors fail before admission and never consume queue capacity.

Use exactly one Uvicorn worker. Playback IDs resolve through process-local
bounded state; multiple independent workers could route a create response and
its follow-up resources to different processes. Two workers would also
duplicate authenticated bundle and derived-view memory on a one-CPU service.

## HTTP and operational behavior

- `/health/live` and `/health/ready` are small, deterministic, and `no-store`.
- `/api/precomputed/playback-requests/{request_id}` is also small and
  `no-store`; queue and health traffic remain responsive during saturated
  playback preparation.
- Immutable playback URLs use long-lived immutable caching, content ETags, and
  conditional responses. HTML and non-fingerprinted package assets revalidate.
- Large package files stream in 64 KiB chunks. Binary playback assets support
  one byte range and return `206` or `416` as appropriate.
- JSON request bodies are limited to 64 KiB. Unsupported media types,
  compression, malformed JSON, unexpected fields, and invalid selectors fail
  cleanly.
- Public requests receive a compatible CSP, frame denial, MIME sniffing
  protection, same-origin resource policy, permissions policy, and request ID.
- Logs contain startup/readiness, graceful shutdown, method, templated route,
  status, elapsed time, request ID, overload, validation, and eviction events.
  Payloads, cookies, and sensitive headers are not logged.
- Render's edge can negotiate response compression. The Python process does not
  spend its single CPU recompressing JSON, ZIP, GLB, PNG, or binary data.

## Repeatable local load test

Start a fresh production-equivalent server, then run:

```bash
python scripts/load_test_public.py \
  --base-url http://127.0.0.1:10000 \
  --server-pid SERVER_PID \
  --queue-levels 25,50,75,100
```

The default run releases synchronized groups of 25, 50, 75, and 100 users.
Selectors vary across systems, bundles, room orientations, aisle modes,
lighting policies, and LED targets. Every user submits with a stable
idempotency token, polls with a 1.5-second initial interval and increasing
jitter, waits for terminal completion, then fetches metrics, visualization
metadata, a heatmap, viewer HTML, scatter-viewer HTML, and the viewer scene.
Admission and resource `429`/`503` responses are retried transparently.

The report separates queue wait, processing, queue drain, and full-resource
latency; counts status polls and retry attempts; samples ready-health latency;
tracks maximum queued/running counts; verifies FIFO using server admission and
start sequences; and records per-level CPU and RSS when `--server-pid` is
provided.

The script refuses a non-loopback target unless `--allow-non-local` is supplied
explicitly. This is a safety guard, not authorization to load-test a deployed
service.

## Measurements and capacity recommendation

Measurements were taken on Linux with Python 3.12.3 over loopback. The one
Uvicorn worker was pinned to one CPU with `taskset`; the client was outside that
affinity. The host has 12 logical CPUs and approximately 32 GB RAM. This
approximates the Standard CPU count but not Render scheduling or network
overhead. Catalog authentication took 1.72 seconds and initial RSS was 111.5
MiB.

All 250 synchronized users across the four levels reached `completed`. There
were no queue admission retries, no failed records, no final subresource
failures, and zero FIFO start-order inversions. Maximum observed running work
was four. All 5,346 periodic readiness probes that reached the server returned
`200`, with about 21–23 ms p50 latency and a 983 ms overall maximum. One
transient client transport error occurred during the 100-user level and the
next probe succeeded. CPU remained approximately 100% of one core. Transient
artifact `503`s were hidden and retried until all 1,500 expected representative
endpoint responses returned `200`; the harness made 5,525 resource retry
attempts, including five transient client transport retries.

| Synchronized users | Queue drain | Queue wait p50 / p95 / p99 / max | Processing p50 / p95 / p99 / max | Preparation / full-user throughput | Full resources | Status polls | Resource retries | Peak RSS |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25 | 45.7 s | 9.6 / 26.0 / 28.0 / 28.0 s | 6.1 / 13.3 / 13.8 / 13.8 s | 0.547 / 0.184 users/s | 136.1 s | 136 | 158 | 468.2 MiB |
| 50 | 97.8 s | 31.7 / 81.0 / 90.3 / 90.3 s | 7.1 / 13.8 / 15.2 / 15.2 s | 0.511 / 0.165 users/s | 302.8 s | 483 | 661 | 481.7 MiB |
| 75 | 175.2 s | 67.2 / 150.3 / 156.1 / 156.1 s | 8.1 / 15.2 / 15.8 / 15.8 s | 0.428 / 0.159 users/s | 471.1 s | 1,070 | 1,732 | 505.5 MiB |
| 100 | 226.7 s | 93.7 / 207.4 / 216.1 / 217.3 s | 7.9 / 14.6 / 16.1 / 16.6 s | 0.441 / 0.159 users/s | 629.8 s | 1,781 | 2,974 | 501.1 MiB |

At 100 synchronized users, every user completed: the median queue wait was
93.7 seconds, p95 was 207.4 seconds, p99 was 216.1 seconds, and the slowest
wait was 217.3 seconds. The five-second settled RSS after the complete
25.7-minute matrix was 462.9 MiB. The run also exercised TTL cleanup: by the
last level the oldest 25
terminal records had become bounded expired tombstones while all current
records remained addressable.

Practical planning limits are:

| Plan | Sustainable active cold/varied flows | Short burst | Mostly idle viewers |
| --- | ---: | ---: | --- |
| Starter, 0.5 CPU / 512 MB | 1 cold preparation | Suggested 32 waiting; expect materially longer waits and memory pressure near the plan limit | Use the conservative 2/4 caches, 128 sessions, and 1/32 queue profile; measure on the actual instance before public traffic |
| Standard, 1 CPU / 2 GB | 4 cold preparations | 100 synchronized users completed; 128 waiting is the configured ceiling | 25-user bursts drained in under a minute; 50–100 are absorbable but take roughly 1.5–4 minutes to prepare and 5–10.5 minutes for the full stressed resource set |

“Active” means server-side playback preparation. Already rendered browser
results consume no server CPU while idle. The 75- and 100-user results prove
bounded eventual completion, not interactive latency. Standard is the minimum
recommended public plan; Starter has half the CPU and one quarter of the
suggested active preparation concurrency, and was not remeasured by this
one-core run. These are operational starting points, not service-level
guarantees. Re-run the harness and inspect Render metrics after changing
catalog content, cache sizes, dependencies, or frontend request patterns.
