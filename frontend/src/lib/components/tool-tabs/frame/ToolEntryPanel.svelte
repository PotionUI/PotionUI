<script lang="ts">
	import { get, type Readable } from 'svelte/store';
	import EditorEntryComponent from '$lib/components/tool-tabs/frame/EditorEntryComponent.svelte';
	import EntryForm from '$lib/components/tool-tabs/frame/EntryForm.svelte';
	import { guard } from '$lib/editors/guard';
	import type { EntryContext, EntryValuesInfo, RailToolEntry } from '$lib/editors/railTools';
	import type { FieldConfig } from '$lib/form/reactions';
	import { mediaToolCatalog } from '$lib/media-tools/catalog';
	import type { EditStackState, PendingEdit } from '$lib/tool-tabs/frame/editStack';
	import type { EditorToolHost } from '$lib/editors/railTools';

	let {
		entry,
		entryKey,
		host,
		phone = false,
		onclose
	}: {
		entry: RailToolEntry;
		entryKey: string;
		host: EditorToolHost<any>;
		phone?: boolean;
		onclose?: () => void;
	} = $props();

	let pendingNow = $state.raw<PendingEdit<any> | null>(null);
	let stackNow = $state.raw<EditStackState<any> | null>(null);
	let catalogNow = $state.raw<EntryContext['catalog']>([]);

	$effect(() => host.pending.subscribe((next) => (pendingNow = next)));
	$effect(() => {
		const store = (host as { stack?: Readable<EditStackState<any>> }).stack;
		if (!store) {
			stackNow = null;
			return;
		}
		return store.subscribe((next) => (stackNow = next));
	});
	$effect(() => mediaToolCatalog.subscribe((next) => (catalogNow = next)));

	function liveContext(): EntryContext {
		const store = (host as { stack?: Readable<EditStackState<any>> }).stack;
		return {
			host,
			phone,
			tool: entry.id,
			pending: get(host.pending),
			stack: store ? get(store) : null,
			catalog: get(mediaToolCatalog)
		};
	}

	let ctx = $derived<EntryContext>({ host, phone, tool: entry.id, pending: pendingNow, stack: stackNow, catalog: catalogNow });

	let fields = $derived.by<FieldConfig[]>(() => {
		const declared = entry.schema;
		if (!declared) return [];
		return guard(entryKey, 'schema', () => (typeof declared === 'function' ? declared(ctx) : declared), []);
	});

	let values = $derived.by<Record<string, unknown>>(() => {
		if (entry.values) return guard(entryKey, 'values', () => entry.values!(ctx), {});
		const held = pendingNow?.tool === entry.id ? (pendingNow.params as Record<string, unknown> | null) : null;
		if (held) return held;
		return Object.fromEntries(fields.flatMap(defaultsOf));
	});

	let description = $derived.by(() => {
		const declared = entry.description;
		if (!declared) return '';
		return guard(entryKey, 'description', () => (typeof declared === 'function' ? declared(ctx) : declared), '');
	});

	function defaultsOf(field: FieldConfig): Array<[string, unknown]> {
		if (field.children) return field.children.flatMap(defaultsOf);
		return field.name && field.default !== undefined ? [[field.name, field.default]] : [];
	}

	function titles(list: FieldConfig[]): Array<[string, string]> {
		return list.flatMap((field) => (field.children ? titles(field.children) : field.name ? [[field.name, field.title || field.name] as [string, string]] : []));
	}

	function summarize(next: Record<string, unknown>, list: FieldConfig[]): string {
		const label = new Map(titles(list));
		return Object.entries(next)
			.filter(([name]) => label.has(name))
			.slice(0, 3)
			.map(([name, value]) => `${label.get(name)} ${typeof value === 'boolean' ? (value ? 'on' : 'off') : value}`)
			.join(' · ');
	}

	function write(next: Record<string, unknown>, info: EntryValuesInfo) {
		if (entry.onValues) {
			guard(entryKey, 'onValues', () => entry.onValues!(next, info, liveContext()), undefined);
			return;
		}
		if (info.commit) return;
		const draft = { tool: entry.id, icon: entry.icon, label: entry.label, summary: summarize(next, fields), params: { ...next } };
		if (get(host.pending)?.tool === entry.id) host.update(draft);
		else host.begin(draft);
	}
</script>

<div class="flex flex-col gap-3" data-entry-options={entry.id}>
	{#if description}
		<p class="text-sm text-fg-muted" data-effect-description>{description}</p>
	{/if}
	{#if entry.schema && fields.length}
		<EntryForm {fields} {values} onValues={write} />
	{/if}
	{#if entry.options}
		<EditorEntryComponent entry={entryKey} componentRef={entry.options} props={{ host, phone, tool: entry.id }} {onclose} />
	{/if}
</div>
