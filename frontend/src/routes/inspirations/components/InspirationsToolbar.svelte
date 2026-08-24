<script lang="ts">
	import { onDestroy } from 'svelte';
	import { inspirationsStore } from '$lib/stores/inspirations';
	import { PageHeader, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';

	$: state = $inspirationsStore;

	// Search matches title/description server-side, debounced - same pattern
	// as the Library toolbar.
	let searchDebounce: ReturnType<typeof setTimeout> | undefined;
	function handleSearchInput(event: Event) {
		const value = (event.currentTarget as HTMLInputElement).value;
		inspirationsStore.setFilter('search', value);
		clearTimeout(searchDebounce);
		searchDebounce = setTimeout(() => inspirationsStore.load(), 300);
	}

	onDestroy(() => clearTimeout(searchDebounce));

	async function toggleSaved() {
		inspirationsStore.setFilter('saved', !state.filters.saved);
		await inspirationsStore.load();
	}
</script>

<PageHeader wrap sticky={false}>
	<div class="flex items-center gap-2 md:gap-4 w-full">
		<!-- Left: title + count -->
		<div class="flex items-baseline gap-3 flex-shrink-0">
			<span class="text-sm font-semibold text-fg">Inspirations</span>
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
				placeholder="Search title or description..."
				value={state.filters.search}
				on:input={handleSearchInput}
			/>
		</div>

		<!-- Saved filter -->
		<button
			class="hidden md:flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded transition-colors duration-100 flex-shrink-0 {state
				.filters.saved
				? 'bg-signal/10 text-signal'
				: 'bg-surface-2/50 text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
			aria-pressed={state.filters.saved}
			on:click={toggleSaved}
		>
			<Icon name="save" className="w-3.5 h-3.5" />
			Saved
		</button>

		<div class="flex-1 hidden md:block"></div>

		<!-- Right: actions -->
		<div class="flex items-center gap-2 ml-auto md:ml-0 flex-shrink-0">
			<IconButton icon="refresh" label="Refresh" onclick={() => inspirationsStore.load()} />
		</div>
	</div>
</PageHeader>
