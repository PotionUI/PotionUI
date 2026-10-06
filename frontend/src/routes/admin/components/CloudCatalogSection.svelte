<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { Alert, Badge, Button, EmptyState, LoadErrorState, Switch } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import LibraryFilterChipRow from '$lib/components/library/LibraryFilterChipRow.svelte';
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SelectFilterField from '$lib/components/library/SelectFilterField.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import { DataTable, TablePager, pageCount, clearAll, type DataTableColumn } from '$lib/components/table';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { cloudModelsKind } from '$lib/components/picker/kinds';
	import { pickFacts } from '$lib/components/picker';
	import type { PresetInfo } from '$lib/types/api';
	import { parseServerDate, timeAgo } from '$lib/utils/relativeTime';
	import {
		getCloudCatalog,
		refreshCloudCatalog,
		setCloudCatalogEnabled
	} from '$lib/services/admin-api';
	import type {
		CloudCatalogItem,
		CloudCatalogPage,
		CloudCatalogRefreshResult
	} from '$lib/services/admin-api';
	import {
		CATALOG_FILTER_PARAMS,
		CATALOG_OUTPUT_OPTIONS,
		CATALOG_PAGE_SIZE,
		CATALOG_PAGE_SIZE_OPTIONS,
		CATALOG_SHOW_OPTIONS,
		DEFAULT_CATALOG_FILTERS,
		CATALOG_TASK_OPTIONS,
		bulkTargets,
		canEnable,
		catalogFilterActiveCount,
		catalogFilterChips,
		catalogFiltersFromSearchParams,
		catalogFiltersToSearchParams,
		catalogHasFilters,
		catalogQuery,
		catalogSummaryLine,
		clearAllCatalogFilters,
		clearCatalogFilterChip,
		itemStatuses,
		priceLines,
		priceSummary,
		suggestedTip,
		modelPageHref,
		catalogPresetsHint,
		catalogShowOf,
		withCatalogShow,
		taskLabel,
		visibleTasks,
		type CatalogFilters,
		type CatalogShow
	} from './cloudCatalog';

	let { backendId }: { backendId: string } = $props();

	let data = $state<CloudCatalogPage | null>(null);
	let items = $state<CloudCatalogItem[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let pageIndex = $state(1);
	let pageSize = $state(CATALOG_PAGE_SIZE);
	let selected = $state<Set<string>>(new Set());
	let rowErrors = $state<Record<string, string>>({});
	let busySlugs = $state<Set<string>>(new Set());
	let refreshing = $state(false);
	let refreshResult = $state<CloudCatalogRefreshResult | null>(null);
	let refreshError = $state<string | null>(null);
	let skippedOpen = $state(false);
	let bulkBusy = $state<'enable' | 'disable' | null>(null);
	let bulkError = $state<string | null>(null);
	let presets = $state<PresetInfo[] | null>(null);
	let presetsRequested = false;
	let requestVersion = 0;
	let lastFiltersKey = '';
	let destroyed = false;
	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	const filters = $derived(catalogFiltersFromSearchParams($page.url.searchParams));
	const filtersKey = $derived(JSON.stringify(filters));
	const activeFilterCount = $derived(catalogFilterActiveCount(filters));
	const hasFilters = $derived(catalogHasFilters(filters));
	const chips = $derived(catalogFilterChips(filters));
	const total = $derived(data?.total ?? 0);
	const counts = $derived(data?.counts ?? { total: 0, enabled: 0, missing: 0 });
	const notice = $derived(data?.provider.data_notice?.trim() ?? '');
	const refreshedAt = $derived(data?.state?.refreshed_at ?? null);
	const catalogEmpty = $derived(!loading && !loadError && counts.total === 0 && !hasFilters);
	const enableTargets = $derived(bulkTargets(items, selected, true));
	const disableTargets = $derived(bulkTargets(items, selected, false));
	const presetsHint = $derived(catalogPresetsHint(presets, data?.driver, counts.enabled));

	$effect(() => {
		if (counts.enabled > 0 && !presetsRequested) {
			presetsRequested = true;
			void loadPresets();
		}
	});

	async function loadPresets() {
		try {
			const response = await api.listPresets(true);
			if (!destroyed && response.success) presets = response.data ?? [];
		} catch {
			presets = null;
		}
	}

	const columns: DataTableColumn<CloudCatalogItem>[] = [
		{ key: 'model', label: 'Model', width: 'minmax(180px,1.6fr)', cell: modelCell },
		{ key: 'tasks', label: 'Tasks', width: 'minmax(120px,1.1fr)', cell: tasksCell },
		{ key: 'price', label: 'Price', width: 'minmax(110px,0.8fr)', mono: true, cell: priceCell },
		{ key: 'status', label: 'Status', width: 'minmax(110px,0.8fr)', cell: statusCell },
		{ key: 'enabled', label: 'Enabled', width: '64px', align: 'right', cell: enabledCell }
	];

	$effect(() => {
		const key = filtersKey;
		const index = pageIndex;
		const size = pageSize;
		untrack(() => {
			if (key !== lastFiltersKey) {
				lastFiltersKey = key;
				if (index !== 1) {
					pageIndex = 1;
					return;
				}
			}
			void load(index, size);
		});
	});

	onDestroy(() => {
		destroyed = true;
		cancelPendingSearch();
	});

	async function load(index: number = pageIndex, size: number = pageSize) {
		const version = ++requestVersion;
		loading = true;
		loadError = null;
		try {
			const response = await getCloudCatalog(backendId, catalogQuery(filters, index, size));
			if (version !== requestVersion || destroyed) return;
			if (response.success && response.data) {
				data = response.data;
				items = response.data.items;
				selected = clearAll();
				rowErrors = {};
				bulkError = null;
			} else {
				items = [];
				loadError = response.message || 'Could not load the catalog.';
			}
		} catch (e: unknown) {
			if (version !== requestVersion || destroyed) return;
			items = [];
			loadError = getApiErrorMessage(e, 'Could not load the catalog.');
		} finally {
			if (version === requestVersion) loading = false;
		}
	}

	function cancelPendingSearch() {
		clearTimeout(searchTimer);
		searchTimer = undefined;
	}

	function applyFilters(next: CatalogFilters) {
		cancelPendingSearch();
		const url = new URL($page.url);
		for (const key of CATALOG_FILTER_PARAMS) url.searchParams.delete(key);
		for (const [key, value] of catalogFiltersToSearchParams(next)) url.searchParams.set(key, value);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	function onQueryChange(value: string) {
		cancelPendingSearch();
		searchTimer = setTimeout(() => {
			searchTimer = undefined;
			applyFilters({ ...filters, q: value });
		}, 300);
	}

	function setShow(show: CatalogShow) {
		applyFilters(withCatalogShow(filters, show));
	}

	async function refreshCatalog() {
		if (refreshing) return;
		refreshing = true;
		refreshError = null;
		refreshResult = null;
		skippedOpen = false;
		try {
			const response = await refreshCloudCatalog(backendId);
			if (destroyed) return;
			if (response.success && response.data) {
				refreshResult = response.data;
				if (!response.data.empty) {
					if (pageIndex !== 1) pageIndex = 1;
					else await load(1, pageSize);
				}
			} else {
				refreshError = response.message || 'Refreshing the catalog failed.';
			}
		} catch (e: unknown) {
			if (destroyed) return;
			refreshError = getApiErrorMessage(e, 'Refreshing the catalog failed.');
		} finally {
			refreshing = false;
		}
	}

	function patchItems(slugs: readonly string[], enabled: boolean, models: Record<string, string> = {}) {
		const target = new Set(slugs);
		items = items.map((item) =>
			target.has(item.slug) ? { ...item, enabled, model_id: models[item.slug] ?? item.model_id } : item
		);
	}

	function shiftEnabledCount(delta: number) {
		if (!data) return;
		data = { ...data, counts: { ...data.counts, enabled: Math.max(0, data.counts.enabled + delta) } };
	}

	function markBusy(slugs: readonly string[], on: boolean) {
		const next = new Set(busySlugs);
		for (const slug of slugs) {
			if (on) next.add(slug);
			else next.delete(slug);
		}
		busySlugs = next;
	}

	async function toggleRow(item: CloudCatalogItem, enabled: boolean) {
		if (busySlugs.has(item.slug)) return;
		const { [item.slug]: _cleared, ...restErrors } = rowErrors;
		rowErrors = restErrors;
		patchItems([item.slug], enabled);
		markBusy([item.slug], true);
		const startedAt = requestVersion;
		try {
			const response = await setCloudCatalogEnabled(backendId, [item.slug], enabled);
			if (destroyed) return;
			if (requestVersion !== startedAt) {
				void load();
				return;
			}
			if (response.success && response.data) {
				patchItems([item.slug], enabled, response.data.models);
				if (response.data.changed.includes(item.slug)) shiftEnabledCount(enabled ? 1 : -1);
			} else {
				patchItems([item.slug], !enabled);
				rowErrors = { ...rowErrors, [item.slug]: response.message || 'Could not update this model.' };
			}
		} catch (e: unknown) {
			if (destroyed) return;
			if (requestVersion !== startedAt) {
				void load();
				return;
			}
			patchItems([item.slug], !enabled);
			rowErrors = { ...rowErrors, [item.slug]: getApiErrorMessage(e, 'Could not update this model.') };
		} finally {
			markBusy([item.slug], false);
		}
	}

	async function bulkToggle(enabled: boolean) {
		const slugs = enabled ? enableTargets : disableTargets;
		if (slugs.length === 0 || bulkBusy) return;
		bulkBusy = enabled ? 'enable' : 'disable';
		bulkError = null;
		patchItems(slugs, enabled);
		markBusy(slugs, true);
		const startedAt = requestVersion;
		try {
			const response = await setCloudCatalogEnabled(backendId, slugs, enabled);
			if (destroyed) return;
			if (requestVersion !== startedAt) {
				void load();
				return;
			}
			if (response.success && response.data) {
				patchItems(slugs, enabled, response.data.models);
				shiftEnabledCount(enabled ? response.data.changed.length : -response.data.changed.length);
				selected = clearAll();
			} else {
				patchItems(slugs, !enabled);
				bulkError = response.message || 'Could not update the selected models.';
			}
		} catch (e: unknown) {
			if (destroyed) return;
			if (requestVersion !== startedAt) {
				void load();
				return;
			}
			patchItems(slugs, !enabled);
			bulkError = getApiErrorMessage(e, 'Could not update the selected models.');
		} finally {
			markBusy(slugs, false);
			bulkBusy = null;
		}
	}

	function onPageChange(next: number) {
		pageIndex = next;
	}

	function onPageSizeChange(next: number) {
		pageSize = next;
		pageIndex = 1;
	}

	function absoluteTime(iso: string | null | undefined): string {
		const date = parseServerDate(iso ?? null);
		if (!date) return '';
		return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
	}

	function deprecatedTip(item: CloudCatalogItem): string {
		const when = absoluteTime(item.deprecated_at);
		return when ? `The provider marked this model deprecated on ${when}.` : 'The provider marked this model deprecated.';
	}

	function missingTip(item: CloudCatalogItem): string {
		const when = absoluteTime(item.missing_since);
		return when
			? `The provider stopped listing this model on ${when}. It cannot be turned on until it comes back.`
			: 'The provider no longer lists this model. It cannot be turned on until it comes back.';
	}

	function idLine(item: CloudCatalogItem): string {
		return pickFacts(item, cloudModelsKind, undefined)
			.map((fact) => fact.value)
			.join(' · ');
	}
</script>

{#snippet modelCell(row: CloudCatalogItem)}
	{@const modelHref = modelPageHref(row)}
	<div class="w-full min-w-0 py-1.5">
		<Tooltip text={modelHref ? `Open ${row.label}: access and allowed presets` : row.label} wrapperClass="block min-w-0">
			{#if modelHref}
				<a href={modelHref} class="block truncate text-base font-medium text-signal hover:underline">{cloudModelsKind.getName(row)}</a>
			{:else}
				<span class="block truncate text-base font-medium text-fg">{cloudModelsKind.getName(row)}</span>
			{/if}
		</Tooltip>
		<Tooltip text={idLine(row)} wrapperClass="block min-w-0">
			<span class="block truncate font-mono text-sm text-fg-subtle">{idLine(row)}</span>
		</Tooltip>
		{#if rowErrors[row.slug]}
			<Tooltip text={rowErrors[row.slug]} wrapperClass="block min-w-0">
				<span class="block truncate text-sm text-danger" role="alert">{rowErrors[row.slug]}</span>
			</Tooltip>
		{/if}
	</div>
{/snippet}

{#snippet tasksCell(row: CloudCatalogItem)}
	{@const split = visibleTasks(row.tasks)}
	<div class="flex min-w-0 flex-wrap items-center gap-1 py-1.5">
		{#each split.shown as task (task)}
			<Badge variant="neutral" class="!text-sm">{taskLabel(task)}</Badge>
		{/each}
		{#if split.hidden.length > 0}
			<Tooltip text={split.hidden.map(taskLabel).join(', ')}>
				<Badge variant="neutral" class="!text-sm font-mono tabular-nums">+{split.hidden.length}</Badge>
			</Tooltip>
		{/if}
	</div>
{/snippet}

{#snippet priceCell(row: CloudCatalogItem)}
	{@const summary = priceSummary(row)}
	{#if summary}
		<Tooltip text={summary.full} wrapperClass="block min-w-0">
			<span class="block truncate font-mono text-sm tabular-nums text-fg">
				{summary.text}{#if summary.extra > 0}<span class="ml-1.5 text-fg-subtle">+{summary.extra}</span>{/if}
			</span>
		</Tooltip>
	{:else}
		<span class="font-mono text-sm text-fg-subtle">—</span>
	{/if}
{/snippet}

{#snippet statusCell(row: CloudCatalogItem)}
	{@const statuses = itemStatuses(row)}
	<div class="flex min-w-0 flex-wrap items-center gap-1 py-1.5">
		{#each statuses as status (status)}
			{#if status === 'missing'}
				<Tooltip text={missingTip(row)}>
					<Badge variant="danger" class="!text-sm">Missing from provider</Badge>
				</Tooltip>
			{:else if status === 'deprecated'}
				<Tooltip text={deprecatedTip(row)}>
					<Badge variant="warning" class="!text-sm">Deprecated</Badge>
				</Tooltip>
			{:else}
				<Tooltip text={suggestedTip(data?.provider.label)}>
					<Badge variant="signal" class="!text-sm">Suggested</Badge>
				</Tooltip>
			{/if}
		{:else}
			<span class="text-sm text-fg-subtle">—</span>
		{/each}
	</div>
{/snippet}

{#snippet enabledCell(row: CloudCatalogItem)}
	{#if canEnable(row)}
		<Switch
			size="sm"
			checked={row.enabled}
			busy={busySlugs.has(row.slug)}
			label="{row.enabled ? 'Disable' : 'Enable'} {row.label}"
			onchange={(next) => toggleRow(row, next)}
		/>
	{:else}
		<Tooltip text={missingTip(row)}>
			<Switch size="sm" checked={false} disabled label="Enable {row.label}" />
		</Tooltip>
	{/if}
{/snippet}

{#snippet cardBody(row: CloudCatalogItem)}
	{@const lines = priceLines(row)}
	{@const statuses = itemStatuses(row)}
	{@const modelHref = modelPageHref(row)}
	<div class="flex items-start justify-between gap-3">
		<div class="min-w-0 flex-1">
			{#if modelHref}
				<a href={modelHref} class="block truncate text-base font-semibold text-signal hover:underline">{cloudModelsKind.getName(row)}</a>
			{:else}
				<span class="block truncate text-base font-semibold text-fg">{cloudModelsKind.getName(row)}</span>
			{/if}
			<span class="block truncate font-mono text-sm text-fg-subtle">{idLine(row)}</span>
		</div>
		{@render enabledCell(row)}
	</div>
	<div class="mt-2 flex flex-wrap items-center gap-1">
		{#each row.tasks as task (task)}
			<Badge variant="neutral" class="!text-sm">{taskLabel(task)}</Badge>
		{/each}
		{#each statuses as status (status)}
			<Badge variant={status === 'missing' ? 'danger' : status === 'deprecated' ? 'warning' : 'signal'} class="!text-sm">
				{status === 'missing' ? 'Missing from provider' : status === 'deprecated' ? 'Deprecated' : 'Suggested'}
			</Badge>
		{/each}
	</div>
	{#if lines.length > 0}
		<div class="mt-2 flex flex-col gap-0.5 font-mono text-sm tabular-nums text-fg">
			{#each lines as line (line)}
				<span>{line}</span>
			{/each}
		</div>
	{/if}
	{#if rowErrors[row.slug]}
		<p class="mt-1 text-sm text-danger" role="alert">{rowErrors[row.slug]}</p>
	{/if}
{/snippet}

<div class="flex flex-col gap-3">
	<div class="flex flex-col gap-3 rounded-lg border border-line bg-surface-1 p-3.5">
		<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
			<Tooltip text="Ask the provider for its current list of models">
				<Button variant="primary" size="sm" icon="refresh" loading={refreshing} onclick={refreshCatalog}>
					Refresh catalog
				</Button>
			</Tooltip>
			{#if data}
				<Tooltip text={refreshedAt ? absoluteTime(refreshedAt) : 'The catalog has not been loaded from the provider yet.'}>
					<span class="font-mono text-sm tabular-nums text-fg-muted" data-testid="catalog-summary">
						{refreshedAt ? `Last refreshed ${timeAgo(refreshedAt)}` : 'Never refreshed'} · {catalogSummaryLine(counts)}
					</span>
				</Tooltip>
			{/if}
		</div>

		{#if notice}
			<Alert variant="neutral" density="compact" icon="info" title={data?.provider.label ? `${data.provider.label} data notice` : 'Data notice'}>
				<p class="text-fg-muted" data-testid="catalog-notice">{notice}</p>
			</Alert>
		{/if}

		{#if presetsHint}
			<Alert variant="info" density="compact" icon title="Make this provider's presets available">
				<p data-testid="catalog-presets-hint">{presetsHint.description}</p>
				{#snippet actions()}
					<Button variant="secondary" size="xs" href={presetsHint.href}>Open presets</Button>
				{/snippet}
			</Alert>
		{/if}

		{#if refreshError}
			<Alert variant="danger" density="compact" icon>
				<p>{refreshError}</p>
				{#snippet actions()}
					<Button variant="ghost" size="xs" onclick={() => (refreshError = null)}>Dismiss</Button>
				{/snippet}
			</Alert>
		{/if}

		{#if refreshResult}
			{#if refreshResult.empty}
				<Alert variant="warning" density="compact" icon>
					<p data-testid="refresh-empty">{refreshResult.message || 'The provider returned no models; nothing was changed.'}</p>
					{#snippet actions()}
						<Button variant="ghost" size="xs" onclick={() => (refreshResult = null)}>Dismiss</Button>
					{/snippet}
				</Alert>
			{:else}
				<Alert variant="success" density="compact" icon title="Catalog refreshed">
					<p class="font-mono tabular-nums" data-testid="refresh-summary">
						{refreshResult.listed} listed · {refreshResult.created} new · {refreshResult.vanished.length} missing from provider · {refreshResult.skipped.length} skipped
					</p>
					{#if refreshResult.skipped.length > 0}
						<div class="mt-1.5">
							<Button variant="ghost" size="xs" ariaExpanded={skippedOpen} onclick={() => (skippedOpen = !skippedOpen)}>
								{skippedOpen ? 'Hide skipped models' : 'Show skipped models'}
							</Button>
						</div>
						{#if skippedOpen}
							<ul class="mt-1.5 flex flex-col gap-1.5" data-testid="skipped-list">
								{#each refreshResult.skipped as skipped (skipped.provider_model_id)}
									<li class="rounded border border-line bg-surface-1 px-2.5 py-1.5 text-fg">
										<span class="block break-all font-mono text-sm">{skipped.provider_model_id}</span>
										<span class="block text-sm text-fg-muted">{skipped.problems.join('; ')}</span>
									</li>
								{/each}
							</ul>
						{/if}
					{/if}
					{#snippet actions()}
						<Button variant="ghost" size="xs" onclick={() => (refreshResult = null)}>Dismiss</Button>
					{/snippet}
				</Alert>
			{/if}
		{/if}
	</div>

	{#if !catalogEmpty}
		<div class="flex flex-wrap items-center gap-2">
			<LibraryFilterBar
				q={filters.q}
				{onQueryChange}
				searchPlaceholder="Search models…"
				searchAriaLabel="Search the catalog"
				filterCount={activeFilterCount}
			>
				{#snippet popover(close: () => void)}
					<FilterPopoverFrame
						label="Catalog filters"
						width="w-[24rem]"
						onClearAll={() => applyFilters(clearAllCatalogFilters(filters))}
						onClose={close}
					>
						<div class="col-span-2">
							<SelectFilterField
								label="Task"
								value={filters.task}
								options={CATALOG_TASK_OPTIONS}
								onChange={(task) => applyFilters({ ...filters, task })}
							/>
						</div>
						<div class="col-span-2">
							<SelectFilterField
								label="Output"
								value={filters.output}
								options={CATALOG_OUTPUT_OPTIONS}
								onChange={(output) => applyFilters({ ...filters, output })}
							/>
						</div>
						<div class="col-span-2">
							<SegmentedFilterGroup
								label="Show"
								options={CATALOG_SHOW_OPTIONS}
								value={catalogShowOf(filters)}
								onChange={setShow}
							/>
						</div>
					</FilterPopoverFrame>
				{/snippet}
			</LibraryFilterBar>
		</div>

		<LibraryFilterChipRow
			{chips}
			onRemoveChip={(key) => applyFilters(clearCatalogFilterChip(filters, key))}
			onClearAll={() => applyFilters(clearAllCatalogFilters(filters))}
			loadedCount={items.length}
			{total}
		/>

		{#if selected.size > 0}
			<div
				class="flex flex-wrap items-center gap-2 rounded-lg border border-signal/28 bg-signal/10 px-3.5 py-2"
				data-testid="bulk-bar"
			>
				<span class="font-mono text-sm tabular-nums text-signal">{selected.size} selected</span>
				<div class="ml-auto flex flex-wrap items-center gap-2">
					<Button
						variant="secondary"
						size="sm"
						icon="check"
						loading={bulkBusy === 'enable'}
						disabled={enableTargets.length === 0 || bulkBusy !== null}
						onclick={() => bulkToggle(true)}
					>
						Enable {enableTargets.length}
					</Button>
					<Button
						variant="secondary"
						size="sm"
						icon="close"
						loading={bulkBusy === 'disable'}
						disabled={disableTargets.length === 0 || bulkBusy !== null}
						onclick={() => bulkToggle(false)}
					>
						Disable {disableTargets.length}
					</Button>
					<Button variant="ghost" size="sm" onclick={() => (selected = clearAll())}>Clear selection</Button>
				</div>
				{#if bulkError}
					<p class="w-full text-sm text-danger" role="alert">{bulkError}</p>
				{/if}
			</div>
		{/if}
	{/if}

	<DataTable
		{columns}
		rows={items}
		getRowId={(row) => row.slug}
		selected={selected}
		onSelectedChange={(next) => (selected = next)}
		{loading}
		isFiltered={hasFilters}
		card={cardBody}
	>
		{#snippet emptyState()}
			{#if loadError}
				<LoadErrorState title="Could not load the catalog" message={loadError} onRetry={() => load()} retrying={loading} />
			{:else}
				<EmptyState
					icon="book"
					title="No models yet"
					description="Refresh the catalog to load this provider's models."
					compact
				>
					{#snippet actions()}
						<Button variant="primary" size="sm" icon="refresh" loading={refreshing} onclick={refreshCatalog}>
							Refresh catalog
						</Button>
					{/snippet}
				</EmptyState>
			{/if}
		{/snippet}
		{#snippet filteredEmptyState()}
			<EmptyState
				icon="search"
				title="No models match your filters"
				description="Try a different search, task, or output."
				compact
			>
				{#snippet actions()}
					<Button variant="ghost" size="sm" onclick={() => applyFilters(DEFAULT_CATALOG_FILTERS)}>
						Clear filters
					</Button>
				{/snippet}
			</EmptyState>
		{/snippet}
	</DataTable>

	{#if !loading && !loadError && total > 0}
		<TablePager
			page={pageIndex}
			pageCount={pageCount(total, pageSize)}
			{pageSize}
			pageSizeOptions={CATALOG_PAGE_SIZE_OPTIONS}
			{onPageChange}
			{onPageSizeChange}
		/>
	{/if}
</div>
