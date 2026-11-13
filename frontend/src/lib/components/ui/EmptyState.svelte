<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '../Icon.svelte';

	let {
		title,
		description,
		icon = 'image',
		tone = 'default',
		compact = false,
		actions
	}: {
		title: string;
		description?: string;
		icon?: string;
		tone?: 'default' | 'danger';
		compact?: boolean;
		actions?: Snippet;
	} = $props();
</script>

<div class="dot-grid text-center rounded-lg {compact ? 'px-4 py-8' : 'px-6 py-20'}" data-empty-state={compact ? 'compact' : 'default'}>
	<div class="max-w-md mx-auto">
		<div
			class="flex items-center justify-center mx-auto rounded-lg bg-surface-2 border border-line {compact ? 'w-12 h-12 mb-4' : 'w-16 h-16 mb-6'}"
		>
			<Icon
				name={icon}
				className="{compact ? 'h-6 w-6' : 'h-8 w-8'} {tone === 'danger' ? 'text-danger' : 'text-fg-subtle'}"
				strokeWidth={1.5}
			/>
		</div>
		<p class="font-mono text-2xs uppercase tracking-[0.07em] {tone === 'danger' ? 'text-danger' : 'text-fg-subtle'}" data-empty-state-title>{title}</p>
		{#if description}
			<p class="text-fg-muted leading-relaxed text-sm break-words {compact ? 'mt-2' : 'mt-3'}" data-empty-state-description>{description}</p>
		{/if}
		{#if actions}
			<div class="flex flex-wrap items-center justify-center gap-3 {compact ? 'mt-4' : 'mt-8'}">{@render actions()}</div>
		{/if}
	</div>
</div>
