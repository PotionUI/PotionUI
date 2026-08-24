<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Button, Badge } from '$lib/components/ui';

	/**
	 * Shared admin filter row: the Presets filter card, generalized, with a hard
	 * guarantee — it never wraps to a second row, down to ~360px wide. Sibling to
	 * `AdminTabShell`, not nested in it.
	 *
	 * How the one-row guarantee holds: `search` is the only flexible-width
	 * piece (`flex-1 min-w-0`, free to shrink); every other piece is
	 * `flex-shrink-0`. Below the `lg` breakpoint the inline `filters` row is
	 * hidden outright and replaced by a fixed-size "Filters" button, so the
	 * row's total width at small viewports is just search + one button (+ a
	 * short `trailing` count) — that always fits at 360px. At `lg` and up
	 * there's room to show filters inline instead.
	 *
	 * `filters` is rendered TWICE — once inline (lg+), once inside the popover
	 * (below lg) — both calls bound to the same reactive state the caller
	 * closed over, so whichever copy is visible always reflects and drives the
	 * same values. This avoids requiring every adopter to author its filter
	 * controls twice in two different snippets.
	 */
	let {
		search,
		filters,
		trailing,
		activeCount = 0,
		onClear
	}: {
		search?: Snippet;
		filters?: Snippet;
		trailing?: Snippet;
		activeCount?: number;
		onClear?: () => void;
	} = $props();

	let popoverOpen = $state(false);
	let triggerEl: HTMLElement | undefined = $state();
	let popoverStyle = $state('');

	function togglePopover() {
		if (popoverOpen) {
			popoverOpen = false;
			return;
		}
		if (triggerEl) {
			const rect = triggerEl.getBoundingClientRect();
			const width = 300;
			let left = rect.right - width;
			if (left < 8) left = 8;
			if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
			popoverStyle = `top: ${rect.bottom + 6}px; left: ${left}px; width: ${width}px;`;
		}
		popoverOpen = true;
	}

	function handleWindowClick(e: MouseEvent) {
		if (!popoverOpen) return;
		const target = e.target as HTMLElement;
		if (target.closest('[data-admin-filter-popover]') || target.closest('[data-admin-filter-trigger]')) return;
		popoverOpen = false;
	}

	function handleWindowKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') popoverOpen = false;
	}
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<section class="admin-filter-bar rounded-lg border border-line bg-surface-1 py-2.5 px-4 shadow-raised">
	<div class="flex flex-nowrap items-center gap-2 min-w-0">
		{#if search}
			<div class="flex-1 min-w-0">
				{@render search()}
			</div>
		{/if}

		{#if filters}
			<div class="hidden lg:flex items-center gap-3 flex-shrink-0">
				{@render filters()}
			</div>
		{/if}

		{#if onClear && activeCount > 0}
			<Button variant="ghost" size="sm" icon="close" class="hidden lg:inline-flex flex-shrink-0" onclick={onClear}>
				Clear
			</Button>
		{/if}

		{#if filters}
			<div class="relative flex-shrink-0 lg:hidden" bind:this={triggerEl} data-admin-filter-trigger>
				<Button variant="secondary" size="sm" icon="filter" onclick={togglePopover}>
					Filters
					{#if activeCount > 0}
						<Badge variant="signal" size="sm" class="ml-1.5">{activeCount}</Badge>
					{/if}
				</Button>

				{#if popoverOpen}
					<div
						data-admin-filter-popover
						class="fixed z-50 rounded-lg border border-line-strong bg-surface-1 shadow-floating p-3 space-y-3"
						style={popoverStyle}
					>
						{@render filters()}
						{#if onClear && activeCount > 0}
							<div class="pt-2 border-t border-line flex justify-end">
								<Button
									variant="ghost"
									size="sm"
									onclick={() => {
										onClear?.();
										popoverOpen = false;
									}}
								>
									Clear filters
								</Button>
							</div>
						{/if}
					</div>
				{/if}
			</div>
		{/if}

		{#if trailing}
			<div class="ml-auto flex-shrink-0 flex items-center gap-2 min-w-0">
				{@render trailing()}
			</div>
		{/if}
	</div>
</section>

<style>
	/* The shared `.input` class sets no font size, so a raw <input>/<select>
	   renders at the browser default (~16px) — too big for this dense filter
	   row. Scope a compact size to every control inside the bar without
	   touching the global `.input` contract that DynamicForm and the edit
	   forms rely on. Filter controls also size to their value (`width: auto`)
	   instead of the fixed `w-40`/`w-56` widths tabs hand them, so a short
	   selection like "Image" doesn't reserve a huge empty box. */
	.admin-filter-bar :global(.input) {
		font-size: 0.8125rem;
		line-height: 1.25rem;
		padding-top: 0.3125rem;
		padding-bottom: 0.3125rem;
	}
	.admin-filter-bar :global(select.input) {
		width: auto;
		min-width: 7rem;
		padding-right: 2rem;
	}
	/* The search box is the one control that should keep filling its column. */
	.admin-filter-bar :global(input[type='search'].input) {
		width: 100%;
	}
</style>
