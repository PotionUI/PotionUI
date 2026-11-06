<script lang="ts">
	import { Alert, Button } from '$lib/components/ui';
	import SubfolderPicker from '$lib/model-roots/SubfolderPicker.svelte';
	import type { ModelRoot } from '$lib/services/api/models';
	import AgainPanel from './AgainPanel.svelte';
	import RootSettings from './RootSettings.svelte';
	import type { FoldersController } from './controller.svelte';
	import { agoText, formatBytes, formatCount, rootBytes, rootFiles, rootUnindexed } from './derive';

	let { ctrl, root: r, onDeleted }: { ctrl: FoldersController; root: ModelRoot; onDeleted: () => void } = $props();
</script>

<div class="space-y-4" data-panel-root>
	<div class="grid grid-cols-2 gap-2">
		{#each [['Files', formatCount(rootFiles(r))], ['Size', formatBytes(rootBytes(r), 1)], ['Not indexed', formatCount(rootUnindexed(r))], ['Checked', agoText(r.state_checked_at)]] as [k, v] (k)}
			<div class="rounded border border-line bg-surface-2 px-3 py-2">
				<div class="font-mono text-[12px] uppercase tracking-[0.05em] text-fg-subtle">{k}</div>
				<div class="truncate font-mono text-sm tabular-nums text-fg">{v}</div>
			</div>
		{/each}
	</div>
	{#if r.state !== 'online'}
		<Alert variant={r.state === 'offline' ? 'danger' : 'warning'} density="compact" icon>
			{r.state_reason}
			{#snippet actions()}
				<Button variant="secondary" size="sm" loading={ctrl.isBusy(`probe:${r.id}`)} onclick={() => ctrl.probe(r)}>Check again</Button>
			{/snippet}
		</Alert>
	{/if}
	<RootSettings {ctrl} root={r} idPrefix="d" />
	<div class="flex flex-wrap gap-2 border-t border-line pt-3">
		<Button variant="secondary" size="sm" icon="refresh" loading={ctrl.isBusy(`probe:${r.id}`)} onclick={() => ctrl.probe(r)}>Check now</Button>
		<Button variant="secondary" size="sm" icon="scan-search" loading={ctrl.isBusy(`detect:${r.id}`)} disabled={r.kind === 'home' || r.state !== 'online'} onclick={() => ctrl.detectAgain(r)}>Detect again</Button>
	</div>
	<AgainPanel {ctrl} root={r} />
	{#if r.kind !== 'home'}
		<SubfolderPicker idPrefix="d-{r.id}" rootPath={r.path} existing={r.bindings} caseInsensitive={r.case_insensitive} disabled={r.state !== 'online'} onAdd={(f) => ctrl.addFolderToRoot(r, f)} />
		<div class="border-t border-line pt-3">
			<Button variant="danger" size="sm" icon="trash-2" loading={ctrl.isBusy(`del:${r.id}`)} onclick={async () => { if (await ctrl.deleteRoot(r)) onDeleted(); }}>Delete folder</Button>
		</div>
	{/if}
</div>
