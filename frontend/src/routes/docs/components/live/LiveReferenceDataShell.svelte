<script lang="ts" generics="T">
	import { onMount } from 'svelte';
	import type { Snippet } from 'svelte';
	import { Spinner, Input } from '$lib/components/ui';
	import { logger } from '$lib/utils/logger';

	let {
		load,
		filter,
		label,
		emptyText,
		content
	}: {
		/** Resolves to the full item list, or throws with a user-facing message on failure. */
		load: () => Promise<T[]>;
		/** Called only for a non-empty query - the shell already short-circuits an empty one. */
		filter: (item: T, query: string) => boolean;
		/** Plural noun driving the generated copy: "No {label} registered.", "Filter {label}...". */
		label: string;
		emptyText?: string;
		content: Snippet<[{ items: T[] }]>;
	} = $props();

	let items = $state<T[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let filterText = $state('');

	let filteredItems = $derived(
		filterText ? items.filter((item) => filter(item, filterText)) : items
	);

	onMount(async () => {
		try {
			items = await load();
		} catch (err) {
			logger.error(`Failed to load ${label}:`, err);
			error = err instanceof Error ? err.message : `Failed to load ${label}`;
		} finally {
			loading = false;
		}
	});
</script>

{#if loading}
	<div class="flex items-center justify-center py-12"><Spinner size="lg" /></div>
{:else if error}
	<div class="text-sm text-danger py-4">{error}</div>
{:else if items.length === 0}
	<p class="text-sm text-fg-subtle">{emptyText ?? `No ${label} registered.`}</p>
{:else}
	<div class="mb-4">
		<Input
			type="search"
			bind:value={filterText}
			placeholder="Filter {label}..."
			aria-label="Filter {label}"
		/>
	</div>

	{#if filteredItems.length === 0}
		<p class="text-sm text-fg-subtle">No {label} match "{filterText}".</p>
	{:else}
		{@render content({ items: filteredItems })}
	{/if}
{/if}
