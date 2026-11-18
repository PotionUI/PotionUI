<script lang="ts">
	import { CopyButton } from '$lib/components/ui';
	import { middleEllipsis } from './idDisplay';

	let {
		id,
		preset,
		created,
		timeLabel,
		time,
		queued = null,
		live = false
	}: {
		id: string;
		preset: string;
		created: string;
		timeLabel: string;
		time: string;
		queued?: string | null;
		live?: boolean;
	} = $props();
</script>

<dl class="m-0 divide-y divide-line px-3 pb-2" data-live-info>
	<div class="flex items-center justify-between gap-3 py-2">
		<dt class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">ID</dt>
		<dd class="m-0 flex items-center gap-1 font-mono text-sm tabular-nums text-fg">
			{middleEllipsis(id, 12)}
			<CopyButton text={id} ariaLabel="Copy ID" size="xs" />
		</dd>
	</div>
	<div class="flex items-center justify-between gap-3 py-2">
		<dt class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">Preset</dt>
		<dd class="m-0 min-w-0 truncate text-sm text-fg">{preset}</dd>
	</div>
	<div class="flex items-center justify-between gap-3 py-2">
		<dt class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">Created</dt>
		<dd class="m-0 font-mono text-sm tabular-nums text-fg">{created}</dd>
	</div>
	<div class="flex items-center justify-between gap-3 py-2">
		<dt class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">{timeLabel}</dt>
		<dd class="m-0 font-mono text-sm tabular-nums text-fg" data-live-time>
			<span class={live ? 'text-signal' : ''}>{time}</span>{#if queued}<span class="text-fg-muted">{` · queued ${queued}`}</span>{/if}
		</dd>
	</div>
</dl>
