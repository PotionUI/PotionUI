<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton, Menu } from '$lib/components/ui';
	import type { EditorMenuContext } from '$lib/editors/menuItems';
	import { viewportWidth } from '$lib/stores/viewport';
	import { getFrameContext } from '$lib/tool-tabs/frame/context';
	import { frameTitleWidth } from '$lib/tool-tabs/frame/layout';
	import { shortcutFor } from '$lib/tool-tabs/frame/keymap';
	import type { ReplaceOption, SaveAction, SavingProgress, SendTarget } from '$lib/tool-tabs/frame/types';
	import EditorMenuBar from './EditorMenuBar.svelte';
	import FrameSheet from './FrameSheet.svelte';
	import SaveMenuRows from './SaveMenuRows.svelte';
	import TickProgress from './TickProgress.svelte';

	let {
		fileName,
		emptyLabel = 'No picture',
		title = 'Editor',
		titleIcon = 'sliders',
		menu,
		canUndo = false,
		canRedo = false,
		undoLabel = null,
		redoLabel = null,
		canSave = true,
		saved = false,
		replace,
		sendTargets = [],
		saveNotices = [],
		newGeneration = false,
		newGenerationHint,
		downloadFormat,
		copyLabel,
		saving = null,
		saveLabel = null,
		menuRows,
		phone = false,
		saveMenuOpen = $bindable(false),
		leading,
		saveState,
		childFooter,
		onopenmenu,
		onundo,
		onredo,
		onsave,
		oncancelsave
	}: {
		fileName: string | null;
		emptyLabel?: string;
		title?: string;
		titleIcon?: string;
		menu?: EditorMenuContext;
		canUndo?: boolean;
		canRedo?: boolean;
		undoLabel?: string | null;
		redoLabel?: string | null;
		canSave?: boolean;
		saved?: boolean;
		replace: ReplaceOption;
		sendTargets?: SendTarget[];
		saveNotices?: string[];
		newGeneration?: boolean;
		newGenerationHint?: string;
		downloadFormat?: string;
		copyLabel?: string;
		saving?: SavingProgress | null;
		saveLabel?: string | null;
		menuRows?: Snippet<[(action: SaveAction) => void]>;
		phone?: boolean;
		saveMenuOpen?: boolean;
		leading?: Snippet;
		saveState?: Snippet;
		childFooter?: Snippet<[{ id: string; close: () => void }]>;
		onopenmenu?: (id: string) => void;
		onundo: () => void;
		onredo: () => void;
		onsave: (action: SaveAction) => void;
		oncancelsave?: () => void;
	} = $props();

	const frame = getFrameContext();
	let titleWidth = $derived(frameTitleWidth($viewportWidth, frame?.panelSide ?? 'left'));

	let primaryLabel = $derived(saveLabel ?? (saved ? 'Save' : 'Save as new'));
	let percent = $derived(saving ? Math.max(0, Math.min(100, Math.round(saving.percent))) : 0);
	let undoText = $derived(undoLabel ? `Undo ${undoLabel}` : 'Undo');
	let redoText = $derived(redoLabel ? `Redo ${redoLabel}` : 'Redo');

	function chooseFromSheet(action: SaveAction) {
		saveMenuOpen = false;
		onsave(action);
	}

	function primary() {
		onsave(saved ? { kind: 'save' } : { kind: 'save-new' });
	}
</script>

{#snippet rows(pick: (action: SaveAction) => void)}
	{#if menuRows}
		{@render menuRows(pick)}
	{:else}
		<SaveMenuRows
			{saved}
			{replace}
			{sendTargets}
			{newGeneration}
			{newGenerationHint}
			{downloadFormat}
			{copyLabel}
			notices={saveNotices}
			onchoose={pick}
		/>
	{/if}
{/snippet}

{#snippet history()}
	<Tooltip text={undoText} kbd={shortcutFor('undo')} position="bottom">
		<IconButton icon="editor-undo" label={undoText} size="sm" disabled={!canUndo} onclick={onundo} data-frame-undo />
	</Tooltip>
	<Tooltip text={redoText} kbd={shortcutFor('redo')} position="bottom">
		<IconButton icon="editor-redo" label={redoText} size="sm" disabled={!canRedo} onclick={onredo} data-frame-redo />
	</Tooltip>
{/snippet}

{#if phone}
	<div class="flex h-[52px] flex-none items-center gap-1.5 border-b border-line bg-surface-1 pl-2.5 pr-2" data-editor-toolbar="phone">
		<div class="flex min-w-0 flex-1 items-center">
			{#if leading}
				{@render leading()}
			{:else}
				<span class="truncate font-mono text-base {fileName ? 'text-fg' : 'text-fg-subtle'}">{fileName ?? emptyLabel}</span>
			{/if}
		</div>
		{@render history()}
		{#if saving}
			<span
				class="inline-flex h-8 items-center rounded bg-signal/10 px-2 font-mono text-sm tabular-nums text-signal"
				role="status"
				aria-label="Saving {percent}%"
				data-frame-saving
			>{percent}%</span>
		{:else}
			<Button
				variant="primary"
				size="sm"
				class="h-8"
				disabled={!canSave}
				ariaHaspopup="dialog"
				ariaExpanded={saveMenuOpen}
				onclick={() => (saveMenuOpen = true)}
				attrs={{ 'data-frame-save': '' }}
			>
				{saveLabel ?? 'Save'}<Icon name="chevron-down" className="h-3.5 w-3.5" />
			</Button>
		{/if}
	</div>
	{#if saveMenuOpen}
		<FrameSheet label="Save" onclose={() => (saveMenuOpen = false)}>
			{#snippet header()}
				<span class="text-lg font-semibold text-fg">Save</span>
				<span class="flex-1"></span>
				{#if fileName}
					<span class="truncate font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{fileName}</span>
				{/if}
			{/snippet}
			<div role="menu" aria-label="Save" class="flex flex-col">
				{@render rows(chooseFromSheet)}
			</div>
		</FrameSheet>
	{/if}
{:else}
	<div class="flex h-[49px] flex-none items-stretch border-b border-line bg-surface-1" data-editor-toolbar="desktop">
		<div
			class="flex flex-none items-center gap-2 overflow-hidden border-r border-line bg-surface-1 pl-4 pr-2"
			style="width: {titleWidth}px"
			data-title-block
		>
			<Icon name={titleIcon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
			<span class="min-w-0 truncate text-base font-semibold text-fg" data-title-name>{title}</span>
			<span class="sr-only" data-frame-file>{fileName ?? emptyLabel}</span>
		</div>
		{#if menu}
			<EditorMenuBar ctx={menu} {onopenmenu} {childFooter} />
		{/if}
		<span class="min-w-2 flex-1"></span>
		{#if saveState}
			<div class="flex flex-none items-center pl-3 pr-1" data-save-state-slot>
				{@render saveState()}
			</div>
		{/if}
		<div class="flex flex-none items-center gap-2 pr-4">
			{@render history()}
			{#if saving}
				<span
					class="inline-flex h-8 flex-none items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5"
					data-frame-saving
				>
					{#if saving.caption}<span class="whitespace-nowrap text-sm text-fg" data-frame-saving-caption>{saving.caption}</span>{/if}
					<TickProgress percent={saving.percent} label={saving.label ?? 'Saving'} />
					<span class="font-mono text-sm tabular-nums text-fg">{percent}%</span>
				</span>
				<Button variant="secondary" size="sm" class="h-7" onclick={() => oncancelsave?.()} attrs={{ 'data-frame-cancel-save': '' }}>Cancel</Button>
			{:else}
				<span class="inline-flex flex-none" data-frame-save-split>
					<Button
						variant="primary"
						size="sm"
						class="h-7 rounded-r-none"
						icon="check"
						disabled={!canSave}
						onclick={primary}
						attrs={{ 'data-frame-save': '' }}
					>
						{primaryLabel}
					</Button>
					<Menu label="More save options" bind:open={saveMenuOpen} width={340} align="right">
						{#snippet trigger({ open, toggle })}
							<Button
								variant="primary"
								size="sm"
								class="ml-px h-7 rounded-l-none !px-2"
								icon="chevron-down"
								disabled={!canSave}
								ariaLabel="More save options"
								ariaHaspopup="menu"
								ariaExpanded={open}
								onclick={toggle}
								attrs={{ 'data-frame-save-menu': '' }}
							/>
						{/snippet}
						{#snippet children({ close })}
							{@render rows((action) => {
								close(true);
								onsave(action);
							})}
						{/snippet}
					</Menu>
				</span>
			{/if}
		</div>
	</div>
{/if}
