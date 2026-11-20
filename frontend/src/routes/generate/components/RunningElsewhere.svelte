<script lang="ts">
	import { Button } from '$lib/components/ui';
	import Menu from '$lib/components/ui/Menu.svelte';
	import StudioSheet from '$lib/components/StudioSheet.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { runningElsewhere } from '$lib/generation/elsewhere/store';
	import { formatElapsed, type ElsewhereItem } from '$lib/generation/elsewhere/select';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';

	let { mobile = false }: { mobile?: boolean } = $props();

	let open = $state(false);
	let busyId = $state<string | null>(null);
	let now = $state(Date.now());

	let total = $derived($runningElsewhere.total);
	let items = $derived($runningElsewhere.items);
	let triggerLabel = $derived(`${total} running over MCP`);

	$effect(() => {
		if (!open) return;
		now = Date.now();
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});

	$effect(() => {
		if (total === 0 && open) open = false;
	});

	function sourceOf(item: ElsewhereItem): string {
		return item.origin?.label ?? 'Started over MCP';
	}

	function stateOf(item: ElsewhereItem): string {
		if (item.status === 'pending') {
			return item.queuePosition === null ? 'Waiting' : `Waiting, #${item.queuePosition + 1}`;
		}
		if (item.progress === null) return 'Running';
		return `Running, ${Math.round(item.progress <= 1 ? item.progress * 100 : item.progress)}%`;
	}

	async function openOne(id: string, close: () => void) {
		if (busyId) return;
		busyId = id;
		try {
			const { openGenerationInNewTab } = await import('$lib/generation/attach/openInTabDefault');
			const result = await openGenerationInNewTab(id);
			if (result.kind === 'opened' || result.kind === 'focused') close();
		} finally {
			busyId = null;
		}
	}
</script>

{#snippet list(close: () => void)}
	<div class="px-2.5 pb-1 pt-1.5 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Started over MCP</div>
	<ul class="flex flex-col" data-testid="running-elsewhere-list">
		{#each items as item (item.generationId)}
			<li class="flex flex-col gap-1 rounded px-2.5 py-2" data-elsewhere-row={item.generationId}>
				<div class="flex items-center gap-2">
					<span class="min-w-0 flex-1 truncate text-sm font-medium text-fg">
						{item.presetName ?? 'Generation'}{#if item.mode}<span class="font-normal text-fg-muted"> · {modeLabel(item.mode)}</span>{/if}
					</span>
					<Button
						size="xs"
						variant="secondary"
						loading={busyId === item.generationId}
						disabled={busyId !== null && busyId !== item.generationId}
						onclick={() => openOne(item.generationId, close)}
						attrs={{ 'data-elsewhere-open': item.generationId }}
					>
						Open in a tab
					</Button>
				</div>
				<div class="flex min-w-0 items-center gap-2 text-sm text-fg-muted">
					{#if item.origin}<Icon name={item.origin.icon} className="h-3.5 w-3.5 flex-none" />{/if}
					<span class="min-w-0 flex-1 truncate">{sourceOf(item)}</span>
				</div>
				<div class="flex items-center gap-2 text-sm text-fg-muted">
					<span class={item.status === 'running' ? 'text-signal' : ''} data-elsewhere-state>{stateOf(item)}</span>
					{#if item.startedAtMs !== null}
						<span class="text-fg-subtle">·</span>
						<span class="font-mono tabular-nums text-fg-subtle" data-elsewhere-elapsed>{formatElapsed(item.startedAtMs, now)}</span>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
	{#if total > items.length}
		<div class="px-2.5 py-1.5 text-sm text-fg-subtle" data-elsewhere-more>and {total - items.length} more</div>
	{/if}
{/snippet}

{#if total > 0}
	{#if mobile}
		<button
			type="button"
			class="pointer-events-auto flex h-8 flex-shrink-0 items-center gap-1.5 rounded border border-line-strong bg-surface-1/85 px-2 text-sm font-medium text-fg backdrop-blur-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal"
			aria-label={triggerLabel}
			aria-haspopup="dialog"
			aria-expanded={open}
			data-testid="running-elsewhere"
			onclick={() => (open = !open)}
		>
			<Icon name="server" className="h-3.5 w-3.5" />
			<span class="font-mono tabular-nums">{total}</span>
		</button>
		{#if open}
			<StudioSheet ariaLabel="Started over MCP" on:close={() => (open = false)}>
				<div class="pb-4">
					{@render list(() => (open = false))}
				</div>
			</StudioSheet>
		{/if}
	{:else}
		<Menu label="Started over MCP" menuLabel="Started over MCP" width={400} align="right" bind:open>
			{#snippet trigger({ open: isOpen, toggle })}
				<Button
					variant="secondary"
					size="xs"
					icon="server"
					ariaExpanded={isOpen}
					ariaHaspopup="menu"
					pressed={isOpen}
					onclick={toggle}
					attrs={{ 'data-testid': 'running-elsewhere' }}
				>
					{triggerLabel}
				</Button>
			{/snippet}
			{#snippet children({ close })}
				{@render list(() => close(true))}
			{/snippet}
		</Menu>
	{/if}
{/if}
