<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Kbd } from '$lib/components/ui';
	import { shortcutFor } from '$lib/tool-tabs/frame/keymap';
	import type { ReplaceOption, SaveAction, SendTarget } from '$lib/tool-tabs/frame/types';

	let {
		saved = false,
		replace,
		sendTargets = [],
		newGeneration = false,
		newGenerationHint = 'Pick a preset with a matching field',
		downloadFormat,
		copyLabel = 'Copy image',
		onchoose
	}: {
		saved?: boolean;
		replace: ReplaceOption;
		sendTargets?: SendTarget[];
		newGeneration?: boolean;
		newGenerationHint?: string;
		downloadFormat?: string;
		copyLabel?: string;
		onchoose: (action: SaveAction) => void;
	} = $props();

	const row =
		'flex min-h-9 w-full items-center gap-2.5 rounded px-2.5 py-1.5 text-left text-base text-fg hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent';
	const sub = 'block text-sm text-fg-subtle';
	const icon = 'h-4 w-4 flex-none text-fg-muted';
	const saveKeys = shortcutFor('save');
</script>

{#if saved}
	<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'save' })} data-save-row="save">
		<Icon name="check" className={icon} />
		<span class="min-w-0 flex-1">Save<span class={sub}>Updates your saved copy</span></span>
		<Kbd keys={saveKeys} />
	</button>
{/if}
<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'save-new' })} data-save-row="save-new">
	<Icon name={saved ? 'plus' : 'check'} className={icon} />
	<span class="min-w-0 flex-1">Save as new<span class={sub}>New Library item, original kept</span></span>
	{#if !saved}<Kbd keys={saveKeys} />{/if}
</button>
<button
	type="button"
	role="menuitem"
	class={row}
	disabled={!replace.allowed}
	aria-disabled={!replace.allowed}
	onclick={() => onchoose({ kind: 'replace' })}
	data-save-row="replace"
>
	<Icon name="editor-replace" className={icon} />
	<span class="min-w-0 flex-1"
		>Replace original<span class={sub}
			>{replace.allowed ? 'Overwrites the Library item' : (replace.reason ?? 'Not available for this file')}</span
		></span
	>
</button>

{#if sendTargets.length || newGeneration}
	<div class="my-1 h-px bg-line" role="separator"></div>
	<div class="px-2.5 pb-1 pt-2 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle" role="presentation">
		Send to a generation
	</div>
	{#each sendTargets as target (target.id)}
		<button
			type="button"
			role="menuitem"
			class={row}
			onclick={() => onchoose({ kind: 'send', target: target.id })}
			data-save-row="send"
		>
			<Icon name="editor-send" className={icon} />
			<span class="min-w-0 flex-1"><span class="block truncate">{target.name}</span><span class="{sub} truncate">{target.detail}</span></span>
			{#if target.badge}
				<span class="rounded bg-surface-3 px-1.5 font-mono text-sm text-fg-muted">{target.badge}</span>
			{:else if target.color}
				<span class="h-[7px] w-[7px] flex-none rounded-full" style="background: {target.color}" aria-hidden="true"></span>
			{/if}
		</button>
	{/each}
	{#if newGeneration}
		<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'new-generation' })} data-save-row="new-generation">
			<Icon name="plus" className={icon} />
			<span class="min-w-0 flex-1">New generation tab…<span class={sub}>{newGenerationHint}</span></span>
		</button>
	{/if}
{/if}

<div class="my-1 h-px bg-line" role="separator"></div>
<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'download' })} data-save-row="download">
	<Icon name="download" className={icon} />
	<span class="min-w-0 flex-1">Download</span>
	{#if downloadFormat}<span class="font-mono text-sm text-fg-subtle">{downloadFormat}</span>{/if}
</button>
<button type="button" role="menuitem" class={row} onclick={() => onchoose({ kind: 'copy' })} data-save-row="copy">
	<Icon name="copy" className={icon} />
	<span class="min-w-0 flex-1">{copyLabel}</span>
</button>
