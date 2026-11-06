<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton, Kbd, Menu } from '$lib/components/ui';
	import type { MenuItem } from '$lib/components/ui/Menu.svelte';
	import { shortcutFor } from '$lib/tool-tabs/frame/keymap';
	import type { ReplaceOption, SaveAction, SavingProgress, SendTarget, SourceInfo } from '$lib/tool-tabs/frame/types';
	import FrameSheet from './FrameSheet.svelte';
	import SaveMenuRows from './SaveMenuRows.svelte';
	import TickProgress from './TickProgress.svelte';

	let {
		fileName,
		emptyLabel = 'No picture',
		chips = [],
		source = null,
		canUndo = false,
		canRedo = false,
		undoLabel = null,
		redoLabel = null,
		compare = false,
		canCompare = true,
		canSave = true,
		saved = false,
		replace,
		sendTargets = [],
		newGeneration = false,
		newGenerationHint,
		downloadFormat,
		copyLabel,
		saving = null,
		saveLabel = null,
		menuRows,
		phone = false,
		saveMenuOpen = $bindable(false),
		moreItems = [],
		leading,
		replaceControl,
		bar,
		barActions,
		onundo,
		onredo,
		oncompare,
		onsave,
		oncancelsave,
		onmore
	}: {
		fileName: string | null;
		emptyLabel?: string;
		chips?: string[];
		source?: SourceInfo | null;
		canUndo?: boolean;
		canRedo?: boolean;
		undoLabel?: string | null;
		redoLabel?: string | null;
		compare?: boolean;
		canCompare?: boolean;
		canSave?: boolean;
		saved?: boolean;
		replace: ReplaceOption;
		sendTargets?: SendTarget[];
		newGeneration?: boolean;
		newGenerationHint?: string;
		downloadFormat?: string;
		copyLabel?: string;
		saving?: SavingProgress | null;
		saveLabel?: string | null;
		menuRows?: Snippet<[(action: SaveAction) => void]>;
		phone?: boolean;
		saveMenuOpen?: boolean;
		moreItems?: MenuItem[];
		leading?: Snippet;
		replaceControl?: Snippet;
		bar?: Snippet;
		barActions?: Snippet;
		onundo: () => void;
		onredo: () => void;
		oncompare: (next: boolean) => void;
		onsave: (action: SaveAction) => void;
		oncancelsave?: () => void;
		onmore?: (id: string) => void;
	} = $props();

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
	<div class="flex h-12 flex-none items-center gap-2 border-b border-line bg-surface-1 pl-4 pr-3" data-editor-toolbar="desktop">
		<div class="flex min-w-0 flex-1 items-center gap-2.5" data-toolbar-lead>
			{#if bar && fileName}
				<span class="sr-only" data-frame-file>{fileName}</span>
				{#if replaceControl}
					{@render replaceControl()}
				{/if}
				{#each chips as chip, i (i)}
					<span class="inline-flex h-6 flex-none items-center rounded border border-line-strong bg-surface-2 px-2 font-mono text-sm tabular-nums text-fg-muted">{chip}</span>
				{/each}
				<span class="h-5 w-px flex-none bg-line-strong" aria-hidden="true"></span>
				<div class="flex min-w-0 flex-1 items-center gap-2.5 overflow-x-auto [scrollbar-width:none]" data-options-bar>
					{@render bar()}
				</div>
			{:else}
				<span class="min-w-0 truncate font-mono text-base {fileName ? 'text-fg' : 'text-fg-subtle'}" data-frame-file>{fileName ?? emptyLabel}</span>
				{#if fileName && replaceControl}
					{@render replaceControl()}
				{/if}
				{#if fileName}
					{#each chips as chip, i (i)}
						<span class="inline-flex h-5 flex-none items-center rounded bg-surface-3 px-1.5 font-mono text-sm tabular-nums text-fg-muted">{chip}</span>
					{/each}
					{#if source}
						<span class="inline-flex min-w-0 items-center gap-1.5 text-sm text-fg-subtle">
							<Icon name={source.icon} className="h-3.5 w-3.5 flex-none" />
							<span class="truncate">{source.label}</span>
						</span>
					{/if}
				{/if}
			{/if}
		</div>
		{#if bar && fileName && barActions}
			<div class="flex flex-none items-center gap-2" data-options-actions>
				{@render barActions()}
			</div>
		{/if}
		{@render history()}
		<span class="h-5 w-px flex-none bg-line-strong" aria-hidden="true"></span>
		<Button
			variant="ghost"
			size="sm"
			class="h-7"
			icon="editor-compare"
			pressed={compare}
			disabled={!canCompare}
			onclick={() => oncompare(!compare)}
			attrs={{ 'data-frame-compare': '' }}
		>
			Before / after<Kbd keys={shortcutFor('compare')} />
		</Button>
		<span class="h-5 w-px flex-none bg-line-strong" aria-hidden="true"></span>
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
		{#if moreItems.length}
			<Menu label="More" icon="chat-more" items={moreItems} onselect={(id) => onmore?.(id)} />
		{/if}
	</div>
{/if}
