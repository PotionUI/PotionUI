<script lang="ts">
	import type { ChipData } from '$lib/types/segments';
	import { api } from '$lib/services/api/index';
	import { chipIndicatorColor } from '$lib/utils/chipIndicatorColor';
	import Icon from './Icon.svelte';
	import FuzzyFindModal from './modals/FuzzyFindModal.svelte';
	import type { FuzzyFindItem } from './modals/FuzzyFindModal.svelte';

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
</script>

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
