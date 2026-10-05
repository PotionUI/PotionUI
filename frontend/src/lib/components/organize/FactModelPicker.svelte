<script lang="ts">
	import ModelPickTrigger from '$lib/components/ui/ModelPickTrigger.svelte';
	import ModelAssignmentModal from '$lib/components/modals/ModelAssignmentModal.svelte';
	import { Button, IconButton } from '$lib/components/ui';
	import { modelDisplayName } from '$lib/utils/modelDisplay';
	import { rememberModelName, resolveModelNames, cachedModelName } from '$lib/organize/labels';

	let {
		value,
		multi = false,
		modelTypes = [],
		onchange
	}: {
		value: unknown;
		multi?: boolean;
		modelTypes?: string[];
		onchange: (next: string | string[]) => void;
	} = $props();

	let open = $state(false);
	let names = $state<Record<string, string>>({});

	const ids = $derived(
		Array.isArray(value) ? (value as string[]) : typeof value === 'string' && value ? [value] : []
	);
	const initialType = $derived(modelTypes.length === 1 ? modelTypes[0] : 'all');

	$effect(() => {
		const wanted = ids;
		const missing = wanted.filter((id) => !names[id]);
		if (missing.length === 0) return;
		void resolveModelNames(missing).then((found) => {
			names = { ...names, ...found };
		});
	});

	function nameOf(id: string): string {
		return names[id] ?? cachedModelName(id) ?? 'Model';
	}

	function handleSelect(model: { id: string }) {
		const name = modelDisplayName(model as Parameters<typeof modelDisplayName>[0]);
		rememberModelName(model.id, name);
		names = { ...names, [model.id]: name };
		open = false;
		if (multi) {
			if (!ids.includes(model.id)) onchange([...ids, model.id]);
		} else {
			onchange(model.id);
		}
	}

	function removeId(id: string) {
		onchange(ids.filter((entry) => entry !== id));
	}
</script>

{#if multi}
	<div class="flex flex-wrap items-center gap-1.5" data-testid="fact-model-multi">
		{#each ids as id (id)}
			<span class="inline-flex h-7 items-center gap-1 rounded border border-line-strong bg-surface-2 pl-2 pr-1 text-xs text-fg">
				<span class="max-w-[14rem] truncate">{nameOf(id)}</span>
				<IconButton icon="close" label="Remove {nameOf(id)}" size="xs" onclick={() => removeId(id)} />
			</span>
		{/each}
		<Button size="sm" variant="secondary" icon="plus" onclick={() => (open = true)}>Add model</Button>
	</div>
{:else}
	<ModelPickTrigger
		value={ids[0] ? nameOf(ids[0]) : null}
		placeholder="Choose a model"
		clearLabel="Clear model"
		onopen={() => (open = true)}
		onclear={() => onchange('')}
	/>
{/if}

{#if open}
	<ModelAssignmentModal
		title="Select model"
		selectionMode="single"
		selectedModelId={multi ? null : (ids[0] ?? null)}
		{initialType}
		onSelect={handleSelect}
		onClose={() => (open = false)}
	/>
{/if}
