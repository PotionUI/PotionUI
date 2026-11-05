<script lang="ts">
	import { Button } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { elapsedOf, type ErrorGroup, type NodeRunView } from '$lib/elixirs/runs';

	interface Props {
		view?: NodeRunView | null;
		idle?: boolean;
		label?: string | null;
		tone?: string | null;
		stale?: boolean;
		staleReason?: string;
		rerunBlocked?: string | null;
		onrerun?: (() => void) | null;
		errors?: ErrorGroup[];
		canRetry?: boolean;
		busy?: boolean;
		onretry?: () => void;
	}

	let {
		view = null,
		idle = false,
		label = null,
		tone: forcedTone = null,
		stale = false,
		staleReason = '',
		rerunBlocked = null,
		onrerun,
		errors = [],
		canRetry = false,
		busy = false,
		onretry
	}: Props = $props();

	let now = $state(Date.now());

	$effect(() => {
		if (!view || view.finishedAt || !['running', 'queued'].includes(view.status)) return;
		now = Date.now();
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});

	const TONES: Record<string, string> = {
		danger: 'text-danger',
		signal: 'text-signal',
		success: 'text-success',
		warning: 'text-warning',
		neutral: 'text-fg-muted'
	};
	const DOTS: Record<string, string> = {
		danger: 'bg-danger',
		signal: 'bg-signal',
		success: 'bg-success',
		warning: 'bg-warning',
		neutral: 'bg-fg-subtle'
	};

	let active = $derived(!!view && ['running', 'queued'].includes(view.status));
	let showStale = $derived(stale && !active);
	let running = $derived(view?.status === 'running');
	let elapsed = $derived(view && ['running', 'queued', 'done', 'choose', 'failed'].includes(view.status) ? elapsedOf(view, now) : '');
	let tone = $derived(showStale ? 'warning' : forcedTone ?? (view ? view.tone : 'neutral'));
	let text = $derived(showStale ? 'Needs rerun' : label ?? (view ? view.label : 'Not brewed yet'));
	let pulse = $derived(running || view?.status === 'choose');
</script>

{#if view || idle || showStale}
	<div class="status-row">
		<span class="dot {DOTS[tone]}" class:pulse aria-hidden="true"></span>
		<span class="min-w-0 flex-1 {TONES[tone]}" role="status" aria-live="polite">{text}</span>
		{#if elapsed}
			<span class="shrink-0 font-mono tabular-nums text-fg-subtle">{elapsed}</span>
		{/if}
		{#if running && view?.progress != null}
			<span class="shrink-0 font-mono tabular-nums text-signal" data-testid="node-progress-percent">{view.progress}%</span>
		{/if}
		{#if running}
			<div class="progress" role="progressbar" aria-label="Progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow={view?.progress ?? 0}>
				<div class="h-full bg-signal transition-[width]" style="width: {view?.progress ?? 0}%"></div>
			</div>
		{/if}
		{#if showStale}
			<p class="sub">{staleReason}</p>
			{#if onrerun}
				<div class="acts">
					<Tooltip text={rerunBlocked ?? 'Run this step again with the newer outputs'}>
						<Button variant="secondary" size="sm" icon="refresh" disabled={!!rerunBlocked} onclick={onrerun}>Rerun</Button>
					</Tooltip>
				</div>
			{/if}
		{:else if errors.length > 0}
			<div class="sub flex flex-col gap-1" role="alert">
				{#each errors as error (error.message)}
					<p>
						{error.message}
						{#if error.count > 1}
							<span class="font-mono tabular-nums text-fg-subtle">· {error.count} generations</span>
						{/if}
					</p>
				{/each}
			</div>
			{#if canRetry}
				<div class="acts">
					<Button variant="secondary" size="sm" icon="refresh" loading={busy} onclick={onretry}>Retry failed</Button>
				</div>
			{/if}
		{/if}
	</div>
{/if}

<style>
	.status-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 10px;
		padding: 14px 16px;
		border-top: 1px solid rgb(var(--line));
		font-size: 12px;
		line-height: 16px;
	}
	.dot {
		width: 8px;
		height: 8px;
		flex: none;
		border-radius: 9999px;
	}
	.progress {
		flex-basis: 100%;
		height: 4px;
		overflow: hidden;
		border-radius: 4px;
		background: rgb(var(--surface-3));
	}
	.sub {
		flex-basis: 100%;
		color: rgb(var(--fg-muted));
	}
	.acts {
		flex-basis: 100%;
		display: flex;
		gap: 6px;
	}
	.pulse {
		animation: pulse 1.6s ease-in-out infinite;
	}
	@keyframes pulse {
		50% {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.pulse {
			animation: none;
		}
	}
</style>
