<script lang="ts">
	import { tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import MediaSourceMenu from './MediaSourceMenu.svelte';
	import type { MediaSource } from './mediaFieldSources';
	import {
		ADD_LABELS,
		GROUP_TITLES,
		dropInGroup,
		formatCount,
		moveSelection,
		nudgeInGroup,
		type GroupLayout,
		type MediaGroup
	} from './mediaFieldGroups';
	import type { MediaKind } from './mediaLoaderConfig';
	import { durationBadge, type MediaItemMetadata } from './mediaLoaderMeta';
	import { isDropAllowed, NO_DRAG, type DragState } from './mediaLoaderReorder';
	import { mediaItemKey } from '$lib/utils/promptResources';

	type Item = Record<string, unknown>;

	let {
		items,
		layout,
		selected,
		adding = null,
		addingProgress = 0,
		offersDraw,
		onSelect,
		onReorder,
		onRemove,
		onSource
	}: {
		items: Item[];
		layout: GroupLayout<Item>;
		selected: number | null;
		adding?: MediaKind | null;
		addingProgress?: number;
		offersDraw: boolean;
		onSelect: (flatIndex: number) => void;
		onReorder: (items: Item[], flatIndex: number) => void;
		onRemove: (flatIndex: number) => void;
		onSource: (kind: MediaKind, source: MediaSource) => void;
	} = $props();

	let drag = $state<DragState>(NO_DRAG);
	let root: HTMLDivElement | undefined = $state();

	const KIND_ICON: Record<MediaKind, string> = { image: 'image', video: 'video', audio: 'audio' };

	function urlOf(item: Item): string {
		return typeof item.url === 'string' ? item.url : '';
	}

	function nameOf(item: Item, position: number): string {
		return (typeof item.label === 'string' && item.label) || (typeof item.name === 'string' && item.name) || `Item ${position}`;
	}

	function badgeOf(item: Item, kind: MediaKind): string | null {
		const meta = item.metadata && typeof item.metadata === 'object' ? (item.metadata as MediaItemMetadata) : null;
		return durationBadge(meta, kind);
	}

	function startDrag(event: DragEvent, group: MediaGroup<Item>, kindIndex: number) {
		drag = { laneKey: group.kind, fromIndex: kindIndex, overIndex: kindIndex };
		if (event.dataTransfer) {
			event.dataTransfer.effectAllowed = 'move';
			event.dataTransfer.setData('text/plain', String(kindIndex));
		}
	}

	function dragOver(event: DragEvent, group: MediaGroup<Item>, kindIndex: number) {
		if (drag.laneKey !== group.kind) return;
		event.preventDefault();
		if (drag.overIndex !== kindIndex) drag = { ...drag, overIndex: kindIndex };
	}

	function drop(event: DragEvent, group: MediaGroup<Item>, kindIndex: number) {
		event.preventDefault();
		event.stopPropagation();
		if (!isDropAllowed(drag, group.kind, kindIndex)) {
			drag = NO_DRAG;
			return;
		}
		const result = dropInGroup(items, group, drag.fromIndex as number, kindIndex);
		drag = NO_DRAG;
		commit(result.items, result.flatIndex);
	}

	async function commit(next: Item[], flatIndex: number) {
		onReorder(next, flatIndex);
		await tick();
		focusTile(flatIndex);
	}

	function focusTile(flatIndex: number) {
		root?.querySelector<HTMLButtonElement>(`[data-media-tile="${flatIndex}"]`)?.focus();
	}

	function handleKeydown(event: KeyboardEvent, group: MediaGroup<Item>, kindIndex: number, flatIndex: number) {
		const back = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
		const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown';
		if ((back || forward) && event.altKey) {
			event.preventDefault();
			const result = nudgeInGroup(items, group, kindIndex, back ? -1 : 1);
			if (result) commit(result.items, result.flatIndex);
			return;
		}
		if (back || forward) {
			event.preventDefault();
			const next = moveSelection(layout.visible, flatIndex, back ? -1 : 1);
			if (next !== null && next !== flatIndex) {
				onSelect(next);
				focusTile(next);
			}
			return;
		}
		if (event.key === 'Delete' || event.key === 'Backspace') {
			event.preventDefault();
			onRemove(flatIndex);
		}
	}
</script>

{#snippet addTile(group: MediaGroup<Item>)}
	<MediaSourceMenu
		kind={group.kind}
		offersDraw={offersDraw && group.kind === 'image'}
		onPick={(source) => onSource(group.kind, source)}
	>
		{#snippet trigger({ toggle, open })}
			<Tooltip text={ADD_LABELS[group.kind]} position="top">
				<button
					type="button"
					class="strip-tile flex items-center justify-center rounded-lg border border-dashed text-fg-muted transition-colors hover:text-fg hover:border-line-hover {open
						? 'border-signal text-fg'
						: 'border-line-strong bg-surface-1'}"
					aria-label={ADD_LABELS[group.kind]}
					aria-haspopup="menu"
					aria-expanded={open}
					data-media-add={group.kind}
					onclick={toggle}
				>
					<Icon name="plus" className="w-4 h-4" />
				</button>
			</Tooltip>
		{/snippet}
	</MediaSourceMenu>
{/snippet}

<div bind:this={root} class="flex flex-col" data-media-strip>
	{#each layout.visible as group (group.kind)}
		<div class="mt-3 first:mt-0" data-media-group={group.kind}>
			{#if layout.eyebrows}
				<div class="flex items-center gap-1.5 mb-1.5" data-media-eyebrow={group.kind}>
					<Icon name={KIND_ICON[group.kind]} className="w-3.5 h-3.5 shrink-0 text-fg-subtle" strokeWidth={1.8} />
					<span class="font-mono text-xs uppercase tracking-[0.07em] text-fg-muted">{GROUP_TITLES[group.kind]}</span>
					<span class="font-mono text-xs tabular-nums {group.full ? 'text-warning' : 'text-fg-subtle'}" data-media-group-count>
						{formatCount(group.count, group.limit)}
					</span>
					<div class="flex-1 h-px bg-line"></div>
				</div>
			{/if}

			<div class="strip-row flex flex-wrap gap-2" role="group" aria-label="{GROUP_TITLES[group.kind]} in order">
				{#each group.items as entry (mediaItemKey(entry.item) ?? entry.flatIndex)}
					{@const item = entry.item}
					{@const isSelected = selected === entry.flatIndex}
					{@const dragging = drag.laneKey === group.kind && drag.fromIndex === entry.kindIndex}
					{@const over = isDropAllowed(drag, group.kind, entry.kindIndex) && drag.overIndex === entry.kindIndex}
					{@const duration = badgeOf(item, group.kind)}
					<Tooltip text={nameOf(item, entry.position)} position="top">
						<button
							type="button"
							draggable="true"
							class="strip-tile relative overflow-hidden rounded-lg bg-surface-3 ring-1 ring-inset transition-transform duration-100 {over
								? 'ring-signal translate-x-0.5'
								: isSelected
									? 'ring-signal'
									: 'ring-line hover:ring-line-hover'} {dragging ? 'opacity-45' : ''}"
							aria-label="{GROUP_TITLES[group.kind]} {entry.position}: {nameOf(item, entry.position)}"
							aria-current={isSelected ? 'true' : undefined}
							data-media-tile={entry.flatIndex}
							data-selected={isSelected ? 'true' : undefined}
							onclick={() => onSelect(entry.flatIndex)}
							onkeydown={(event) => handleKeydown(event, group, entry.kindIndex, entry.flatIndex)}
							ondragstart={(event) => startDrag(event, group, entry.kindIndex)}
							ondragover={(event) => dragOver(event, group, entry.kindIndex)}
							ondrop={(event) => drop(event, group, entry.kindIndex)}
							ondragend={() => (drag = NO_DRAG)}
						>
							{#if group.kind === 'image' && urlOf(item)}
								<img src={urlOf(item)} alt="" class="w-full h-full object-cover" draggable="false" />
							{:else if group.kind === 'video' && urlOf(item)}
								<!-- svelte-ignore a11y_media_has_caption -->
								<video src={urlOf(item)} class="w-full h-full object-cover" preload="metadata" muted></video>
							{:else}
								<span class="w-full h-full flex items-center justify-center">
									<Icon name={KIND_ICON[group.kind]} className="w-5 h-5 text-fg-subtle" />
								</span>
							{/if}

							<span
								class="absolute top-1 left-1 min-w-[18px] h-[18px] px-1 inline-flex items-center justify-center rounded font-mono text-xs font-semibold tabular-nums {isSelected
									? 'bg-signal-solid text-white'
									: 'bg-canvas/75 text-fg'}"
							>
								{entry.position}
							</span>

							{#if duration}
								<span
									class="absolute bottom-1 right-1 px-1 rounded bg-canvas/75 font-mono text-xs tabular-nums text-fg"
								>
									{duration}
								</span>
							{/if}

							{#if isSelected}
								<span class="absolute top-0.5 left-0.5 w-3 h-3 border-t-2 border-l-2 border-signal rounded-tl pointer-events-none"></span>
								<span class="absolute top-0.5 right-0.5 w-3 h-3 border-t-2 border-r-2 border-signal rounded-tr pointer-events-none"></span>
								<span class="absolute bottom-0.5 left-0.5 w-3 h-3 border-b-2 border-l-2 border-signal rounded-bl pointer-events-none"></span>
								<span class="absolute bottom-0.5 right-0.5 w-3 h-3 border-b-2 border-r-2 border-signal rounded-br pointer-events-none"></span>
							{/if}
						</button>
					</Tooltip>
				{/each}

				{#if adding === group.kind}
					<div
						class="strip-tile flex flex-col items-center justify-center gap-1 rounded-lg border border-line-strong bg-surface-2"
						role="status"
						aria-label="Uploading"
						data-media-progress
					>
						<span class="spinner"></span>
						<span class="font-mono text-xs tabular-nums text-fg-muted">{addingProgress}%</span>
					</div>
				{/if}

				{#if !group.full}
					{@render addTile(group)}
				{/if}
			</div>
		</div>
	{/each}
</div>

<style>
	.strip-row {
		container-type: inline-size;
	}

	.strip-tile {
		width: clamp(52px, calc((100cqw - 32px) / 5), 64px);
		aspect-ratio: 1 / 1;
	}

	.spinner {
		border: 2px solid rgb(var(--line-strong));
		border-top-color: rgb(var(--signal));
		border-radius: 50%;
		width: 16px;
		height: 16px;
		animation: spin 0.9s linear infinite;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
