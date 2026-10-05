<script lang="ts">
	import { onMount } from 'svelte';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import { Button, IconButton, Input, Switch } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { actionsForSubject, newAction, supportsTags, type RuleDraft, type DraftAction } from '$lib/organize/draft';
	import { collectionOptions, loadCollections, type PlainCollection } from '$lib/organize/collections';
	import { scopeFromSubject } from '$lib/organize/subjects';
	import type { OrganizeActionSpec, OrganizeCatalog, OrganizeOption } from '$lib/types/organize';
	import TagNamesInput from './TagNamesInput.svelte';

	let {
		draft = $bindable(),
		catalog
	}: {
		draft: RuleDraft;
		catalog: OrganizeCatalog;
	} = $props();

	const NEW = '__new__';
	const ROOT = '__root__';

	let collections = $state<PlainCollection[]>([]);
	let creating = $state<Record<string, boolean>>({});
	let loadedScope = '';

	const specs = $derived(
		actionsForSubject(catalog, draft.subject).filter(
			(s) => s.key !== 'add_tags' || supportsTags(catalog, draft.subject)
		)
	);
	const options = $derived(collectionOptions(collections));

	$effect(() => {
		const scope = scopeFromSubject(draft.subject);
		if (scope === loadedScope) return;
		loadedScope = scope;
		void loadCollections(scope)
			.then((list) => {
				collections = list;
			})
			.catch(() => {
				collections = [];
			});
	});

	onMount(() => {
		if (draft.actions.length === 0 && specs.length > 0) {
			const collectionSpec = specs.find((s) => s.key === 'add_to_collection') ?? specs[0];
			draft.actions.push(newAction(collectionSpec));
		}
	});

	function specOf(key: string): OrganizeActionSpec | undefined {
		return catalog.actions.find((a) => a.key === key);
	}

	function setConfig(action: DraftAction, key: string, value: unknown) {
		action.config = { ...action.config, [key]: value };
	}

	function pickCollection(action: DraftAction, value: string) {
		if (value === NEW) {
			creating[action.uid] = true;
			action.config = { ...action.config, collection_id: null };
			return;
		}
		creating[action.uid] = false;
		action.config = { ...action.config, collection_id: value, collection_name: '', parent_id: null };
	}

	function isCreating(action: DraftAction): boolean {
		if (creating[action.uid] !== undefined) return creating[action.uid];
		return !action.config.collection_id && !!String(action.config.collection_name ?? '');
	}

	function addAction(key: string) {
		const spec = specOf(key);
		if (spec) draft.actions.push(newAction(spec));
	}

	function removeAction(index: number) {
		draft.actions.splice(index, 1);
	}

	function tagLoader() {
		return async (query: string): Promise<OrganizeOption[]> => {
			const response = await api.getOrganizeFactOptions('tags', { subject: draft.subject, q: query, limit: 20 });
			return response.success && Array.isArray(response.data) ? response.data : [];
		};
	}

</script>

<div class="space-y-3">
	{#each draft.actions as action, index (action.uid)}
		{@const spec = specOf(action.action)}
		<div class="rounded-lg border border-line bg-surface-2 p-3" data-testid="action-row" data-action={action.action}>
			<div class="flex items-start gap-3">
				<span class="w-12 flex-shrink-0 pt-2 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
					{index === 0 ? 'Then' : 'And'}
				</span>
				<div class="min-w-0 flex-1 space-y-2">
					{#if !spec}
						<p class="pt-1.5 text-sm text-warning">This action is no longer available.</p>
					{:else if action.action === 'add_to_collection'}
						<p class="pt-1.5 text-sm font-medium text-fg">{spec.label}</p>
						<CustomSelect
							value={isCreating(action) ? NEW : ((action.config.collection_id as string) ?? '')}
							searchable
							placeholder="Choose a collection"
							options={[...options, { value: NEW, label: 'Create a new collection' }]}
							on:change={(event) => pickCollection(action, event.detail)}
						/>
						{#if isCreating(action)}
							<div class="grid gap-2 sm:grid-cols-2">
								<Input
									value={String(action.config.collection_name ?? '')}
									placeholder="New collection name"
									aria-label="New collection name"
									oninput={(event: Event) => setConfig(action, 'collection_name', (event.currentTarget as HTMLInputElement).value)}
								/>
								<CustomSelect
									value={(action.config.parent_id as string) ?? ROOT}
									placeholder="Create inside"
									options={[{ value: ROOT, label: 'Top level' }, ...options]}
									on:change={(event) => setConfig(action, 'parent_id', event.detail === ROOT ? null : event.detail)}
								/>
							</div>
						{/if}
						<div class="flex items-center gap-2">
							<Switch
								size="sm"
								label="Create it again if it goes missing"
								checked={action.config.create_if_missing !== false}
								onchange={(next) => setConfig(action, 'create_if_missing', next)}
							/>
							<span class="text-xs text-fg-muted">Create it again if it goes missing</span>
						</div>
					{:else}
						<p class="pt-1.5 text-sm font-medium text-fg">{spec.label}</p>
						{#each spec.config_schema as field (field.key)}
							<div class="space-y-1">
								{#if field.kind !== 'bool'}
									<span class="block text-xs text-fg-muted">{field.label}</span>
								{/if}
								{#if field.kind === 'tag_list'}
									<TagNamesInput
										value={Array.isArray(action.config[field.key]) ? (action.config[field.key] as string[]) : []}
										loadOptions={action.action === 'add_tags' ? tagLoader() : undefined}
										onchange={(next) => setConfig(action, field.key, next)}
									/>
								{:else if field.kind === 'collection'}
									<CustomSelect
										value={(action.config[field.key] as string) ?? ''}
										searchable
										placeholder="Choose a collection"
										{options}
										on:change={(event) => setConfig(action, field.key, event.detail)}
									/>
								{:else if field.kind === 'bool'}
									<div class="flex items-center gap-2">
										<Switch
											size="sm"
											label={field.label}
											checked={action.config[field.key] === true}
											onchange={(next) => setConfig(action, field.key, next)}
										/>
										<span class="text-xs text-fg-muted">{field.label}</span>
									</div>
								{:else if field.kind === 'enum'}
									<CustomSelect
										value={(action.config[field.key] as string) ?? ''}
										options={field.options ?? []}
										on:change={(event) => setConfig(action, field.key, event.detail)}
									/>
								{:else if field.kind === 'number'}
									<input
										type="number"
										class="input w-32 font-mono tabular-nums"
										aria-label={field.label}
										min={field.min}
										max={field.max}
										step={field.step ?? 1}
										value={typeof action.config[field.key] === 'number' ? (action.config[field.key] as number) : ''}
										oninput={(event) => setConfig(action, field.key, event.currentTarget.value === '' ? null : Number(event.currentTarget.value))}
									/>
								{:else}
									<Input
										value={String(action.config[field.key] ?? '')}
										aria-label={field.label}
										oninput={(event: Event) => setConfig(action, field.key, (event.currentTarget as HTMLInputElement).value)}
									/>
								{/if}
							</div>
						{/each}
					{/if}
				</div>
				<IconButton icon="close" label="Remove action" onclick={() => removeAction(index)} />
			</div>
		</div>
	{/each}

	<div class="flex flex-wrap items-center gap-2">
		{#each specs as spec (spec.key)}
			<Button size="sm" variant="ghost" icon="plus" onclick={() => addAction(spec.key)}>{spec.label}</Button>
		{/each}
	</div>
</div>
