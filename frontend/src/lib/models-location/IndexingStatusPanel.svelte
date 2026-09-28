<script lang="ts">
	import { Badge, Spinner, Alert } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { IndexingStatus, ModelsLocationConfig } from '$lib/services/api/models';
	import { indexingDoneSummary, indexingIsVisible, indexingPercent } from './indexingDisplay';

	let {
		status,
		config = null
	}: {
		status: IndexingStatus | null;
		config?: ModelsLocationConfig | null;
	} = $props();

	let failedOpen = $state(false);
	let duplicatesOpen = $state(false);
	let conflictsOpen = $state(false);

	const percent = $derived(indexingPercent(status));
	const failedFiles = $derived(status?.failed_files ?? []);
	const failedTotal = $derived(status?.failed_files_total ?? 0);
	const expectedFolders = $derived(config?.directories.map((dir) => dir.directory) ?? []);
	const doneSummary = $derived(indexingDoneSummary(status));
	const duplicates = $derived(status?.duplicates ?? []);
	const conflicts = $derived(status?.conflicts ?? []);
</script>

{#if indexingIsVisible(status) && status}
	<div class="space-y-2">
		{#if status.restart_pending}
			<Alert variant="info" density="compact" icon>
				Applying the new location - restarting the scan once this pass finishes.
			</Alert>
		{/if}

		{#if status.state === 'scanning'}
			<div class="flex items-center gap-2 text-sm text-fg-muted">
				<Spinner size="sm" />
				<span>Scanning {status.scanned_roots?.[0] ?? 'the models directory'}…</span>
			</div>
		{:else if status.state === 'indexing'}
			<div class="space-y-1.5">
				<div class="flex items-center justify-between text-xs font-mono tabular-nums text-fg-muted">
					<span>Indexing models…</span>
					<span>{status.processed ?? 0} / {status.total ?? 0}{percent != null ? ` (${percent}%)` : ''}</span>
				</div>
				<div class="h-1.5 bg-surface-3 rounded-sm overflow-hidden">
					<div class="h-full bg-signal-solid transition-all duration-300" style="width: {percent ?? 0}%"></div>
				</div>
			</div>
		{:else if status.state === 'done'}
			{#if status.found_on_disk === 0}
				<Alert variant="warning" density="compact" icon>
					No model files found in
					<span class="font-mono">{status.scanned_roots?.join(', ') || 'the configured location'}</span>.
					{#if expectedFolders.length}
						Expected folders: <span class="font-mono">{expectedFolders.join(', ')}</span>.
					{/if}
				</Alert>
				{#if config?.auto_matched?.length}
					<p class="text-xs text-fg-subtle">
						Matched an existing folder for: <span class="font-mono text-fg-muted">{config.auto_matched.join(', ')}</span>.
					</p>
				{/if}
				{#if config?.created_empty?.length}
					<p class="text-xs text-fg-subtle">
						No matching folder found, created empty: <span class="font-mono text-fg-muted">{config.created_empty.join(', ')}</span>.
					</p>
				{/if}
			{:else if doneSummary?.kind === 'up_to_date'}
				<p class="text-sm text-fg-muted">
					All <span class="font-mono tabular-nums text-fg">{doneSummary.found}</span> model files are up to date.
				</p>
			{:else if doneSummary?.kind === 'new_indexed'}
				<p class="text-sm text-fg-muted">
					<span class="font-mono tabular-nums text-fg">{doneSummary.indexed}</span> new ·
					<span class="font-mono tabular-nums text-fg">{doneSummary.alreadyIndexed}</span> already indexed ·
					<span class="font-mono tabular-nums text-fg">{doneSummary.found}</span> on disk.
				</p>
			{/if}
		{:else if status.state === 'failed'}
			<Alert variant="danger" density="compact" icon>{status.error || 'Indexing failed.'}</Alert>
		{:else if status.state === 'cancelled'}
			<Alert variant="neutral" density="compact" icon>Indexing was cancelled.</Alert>
		{:else if status.state === 'blocked'}
			<Alert variant="warning" density="compact" icon>{status.error || 'A plugin blocked indexing.'}</Alert>
		{/if}

		{#if failedTotal > 0}
			<button
				type="button"
				class="flex items-center gap-1.5 text-xs font-mono uppercase tracking-[0.05em] text-warning hover:underline"
				onclick={() => (failedOpen = !failedOpen)}
				aria-expanded={failedOpen}
			>
				<Badge variant="warning" size="sm" dot>
					{failedTotal} file{failedTotal === 1 ? '' : 's'} failed
				</Badge>
				<span>{failedOpen ? 'Hide' : 'Show'}</span>
			</button>
			{#if failedOpen}
				<ul class="max-h-48 overflow-y-auto space-y-1.5 text-xs text-fg-subtle leading-relaxed">
					{#each failedFiles as failure (failure.path)}
						<li class="flex items-start gap-2 min-w-0">
							<Tooltip text={failure.path} position="bottom">
								<span class="font-mono text-fg-muted truncate max-w-[18rem] inline-block align-bottom">
									{failure.path}
								</span>
							</Tooltip>
							<span class="text-danger truncate">{failure.error}</span>
						</li>
					{/each}
					{#if failedTotal > failedFiles.length}
						<li>and {failedTotal - failedFiles.length} more</li>
					{/if}
				</ul>
			{/if}
		{/if}

		{#if duplicates.length}
			<button
				type="button"
				class="flex items-center gap-1.5 text-xs text-fg-subtle hover:underline"
				onclick={() => (duplicatesOpen = !duplicatesOpen)}
				aria-expanded={duplicatesOpen}
			>
				<Badge variant="neutral" size="sm">
					{duplicates.length} model{duplicates.length === 1 ? '' : 's'} exist in more than one folder — the copy in
					the first folder is used
				</Badge>
				<span class="font-mono uppercase tracking-[0.05em]">{duplicatesOpen ? 'Hide' : 'Show'}</span>
			</button>
			{#if duplicatesOpen}
				<ul class="max-h-48 overflow-y-auto space-y-2 text-xs text-fg-subtle leading-relaxed">
					{#each duplicates as entry (entry.filename + entry.model_type)}
						<li class="space-y-1">
							<span class="font-mono text-fg-muted">{entry.filename}</span>
							<ul class="space-y-1 pl-3">
								{#each entry.copies as copy (copy.root_label + copy.rel_path)}
									<li class="flex items-center gap-2 min-w-0">
										<Tooltip text={copy.rel_path} position="bottom">
											<span class="font-mono text-fg-subtle truncate max-w-[14rem] inline-block align-bottom">
												{copy.root_label}
											</span>
										</Tooltip>
										{#if copy.winner}
											<Badge variant="success" size="sm">used</Badge>
										{/if}
									</li>
								{/each}
							</ul>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}

		{#if conflicts.length}
			<button
				type="button"
				class="flex items-center gap-1.5 text-xs text-warning hover:underline"
				onclick={() => (conflictsOpen = !conflictsOpen)}
				aria-expanded={conflictsOpen}
			>
				<Badge variant="warning" size="sm" dot>
					{conflicts.length} file{conflicts.length === 1 ? '' : 's'} share a name with a different model file and
					{conflicts.length === 1 ? 'is' : 'are'} not used
				</Badge>
				<span class="font-mono uppercase tracking-[0.05em]">{conflictsOpen ? 'Hide' : 'Show'}</span>
			</button>
			{#if conflictsOpen}
				<ul class="max-h-48 overflow-y-auto space-y-1.5 text-xs text-fg-subtle leading-relaxed">
					{#each conflicts as entry (entry.id)}
						<li class="flex items-start gap-2 min-w-0">
							<Tooltip text={entry.rel_path} position="bottom">
								<span class="font-mono text-fg-muted truncate max-w-[18rem] inline-block align-bottom">
									{entry.rel_path}
								</span>
							</Tooltip>
							<span class="text-fg-subtle truncate">{entry.root_label}</span>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
{/if}
