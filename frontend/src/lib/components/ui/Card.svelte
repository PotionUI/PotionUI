<script lang="ts">
	import type { Snippet } from 'svelte';

	type Padding = 'none' | 'sm' | 'md';

	let {
		padding = 'md',
		interactive = false,
		as = 'div',
		class: className = '',
		onclick,
		children
	}: {
		padding?: Padding;
		interactive?: boolean;
		as?: string;
		class?: string;
		onclick?: (e: MouseEvent) => void;
		children?: Snippet;
	} = $props();

	const paddingClasses: Record<Padding, string> = {
		none: '',
		sm: 'p-3',
		md: 'p-4'
	};

	let classes = $derived(
		`bg-surface-1 border border-line rounded-lg shadow-raised ${paddingClasses[padding]} ${
			interactive
				? 'hover:border-line-strong hover:bg-surface-2 transition-colors duration-100 cursor-pointer'
				: ''
		} ${className}`
	);
</script>

<svelte:element
	this={as}
	class={classes}
	{onclick}
	onkeydown={onclick
		? (e: KeyboardEvent) => {
				if (e.key === 'Enter' || e.key === ' ') {
					e.preventDefault();
					onclick(e as unknown as MouseEvent);
				}
			}
		: undefined}
	role={onclick ? 'button' : undefined}
	tabindex={onclick ? 0 : undefined}
>
	{@render children?.()}
</svelte:element>
