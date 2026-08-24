<script lang="ts">
	import type { Snippet } from 'svelte';
	import Logo from '$lib/components/brand/Logo.svelte';

	let {
		heading,
		subtitle,
		children
	}: {
		heading: string;
		subtitle?: Snippet;
		children?: Snippet;
	} = $props();
</script>

<div
	class="relative flex min-h-screen items-center justify-center overflow-hidden bg-canvas px-4 py-10"
>
	<div
		class="dot-grid pointer-events-none absolute inset-0"
		style="mask-image: radial-gradient(min(520px, 92vw) min(520px, 92vw) at 50% 46%, black 20%, transparent 78%); -webkit-mask-image: radial-gradient(min(520px, 92vw) min(520px, 92vw) at 50% 46%, black 20%, transparent 78%);"
		aria-hidden="true"
	></div>

	<div class="relative flex w-full max-w-[340px] flex-col text-left">
		<Logo size={30} />

		<h1 class="mt-6 text-[28px] font-semibold leading-[34px] tracking-[-0.02em] text-fg">
			{heading}
		</h1>

		{#if subtitle}
			<p class="mt-1.5 text-base text-fg-subtle">
				{@render subtitle()}
			</p>
		{/if}

		{@render children?.()}
	</div>
</div>
