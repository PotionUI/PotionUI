<script lang="ts">
	import type { Snippet } from 'svelte';
	import { kvItemValueClass, kvItemWrapperClass } from './kvItem';

	let {
		label,
		mono = false,
		full = false,
		children
	}: {
		label: string;
		mono?: boolean;
		full?: boolean;
		children?: Snippet;
	} = $props();
</script>

<div class={kvItemWrapperClass(full)}>
	<span class="block font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1">{label}</span>
	<div class={kvItemValueClass(mono)}>
		{@render children?.()}
	</div>
</div>
