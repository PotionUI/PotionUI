<script lang="ts">
	import { tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Menu, { type MenuItem } from '$lib/components/ui/Menu.svelte';
	import { Button, Kbd } from '$lib/components/ui';
	import ImageSourceMenu from '$lib/components/paint/ImageSourceMenu.svelte';
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import { dropRow, slotFor } from '$lib/paint/dragReorder';
	import { PAINT_ICONS } from '$lib/paint/icons';
	import type { LayerAction } from '$lib/paint/layerActions';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';
	import thumb from '$lib/paint/thumb';
	import type { DocSnapshot, Layer } from '$lib/paint/types';
	import { registerIcon } from '$lib/utils/IconLibrary';

	let {
		session,
		state: snapshot,
		phone = false,
		actions = [],
		note = true,
		adding = $bindable(false)
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		phone?: boolean;
		actions?: LayerAction[];
		note?: boolean;
		adding?: boolean;
	} = $props();

	registerIcon('paint-merge', PAINT_ICONS.merge);
	registerIcon('paint-add-image', PAINT_ICONS.addImage);

	let renaming = $state<number | null>(null);
	let renameDraft = $state('');
	let dragFrom = $state<number | null>(null);
	let dropAt = $state<{ index: number; after: boolean } | null>(null);
	let opacityBefore: DocSnapshot | null = null;

	let floating = $derived(snapshot.floatingId === null ? null : (snapshot.layers.find((layer) => layer.id === snapshot.floatingId) ?? null));
	let rows = $derived(
		snapshot.layers
			.map((layer, index) => ({ layer, index }))
			.filter(({ layer }) => layer.id !== snapshot.floatingId && !layer.internal)
			.reverse()
	);
	let active = $derived(snapshot.layers[snapshot.activeIndex]);
	let opacityPercent = $derived(Math.round((active?.opacity ?? 1) * 100));

	let rowMin = $derived(phone ? 'min-h-[52px]' : 'min-h-11');
	const thumbSize = 'h-9 w-9';

	const iconButton ='inline-flex h-8 w-8 flex-none items-center justify-center rounded text-fg-muted hover:bg-surface-3/50 hover:text-fg focus-visible:outline focus-visible:outline-1 focus-visible:outline-signal';

	function addItems(): MenuItem[] {
		return [
			{ id: 'new', label: 'New layer', icon: 'plus' },
			{ id: 'image', label: 'Add a picture as a layer…', icon: 'paint-add-image' }
		];
	}

	function rowItems(index: number): MenuItem[] {
		const top = snapshot.layers.findLastIndex((layer) => !layer.internal);
		const items: MenuItem[] = [
			{ id: 'rename', label: 'Rename', icon: 'edit' },
			{ id: 'duplicate', label: 'Duplicate', icon: 'copy' },
			{ id: 'merge', label: 'Merge down', icon: 'paint-merge', disabled: index < 1 }
		];
		if (actions.length) {
			items.push({ id: 'sep-actions', label: '', separator: true });
			for (const action of actions) items.push({ id: `action:${action.id}`, label: action.label, icon: action.icon });
		}
		items.push(
			{ id: 'sep-order', label: '', separator: true },
			{ id: 'up', label: 'Move up', icon: 'chevron-up', disabled: index >= top },
			{ id: 'down', label: 'Move down', icon: 'chevron-down', disabled: index < 1 },
			{ id: 'sep-delete', label: '', separator: true },
			{ id: 'delete', label: 'Delete', icon: 'trash', danger: true, disabled: snapshot.layers.length < 2 }
		);
		return items;
	}

	function startRename(layer: Layer) {
		renaming = layer.id;
		renameDraft = layer.name;
	}

	function commitRename(index: number) {
		if (renaming === null) return;
		renaming = null;
		session.renameLayer(index, renameDraft);
	}

	function focusInput(node: HTMLInputElement) {
		void tick().then(() => {
			node.focus();
			node.select();
		});
	}

	function chooseRow(index: number, layer: Layer, id: string) {
		if (id === 'rename') return startRename(layer);
		session.setActiveLayer(index);
		if (id === 'duplicate') session.duplicateLayer();
		else if (id === 'merge') session.mergeDown();
		else if (id === 'up') session.moveLayer('up');
		else if (id === 'down') session.moveLayer('down');
		else if (id === 'delete') session.deleteLayer();
		else if (id.startsWith('action:')) actions.find((action) => `action:${action.id}` === id)?.run(index);
	}

	function chooseAdd(id: string) {
		if (id === 'new') session.addLayer();
		else adding = true;
	}

	function chooseFloating(id: string) {
		if (id === 'place') session.placeFloating();
		else if (id === 'keep') session.keepFloatingAsLayer();
	}

	function beginOpacity() {
		opacityBefore = session.beginLayerOpacity(snapshot.activeIndex);
	}

	function liveOpacity(value: number) {
		if (!opacityBefore) beginOpacity();
		session.setLayerOpacity(snapshot.activeIndex, value / 100);
	}

	function commitOpacity() {
		session.finishLayerOpacity(opacityBefore);
		opacityBefore = null;
	}

	function dragStart(event: DragEvent, index: number, row: HTMLElement | null) {
		dragFrom = index;
		if (event.dataTransfer) {
			event.dataTransfer.effectAllowed = 'move';
			event.dataTransfer.setData('text/plain', String(index));
			if (row) event.dataTransfer.setDragImage(row, 16, 16);
		}
	}

	function dragOver(event: DragEvent, index: number) {
		if (dragFrom === null) return;
		event.preventDefault();
		const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
		dropAt = { index, after: event.clientY > box.top + box.height / 2 };
	}

	function dragEnd() {
		dragFrom = null;
		dropAt = null;
	}

	function drop(event: DragEvent) {
		event.preventDefault();
		const from = dragFrom;
		const target = dropAt;
		dragEnd();
		if (from === null || !target) return;
		session.moveLayerTo(from, slotFor(from, target.index, target.after));
	}
</script>

<section class="relative flex flex-col gap-2.5" aria-label="Layers" data-paint-layers>
	<div class="flex items-center gap-2">
		<h3 class="text-base font-semibold text-fg">Layers</h3>
		<span class="font-mono text-sm tabular-nums text-fg-subtle" data-layer-count>{snapshot.layers.length}</span>
		<span class="flex-1"></span>
		<Menu label="Add a layer" icon="plus" size="sm" items={addItems()} onselect={chooseAdd} align="right" width={240} menuAttrs={{ 'data-layers-add-menu': '' }} />
	</div>
	{#if note}
		<p class="text-sm text-fg-subtle" data-layers-note>Layers land as one edit when you press Apply paint.</p>
	{/if}

	{#if adding}
		<div class="relative">
			<ImageSourceMenu mode="layer" onPick={(image) => session.addImage(image.canvas, image.name)} onClose={() => (adding = false)} />
		</div>
	{/if}

	<ul class="flex flex-col gap-1.5" aria-label="Layers">
		{#if floating}
			<li data-floating-row>
				<div class="flex items-center gap-2 rounded border border-dashed border-signal bg-signal/10 px-1.5 {rowMin}">
					<span class="w-4 flex-none" aria-hidden="true"></span>
					<button type="button" class={iconButton} aria-label={floating.visible ? 'Hide floating piece' : 'Show floating piece'} aria-pressed={floating.visible} onclick={() => session.toggleLayerVisible(snapshot.layers.indexOf(floating!))}>
						<Icon name={floating.visible ? 'editor-eye' : 'editor-eye-off'} className="h-4 w-4" />
					</button>
					<canvas width="36" height="36" class="alpha-checker {thumbSize} flex-none rounded-sm" use:thumb={{ layer: floating, revision: snapshot.revision }}></canvas>
					<div class="flex min-w-0 flex-1 flex-col">
						<span class="truncate text-base text-fg">Floating piece</span>
						<span class="flex items-center gap-1 text-sm text-signal">Press <Kbd keys="Enter" /> to place</span>
					</div>
					<Button variant="secondary" size="xs" onclick={() => session.placeFloating()} attrs={{ 'data-floating-place': '' }}>Place</Button>
					<Menu
						label="Floating piece actions"
						icon="more"
						size="xs"
						items={[
							{ id: 'place', label: 'Place', icon: 'check' },
							{ id: 'keep', label: 'Keep as a new layer', icon: 'layers' }
						]}
						onselect={chooseFloating}
						width={232}
						menuAttrs={{ 'data-floating-menu': '' }}
					/>
				</div>
			</li>
		{/if}
		{#each rows as { layer, index } (layer.id)}
			{@const isActive = index === snapshot.activeIndex}
			{@const hint = index === 0 && layer.name === 'Background' ? 'Your picture' : ''}
			<li
				data-layer-row={index}
				data-layer-active={isActive || undefined}
				use:dropRow={{ over: (event) => dragOver(event, index), drop }}
			>
				{#if dropAt && dropAt.index === index && !dropAt.after && dragFrom !== null && dragFrom !== index}
					<div class="mb-1 h-0.5 rounded-sm bg-signal" aria-hidden="true"></div>
				{/if}
				<div
					class="rounded border {isActive ? 'border-signal/60 bg-signal/10' : 'border-line bg-surface-2 hover:border-line-hover'} {dragFrom === index ? 'opacity-50' : ''}"
					data-layer-box
				>
					<div class="flex items-center gap-1 px-1 {rowMin}">
						<span
							class="inline-flex h-8 w-5 flex-none cursor-grab items-center justify-center text-fg-subtle active:cursor-grabbing"
							role="button"
							tabindex="-1"
							aria-label="Drag to reorder {layer.name}"
							draggable="true"
							ondragstart={(event) => dragStart(event, index, (event.currentTarget as HTMLElement).closest('[data-layer-box]'))}
							ondragend={dragEnd}
							data-layer-handle
						>
							<Icon name="grip" className="h-4 w-4" />
						</span>
						<button
							type="button"
							class={iconButton}
							aria-label={layer.visible ? `Hide ${layer.name}` : `Show ${layer.name}`}
							aria-pressed={layer.visible}
							onclick={() => session.toggleLayerVisible(index)}
							data-layer-eye
						>
							<Icon name={layer.visible ? 'editor-eye' : 'editor-eye-off'} className="h-4 w-4" />
						</button>
						{#if renaming === layer.id}
							<div class="flex min-w-0 flex-1 items-center gap-2 px-1 py-1">
								<canvas width="36" height="36" class="alpha-checker {thumbSize} flex-none rounded-sm" use:thumb={{ layer, revision: snapshot.revision }}></canvas>
								<input
									type="text"
									class="h-8 min-w-0 flex-1 rounded border border-signal/60 bg-surface-1 px-2 text-base text-fg focus:outline-none"
									aria-label="Layer name"
									bind:value={renameDraft}
									use:focusInput
									onkeydown={(event) => {
										event.stopPropagation();
										if (event.key === 'Enter') commitRename(index);
										else if (event.key === 'Escape') renaming = null;
									}}
									onblur={() => commitRename(index)}
								/>
							</div>
						{:else}
							<button
								type="button"
								class="flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-1 text-left focus-visible:outline focus-visible:outline-1 focus-visible:outline-signal"
								aria-label="Select layer {layer.name}"
								aria-current={isActive}
								onclick={() => session.setActiveLayer(index)}
								ondblclick={() => startRename(layer)}
								data-layer-select
							>
								<canvas width="36" height="36" class="alpha-checker {thumbSize} flex-none rounded-sm" use:thumb={{ layer, revision: snapshot.revision }}></canvas>
								<span class="flex min-w-0 flex-1 flex-col">
									<span class="truncate text-base {layer.visible ? 'text-fg' : 'text-fg-disabled'}">{layer.name}</span>
									{#if hint}<span class="truncate text-sm text-fg-subtle">{hint}</span>{/if}
								</span>
								<span class="flex-none font-mono text-sm tabular-nums {isActive ? 'text-signal' : 'text-fg-subtle'}" data-layer-opacity-readout>{Math.round(layer.opacity * 100)}%</span>
							</button>
						{/if}
						<Menu label="Actions for {layer.name}" icon="more" size="xs" items={rowItems(index)} onselect={(id) => chooseRow(index, layer, id)} width={248} menuAttrs={{ 'data-layer-menu': index }} />
					</div>
					{#if isActive}
						<div class="px-3 pb-2" role="group" aria-label="Layer opacity" data-layer-opacity>
							<SliderField
								name="layer-opacity"
								config={{ title: 'Opacity', min: 0, max: 100, default: 100, unit: '%', layout: 'inline', reset: true, legend: false }}
								value={opacityPercent}
								onChange={(_name, value) => liveOpacity(Number(value))}
								onCommit={commitOpacity}
							/>
						</div>
					{/if}
				</div>
				{#if dropAt && dropAt.index === index && dropAt.after && dragFrom !== null && dragFrom !== index}
					<div class="mt-1 h-0.5 rounded-sm bg-signal" aria-hidden="true"></div>
				{/if}
			</li>
		{/each}
	</ul>
</section>
