<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Alert, Badge, Button, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { DetailSection } from '$lib/components/detail';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { toasts } from '$lib/stores/toast';
	import { formatBytes } from '$lib/utils/format';
	import {
		getRemoteModelSyncView,
		pushRemoteModels,
		fetchRemoteModels,
		getRemoteModelTransfers
	} from '$lib/services/admin-api';
	import type { RemoteModelSyncRow, RemoteModelSyncStatus, WorkerModelTransfer } from '$lib/services/admin-api';
	import ModelAssignmentPicker from '$lib/components/modals/ModelAssignmentPicker.svelte';
	import {
		findTransferForFilename,
		hasRunningTransfer,
		transferMatchesFilename,
		transferProgressPercent,
		filterSyncRows,
		countByStatus,
		sumSizeBytes,
		toPickerModel,
		type RemoteModelPickerModel
	} from './backendModelsSync';

	/**
	 * Shown on a `native.remote` backend's detail pane, under Infrastructure -
	 * lets an admin sync host model files onto the worker's depot (push) or
	 * have the worker pull them itself through a linked provider (fetch).
	 * `GET .../{backendId}` distinguishes three outcomes: `invalid_backend`
	 * (not a native.remote backend, or missing) → render nothing, the caller
	 * shouldn't have mounted this; `worker_unreachable` → an error card with
	 * Retry; success → the row list. The caller re-mounts this with
	 * `{#key backendId}` when the selected backend changes.
	 *
	 * Search/type filtering/pagination/card grid are `ModelAssignmentPicker` -
	 * the same module the user-model and group-model assignment screens use -
	 * driven in `external`+`multi` mode. Only the status pills, byte total, and
	 * push/fetch actions below are sync-specific.
	 */
	let { backendId }: { backendId: string } = $props();

	const POLL_INTERVAL_MS = 2000;
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	let loading = $state(true);
	let rows = $state<RemoteModelSyncRow[]>([]);
	let sectionError = $state<{ kind: 'invalid_backend' | 'worker_unreachable'; message: string } | null>(null);
	let selected = $state<Set<string>>(new Set());
	let transfers = $state<WorkerModelTransfer[]>([]);
	let rowErrors = $state<Record<string, string>>({});
	let syncing = $state<'push' | 'fetch' | null>(null);
	let wasRunning = false;

	let searchQuery = $state('');
	let typeFilter = $state('all');
	let currentPage = $state(1);
	let statusFilter = $state<RemoteModelSyncStatus | 'all'>('missing');

	const statusVariant: Record<RemoteModelSyncStatus, 'success' | 'warning' | 'danger'> = {
		on_worker: 'success',
		missing: 'warning',
		digest_mismatch: 'danger'
	};

	const statusLabel: Record<RemoteModelSyncStatus, string> = {
		on_worker: 'On worker',
		missing: 'Missing',
		digest_mismatch: 'Digest mismatch'
	};

	const statusPills: { id: RemoteModelSyncStatus | 'all'; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'missing', label: 'Missing' },
		{ id: 'on_worker', label: 'On worker' },
		{ id: 'digest_mismatch', label: 'Mismatch' }
	];

	let pickerModels = $derived(rows.map(toPickerModel));
	let statusPredicate = $derived((m: RemoteModelPickerModel) => statusFilter === 'all' || m.status === statusFilter);
	// Status excluded here so the pill counts reflect search + type only.
	let rowsByTypeAndSearch = $derived(filterSyncRows(rows, { search: searchQuery, modelType: typeFilter, status: 'all' }));
	let statusCounts = $derived(countByStatus(rowsByTypeAndSearch));
	let filteredRows = $derived(
		statusFilter === 'all' ? rowsByTypeAndSearch : rowsByTypeAndSearch.filter((r) => r.status === statusFilter)
	);
	let activeFilterCount = $derived(
		(searchQuery.trim() ? 1 : 0) + (typeFilter !== 'all' ? 1 : 0) + (statusFilter !== 'missing' ? 1 : 0)
	);

	// The picker resets its own page on search/type changes - only the status
	// pills (sync-specific, live outside the picker) need this nudge.
	$effect(() => {
		statusFilter;
		currentPage = 1;
	});

	let selectedRows = $derived(rows.filter((r) => selected.has(r.model_id)));
	let canFetchSelected = $derived(selectedRows.length > 0 && selectedRows.every((r) => r.providers_can_fetch));
	let selectedInFlight = $derived(selectedRows.some((r) => !!activeTransferFor(r.filename)));
	let selectedBytes = $derived(sumSizeBytes(selectedRows));
	let allFilteredSelected = $derived(filteredRows.length > 0 && filteredRows.every((r) => selected.has(r.model_id)));
	let someFilteredSelected = $derived(filteredRows.some((r) => selected.has(r.model_id)));
	let fetchDisabledTitle = $derived(
		selectedRows.length > 0 && !canFetchSelected
			? 'One or more selected models have no provider link or hash for this model'
			: undefined
	);

	function activeTransferFor(filename: string): WorkerModelTransfer | undefined {
		const transfer = findTransferForFilename(transfers, filename);
		return transfer && transfer.state === 'running' ? transfer : undefined;
	}

	function toggleSelected(modelId: string) {
		const next = new Set(selected);
		if (next.has(modelId)) next.delete(modelId);
		else next.add(modelId);
		selected = next;
	}

	function toggleSelectAllFiltered() {
		const next = new Set(selected);
		for (const row of filteredRows) {
			if (allFilteredSelected) next.delete(row.model_id);
			else next.add(row.model_id);
		}
		selected = next;
	}

	function clearFilters() {
		searchQuery = '';
		typeFilter = 'all';
		statusFilter = 'missing';
	}

	// `indeterminate` is a DOM property, not an HTML attribute - set via action.
	function indeterminateAction(node: HTMLInputElement, isIndeterminate: boolean) {
		node.indeterminate = isIndeterminate;
		return {
			update(next: boolean) {
				node.indeterminate = next;
			}
		};
	}

	async function loadSyncView() {
		loading = true;
		sectionError = null;
		try {
			const response = await getRemoteModelSyncView(backendId);
			if (response.success && response.data) {
				rows = response.data.models;
				selected = new Set([...selected].filter((id) => rows.some((r) => r.model_id === id)));
			} else if (response.error === 'invalid_backend') {
				sectionError = { kind: 'invalid_backend', message: response.message ?? '' };
			} else if (response.error === 'worker_unreachable') {
				sectionError = { kind: 'worker_unreachable', message: response.message || 'Worker unreachable' };
			} else {
				toasts.error(response.message || 'Failed to load models');
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, 'Failed to load models'));
		} finally {
			loading = false;
		}
	}

	function captureRowErrors() {
		const next = { ...rowErrors };
		for (const transfer of transfers) {
			if (transfer.state !== 'failed' || !transfer.error) continue;
			const row = rows.find((r) => transferMatchesFilename(transfer, r.filename));
			if (row) next[row.model_id] = transfer.error;
		}
		rowErrors = next;
	}

	async function pollTransfers() {
		try {
			const response = await getRemoteModelTransfers(backendId);
			if (!response.success || !response.data) return;
			transfers = response.data.transfers;
			captureRowErrors();
			const running = hasRunningTransfer(transfers);
			if (!running) {
				stopPolling();
				if (wasRunning) await loadSyncView();
			}
			wasRunning = running;
		} catch {
			// Transient poll failure - the next tick retries, nothing to surface.
		}
	}

	function startPolling() {
		stopPolling();
		pollHandle = setInterval(pollTransfers, POLL_INTERVAL_MS);
	}

	function stopPolling() {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	}

	onMount(async () => {
		await loadSyncView();
		if (!sectionError) {
			await pollTransfers();
			if (hasRunningTransfer(transfers)) startPolling();
		}
	});

	onDestroy(stopPolling);

	async function submitSync(kind: 'push' | 'fetch') {
		if (selected.size === 0 || syncing) return;
		syncing = kind;
		const modelIds = [...selected];
		try {
			const response = kind === 'push' ? await pushRemoteModels(backendId, modelIds) : await fetchRemoteModels(backendId, modelIds);
			if (response.success && response.data) {
				const next = { ...rowErrors };
				for (const result of response.data.transfers) {
					if (result.error) next[result.model_id] = result.error;
					else delete next[result.model_id];
				}
				rowErrors = next;
				selected = new Set();
				await pollTransfers();
				if (hasRunningTransfer(transfers)) startPolling();
			} else if (response.error === 'worker_unreachable') {
				sectionError = { kind: 'worker_unreachable', message: response.message || 'Worker unreachable' };
			} else {
				toasts.error(response.message || `Failed to ${kind} models`);
			}
		} catch (e: unknown) {
			toasts.error(getApiErrorMessage(e, `Failed to ${kind} models`));
		} finally {
			syncing = null;
		}
	}

	function retry() {
		void loadSyncView().then(() => {
			if (!sectionError) {
				pollTransfers().then(() => {
					if (hasRunningTransfer(transfers)) startPolling();
				});
			}
		});
	}
</script>

{#snippet modelsFooter()}
	{#if selectedRows.length > 0}
		<span class="font-mono text-2xs tabular-nums text-fg-subtle mr-auto">{selectedRows.length} selected</span>
	{/if}
	<Button
		variant="secondary"
		size="sm"
		icon="upload"
		loading={syncing === 'push'}
		disabled={selected.size === 0 || syncing !== null || selectedInFlight}
		onclick={() => submitSync('push')}
	>
		Upload from this machine
	</Button>
	{#if selectedRows.length > 0}
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">{formatBytes(selectedBytes)}</span>
	{/if}
	<Button
		variant="secondary"
		size="sm"
		icon="download"
		loading={syncing === 'fetch'}
		disabled={!canFetchSelected || syncing !== null || selectedInFlight}
		title={fetchDisabledTitle}
		onclick={() => submitSync('fetch')}
	>
		Fetch via provider
	</Button>
{/snippet}

{#if loading}
	<DetailSection label="Models">
		<div class="flex items-center justify-center py-2">
			<Spinner size="md" />
		</div>
	</DetailSection>
{:else if sectionError?.kind === 'worker_unreachable'}
	<DetailSection label="Models">
		<Alert variant="danger" density="compact">
			{sectionError.message}
			<div class="mt-2">
				<Button variant="secondary" size="sm" onclick={retry}>Retry</Button>
			</div>
		</Alert>
	</DetailSection>
{:else if sectionError?.kind !== 'invalid_backend'}
	<DetailSection label="Models" padded={false} footer={rows.length > 0 ? modelsFooter : undefined}>
		{#snippet headerExtra()}
			<span class="font-mono text-2xs tabular-nums text-fg-subtle">{rows.length}</span>
		{/snippet}

		{#if rows.length === 0}
			<p class="px-4 sm:px-5 py-4 text-sm text-fg-muted">No models are known on the host yet.</p>
		{:else}
			<div class="px-4 sm:px-5 py-2 border-b border-line flex items-center gap-3">
				<input
					type="checkbox"
					class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal flex-shrink-0"
					checked={allFilteredSelected}
					use:indeterminateAction={!allFilteredSelected && someFilteredSelected}
					onchange={toggleSelectAllFiltered}
					aria-label="Select all filtered models"
					disabled={filteredRows.length === 0}
				/>
				<span class="font-mono text-2xs tabular-nums text-fg-subtle">
					{selected.size} selected · {filteredRows.length} / {rows.length} matched
				</span>
				{#if activeFilterCount > 0}
					<Button variant="ghost" size="xs" icon="close" class="ml-auto" onclick={clearFilters}>Clear filters</Button>
				{/if}
			</div>

			<ModelAssignmentPicker
				dataSource="external"
				externalModels={pickerModels}
				externalFilter={statusPredicate}
				selectionMode="multi"
				selectedIds={[...selected]}
				onToggle={toggleSelected}
				bind:searchQuery
				bind:selectedType={typeFilter}
				bind:currentPage
				pageSize={40}
			>
				<svelte:fragment slot="extraFilters">
					<div class="flex flex-wrap items-center gap-0.5 rounded p-0.5 bg-surface-2/50">
						{#each statusPills as p}
							<button
								type="button"
								class="px-3 py-1 rounded text-xs font-medium transition-colors flex items-center gap-1.5 {statusFilter === p.id
									? 'bg-signal/10 text-signal'
									: 'text-fg-muted hover:bg-surface-3/50'}"
								onclick={() => (statusFilter = p.id)}
							>
								{p.label}
								<span class="font-mono text-2xs tabular-nums">{p.id === 'all' ? rowsByTypeAndSearch.length : statusCounts[p.id]}</span>
							</button>
						{/each}
					</div>
				</svelte:fragment>

				<svelte:fragment slot="cardExtra" let:model>
					{@const m = model as RemoteModelPickerModel}
					{@const activeTransfer = activeTransferFor(m.filename)}
					<div class="flex items-center gap-1.5 flex-wrap pt-0.5">
						<Badge variant={statusVariant[m.status]} size="sm">{statusLabel[m.status]}</Badge>
						{#if !m.providers_can_fetch}
							<span title="No provider link or hash for this model - fetch via provider is unavailable" class="flex-shrink-0">
								<Icon name="information-circle" className="w-3.5 h-3.5 text-fg-subtle" />
							</span>
						{/if}
					</div>
					{#if activeTransfer}
						{@const percent = transferProgressPercent(activeTransfer)}
						<div class="mt-1 h-1 rounded-full bg-surface-3 overflow-hidden">
							<div class="h-full bg-signal rounded-full transition-[width]" style="width: {percent}%"></div>
						</div>
					{:else if rowErrors[m.id]}
						<p class="text-2xs text-danger truncate" title={rowErrors[m.id]}>{rowErrors[m.id]}</p>
					{/if}
				</svelte:fragment>
			</ModelAssignmentPicker>
		{/if}
	</DetailSection>
{/if}
