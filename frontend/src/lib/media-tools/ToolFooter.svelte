<script lang="ts">
	import Button from '$lib/components/ui/Button.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import { NEW_COPY_NOTE } from './newCopyNote';
	import type { MediaToolOutputMode } from './types';

	let {
		mode,
		showModes,
		note,
		applyLabel,
		applyIcon = true,
		flush = false,
		cancellable = true,
		disabled,
		working,
		progress,
		statusLabel,
		cancelling,
		failure,
		blocked,
		reserveMessage = false,
		onMode,
		onCancel,
		onApply,
		onCancelRun
	}: {
		mode: MediaToolOutputMode;
		showModes: boolean;
		note: string | null;
		applyLabel: string;
		applyIcon?: boolean;
		flush?: boolean;
		cancellable?: boolean;
		disabled: boolean;
		working: boolean;
		progress: number;
		statusLabel: string | null;
		cancelling: boolean;
		failure: string | null;
		blocked: string | null;
		reserveMessage?: boolean;
		onMode: (mode: MediaToolOutputMode) => void;
		onCancel: () => void;
		onApply: () => void;
		onCancelRun: () => void;
	} = $props();

	const MODES = [
		{ id: 'new', label: 'Save as new' },
		{ id: 'replace', label: 'Replace original' }
	];

	let percent = $derived(Math.round(Math.min(1, Math.max(0, progress)) * 100));
</script>

<div class="flex flex-col gap-2 {flush ? 'w-full' : 'bg-surface-1 px-4 py-3'}" data-tool-footer>
	{#if failure}
		<p class="text-sm text-danger" role="alert" data-tool-failure>{failure}</p>
	{:else if blocked && !working}
		<p class="text-sm text-warning" data-tool-blocked>{blocked}</p>
	{:else if reserveMessage}
		<p class="invisible text-sm" aria-hidden="true" data-tool-message-space>&nbsp;</p>
	{/if}

	<div class="flex flex-wrap items-center gap-2">
		{#if working && !cancellable}
			<span class="inline-flex items-center gap-2 text-sm text-fg-muted" data-tool-working>
				<Spinner size="sm" />
				Working…
			</span>
			<div class="ml-auto flex items-center gap-2">
				<Button variant="ghost" size="sm" disabled onclick={onCancel}>Cancel</Button>
			</div>
		{:else if working}
			<div class="flex min-w-0 flex-1 items-center gap-3" data-tool-progress>
				<span
					class="relative h-1.5 min-w-24 flex-1 overflow-hidden rounded bg-surface-3"
					role="progressbar"
					aria-label="Progress"
					aria-valuemin="0"
					aria-valuemax="100"
					aria-valuenow={percent}
				>
					<span
						class="absolute inset-y-0 left-0 bg-signal transition-[width] duration-200"
						style="width: {percent}%;"
					></span>
				</span>
				<span class="shrink-0 font-mono text-sm tabular-nums text-fg-muted">
					{statusLabel ? `${statusLabel} · ` : ''}{percent}%
				</span>
			</div>
			<Button variant="secondary" size="sm" disabled={cancelling} onclick={onCancelRun}>
				{cancelling ? 'Cancelling' : 'Cancel edit'}
			</Button>
		{:else}
			{#if showModes}
				<SegmentedControl
					variant="toggle"
					ariaLabel="Save as"
					items={MODES}
					selected={mode}
					onSelect={(id) => onMode(id === 'replace' ? 'replace' : 'new')}
				/>
				{#if mode === 'new'}
					<p class="min-w-0 flex-1 basis-48 text-sm text-fg-muted" data-tool-note>{NEW_COPY_NOTE}</p>
				{/if}
			{:else if note}
				<p class="min-w-0 flex-1 basis-48 text-sm text-fg-muted" data-tool-note>{note}</p>
			{/if}
			<div class="ml-auto flex items-center gap-2">
				<Button variant="ghost" size="sm" onclick={onCancel}>Cancel</Button>
				<Button
					variant="primary"
					size="sm"
					icon={applyIcon ? 'check' : undefined}
					{disabled}
					onclick={onApply}
				>
					{applyLabel}
				</Button>
			</div>
		{/if}
	</div>
</div>
