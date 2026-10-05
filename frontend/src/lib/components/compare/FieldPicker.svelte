<script lang="ts">
	import { onMount, tick } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import Icon from '$lib/components/Icon.svelte';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import { editorTypeLabel, filterCandidates, groupCandidates } from '$lib/generation/compare/candidates';
	import type { AxisCandidate } from '$lib/generation/compare/types';

	let {
		candidates,
		selectedField,
		otherField,
		otherSlot,
		anchor,
		onSelect,
		onClose
	}: {
		candidates: AxisCandidate[];
		selectedField: string | null;
		otherField: string | null;
		otherSlot: 'x' | 'y';
		anchor: HTMLElement;
		onSelect: (candidate: AxisCandidate) => void;
		onClose: () => void;
	} = $props();

	let query = $state('');
	let activeField = $state<string | null>(null);
	let searchEl = $state<HTMLInputElement>();
	let panelEl = $state<HTMLDivElement>();
	let position = $state<FlippedMenuPosition>({ left: 0, top: 0, maxHeight: 360 });

	let filtered = $derived(filterCandidates(candidates, query));
	let groups = $derived(groupCandidates(filtered));
	let selectable = $derived(
		groups.flatMap((g) => g.items).filter((c) => c.unavailableReason === null && c.field !== otherField)
	);

	function place() {
		if (!anchor) return;
		const width = Math.max(anchor.getBoundingClientRect().width, 280);
		position = computeFlippedMenuPosition(anchor, { width, heightEstimate: 380 });
	}

	function widthOf(): number {
		return Math.max(anchor?.getBoundingClientRect().width ?? 280, 280);
	}

	function choose(candidate: AxisCandidate) {
		if (candidate.unavailableReason !== null || candidate.field === otherField) return;
		onSelect(candidate);
	}

	function move(delta: number) {
		if (selectable.length === 0) return;
		const index = selectable.findIndex((c) => c.field === activeField);
		const next = (index + delta + selectable.length) % selectable.length;
		activeField = selectable[next].field;
		void tick().then(() => {
			const rows = [...(panelEl?.querySelectorAll<HTMLElement>('[data-field]') ?? [])];
			rows.find((row) => row.dataset.field === activeField)?.scrollIntoView?.({ block: 'nearest' });
		});
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			onClose();
		} else if (event.key === 'ArrowDown') {
			event.preventDefault();
			move(1);
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			move(-1);
		} else if (event.key === 'Enter') {
			event.preventDefault();
			const hit = selectable.find((c) => c.field === activeField) ?? selectable[0];
			if (hit) choose(hit);
		}
	}

	function onPointerDown(event: PointerEvent) {
		const target = event.target as Node;
		if (panelEl?.contains(target) || anchor?.contains(target)) return;
		onClose();
	}

	function reasonFor(candidate: AxisCandidate): string | null {
		if (candidate.field === otherField) return `On the ${otherSlot.toUpperCase()} axis`;
		return candidate.unavailableReason;
	}

	$effect(() => {
		if (!activeField || !selectable.some((c) => c.field === activeField)) {
			activeField = selectable.find((c) => c.field === selectedField)?.field ?? selectable[0]?.field ?? null;
		}
	});

	onMount(() => {
		place();
		searchEl?.focus();
		window.addEventListener('pointerdown', onPointerDown, true);
		window.addEventListener('resize', place);
		window.addEventListener('scroll', place, true);
		return () => {
			window.removeEventListener('pointerdown', onPointerDown, true);
			window.removeEventListener('resize', place);
			window.removeEventListener('scroll', place, true);
		};
	});
</script>

<div
	bind:this={panelEl}
	use:portal
	use:overlayLayer
	class="fixed z-overlay flex flex-col overflow-hidden rounded-lg border border-line-strong bg-surface-2 shadow-floating"
	style="left: {position.left}px; width: {widthOf()}px; max-height: {position.maxHeight}px; {position.top !== undefined
		? `top: ${position.top}px`
		: `bottom: ${position.bottom}px`}"
	role="dialog"
	aria-label="Pick a field"
	tabindex="-1"
	onkeydown={onKeydown}
>
	<div class="flex-shrink-0 p-2">
		<div class="flex items-center gap-2 rounded border border-line bg-surface-1 px-2.5 py-1.5">
			<Icon name="search" className="h-4 w-4 text-fg-subtle" />
			<input
				bind:this={searchEl}
				bind:value={query}
				type="text"
				class="min-w-0 flex-1 bg-transparent text-sm text-fg placeholder:text-fg-subtle focus:outline-none"
				placeholder="Find a field"
				aria-label="Find a field"
			/>
		</div>
	</div>
	<div class="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2" role="listbox" aria-label="Fields">
		{#if candidates.length === 0}
			<p class="px-2.5 py-4 text-sm text-fg-subtle">Open a preset first.</p>
		{:else if groups.length === 0}
			<p class="px-2.5 py-4 text-sm text-fg-subtle">No field matches.</p>
		{:else}
			{#each groups as group (group.group)}
				<div class="px-2.5 pb-1 pt-2.5 font-mono text-[10px] uppercase tracking-wider text-fg-subtle">
					{group.group}
				</div>
				{#each group.items as candidate (candidate.field)}
					{@const reason = reasonFor(candidate)}
					<button
						type="button"
						role="option"
						data-field={candidate.field}
						aria-selected={candidate.field === selectedField}
						aria-disabled={reason !== null}
						disabled={reason !== null}
						class="flex w-full items-center justify-between gap-3 rounded px-2.5 py-1.5 text-left text-sm transition-colors {reason !== null
							? 'cursor-not-allowed text-fg-subtle'
							: candidate.field === selectedField
								? 'bg-signal/10 text-signal'
								: candidate.field === activeField
									? 'bg-surface-3 text-fg'
									: 'text-fg hover:bg-surface-3'}"
						onclick={() => choose(candidate)}
						onmousemove={() => reason === null && (activeField = candidate.field)}
					>
						<span class="min-w-0 truncate">{candidate.label}</span>
						<span class="flex-shrink-0 font-mono text-xs text-fg-subtle">
							{reason ?? editorTypeLabel(candidate)}
						</span>
					</button>
				{/each}
			{/each}
		{/if}
	</div>
</div>
