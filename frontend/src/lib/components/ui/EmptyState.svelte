<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '../Icon.svelte';

	let {
		title,
		description,
		icon = 'image',
		tone = 'default',
		compact = false,
		page = false,
		actions,
		extra
	}: {
		title: string;
		description?: string;
		icon?: string;
		tone?: 'default' | 'danger';
		compact?: boolean;
		page?: boolean;
		actions?: Snippet;
		extra?: Snippet;
	} = $props();
</script>

<div
	class="text-center {page ? 'dot-grid bg-canvas flex h-full flex-1 min-h-72 items-center justify-center px-6 py-10' : compact ? 'px-4 py-8' : 'px-6 py-14'}"
	data-empty-state={page ? 'page' : compact ? 'compact' : 'default'}
>
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
		<p class="font-mono text-xs uppercase tracking-[0.06em] {tone === 'danger' ? 'text-danger' : 'text-fg-subtle'}" data-empty-state-title>{title}</p>
		{#if description}
			<p class="text-fg-muted leading-relaxed text-sm break-words {compact ? 'mt-2' : 'mt-3'}" data-empty-state-description>{description}</p>
		{/if}
		{#if extra}
			<div class="text-sm text-fg-subtle {compact ? 'mt-2' : 'mt-3'}">{@render extra()}</div>
		{/if}
		{#if actions}
			<div class="flex flex-wrap items-center justify-center gap-3 {compact ? 'mt-4' : 'mt-8'}">{@render actions()}</div>
		{/if}
	</div>
</div>
