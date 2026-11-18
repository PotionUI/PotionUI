<script lang="ts">
	import { Button, Kbd } from '$lib/components/ui';
	import { editorsRevision } from '$lib/editors/registry';
	import { tasks } from '$lib/editors/tasks';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { defaultRange, lengthOf, parseSeconds, withEnd, withStart } from '$lib/tool-tabs/video/aiRange';
	import type { VideoToolHost } from '$lib/tool-tabs/video/toolHost';
	import { formatEditorTime } from '$lib/tool-tabs/video/videoState';
	import { fixed2 } from '$lib/tool-tabs/video/videoSteps';

	let { host, phone = false, tool }: { host: VideoToolHost; phone?: boolean; tool?: string } = $props();

	let video = $derived(host.video);
	const generate = getGenerateController();
	const ui = generate?.ui;

	let taskId = $derived($ui?.taskId ?? tool ?? null);
	let entry = $derived.by(() => {
		void $editorsRevision;
		return taskId ? tasks.get('video', taskId) : undefined;
	});
	let extend = $derived(entry?.result === 'append');
	let range = $derived(video.aiRange);
	let duration = $derived(video.clip.duration);
	let chosen = $derived(taskId && $ui ? $ui.choice[taskId] : null);
	let tweak = $derived(taskId && $ui ? $ui.tweaks[taskId] : undefined);
	let seconds = $derived(typeof tweak?.values.duration === 'number' ? (tweak.values.duration as number) : null);
	let editing = $state<{ field: 'start' | 'end' | 'seconds'; text: string } | null>(null);

	$effect(() => {
		if (extend && taskId && chosen && generate) void generate.settings(taskId);
	});

	function shown(field: 'start' | 'end'): string {
		if (editing?.field === field) return editing.text;
		return range ? fixed2(field === 'start' ? range.start : range.end) : '';
	}

	function commit(field: 'start' | 'end' | 'seconds') {
		const typed = editing?.field === field ? editing.text : null;
		editing = null;
		if (typed === null) return;
		const value = parseSeconds(typed);
		if (value === null) return;
		if (field === 'seconds') {
			if (tweak && taskId && generate) {
				if (!tweak.settled) generate.tweak(taskId, { ...tweak.values });
				generate.tweak(taskId, { ...tweak.values, duration: Math.max(0.5, value) });
			}
		} else if (range) {
			video.setAiRange(field === 'start' ? withStart(range, value, duration) : withEnd(range, value, duration));
		}
	}

	function key(event: KeyboardEvent, field: 'start' | 'end' | 'seconds') {
		if (event.key === 'Enter') {
			event.preventDefault();
			event.stopPropagation();
			commit(field);
			(event.currentTarget as HTMLInputElement).blur();
		} else if (event.key === 'Escape') {
			editing = null;
			(event.currentTarget as HTMLInputElement).blur();
		}
	}

	function toggle(on: boolean) {
		video.setAiRange(on ? defaultRange(video.playhead, duration) : null);
	}

	function setStart() {
		const at = video.playhead;
		video.setAiRange(range ? withStart(range, at, duration) : defaultRange(at, duration));
	}

	function setEnd() {
		const at = video.playhead;
		video.setAiRange(range ? withEnd(range, at, duration) : defaultRange(Math.max(0, at - 2), duration));
	}

	let marker = $derived(range ? formatEditorTime(range.end) : formatEditorTime(duration));
</script>

<div class="flex flex-col gap-3" data-range-options>
	{#if extend}
		<div class="flex flex-col gap-2 rounded-lg border border-line bg-surface-1 p-3" data-extend-options>
			<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Add after</span>
			{#if seconds !== null}
				<label class="flex h-9 items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 focus-within:border-signal">
					<input
						class="min-w-0 flex-1 bg-transparent font-mono text-base tabular-nums text-fg outline-none"
						value={editing?.field === 'seconds' ? editing.text : String(seconds)}
						aria-label="Seconds to add"
						inputmode="decimal"
						onfocus={() => (editing = { field: 'seconds', text: String(seconds) })}
						oninput={(event) => (editing = { field: 'seconds', text: (event.currentTarget as HTMLInputElement).value })}
						onblur={() => commit('seconds')}
						onkeydown={(event) => key(event, 'seconds')}
						data-extend-seconds
					/>
					<span class="font-mono text-sm text-fg-subtle">s</span>
				</label>
			{/if}
			<p class="m-0 text-sm text-fg-muted" data-extend-note>
				{seconds === null ? 'New footage' : `${fixed2(seconds)} s of new footage`} is added after
				<span class="font-mono tabular-nums text-fg" data-extend-marker>{marker}</span>, the {range ? 'end of the marked range' : 'end of the clip'}.
			</p>
		</div>
	{/if}

	<div class="flex flex-col gap-2.5 rounded-lg border border-line bg-surface-1 p-3" data-range-card={range ? 'marked' : 'whole'}>
		<div class="flex items-center justify-between gap-2">
			<label class="flex min-w-0 items-center gap-2 text-base font-semibold text-fg">
				<input type="checkbox" class="h-4 w-4 flex-none rounded-sm border-line-strong bg-surface-2 accent-[rgb(var(--signal))]" checked={!!range} onchange={(event) => toggle((event.currentTarget as HTMLInputElement).checked)} data-range-toggle />
				<span class="truncate">{range ? 'Marked range' : 'Whole clip'}</span>
			</label>
			<span class="whitespace-nowrap font-mono text-sm tabular-nums text-fg-muted" data-range-length>{fixed2(lengthOf(range, duration))} s</span>
		</div>
		{#if range}
			<div class="grid grid-cols-2 gap-2">
				{#each ['start', 'end'] as const as field (field)}
					<label class="flex h-9 items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 focus-within:border-signal">
						<input
							class="min-w-0 flex-1 bg-transparent font-mono text-base tabular-nums text-fg outline-none"
							value={shown(field)}
							aria-label={field === 'start' ? 'Range start in seconds' : 'Range end in seconds'}
							inputmode="decimal"
							onfocus={() => (editing = { field, text: fixed2(field === 'start' ? range!.start : range!.end) })}
							oninput={(event) => (editing = { field, text: (event.currentTarget as HTMLInputElement).value })}
							onblur={() => commit(field)}
							onkeydown={(event) => key(event, field)}
							data-range-field={field}
						/>
						<span class="font-mono text-sm uppercase text-fg-subtle">{field === 'start' ? 'in' : 'out'}</span>
					</label>
				{/each}
			</div>
			{#if !phone}
				<div class="grid grid-cols-2 gap-2">
					<Button variant="secondary" size="sm" class="h-8" onclick={setStart} attrs={{ 'data-range-set': 'start' }}>Set start <Kbd keys="I" /></Button>
					<Button variant="secondary" size="sm" class="h-8" onclick={setEnd} attrs={{ 'data-range-set': 'end' }}>Set end <Kbd keys="O" /></Button>
				</div>
			{/if}
			<div class="flex items-center justify-between gap-2">
				<p class="m-0 text-sm text-fg-muted">{phone ? 'Drag on the timeline.' : 'Drag on the timeline, or set the ends at the playhead.'}</p>
				<Button variant="ghost" size="sm" icon="trash" onclick={() => video.setAiRange(null)} attrs={{ 'data-range-clear': '' }}>Clear range</Button>
			</div>
		{:else}
			<p class="m-0 text-sm text-fg-muted">Drag on the timeline to mark a part of the clip, or tick the box.</p>
		{/if}
	</div>
	{#if !phone}
		<p class="m-0 text-sm text-fg-muted" data-range-hint>
			{#if range}
				{extend ? 'The marked range is the part the new footage follows.' : 'Only the marked range is regenerated. The rest of the clip is kept as it is.'}
			{:else}
				{extend ? 'With nothing marked, the new footage follows the whole clip.' : 'With nothing marked, the whole clip is regenerated.'}
			{/if}
		</p>
	{/if}
</div>
