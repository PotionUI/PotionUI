<script lang="ts">
	import { Badge, IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { DetailHeader, DetailBody, DetailLayout, DetailFooter } from '$lib/components/detail';
	import AttributeDefinitionForm from '../AttributeDefinitionForm.svelte';
	import { draftFromDefinition, emptyAttributeDraft, type AttributeDraft } from '../attributeDefinitionForm';
	import type { AttributeDefinition } from '$lib/types/models';
	import { adminSectionIcon } from '../../adminSections';

	let {
		definition,
		modelTypeOptions,
		onBack,
		onDelete,
		onSave
	}: {
		definition: AttributeDefinition;
		modelTypeOptions: string[];
		onBack: () => void;
		onDelete: (definition: AttributeDefinition) => void;
		onSave: (definition: AttributeDefinition, draft: AttributeDraft) => Promise<boolean>;
	} = $props();

	let draft: AttributeDraft = $state(emptyAttributeDraft());
	let snapshot = $state('');
	let saving = $state(false);

	$effect(() => {
		draft = draftFromDefinition(definition);
		snapshot = JSON.stringify(draft);
	});

	const dirty = $derived(JSON.stringify(draft) !== snapshot);

	function discard() {
		draft = draftFromDefinition(definition);
	}

	async function save() {
		saving = true;
		try {
			const ok = await onSave(definition, draft);
			if (ok) snapshot = JSON.stringify(draft);
		} finally {
			saving = false;
		}
	}
</script>

<div class="flex h-full flex-col">
	<DetailHeader title={definition.label} icon={adminSectionIcon('models')} backLabel="Attributes" {onBack}>
		{#snippet chips()}
			<Badge variant="neutral" size="sm" class="font-mono">{definition.key}</Badge>
			<Badge variant="neutral" size="sm" class="uppercase">{definition.field_type}</Badge>
			{#if definition.system}<Badge variant="info" size="sm">Built-in</Badge>{/if}
		{/snippet}
		{#snippet actions()}
			<Tooltip text={definition.system ? "Built-in attributes can't be deleted." : 'Delete attribute'}>
				<IconButton
					icon="trash"
					label="Delete attribute"
					class="text-danger hover:text-danger hover:bg-danger/10"
					disabled={definition.system}
					onclick={() => onDelete(definition)}
				/>
			</Tooltip>
		{/snippet}
	</DetailHeader>

	<DetailBody>
		<DetailLayout>
			{#snippet main()}
				{#key definition.id}
					<AttributeDefinitionForm bind:draft layout="panel" idPrefix="edit-attr" locked={definition.system} {modelTypeOptions} />
				{/key}
			{/snippet}
		</DetailLayout>
	</DetailBody>

	<DetailFooter dirtyCount={dirty ? 1 : 0} {saving} canSave={dirty} onSave={save} onDiscard={discard} />
</div>
