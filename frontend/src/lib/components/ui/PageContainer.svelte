<script lang="ts">
	import type { Snippet } from 'svelte';

	type Width = 'sm' | 'md' | 'lg' | 'full';

	let {
		width = 'lg',
		class: className = '',
		children
	}: { width?: Width; class?: string; children?: Snippet } = $props();

	const widths: Record<Width, string> = {
		sm: 'max-w-2xl',
		md: 'max-w-5xl',
		lg: 'max-w-[1600px]',
		full: 'max-w-none'
	};

	let classes = $derived(`w-full mx-auto px-4 sm:px-6 py-4 sm:py-6 ${widths[width]} ${className}`);
</script>

<main class={classes}>
	{@render children?.()}
</main>
