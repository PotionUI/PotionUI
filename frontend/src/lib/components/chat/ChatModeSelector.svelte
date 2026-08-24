<script lang="ts">
	import type { ChatMode } from '$lib/types/chat';

	export let modes: ChatMode[] = [];
	export let selected: string;
	export let locked = false;
	export let onSelect: (id: string) => void;

	let open = false;

	$: selectedMode = modes.find((m) => m.id === selected);

	function choose(id: string) {
		open = false;
		if (id !== selected) onSelect(id);
	}
</script>

<div class="relative flex-shrink-0">
	<button
		type="button"
		title={locked
			? 'Mode is fixed for this conversation — start a new chat to change it'
			: 'Chat mode'}
		disabled={locked}
		class="flex items-center gap-1 px-1.5 py-1.5 text-xs rounded transition-colors {open
			? 'bg-surface-2 text-fg-muted'
			: locked
				? 'text-fg-subtle cursor-default'
				: 'text-fg-muted hover:text-fg-muted hover:bg-surface-2'}"
		on:click={() => (open = !open)}
	>
		{#if locked}
			<svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
			</svg>
		{:else}
			<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 4a2 2 0 114 0v1a1 1 0 001 1h3a1 1 0 011 1v3a1 1 0 01-1 1h-1a2 2 0 100 4h1a1 1 0 011 1v3a1 1 0 01-1 1h-3a1 1 0 01-1-1v-1a2 2 0 10-4 0v1a1 1 0 01-1 1H7a1 1 0 01-1-1v-3a1 1 0 00-1-1H4a2 2 0 110-4h1a1 1 0 001-1V7a1 1 0 011-1h3a1 1 0 001-1V4z" />
			</svg>
		{/if}
		<span class="max-w-[110px] truncate">{selectedMode?.name || selected}</span>
		{#if !locked}
			<svg class="w-2.5 h-2.5 flex-shrink-0 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
			</svg>
		{/if}
	</button>
	{#if open && !locked}
		<div class="fixed inset-0 z-30" role="button" tabindex="-1" aria-label="Close mode selector" on:click={() => (open = false)} on:keydown={(e) => { if (e.key === 'Escape') open = false; }}></div>
		<div class="absolute z-40 top-full left-0 mt-1 w-64 bg-surface-1 border border-line rounded-lg shadow-floating max-h-72 overflow-y-auto">
			{#each modes as mode}
				<button
					type="button"
					class="w-full px-3 py-2 text-left hover:bg-surface-2 transition-colors flex items-start gap-2 {mode.id === selected ? 'bg-surface-2' : ''}"
					on:click={() => choose(mode.id)}
				>
					<div class="flex-1 min-w-0">
						<div class="text-xs font-medium {mode.id === selected ? 'text-signal' : 'text-fg-muted'}">
							{mode.name}
						</div>
						{#if mode.description}
							<div class="text-[10px] text-fg-subtle mt-0.5 line-clamp-2">{mode.description}</div>
						{/if}
					</div>
					{#if mode.id === selected}
						<svg class="w-3.5 h-3.5 text-signal flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
						</svg>
					{/if}
				</button>
			{/each}
			{#if modes.length === 0}
				<div class="px-3 py-4 text-xs text-fg-subtle text-center">No modes available</div>
			{/if}
		</div>
	{/if}
</div>
