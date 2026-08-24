<script lang="ts">
	// Favorite (heart) toggle. Filled signal heart when active, outline otherwise.
	export let active: boolean = false;
	export let size: 'sm' | 'md' | 'lg' = 'sm';
	// 'default' sits on a surface, 'onMedia' sits over imagery (needs white inactive).
	export let tone: 'default' | 'onMedia' = 'default';
	export let onToggle: (() => void) | undefined = undefined;

	const HEART_PATH =
		'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z';

	$: dim = size === 'sm' ? 'w-3.5 h-3.5' : size === 'lg' ? 'w-6 h-6' : 'w-5 h-5';
	$: inactiveClass = tone === 'onMedia' ? 'text-white hover:text-white' : 'text-fg-subtle hover:text-fg';

	function toggle(event: Event) {
		event.stopPropagation();
		event.preventDefault();
		onToggle?.();
	}
</script>

<button
	type="button"
	aria-pressed={active}
	aria-label={active ? 'Remove from favorites' : 'Add to favorites'}
	title={active ? 'Remove from favorites' : 'Add to favorites'}
	class="transition-colors duration-100 {active ? 'text-signal' : inactiveClass}"
	on:click={toggle}
>
	<svg
		class={dim}
		viewBox="0 0 24 24"
		fill={active ? 'currentColor' : 'none'}
		stroke="currentColor"
		stroke-width="2"
	>
		<path stroke-linecap="round" stroke-linejoin="round" d={HEART_PATH} />
	</svg>
</button>
