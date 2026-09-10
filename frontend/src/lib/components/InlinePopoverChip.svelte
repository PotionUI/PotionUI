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
		popover,
		// The segment composer's own chip anatomy (`.chip.{kind}-chip` with a
		// chip-main/chip-config split, and a `.{kind}-popover` with the mock's
		// own header+close instead of a bare panel) — see
		// prompt-segments-concept.html. Every other host of this chip (chat's
		// ChatChipInput today) gets `variant="default"` and keeps today's
		// trigger+remove-button island untouched.
		variant = 'default',
		kind = 'choice',
		composerLabel,
		headerMark = '',
		headerTitle = '',
		headerSubtitle = '',
		footer
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
		variant?: 'default' | 'segment-composer';
		/** Which of the mock's two popover chips this is — picks `.choice-chip`/
		 *  `.variable-chip` and `.choice-popover`/`.variable-popover`. */
		kind?: 'choice' | 'variable';
		/** segment-composer only: the mock's plainer chip-main content (no
		 *  colour dot, no chevron) — `label` above stays the default variant's
		 *  own look, since the two anatomies aren't interchangeable. */
		composerLabel?: Snippet;
		headerMark?: string;
		headerTitle?: string;
		headerSubtitle?: string;
		/** segment-composer only: `.popover-actions` content — the mock puts
		 *  "Remove" here instead of a standing button on the chip itself. */
		footer?: Snippet;
	} = $props();

	// Matches the `w-72` popover width; the height is a rough pre-portal
	// estimate refined once the popover has actually mounted and can be
	// measured (its content — a few inputs or a short list — varies by chip).
	const POPOVER_WIDTH = 288;
	const POPOVER_HEIGHT_ESTIMATE = 220;
	const POPOVER_GAP = 6;

	let chipRef = $state<HTMLSpanElement>();
	let popoverRef = $state<HTMLDivElement>();
	let popoverPos = $state<FlippedMenuPosition>({ left: 0, top: 0, maxHeight: POPOVER_HEIGHT_ESTIMATE });

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
		// `scrollHeight`, not `getBoundingClientRect().height` — once a tall
		// popover is clamped by the `maxHeight` below, its rendered height is
		// the clamp itself, which would make it "fit" on the next reposition
		// and flip back to the other side. `scrollHeight` keeps reporting the
		// full (unclamped) content height regardless of overflow.
		const heightEstimate = popoverRef?.scrollHeight || POPOVER_HEIGHT_ESTIMATE;
		popoverPos = computeFlippedMenuPosition(chipRef, {
			width: POPOVER_WIDTH,
			heightEstimate,
			gap: POPOVER_GAP
		});
	}

	// The segment-composer variant's `.popover` class (ported from the mock)
	// carries its own static prototype `right`/`top` — every offset below is
	// set explicitly, including the unused pair as `auto`, so those can never
	// leak through a `left`+`width`+`right`(all-specified) or a `top`+`bottom`
	// (with no explicit height) resolution. `position: fixed` likewise
	// overrides `.floating`'s own `position: absolute`.
	let popoverStyle = $derived(
		`position: fixed; left: ${popoverPos.left}px; right: auto; max-height: ${popoverPos.maxHeight}px; overflow-y: auto; ${
			popoverPos.top !== undefined
				? `top: ${popoverPos.top}px; bottom: auto;`
				: `bottom: ${popoverPos.bottom}px; top: auto;`
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

{#if variant === 'segment-composer'}
	<span
		bind:this={chipRef}
		class="chip {kind}-chip {className}"
		class:active={open}
		class:opacity-50={disabled}
		contenteditable="false"
		data-chip={kind}
		style="user-select: none; vertical-align: middle;"
	>
		<button
			type="button"
			onmousedown={toggle}
			disabled={disabled || !canOpen}
			class="chip-main"
			aria-label={popoverLabel}
		>
			{@render (composerLabel ?? label)()}
		</button>
		{#if !disabled}
			<button type="button" onmousedown={toggle} class="chip-config" title={popoverLabel} aria-label={popoverLabel}>
				<svg class="icon"><use href="#i-sliders" /></svg>
			</button>
		{/if}

		{#if open}
			<!-- `display: contents`: a pure CSS-scope carrier, not a positioned box —
			     the real position lives inline on the popover div itself (see
			     popoverStyle above), never on this wrapper. -->
			<div class="segment-composer" use:portal style="display: contents;">
				<div
					bind:this={popoverRef}
					class="floating popover {kind}-popover"
					style={popoverStyle}
					role="dialog"
					aria-label={popoverLabel}
				>
					<header class="popover-head">
						<span class="popover-mark">{headerMark}</span>
						<div class="popover-title">
							<strong>{headerTitle}</strong>
							{#if headerSubtitle}<span>{headerSubtitle}</span>{/if}
						</div>
						<button type="button" class="close" aria-label="Close" onmousedown={toggle}>
							<svg class="icon"><use href="#i-close" /></svg>
						</button>
					</header>
					<div class="popover-body">
						{@render popover()}
					</div>
					{#if footer}
						<footer class="popover-actions">
							{@render footer()}
						</footer>
					{/if}
				</div>
			</div>
		{/if}
	</span>
{:else}
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
				class="fixed z-[99999] w-72 rounded-lg border border-line-strong bg-surface-1 p-2.5 shadow-floating"
				style={popoverStyle}
				role="dialog"
				aria-label={popoverLabel}
			>
				{@render popover()}
			</div>
		{/if}
	</span>
{/if}

<style>
	.inline-popover-chip {
		position: relative;
		display: inline-flex;
		vertical-align: middle;
		line-height: 1;
	}
</style>
