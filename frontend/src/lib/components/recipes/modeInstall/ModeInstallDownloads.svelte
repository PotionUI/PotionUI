<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { ModeDownloadProgress } from '$lib/services/api/recipeModes';
	import {
		aggregateDownloadBytes,
		downloadFileLabel,
		fileProgressPercent,
		formatEtaLeft,
		formatSpeed,
		sizeLabel
	} from './modeInstallModel';

	let {
		progress,
		cancelBusy = false,
		cancelError = '',
		onCancel
	}: {
		progress: ModeDownloadProgress;
		cancelBusy?: boolean;
		cancelError?: string;
		onCancel: () => void;
	} = $props();

	const totals = $derived(aggregateDownloadBytes(progress));
	const percent = $derived(totals.total > 0 ? Math.min(100, (totals.done / totals.total) * 100) : 0);
	const speedLine = $derived.by(() => {
		const parts: string[] = [];
		if (progress.speed_bytes_per_sec) parts.push(formatSpeed(progress.speed_bytes_per_sec));
		if (progress.eta_seconds != null) parts.push(formatEtaLeft(progress.eta_seconds));
		return parts.join(' · ');
	});
</script>

<div class="rounded-lg border border-line bg-surface-1 p-5" data-mode-downloads>
	<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
		<div class="font-mono tabular-nums">
			<span class="text-xl text-fg">{sizeLabel(totals.done)}</span>
			<span class="ml-1 text-sm text-fg-muted">of {sizeLabel(totals.total)}</span>
		</div>
		{#if speedLine}
			<span class="font-mono text-sm tabular-nums text-fg-muted" data-download-speed>{speedLine}</span>
		{/if}
	</div>
	<div
		class="mt-3 h-1.5 overflow-hidden rounded-sm bg-surface-3"
		role="progressbar"
		aria-valuemin="0"
		aria-valuemax="100"
		aria-valuenow={Math.round(percent)}
		aria-label="Download progress"
	>
		<div class="h-full bg-signal-solid transition-all duration-300" style="width: {percent}%"></div>
	</div>

	<ul class="mt-3 divide-y divide-line">
		{#each progress.files as file (file.slot_id)}
			<li class="grid grid-cols-[minmax(0,1fr)_minmax(0,160px)_56px] items-center gap-4 py-2.5 max-sm:grid-cols-[minmax(0,1fr)_56px]" data-download-file={file.slot_id}>
				<div class="min-w-0">
					<div class="truncate text-base text-fg">{file.display_name}</div>
					{#if file.size_bytes != null}
						<div class="font-mono text-sm tabular-nums text-fg-muted">{sizeLabel(file.size_bytes)}</div>
					{/if}
				</div>
				<div class="h-1 overflow-hidden rounded-sm bg-surface-3 max-sm:hidden">
					<div
						class="h-full bg-signal-solid transition-all duration-300"
						style="width: {file.status === 'completed' ? 100 : fileProgressPercent(file)}%"
					></div>
				</div>
				<span
					class="text-right font-mono text-sm tabular-nums {file.status === 'completed'
						? 'text-success'
						: file.status === 'failed'
							? 'text-danger'
							: 'text-fg-muted'}"
				>
					{downloadFileLabel(file)}
				</span>
			</li>
		{/each}
	</ul>

	<div class="mt-2 flex items-center justify-between gap-4 border-t border-line pt-4 max-sm:flex-col max-sm:items-start">
		<p class="text-sm text-fg-muted">
			Two files at a time. If you cancel, finished files stay and the rest resume next time.
		</p>
		<Button variant="secondary" size="sm" loading={cancelBusy} onclick={onCancel}>Cancel download</Button>
	</div>
	{#if cancelError}
		<p class="mt-2 text-sm text-danger">{cancelError}</p>
	{/if}
</div>
