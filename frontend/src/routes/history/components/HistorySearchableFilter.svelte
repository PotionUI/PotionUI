<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Spinner } from '$lib/components/ui';

	interface Option {
		id: string;
		label: string;
		sublabel?: string;
	}

	let {
		placeholder = 'Filter',
		icon = 'search',
		selectedId = undefined,
		selectedLabel = undefined,
		search,
		onSelect
	}: {
		placeholder?: string;
		icon?: string;
		selectedId?: string;
		selectedLabel?: string;
		search: (query: string) => Promise<Option[]>;
		onSelect: (id: string | null, label: string | null) => void;
	} = $props();

	let open = $state(false);
	let query = $state('');
	let loading = $state(false);
	let options = $state<Option[]>([]);
	let debounce: ReturnType<typeof setTimeout> | undefined;
	let rootEl: HTMLDivElement;

	async function runSearch() {
		loading = true;
		try {
			options = await search(query.trim());
		} catch {
			options = [];
		} finally {
			loading = false;
		}
	}

	function handleInput(event: Event) {
		query = (event.currentTarget as HTMLInputElement).value;
		clearTimeout(debounce);
		debounce = setTimeout(runSearch, 250);
	}

	function toggleOpen() {
		open = !open;
		if (open) runSearch();
	}

	function choose(option: Option) {
		onSelect(option.id, option.label);
		open = false;
		query = '';
	}

	function clear() {
		onSelect(null, null);
		open = false;
		query = '';
	}

	function handleWindowClick(event: MouseEvent) {
		if (rootEl && !rootEl.contains(event.target as Node)) {
			open = false;
		}
	}
</script>

<svelte:window on:click={handleWindowClick} />

<div class="relative" bind:this={rootEl}>
	<button
		class="input text-xs py-1.5 px-2 bg-surface-2/50 w-auto max-w-[12rem] flex items-center gap-1.5 {selectedId
			? 'text-signal'
			: 'text-fg-muted'}"
		onclick={toggleOpen}
		title={selectedLabel ?? placeholder}
	>
		<Icon name={icon} className="w-3.5 h-3.5 flex-shrink-0" />
		<span class="truncate">{selectedLabel ?? placeholder}</span>
		{#if selectedId}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<span
				class="ml-0.5 flex-shrink-0 hover:text-fg"
				role="button"
				tabindex="0"
				aria-label="Clear filter"
				onclick={(e) => {
					e.stopPropagation();
					clear();
				}}
				onkeydown={(e) => {
					if (e.key === 'Enter' || e.key === ' ') {
						e.preventDefault();
						e.stopPropagation();
						clear();
					}
				}}
			>
				<Icon name="close" className="w-3 h-3" />
			</span>
		{/if}
	</button>

	{#if open}
		<div
			class="absolute left-0 top-full mt-1 w-72 max-w-[80vw] bg-surface-2 rounded-xl border border-line-strong shadow-floating z-50 p-2"
		>
			<div class="relative mb-2">
				<Icon
					name="search"
					className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
				/>
				<!-- svelte-ignore a11y_autofocus -->
				<input
					type="text"
					class="input text-xs py-1.5 pl-8 pr-3 w-full"
					{placeholder}
					value={query}
					oninput={handleInput}
					autofocus
				/>
			</div>

			<div class="max-h-64 overflow-y-auto">
				{#if loading}
					<div class="flex items-center justify-center py-6">
						<Spinner size="sm" />
					</div>
				{:else if options.length === 0}
					<p class="text-xs text-fg-subtle text-center py-6">No matches</p>
				{:else}
					<ul class="space-y-0.5">
						{#each options as option (option.id)}
							<li>
								<button
									class="w-full text-left px-2 py-1.5 rounded text-xs transition-colors {option.id ===
									selectedId
										? 'bg-signal/10 text-signal'
										: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
									onclick={() => choose(option)}
								>
									<span class="block truncate">{option.label}</span>
									{#if option.sublabel}
										<span class="block truncate text-2xs text-fg-subtle font-mono">{option.sublabel}</span>
									{/if}
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</div>
	{/if}
</div>
