<script lang="ts">
	import { onDestroy, onMount, tick } from 'svelte';
	import { logger } from '$lib/utils/logger';
	import * as adminApi from '$lib/services/admin-api';
	import type { LogLevel, LogTail } from '$lib/services/admin-api';
	import { Alert, Button, CopyButton, EmptyState, IconButton, SegmentedControl, Spinner, Switch } from '$lib/components/ui';
	import { DetailSection } from '$lib/components/detail';
	import { formatBytes } from '$lib/utils/format';

	const LEVEL_ITEMS = [
		{ id: 'DEBUG', label: 'Debug' },
		{ id: 'INFO', label: 'Info' },
		{ id: 'WARNING', label: 'Warning' },
		{ id: 'ERROR', label: 'Error' }
	];
	const LINES_ITEMS = [
		{ id: '200', label: '200' },
		{ id: '500', label: '500' },
		{ id: '2000', label: '2000' }
	];
	const LEVEL_TOKEN: Record<string, string> = {
		ERROR: 'text-danger',
		WARNING: 'text-warning',
		INFO: 'text-info',
		DEBUG: 'text-fg-muted'
	};
	const POLL_MS = 3000;
	const AT_BOTTOM_SLOP_PX = 40;

	let level = $state<LogLevel>('INFO');
	let linesWanted = $state('500');
	let autoFollow = $state(true);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let result = $state<LogTail | null>(null);
	// True once the user has scrolled away from the bottom - independent of
	// the auto-follow switch, which instead gates the poll itself.
	let scrolledUp = $state(false);
	let hasNewSincePause = $state(false);

	let listEl = $state<HTMLDivElement | undefined>();
	let pollHandle: ReturnType<typeof setInterval> | undefined;

	onMount(() => {
		load();
		syncPolling();
		document.addEventListener('visibilitychange', syncPolling);
	});

	onDestroy(() => {
		stopPolling();
		document.removeEventListener('visibilitychange', syncPolling);
	});

	$effect(() => {
		autoFollow;
		syncPolling();
	});

	function syncPolling() {
		stopPolling();
		if (autoFollow && document.visibilityState === 'visible') {
			pollHandle = setInterval(load, POLL_MS);
		}
	}

	function stopPolling() {
		if (pollHandle !== undefined) {
			clearInterval(pollHandle);
			pollHandle = undefined;
		}
	}

	async function load() {
		error = null;
		try {
			const response = await adminApi.getLogTail(Number(linesWanted), level);
			if (!response.success || !response.data) {
				error = response.message ?? 'Failed to load the log tail.';
				return;
			}
			result = response.data;
			if (scrolledUp) {
				hasNewSincePause = true;
			} else {
				await tick();
				scrollToBottom();
			}
		} catch (e: any) {
			logger.error('Failed to load the log tail:', e);
			error = e.response?.data?.message || e.message || 'Failed to load the log tail.';
		} finally {
			loading = false;
		}
	}

	function scrollToBottom() {
		if (listEl) listEl.scrollTop = listEl.scrollHeight;
	}

	function handleScroll() {
		if (!listEl) return;
		const distance = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight;
		scrolledUp = distance > AT_BOTTOM_SLOP_PX;
		if (!scrolledUp) hasNewSincePause = false;
	}

	function jumpToLatest() {
		scrolledUp = false;
		hasNewSincePause = false;
		tick().then(scrollToBottom);
	}

	function changeLevel(id: string) {
		level = id as LogLevel;
		refresh();
	}

	function changeLines(id: string) {
		linesWanted = id;
		refresh();
	}

	function refresh() {
		loading = true;
		load();
	}

	function copyText(): string {
		if (!result) return '';
		return result.lines.map((l) => `${l.ts} | ${l.level.padStart(8)} | ${l.logger} | ${l.message}`).join('\n');
	}
</script>

<DetailSection label="Logs" padded={false}>
	{#snippet headerExtra()}
		<div class="flex items-center gap-2 flex-wrap justify-end">
			<SegmentedControl items={LEVEL_ITEMS} selected={level} onSelect={changeLevel} ariaLabel="Minimum log level" />
			<SegmentedControl items={LINES_ITEMS} selected={linesWanted} onSelect={changeLines} ariaLabel="Lines to show" />
			<Switch checked={autoFollow} onchange={(v) => (autoFollow = v)} label="Auto-follow" size="sm" />
			<IconButton icon="refresh" label="Refresh" size="sm" onclick={refresh} />
			<CopyButton text={copyText} title="Copy visible lines" size="sm" />
		</div>
	{/snippet}

	{#if loading}
		<div class="px-4 sm:px-5 py-4">
			<Spinner size="sm" />
		</div>
	{:else if error}
		<div class="px-4 sm:px-5 py-4">
			<Alert variant="danger" icon>{error}</Alert>
		</div>
	{:else if !result || result.file === null}
		<div class="px-4 sm:px-5 py-4">
			<EmptyState icon="clipboard-list" title="File logging is off" compact />
		</div>
	{:else if result.lines.length === 0}
		<div class="px-4 sm:px-5 py-4">
			<EmptyState icon="clipboard-list" title="No log lines yet" compact />
		</div>
	{:else}
		<div class="relative">
			<div
				bind:this={listEl}
				onscroll={handleScroll}
				class="max-h-[60vh] overflow-y-auto px-4 sm:px-5 py-2 font-mono text-xs border-t border-line"
			>
				{#each result.lines as line, i (i)}
					{@const messageLines = line.message.split('\n')}
					<div class="leading-5">
						<div class="flex gap-2 whitespace-pre-wrap break-all">
							<span class="tabular-nums text-fg-subtle flex-shrink-0">{line.ts}</span>
							<span class="w-16 flex-shrink-0 font-semibold {LEVEL_TOKEN[line.level] ?? 'text-fg-muted'}">{line.level}</span>
							<span class="text-fg-subtle flex-shrink-0 truncate max-w-[10rem]">{line.logger}</span>
							<span class="text-fg flex-1 min-w-0">{messageLines[0]}</span>
						</div>
						{#each messageLines.slice(1) as cont, j (j)}
							<div class="pl-[9.5rem] text-fg-subtle whitespace-pre-wrap break-all">{cont}</div>
						{/each}
					</div>
				{/each}
			</div>
			{#if scrolledUp && hasNewSincePause}
				<div class="absolute bottom-3 right-4 z-10">
					<Button variant="secondary" size="sm" onclick={jumpToLatest}>Jump to latest</Button>
				</div>
			{/if}
		</div>
		{#if result.truncated}
			<p class="px-4 sm:px-5 py-2 font-mono text-2xs tabular-nums text-fg-subtle border-t border-line">
				Showing the last {result.lines.length} lines · earlier lines are not shown
			</p>
		{/if}
	{/if}
</DetailSection>
