<script lang="ts">
	import { libraryCardDensity, type LibraryCardDensity } from './libraryCardDensity';

	const options: readonly { value: LibraryCardDensity; label: string }[] = [
		{ value: 'comfortable', label: 'Comfortable' },
		{ value: 'compact', label: 'Compact' }
	];
</script>

<div
	class="flex flex-shrink-0 items-center gap-0.5 rounded bg-surface-2 p-0.5"
	role="radiogroup"
	aria-label="Card density"
>
	{#each options as option (option.value)}
		<button
			type="button"
			role="radio"
			aria-checked={$libraryCardDensity === option.value}
			class="rounded px-2 py-1 text-xs font-medium transition-colors {$libraryCardDensity === option.value
				? 'bg-signal/10 text-signal'
				: 'text-fg-muted hover:bg-surface-3 hover:text-fg'}"
			onclick={() => libraryCardDensity.set(option.value)}
		>
			{option.label}
		</button>
	{/each}
</div>
