<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	type DetailTabDescriptor = { id: string; label: string; icon?: string; count?: number };

	let {
		tabs,
		active,
		onSelect,
		ariaLabel = 'Detail tabs'
	}: {
		tabs: DetailTabDescriptor[];
		active: string;
		onSelect: (id: string) => void;
		ariaLabel?: string;
	} = $props();
</script>

<div class="px-4 sm:px-5 py-1.5 border-b border-line bg-surface-1 flex-shrink-0 overflow-x-auto">
	<nav class="inline-flex items-center gap-1" aria-label={ariaLabel}>
		{#each tabs as tab (tab.id)}
			<button
				type="button"
				class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-colors {active ===
				tab.id
					? 'bg-signal/10 text-signal'
					: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
				onclick={() => onSelect(tab.id)}
				aria-current={active === tab.id ? 'page' : undefined}
			>
				{#if tab.icon}<Icon name={tab.icon} className="w-3.5 h-3.5" />{/if}
				{tab.label}
				{#if tab.count !== undefined}<span class="font-mono text-2xs opacity-70">{tab.count}</span>{/if}
			</button>
		{/each}
	</nav>
</div>
