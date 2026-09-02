<script lang="ts">
	import { api, type PhrasebookStateFilter } from '$lib/services/api/index';
	import { PageHeader, Alert, Button, Kbd, Spinner, Switch } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
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

	const modes: { id: PhrasebookFindMode; label: string }[] = [
		{ id: 'contains', label: 'Contains' },
		{ id: 'word', label: 'Word' },
		{ id: 'regex', label: 'Regex' }
	];

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
	let filterCount = $derived(nonDefaultFilterCount(filters, current.stateFilter));

	function handleWindowKeydown(e: KeyboardEvent) {
		if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
		const target = e.target as HTMLElement | null;
		const tag = target?.tagName;
		if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) return;
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
		`px-2 py-1 text-xs rounded-sm transition-colors duration-100 ${
			pressed ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'
		}`;

	// Filters popover
	let filtersOpen = $state(false);
	let filtersTrigger: HTMLButtonElement | undefined = $state();
	let filtersPopoverEl: HTMLDivElement | undefined = $state();
	let popoverPos = $state<FlippedMenuPosition>({ left: 0, top: 0 });

	const POPOVER_WIDTH = 288;
	const POPOVER_HEIGHT_ESTIMATE = 340;
	const POPOVER_GAP = 6;

	function updatePopoverPosition() {
		if (!filtersTrigger) return;
		const heightEstimate = filtersPopoverEl?.getBoundingClientRect().height || POPOVER_HEIGHT_ESTIMATE;
		popoverPos = computeFlippedMenuPosition(filtersTrigger, {
			width: POPOVER_WIDTH,
			heightEstimate,
			gap: POPOVER_GAP
		});
	}

	function toggleFilters() {
		filtersOpen = !filtersOpen;
	}

	function closeFilters() {
		filtersOpen = false;
	}

	// Import popover
	let importOpen = $state(false);
	let importTrigger: HTMLButtonElement | undefined = $state();
	let importPopoverEl: HTMLDivElement | undefined = $state();
	let importPopoverPos = $state<FlippedMenuPosition>({ left: 0, top: 0 });

	const IMPORT_POPOVER_WIDTH = 320;
	const IMPORT_POPOVER_HEIGHT_ESTIMATE = 280;

	function updateImportPopoverPosition() {
		if (!importTrigger) return;
		const heightEstimate = importPopoverEl?.getBoundingClientRect().height || IMPORT_POPOVER_HEIGHT_ESTIMATE;
		importPopoverPos = computeFlippedMenuPosition(importTrigger, {
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

	function handleWindowPointerDown(e: PointerEvent) {
		const target = e.target as Node;
		if (filtersOpen && !filtersTrigger?.contains(target) && !filtersPopoverEl?.contains(target)) {
			closeFilters();
		}
		if (importOpen && !importTrigger?.contains(target) && !importPopoverEl?.contains(target)) {
			closeImport();
		}
	}

	function handlePopoverKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		if (filtersOpen || importOpen) {
			e.preventDefault();
			e.stopPropagation();
			closeFilters();
			closeImport();
		}
	}

	function resetFilters() {
		onChange({
			caseSensitive: false,
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

	let popoverStyle = $derived(styleFor(popoverPos));
	let importPopoverStyle = $derived(styleFor(importPopoverPos));

	$effect(() => {
		if (!filtersOpen) return;
		updatePopoverPosition();
		const raf = requestAnimationFrame(updatePopoverPosition);
		window.addEventListener('resize', updatePopoverPosition);
		window.addEventListener('scroll', updatePopoverPosition, true);
		return () => {
			cancelAnimationFrame(raf);
			window.removeEventListener('resize', updatePopoverPosition);
			window.removeEventListener('scroll', updatePopoverPosition, true);
		};
	});

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
</script>

<svelte:window onkeydown={(e) => { handleWindowKeydown(e); handlePopoverKeydown(e); }} onpointerdown={handleWindowPointerDown} />

<PageHeader sticky={false}>
	<div class="flex items-center gap-3 w-full" data-phrasebook-header>
		<div class="flex items-center gap-3 flex-shrink-0">
			<svg class="w-5 h-5 text-fg-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14" />
			</svg>
			<span class="text-sm font-semibold text-fg whitespace-nowrap">Phrasebook Management</span>
		</div>

		<div class="relative flex-1 min-w-[16rem] max-w-2xl">
			<Icon
				name="search"
				className="w-4 h-4 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
			/>
			<input
				bind:this={inputEl}
				type="text"
				class="input text-sm py-1.5 pl-8 pr-16 bg-surface-2/50 w-full {error ? 'border-danger focus:ring-danger' : ''}"
				placeholder="Find in phrasebook…"
				aria-label="Find in phrasebook"
				aria-invalid={!!error}
				data-find-input
				value={filters.query}
				oninput={(e) => onChange({ query: e.currentTarget.value })}
				onkeydown={handleInputKeydown}
			/>
			<div class="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1.5">
				{#if searching}
					<Spinner size="sm" />
				{:else if !active}
					<Kbd keys="/" />
				{:else}
					<Tooltip text="Clear" kbd="Esc" position="bottom">
						<button
							type="button"
							class="p-0.5 rounded text-fg-muted hover:text-fg hover:bg-surface-3/50 transition-colors"
							aria-label="Clear search"
							onclick={onClear}
						>
							<Icon name="close" className="w-3.5 h-3.5" />
						</button>
					</Tooltip>
				{/if}
			</div>
		</div>

		<div class="flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5 flex-shrink-0" role="group" aria-label="Match mode">
			{#each modes as mode (mode.id)}
				<button
					type="button"
					class={toggleClass(filters.mode === mode.id)}
					aria-pressed={filters.mode === mode.id}
					onclick={() => onChange({ mode: mode.id })}
				>
					{mode.label}
				</button>
			{/each}
		</div>

		<div class="relative flex-shrink-0">
			<button
				bind:this={filtersTrigger}
				type="button"
				class="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs font-medium transition-colors {filtersOpen ||
				filterCount > 0
					? 'bg-signal/10 text-signal'
					: 'bg-surface-3 text-fg hover:bg-line-hover'}"
				aria-expanded={filtersOpen}
				aria-haspopup="dialog"
				data-filters-trigger
				onclick={toggleFilters}
			>
				<Icon name="sliders" className="w-3.5 h-3.5" />
				Filters
				{#if filterCount > 0}
					<span class="font-mono text-2xs tabular-nums px-1 rounded-sm bg-signal/15" data-filters-count
						>{filterCount}</span
					>
				{/if}
			</button>

			{#if filtersOpen}
				<div
					use:portal
					bind:this={filtersPopoverEl}
					class="fixed z-[9999] w-72 bg-surface-1 border border-line-strong rounded-xl shadow-overlay p-3 flex flex-col gap-3"
					style={popoverStyle}
					role="dialog"
					aria-label="Search filters"
					data-filters-popover
				>
					<div>
						<span class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Show</span>
						<div class="flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5" role="group" aria-label="Show">
							{#each stateFilters as stateOption (stateOption.id)}
								<button
									type="button"
									class={toggleClass(current.stateFilter === stateOption.id)}
									aria-pressed={current.stateFilter === stateOption.id}
									onclick={() => setStateFilter(stateOption.id)}
								>
									{stateOption.label}
								</button>
							{/each}
						</div>
					</div>

					<div class="pt-2 border-t border-line flex flex-col gap-3">
						<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Search</span>

						<div class="flex items-center justify-between">
							<span class="text-xs font-medium text-fg">Case-sensitive</span>
							<Switch
								checked={filters.caseSensitive}
								label="Match case"
								size="sm"
								onchange={(v) => onChange({ caseSensitive: v })}
							/>
						</div>

						{#if filters.scope !== 'categories'}
							<div>
								<span class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Fields</span>
								<div class="flex items-center gap-3 text-xs text-fg" role="group" aria-label="Value fields">
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

						<div>
							<span class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">Scope</span>
							<div class="flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5" role="group" aria-label="Scope">
								{#each scopes as scope (scope.id)}
									<button
										type="button"
										class={toggleClass(filters.scope === scope.id)}
										aria-pressed={filters.scope === scope.id}
										onclick={() => onChange({ scope: scope.id })}
									>
										{scope.label}
									</button>
								{/each}
							</div>
						</div>

						<div class="flex items-center justify-between">
							<span class="text-xs font-medium text-fg">Include inactive</span>
							<Switch
								checked={filters.includeInactive}
								label="Include inactive"
								size="sm"
								onchange={(v) => onChange({ includeInactive: v })}
							/>
						</div>

						<div>
							<label class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5" for="phrasebook-filters-subtree">
								Subtree
							</label>
							<select
								id="phrasebook-filters-subtree"
								class="input text-xs py-1 w-full"
								value={filters.pathPrefix}
								onchange={(e) => onChange({ pathPrefix: e.currentTarget.value })}
							>
								<option value="">Everywhere</option>
								{#each topLevel as category (category.id)}
									<option value={category.path}>{category.name}</option>
								{/each}
							</select>
						</div>

						<div class="pt-1 flex justify-end">
							<Button variant="ghost" size="sm" disabled={nonDefaultFilterCount(filters) === 0} onclick={resetFilters}>
								Reset filters
							</Button>
						</div>
					</div>
				</div>
			{/if}
		</div>

		<div class="ml-auto flex items-center gap-3 flex-shrink-0">
			<div class="relative flex-shrink-0">
				<button
					bind:this={importTrigger}
					type="button"
					class="btn-header-secondary"
					aria-expanded={importOpen}
					aria-haspopup="dialog"
					data-import-trigger
					onclick={toggleImport}
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
					</svg>
					Import
				</button>

				{#if importOpen}
					<div
						use:portal
						bind:this={importPopoverEl}
						class="fixed z-[9999] w-80 bg-surface-1 border border-line-strong rounded-xl shadow-overlay p-3 flex flex-col gap-3"
						style={importPopoverStyle}
						role="dialog"
						aria-label="Import phrasebook YAML"
						data-import-popover
					>
						<div>
							<span class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5">File</span>
							<label
								class="flex flex-col items-center justify-center gap-1 px-3 py-4 rounded-lg border border-dashed border-line-strong text-center cursor-pointer hover:border-line-hover hover:bg-surface-2/50 transition-colors {isLoading
									? 'opacity-50 pointer-events-none'
									: ''}"
								ondragover={(e) => e.preventDefault()}
								ondrop={handleImportDrop}
							>
								<Icon name="upload" className="w-5 h-5 text-fg-subtle" />
								<span class="text-xs text-fg" data-import-file-name>
									{selectedFile ? selectedFile.name : 'Drop a .yaml file, or choose one'}
								</span>
								{#if !selectedFile}
									<span class="text-2xs text-fg-subtle underline">Choose .yaml file</span>
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
								<p class="mt-1.5 text-xs text-danger" data-import-file-error>{fileError}</p>
							{/if}
						</div>

						<div>
							<label
								class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1.5"
								for="phrasebook-import-root"
							>
								Nest under a new root category
							</label>
							<input
								id="phrasebook-import-root"
								type="text"
								class="input text-xs w-full"
								placeholder="e.g. Imported"
								bind:value={rootCategoryName}
							/>
							<p class="mt-1 text-2xs text-fg-subtle">Leave empty to import at the top level.</p>
						</div>

						<div class="pt-1 border-t border-line flex justify-end gap-2">
							<Button variant="ghost" size="sm" disabled={isLoading} onclick={closeImport}>Cancel</Button>
							<Button variant="primary" size="sm" loading={isLoading} disabled={!selectedFile} onclick={submitImport}>
								Import
							</Button>
						</div>
					</div>
				{/if}
			</div>

			<button class="btn-header-primary" onclick={() => phrasebookStore.handleNewCategory()}>
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
				</svg>
				New Category
			</button>
		</div>
	</div>
</PageHeader>

{#if error}
	<p class="px-6 pt-1.5 text-xs text-danger" data-find-error role="alert">{error}</p>
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
