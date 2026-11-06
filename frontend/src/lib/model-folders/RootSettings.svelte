<script lang="ts">
	import { Button, Input, Switch } from '$lib/components/ui';
	import type { ModelRoot } from '$lib/services/api/models';
	import type { FoldersController } from './controller.svelte';

	let { ctrl, root, idPrefix = 'rs' }: { ctrl: FoldersController; root: ModelRoot; idPrefix?: string } = $props();

	let label = $state('');
	let path = $state('');
	const rootLabel = $derived(root.label);
	const rootPath = $derived(root.path);
	$effect(() => {
		label = rootLabel;
		path = rootPath;
	});
	const dirty = $derived(label.trim() !== root.label || path.trim() !== root.path);
	const home = $derived(root.kind === 'home');
</script>

<div class="space-y-3">
	<div>
		<label for="{idPrefix}-label-{root.id}" class="mb-1 block text-[12px] font-medium text-fg-muted">Name</label>
		<Input id="{idPrefix}-label-{root.id}" bind:value={label} disabled={ctrl.isBusy(`save:${root.id}`)} />
	</div>
	<div>
		<label for="{idPrefix}-path-{root.id}" class="mb-1 block text-[12px] font-medium text-fg-muted">Path on the server</label>
		<Input id="{idPrefix}-path-{root.id}" bind:value={path} disabled={home || ctrl.isBusy(`save:${root.id}`)} class="font-mono" />
		{#if home}<p class="mt-1 text-[12px] text-fg-subtle">The built-in folder cannot be moved.</p>{/if}
	</div>
	{#if dirty}
		<div class="flex items-center gap-2">
			<Button variant="secondary" size="sm" icon="save" loading={ctrl.isBusy(`save:${root.id}`)} onclick={() => ctrl.saveRoot(root, label, path)}>
				Save changes
			</Button>
			<Button
				variant="ghost"
				size="sm"
				onclick={() => {
					label = root.label;
					path = root.path;
				}}
			>
				Reset
			</Button>
		</div>
	{/if}
	<div class="flex items-center justify-between gap-3 border-t border-line pt-3">
		<div class="min-w-0">
			<div class="text-sm text-fg">Read-only</div>
			<div class="text-[12px] text-fg-subtle">Models here are used, but nothing is downloaded into it.</div>
		</div>
		{#key ctrl.failures}
			<Switch
				checked={root.read_only}
				onchange={() => ctrl.toggleReadOnly(root)}
				disabled={home || ctrl.isBusy(`ro:${root.id}`)}
				busy={ctrl.isBusy(`ro:${root.id}`)}
				label="Read-only"
				id="{idPrefix}-ro-{root.id}"
			/>
		{/key}
	</div>
</div>
