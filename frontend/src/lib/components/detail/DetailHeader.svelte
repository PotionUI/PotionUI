<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	let {
		title,
		icon,
		chips,
		subtitle,
		actions
	}: {
		title: string;
		icon?: string;
		chips?: Snippet;
		subtitle?: Snippet;
		actions?: Snippet;
	} = $props();
</script>

<div class="flex items-center gap-3 px-4 sm:px-5 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
	{#if icon}
		<span
			class="flex-shrink-0 flex items-center justify-center w-[30px] h-[30px] rounded bg-surface-2 border border-line-strong text-fg-subtle"
		>
			<Icon name={icon} className="w-4 h-4" />
		</span>
	{/if}
	<div class="min-w-0 flex-1">
		<div class="flex items-center gap-2 min-w-0 flex-wrap">
			<h2 class="text-base font-semibold text-fg truncate">{title}</h2>
			{@render chips?.()}
		</div>
		{#if subtitle}
			<div class="mt-0.5 font-mono text-2xs text-fg-subtle flex items-center gap-1.5">
				{@render subtitle()}
			</div>
		{/if}
	</div>
	{#if actions}
		<div class="ml-auto flex items-center gap-2 flex-shrink-0 flex-wrap justify-end">
			{@render actions()}
		</div>
	{/if}
</div>
