<script lang="ts">
	import type { PaintSession, SessionSnapshot } from './session';
	import type { PaintTool } from './types';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let tool: PaintTool | undefined;

	const button =
		'h-8 px-3 rounded border border-line-strong bg-surface-2 text-xs text-fg-muted transition-colors hover:border-line-hover hover:text-fg disabled:opacity-40 disabled:pointer-events-none';
</script>

<div class="flex flex-col gap-3 p-3">
	<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">{tool?.label ?? 'Selection'}</p>
	{#if tool?.hint}
		<p class="text-xs leading-relaxed text-fg-subtle">{tool.hint}</p>
	{/if}

	<div class="flex items-baseline justify-between">
		<span class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Selection</span>
		<span class="font-mono text-xs tabular-nums text-fg-muted">
			{#if state.selectionSize}{state.selectionSize.width} × {state.selectionSize.height}{:else}none{/if}
		</span>
	</div>

	<div class="flex flex-wrap gap-1.5">
		<button type="button" class={button} disabled={!state.hasSelection} on:click={() => session.cutSelection()}>
			Cut
		</button>
		<button type="button" class={button} disabled={!state.hasSelection} on:click={() => session.copySelection()}>
			Copy
		</button>
		<button type="button" class={button} disabled={!state.hasClipboard} on:click={() => session.pasteClipboard()}>
			Paste
		</button>
		<button type="button" class={button} disabled={!state.hasSelection} on:click={() => session.deleteSelection()}>
			Delete
		</button>
		<button type="button" class={button} on:click={() => session.selectAll()}>Select all</button>
		<button type="button" class={button} disabled={!state.hasSelection} on:click={() => session.deselect()}>
			Deselect
		</button>
	</div>
</div>
