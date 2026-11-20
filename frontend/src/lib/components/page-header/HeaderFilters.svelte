<script module lang="ts">
	export interface FilterChip {
		id: string;
		label: string;
		onremove: () => void;
	}
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import dismiss from '$lib/actions/dismiss';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import tooltip from '$lib/actions/tooltip';
	import Icon from '$lib/components/Icon.svelte';
	import { computeAnchoredMenuPosition } from '$lib/utils/menuPosition';

	let {
		activeCount,
		chips = [],
		onClear,
		open = $bindable(false),
		iconOnly = false,
		bare = false,
		label = 'Filters',
		children,
		footerAction
	}: {
		activeCount: number;
		chips?: FilterChip[];
		onClear: () => void;
		open?: boolean;
		iconOnly?: boolean;
		bare?: boolean;
		label?: string;
		children: Snippet<[() => void]>;
		footerAction?: Snippet<[() => void]>;
	} = $props();

	let rootEl: HTMLDivElement | undefined = $state();
	let buttonEl: HTMLButtonElement | undefined = $state();
	let panelEl: HTMLDivElement | undefined = $state();
	let position = $state({ top: 0, left: 0 });

	function updatePosition() {
		if (!buttonEl) return;
		const width = panelEl?.getBoundingClientRect().width ?? 352;
		position = computeAnchoredMenuPosition(buttonEl, { width });
	}

	function close() {
		open = false;
	}

	function toggle() {
		open = !open;
	}

	$effect(() => {
		if (!open) return;
		updatePosition();
		const frame = requestAnimationFrame(updatePosition);
		return () => cancelAnimationFrame(frame);
	});
</script>

<div class="relative flex-none" bind:this={rootEl} data-header-filters data-filters-trigger>
	<button
		bind:this={buttonEl}
		type="button"
		class="inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded border px-2.5 text-sm transition-colors {activeCount > 0
			? 'border-signal/25 bg-signal/10 text-signal'
			: 'border-line-strong bg-surface-2/50 text-fg hover:bg-surface-3/60'}"
		aria-haspopup="dialog"
		aria-expanded={open}
		aria-label={iconOnly ? label : undefined}
		use:tooltip={iconOnly ? label : null}
		onclick={toggle}
	>
		<Icon name="sliders-horizontal" className="h-3.5 w-3.5" />
		{#if !iconOnly}{label}{/if}
		{#if activeCount > 0}
			<span class="font-mono text-sm font-semibold tabular-nums" data-filters-count>{activeCount}</span>
		{/if}
		{#if !iconOnly}
			<Icon name="chevron-down" className="h-3 w-3 text-fg-subtle" />
		{/if}
	</button>
	{#if open}
		<div
			use:portal
			use:overlayLayer
			bind:this={panelEl}
			use:dismiss={{ onDismiss: close, ignore: [rootEl] }}
			class={bare
				? 'fixed z-overlay'
				: 'fixed z-overlay w-[22rem] max-w-[90vw] rounded-xl border border-line-strong bg-surface-2 p-3 shadow-floating'}
			style="top: {position.top}px; left: {position.left}px;"
			data-filters-popover
			role={bare ? undefined : 'dialog'}
			aria-label={bare ? undefined : label}
		>
			{#if bare}
				{@render children(close)}
			{:else}
			{#if chips.length > 0}
				<div class="mb-3 border-b border-line-strong/70 pb-3">
					<div class="mb-1.5 flex items-center justify-between">
						<span class="text-sm uppercase tracking-[0.07em] text-fg-subtle">Active</span>
						<button
							type="button"
							class="flex items-center gap-1 text-sm text-fg-muted transition-colors hover:text-fg"
							onclick={onClear}
						>
							<Icon name="close" className="h-3 w-3" />
							Clear all
						</button>
					</div>
					<div class="flex flex-wrap gap-1.5">
						{#each chips as chip (chip.id)}
							<span
								class="inline-flex h-6 max-w-full items-center gap-1 rounded border border-signal/25 bg-signal/10 pl-2 pr-1 text-sm text-signal"
							>
								<span class="truncate">{chip.label}</span>
								<button
									type="button"
									class="inline-flex h-4 w-4 flex-none items-center justify-center rounded-sm hover:bg-signal/10"
									aria-label="Remove {chip.label}"
									onclick={chip.onremove}
								>
									<Icon name="close" className="h-3 w-3" />
								</button>
							</span>
						{/each}
					</div>
				</div>
			{/if}
			<div class="grid grid-cols-2 gap-2.5">
				{@render children(close)}
			</div>
			{#if footerAction}
				<div class="mt-3 flex items-center border-t border-line-strong/70 pt-2.5">
					{@render footerAction(close)}
				</div>
			{/if}
			{/if}
		</div>
	{/if}
</div>
