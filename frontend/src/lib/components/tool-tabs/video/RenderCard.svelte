<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import TickProgress from '$lib/components/tool-tabs/frame/TickProgress.svelte';
	import { formatEditorTime } from '$lib/tool-tabs/video/videoState';

	let {
		percent,
		total,
		output,
		left = null,
		label = 'Saving as new',
		phone = false,
		cancelling = false,
		oncancel
	}: {
		percent: number;
		total: number;
		output: string;
		left?: number | null;
		label?: string;
		phone?: boolean;
		cancelling?: boolean;
		oncancel?: () => void;
	} = $props();

	let value = $derived(Math.max(0, Math.min(100, Math.round(percent))));
	let rendered = $derived((Math.max(0, total) * value) / 100);
	let leftText = $derived(left === null ? 'Working it out' : left < 1 ? 'Almost done' : `about ${Math.max(1, Math.round(left))} s`);
</script>

<section
	class={phone
		? 'flex flex-none flex-col gap-2.5 border-t border-line-strong bg-surface-1 px-3 pb-3 pt-2.5 shadow-[var(--shadow-dock)]'
		: 'm-3 mb-1 flex flex-none flex-col gap-2.5 rounded-lg border border-line-strong bg-surface-2 p-3'}
	aria-label="Render progress"
	data-render-card={phone ? 'phone' : 'panel'}
>
	<div class="flex items-center gap-2">
		<Icon name="editor-save" className="h-3.5 w-3.5 flex-none text-fg-muted" />
		<h2 class="min-w-0 flex-1 truncate text-base font-semibold text-fg">{label}</h2>
		<span class="font-mono text-sm tabular-nums text-signal" data-render-percent>{value}%</span>
	</div>
	<TickProgress percent={value} label={label} size="md" />
	<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
		<dt class="text-fg-muted">Rendered</dt>
		<dd class="text-right font-mono tabular-nums text-fg" data-render-rendered>{formatEditorTime(rendered)} of {formatEditorTime(total)}</dd>
		{#if !phone}
			<dt class="text-fg-muted">Output</dt>
			<dd class="text-right font-mono tabular-nums text-fg" data-render-output>{output}</dd>
		{/if}
		<dt class="text-fg-muted">Left</dt>
		<dd class="text-right font-mono tabular-nums text-fg" data-render-left>{leftText}</dd>
	</dl>
	{#if phone}
		<Button variant="secondary" size="sm" class="h-8 w-full" disabled={cancelling} onclick={() => oncancel?.()} attrs={{ 'data-render-cancel': '' }}>
			{cancelling ? 'Cancelling…' : 'Cancel'}
		</Button>
	{:else}
		<p class="text-sm text-fg-subtle">Runs in the background as one media-tools run. Leave the tab or keep editing.</p>
	{/if}
</section>
