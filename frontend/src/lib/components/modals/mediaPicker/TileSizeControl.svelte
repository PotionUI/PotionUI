<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { historyTileSize, type HistoryTileSize } from '$lib/stores/historyTileSize';

	let { class: className = '' }: { class?: string } = $props();

	const sizes: Array<{ value: HistoryTileSize; label: string; tip: string }> = [
		{ value: 'small', label: 'S', tip: 'Small tiles' },
		{ value: 'medium', label: 'M', tip: 'Medium tiles' },
		{ value: 'large', label: 'L', tip: 'Large tiles' }
	];
</script>

<div
	class="flex flex-shrink-0 items-center gap-0.5 rounded bg-surface-2/50 p-0.5 {className}"
	role="radiogroup"
	aria-label="Tile size"
	data-tile-size
>
	{#each sizes as size (size.value)}
		<button
			type="button"
			role="radio"
			aria-label={size.tip}
			aria-checked={$historyTileSize === size.value}
			use:tooltip={size.tip}
			data-size={size.value}
			class="h-7 w-7 rounded-sm font-mono text-sm transition-colors duration-100 {$historyTileSize === size.value
				? 'bg-signal/10 text-signal'
				: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
			onclick={() => historyTileSize.set(size.value)}
		>
			{size.label}
		</button>
	{/each}
</div>
