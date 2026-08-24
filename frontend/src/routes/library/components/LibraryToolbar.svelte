<script lang="ts">
	import { onDestroy } from 'svelte';
	import { libraryStore } from '$lib/stores/library';
	import { historyTileSize, type HistoryTileSize } from '$lib/stores/historyTileSize';
	import { PageHeader, IconButton, Button } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	// Self-contained: reads/writes libraryStore directly. Modal-opening
	// callbacks stay as props since modal state lives on the page.
	export let onOpenAddTag: () => void;
	export let onPickFiles: () => void;

	$: state = $libraryStore;
	$: counts = state.mediaTypeCounts;

	let isMoreMenuOpen = false;

	const mediaTypes: Array<{ value: 'all' | 'image' | 'video' | 'audio'; label: string; icon?: string }> = [
		{ value: 'all', label: 'All' },
		{ value: 'image', label: 'Images', icon: 'image' },
		{ value: 'video', label: 'Videos', icon: 'video' },
		{ value: 'audio', label: 'Audio', icon: 'audio' }
	];

	const tileSizes: Array<{ value: HistoryTileSize; label: string; title: string }> = [
		{ value: 'small', label: 'S', title: 'Small tiles' },
		{ value: 'medium', label: 'M', title: 'Medium tiles' },
		{ value: 'large', label: 'L', title: 'Large tiles' }
	];

	async function handleMediaTypeChange(value: 'all' | 'image' | 'video' | 'audio') {
		libraryStore.setFilter('mediaType', value);
		await libraryStore.load();
	}

	// Search matches the item's original filename server-side, debounced.
	let searchDebounce: ReturnType<typeof setTimeout> | undefined;
	function handleSearchInput(event: Event) {
		const value = (event.currentTarget as HTMLInputElement).value;
		libraryStore.setFilter('search', value);
		clearTimeout(searchDebounce);
		searchDebounce = setTimeout(() => libraryStore.load(), 300);
	}

	onDestroy(() => clearTimeout(searchDebounce));

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (!target.closest('.library-more-menu')) isMoreMenuOpen = false;
	}
</script>

<svelte:window on:click={handleWindowClick} />

<PageHeader wrap sticky={false}>
	<div class="flex items-center gap-2 md:gap-4 w-full">
		<!-- Left: title + count -->
		<div class="flex items-baseline gap-3 flex-shrink-0">
			<span class="text-sm font-semibold text-fg">Library</span>
			<span
				class="font-mono tabular-nums text-2xs uppercase tracking-[0.07em] text-fg-subtle whitespace-nowrap"
			>
				{state.totalCount} items
			</span>
		</div>

		<div class="hidden md:block h-6 w-px bg-line-strong flex-shrink-0"></div>

		<!-- Search -->
		<div class="relative flex-1 min-w-[8rem] max-w-md">
			<Icon
				name="search"
				className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
			/>
			<input
				type="text"
				class="input text-xs py-1.5 pl-8 pr-3 bg-surface-2/50 w-full"
				placeholder="Search by file name..."
				value={state.filters.search}
				on:input={handleSearchInput}
			/>
		</div>

		<!-- Media type -->
		<div class="hidden md:flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5 flex-shrink-0">
			{#each mediaTypes as type}
				<button
					class="flex items-center gap-1.5 px-2 py-1 text-xs rounded-sm transition-colors duration-100 {state
						.filters.mediaType === type.value
						? 'bg-signal/10 text-signal'
						: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
					title={type.label}
					aria-pressed={state.filters.mediaType === type.value}
					on:click={() => handleMediaTypeChange(type.value)}
				>
					{#if type.icon}
						<Icon name={type.icon} className="w-3.5 h-3.5" />
					{:else}
						{type.label}
					{/if}
					{#if type.value !== 'all' && counts[type.value]}
						<span class="font-mono tabular-nums text-2xs">{counts[type.value]}</span>
					{/if}
				</button>
			{/each}
		</div>

		<div class="flex-1 hidden md:block"></div>

		<!-- Right: view controls + actions -->
		<div class="flex items-center gap-2 ml-auto md:ml-0 flex-shrink-0">
			<div
				class="hidden md:flex items-center gap-0.5 bg-surface-2/50 rounded p-0.5"
				role="radiogroup"
				aria-label="Tile size"
			>
				{#each tileSizes as size}
					<button
						role="radio"
						aria-checked={$historyTileSize === size.value}
						title={size.title}
						class="w-6 py-1 font-mono text-2xs rounded-sm transition-colors duration-100 {$historyTileSize ===
						size.value
							? 'bg-signal/10 text-signal'
							: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
						on:click={() => historyTileSize.set(size.value)}
					>
						{size.label}
					</button>
				{/each}
			</div>

			<IconButton icon="refresh" label="Refresh" onclick={() => libraryStore.load()} />

			<Button
				variant="primary"
				size="sm"
				icon="upload"
				loading={state.uploading}
				disabled={state.uploading}
				onclick={onPickFiles}
			>
				Upload
			</Button>

			<div class="relative library-more-menu">
				<IconButton
					icon="more"
					label="More actions"
					active={isMoreMenuOpen}
					onclick={() => (isMoreMenuOpen = !isMoreMenuOpen)}
				/>
				{#if isMoreMenuOpen}
					<div
						class="absolute right-0 top-full mt-1 min-w-[180px] bg-surface-2 rounded-xl border border-line-strong shadow-floating z-50 py-1"
					>
						<button
							class="w-full px-3 py-2 text-left text-xs hover:bg-surface-3/50 transition-colors flex items-center gap-2 text-fg-muted hover:text-fg"
							on:click={() => {
								isMoreMenuOpen = false;
								onOpenAddTag();
							}}
						>
							<Icon name="plus" className="w-3.5 h-3.5" />
							Add tag
						</button>
					</div>
				{/if}
			</div>
		</div>
	</div>
</PageHeader>
