# Jude Film Worker (Runpod Pod)

The worker is the disposable GPU runtime used by Jude Code's new film director. It
accepts authenticated jobs, downloads eligible public Hugging Face model weights to
the Pod's ephemeral disk, and returns generated MP4/WAV files to the local agent.
The application does **not** call OpenRouter or run model-card Python/install steps.

## Build and publish

The image recipe uses Runpod's pinned PyTorch/CUDA base. Build it for Runpod's Linux
x86_64 hosts and push it to a registry that the Runpod account can pull:

```sh
docker buildx build --platform linux/amd64 \
  -t <your-registry>/<your-image>:<version> --push \
  plugins/runpod/film-worker
```

Set the resulting image reference in the local Jude Code environment:

```dotenv
JUDECODE_FILM_WORKER_IMAGE=<your-registry>/<your-image>:<version>
RUNPOD_API_KEY=<Runpod API key>
```

Use the registry credential in Runpod if the image is private. Do not put either
credential in the Dockerfile, image, source control, or a model card. The worker
does not need the Runpod API key or a Hugging Face token; the controller sends only
a short-lived, per-Pod worker token.

## Pod contract

- Target: one on-demand Secure Cloud GPU Pod per film production.
- Port: `8000/http` only; the API requires the per-Pod bearer token.
- Storage: container disk only. No network volume, persistent volume, SSH or Jupyter.
- Model delivery: download the selected public model revision while the Pod is up.
  Weights and temporary outputs disappear when the Pod is terminated.
- Controller: waits for `/healthz`, submits one job at a time, downloads and verifies
  each artifact to the local Mac, then terminates the Pod. Local FFmpeg assembles the
  final movie after teardown.
- GPU/CUDA: selection is based on current Runpod catalog data and a conservative
  metadata estimate. The first real GPU run is required to validate VRAM and output.

The proxy URL is publicly routable. The worker authenticates every route and the
controller never returns its token. Do not disable authentication or expose any other
port. A host crash or Runpod API outage can prevent immediate termination; Jude keeps
an owner-marked lease for recovery and offers `film_cleanup` for agent-owned leftovers.

## Local checks

```sh
cd plugins/runpod/film-worker
python -m pip install -r requirements.txt pytest httpx==0.28.1
python -m pytest -c /dev/null -p no:cacheprovider tests -v
```

Run these commands in an isolated venv. The tests exercise API authentication, request
validation and job idempotency with stubbed inference; they are not GPU or live Runpod
acceptance tests.
