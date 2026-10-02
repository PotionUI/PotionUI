<script lang="ts">
	import { tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
	import { Button, IconButton, Input } from '$lib/components/ui';
	import type { MediaTool, MediaToolGroup } from '$lib/tools/tools';
	import { mediaItemKey } from '$lib/utils/promptResources';
	import MediaSourceMenu from './MediaSourceMenu.svelte';
	import type { MediaSource } from './mediaFieldSources';
	import {
		ADD_LABELS,
		GROUP_TITLES,
		dropInGroup,
		formatCount,
		nudgeInGroup,
		type GroupLayout,
		type MediaGroup
	} from './mediaFieldGroups';
	import type { MediaKind } from './mediaLoaderConfig';
	import { metaLine, type MediaItemMetadata } from './mediaLoaderMeta';
	import { isDropAllowed, NO_DRAG, type DragState } from './mediaLoaderReorder';

	type Item = Record<string, unknown>;

	let {
		multiple,
		kinds,
		items,
		layout,
		adding = null,
		addingProgress = 0,
		addingName = null,
		dragging,
		pasteArmed,
		offersDraw,
		maxLabelLength = 64,
		toolGroupsFor,
		handleFor,
		brokenFlat = null,
		onTool,
		onLabel,
		onReorder,
		onSource,
		onPreview
	}: {
		multiple: boolean;
		kinds: MediaKind[];
		items: Item[];
		layout: GroupLayout<Item>;
		adding?: MediaKind | null;
		addingProgress?: number;
		addingName?: string | null;
		dragging: boolean;
		pasteArmed: boolean;
		offersDraw: boolean;
		maxLabelLength?: number;
		toolGroupsFor: (flatIndex: number) => MediaToolGroup[];
		handleFor: (flatIndex: number) => string | null;
		brokenFlat?: number | null;
		onTool: (tool: MediaTool, flatIndex: number) => void;
		onLabel: (flatIndex: number, label: string) => void;
		onReorder: (items: Item[], flatIndex: number) => void;
		onSource: (kind: MediaKind | null, source: MediaSource) => void;
		onPreview: (flatIndex: number) => void;
	} = $props();

	let openMenu = $state<number | null>(null);
	let drag = $state<DragState>(NO_DRAG);
	let root: HTMLDivElement | undefined = $state();

	const KIND_ICON: Record<MediaKind, string> = { image: 'image', video: 'video', audio: 'audio' };

	let addKind = $derived<MediaKind | null>(kinds.length === 1 ? kinds[0] : null);
	let addLabel = $derived(addKind ? ADD_LABELS[addKind] : 'Add media');
	let hasRows = $derived(layout.visible.some((group) => group.count > 0));
	let canAdd = $derived(layout.eyebrows ? layout.visible.concat(layout.folded).some((g) => !g.full) : !layout.visible[0]?.full);

	function urlOf(item: Item): string {
		return typeof item.url === 'string' ? item.url : '';
	}

	function nameOf(item: Item): string {
		return typeof item.name === 'string' && item.name ? item.name : 'Media';
	}

	function factsOf(item: Item, kind: MediaKind): string {
		const meta = item.metadata && typeof item.metadata === 'object' ? (item.metadata as MediaItemMetadata) : null;
		return metaLine(meta, kind) ?? '';
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
		root?.querySelector<HTMLButtonElement>(`[data-media-grip="${flatIndex}"]`)?.focus();
	}

	function handleGripKeydown(event: KeyboardEvent, group: MediaGroup<Item>, kindIndex: number) {
		const back = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
		const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown';
		if (!back && !forward) return;
		event.preventDefault();
		const result = nudgeInGroup(items, group, kindIndex, back ? -1 : 1);
		if (result) commit(result.items, result.flatIndex);
	}
</script>

<div bind:this={root} class="flex flex-col gap-2 min-w-0" data-media-row-face>
	{#each layout.visible as group (group.kind)}
		<div class="flex flex-col gap-1.5 min-w-0" data-media-group={group.kind}>
			{#if layout.eyebrows}
				<div class="flex items-center gap-1.5" data-media-eyebrow={group.kind}>
					<Icon name={KIND_ICON[group.kind]} className="w-3.5 h-3.5 shrink-0 text-fg-subtle" strokeWidth={1.8} />
					<span class="font-mono text-xs uppercase tracking-[0.07em] text-fg-muted">{GROUP_TITLES[group.kind]}</span>
					<span class="font-mono text-xs tabular-nums {group.full ? 'text-warning' : 'text-fg-subtle'}" data-media-group-count>
						{formatCount(group.count, group.limit)}
					</span>
					<div class="flex-1 h-px bg-line"></div>
				</div>
			{/if}

			{#each group.items as entry (mediaItemKey(entry.item) ?? entry.flatIndex)}
				{@const item = entry.item}
				{@const handle = handleFor(entry.flatIndex)}
				{@const over = isDropAllowed(drag, group.kind, entry.kindIndex) && drag.overIndex === entry.kindIndex}
				{@const broken = brokenFlat === entry.flatIndex}
				<div
					class="relative flex items-center gap-2 min-w-0 rounded-lg border bg-surface-1 p-1.5 transition-colors {over
						? 'border-signal'
						: 'border-line-strong'} {drag.laneKey === group.kind && drag.fromIndex === entry.kindIndex ? 'opacity-45' : ''}"
					role="group"
					aria-label="{GROUP_TITLES[group.kind]} {entry.position}"
					data-media-row={entry.flatIndex}
					ondragover={(event) => dragOver(event, group, entry.kindIndex)}
					ondrop={(event) => drop(event, group, entry.kindIndex)}
				>
					{#if multiple}
						<span
							class="shrink-0 inline-flex items-center"
							draggable="true"
							role="presentation"
							ondragstart={(event) => startDrag(event, group, entry.kindIndex)}
							ondragend={() => (drag = NO_DRAG)}
						>
							<Tooltip text="Drag to reorder, or Alt and the arrow keys" position="top">
								<button
									type="button"
									class="w-5 h-8 inline-flex items-center justify-center rounded text-fg-subtle hover:text-fg cursor-grab active:cursor-grabbing"
									aria-label="Reorder {GROUP_TITLES[group.kind]} {entry.position}"
									data-media-grip={entry.flatIndex}
									onkeydown={(event) => handleGripKeydown(event, group, entry.kindIndex)}
								>
									<Icon name="grip" className="w-3.5 h-3.5" />
								</button>
							</Tooltip>
						</span>
						<span class="shrink-0 font-mono text-xs tabular-nums text-fg-subtle w-4 text-right">{entry.position}</span>
					{/if}

					<button
						type="button"
						class="shrink-0 w-10 h-10 rounded overflow-hidden bg-surface-3 flex items-center justify-center"
						aria-label="View full size"
						disabled={!urlOf(item) || broken}
						onclick={() => onPreview(entry.flatIndex)}
					>
						{#if broken}
							<Icon name="warning" className="w-4 h-4 text-danger" />
						{:else if group.kind === 'image' && urlOf(item)}
							<img src={urlOf(item)} alt="" class="w-full h-full object-cover" />
						{:else if group.kind === 'video' && urlOf(item)}
							<!-- svelte-ignore a11y_media_has_caption -->
							<video src={urlOf(item)} class="w-full h-full object-cover" preload="metadata" muted></video>
						{:else}
							<Icon name={KIND_ICON[group.kind]} className="w-4 h-4 text-fg-subtle" />
						{/if}
					</button>

					<div class="min-w-0 flex-1 flex flex-col gap-0.5">
						{#if multiple}
							<Input
								type="text"
								value={typeof item.label === 'string' ? item.label : ''}
								placeholder={nameOf(item)}
								maxlength={maxLabelLength}
								aria-label="Label"
								class="min-w-0 !h-7 !py-0 !px-1.5"
								oninput={(event: Event) => onLabel(entry.flatIndex, (event.currentTarget as HTMLInputElement).value)}
							/>
						{:else}
							<Tooltip text={nameOf(item)} position="top" wrapperClass="flex min-w-0">
								<span class="min-w-0 truncate text-sm text-fg">{nameOf(item)}</span>
							</Tooltip>
						{/if}
						<span class="truncate font-mono text-xs tabular-nums {broken ? 'text-danger' : 'text-fg-subtle'}">
							{#if broken}File not found{:else if handle}{handle}{#if factsOf(item, group.kind)} · {/if}{factsOf(item, group.kind)}{:else}{factsOf(item, group.kind)}{/if}
						</span>
					</div>

					<ToolsMenu
						open={openMenu === entry.flatIndex}
						scope="field"
						groups={toolGroupsFor(entry.flatIndex)}
						variant="field"
						placement="down"
						compact
						icon="more"
						onToggle={() => (openMenu = openMenu === entry.flatIndex ? null : entry.flatIndex)}
						onClose={() => (openMenu = null)}
						onPick={(tool) => onTool(tool, entry.flatIndex)}
					/>
				</div>
			{/each}

			{#if adding === group.kind}
				<div
					class="flex items-center gap-2 rounded-lg border border-line-strong bg-surface-2 p-1.5"
					role="status"
					aria-label="Uploading"
					data-media-progress
				>
					<span class="spinner shrink-0"></span>
					<span class="min-w-0 flex-1 truncate text-sm text-fg-muted">{addingName ?? 'Uploading'}</span>
					<span class="shrink-0 font-mono text-xs tabular-nums text-fg-muted">{addingProgress}%</span>
				</div>
			{/if}
		</div>
	{/each}

	{#if (multiple || !hasRows) && canAdd && adding === null}
		<div
			class="flex items-center gap-2 rounded-lg border border-dashed p-1.5 min-w-0 transition-colors {dragging || pasteArmed
				? 'border-signal bg-signal/10'
				: 'border-line-strong bg-surface-1'}"
			data-media-row-add
		>
			<MediaSourceMenu kind={addKind} {offersDraw} onPick={(source) => onSource(addKind, source)}>
				{#snippet trigger({ toggle, open })}
					<div class="inline-flex shrink-0">
						<Button variant="secondary" size="xs" icon="plus" class="rounded-r-none" onclick={() => onSource(addKind, 'browse')}>
							{addLabel}
						</Button>
						<Tooltip text="More ways to add" position="top">
							<IconButton
								icon="chevron-down"
								label="More ways to add"
								size="xs"
								variant="secondary"
								class="rounded-l-none border-l border-line-strong h-full"
								ariaExpanded={open}
								onclick={toggle}
							/>
						</Tooltip>
					</div>
				{/snippet}
			</MediaSourceMenu>
			<span class="min-w-0 truncate font-mono text-xs text-fg-subtle">
				{pasteArmed ? 'Paste armed · Ctrl V' : dragging ? 'Release to add' : 'or drop here'}
			</span>
		</div>
	{/if}

</div>

<style>
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
