<script lang="ts">
	import type { ChipData } from '$lib/types/segments';
	import { api } from '$lib/services/api/index';
	import { chipIndicatorColor } from '$lib/utils/chipIndicatorColor';
	import Icon from './Icon.svelte';
	import FuzzyFindModal from './modals/FuzzyFindModal.svelte';
	import type { FuzzyFindItem } from './modals/FuzzyFindModal.svelte';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';

	// Max characters before truncating
	const MAX_LABEL_LENGTH = 25;

	// In-chip tooltip: chips live inside the contenteditable editor, so the hint
	// must stay within the contenteditable=false span — a fixed-position tooltip
	// rendered as a sibling could get serialized into the segment text.
	let hint: string | null = null;
	let hintTimeout: ReturnType<typeof setTimeout> | null = null;


	// Props
	export let data: ChipData;
	export let colorIndex: number = 0;
	export let disabled: boolean = false;
	export let animate: 'shuffle' | 'none' = 'none';
	// The segment composer's own anatomy (`#` mark, chip-main/chip-config split,
	// a settings popover instead of inline shuffle/deactivate/remove buttons) —
	// see prompt-segments-concept.html. Every other host of this chip (chat's
	// ChatChipInput today) keeps the original markup untouched.
	export let variant: 'default' | 'segment-composer' = 'default';

	// Callback props (Svelte 5 style)
	export let onchange: ((data: ChipData) => void) | undefined = undefined;
	export let onremove: (() => void) | undefined = undefined;
	export let ondeactivate: ((data: ChipData) => void) | undefined = undefined;

	let showModal = false;
	let chipRef: HTMLSpanElement;

	// Transform chip values to FuzzyFindItem format
	$: modalItems = data.allValues.map((v) => ({
		id: v.id,
		label: v.label,
		description: v.value !== v.label ? v.value : undefined,
		imageUrl: v.preview_file_id ? api.getFileURL(v.preview_file_id, 'small') : undefined
	})) as FuzzyFindItem[];

	$: indicatorColor = chipIndicatorColor(colorIndex);
	$: hasAlternatives = data.allValues.length > 1;

	// Truncate long labels
	$: isTruncated = data.label.length > MAX_LABEL_LENGTH;
	$: displayLabel = isTruncated ? data.label.substring(0, MAX_LABEL_LENGTH) + '...' : data.label;

	function showHint(text: string) {
		hintTimeout = setTimeout(() => {
			hint = text;
		}, 300);
	}

	function clearHint() {
		if (hintTimeout) {
			clearTimeout(hintTimeout);
			hintTimeout = null;
		}
		hint = null;
	}

	function handleShuffle(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();

		if (data.allValues.length <= 1) return;

		// Pick a random value different from current
		const availableValues = data.allValues.filter((v) => v.id !== data.valueId);
		if (availableValues.length === 0) return;

		const randomValue = availableValues[Math.floor(Math.random() * availableValues.length)];
		onchange?.({
			...data,
			valueId: randomValue.id,
			label: randomValue.label,
			value: randomValue.value
		});
	}

	function toggleShuffleMode(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		onchange?.({ ...data, shuffle: !data.shuffle });
	}

	/** Segment-composer's segmented Fixed/Auto-shuffle control sets explicitly
	 *  rather than toggling — clicking the already-active side is a no-op. */
	function setFixed(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		if (!data.shuffle) return;
		onchange?.({ ...data, shuffle: false });
	}

	function setShuffle(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		if (data.shuffle || !hasAlternatives) return;
		onchange?.({ ...data, shuffle: true });
	}

	function handleLabelClick(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();

		if (disabled || data.allValues.length <= 1) return;

		showModal = true;
	}

	function handleRemove(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		onremove?.();
	}

	function handleDeactivate(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		ondeactivate?.(data);
	}

	function handleModalSelect(e: CustomEvent<FuzzyFindItem>) {
		const selected = e.detail;
		// Find the original value to get the full data
		const originalValue = data.allValues.find((v) => v.id === selected.id);
		if (originalValue) {
			onchange?.({
				...data,
				valueId: originalValue.id,
				label: originalValue.label,
				value: originalValue.value
			});
		}
	}

	function handleModalClose() {
		showModal = false;
	}

	// =====================
	// segment-composer variant: the `#` chip's settings popover
	// (`.chip-config` → `.phrase-popover`), replacing the default variant's
	// inline shuffle/AUTO/deactivate/remove button row.
	// =====================
	let configOpen = false;
	let configTriggerRef: HTMLButtonElement;
	let popoverRef: HTMLDivElement;
	let configPos: FlippedMenuPosition = { left: 0, maxHeight: 0 };

	function capitalize(part: string): string {
		return part ? part.charAt(0).toUpperCase() + part.slice(1) : part;
	}

	$: categoryParts = (data.categoryPath || '').split('.').filter(Boolean);
	$: categoryContext = categoryParts.length ? capitalize(categoryParts[categoryParts.length - 1]) : '';
	$: categoryTitle = categoryParts.length ? categoryParts.map(capitalize).join(' · ') : data.categoryPath;

	function updateConfigPos() {
		if (!configTriggerRef) return;
		configPos = computeFlippedMenuPosition(configTriggerRef, { width: 350, heightEstimate: 260, gap: 8 });
	}

	function toggleConfig(e: MouseEvent) {
		e.preventDefault();
		e.stopPropagation();
		if (disabled) return;
		if (!configOpen) updateConfigPos();
		configOpen = !configOpen;
	}

	function closeConfig() {
		configOpen = false;
	}

	function handleConfigWindowPointerDown(e: PointerEvent) {
		if (!configOpen) return;
		const target = e.target as Node;
		if (chipRef?.contains(target)) return;
		if (popoverRef?.contains(target)) return;
		configOpen = false;
	}

	function handleConfigWindowKeydown(e: KeyboardEvent) {
		if (configOpen && e.key === 'Escape') configOpen = false;
	}
</script>

<svelte:window on:pointerdown={handleConfigWindowPointerDown} on:keydown={handleConfigWindowKeydown} />

{#if variant === 'segment-composer'}
	<span
		bind:this={chipRef}
		class="chip phrase-chip"
		class:active={configOpen}
		class:shuffle-enabled={data.shuffle}
		class:shuffling={animate === 'shuffle'}
		class:opacity-50={disabled}
		contenteditable="false"
		data-chip-id={data.id}
		data-chip="phrase"
		style="user-select: none; vertical-align: middle;"
	>
		<button
			type="button"
			class="chip-main"
			disabled={disabled}
			title="Choose another value"
			on:mousedown|preventDefault|stopPropagation={handleLabelClick}
		>
			<span class="chip-mark">#</span>
			<span class="chip-context">{categoryContext}</span>
			<span class="chip-label">{displayLabel}</span>
		</button>
		{#if !disabled}
			<button
				type="button"
				class="chip-config"
				bind:this={configTriggerRef}
				title="Phrasebook chip settings"
				aria-label="Phrasebook chip settings"
				on:mousedown|preventDefault|stopPropagation={toggleConfig}
			>
				<svg class="icon"><use href="#i-sliders" /></svg>
			</button>
		{/if}

		{#if configOpen}
			<!-- `display: contents` on the wrapper: a pure CSS-scope carrier, not a
			     positioned box — the mock's `.popover` class carries its own static
			     prototype `right`/`top`, so the real caret-anchored position has to be
			     inline on the SAME element as `.popover`, with every offset set
			     explicitly (not just the ones in use) so the mock's own values can
			     never leak through a `left`+`width`+`right`(all-specified) or a
			     `top`+`bottom`(with no explicit height) resolution. -->
			<div class="segment-composer" use:portal style="display: contents;">
				<div
					bind:this={popoverRef}
					class="floating popover phrase-popover"
					style="position: fixed; left: {configPos.left}px; right: auto; {configPos.top !== undefined
						? `top: ${configPos.top}px; bottom: auto;`
						: `bottom: ${configPos.bottom}px; top: auto;`}"
					role="dialog"
					aria-label="Phrasebook chip settings"
				>
					<header class="popover-head">
						<span class="popover-mark">#</span>
						<div class="popover-title">
							<strong>{categoryTitle}</strong>
							<span
								>#{data.categoryPath} · {data.allValues.length} available value{data.allValues.length === 1
									? ''
									: 's'}</span
							>
						</div>
						<button type="button" class="close" aria-label="Close" on:click={closeConfig}>
							<svg class="icon"><use href="#i-close" /></svg>
						</button>
					</header>
					<div class="popover-body">
						<div class="section-label">Selected value</div>
						<div class="current-value-card">
							<div class="current-value-copy">
								<span>Used in this prompt</span>
								<strong>{data.label}</strong>
							</div>
							{#if hasAlternatives}
								<button type="button" class="small-button" on:click={handleLabelClick}>Change value…</button>
							{/if}
						</div>
						<div class="behavior">
							<div class="section-label">Generation behavior</div>
							<div class="segmented two">
								<button type="button" class="behavior-button" class:active={!data.shuffle} on:click={setFixed}
									>Fixed value</button
								>
								<button
									type="button"
									class="behavior-button"
									class:active={data.shuffle}
									disabled={!hasAlternatives}
									on:click={setShuffle}>Auto-shuffle</button
								>
							</div>
							<p class="helper">
								{data.shuffle
									? 'A new value is selected each time Generate is clicked.'
									: 'This value remains fixed until you choose another one.'}
							</p>
						</div>
					</div>
					<footer class="popover-actions">
						{#if hasAlternatives}
							<button type="button" class="small-button" on:click={handleShuffle}>
								<svg class="icon"><use href="#i-shuffle" /></svg>
								Shuffle now
							</button>
						{/if}
						{#if ondeactivate && hasAlternatives}
							<button type="button" class="small-button" on:click={handleDeactivate}>Deactivate value</button>
						{/if}
						<button type="button" class="small-button danger" on:click={handleRemove}>Remove</button>
					</footer>
				</div>
			</div>
		{/if}
	</span>
{:else}
	<span
		bind:this={chipRef}
		class="inline-chip group inline-flex items-center rounded border border-line bg-surface-2 text-fg-muted
			transition-colors duration-100 mx-1 {disabled ? '' : 'hover:border-line-hover'}
			{disabled ? 'opacity-50 cursor-not-allowed' : ''}
			{animate === 'shuffle' ? 'chip-shuffle-animation' : ''}"
		contenteditable="false"
		data-chip-id={data.id}
		style="user-select: none; vertical-align: middle;"
	>
		<button
			type="button"
			on:mousedown|preventDefault|stopPropagation={handleLabelClick}
			on:mouseenter={() => showHint(isTruncated ? data.label : '#' + data.categoryPath)}
			on:mouseleave={clearHint}
			disabled={disabled}
			class="relative inline-flex items-center gap-1.5 py-1 pl-1.5 {hasAlternatives ? 'pr-1' : 'pr-1.5'}
				{disabled ? '' : 'cursor-pointer hover:text-fg'} transition-colors duration-100"
		>
			<span class="w-2.5 h-2.5 rounded-full flex-shrink-0" style="background-color: {indicatorColor}"></span>
			<span class="text-xs font-medium whitespace-nowrap">{displayLabel}</span>
			{#if hasAlternatives}
				<Icon name="chevron-down" className="w-3 h-3 text-fg-subtle" strokeWidth={2} />
			{/if}

		</button>

		{#if !disabled}
			<span class="w-px h-4 bg-line flex-shrink-0" aria-hidden="true"></span>

			{#if hasAlternatives}
				<button
					type="button"
					on:mousedown|preventDefault|stopPropagation={handleShuffle}
					on:mouseenter={() => showHint(`Shuffle now (${data.allValues.length} options)`)}
					on:mouseleave={clearHint}
					class="p-1.5 text-fg-muted opacity-40 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 hover:text-fg transition-opacity duration-100"
					aria-label="Shuffle now"
				>
					<Icon name="shuffle" className="w-3.5 h-3.5" strokeWidth={2.5} />
				</button>

				<button
					type="button"
					on:mousedown|preventDefault|stopPropagation={toggleShuffleMode}
					on:mouseenter={() =>
						showHint(
							data.shuffle
								? 'Auto-shuffle is on — click to turn off'
								: 'Auto-shuffle on every generation — click to turn on'
						)}
					on:mouseleave={clearHint}
					class="flex items-center justify-center px-1 py-1.5 transition-opacity duration-100 {data.shuffle
						? 'opacity-100'
						: 'opacity-40 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100'}"
					aria-pressed={data.shuffle}
				>
					<span
						class="block rounded-sm px-1 font-mono text-[9px] font-semibold leading-4 tracking-wide transition-colors duration-100 {data.shuffle
							? 'bg-signal/15 text-signal'
							: 'text-fg-subtle hover:text-fg'}">AUTO</span
					>
				</button>
			{/if}

			{#if ondeactivate && hasAlternatives}
				<button
					type="button"
					on:mousedown|preventDefault|stopPropagation={handleDeactivate}
					on:mouseenter={() => showHint("Deactivate this value (won't appear in future shuffles)")}
					on:mouseleave={clearHint}
					class="p-1.5 text-fg-muted opacity-40 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 hover:text-danger transition-opacity duration-100"
					aria-label="Deactivate this value"
				>
					<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
						<circle cx="12" cy="12" r="9" />
						<path stroke-linecap="round" d="M5.5 5.5l13 13" />
					</svg>
				</button>
			{/if}

			<button
				type="button"
				on:mousedown|preventDefault|stopPropagation={handleRemove}
				on:mouseenter={() => showHint('Remove chip')}
				on:mouseleave={clearHint}
				class="p-1.5 text-fg-muted opacity-40 group-hover:opacity-100 group-focus-within:opacity-100 focus:opacity-100 hover:text-danger transition-opacity duration-100"
				aria-label="Remove chip"
			>
				<Icon name="close" className="w-3.5 h-3.5" strokeWidth={2.5} />
			</button>
		{/if}

		{#if hint}
			<span class="chip-tooltip">{hint}</span>
		{/if}
	</span>
{/if}

<!-- Value Selection Modal -->
<FuzzyFindModal
	isOpen={showModal}
	title="Select Value"
	subtitle="#{data.categoryPath}"
	items={modalItems}
	selectedId={data.valueId}
	placeholder="Search values..."
	emptyMessage="No values match your search"
	size="lg"
	variant={variant === 'segment-composer' ? 'segment-composer' : 'default'}
	on:select={handleModalSelect}
	on:close={handleModalClose}
/>

<style>
	.inline-chip {
		position: relative;
		display: inline-flex;
		vertical-align: middle;
		line-height: 1;
	}

	.chip-tooltip {
		position: absolute;
		bottom: calc(100% + 8px);
		left: 50%;
		transform: translateX(-50%);
		background-color: rgb(var(--surface-3));
		border: 1px solid rgb(var(--line-hover));
		color: rgb(var(--fg));
		padding: 6px 10px;
		border-radius: 6px;
		font-size: 12px;
		font-weight: 500;
		white-space: normal;
		word-break: break-word;
		width: max-content;
		max-width: 300px;
		box-shadow: var(--shadow-floating);
		z-index: 9999;
		pointer-events: none;
		animation: fadeIn 0.15s ease-out;
	}

	.chip-tooltip::after {
		content: '';
		position: absolute;
		top: 100%;
		left: 50%;
		transform: translateX(-50%);
		border: 6px solid transparent;
		border-top-color: rgb(var(--surface-3));
	}

	@keyframes fadeIn {
		from {
			opacity: 0;
			transform: translateX(-50%) translateY(4px);
		}
		to {
			opacity: 1;
			transform: translateX(-50%) translateY(0);
		}
	}

	/* Quiet acknowledgment flash - no transform, no color glow */
	.chip-shuffle-animation {
		animation: chip-flash 0.5s ease-in-out;
	}

	@keyframes chip-flash {
		0%,
		100% {
			box-shadow: none;
		}
		50% {
			box-shadow:
				0 0 0 1px rgb(var(--accent) / 0.6),
				0 0 8px rgb(var(--accent) / 0.15);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.chip-shuffle-animation {
			animation: none;
		}
		.chip-tooltip {
			animation: none;
		}
	}
</style>
