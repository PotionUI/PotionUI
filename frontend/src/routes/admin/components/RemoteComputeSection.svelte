<script lang="ts">
	import { onMount } from 'svelte';
	import { Alert, Badge, Button, EmptyState, Input, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { toasts } from '$lib/stores/toast';
	import {
		getComputeProviders,
		getComputeGpuTypes,
		getProvisionedCompute,
		provisionCompute,
		stopProvisionedCompute,
		terminateProvisionedCompute
	} from '$lib/services/admin-api';
	import type { Backend, ComputeGpuType, ComputeProvider, ProvisionedCompute } from '$lib/services/admin-api';
	import RemoteComputeCard from './RemoteComputeCard.svelte';

	/**
	 * "Remote Compute": rent a GPU pod through a registered `ComputeProvisioner`
	 * plugin and let core turn it into a `native.remote` backend row. Lives
	 * inside the Backends admin tab (not a top-level page) since a provisioned
	 * pod's whole purpose is becoming a backend.
	 */
	let {
		backends,
		onOpenBackend
	}: {
		backends: Backend[];
		onOpenBackend: (backendId: string) => void;
	} = $props();

	let providers = $state<ComputeProvider[]>([]);
	let providersLoading = $state(true);
	let providersError = $state<string | null>(null);

	let provisioned = $state<ProvisionedCompute[]>([]);
	let provisionedLoading = $state(true);
	let provisionedError = $state<string | null>(null);

	let selectedProviderId = $state('');
	let gpuTypes = $state<ComputeGpuType[]>([]);
	let gpuTypesLoading = $state(false);
	let gpuSearch = $state('');
	let selectedGpuTypeId = $state('');

	let profileName = $state('');
	let volumeSizeGb = $state('');
	let provisioning = $state(false);
	let provisionError = $state<string | null>(null);

	let actingRowId = $state<string | null>(null);
	let pendingAction = $state<{ kind: 'stop' | 'terminate'; row: ProvisionedCompute } | null>(null);

	let filteredGpuTypes = $derived(
		(() => {
			const q = gpuSearch.trim().toLowerCase();
			if (!q) return gpuTypes;
			return gpuTypes.filter((g) => g.id.toLowerCase().includes(q));
		})()
	);

	let canProvision = $derived(
		!provisioning && selectedProviderId !== '' && selectedGpuTypeId !== '' && profileName.trim() !== ''
	);

	onMount(async () => {
		await Promise.all([loadProviders(), loadProvisioned()]);
	});

	async function loadProviders() {
		providersLoading = true;
		providersError = null;
		try {
			const response = await getComputeProviders();
			if (response.success && response.data) {
				providers = response.data.providers;
				if (!selectedProviderId && providers.length > 0) {
					selectedProviderId = providers[0].provider_id;
				}
			} else {
				providersError = response.message || 'Failed to load compute providers';
			}
		} catch (e: unknown) {
			providersError = getApiErrorMessage(e, 'Failed to load compute providers');
		} finally {
			providersLoading = false;
		}
	}

	async function loadProvisioned() {
		provisionedLoading = true;
		provisionedError = null;
		try {
			const response = await getProvisionedCompute();
			if (response.success && response.data) {
				provisioned = response.data.items;
			} else {
				provisionedError = response.message || 'Failed to load provisioned compute';
			}
		} catch (e: unknown) {
			provisionedError = getApiErrorMessage(e, 'Failed to load provisioned compute');
		} finally {
			provisionedLoading = false;
		}
	}

	async function loadGpuTypes(providerId: string) {
		selectedGpuTypeId = '';
		gpuTypes = [];
		if (!providerId) return;
		gpuTypesLoading = true;
		try {
			const response = await getComputeGpuTypes(providerId);
			if (response.success && response.data) {
				gpuTypes = response.data.gpu_types;
			} else {
				toasts.error(response.message || 'Failed to load GPU types');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to load GPU types'));
		} finally {
			gpuTypesLoading = false;
		}
	}

	function onProviderChange(providerId: string) {
		selectedProviderId = providerId;
		void loadGpuTypes(providerId);
	}

	async function provision() {
		if (!canProvision) return;
		provisioning = true;
		provisionError = null;
		try {
			const response = await provisionCompute({
				provider_id: selectedProviderId,
				profile_name: profileName.trim(),
				gpu_type_id: selectedGpuTypeId,
				volume_size_gb: volumeSizeGb === '' ? undefined : Number(volumeSizeGb)
			});
			if (response.success && response.data) {
				provisioned = [response.data, ...provisioned];
				toasts.success(`Provisioning "${response.data.profile_name}" started`);
				profileName = '';
				volumeSizeGb = '';
				selectedGpuTypeId = '';
			} else {
				provisionError = response.message || 'Failed to provision compute';
			}
		} catch (e: unknown) {
			provisionError = getApiErrorMessage(e, 'Failed to provision compute');
		} finally {
			provisioning = false;
		}
	}

	function handleStatusUpdate(updated: ProvisionedCompute) {
		provisioned = provisioned.map((row) => (row.id === updated.id ? updated : row));
	}

	function requestStop(row: ProvisionedCompute) {
		pendingAction = { kind: 'stop', row };
	}

	function requestTerminate(row: ProvisionedCompute) {
		pendingAction = { kind: 'terminate', row };
	}

	function cancelPendingAction() {
		pendingAction = null;
	}

	async function confirmPendingAction() {
		if (!pendingAction) return;
		const { kind, row } = pendingAction;
		actingRowId = row.id;
		try {
			if (kind === 'stop') {
				const response = await stopProvisionedCompute(row.id);
				if (response.success && response.data) {
					handleStatusUpdate(response.data);
					toasts.success(`"${row.profile_name}" stopped`);
				} else {
					toasts.error(response.message || 'Failed to stop compute');
				}
			} else {
				const response = await terminateProvisionedCompute(row.id);
				if (response.success) {
					provisioned = provisioned.filter((r) => r.id !== row.id);
					toasts.success(`"${row.profile_name}" terminated`);
				} else {
					toasts.error(response.message || 'Failed to terminate compute');
				}
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, kind === 'stop' ? 'Failed to stop compute' : 'Failed to terminate compute'));
		} finally {
			actingRowId = null;
			pendingAction = null;
		}
	}
</script>

<div class="p-4 sm:p-6 space-y-6">
	{#if providersLoading}
		<div class="flex items-center justify-center py-12">
			<Spinner size="lg" />
		</div>
	{:else if providersError}
		<EmptyState title="Error loading compute providers" description={providersError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadProviders}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if providers.length === 0}
		<EmptyState
			icon="cpu"
			title="No compute provider enabled"
			description="Remote compute is provisioned through a plugin that registers a compute provider (e.g. a GPU rental service). Enable one under Admin → Plugins to provision compute here."
			compact
		/>
	{:else}
		<section class="max-w-2xl space-y-4">
			<h3 class="text-sm font-semibold text-fg flex items-center gap-2">
				<Icon name="zap" className="w-4 h-4 text-fg-muted" />
				Provision compute
			</h3>

			{#if provisionError}
				<Alert variant="danger" density="compact">{provisionError}</Alert>
			{/if}

			<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
				<label class="block space-y-1.5">
					<span class="text-xs font-mono uppercase tracking-[0.07em] text-fg-subtle">Provider</span>
					<select
						class="input"
						value={selectedProviderId}
						onchange={(e) => onProviderChange((e.target as HTMLSelectElement).value)}
					>
						{#each providers as provider (provider.provider_id)}
							<option value={provider.provider_id}>{provider.label}</option>
						{/each}
					</select>
				</label>

				<label class="block space-y-1.5">
					<span class="text-xs font-mono uppercase tracking-[0.07em] text-fg-subtle">Name</span>
					<Input bind:value={profileName} placeholder="e.g. SDXL worker" />
				</label>
			</div>

			<div class="space-y-1.5">
				<span class="text-xs font-mono uppercase tracking-[0.07em] text-fg-subtle">GPU type</span>
				<div class="relative">
					<Icon name="search" className="w-4 h-4 text-fg-subtle absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
					<Input bind:value={gpuSearch} type="search" class="pl-9" placeholder="Search GPU types…" aria-label="Search GPU types" />
				</div>
				<div class="rounded-lg border border-line bg-surface-1 max-h-56 overflow-y-auto" role="listbox" aria-label="GPU types">
					{#if gpuTypesLoading}
						<div class="flex items-center justify-center py-8">
							<Spinner size="md" />
						</div>
					{:else if filteredGpuTypes.length === 0}
						<p class="text-sm text-fg-muted text-center py-8">No GPU types match your search.</p>
					{:else}
						{#each filteredGpuTypes as gpu (gpu.id)}
							<button
								type="button"
								role="option"
								aria-selected={selectedGpuTypeId === gpu.id}
								class="w-full flex items-center justify-between gap-3 px-3 py-2 text-sm text-left transition-colors {selectedGpuTypeId === gpu.id ? 'bg-signal/10 text-signal' : 'text-fg hover:bg-surface-2'}"
								onclick={() => (selectedGpuTypeId = gpu.id)}
							>
								<span class="truncate">{gpu.id}</span>
								<span class="font-mono tabular-nums text-xs text-fg-subtle flex-shrink-0">
									{gpu.memory_gb !== null ? `${gpu.memory_gb} GB` : '—'}
								</span>
							</button>
						{/each}
					{/if}
				</div>
			</div>

			<label class="block space-y-1.5 max-w-xs">
				<span class="text-xs font-mono uppercase tracking-[0.07em] text-fg-subtle">Volume size (GB)</span>
				<Input
					type="number"
					min="0"
					class="font-mono tabular-nums"
					bind:value={volumeSizeGb}
					placeholder="Provider default"
				/>
			</label>

			<div class="flex justify-end">
				<Button variant="primary" icon="zap" loading={provisioning} disabled={!canProvision} onclick={provision}>
					{provisioning ? 'Provisioning…' : 'Provision'}
				</Button>
			</div>
		</section>

		<section class="space-y-4">
			<h3 class="text-sm font-semibold text-fg flex items-center gap-2">
				<Icon name="database" className="w-4 h-4 text-fg-muted" />
				Provisioned compute
				<span class="font-mono tabular-nums text-xs text-fg-subtle">{provisioned.length}</span>
			</h3>

			{#if provisionedLoading}
				<div class="flex items-center justify-center py-12">
					<Spinner size="lg" />
				</div>
			{:else if provisionedError}
				<EmptyState title="Error loading provisioned compute" description={provisionedError} icon="warning" compact>
					{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={loadProvisioned}>Try again</Button>{/snippet}
				</EmptyState>
			{:else if provisioned.length === 0}
				<EmptyState
					icon="database"
					title="No compute provisioned yet"
					description="Provision a pod above to rent a GPU and register it as a backend."
					compact
				/>
			{:else}
				<div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
					{#each provisioned as row (row.id)}
						<RemoteComputeCard
							{row}
							{backends}
							busy={actingRowId === row.id}
							{onOpenBackend}
							onStatusUpdate={handleStatusUpdate}
							onStop={requestStop}
							onTerminate={requestTerminate}
						/>
					{/each}
				</div>
			{/if}
		</section>
	{/if}
</div>

<ConfirmModal
	isOpen={pendingAction !== null}
	title={pendingAction?.kind === 'terminate' ? 'Terminate compute' : 'Stop compute'}
	message={pendingAction?.kind === 'terminate'
		? `Terminate "${pendingAction.row.profile_name}"? This tears down the pod and removes its linked backend row. This cannot be undone.`
		: pendingAction
			? `Stop "${pendingAction.row.profile_name}"? Its linked backend is disabled until it's re-provisioned. Models on its volume persist.`
			: ''}
	variant={pendingAction?.kind === 'terminate' ? 'danger' : 'warning'}
	busy={actingRowId !== null}
	on:confirm={confirmPendingAction}
	on:cancel={cancelPendingAction}
/>
