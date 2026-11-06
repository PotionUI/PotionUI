<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { pageOwnsKeys } from '$lib/utils/shortcutGuard';
	import { setFrameContext } from '$lib/tool-tabs/frame/context';
	import { resolveFrameKey, targetActivatesOn } from '$lib/tool-tabs/frame/keymap';
	import type { StackVariant } from '$lib/tool-tabs/frame/types';
	import FrameSheet from './FrameSheet.svelte';

	let {
		phone = false,
		active = true,
		compare = $bindable(false),
		canCompare = true,
		editCount = 0,
		stackOpen = $bindable(false),
		phoneChips = 'top',
		barVisible = true,
		onundo,
		onredo,
		onsave,
		onapply,
		toolbar,
		phoneLead,
		rail,
		canvas,
		below,
		panelTop,
		bar,
		options,
		stack
	}: {
		phone?: boolean;
		active?: boolean;
		compare?: boolean;
		canCompare?: boolean;
		editCount?: number;
		stackOpen?: boolean;
		phoneChips?: 'top' | 'bottom';
		barVisible?: boolean;
		onundo: () => void;
		onredo: () => void;
		onsave: () => void;
		onapply?: () => void;
		toolbar: Snippet;
		phoneLead?: Snippet;
		rail?: Snippet;
		canvas: Snippet;
		below?: Snippet;
		panelTop?: Snippet;
		bar?: Snippet;
		options?: Snippet;
		stack?: Snippet<[StackVariant]>;
	} = $props();

	let root = $state<HTMLElement | null>(null);
	let spaceHeld = $state(false);

	setFrameContext({
		get spaceHeld() {
			return spaceHeld;
		},
		get sheetHost() {
			return root;
		}
	});

	function onkeydown(event: KeyboardEvent) {
		if (!active || event.defaultPrevented) return;
		const action = resolveFrameKey(event);
		if (!action || !pageOwnsKeys(event)) return;
		if ((action === 'apply' || action === 'pan') && targetActivatesOn(event.target)) return;
		if (action === 'pan') {
			event.preventDefault();
			spaceHeld = true;
			return;
		}
		if (action === 'compare' && !canCompare) return;
		if (action === 'apply' && !onapply) return;
		event.preventDefault();
		event.stopPropagation();
		if (action === 'undo') onundo();
		else if (action === 'redo') onredo();
		else if (action === 'save') onsave();
		else if (action === 'apply') onapply?.();
		else compare = !compare;
	}

	function onkeyup(event: KeyboardEvent) {
		if (event.key === ' ') spaceHeld = false;
	}

	$effect(() => {
		if (!active) spaceHeld = false;
	});

	function hold(on: boolean) {
		if (canCompare) compare = on;
	}

	function holdKey(event: KeyboardEvent, on: boolean) {
		if (event.key !== ' ' && event.key !== 'Enter') return;
		if (on && event.repeat) return;
		event.preventDefault();
		hold(on);
	}
</script>

<svelte:window onkeydowncapture={onkeydown} onkeyup={onkeyup} onblur={() => (spaceHeld = false)} />

{#if phone}
	<div bind:this={root} class="relative flex h-full min-h-0 flex-col bg-canvas" data-editor-frame="phone">
		{@render toolbar()}
		<div class="relative flex min-h-0 flex-1">
			{@render canvas()}
			<div class="pointer-events-none absolute inset-x-2.5 flex items-center justify-between {phoneChips === 'top' ? 'top-2.5' : 'bottom-2.5'}">
				<div class="pointer-events-auto flex items-center gap-1.5">
					{@render phoneLead?.()}
					{#if stack}
						<button
							type="button"
							class="pointer-events-auto inline-flex h-7 items-center gap-1 rounded bg-scrim/70 px-2 font-mono text-sm tabular-nums text-scrim-on backdrop-blur"
							aria-haspopup="dialog"
							aria-expanded={stackOpen}
							onclick={() => (stackOpen = true)}
							data-frame-edits
						>
							<Icon name="editor-layers" className="h-3.5 w-3.5" />
							{editCount} {editCount === 1 ? 'edit' : 'edits'}
							<Icon name="chevron-down" className="h-3.5 w-3.5" />
						</button>
					{:else}
						<span></span>
					{/if}
				</div>
				{#if canCompare}
					<button
						type="button"
						class="pointer-events-auto inline-flex h-7 touch-none select-none items-center gap-1 rounded px-2 font-mono text-sm backdrop-blur {compare
							? 'bg-signal/10 text-signal'
							: 'bg-scrim/70 text-scrim-on'}"
						aria-label="Hold to compare with before"
						aria-pressed={compare}
						onpointerdown={() => hold(true)}
						onpointerup={() => hold(false)}
						onpointerleave={() => hold(false)}
						onpointercancel={() => hold(false)}
						onkeydown={(event) => holdKey(event, true)}
						onkeyup={(event) => holdKey(event, false)}
						oncontextmenu={(event) => event.preventDefault()}
						data-frame-hold
					>
						<Icon name="editor-compare" className="h-3.5 w-3.5" />Hold
					</button>
				{/if}
			</div>
		</div>
		{@render below?.()}
		{#if bar && barVisible}
			<div class="flex h-12 flex-none items-center gap-2.5 border-t border-line bg-surface-1 px-3" data-options-bar="phone">
				{@render bar()}
			</div>
		{/if}
		{@render options?.()}
		{@render rail?.()}
		{#if stackOpen && stack}
			<FrameSheet label="Edits" onclose={() => (stackOpen = false)}>
				{@render stack('sheet')}
			</FrameSheet>
		{/if}
	</div>
{:else}
	<div bind:this={root} class="relative flex h-full min-h-0 flex-col bg-canvas" data-editor-frame="desktop">
		{@render toolbar()}
		<div class="flex min-h-0 flex-1">
			{@render rail?.()}
			<div class="flex min-w-0 flex-1 flex-col">
				<div class="relative flex min-h-0 flex-1">
					{@render canvas()}
				</div>
				{@render below?.()}
			</div>
			<aside class="flex min-h-0 w-[312px] flex-none flex-col border-l border-line bg-surface-1" aria-label="Tool options and edits" data-frame-panel>
				{@render panelTop?.()}
				{@render options?.()}
				{@render stack?.('panel')}
			</aside>
		</div>
	</div>
{/if}
