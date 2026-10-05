<script lang="ts">
	import { Badge, Button, EmptyState, LoadErrorState, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { runKindLabel, runSummary, runTitle, undoMessage } from '$lib/organize/activity';
	import { parseOrganizeError } from '$lib/organize/errors';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { OrganizeRun, OrganizeSubject } from '$lib/types/organize';

	let {
		subject,
		ruleId = undefined,
		refreshKey = 0,
		onundone
	}: {
		subject: OrganizeSubject;
		ruleId?: string;
		refreshKey?: number;
		onundone?: (run: OrganizeRun) => void;
	} = $props();

	let runs = $state<OrganizeRun[]>([]);
	let nextBefore = $state<string | null>(null);
	let loading = $state(true);
	let loadingMore = $state(false);
	let error = $state<string | null>(null);
	let undoing = $state<string | null>(null);
	let loadedKey = '';

	async function load(more = false) {
		if (more) loadingMore = true;
		else {
			loading = true;
			error = null;
		}
		try {
			const response = await api.getOrganizeActivity({
				subject,
				rule_id: ruleId,
				limit: 50,
				before: more ? (nextBefore ?? undefined) : undefined
			});
			if (!response.success || !response.data) {
				error = response.message || 'The activity could not be loaded.';
				return;
			}
			runs = more ? [...runs, ...response.data.runs] : response.data.runs;
			nextBefore = response.data.next_before;
		} catch (err) {
			error = parseOrganizeError(err, 'The activity could not be loaded.').message;
		} finally {
			loading = false;
			loadingMore = false;
		}
	}

	$effect(() => {
		const key = `${subject}|${ruleId ?? ''}|${refreshKey}`;
		if (key === loadedKey) return;
		loadedKey = key;
		void load();
	});

	async function undo(run: OrganizeRun) {
		const ok = await confirmDialog({
			title: 'Undo this run?',
			message: undoMessage(run),
			variant: 'warning'
		});
		if (!ok) return;
		undoing = run.id;
		try {
			const response = await api.undoOrganizeRun(run.id);
			if (response.success && response.data) {
				runs = runs.map((r) => (r.id === run.id ? response.data!.run : r));
				toasts.success(`Removed ${response.data.undone} ${response.data.undone === 1 ? 'item' : 'items'} this run added`);
				onundone?.(response.data.run);
			} else {
				toasts.error(response.message || 'The run could not be undone.');
			}
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The run could not be undone.').message);
		} finally {
			undoing = null;
		}
	}
</script>

{#if loading}
	<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
{:else if error && runs.length === 0}
	<LoadErrorState message={error} onRetry={() => load()} retrying={loading} />
{:else if runs.length === 0}
	<EmptyState
		icon="clock"
		title="No activity yet"
		description="When a rule files something, it shows up here and you can undo it."
		compact
	/>
{:else}
	<ul class="space-y-2" data-testid="activity-list">
		{#each runs as run (run.id)}
			<li class="rounded-lg border border-line bg-surface-1 p-3 shadow-raised" data-testid="activity-run" data-run-id={run.id}>
				<div class="flex flex-wrap items-start gap-3">
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-2">
							<span class="truncate text-sm font-semibold text-fg">{runTitle(run)}</span>
							<Badge size="sm" variant="neutral">{runKindLabel(run)}</Badge>
							{#if run.status === 'undone'}
								<Badge size="sm" variant="neutral">Undone</Badge>
							{:else if run.status === 'running'}
								<Badge size="sm" variant="signal">Running</Badge>
							{:else if run.status === 'failed'}
								<Badge size="sm" variant="danger">Did not finish</Badge>
							{:else if run.status === 'cancelled'}
								<Badge size="sm" variant="neutral">Stopped</Badge>
							{/if}
						</div>
						<ul class="mt-1 space-y-0.5 text-sm text-fg-muted">
							{#each runSummary(run) as line (line)}
								<li>{line}</li>
							{/each}
						</ul>
						<p class="mt-1.5 font-mono text-2xs tabular-nums text-fg-subtle">
							{timeAgo(run.started_at)}
							{#if run.undone > 0 && run.status !== 'undone'} · {run.undone} undone{/if}
						</p>
					</div>
					{#if run.can_undo}
						<Button size="sm" variant="secondary" icon="undo" loading={undoing === run.id} onclick={() => undo(run)}>
							Undo
						</Button>
					{/if}
				</div>
			</li>
		{/each}
	</ul>
	{#if nextBefore}
		<div class="mt-3 flex justify-center">
			<Button size="sm" variant="secondary" loading={loadingMore} onclick={() => load(true)}>Show older</Button>
		</div>
	{/if}
{/if}
