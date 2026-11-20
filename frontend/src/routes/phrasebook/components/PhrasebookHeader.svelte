<script lang="ts">
	import dismiss from '$lib/actions/dismiss';
	import { api, type PhrasebookStateFilter } from '$lib/services/api/index';
	import { Alert, Button, Kbd, Menu, Spinner, Switch } from '$lib/components/ui';
	import {
		ListPageHeader,
		HeaderTitle,
		HeaderSearch,
		HeaderToggle,
		HeaderFilters,
		HeaderSort,
		HeaderViewOptions,
		HeaderDropdown,
		HeaderAction,
		type HeaderStage,
		type FilterChip
	} from '$lib/components/page-header';
	import { phrasebookSort, PHRASEBOOK_SORT_DEFAULT_DIR, type PhrasebookSortBy } from '$lib/stores/phrasebookSort';
	import Icon from '$lib/components/Icon.svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import { isTypingInField } from '$lib/utils/editableTarget';
	import { phrasebookStore } from '$lib/stores/phrasebook';
	import type { PhrasebookFindMode, PhrasebookFindScope } from '$lib/types/api';
	import { isSearching, nonDefaultFilterCount, type FindFilters } from '../phrasebookSearch';

	// One page header row: title, find input, match-mode toggle, a Filters
	// popover for the rest of the find controls, then Import + New Category.
	// Replaces the separate PhrasebookToolbar + PhrasebookFindBar bars.
	let {
		filters,
		searching = false,
		error = null,
		topLevel = [],
		onChange,
		onClear
	}: {
		filters: FindFilters;
		searching?: boolean;
		error?: string | null;
		topLevel?: { id: string; name: string; path: string }[];
		onChange: (patch: Partial<FindFilters>) => void;
		onClear: () => void;
	} = $props();

	let current = $derived($phrasebookStore);

	let fileInputEl: HTMLInputElement | undefined = $state();
	let rootCategoryName = $state('');
	let uploadStatus = $state<string | null>(null);
	let isLoading = $state(false);
	let selectedFile = $state<File | null>(null);
	let fileError = $state<string | null>(null);

	function setStateFilter(value: PhrasebookStateFilter) {
		phrasebookStore.setStateFilter(value);
		phrasebookStore.handleStateFilterChange();
	}

	function acceptFile(file: File | undefined) {
		if (!file) return;
		if (!file.name.endsWith('.yaml') && !file.name.endsWith('.yml')) {
			fileError = 'Please select a YAML file (.yaml or .yml)';
			selectedFile = null;
			return;
		}
		fileError = null;
		selectedFile = file;
	}

	function handleFileChosen(event: Event) {
		acceptFile((event.target as HTMLInputElement).files?.[0]);
	}

	function handleImportDrop(event: DragEvent) {
		event.preventDefault();
		acceptFile(event.dataTransfer?.files?.[0]);
	}

	function resetImportForm() {
		selectedFile = null;
		rootCategoryName = '';
		fileError = null;
		if (fileInputEl) fileInputEl.value = '';
	}

	async function submitImport() {
		if (!selectedFile || isLoading) return;
		isLoading = true;
		uploadStatus = null;

		try {
			const response = await api.importPhrasebookYAML(selectedFile, rootCategoryName || undefined);
			if (response.success && response.data) {
				uploadStatus = `Imported ${response.data.categories_created} categories and ${response.data.values_created} values`;
				await phrasebookStore.loadRootCategories();
				await phrasebookStore.loadAllCategories();
				resetImportForm();
				closeImport();
			} else {
				uploadStatus = response.error || 'Import failed';
			}
		} catch {
			uploadStatus = 'Failed to upload file';
		} finally {
			isLoading = false;
		}
	}

	const scopes: { id: PhrasebookFindScope; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'values', label: 'Values' },
		{ id: 'categories', label: 'Categories' }
	];

	// The "Show" group filters the browsing tree/values panes (three states,
	// including inactive-only). Search's own "Include inactive" is a plain
	// boolean the find API exposes (active+inactive vs. active-only) with no
	// inactive-only mode, so the two can't collapse into one control — both
	// stay, each counted separately toward the badge.
	const stateFilters: { id: PhrasebookStateFilter; label: string }[] = [
		{ id: 'all', label: 'All' },
		{ id: 'active', label: 'Active' },
		{ id: 'inactive', label: 'Inactive' }
	];

	let inputEl: HTMLInputElement | undefined = $state();
	let active = $derived(isSearching(filters.query));
	let stage = $state<HeaderStage>('full');
	let filtersOpen = $state(false);
	let moreWrap: HTMLDivElement | undefined = $state();

	let filterCount = $derived(
		nonDefaultFilterCount(filters, current.stateFilter) - (filters.caseSensitive ? 1 : 0)
	);

	function handleWindowKeydown(e: KeyboardEvent) {
		if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
		if (isTypingInField(e)) return;
		e.preventDefault();
		inputEl?.focus();
		inputEl?.select();
	}

	function handleInputKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			onClear();
		}
	}

	const toggleClass = (pressed: boolean) =>
		`px-2 py-1 text-sm rounded-sm transition-colors duration-100 ${
			pressed ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'
		}`;

	let subtreeOptions = $derived([
		{ value: '', label: 'Everywhere', pinned: true },
		...topLevel.map((category) => ({ value: category.path, label: category.name, description: category.path }))
	]);

	const labelClass = 'block font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle mb-1.5';

	function toggleMode(mode: 'word' | 'regex') {
		onChange({ mode: filters.mode === mode ? 'contains' : mode });
	}

	// Import popover
	let importOpen = $state(false);
	let importTrigger: HTMLDivElement | undefined = $state();
	let importPopoverEl: HTMLDivElement | undefined = $state();
	let importPopoverPos = $state<FlippedMenuPosition>({ left: 0, top: 0, maxHeight: 0 });

	const POPOVER_GAP = 6;
	const IMPORT_POPOVER_WIDTH = 320;
	const IMPORT_POPOVER_HEIGHT_ESTIMATE = 280;

	function updateImportPopoverPosition() {
		const anchor = importTrigger ?? moreWrap;
		if (!anchor) return;
		const heightEstimate = importPopoverEl?.getBoundingClientRect().height || IMPORT_POPOVER_HEIGHT_ESTIMATE;
		importPopoverPos = computeFlippedMenuPosition(anchor, {
			width: IMPORT_POPOVER_WIDTH,
			heightEstimate,
			gap: POPOVER_GAP
		});
	}

	function toggleImport() {
		importOpen = !importOpen;
	}

	function closeImport() {
		importOpen = false;
	}

	function openImport() {
		importOpen = true;
	}

	function resetFilters() {
		onChange({
			inLabel: true,
			inValue: true,
			scope: 'all',
			includeInactive: true,
			pathPrefix: ''
		});
	}

	function styleFor(pos: FlippedMenuPosition): string {
		return `left: ${pos.left}px; ${pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`}`;
	}

	let importPopoverStyle = $derived(styleFor(importPopoverPos));

	$effect(() => {
		if (!importOpen) return;
		updateImportPopoverPosition();
		const raf = requestAnimationFrame(updateImportPopoverPosition);
		window.addEventListener('resize', updateImportPopoverPosition);
		window.addEventListener('scroll', updateImportPopoverPosition, true);
		return () => {
			cancelAnimationFrame(raf);
			window.removeEventListener('resize', updateImportPopoverPosition);
			window.removeEventListener('scroll', updateImportPopoverPosition, true);
		};
	});
	let chips = $derived.by(() => {
		const list: FilterChip[] = [];
		if (current.stateFilter !== 'all') {
			list.push({
				id: 'show',
				label: current.stateFilter === 'active' ? 'Active only' : 'Inactive only',
				onremove: () => setStateFilter('all')
			});
		}
		if (filters.inLabel !== true || filters.inValue !== true) {
			list.push({
				id: 'fields',
				label: filters.inLabel ? 'Label only' : 'Value only',
				onremove: () => onChange({ inLabel: true, inValue: true })
			});
		}
		if (filters.scope !== 'all') {
			list.push({
				id: 'scope',
				label: `Scope: ${scopes.find((scope) => scope.id === filters.scope)?.label ?? filters.scope}`,
				onremove: () => onChange({ scope: 'all' })
			});
		}
		if (!filters.includeInactive) {
			list.push({ id: 'inactive', label: 'Hide inactive', onremove: () => onChange({ includeInactive: true }) });
		}
		if (filters.pathPrefix) {
			const name = topLevel.find((category) => category.path === filters.pathPrefix)?.name ?? filters.pathPrefix;
			list.push({ id: 'subtree', label: `In ${name}`, onremove: () => onChange({ pathPrefix: '' }) });
		}
		return list;
	});

	function clearAllFilters() {
		resetFilters();
		if (current.stateFilter !== 'all') setStateFilter('all');
	}

	const sortOptions = [
		{ value: 'path', label: 'Path' },
		{ value: 'name', label: 'Name' },
		{ value: 'updated', label: 'Updated' }
	];

	function pickSort(value: string) {
		phrasebookSort.set({ by: value as PhrasebookSortBy, dir: PHRASEBOOK_SORT_DEFAULT_DIR[value as PhrasebookSortBy] });
	}

	function pickSortDir(dir: 'asc' | 'desc') {
		phrasebookSort.update((sort) => ({ ...sort, dir }));
	}

	async function refresh() {
		await phrasebookStore.loadRootCategories();
		await phrasebookStore.loadAllCategories();
	}

	let moreItems = $derived([
		...(stage !== 'full' ? [{ id: 'import', label: 'Import YAML…', icon: 'upload' }] : []),
		...(stage === 'narrow'
			? [
					{ id: 'refresh', label: 'Refresh', icon: 'refresh' },
					...sortOptions.map((option) => ({
						id: `sort-${option.value}`,
						label: `Sort by ${option.label.toLowerCase()}`,
						icon: $phrasebookSort.by === option.value ? 'check' : undefined
					})),
					{
						id: 'sort-dir',
						label: $phrasebookSort.dir === 'desc' ? 'Descending. Switch to ascending' : 'Ascending. Switch to descending',
						icon: $phrasebookSort.dir === 'desc' ? 'arrow-down' : 'arrow-up'
					}
				]
			: [])
	]);

	function handleMore(id: string) {
		if (id === 'import') openImport();
		else if (id === 'refresh') void refresh();
		else if (id === 'sort-dir') pickSortDir($phrasebookSort.dir === 'desc' ? 'asc' : 'desc');
		else if (id.startsWith('sort-')) pickSort(id.slice(5));
	}
</script>

<svelte:window onkeydown={handleWindowKeydown} />

{#snippet titleZone(s: HeaderStage)}
	<div data-phrasebook-header class="contents">
		<HeaderTitle
			title="Phrasebook"
			count={current.allCategories.length}
			unit="categories"
			unitOne="category"
			filterCount={chips.length}
			onOpenFilters={() => (filtersOpen = true)}
			showUnit={s === 'full'}
			narrow={s === 'narrow'}
		/>
	</div>
{/snippet}

{#snippet searchZone(s: HeaderStage)}
	<HeaderSearch
		value={filters.query}
		placeholder="Find in phrasebook…"
		label="Find in phrasebook"
		{error}
		oninput={(value) => onChange({ query: value })}
		onclear={onClear}
		onkeydown={handleInputKeydown}
		bind:inputEl
		inputAttrs={{ 'data-find-input': '' }}
		narrow={s === 'narrow'}
	>
		{#snippet modifiers()}
			{#if searching}
				<Spinner size="sm" />
			{:else if !active && s !== 'narrow'}
				<Kbd keys="/" />
			{/if}
			<HeaderToggle
				text="Aa"
				label="Match case"
				tooltip={filters.caseSensitive ? 'Match case on' : 'Match case'}
				pressed={filters.caseSensitive}
				onclick={() => onChange({ caseSensitive: !filters.caseSensitive })}
			/>
			<HeaderToggle
				text="ab"
				label="Whole word"
				tooltip={filters.mode === 'word' ? 'Whole word on' : 'Match whole words'}
				pressed={filters.mode === 'word'}
				onclick={() => toggleMode('word')}
			/>
			<HeaderToggle
				icon="regex"
				label="Regular expression"
				pressed={filters.mode === 'regex'}
				onclick={() => toggleMode('regex')}
			/>
		{/snippet}
	</HeaderSearch>
{/snippet}

{#snippet filtersZone(s: HeaderStage)}
	<HeaderFilters
		bind:open={filtersOpen}
		activeCount={filterCount}
		{chips}
		onClear={clearAllFilters}
		iconOnly={s === 'narrow'}
		label="Search filters"
	>
		{#snippet children()}
			<div class="col-span-2">
				<span class={labelClass}>Show</span>
				<div class="flex items-center gap-0.5 bg-surface-3/50 rounded p-0.5" role="group" aria-label="Show">
					{#each stateFilters as stateOption (stateOption.id)}
						<button
							type="button"
							class="flex-1 {toggleClass(current.stateFilter === stateOption.id)}"
							aria-pressed={current.stateFilter === stateOption.id}
							onclick={() => setStateFilter(stateOption.id)}
						>
							{stateOption.label}
						</button>
					{/each}
				</div>
			</div>

			{#if filters.scope !== 'categories'}
				<div class="col-span-2">
					<span class={labelClass}>Fields</span>
					<div class="flex items-center gap-3 text-sm text-fg" role="group" aria-label="Value fields">
						<label class="flex items-center gap-1.5 cursor-pointer select-none">
							<input
								type="checkbox"
								class="accent-accent"
								checked={filters.inLabel}
								disabled={filters.inLabel && !filters.inValue}
								onchange={(e) => onChange({ inLabel: e.currentTarget.checked })}
							/>
							Label
						</label>
						<label class="flex items-center gap-1.5 cursor-pointer select-none">
							<input
								type="checkbox"
								class="accent-accent"
								checked={filters.inValue}
								disabled={filters.inValue && !filters.inLabel}
								onchange={(e) => onChange({ inValue: e.currentTarget.checked })}
							/>
							Value
						</label>
					</div>
				</div>
			{/if}

			<div class="col-span-2">
				<span class={labelClass}>Scope</span>
				<div class="flex items-center gap-0.5 bg-surface-3/50 rounded p-0.5" role="group" aria-label="Scope">
					{#each scopes as scope (scope.id)}
						<button
							type="button"
							class="flex-1 {toggleClass(filters.scope === scope.id)}"
							aria-pressed={filters.scope === scope.id}
							onclick={() => onChange({ scope: scope.id })}
						>
							{scope.label}
						</button>
					{/each}
				</div>
			</div>

			<div class="col-span-2 flex items-center justify-between">
				<span class="text-sm font-medium text-fg">Include inactive</span>
				<Switch
					checked={filters.includeInactive}
					label="Include inactive"
					size="sm"
					onchange={(v) => onChange({ includeInactive: v })}
				/>
			</div>

			<div class="col-span-2">
				<span class={labelClass}>Subtree</span>
				<HeaderDropdown
					block
					ariaLabel="Subtree"
					value={filters.pathPrefix}
					options={subtreeOptions}
					onchange={(value) => onChange({ pathPrefix: value })}
				/>
			</div>
		{/snippet}
	</HeaderFilters>
{/snippet}

{#snippet sortZone()}
	<HeaderSort
		options={sortOptions}
		value={$phrasebookSort.by}
		dir={$phrasebookSort.dir}
		onchange={pickSort}
		ondir={pickSortDir}
	/>
{/snippet}

{#snippet viewZone()}
	<HeaderViewOptions onrefresh={refresh} />
{/snippet}

{#snippet secondaryZone(s: HeaderStage)}
	{#if s === 'full'}
		<div class="relative flex-shrink-0" bind:this={importTrigger} data-import-trigger>
			<Button
				size="sm"
				variant="secondary"
				icon="upload"
				class="h-8"
				ariaExpanded={importOpen}
				ariaHaspopup="dialog"
				onclick={toggleImport}
			>
				Import
			</Button>
		</div>
	{/if}
{/snippet}

{#snippet primaryZone(s: HeaderStage)}
	<HeaderAction
		primary
		icon="plus"
		label="New category"
		compact={s === 'narrow'}
		onclick={() => phrasebookStore.handleNewCategory()}
	/>
{/snippet}

{#snippet moreZone()}
	<div bind:this={moreWrap} class="flex-none">
		<Menu label="More actions" tooltip="More actions" items={moreItems} onselect={handleMore} width={220} />
	</div>
{/snippet}

<ListPageHeader
	bind:stage
	midBelow={1000}
	tightBelow={900}
	title={titleZone}
	search={searchZone}
	filters={filtersZone}
	sort={sortZone}
	view={viewZone}
	secondary={secondaryZone}
	primary={primaryZone}
	more={moreZone}
/>

{#if importOpen}
									<div
						use:portal
						use:overlayLayer
						bind:this={importPopoverEl}
						use:dismiss={{ onDismiss: closeImport, ignore: [importTrigger, moreWrap] }}
						class="fixed z-overlay w-80 bg-surface-1 border border-line-strong rounded-xl shadow-overlay p-3 flex flex-col gap-3"
						style={importPopoverStyle}
						role="dialog"
						aria-label="Import phrasebook YAML"
						data-import-popover
					>
						<div>
							<span class="block font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle mb-1.5">File</span>
							<label
								class="flex flex-col items-center justify-center gap-1 px-3 py-4 rounded-lg border border-dashed border-line-strong text-center cursor-pointer hover:border-line-hover hover:bg-surface-2/50 transition-colors {isLoading
									? 'opacity-50 pointer-events-none'
									: ''}"
								ondragover={(e) => e.preventDefault()}
								ondrop={handleImportDrop}
							>
								<Icon name="upload" className="w-5 h-5 text-fg-subtle" />
								<span class="text-sm text-fg" data-import-file-name>
									{selectedFile ? selectedFile.name : 'Drop a .yaml file, or choose one'}
								</span>
								{#if !selectedFile}
									<span class="text-sm text-fg-subtle underline">Choose .yaml file</span>
								{/if}
								<input
									bind:this={fileInputEl}
									type="file"
									accept=".yaml,.yml"
									class="hidden"
									data-import-file-input
									onchange={handleFileChosen}
								/>
							</label>
							{#if fileError}
								<p class="mt-1.5 text-sm text-danger" data-import-file-error>{fileError}</p>
							{/if}
						</div>

						<div>
							<label
								class="block font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle mb-1.5"
								for="phrasebook-import-root"
							>
								Nest under a new root category
							</label>
							<input
								id="phrasebook-import-root"
								type="text"
								class="input text-sm w-full"
								placeholder="e.g. Imported"
								bind:value={rootCategoryName}
							/>
							<p class="mt-1 text-sm text-fg-subtle">Leave empty to import at the top level.</p>
						</div>

						<div class="pt-1 border-t border-line flex justify-end gap-2">
							<Button variant="ghost" size="sm" disabled={isLoading} onclick={closeImport}>Cancel</Button>
							<Button variant="primary" size="sm" loading={isLoading} disabled={!selectedFile} onclick={submitImport}>
								Import
							</Button>
						</div>
					</div>
				{/if}

{#if error}
	<p class="px-6 pt-1.5 text-sm text-danger" data-find-error role="alert">{error}</p>
{/if}

{#if uploadStatus}
	<div class="px-6 pt-1.5">
		<Alert variant={uploadStatus.includes('Imported') ? 'success' : 'danger'} density="compact">
			{uploadStatus}
			{#snippet actions()}
				<button class="underline" onclick={() => (uploadStatus = null)}>dismiss</button>
			{/snippet}
		</Alert>
	</div>
{/if}
