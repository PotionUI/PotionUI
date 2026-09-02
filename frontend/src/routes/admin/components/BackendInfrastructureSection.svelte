<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { isAxiosError } from 'axios';
	import { Alert, Badge, Button, Input, Spinner } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { getApiErrorMessage, logger } from '$lib/utils/logger';
	import { toasts } from '$lib/stores/toast';
	import { adminWebSocket } from '$lib/services/adminWebsocket';
	import {
		getComputeProviders,
		getProviderFields,
		getProvisionedComputeByBackend,
		provisionCompute,
		refreshProvisionedComputeStatus,
		stopProvisionedCompute,
		terminateProvisionedCompute
	} from '$lib/services/admin-api';
	import type { ComputeField, ComputeProvider, ProvisionedCompute } from '$lib/services/admin-api';
	import { statusVariant, stageLabel, formatClockTime, checkedAgo, latestPercent } from './provisionedComputeView';

	/**
	 * Shown on a backend's detail pane. Three states, discriminated by whether
	 * `GET by-backend/{id}` finds a linked row and (when it doesn't) whether
	 * this is an unconfigured `native.remote` backend:
	 *  - a linked `ProvisionedCompute` row exists → a status card keyed off
	 *    `row.status` (provisioning/running/stopped/missing/unreachable/failed/unknown),
	 *    kept live by the admin WebSocket's `compute_status` broadcasts, with a
	 *    30s poll as a fallback if the socket drops.
	 *  - no row, and the backend is an unconfigured `native.remote` row → the
	 *    provision form (provider select → dynamic descriptor fields → Provision),
	 *    which rents compute through a `ComputeProvisioner` plugin and fills this
	 *    backend in (`POST /api/admin/provisioning`). The same form reopens over
	 *    a `failed` row via `reprovisioning` - the server replaces the row.
	 *  - no row otherwise (hand-connected, or a non-remote driver) → renders
	 *    nothing; the sibling `BackendForm` below already covers connect-by-hand.
	 * The caller re-mounts this with `{#key backendId}` when the selected backend
	 * changes, so `onMount` re-runs cleanly instead of reacting to a changing prop.
	 */
	const NATIVE_REMOTE_DRIVER = 'native.remote';

	let {
		backendId,
		backendDriver,
		configured,
		backendEnabled,
		onStopped,
		onProvisioned,
		onTerminated,
		onEnableBackend
	}: {
		backendId: string;
		backendDriver: string;
		configured: boolean;
		backendEnabled: boolean;
		onStopped: () => void;
		onProvisioned: () => void;
		onTerminated: () => void;
		onEnableBackend: () => Promise<void> | void;
	} = $props();

	const POLL_INTERVAL_MS = 30000;
	let pollHandle: ReturnType<typeof setInterval> | null = null;
	let unsubscribeComputeStatus: (() => void) | null = null;
	let checkedAgoTickHandle: ReturnType<typeof setInterval> | null = null;

	let loading = $state(true);
	let row = $state<ProvisionedCompute | null>(null);
	let pendingAction = $state<'stop' | 'terminate' | null>(null);
	let acting = $state(false);
	// Ticks every 5s purely to force the `checkedAgo(row.status_checked_at)`
	// label to re-render - the label's own computation always reads the clock.
	let checkedAgoTick = $state(0);

	async function pollStatus() {
		if (!row) return;
		try {
			const response = await refreshProvisionedComputeStatus(row.id);
			if (response.success && response.data) row = response.data;
		} catch {
			// Transient poll failure - the next tick retries, nothing to surface.
		}
	}

	function startPolling() {
		stopPolling();
		pollHandle = setInterval(pollStatus, POLL_INTERVAL_MS);
	}

	function stopPolling() {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	}

	onMount(async () => {
		try {
			const response = await getProvisionedComputeByBackend(backendId);
			if (response.success && response.data) {
				row = response.data;
				startPolling();
			}
		} catch (e: unknown) {
			if (!isAxiosError(e) || e.response?.status !== 404) {
				toasts.error(getApiErrorMessage(e, 'Failed to load infrastructure status'));
			}
			row = null;
		} finally {
			loading = false;
		}

		if (!row && backendDriver === NATIVE_REMOTE_DRIVER && !configured) {
			void loadProviders();
		}

		unsubscribeComputeStatus = adminWebSocket.onComputeStatus(({ row: incoming }) => {
			if (incoming.backend_id === backendId) row = incoming;
		});
		if (!adminWebSocket.isConnected()) {
			adminWebSocket.connectAsync().catch((err) => {
				logger.error('Admin WebSocket unavailable - infrastructure status will not stream live:', err);
			});
		}

		checkedAgoTickHandle = setInterval(() => (checkedAgoTick += 1), 5000);
	});

	onDestroy(() => {
		stopPolling();
		unsubscribeComputeStatus?.();
		if (checkedAgoTickHandle !== null) clearInterval(checkedAgoTickHandle);
	});

	function requestStop() {
		pendingAction = 'stop';
	}

	function requestTerminate() {
		pendingAction = 'terminate';
	}

	async function confirmPendingAction() {
		if (!pendingAction || !row) return;
		const kind = pendingAction;
		acting = true;
		try {
			if (kind === 'stop') {
				const response = await stopProvisionedCompute(row.id);
				if (response.success && response.data) {
					row = response.data;
					toasts.success(`"${row.profile_name}" stopped`);
					onStopped();
				} else {
					toasts.error(response.message || 'Failed to stop compute');
				}
			} else {
				const response = await terminateProvisionedCompute(row.id);
				if (response.success) {
					toasts.success(`"${row.profile_name}" terminated`);
					stopPolling();
					row = null;
					onTerminated();
				} else {
					toasts.error(response.message || 'Failed to terminate compute');
				}
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, kind === 'stop' ? 'Failed to stop compute' : 'Failed to terminate compute'));
		} finally {
			acting = false;
			pendingAction = null;
		}
	}

	// ---- Provision form: unconfigured native.remote backend, no linked row,
	// or a `failed` row being provisioned again ----

	let reprovisioning = $state(false);
	let providers = $state<ComputeProvider[]>([]);
	let providersLoading = $state(false);
	let selectedProviderId = $state('');
	let providerFields = $state<ComputeField[]>([]);
	let providerFieldsLoading = $state(false);
	// `any`, not `unknown` - mirrors BackendForm's own `draft: Record<string, any>`,
	// so `bind:value` on both text and native number inputs below type-checks
	// without per-field casts.
	let provisionValues = $state<Record<string, any>>({});
	let provisioning = $state(false);
	let provisionError = $state<string | null>(null);

	let canProvision = $derived(
		!provisioning &&
			!providerFieldsLoading &&
			selectedProviderId !== '' &&
			providerFields.every((f) => !f.required || provisionValues[f.key] !== '')
	);

	// The selected provider's own optional referral/signup link (e.g.
	// RunPod's) - both fields "" when a provider has none, in which case
	// nothing renders.
	let selectedProvider = $derived(providers.find((p) => p.provider_id === selectedProviderId) ?? null);

	let showProvisionForm = $derived(
		(!row && backendDriver === NATIVE_REMOTE_DRIVER && !configured) || reprovisioning
	);

	function startReprovision() {
		reprovisioning = true;
		provisionError = null;
		if (providers.length === 0) void loadProviders();
	}

	async function loadProviders() {
		providersLoading = true;
		try {
			const response = await getComputeProviders();
			if (response.success && response.data) {
				providers = response.data.providers;
				if (providers[0]) await onProviderChange(providers[0].provider_id);
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to load compute providers'));
		} finally {
			providersLoading = false;
		}
	}

	async function onProviderChange(providerId: string) {
		selectedProviderId = providerId;
		provisionValues = {};
		providerFields = [];
		await loadProviderFields();
	}

	/** Re-fetches this provider's fields against the CURRENT `provisionValues` -
	 * every dependent field (`depends_on`) resolves its options off whatever has
	 * been submitted so far. Called on provider change, on every change to a
	 * field something else depends on, and once more after the very first fetch
	 * if a field's own default just satisfied a dependency the first fetch
	 * didn't know about yet (e.g. a configured `data_center_id` default hydrating
	 * `gpu_type_id`'s options on load) - guarded by `depth` against looping. */
	async function loadProviderFields(depth = 0) {
		if (!selectedProviderId) return;
		providerFieldsLoading = true;
		provisionError = null;
		try {
			const response = await getProviderFields(selectedProviderId, provisionValues);
			if (response.success && response.data) {
				const nextFields = response.data.fields;
				const nextValues: Record<string, any> = {};
				let changed = false;
				for (const field of nextFields) {
					const current = provisionValues[field.key];
					const resolved = current !== undefined ? current : (field.default ?? '');
					nextValues[field.key] = resolved;
					if (resolved !== current) changed = true;
				}
				providerFields = nextFields;
				provisionValues = nextValues;
				if (changed && depth === 0) {
					await loadProviderFields(1);
					return;
				}
			} else {
				provisionError = response.message || 'Failed to load provider fields';
			}
		} catch (e: unknown) {
			provisionError = getApiErrorMessage(e, 'Failed to load provider fields');
		} finally {
			providerFieldsLoading = false;
		}
	}

	function onSelectFieldChange(field: ComputeField, value: string) {
		provisionValues[field.key] = value;
		if (providerFields.some((f) => f.depends_on.includes(field.key))) {
			void loadProviderFields();
		}
	}

	// A provisioner's own error message (see ComputeProvisionerError - surfaced
	// verbatim by the server, never rewritten here) is shown as-is; this only adds
	// a follow-up hint for the specific case an admin can actually act on themselves.
	function looksLikeAuthFailure(message: string): boolean {
		return /api key|unauthoriz|forbidden|\b401\b|\b403\b|credential/i.test(message);
	}

	async function submitProvision() {
		if (!canProvision) return;
		provisioning = true;
		provisionError = null;
		try {
			const response = await provisionCompute({
				provider_id: selectedProviderId,
				backend_id: backendId,
				values: provisionValues
			});
			if (response.success && response.data) {
				toasts.info(`Bringing up "${response.data.profile_name}"…`);
				row = response.data;
				reprovisioning = false;
				startPolling();
				onProvisioned();
			} else {
				provisionError = response.message || 'Failed to provision compute';
			}
		} catch (e: unknown) {
			provisionError = getApiErrorMessage(e, 'Failed to provision compute');
		} finally {
			provisioning = false;
		}
	}
</script>

{#snippet timeline(entries: ProvisionedCompute['progress'], live: boolean)}
	{#if entries.length === 0}
		<div class="flex items-center gap-2 text-sm text-fg-muted">
			<Spinner size="sm" />
			<span>Starting…</span>
		</div>
	{:else}
		<div>
			{#each entries as entry, i (i)}
				{@const isLast = i === entries.length - 1}
				<div class="relative flex gap-3 pb-3 last:pb-0">
					<div class="relative flex-shrink-0 w-4 flex justify-center">
						{#if !isLast}
							<span class="absolute top-3 bottom-0 w-px bg-line"></span>
						{/if}
						{#if isLast && live}
							<Spinner size="sm" />
						{:else if isLast}
							<span class="mt-1.5 w-1.5 h-1.5 rounded-full bg-danger"></span>
						{:else}
							<span class="mt-1.5 w-1.5 h-1.5 rounded-full bg-signal"></span>
						{/if}
					</div>
					<div class="flex-1 min-w-0 flex items-start justify-between gap-3">
						<div class="min-w-0">
							<p class="text-sm font-medium text-fg">{stageLabel(entry.stage)}</p>
							<p class="text-sm text-fg-muted">{entry.message}</p>
						</div>
						<span class="font-mono tabular-nums text-xs text-fg-subtle flex-shrink-0">{formatClockTime(entry.at)}</span>
					</div>
				</div>
			{/each}
		</div>
	{/if}
{/snippet}

{#if row && !showProvisionForm}
	<DetailSection label="Infrastructure">
		{#snippet headerExtra()}
			<Badge variant={statusVariant(row!.status)} size="sm" dot class="uppercase">{row!.status}</Badge>
		{/snippet}
		<div class="space-y-4">
			{#if row.status === 'provisioning'}
				{@const percent = latestPercent(row.progress)}
				{#if percent !== null}
					<div class="space-y-1">
						<div class="h-1 rounded bg-surface-3 overflow-hidden">
							<div class="h-1 rounded bg-signal" style="width: {percent}%"></div>
						</div>
						<p class="text-right font-mono tabular-nums text-xs text-fg-subtle">{percent}%</p>
					</div>
				{/if}
				{@render timeline(row.progress, true)}
				<div class="flex items-center justify-end">
					<Button variant="danger" size="sm" icon="trash" disabled={acting} onclick={requestTerminate}>
						Terminate
					</Button>
				</div>
			{:else if row.status === 'failed'}
				<Alert variant="danger" density="compact" title="Provisioning failed">
					{row.status_detail}
					{#snippet actions()}
						<div class="flex items-center gap-2">
							<Button variant="secondary" size="sm" onclick={startReprovision}>Provision again</Button>
							<Button variant="danger" size="sm" icon="trash" disabled={acting} onclick={requestTerminate}>
								Terminate
							</Button>
						</div>
					{/snippet}
				</Alert>
				{@render timeline(row.progress, false)}
			{:else}
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
					<dt class="text-fg-subtle">Pod ID</dt>
					<dd class="font-mono tabular-nums text-fg-muted truncate min-w-0">{row.resource_ref ?? row.handle}</dd>

					<dt class="text-fg-subtle">Provider</dt>
					<dd class="font-mono text-fg-muted truncate min-w-0">{row.provider_id}</dd>
				</dl>
				{#if row.status !== 'running' && row.status_detail}
					<p class="text-sm text-fg-muted">{row.status_detail}</p>
				{/if}
				{#key checkedAgoTick}
					{#if checkedAgo(row.status_checked_at)}
						<p class="font-mono tabular-nums text-xs text-fg-subtle">{checkedAgo(row.status_checked_at)}</p>
					{/if}
				{/key}

				{#if row.status === 'running' && !backendEnabled}
					<Alert variant="info" density="compact">
						Worker is back. The backend was disabled while it was stopped.
						{#snippet actions()}
							<Button variant="secondary" size="sm" onclick={onEnableBackend}>Enable backend</Button>
						{/snippet}
					</Alert>
				{/if}

				<div class="flex items-center justify-end gap-2">
					<Button
						variant="secondary"
						size="sm"
						icon="pause"
						disabled={acting || row.status !== 'running'}
						onclick={requestStop}
					>
						Stop
					</Button>
					<Button variant="danger" size="sm" icon="trash" disabled={acting} onclick={requestTerminate}>
						Terminate
					</Button>
				</div>
			{/if}
		</div>
	</DetailSection>

	<ConfirmModal
		isOpen={pendingAction !== null}
		title={pendingAction === 'terminate' ? 'Terminate compute' : 'Stop compute'}
		message={pendingAction === 'terminate'
			? row.status === 'provisioning'
				? `Cancel provisioning of "${row.profile_name}"? The half-built pod is torn down and this backend returns to "Not configured".`
				: `Terminate "${row.profile_name}"? This tears down the pod and clears this backend's connection, returning it to "Not configured". This cannot be undone.`
			: `Stop "${row.profile_name}"? This backend is disabled until it's re-provisioned. Models on its volume persist.`}
		variant={pendingAction === 'terminate' ? 'danger' : 'warning'}
		busy={acting}
		on:confirm={confirmPendingAction}
		on:cancel={() => (pendingAction = null)}
	/>
{:else if showProvisionForm}
	<DetailSection label="Infrastructure">
		<div class="space-y-4">
			{#if row?.status === 'failed'}
				<Alert variant="danger" density="compact" title="Previous attempt failed">
					{row.status_detail}
				</Alert>
			{/if}

			{#if provisionError}
				<Alert variant="danger" density="compact">
					{provisionError}
					{#if looksLikeAuthFailure(provisionError)}
						<p class="mt-1">Check the provider plugin's API key in Admin → Plugins.</p>
					{/if}
					{#if providerFields.length === 0 && selectedProviderId}
						<div class="mt-2">
							<Button variant="secondary" size="sm" onclick={() => loadProviderFields()}>Retry</Button>
						</div>
					{/if}
				</Alert>
			{/if}

			{#if providersLoading}
				<div class="flex items-center justify-center py-6"><Spinner size="md" /></div>
			{:else if providers.length === 0}
				<p class="text-sm text-fg-muted">
					No compute providers are available — enable a provider plugin (Admin → Plugins) to
					provision a worker here, or connect one by hand below.
				</p>
			{:else}
				<div>
					<label for="provision-provider" class="label">Provider</label>
					<select
						id="provision-provider"
						class="input"
						value={selectedProviderId}
						onchange={(e) => onProviderChange((e.target as HTMLSelectElement).value)}
					>
						{#each providers as provider (provider.provider_id)}
							<option value={provider.provider_id}>{provider.label}</option>
						{/each}
					</select>
					{#if selectedProvider?.signup_url}
						<p class="text-xs text-fg-subtle mt-1">
							<a
								href={selectedProvider.signup_url}
								target="_blank"
								rel="noopener noreferrer"
								class="text-signal hover:underline"
							>
								{selectedProvider.signup_note || selectedProvider.signup_url}
							</a>
						</p>
					{/if}
				</div>

				{#each providerFields as field (field.key)}
					<div>
						<label for="provision-field-{field.key}" class="label">
							{field.label}{#if field.required}<span class="text-danger"> *</span>{/if}
						</label>
						{#if field.type === 'select'}
							<select
								id="provision-field-{field.key}"
								class="input disabled:opacity-50"
								value={provisionValues[field.key] ?? ''}
								disabled={providerFieldsLoading}
								onchange={(e) => onSelectFieldChange(field, (e.target as HTMLSelectElement).value)}
							>
								{#each field.options ?? [] as option (option.value)}
									<option value={option.value}>{option.label}{option.detail ? ` — ${option.detail}` : ''}</option>
								{/each}
							</select>
						{:else if field.type === 'number'}
							<input
								id="provision-field-{field.key}"
								type="number"
								class="input font-mono tabular-nums"
								bind:value={provisionValues[field.key]}
								placeholder={field.default != null ? String(field.default) : ''}
								required={field.required}
							/>
						{:else}
							<Input
								id="provision-field-{field.key}"
								bind:value={provisionValues[field.key]}
								placeholder={field.default != null ? String(field.default) : ''}
								required={field.required}
							/>
						{/if}
						{#if field.help_text}<p class="text-xs text-fg-subtle mt-1">{field.help_text}</p>{/if}
					</div>
				{/each}

				<div class="flex items-center justify-end">
					<Button variant="primary" size="sm" loading={provisioning} disabled={!canProvision} onclick={submitProvision}>
						{provisioning ? 'Provisioning…' : 'Provision'}
					</Button>
				</div>
			{/if}
		</div>
	</DetailSection>
{/if}
