<script lang="ts">
	// Compact 1-5 star rating widget. Filled stars use the signal token (state);
	// empty stars are text-fg-subtle. Clicking the current rating clears it (→ 0).
	export let value: number = 0;
	export let size: 'sm' | 'md' = 'sm';
	export let readonly: boolean = false;
	// 'default' sits on a surface, 'onMedia' sits over imagery (needs white empties).
	export let tone: 'default' | 'onMedia' = 'default';
	export let onChange: ((rating: number) => void) | undefined = undefined;

	const STAR_PATH =
		'M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z';

	const stars = [1, 2, 3, 4, 5];
	let hovered = 0;

	$: dim = size === 'sm' ? 'w-3.5 h-3.5' : 'w-5 h-5';
	$: active = hovered || value;
	$: emptyClass = tone === 'onMedia' ? 'text-white/40' : 'text-fg-subtle';

	function pick(n: number, event: Event) {
		event.stopPropagation();
		event.preventDefault();
		if (readonly) return;
		onChange?.(value === n ? 0 : n);
	}
</script>

<div class="flex items-center gap-0.5" role="radiogroup" aria-label="Rating">
	{#each stars as n (n)}
		<button
			type="button"
			role="radio"
			aria-checked={value === n}
			aria-label={`Rate ${n} star${n === 1 ? '' : 's'}`}
			class="transition-colors duration-100 {n <= active ? 'text-signal' : emptyClass} {readonly
				? ''
				: 'hover:text-signal cursor-pointer'}"
			disabled={readonly}
			on:click={(e) => pick(n, e)}
			on:mouseenter={() => !readonly && (hovered = n)}
			on:mouseleave={() => !readonly && (hovered = 0)}
		>
			<svg
				class={dim}
				viewBox="0 0 24 24"
				fill={n <= active ? 'currentColor' : 'none'}
				stroke="currentColor"
				stroke-width="1.5"
			>
				<path stroke-linecap="round" stroke-linejoin="round" d={STAR_PATH} />
			</svg>
		</button>
	{/each}
</div>
