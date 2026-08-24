<script lang="ts">
	import { onMount } from 'svelte';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { modelLibraryStore } from '$lib/stores/modelLibrary';
	import { toggleModelFavoriteOptimistic } from '$lib/utils/modelFavorite';
	import Icon from '$lib/components/Icon.svelte';
	import ModelResultRow from './ModelResultRow.svelte';

	// Drill-down browser for the "Collections" view of a model/lora picker.
	// Starts at the root folder list; entering a folder shows its subfolders
	// plus the models that are direct members of it, with a breadcrumb to go up.
	export let modelType: string = 'checkpoint';
	// file_paths to hide (e.g. loras already added to the picker).
	/** Model ids already chosen, hidden from the browser.
	 *
	 * Keyed on id rather than file_path: a model that lives only on a remote backend has
	 * no local path, and non-admin payloads omit file_path entirely — so a path-keyed set
	 * silently matched nothing and already-added models kept reappearing. */
	export let excludeIds: Set<string> = new Set();
	export let onSelect: (model: any) => void;
	export let limit: number = 100;
	// When non-empty, the browser leaves the folder drill-down and searches across
	// every model in any of the user's collections (a flat result list).
	export let search: string = '';

	$: collections = $modelLibraryStore.collections;

	// null currentId = root (top-level folders only, no models).
	let currentId: string | null = null;
	let path: { id: string; name: string }[] = [];
	let models: any[] = [];
	let loading = false;

	onMount(() => {
		if ($modelLibraryStore.collections.length === 0) modelLibraryStore.load();
	});

	$: searching = search.trim().length > 0;

	// Direct child folders of the current node (hidden while searching).
	$: subfolders = searching ? [] : collections.filter((c) => (c.parent_id ?? null) === currentId);
	$: visibleModels = models.filter((m) => !excludeIds.has(m.id));

	// Re-fetch whenever the search term flips or changes (debounced), or the
	// current folder changes (handled by enter/goTo calling loadModels directly).
	let searchDebounce: ReturnType<typeof setTimeout> | null = null;
	let lastSearch = '';
	$: {
		const term = search.trim();
		if (term !== lastSearch) {
			lastSearch = term;
			if (searchDebounce) clearTimeout(searchDebounce);
			searchDebounce = setTimeout(loadModels, 250);
		}
	}

	async function loadModels() {
		const term = search.trim();
		// Root with no search shows folders only, no models.
		if (!term && currentId === null) {
			models = [];
			return;
		}
		loading = true;
		try {
			const response = await api.getModels({
				model_type: modelType,
				include_tags: true,
				limit,
				// Search spans the whole curated set; browsing scopes to one folder.
				...(term
					? { search: term, in_any_collection: true }
					: { collection_id: currentId as string })
			});
			models = response.success && response.data?.models ? response.data.models : [];
		} catch (error) {
			logger.error('Failed to load collection models:', getErrorMessage(error));
			models = [];
		} finally {
			loading = false;
		}
	}

	function enter(folder: any) {
		currentId = folder.id;
		path = [...path, { id: folder.id, name: folder.name }];
		loadModels();
	}

	function goToRoot() {
		currentId = null;
		path = [];
		models = [];
	}

	function goTo(index: number) {
		path = path.slice(0, index + 1);
		currentId = path[index].id;
		loadModels();
	}

	function toggleFavorite(model: any, event: Event) {
		event.stopPropagation();
		event.preventDefault();
		void toggleModelFavoriteOptimistic(model, (favorite) => {
			model.is_favorite = favorite;
			models = models;
		});
	}
</script>

<div class="flex flex-col">
	{#if searching}
		<!-- Search spans the whole curated set, so the folder breadcrumb is hidden -->
		<div class="px-2 py-1.5 border-b border-line text-2xs uppercase tracking-wide text-fg-subtle">
			Searching your collections
		</div>
	{:else}
	<!-- Breadcrumb -->
	<div class="flex items-center gap-1 px-2 py-1.5 border-b border-line text-xs flex-wrap">
		<button
			type="button"
			class="inline-flex items-center gap-1 hover:text-fg {currentId === null
				? 'text-fg'
				: 'text-fg-muted'}"
			on:click={goToRoot}
		>
			<Icon name="folder" className="w-3.5 h-3.5" />
			Collections
		</button>
		{#each path as seg, i}
			<Icon name="chevron-right" className="w-3 h-3 text-fg-subtle" />
			<button
				type="button"
				class="hover:text-fg truncate max-w-[8rem] {i === path.length - 1
					? 'text-fg'
					: 'text-fg-muted'}"
				on:click={() => goTo(i)}
			>
				{seg.name}
			</button>
		{/each}
	</div>
	{/if}

	<div class="max-h-64 overflow-y-auto">
		<!-- Subfolders -->
		{#each subfolders as folder (folder.id)}
			<button
				type="button"
				on:click={() => enter(folder)}
				class="w-full flex items-center gap-2 p-2 hover:bg-surface-2 border-b border-line last:border-b-0 text-left"
			>
				<Icon name="folder" className="w-4 h-4 text-signal shrink-0" />
				<span class="flex-1 min-w-0 truncate text-sm">{folder.name}</span>
				<span class="tabular-nums text-2xs text-fg-subtle">{folder.item_count}</span>
				<Icon name="chevron-right" className="w-3.5 h-3.5 text-fg-subtle shrink-0" />
			</button>
		{/each}

		<!-- Models that are direct members of this folder -->
		{#if loading}
			<div class="p-3 text-center text-fg-muted text-sm">Loading…</div>
		{:else}
			{#each visibleModels as model (model.id)}
				<ModelResultRow {model} size="sm" {onSelect} onToggleFavorite={toggleFavorite} />
			{/each}
		{/if}

		<!-- Empty state -->
		{#if !loading && subfolders.length === 0 && visibleModels.length === 0}
			<div class="p-3 text-center text-fg-subtle text-xs">
				{#if searching}
					No models in your collections match “{search.trim()}”.
				{:else if currentId === null}
					No collections yet.
				{:else}
					This collection is empty.
				{/if}
			</div>
		{/if}
	</div>
</div>
