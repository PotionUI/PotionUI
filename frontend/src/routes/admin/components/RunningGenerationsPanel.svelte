<script lang="ts">
	import { onMount } from 'svelte';
	import * as adminApi from '$lib/services/admin-api';
	import type { AdminGenerationQueue } from '$lib/services/admin-api';
	import { api } from '$lib/services/api/index';
	import { loadPresets } from '$lib/stores/presetsCatalog';
	import { confirmDialog } from '$lib/stores/confirm';
	import { logger } from '$lib/utils/logger';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Button } from '$lib/components/ui';
	import { createVisiblePoll } from './visiblePoll';
	import {
		RUNNING_PAGE_SIZE,
		cancelErrorMessage,
		clampPage,
		elapsedLabel,
		mapRunningRows,
		pageSlice,
		settleStopping,
		totalPages,
		type RunningRow
	} from './generations/runningGenerations';

	let { usernameFor }: { usernameFor: (userId: string) => string | undefined } = $props();

	const POLL_MS = 3000;

	let queue = $state<AdminGenerationQueue | null>(null);
	let presetNames = $state<Record<string, string>>({});
	let backendNames = $state<Record<string, string>>({});
	let now = $state(Date.now());
	let pageIndex = $state(1);
	let stopping = $state<Record<string, boolean>>({});
	let rowErrors = $state<Record<string, string>>({});
	let stoppingAll = $state(false);
	let loadError = $state<string | null>(null);

	const rows = $derived(
		mapRunningRows(queue, {
			usernameFor,
			presetNameFor: (id) => presetNames[id],
			backendNameFor: (id) => backendNames[id]
		})
	);
	const runningCount = $derived(rows.filter((r) => r.state === 'running').length);
	const pageCountValue = $derived(totalPages(rows.length));
	const visibleRows = $derived(pageSlice(rows, clampPage(pageIndex, rows.length)));
	const currentPage = $derived(clampPage(pageIndex, rows.length));
	const stoppableCount = $derived(rows.filter((r) => !stopping[r.id]).length);

	async function refresh() {
		try {
			const response = await adminApi.getAdminGenerationQueue();
			if (response.success && response.data) {
				queue = response.data;
				loadError = null;
				const all = mapRunningRows(response.data, {
					usernameFor,
					presetNameFor: () => undefined,
					backendNameFor: () => undefined
				});
				stopping = settleStopping(stopping, all);
			} else {
				loadError = response.message || 'Could not load running generations.';
			}
		} catch (e) {
			logger.error('Failed to load the running generations:', e);
			loadError = cancelErrorMessage(e);
		}
	}

	const poll = createVisiblePoll(refresh, POLL_MS);

	onMount(() => {
		void refresh();
		poll.start();
		const clock = setInterval(() => (now = Date.now()), 1000);
		void loadPresets()
			.then((response) => {
				if (response.success && response.data) {
					presetNames = Object.fromEntries(response.data.map((p) => [p.id, p.name]));
				}
			})
			.catch(() => {});
		void adminApi
			.getBackends()
			.then((response) => {
				if (response.success && response.data) {
					backendNames = Object.fromEntries(response.data.map((b) => [b.id, b.name]));
				}
			})
			.catch(() => {});
		return () => {
			poll.stop();
			clearInterval(clock);
		};
	});

	async function cancelOne(id: string): Promise<boolean> {
		rowErrors = { ...rowErrors, [id]: '' };
		try {
			await api.cancelGeneration(id);
			stopping = { ...stopping, [id]: true };
			return true;
		} catch (e) {
			rowErrors = { ...rowErrors, [id]: cancelErrorMessage(e) };
			return false;
		}
	}

	async function stop(row: RunningRow) {
		const confirmed = await confirmDialog({
			title: row.state === 'running' ? 'Stop this generation?' : 'Remove this generation from the queue?',
			message: `${row.preset} started by ${row.owner} will be cancelled.`,
			variant: 'danger'
		});
		if (!confirmed) return;
		await cancelOne(row.id);
		void refresh();
	}

	async function stopAll() {
		const targets = rows.filter((r) => !stopping[r.id]);
		if (targets.length === 0) return;
		const confirmed = await confirmDialog({
			title: `Stop all ${targets.length} generation${targets.length === 1 ? '' : 's'}?`,
			message: 'Everything running or waiting in the queue, from every user, will be cancelled.',
			variant: 'danger'
		});
		if (!confirmed) return;
		stoppingAll = true;
		try {
			for (const row of targets) await cancelOne(row.id);
		} finally {
			stoppingAll = false;
			void refresh();
		}
	}
</script>

{#if rows.length > 0 || loadError}
	<section class="rounded-lg border border-line bg-surface-1" aria-label="Running generations" data-running-generations>
		<header class="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
			<h3 class="text-sm font-semibold text-fg">Running</h3>
			<Badge variant="info" size="sm" class="font-mono tabular-nums">{runningCount} running</Badge>
			{#if rows.length > runningCount}
				<Badge variant="neutral" size="sm" class="font-mono tabular-nums">{rows.length - runningCount} queued</Badge>
			{/if}
			<div class="ml-auto">
				<Button variant="danger" size="sm" loading={stoppingAll} disabled={stoppingAll || stoppableCount === 0} onclick={stopAll}>
					Stop all
				</Button>
			</div>
		</header>

		{#if loadError}
			<p class="px-4 py-2.5 text-sm text-danger">{loadError}</p>
		{/if}

		<ul class="divide-y divide-line">
			{#each visibleRows as row (row.id)}
				<li class="px-4 py-3" data-running-row={row.id}>
					<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
						<div class="min-w-0 flex-1 basis-56">
							<div class="flex items-center gap-2">
								{#if row.state === 'running'}
									<Badge variant="info" size="sm" dot>Running</Badge>
								{:else}
									<Badge variant="neutral" size="sm" class="font-mono tabular-nums">Queued #{(row.position ?? 0) + 1}</Badge>
								{/if}
								<Tooltip text={row.preset} position="top">
									<span class="truncate text-sm font-medium text-fg">{row.preset}</span>
								</Tooltip>
							</div>
							<div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-muted">
								<span>{row.owner}</span>
								{#if row.noTab}
									<Tooltip
										text="Started without a browser tab, for example by a recipe's test render or the API."
										position="top"
									>
										<Badge variant="neutral" size="sm">No tab</Badge>
									</Tooltip>
								{/if}
								<span class="font-mono text-fg-subtle">{row.backend}</span>
							</div>
						</div>

						<div class="flex min-w-32 flex-1 flex-col gap-1 sm:w-40 sm:flex-none">
							{#if row.state === 'running'}
								<div class="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={row.progress ?? 0} aria-label="Progress">
									<div class="h-full bg-signal transition-[width]" style="width: {row.progress ?? 0}%"></div>
								</div>
								<span class="font-mono text-xs tabular-nums text-fg-muted">
									{row.progress == null ? 'Starting…' : `${row.progress}%`}
								</span>
							{:else}
								<span class="font-mono text-xs text-fg-subtle">Waiting</span>
							{/if}
						</div>

						<span class="w-20 text-right font-mono text-xs tabular-nums text-fg-muted">
							{elapsedLabel(row.sinceMs, now)}
						</span>

						<Button
							variant="danger"
							size="sm"
							class="min-w-28 justify-center"
							loading={!!stopping[row.id]}
							disabled={!!stopping[row.id] || stoppingAll}
							onclick={() => stop(row)}
						>
							{stopping[row.id] ? 'Stopping…' : row.state === 'running' ? 'Stop' : 'Remove'}
						</Button>
					</div>
					{#if rowErrors[row.id]}
						<p class="mt-2 text-sm text-danger" role="alert">{rowErrors[row.id]}</p>
					{/if}
				</li>
			{/each}
		</ul>

		{#if pageCountValue > 1}
			<footer class="flex items-center justify-between gap-2 border-t border-line px-4 py-2">
				<span class="font-mono text-xs tabular-nums text-fg-subtle">
					{(currentPage - 1) * RUNNING_PAGE_SIZE + 1}–{Math.min(currentPage * RUNNING_PAGE_SIZE, rows.length)} of {rows.length}
				</span>
				<div class="flex items-center gap-2">
					<Button variant="ghost" size="sm" disabled={currentPage <= 1} onclick={() => (pageIndex = currentPage - 1)}>Previous</Button>
					<Button variant="ghost" size="sm" disabled={currentPage >= pageCountValue} onclick={() => (pageIndex = currentPage + 1)}>Next</Button>
				</div>
			</footer>
		{/if}
	</section>
{/if}
