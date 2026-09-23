<script lang="ts">
	import Card from '$lib/components/ui/Card.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import * as adminApi from '$lib/services/admin-api';
	import type { AdminGenerationDetailResult, AdminGenerationListItem } from '$lib/services/admin-api';
	import type { User } from '$lib/stores/auth';
	import { debounce } from '$lib/stores/tabPersistence';
	import { timeAgo, parseServerDate } from '$lib/utils/relativeTime';
	import { formatDurationMs } from '$lib/components/generation-panel/barState';
	import { Badge, EmptyState, Spinner } from '$lib/components/ui';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import { Pane, PaneRow, PanePager } from '$lib/components/pane';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryFilterChipRow from '$lib/components/library/LibraryFilterChipRow.svelte';
	import AdminTabShell from './AdminTabShell.svelte';
	import GenerationRunReport from './GenerationRunReport.svelte';
	import GenerationsFiltersPopover from './GenerationsFiltersPopover.svelte';
	import {
		GENERATION_SORT_OPTIONS,
		clearAllGenerationsFilters,
		clearGenerationsFilterChip,
		generationsFilterActiveCount,
		generationsFilterChips,
		generationsFiltersFromSearchParams,
		generationsFiltersToSearchParams,
		generationsSortParams,
		type GenerationSortBy,
		type GenerationsFilters
	} from './generationsFilters';

	const PAGE_SIZE = 20;
	const FILTER_PARAM_KEYS = ['q', 'status', 'user', 'from', 'to', 'sort_by'];

	const STATUS_VARIANT: Record<string, 'neutral' | 'success' | 'warning' | 'danger' | 'info'> = {
		completed: 'success',
		running: 'info',
		pending: 'neutral',
		failed: 'danger',
		cancelled: 'warning'
	};

	let generations: AdminGenerationListItem[] = [];
	let total = 0;
	let offset = 0;
	let listLoading = true;
	let listError: string | null = null;
	let lastFiltersKey = '';

	let users: User[] = [];
	$: usersById = new Map(users.map((u) => [u.id, u]));

	let selectedGenerationId: string | null = null;
	let detail: AdminGenerationDetailResult | null = null;
	let detailLoading = false;
	let detailError: string | null = null;

	$: filters = generationsFiltersFromSearchParams($page.url.searchParams);
	$: filtersKey = JSON.stringify(filters);
	$: activeFilterCount = generationsFilterActiveCount(filters);
	$: chips = generationsFilterChips(filters, (userId) => usersById.get(userId)?.username ?? userId);

	$: {
		if (filtersKey !== lastFiltersKey) {
			lastFiltersKey = filtersKey;
			offset = 0;
			loadGenerations();
		}
	}

	onMount(async () => {
		const usersResponse = await adminApi.getUsers();
		if (usersResponse.success && usersResponse.data) users = usersResponse.data;
	});

	async function loadGenerations() {
		listLoading = true;
		listError = null;
		try {
			const sort = generationsSortParams(filters.sortBy);
			const response = await adminApi.getAdminGenerations({
				limit: PAGE_SIZE,
				offset,
				status: filters.status || undefined,
				userId: filters.userId || undefined,
				search: filters.q || undefined,
				createdFrom: filters.createdFrom || undefined,
				createdTo: filters.createdTo || undefined,
				sortBy: sort.sortBy,
				sortDir: sort.sortDir
			});
			if (response.success && response.data) {
				generations = response.data.generations;
				total = response.data.total;
			} else {
				listError = response.message || 'Failed to load generations';
			}
		} catch (e: any) {
			listError = e.response?.data?.message || e.message || 'Failed to load generations';
		} finally {
			listLoading = false;
		}
	}

	function applyFilters(next: GenerationsFilters) {
		const url = new URL($page.url);
		for (const key of FILTER_PARAM_KEYS) url.searchParams.delete(key);
		for (const [key, value] of generationsFiltersToSearchParams(next)) url.searchParams.set(key, value);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const debouncedApplyFilters = debounce(applyFilters, 300);

	function onQueryChange(value: string) {
		debouncedApplyFilters({ ...filters, q: value });
	}

	function onSortChange(value: string) {
		applyFilters({ ...filters, sortBy: value as GenerationSortBy });
	}

	function onPopoverChange(next: GenerationsFilters) {
		applyFilters(next);
	}

	function onRemoveChip(key: string) {
		applyFilters(clearGenerationsFilterChip(filters, key));
	}

	function onClearAllFilters() {
		applyFilters(clearAllGenerationsFilters(filters));
	}

	function nextPage() {
		if (offset + PAGE_SIZE >= total) return;
		offset += PAGE_SIZE;
		loadGenerations();
	}

	function prevPage() {
		if (offset === 0) return;
		offset = Math.max(0, offset - PAGE_SIZE);
		loadGenerations();
	}

	async function selectGeneration(generationId: string) {
		selectedGenerationId = generationId;
		detail = null;
		detailError = null;
		detailLoading = true;
		try {
			const response = await adminApi.getAdminGenerationDetail(generationId);
			if (response.success && response.data) {
				detail = response.data;
			} else {
				detailError = response.message || 'Failed to load generation detail';
			}
		} catch (e: any) {
			detailError = e.response?.data?.message || e.message || 'Failed to load generation detail';
		} finally {
			detailLoading = false;
		}
	}

	function usernameFor(userId: string): string {
		return usersById.get(userId)?.username ?? userId;
	}

	function durationFor(row: AdminGenerationListItem): string {
		if (!row.completed_at) return row.status === 'running' ? 'running' : '-';
		const completed = parseServerDate(row.completed_at)?.getTime();
		const created = parseServerDate(row.created_at)?.getTime();
		const ms = completed != null && created != null ? completed - created : NaN;
		return Number.isFinite(ms) && ms >= 0 ? formatDurationMs(ms) : '-';
	}
</script>

<div class="flex min-h-[calc(100dvh-var(--header-h)-2rem)] flex-col gap-4 sm:min-h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell
		title="Generations"
		icon="generation"
		counts={[{ label: total === 1 ? 'generation' : 'generations', value: total }]}
	/>

	<Card padding="none" class="flex flex-wrap items-center gap-2 px-4 py-2.5">
		<LibraryFilterBar
			q={filters.q}
			{onQueryChange}
			searchPlaceholder="Search prompt, preset…"
			sortBy={filters.sortBy}
			sortOptions={GENERATION_SORT_OPTIONS}
			{onSortChange}
			filterCount={activeFilterCount}
		>
			{#snippet popover(close: () => void)}
				<GenerationsFiltersPopover {filters} {users} onChange={onPopoverChange} onClose={close} />
			{/snippet}
		</LibraryFilterBar>
	</Card>

	<LibraryFilterChipRow {chips} {onRemoveChip} onClearAll={onClearAllFilters} loadedCount={generations.length} {total} />

	<section class="flex flex-1 flex-col rounded-lg border border-line bg-surface-1 overflow-hidden">
		<MasterDetailLayout leftWidth={360} minWidth={300} maxWidth={480} storageKey="admin-generations-width">
			<div slot="list" class="h-full min-h-0">
				<Pane
					label="Generations"
					count={total}
					loading={listLoading}
					isEmpty={!listLoading && (Boolean(listError) || generations.length === 0)}
					bodyRole="listbox"
					ariaLabel="Generations"
				>
					{#snippet empty()}
						<div class="p-4 h-full flex items-center justify-center">
							{#if listError}
								<EmptyState title="Could not load generations" description={listError} icon="warning" compact />
							{:else}
								<EmptyState
									icon="generation"
									title={activeFilterCount > 0 ? 'No generations match your filters' : 'No generations yet'}
									description={activeFilterCount > 0
										? 'Try a different status, user, or date range.'
										: 'Generations show up here once a user runs one.'}
									compact
								/>
							{/if}
						</div>
					{/snippet}

					{#snippet children()}
						{#each generations as row (row.id)}
							{#snippet rowBody()}
								<div class="flex items-center justify-between gap-2 mb-1">
									<span class="text-sm font-medium truncate text-fg">{row.preset_name || row.mode || 'Untitled generation'}</span>
									<Badge variant={STATUS_VARIANT[row.status] ?? 'neutral'} size="sm" dot class="uppercase flex-shrink-0">
										{row.status}
									</Badge>
								</div>
								<div class="flex items-center justify-between gap-2 text-xs text-fg-subtle">
									<span class="truncate">{usernameFor(row.user_id)}</span>
									<span class="font-mono tabular-nums flex-shrink-0">{timeAgo(row.created_at)}</span>
								</div>
								<div class="flex items-center justify-between gap-2 text-xs font-mono tabular-nums text-fg-subtle mt-0.5">
									<span>{durationFor(row)} · {row.files?.length ?? 0} file{(row.files?.length ?? 0) === 1 ? '' : 's'}</span>
									{#if !row.has_run_report}
										<Tooltip text="No run report recorded for this generation"><span class="text-fg-disabled normal-case">no report</span></Tooltip>
									{/if}
								</div>
							{/snippet}
							<PaneRow
								selected={selectedGenerationId === row.id}
								onclick={() => selectGeneration(row.id)}
								children={rowBody}
							/>
						{/each}
					{/snippet}

					{#snippet footer()}
						<PanePager {offset} limit={PAGE_SIZE} {total} onPrev={prevPage} onNext={nextPage} />
					{/snippet}
				</Pane>
			</div>

			<div slot="detail" class="h-full min-h-0 flex flex-col">
				{#if !selectedGenerationId}
					<div class="flex-1 p-5 flex items-center justify-center bg-surface-2">
						<EmptyState
							icon="generation"
							title="Select a generation"
							description="Choose a generation from the list to inspect its run report."
							compact
						/>
					</div>
				{:else if detailLoading}
					<div class="flex-1 flex items-center justify-center bg-surface-2">
						<Spinner size="lg" />
					</div>
				{:else if detailError}
					<div class="flex-1 p-5 flex items-center justify-center bg-surface-2">
						<EmptyState title="Could not load generation" description={detailError} icon="warning" compact />
					</div>
				{:else if detail}
					<GenerationRunReport
						generation={detail.generation}
						report={detail.run_report}
						username={usernameFor(detail.generation.user_id)}
					/>
				{/if}
			</div>
		</MasterDetailLayout>
	</section>
</div>
