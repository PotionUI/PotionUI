<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	/**
	 * Canonical admin page header: every admin page gets exactly
	 * two chrome rows — this header, then `AdminFilterBar` — both shared
	 * components, so no two pages hand-roll their own bar anymore.
	 *
	 * This is the Presets compact bar, generalized: icon + page name + a
	 * `counts` strip ("N presets | N installed", mono, pipe-separated) +
	 * right-aligned global actions.
	 * It renders ONE row (it may still wrap on very narrow viewports — the
	 * no-wrap guarantee is `AdminFilterBar`'s job, not this one's) and is a
	 * sibling to the page's content, not a wrapper around it: callers own their
	 * own root layout (bounded flex column, plain `space-y-4`, whatever their
	 * content zone needs) and place `<AdminTabShell>` then `<AdminFilterBar>`
	 * then content inside it.
	 */
	type CountTone = 'muted' | 'success' | 'info';
	type CountItem = { label: string; value: number | string; tone?: CountTone };

	let {
		title,
		icon,
		counts = [],
		actions
	}: {
		title: string;
		icon?: string;
		counts?: CountItem[];
		actions?: Snippet;
	} = $props();

	const toneClass: Record<CountTone, string> = {
		muted: 'text-fg-muted',
		success: 'text-success',
		info: 'text-info'
	};
</script>

<section class="bg-surface-1/50 rounded-lg border border-line px-4 min-h-12 py-2 flex flex-wrap items-center gap-3">
	<div class="flex items-center gap-2 min-w-0 flex-shrink-0">
		{#if icon}
			<Icon name={icon} className="w-4 h-4 text-fg-muted flex-shrink-0" />
		{/if}
		<span class="text-sm font-medium text-fg truncate" title={title}>{title}</span>
	</div>

	{#if counts.length}
		<div class="h-5 w-px bg-line-strong flex-shrink-0"></div>
		{#each counts as count, i (count.label)}
			{#if i > 0}<span class="text-xs text-fg-disabled flex-shrink-0">|</span>{/if}
			<span class="text-xs font-mono tabular-nums whitespace-nowrap flex-shrink-0 {toneClass[count.tone ?? 'muted']}">
				{count.value} {count.label}
			</span>
		{/each}
	{/if}

	{#if actions}
		<div class="ml-auto flex items-center gap-2 flex-shrink-0">
			{@render actions()}
		</div>
	{/if}
</section>
