<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	/**
	 * Two flavors sharing one item/selection contract:
	 * - `nav` (default): "which view am I looking at" navigation. Not for
	 *   search/filter toggles — those stay bespoke in a toolbar. Renders as a
	 *   labelled `nav` of buttons with `aria-current`.
	 * - `toggle`: a real segmented control for choosing between two or more
	 *   mutually exclusive options (e.g. Fixed value / Auto-shuffle). Renders
	 *   as a single bordered container with `role="group"` and `aria-pressed`
	 *   on each segment.
	 */
	type Item = {
		id: string;
		label: string;
		icon?: string;
		count?: number;
		disabled?: boolean;
	};

	let {
		items,
		selected,
		onSelect,
		ariaLabel = 'View switcher',
		variant = 'nav'
	}: {
		items: Item[];
		selected: string;
		onSelect: (id: string) => void;
		ariaLabel?: string;
		variant?: 'nav' | 'toggle';
	} = $props();
</script>

{#if variant === 'toggle'}
	<div class="inline-flex items-center gap-0.5 rounded border border-line bg-surface-2 p-0.5" role="group" aria-label={ariaLabel}>
		{#each items as item (item.id)}
			<button
				type="button"
				class="rounded px-2.5 py-1 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 {item.id ===
				selected
					? 'bg-surface-3 text-fg'
					: 'text-fg-muted hover:text-fg'}"
				aria-pressed={item.id === selected}
				disabled={item.disabled}
				onclick={() => onSelect(item.id)}
			>
				{item.label}
			</button>
		{/each}
	</div>
{:else}
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
{/if}
