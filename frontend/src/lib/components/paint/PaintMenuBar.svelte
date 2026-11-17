<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton } from '$lib/components/ui';
	import Menu from '$lib/components/ui/Menu.svelte';
	import { guard, reportFailure } from '$lib/editors/guard';
	import { paintActions } from '$lib/paint/registries';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';
	import type { PaintAction } from '$lib/paint/types';
	import type { RailGroup } from '$lib/tool-tabs/frame/types';
	import { primaryModifier } from '$lib/utils/platform';

	interface Row {
		id: string;
		label?: string;
		icon?: string;
		keys?: string;
		checked?: boolean;
		disabled?: boolean;
		heading?: boolean;
		separator?: boolean;
		run?: () => void;
	}

	let {
		session,
		state: snapshot,
		rail,
		activeTool,
		panelHidden,
		fullScreen,
		onOpenImage,
		onAddImage,
		onCanvasSize,
		onSelectTool,
		onTogglePanel,
		onToggleFull
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		rail: RailGroup[];
		activeTool: string;
		panelHidden: boolean;
		fullScreen: boolean;
		onOpenImage: () => void;
		onAddImage: () => void;
		onCanvasSize: () => void;
		onSelectTool: (id: string) => void;
		onTogglePanel: () => void;
		onToggleFull: () => void;
	} = $props();

	const modifier = primaryModifier();

	let actions = $state.raw<PaintAction[]>(paintActions.list());
	const stop = paintActions.subscribe(() => (actions = paintActions.list()));
	onDestroy(stop);

	function runAction(action: PaintAction) {
		const entry = `paint:action:${action.id}`;
		guard(
			entry,
			'run',
			() => {
				const pending = action.run(session);
				if (pending && typeof pending.catch === 'function') pending.catch((error: unknown) => reportFailure(entry, 'run', error));
			},
			undefined
		);
	}

	let menus = $derived.by<{ id: string; label: string; rows: Row[] }[]>(() => [
		{
			id: 'file',
			label: 'File',
			rows: [
				{ id: 'open', label: 'Open another image…', icon: 'folder-open', run: onOpenImage },
				{ id: 'add', label: 'Add a picture as a layer…', icon: 'plus', run: onAddImage }
			]
		},
		{
			id: 'edit',
			label: 'Edit',
			rows: [
				{ id: 'undo', label: snapshot.undoLabel ? `Undo ${snapshot.undoLabel}` : 'Undo', icon: 'editor-undo', keys: `${modifier} Z`, disabled: !snapshot.canUndo, run: () => session.undo() },
				{ id: 'redo', label: snapshot.redoLabel ? `Redo ${snapshot.redoLabel}` : 'Redo', icon: 'editor-redo', keys: `${modifier} Y`, disabled: !snapshot.canRedo, run: () => session.redo() },
				{ id: 'sep-flip', separator: true },
				{ id: 'flip-h', label: 'Flip horizontally', icon: 'flip', run: () => session.flip('horizontal') },
				{ id: 'flip-v', label: 'Flip vertically', icon: 'editor-flip-v', run: () => session.flip('vertical') },
				{ id: 'rotate-l', label: 'Rotate left', icon: 'editor-rotate-left', run: () => session.rotate('ccw') },
				{ id: 'rotate-r', label: 'Rotate right', icon: 'editor-rotate-right', run: () => session.rotate('cw') },
				{ id: 'canvas', label: 'Canvas size…', icon: 'editor-canvas', run: onCanvasSize },
				...(actions.length
					? [
							{ id: 'sep-actions', separator: true } as Row,
							{ id: 'head-actions', label: 'Paint actions', heading: true } as Row,
							...actions.map((action): Row => ({ id: `action:${action.id}`, label: action.label, icon: 'wand', run: () => runAction(action) }))
						]
					: [])
			]
		},
		{
			id: 'view',
			label: 'View',
			rows: [
				{ id: 'zoom-in', label: 'Zoom in', icon: 'plus', keys: '+', run: () => session.zoomBy(1.25) },
				{ id: 'zoom-out', label: 'Zoom out', icon: 'minus', keys: '-', run: () => session.zoomBy(1 / 1.25) },
				{ id: 'fit', label: 'Fit to view', icon: 'fit', keys: '0', run: () => session.fit() },
				{ id: 'sep-panel', separator: true },
				{ id: 'panel', label: panelHidden ? 'Show panel' : 'Hide panel', icon: 'sidebar-collapse', keys: 'P', run: onTogglePanel },
				{ id: 'full', label: fullScreen ? 'Exit full screen' : 'Full screen', icon: 'expand', keys: 'F', run: onToggleFull }
			]
		},
		{
			id: 'tools',
			label: 'Tools',
			rows: rail.flatMap((group, index): Row[] => [
				...(index > 0 ? [{ id: `sep-${group.id}`, separator: true } as Row] : []),
				{ id: `head-${group.id}`, label: group.label, heading: true },
				...group.tools.map((tool): Row => ({ id: `tool:${tool.id}`, label: tool.label, icon: tool.icon, keys: tool.keys, checked: tool.id === activeTool, run: () => onSelectTool(tool.id) }))
			])
		}
	]);
</script>

<div class="flex flex-none items-center gap-0.5" data-paint-menubar>
	{#each menus as menu (menu.id)}
		<Menu label={menu.label} menuLabel="{menu.label} menu" width={264} align="left" menuAttrs={{ 'data-paint-menu': menu.id }}>
			{#snippet trigger({ open, toggle })}
				<button
					type="button"
					class="h-8 rounded px-3 text-base text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline focus-visible:outline-1 focus-visible:outline-signal {open ? 'bg-surface-2 text-fg' : ''}"
					aria-haspopup="menu"
					aria-expanded={open}
					data-menu-tab={menu.id}
					onclick={toggle}
				>
					{menu.label}
				</button>
			{/snippet}
			{#snippet children({ close })}
				{#each menu.rows as row (row.id)}
					{#if row.separator}
						<div class="my-1 h-px bg-line" role="separator"></div>
					{:else if row.heading}
						<div class="px-2.5 pb-1 pt-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle" role="presentation">{row.label}</div>
					{:else}
						<button
							type="button"
							role={row.checked === undefined ? 'menuitem' : 'menuitemradio'}
							aria-checked={row.checked === undefined ? undefined : row.checked}
							disabled={row.disabled}
							class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-base hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 {row.checked ? 'text-signal' : 'text-fg'}"
							data-menu-row={row.id}
							onclick={() => {
								close(true);
								row.run?.();
							}}
						>
							<span class="grid h-4 w-4 flex-none place-items-center text-fg-muted">{#if row.icon}<Icon name={row.icon} className="h-4 w-4" />{/if}</span>
							<span class="min-w-0 flex-1 truncate">{row.label}</span>
							{#if row.keys}<span class="flex-none font-mono text-sm tabular-nums text-fg-subtle">{row.keys}</span>{/if}
							{#if row.checked}<Icon name="check" className="h-3.5 w-3.5 flex-none text-signal" />{/if}
						</button>
					{/if}
				{/each}
			{/snippet}
		</Menu>
	{/each}
	<span class="mx-1.5 h-5 w-px flex-none bg-line" aria-hidden="true"></span>
	<IconButton icon="editor-undo" label={snapshot.undoLabel ? `Undo ${snapshot.undoLabel}` : 'Undo'} kbd="{modifier}+Z" size="sm" disabled={!snapshot.canUndo} onclick={() => session.undo()} />
	<IconButton icon="editor-redo" label={snapshot.redoLabel ? `Redo ${snapshot.redoLabel}` : 'Redo'} kbd="{modifier}+Y" size="sm" disabled={!snapshot.canRedo} onclick={() => session.redo()} />
</div>
