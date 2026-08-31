# RunPod Provider

Provisions a [RunPod](https://runpod.io) GPU Pod running PotionUI's Remote
Native worker (`worker.py`, `docs/remote-native.md`), so a generation can run
on rented compute instead of the box PotionUI itself is installed on.

## Flow

1. **Admin -> Plugins -> RunPod Provider**: paste a RunPod REST API key
   (console.runpod.io -> Settings -> API Keys). `POST .../validate-key` checks
   it before you save.
2. **Admin -> Remote Compute** (core's `/api/admin/provisioning` API,
   `src.features.provisioning.routes`): pick "runpod" as the provider,
   `GET .../providers/runpod/gpu-types` lists known GPU type ids (see "GPU
   catalog is static" below), and `POST /api/admin/provisioning` with a
   `profile_name` provisions - creating a network volume (reused on a later
   provision of the same profile) mounted at `/models`, then a Pod running
   the configured worker image with a freshly generated
   `POTIONUI_WORKER_TOKEN`.
3. On success, core creates and enables a `native.remote` backend row from
   the returned connection details itself and links it to the provisioned
   row - no manual backend setup. This plugin's job ends at
   `RunpodComputeProvisioner.provision()` (`backend/provisioner.py`), which
   core's provisioning registry dispatches to via the `compute.register`
   hook (`backend/hooks/compute_hooks.py`).
4. `GET /api/admin/provisioning/{id}` reconciles RunPod's reported Pod state
   against a real handshake through the RunPod HTTP proxy
   (`GET {base_url}/v1/worker`), returning one of `running` / `stopped` /
   `missing` / `unreachable`.
5. `POST /api/admin/provisioning/{id}/stop` stops the Pod and disables the
   linked backend row (never removes it). `POST .../terminate` terminates
   the Pod, removes the backend row, and deletes the provisioned-compute
   row. **The network volume is never deleted by either call** - only a
   direct `RunpodComputeProvisioner.terminate()`'s underlying `deprovision`
   call passes `delete_volume=True`, and nothing in this flow sets it.

## Current limits

- **Pod-based MVP only.** This provisions a RunPod GPU Pod, not a Serverless
  endpoint. Serverless (queue-based, scale-to-zero) is a real alternative for
  bursty workloads and is a later phase, not this one.
- **No model sync.** The network volume is created and mounted at `/models`,
  but nothing in this pass populates it - an admin (or a follow-up plugin
  feature) has to get model files onto it themselves before a generation on
  that pod can find them.
- **GPU catalog is static, not live.** RunPod's REST API (v1) has no endpoint
  to list GPU types or pricing - confirmed by enumerating every path in
  `https://rest.runpod.io/v1/openapi.json`. `list_gpu_types()` returns the
  exact `gpuTypeIds` enum from that same spec (so every id it returns is one
  `create_pod` accepts) with public specs for `memory_gb` where confidently
  known, and no live pricing.
- **No pod logs.** RunPod's REST API (v1) has no logs endpoint either - only
  the deprecated GraphQL API's `podLogs` query exposes it, which this plugin
  does not speak, and log retrieval isn't part of the `ComputeProvisioner`
  contract this plugin implements.

## What this plugin does NOT do

- Import the `runpod` Python SDK or call RunPod's GraphQL API - both are
  explicitly out of scope; every call in `backend/client.py` goes through
  `https://rest.runpod.io/v1` via `httpx`.
- Write to PotionUI's backend tables directly. It implements
  `src.plugin_api.compute.ComputeProvisioner`; core's
  `src.features.provisioning.operations` is what creates, enables, disables
  and removes the `native.remote` backend row.
- Delete a network volume as a side effect of stopping or terminating a pod
  through the provisioning API above.
