<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	/**
	 * Generic view-switcher nav for "which view am I looking at" navigation.
	 * Not for search/filter toggles — those stay bespoke in a toolbar. Renders
	 * as a labelled `nav` of buttons with `aria-current`.
	 */
	type Item = {
		id: string;
		label: string;
		icon?: string;
		count?: number;
	};

	let {
		items,
		selected,
		onSelect,
		ariaLabel = 'View switcher'
	}: {
		items: Item[];
		selected: string;
		onSelect: (id: string) => void;
		ariaLabel?: string;
	} = $props();
</script>

<nav class="inline-flex items-center gap-1" aria-label={ariaLabel}>
	{#each items as item (item.id)}
		<button
			type="button"
			class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition-colors {item.id ===
			selected
				? 'bg-signal/10 text-signal'
				: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
			onclick={() => onSelect(item.id)}
			aria-current={item.id === selected ? 'page' : undefined}
		>
			{#if item.icon}
				<Icon name={item.icon} className="w-3.5 h-3.5" />
			{/if}
			{item.label}
			{#if item.count !== undefined}
				<span class="font-mono text-2xs opacity-70">{item.count}</span>
			{/if}
		</button>
	{/each}
</nav>
