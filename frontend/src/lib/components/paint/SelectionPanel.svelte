<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';
	import type { PaintTool } from '$lib/paint/types';

	export let session: PaintSession;
	export let state: SessionSnapshot;
	export let tool: PaintTool | undefined;
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
		<Button variant="secondary" size="sm" disabled={!state.hasSelection} onclick={() => session.cutSelection()}>Cut</Button>
		<Button variant="secondary" size="sm" disabled={!state.hasSelection} onclick={() => session.copySelection()}>Copy</Button>
		<Button variant="secondary" size="sm" disabled={!state.hasClipboard} onclick={() => session.pasteClipboard()}>Paste</Button>
		<Button variant="secondary" size="sm" disabled={!state.hasSelection} onclick={() => session.deleteSelection()}>Delete</Button>
		<Button variant="secondary" size="sm" onclick={() => session.selectAll()}>Select all</Button>
		<Button variant="secondary" size="sm" disabled={!state.hasSelection} onclick={() => session.deselect()}>Deselect</Button>
	</div>
</div>
