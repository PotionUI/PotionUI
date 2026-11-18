<script lang="ts">
	import { Button, Kbd } from '$lib/components/ui';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';
	import { formatEditorTime, parseEditorTime } from '$lib/tool-tabs/video/videoState';
	import { fixed2, readTrim } from '$lib/tool-tabs/video/videoSteps';

	let { host, phone = false }: { host: VideoToolHost; phone?: boolean } = $props();

	let video = $derived(host.video);
	let editing = $state<{ which: 'in' | 'out'; text: string } | null>(null);
	let trim = $derived(readTrim(video.panelParams('trim')));

	function shown(which: 'in' | 'out'): string {
		if (editing?.which === which) return editing.text;
		return formatEditorTime(which === 'in' ? trim.start : trim.end);
	}

	function commit(which: 'in' | 'out') {
		const text = editing?.which === which ? editing.text : null;
		editing = null;
		if (text === null) return;
		const value = parseEditorTime(text);
		if (value === null) return;
		const next = Math.max(0, Math.min(video.clip.duration, value));
		if (which === 'in') video.setIn(Math.min(next, trim.end - 0.05));
		else video.setOut(Math.max(next, trim.start + 0.05));
	}

	function key(event: KeyboardEvent, which: 'in' | 'out') {
		if (event.key === 'Enter') {
			event.preventDefault();
			event.stopPropagation();
			commit(which);
			(event.currentTarget as HTMLInputElement).blur();
		} else if (event.key === 'Escape') {
			editing = null;
			(event.currentTarget as HTMLInputElement).blur();
		}
	}
</script>

<div class="flex flex-col gap-2" data-trim-options>
	{#if !phone}
		<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">In and out</span>
	{/if}
	<div class="grid grid-cols-2 gap-2">
		{#each ['in', 'out'] as const as which (which)}
			<label class="flex h-9 items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 focus-within:border-signal">
				<input
					class="min-w-0 flex-1 bg-transparent font-mono text-base tabular-nums text-fg outline-none"
					value={shown(which)}
					aria-label={which === 'in' ? 'In point' : 'Out point'}
					inputmode="decimal"
					onfocus={() => (editing = { which, text: formatEditorTime(which === 'in' ? trim.start : trim.end) })}
					oninput={(event) => (editing = { which, text: (event.currentTarget as HTMLInputElement).value })}
					onblur={() => commit(which)}
					onkeydown={(event) => key(event, which)}
					data-trim-field={which}
				/>
				<span class="font-mono text-sm uppercase text-fg-subtle">{which}</span>
			</label>
		{/each}
	</div>
	{#if !phone}
		<div class="grid grid-cols-2 gap-2">
			<Button variant="secondary" size="sm" class="h-8" onclick={() => video.setIn(video.playhead)} attrs={{ 'data-trim-set': 'in' }}>Set in <Kbd keys="I" /></Button>
			<Button variant="secondary" size="sm" class="h-8" onclick={() => video.setOut(video.playhead)} attrs={{ 'data-trim-set': 'out' }}>Set out <Kbd keys="O" /></Button>
		</div>
	{/if}
</div>
{#if !phone}
	<div class="flex flex-col gap-1.5 border-b border-line pb-4 text-sm">
		<div class="flex items-center justify-between"><span class="text-fg-muted">Source</span><span class="font-mono tabular-nums text-fg">{fixed2(video.clip.duration)} s</span></div>
		<div class="flex items-center justify-between"><span class="text-fg-muted">Output</span><span class="font-mono tabular-nums text-success" data-trim-output>{fixed2(video.outputLength)} s</span></div>
	</div>
	<p class="text-sm text-fg-muted">Drag the white handles on the timeline, or set them at the playhead.</p>
{:else}
	<div class="flex items-center justify-between text-sm"><span class="text-fg-muted">Output</span><span class="font-mono tabular-nums text-success" data-trim-output>{fixed2(video.outputLength)} s</span></div>
	<p class="text-sm text-fg-muted">Drag the white handles on the timeline.</p>
{/if}
