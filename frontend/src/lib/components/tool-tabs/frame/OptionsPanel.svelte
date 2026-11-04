<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, IconButton } from '$lib/components/ui';

	let {
		title,
		icon,
		subtitle = null,
		collapsed = $bindable(false),
		applyLabel = 'Apply',
		canApply = true,
		canReset = true,
		phone = false,
		actions = true,
		onapply,
		onreset,
		children
	}: {
		title: string;
		icon: string;
		subtitle?: string | null;
		collapsed?: boolean;
		applyLabel?: string;
		canApply?: boolean;
		canReset?: boolean;
		phone?: boolean;
		actions?: boolean;
		onapply: () => void;
		onreset: () => void;
		children?: Snippet;
	} = $props();
</script>

{#if phone}
	<section class="flex flex-none flex-col border-t border-line-strong bg-surface-1 shadow-[var(--shadow-dock)]" aria-label="{title} options" data-options-panel="phone">
		<div class="flex items-center gap-2 px-3 pb-2 pt-2.5">
			<Icon name={icon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
			<h2 class="min-w-0 flex-1 truncate text-base font-semibold text-fg">{title}</h2>
			{#if actions}
				<Button variant="secondary" size="sm" class="h-7" disabled={!canReset} onclick={onreset} attrs={{ 'data-options-reset': '' }}>Reset</Button>
				<Button variant="primary" size="sm" class="h-7" disabled={!canApply} onclick={onapply} attrs={{ 'data-options-apply': '' }}>Apply</Button>
			{/if}
		</div>
		{#if children}
			<div class="flex flex-col gap-3 px-3 pb-3">
				{@render children()}
			</div>
		{/if}
	</section>
{:else}
	<section class="flex min-h-0 flex-col {collapsed ? 'flex-none' : 'flex-1'}" aria-label="{title} options" data-options-panel="desktop" data-collapsed={collapsed || undefined}>
		<div class="flex h-11 flex-none items-center gap-2 pl-4 pr-2">
			<Icon name={icon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
			<h2 class="min-w-0 flex-1 truncate text-base font-semibold text-fg">{title}{#if subtitle}<span class="ml-2 font-mono text-sm font-normal text-fg-subtle" data-options-subtitle>{subtitle}</span>{/if}</h2>
			<IconButton
				icon={collapsed ? 'chevron-right' : 'chevron-down'}
				label={collapsed ? 'Expand tool options' : 'Collapse tool options'}
				size="sm"
				ariaExpanded={!collapsed}
				onclick={() => (collapsed = !collapsed)}
				data-options-collapse
			/>
		</div>
		{#if !collapsed}
			<div class="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4 pt-1 [&>*]:flex-none" data-options-body>
				{@render children?.()}
			</div>
			{#if actions}
				<div class="flex flex-none gap-2 border-t border-line px-4 py-3">
					<Button variant="secondary" size="sm" class="h-7 flex-1" disabled={!canReset} onclick={onreset} attrs={{ 'data-options-reset': '' }}>Reset</Button>
					<Button variant="primary" size="sm" class="h-7 flex-1" disabled={!canApply} onclick={onapply} attrs={{ 'data-options-apply': '' }}>
						{applyLabel}
						<kbd class="ml-0.5 rounded border border-accent-contrast/25 px-1 font-mono text-sm leading-4 text-accent-contrast/70" aria-hidden="true">↵</kbd>
					</Button>
				</div>
			{/if}
		{/if}
	</section>
{/if}
