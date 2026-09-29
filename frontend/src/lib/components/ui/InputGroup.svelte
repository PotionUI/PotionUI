<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		invalid = false,
		class: className = '',
		leading,
		children,
		trailing
	}: {
		invalid?: boolean;
		class?: string;
		leading?: Snippet;
		children: Snippet;
		trailing?: Snippet;
	} = $props();
</script>

<div
	class="input flex min-w-0 items-center gap-2 {invalid ? 'border-danger focus-within:ring-danger' : ''} {className}"
>
	{@render leading?.()}
	{@render children()}
	{@render trailing?.()}
</div>
