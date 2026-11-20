<script lang="ts">
	import { untrack } from 'svelte';
	import DynamicForm from '$lib/components/DynamicForm.svelte';
	import { registerEditorFieldComponents } from '$lib/fields/editorFields';
	import { buildFormSchema } from '$lib/form/formSchema';
	import { extractAllFields, type FieldConfig } from '$lib/form/reactions';
	import { jsonEqual } from '$lib/utils/equal';

	export interface EntryFormInfo {
		commit: boolean;
		name?: string;
	}

	let {
		fields,
		values,
		errors = {},
		label = 'Tool options',
		commitIdleMs = 400,
		onValues
	}: {
		fields: FieldConfig[];
		values: Record<string, unknown>;
		errors?: Record<string, string>;
		label?: string;
		commitIdleMs?: number;
		onValues: (values: Record<string, unknown>, info: EntryFormInfo) => void;
	} = $props();

	registerEditorFieldComponents();

	let schemaKey = $derived(JSON.stringify(fields));
	let schema = $derived.by(() => {
		void schemaKey;
		return buildFormSchema(untrack(() => fields));
	});
	let names = $derived(
		extractAllFields(schema)
			.map((field) => field.name)
			.filter((name): name is string => !!name)
	);
	let initial = $derived.by(() => {
		void schemaKey;
		return untrack(() => ({ ...values }));
	});
	let fieldErrors = $derived(Object.fromEntries(Object.entries(errors).map(([name, message]) => [name, [message]])));

	let form = $state<{ setValues(partial: Record<string, unknown>): boolean } | undefined>();
	let known: Record<string, unknown> | null = null;
	let latest: Record<string, unknown> = untrack(() => values);

	$effect.pre(() => {
		void schemaKey;
		known = null;
	});

	function changedNames(data: Record<string, unknown>): string[] {
		const before = known ?? {};
		return names.filter((name) => name in data && !jsonEqual(before[name], data[name]));
	}

	function handleChange(data: Record<string, unknown>) {
		if (known === null) {
			known = data;
			return;
		}
		const changed = changedNames(data);
		known = data;
		if (!changed.length) return;
		const next = { ...latest };
		for (const name of changed) next[name] = data[name];
		latest = next;
		onValues(next, { commit: false, name: changed[0] });
	}

	function handleCommit(_data: Record<string, unknown>, info: { name: string }) {
		onValues(latest, { commit: true, name: info.name });
	}

	$effect(() => {
		const incoming = values;
		const target = form;
		untrack(() => {
			latest = incoming;
			if (!target || known === null) return;
			const diff: Record<string, unknown> = {};
			for (const name of names) {
				if (incoming[name] !== undefined && !jsonEqual(known[name], incoming[name])) diff[name] = incoming[name];
			}
			if (!Object.keys(diff).length) return;
			if (target.setValues(diff)) known = { ...known, ...diff };
		});
	});
</script>

<div data-entry-form>
	{#key schemaKey}
		<DynamicForm
			bind:this={form}
			{schema}
			initialData={initial}
			formName={label}
			{fieldErrors}
			{commitIdleMs}
			onFormDataChange={handleChange}
			onFormDataCommit={handleCommit}
		/>
	{/key}
</div>
