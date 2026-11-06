<script lang="ts">
	import { Alert, Badge, Button, Switch } from '$lib/components/ui';
	import type { ModelRoot, ModelRootBinding } from '$lib/services/api/models';
	import type { FoldersController } from './controller.svelte';
	import { bindingFiles, bindingKey, formatBytes, formatCount, supportsHeaderScan, typeLabel } from './derive';

	let { ctrl, root: r, binding: b, onRemoved }: { ctrl: FoldersController; root: ModelRoot; binding: ModelRootBinding; onRemoved: () => void } = $props();
</script>

<div class="space-y-4" data-panel-binding>
	<div>
		<div class="font-mono text-[12px] uppercase tracking-[0.05em] text-fg-subtle">Full path</div>
		<div class="mt-1 break-all rounded border border-line bg-surface-2 px-3 py-2 font-mono text-[12px] text-fg">{b.path}</div>
	</div>
	<div class="grid grid-cols-2 gap-2">
		{#each [['Files', formatCount(bindingFiles(b))], ['Size', formatBytes(b.size_bytes, 1)], ['Not indexed', formatCount(b.unindexed)], ['On disk', b.exists ? 'Yes' : 'Missing']] as [k, v] (k)}
			<div class="rounded border border-line bg-surface-2 px-3 py-2">
				<div class="font-mono text-[12px] uppercase tracking-[0.05em] text-fg-subtle">{k}</div>
				<div class="truncate font-mono text-sm tabular-nums {k === 'On disk' && !b.exists ? 'text-warning' : 'text-fg'}">{v}</div>
			</div>
		{/each}
	</div>
	{#if !b.exists}
		<Alert variant="warning" density="compact" icon>This folder is missing on disk. Create it, or remove it from {r.label}.</Alert>
	{/if}
	<div class="flex items-center justify-between gap-3 border-t border-line pt-3">
		<div class="min-w-0">
			<div class="text-sm text-fg">Downloads folder</div>
			<div class="text-[12px] text-fg-subtle">{b.is_write ? `New ${typeLabel(b.model_type)} downloads are saved here.` : r.read_only ? 'This folder is read-only.' : `Save new ${typeLabel(b.model_type)} downloads here.`}</div>
		</div>
		{#if b.is_write}
			<Badge variant="signal" size="sm">Current</Badge>
		{:else}
			<Button variant="secondary" size="sm" disabled={r.read_only} loading={ctrl.isBusy(`dl:${bindingKey(r.id, b)}`)} onclick={() => ctrl.makeDownloads(r, b)}>Use</Button>
		{/if}
	</div>
	{#if supportsHeaderScan(b)}
		<div class="flex items-center justify-between gap-3 border-t border-line pt-3">
			<div class="min-w-0">
				<div class="text-sm text-fg">Detect type from file</div>
				<div class="text-[12px] text-fg-subtle">Reads each file header to tell full checkpoints from diffusion models. Pickle files keep the folder type.</div>
			</div>
			{#key ctrl.failures}
				<Switch checked={b.scan_headers} onchange={(v) => ctrl.toggleScan(r, b, v)} busy={ctrl.isBusy(`scan:${bindingKey(r.id, b)}`)} label="Detect type from file" id="d-scan-{bindingKey(r.id, b)}" />
			{/key}
		</div>
		{#if ctrl.errors[`scan:${bindingKey(r.id, b)}`]}<p class="text-[12px] text-danger">{ctrl.errors[`scan:${bindingKey(r.id, b)}`]}</p>{/if}
	{/if}
	{#if r.kind !== 'home'}
		<div class="border-t border-line pt-3">
			<Button variant="danger" size="sm" icon="trash-2" loading={ctrl.isBusy(`rm:${bindingKey(r.id, b)}`)} onclick={async () => { if (await ctrl.removeBinding(r, b)) onRemoved(); }}>Remove from folder</Button>
		</div>
	{/if}
</div>
