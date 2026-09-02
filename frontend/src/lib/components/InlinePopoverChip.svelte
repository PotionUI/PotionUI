<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from './Icon.svelte';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';

	// The chrome and behavior shared by every inline chip that opens a popover
	// in place inside a contenteditable host (InlineChipEditor). What lives here
	// is exactly the part that must not drift: the `contenteditable="false"` +
	// non-selectable island, mousedown-instead-of-click so the host editor never
	// loses its caret, outside-pointer/Escape dismissal, and the popover shell.
	// Every chip's own data, editing and labels stay in the wrapper.

	type ChipTone = 'signal' | 'accent' | 'warning';
	type ChipDensity = 'default' | 'tight';

	let {
		tone = 'signal',
		density = 'default',
		disabled = false,
		canOpen = true,
		removeTitle = 'Remove',
		popoverLabel,
		class: className = '',
		open = $bindable(false),
		onremove,
		label,
		popover
	}: {
		tone?: ChipTone;
		density?: ChipDensity;
		disabled?: boolean;
		/** False when the wrapper has nothing to show — the chip then stays inert
		 *  instead of opening an empty popover. */
		canOpen?: boolean;
		removeTitle?: string;
		popoverLabel: string;
		/** The host editor finds and re-mounts chips by class name, so each
		 *  wrapper's own class has to land on this root element. */
		class?: string;
		open?: boolean;
		onremove?: () => void;
		label: Snippet;
		popover: Snippet;
	} = $props();

	let chipRef = $state<HTMLSpanElement>();
	let popoverRef = $state<HTMLDivElement>();
	let popoverPos = $state<FlippedMenuPosition>({ left: 0, top: 0 });

	// Matches the `w-72` popover width; the height is a rough pre-portal
	// estimate refined once the popover has actually mounted and can be
	// measured (its content — a few inputs or a short list — varies by chip).
	const POPOVER_WIDTH = 288;
	const POPOVER_HEIGHT_ESTIMATE = 220;
	const POPOVER_GAP = 6;

	// Written as full literal strings so Tailwind's class scanner can see them.
	const toneClasses: Record<ChipTone, string> = {
		signal: 'bg-signal/10 border-signal/30 hover:border-signal/50',
		accent: 'bg-accent/10 border-accent/30 hover:border-accent/50',
		warning: 'bg-warning/10 border-warning/40 hover:border-warning/60'
	};

	const triggerGapClasses: Record<ChipDensity, string> = {
		default: 'gap-1.5',
		tight: 'gap-1'
	};

	const removePaddingClasses: Record<ChipDensity, string> = {
		default: 'pr-2.5',
		tight: 'pr-2'
	};

	function toggle(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		if (disabled || !canOpen) return;
		open = !open;
	}

	function handleRemove(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		onremove?.();
	}

	function handleWindowPointerDown(e: PointerEvent) {
		if (!open) return;
		const target = e.target as Node;
		if (chipRef?.contains(target)) return;
		// The popover itself lives at body level once open (see `use:portal`
		// below), so it's outside `chipRef` even for a click the user sees as
		// "inside the chip".
		if (popoverRef?.contains(target)) return;
		open = false;
	}

	function handleWindowKeydown(e: KeyboardEvent) {
		if (open && e.key === 'Escape') open = false;
	}

	function updatePosition() {
		if (!chipRef) return;
		const heightEstimate = popoverRef?.getBoundingClientRect().height || POPOVER_HEIGHT_ESTIMATE;
		popoverPos = computeFlippedMenuPosition(chipRef, {
			width: POPOVER_WIDTH,
			heightEstimate,
			gap: POPOVER_GAP
		});
	}

	let popoverStyle = $derived(
		`left: ${popoverPos.left}px; ${
			popoverPos.top !== undefined ? `top: ${popoverPos.top}px;` : `bottom: ${popoverPos.bottom}px;`
		}`
	);

	// The chip sits inside a prompt segment card with `overflow: hidden`
	// (PromptSegment.svelte `.card`), which clips an absolutely positioned
	// popover — `use:portal` escapes it and this keeps the fixed-position
	// popover anchored to the trigger while it's open.
	$effect(() => {
		if (!open) return;
		updatePosition();
		const raf = requestAnimationFrame(updatePosition);
		window.addEventListener('resize', updatePosition);
		window.addEventListener('scroll', updatePosition, true);
		return () => {
			cancelAnimationFrame(raf);
			window.removeEventListener('resize', updatePosition);
			window.removeEventListener('scroll', updatePosition, true);
		};
	});
</script>

<svelte:window onpointerdown={handleWindowPointerDown} onkeydown={handleWindowKeydown} />

<span
	bind:this={chipRef}
	class="inline-popover-chip relative inline-flex items-center rounded border text-fg
		transition-colors duration-100 mx-1 {toneClasses[tone]}
		{disabled ? 'opacity-50 cursor-not-allowed' : ''} {className}"
	contenteditable="false"
	style="user-select: none; vertical-align: middle;"
>
	<button
		type="button"
		onmousedown={toggle}
		{disabled}
		class="inline-flex items-center {triggerGapClasses[density]} px-2 py-1 {disabled
			? ''
			: 'cursor-pointer'} transition-colors duration-100"
	>
		{@render label()}
	</button>

	{#if !disabled}
		<button
			type="button"
			onmousedown={handleRemove}
			class="p-1.5 {removePaddingClasses[density]} text-fg-muted hover:text-fg transition-colors duration-100"
			title={removeTitle}
		>
			<Icon name="close" className="w-3.5 h-3.5" />
		</button>
	{/if}

	{#if open}
		<div
			bind:this={popoverRef}
			use:portal
			class="fixed z-50 w-72 rounded-lg border border-line-strong bg-surface-1 p-2.5 shadow-floating"
			style={popoverStyle}
			role="dialog"
			aria-label={popoverLabel}
		>
			{@render popover()}
		</div>
	{/if}
</span>

<style>
	.inline-popover-chip {
		position: relative;
		display: inline-flex;
		vertical-align: middle;
		line-height: 1;
	}
</style>
