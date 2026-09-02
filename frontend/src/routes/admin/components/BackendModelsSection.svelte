<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Alert, Badge, Button, Input, Spinner, EmptyState } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
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
	import {
		findTransferForFilename,
		hasRunningTransfer,
		transferMatchesFilename,
		transferProgressPercent,
		filterSyncRows,
		countByStatus,
		sumSizeBytes,
		isDefaultSyncFilters,
		capSyncRows,
		distinctModelTypes,
		DEFAULT_STATUS_FILTER,
		MAX_RENDERED_SYNC_ROWS
	} from './backendModelsSync';

	/**
	 * Shown on a `native.remote` backend's Models tab - lets an admin sync
	 * host model files onto the worker's depot (push) or have the worker pull
	 * them itself through a linked provider (fetch). `GET .../{backendId}`
	 * distinguishes four outcomes: `invalid_backend` (not a native.remote
	 * backend, or missing) -> render nothing, the caller shouldn't have
	 * mounted this; `worker_not_running` (a gateway answered for a
	 * stopped/still-starting worker) -> an empty state with Retry and,
	 * optionally, a jump to the Infrastructure tab; `worker_unreachable` (a
	 * genuine connect failure or a real protocol error) -> a danger alert with
	 * Retry; success -> the row list. The caller re-mounts this with
	 * `{#key backendId}` when the selected backend changes.
	 *
	 * The list fills the tab: a toolbar (search, type, status, select-all)
	 * over a scrolling row list with a pinned footer for the push/fetch
	 * actions.
	 */
	let { backendId, onOpenInfrastructure }: { backendId: string; onOpenInfrastructure?: () => void } = $props();

	const POLL_INTERVAL_MS = 2000;
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	let loading = $state(true);
	let rows = $state<RemoteModelSyncRow[]>([]);
	let sectionError = $state<{ kind: 'invalid_backend' | 'worker_not_running' | 'worker_unreachable'; message: string } | null>(
		null
	);
	let selected = $state<Set<string>>(new Set());
	let transfers = $state<WorkerModelTransfer[]>([]);
	let rowErrors = $state<Record<string, string>>({});
	let syncing = $state<'push' | 'fetch' | null>(null);
	let wasRunning = false;

	let searchQuery = $state('');
	let typeFilter = $state('all');
	let statusFilter = $state<RemoteModelSyncStatus | 'all'>(DEFAULT_STATUS_FILTER);

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
		{ id: 'on_worker', label: 'On worker' },
		{ id: 'missing', label: 'Missing' },
		{ id: 'digest_mismatch', label: 'Mismatch' },
		{ id: 'all', label: 'All' }
	];

	let modelTypes = $derived(distinctModelTypes(rows));
	// Status excluded here so the pill counts reflect search + type only.
	let rowsByTypeAndSearch = $derived(filterSyncRows(rows, { search: searchQuery, modelType: typeFilter, status: 'all' }));
	let statusCounts = $derived(countByStatus(rowsByTypeAndSearch));
	let filteredRows = $derived(
		statusFilter === 'all' ? rowsByTypeAndSearch : rowsByTypeAndSearch.filter((r) => r.status === statusFilter)
	);
	let renderedRows = $derived(capSyncRows(filteredRows));
	let isDefaultFilters = $derived(
		isDefaultSyncFilters({ search: searchQuery, modelType: typeFilter, status: statusFilter })
	);

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
		statusFilter = DEFAULT_STATUS_FILTER;
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
			} else if (response.error === 'worker_not_running') {
				sectionError = { kind: 'worker_not_running', message: response.message ?? '' };
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
			} else if (response.error === 'worker_not_running') {
				sectionError = { kind: 'worker_not_running', message: response.message ?? '' };
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

{#if loading}
	<div class="h-full flex items-center justify-center">
		<Spinner size="md" />
	</div>
{:else if sectionError?.kind === 'worker_not_running'}
	<EmptyState
		icon="pause"
		title="Worker isn't running"
		description="It's stopped or still starting. Models will show up here once it's back."
	>
		{#snippet actions()}
			<Button variant="secondary" size="sm" onclick={retry}>Retry</Button>
			{#if onOpenInfrastructure}
				<Button variant="secondary" size="sm" onclick={onOpenInfrastructure}>Open Infrastructure</Button>
			{/if}
		{/snippet}
	</EmptyState>
{:else if sectionError?.kind === 'worker_unreachable'}
	<Alert variant="danger" density="compact">
		<p>Couldn't reach the worker.</p>
		<p class="font-mono text-2xs mt-1">{sectionError.message}</p>
		<div class="mt-2">
			<Button variant="secondary" size="sm" onclick={retry}>Retry</Button>
		</div>
	</Alert>
{:else if sectionError?.kind !== 'invalid_backend'}
	<div class="h-full flex flex-col min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		{#if rows.length === 0}
			<p class="px-4 py-6 text-sm text-fg-muted">No models are known on the host yet.</p>
		{:else}
			<div class="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-line flex-shrink-0">
				<div class="relative w-56">
					<Icon name="search" className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
					<Input
						type="search"
						bind:value={searchQuery}
						placeholder="Search by filename…"
						class="pl-8 {searchQuery ? 'pr-7' : ''}"
						aria-label="Search by filename"
					/>
					{#if searchQuery}
						<button
							type="button"
							class="absolute right-2 top-1/2 -translate-y-1/2 text-fg-subtle hover:text-fg"
							aria-label="Clear search"
							onclick={() => (searchQuery = '')}
						>
							<Icon name="close" className="w-3.5 h-3.5" />
						</button>
					{/if}
				</div>

				{#if modelTypes.length > 1}
					<select bind:value={typeFilter} class="input w-40" aria-label="Filter by model type">
						<option value="all">All types</option>
						{#each modelTypes as type}
							<option value={type}>{type}</option>
						{/each}
					</select>
				{/if}

				<div class="flex flex-wrap items-center gap-0.5 rounded p-0.5 bg-surface-2/50">
					{#each statusPills as p}
						<button
							type="button"
							class="px-2.5 py-1 rounded text-xs font-medium transition-colors flex items-center gap-1.5 {statusFilter === p.id
								? 'bg-signal/10 text-signal'
								: 'text-fg-muted hover:bg-surface-3/50'}"
							onclick={() => (statusFilter = p.id)}
						>
							{p.label}
							<span class="font-mono text-2xs tabular-nums">{p.id === 'all' ? rowsByTypeAndSearch.length : statusCounts[p.id]}</span>
						</button>
					{/each}
				</div>

				<div class="flex items-center gap-2 ml-auto">
					<input
						type="checkbox"
						class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal flex-shrink-0"
						checked={allFilteredSelected}
						use:indeterminateAction={!allFilteredSelected && someFilteredSelected}
						onchange={toggleSelectAllFiltered}
						aria-label="Select all filtered models"
						disabled={filteredRows.length === 0}
					/>
					<span class="font-mono text-2xs tabular-nums text-fg-subtle whitespace-nowrap">
						{selected.size} selected · {filteredRows.length} / {rows.length} matched
					</span>
					{#if !isDefaultFilters}
						<Button variant="ghost" size="xs" icon="close" onclick={clearFilters}>Clear filters</Button>
					{/if}
				</div>
			</div>

			<div class="flex-1 min-h-0 overflow-y-auto">
				{#if filteredRows.length === 0}
					<EmptyState compact icon="search" title="No models match" description="Try a different search or filter.">
						{#snippet actions()}
							<Button variant="secondary" size="sm" onclick={clearFilters}>Clear filters</Button>
						{/snippet}
					</EmptyState>
				{:else}
					<div class="divide-y divide-line" role="listbox" aria-multiselectable="true" aria-label="Models">
						{#each renderedRows.rows as row (row.model_id)}
							{@const isSelected = selected.has(row.model_id)}
							{@const activeTransfer = activeTransferFor(row.filename)}
							{@const error = !activeTransfer ? rowErrors[row.model_id] : undefined}
							<div
								class="flex items-center gap-3 px-3 py-2 cursor-pointer transition-colors {isSelected
									? 'bg-signal/10'
									: 'hover:bg-surface-2/50'}"
								role="option"
								aria-selected={isSelected}
								tabindex="0"
								onclick={() => toggleSelected(row.model_id)}
								onkeydown={(e) => {
									if (e.key === 'Enter' || e.key === ' ') {
										e.preventDefault();
										toggleSelected(row.model_id);
									}
								}}
							>
								<input
									type="checkbox"
									class="h-4 w-4 rounded border-line-strong bg-surface-2 text-signal focus:ring-signal flex-shrink-0"
									checked={isSelected}
									onclick={(e) => e.stopPropagation()}
									onchange={() => toggleSelected(row.model_id)}
									aria-label="Select {row.filename}"
								/>
								<div class="min-w-0 flex-1">
									<div class="flex items-center gap-2 min-w-0">
										<span class="truncate font-mono text-xs text-fg" title={row.filename}>{row.filename}</span>
										<Badge variant="neutral" size="sm" class="flex-shrink-0">{row.model_type}</Badge>
										{#if !row.providers_can_fetch}
											<span
												title="No provider link or hash for this model - fetch via provider is unavailable"
												class="flex-shrink-0"
											>
												<Icon name="information-circle" className="w-3.5 h-3.5 text-fg-subtle" />
											</span>
										{/if}
									</div>
									{#if activeTransfer}
										{@const percent = transferProgressPercent(activeTransfer)}
										<div class="mt-1 flex items-center gap-2">
											<div class="h-1 flex-1 rounded-full bg-surface-3 overflow-hidden">
												<div class="h-full bg-signal rounded-full transition-[width]" style="width: {percent}%"></div>
											</div>
											<span class="font-mono text-2xs tabular-nums text-fg-subtle flex-shrink-0">{percent}%</span>
										</div>
									{:else if error}
										<p class="text-2xs text-danger truncate mt-0.5" title={error}>{error}</p>
									{/if}
								</div>
								<span class="font-mono text-2xs tabular-nums text-fg-subtle flex-shrink-0 w-16 text-right">
									{formatBytes(row.size_bytes ?? 0)}
								</span>
								<Badge variant={statusVariant[row.status]} size="sm" class="flex-shrink-0">{statusLabel[row.status]}</Badge>
							</div>
						{/each}
						{#if renderedRows.truncated}
							<p class="px-3 py-2 font-mono text-2xs text-fg-subtle">
								showing first {MAX_RENDERED_SYNC_ROWS} — narrow the search
							</p>
						{/if}
					</div>
				{/if}
			</div>

			<div class="flex items-center gap-3 px-3 py-2 border-t border-line flex-shrink-0">
				{#if selectedRows.length > 0}
					<span class="font-mono text-2xs tabular-nums text-fg-subtle mr-auto">
						{selectedRows.length} selected · {formatBytes(selectedBytes)}
					</span>
				{:else}
					<span class="mr-auto"></span>
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
			</div>
		{/if}
	</div>
{/if}
