<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Kbd } from '$lib/components/ui';
	import { shortcutFor } from '$lib/tool-tabs/frame/keymap';
	import type { SaveAction } from '$lib/tool-tabs/frame/types';
	import type { ReferenceTarget } from '$lib/tool-tabs/image/slice/references';

	let {
		count,
		destination,
		targets = [],
		referencesOnly = false,
		canSaveSheet = true,
		onchoose
	}: {
		count: number;
		destination: string;
		targets?: ReferenceTarget[];
		referencesOnly?: boolean;
		canSaveSheet?: boolean;
		onchoose: (action: SaveAction) => void;
	} = $props();

	const row =
		'flex min-h-9 w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-base text-fg hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent';
	const sub = 'block text-sm text-fg-subtle';
	const icon = 'h-4 w-4 flex-none text-fg-muted';
	const images = (n: number) => `${n} ${n === 1 ? 'image' : 'images'}`;
</script>

{#if !referencesOnly}
	<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'save-new' })} data-save-row="slice-save">
		<Icon name="editor-slice" className={icon} />
		<span class="min-w-0 flex-1">Save {images(count)}<span class={sub}>New Library items in {destination}, sheet kept</span></span>
		<Kbd keys={shortcutFor('save')} />
	</button>
	<button type="button" role="menuitem" class={row} disabled={!canSaveSheet} onclick={() => onchoose({ kind: 'save-sheet' })} data-save-row="sheet-save">
		<Icon name="check" className={icon} />
		<span class="min-w-0 flex-1">Save the sheet as new<span class={sub}>One item, Slice left out</span></span>
	</button>
	<button type="button" role="menuitem" class={row} disabled aria-disabled="true" data-save-row="replace">
		<Icon name="editor-replace" className={icon} />
		<span class="min-w-0 flex-1">Replace original<span class={sub}>Slicing never overwrites the sheet</span></span>
	</button>
	<div class="my-1 h-px bg-line" role="separator"></div>
{/if}
<div class="px-2.5 pb-1 pt-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle" role="presentation">{referencesOnly ? 'Use as references' : 'Save and use as references'}</div>
{#each targets as target (target.id)}
	<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'send', target: target.id })} data-save-row="references">
		<Icon name="editor-send" className={icon} />
		<span class="min-w-0 flex-1"><span class="block truncate">{target.name}</span><span class="{sub} truncate">{target.detail}</span></span>
		{#if target.color}
			<span class="h-[7px] w-[7px] flex-none rounded-full" style="background: {target.color}" aria-hidden="true"></span>
		{/if}
	</button>
{/each}
<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'new-generation' })} data-save-row="new-generation">
	<Icon name="plus" className={icon} />
	<span class="min-w-0 flex-1">New generation tab…<span class={sub}>Pick a preset with a references field</span></span>
</button>
{#if !referencesOnly}
	<div class="my-1 h-px bg-line" role="separator"></div>
	<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'download' })} data-save-row="download">
		<Icon name="download" className={icon} />
		<span class="min-w-0 flex-1">Download {images(count)}</span>
		<span class="font-mono text-sm text-fg-subtle">ZIP</span>
	</button>
{/if}
