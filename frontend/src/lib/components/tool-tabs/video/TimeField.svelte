<script lang="ts">
	import { formatEditorTime, parseEditorTime } from '$lib/tool-tabs/video/videoState';

	let {
		which,
		value,
		min = 0,
		max,
		compact = false,
		oncommit
	}: {
		which: 'in' | 'out';
		value: number;
		min?: number;
		max: number;
		compact?: boolean;
		oncommit: (which: 'in' | 'out', seconds: number) => void;
	} = $props();

	let editing = $state<string | null>(null);

	let text = $derived(editing ?? formatEditorTime(value));

	function commit() {
		const typed = editing;
		editing = null;
		if (typed === null) return;
		const parsed = parseEditorTime(typed);
		if (parsed === null) return;
		oncommit(which, Math.max(min, Math.min(max, parsed)));
	}

	function key(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			event.stopPropagation();
			commit();
			(event.currentTarget as HTMLInputElement).blur();
		} else if (event.key === 'Escape') {
			editing = null;
			(event.currentTarget as HTMLInputElement).blur();
		}
	}
</script>

<label class="flex flex-none items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 focus-within:border-signal {compact ? 'h-7' : 'h-9'}" data-time-field={which}>
	<span class="font-mono text-sm uppercase text-fg-subtle">{which}</span>
	<input
		class="min-w-0 bg-transparent font-mono tabular-nums text-fg outline-none {compact ? 'w-[88px] text-sm' : 'w-full flex-1 text-base'}"
		value={text}
		aria-label={which === 'in' ? 'In point' : 'Out point'}
		inputmode="decimal"
		onfocus={() => (editing = formatEditorTime(value))}
		oninput={(event) => (editing = (event.currentTarget as HTMLInputElement).value)}
		onblur={commit}
		onkeydown={key}
		data-bar-time={which}
	/>
</label>
